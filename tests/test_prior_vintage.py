"""Previous-vintage retail series (revision terms of the latest-month PCE nowcast, IMPLEMENTATION.md DD6).

From 2026-10-06 to 2026-11-19 the "previous" retail and food-services series were read through public_data.fred(), which routes
them to Census's bulk file. That file holds only today's vintage and census_bulk cached by series alone, so the previous vintage
equalled the current one and every revision term was zero: on 2026-11-17 (October advance retail sales, August and September revised
up 0.36% and 0.75%) the nowcast missed about -0.2 pp of PCE goods. The values below are that release, from the vintage archive.
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import duckdb
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import census_bulk as C, components, public_data as P

MONTHS = pd.to_datetime(['2026-08-31', '2026-09-30', '2026-10-31'])
# Census MARTS, $ million SA: (series, vintage of 2026-10-15, vintage of 2026-11-17)
MARTS = {
    'RSFSXMV': ([599797, 605188], [601001, 607409, 611226]),
    'RSGASS': ([63950, 68021], [63781, 67581, 68608]),
    'RSBMGESD': ([44323, 44077], [44305, 43959, 44164]),
    'RSFSDP': ([91882, 92906], [91850, 92694, 92921]),
}


def con_with_pulls():
    con = duckdb.connect()
    con.execute('CREATE TABLE raw_pulls(source VARCHAR, series VARCHAR, as_of VARCHAR, retrieved_at TIMESTAMP, date TIMESTAMP, value DOUBLE)')
    return con


def alfred_json(values):
    return {'observations': [{'date': str(d.to_period('M').to_timestamp().date()), 'value': str(v)} for d, v in zip(MONTHS, values)]}


class CensusBulkCache(unittest.TestCase):
    def setUp(self):
        C._CACHE.clear()
        self.con = con_with_pulls()

    def tearDown(self):
        C._CACHE.clear()

    def test_cache_is_per_asof(self):
        """Two as-of dates archived with different values: each read returns its own, in either order."""
        prog, cat, dt_, adj, geo = C.FRED['RSFSXMV']
        key = f'{prog}|{cat}|{dt_}|{adj}|{geo}'
        old, new = MARTS['RSFSXMV']
        P._archive(self.con, 'census_bulk', key, '2026-10-15', pd.Series(old, index=MONTHS[:2], dtype=float))
        P._archive(self.con, 'census_bulk', key, '2026-11-17', pd.Series(new, index=MONTHS, dtype=float))
        with mock.patch.object(C, '_frame', side_effect=AssertionError('no download expected')):
            a = C.series(self.con, 'RSFSXMV', '2026-11-17')
            b = C.series(self.con, 'RSFSXMV', '2026-10-15')
        self.assertEqual(len(a), 3)
        self.assertEqual(len(b), 2)
        self.assertNotEqual(a.iloc[1], b.iloc[1])


class Alfred(unittest.TestCase):
    def setUp(self):
        self.con = con_with_pulls()
        self.env = mock.patch.dict(os.environ, {'FRED_API_KEY': 'k'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_reads_the_real_time_period_and_bypasses_bulk_files(self):
        urls = []

        def get(url, tries=4):
            urls.append(url)
            return alfred_json(MARTS['RSFSXMV'][0])
        with mock.patch.object(P, '_get', side_effect=get), \
                mock.patch.object(C, 'series', side_effect=AssertionError('bulk file must not be used')):
            s = P.alfred(self.con, 'RSFSXMV', '2026-10-17')
            again = P.alfred(self.con, 'RSFSXMV', '2026-10-17')         # served from the archive
        self.assertEqual(len(urls), 1)
        self.assertIn('realtime_start=2026-10-17', urls[0])
        self.assertIn('realtime_end=2026-10-17', urls[0])
        self.assertEqual(list(s), [599797.0, 605188.0])
        pd.testing.assert_series_equal(s, again, check_names=False, check_freq=False)

    def test_no_silent_current_vintage(self):
        """A failed real-time request raises instead of falling back to the current vintage (the fallback fred() keeps for NAR)."""
        calls = []

        def get(url, tries=4):
            calls.append(url)
            raise RuntimeError('HTTP 400')
        with mock.patch.object(P, '_get', side_effect=get):
            with self.assertRaises(RuntimeError):
                P.alfred(self.con, 'RSFSXMV', '2026-10-17')
        self.assertEqual(len(calls), 1)

    def test_archive_read_ignores_the_mixed_vintage_override(self):
        """Release-effect runs remap run dates; a past ALFRED vintage is read at its own date."""
        P._archive(self.con, 'alfred', 'RSFSDP', '2026-10-17', pd.Series([1.0, 2.0], index=MONTHS[:2]))
        cfg = Path(self.id().replace('.', '_') + '.json')
        cfg.write_text(json.dumps({'new': '2026-11-17', 'prev': '2026-11-16', 'release': [['alfred', 'RSFSDP']]}))
        try:
            with mock.patch.dict(os.environ, {'GDPNOW_OVERRIDE': str(cfg)}), mock.patch.dict(P._OVR, {}, clear=True), \
                    mock.patch.object(P, '_get', side_effect=AssertionError('no request expected')):
                s = P.alfred(self.con, 'RSFSDP', '2026-10-17')
        finally:
            cfg.unlink()
        self.assertEqual(list(s), [1.0, 2.0])


class RevisionTerms(unittest.TestCase):
    """latest_month_nowcast with the 2026-11-17 release: September PCE is published, October is not."""

    def growth(self, s):
        return 1200 * np.log(s / s.shift(1))

    def test_revisions_reach_published_months(self):
        old = {k: pd.Series(v[0], index=MONTHS[:2], dtype=float) for k, v in MARTS.items()}
        new = {k: pd.Series(v[1], index=MONTHS, dtype=float) for k, v in MARTS.items()}
        ctrl = lambda d: d['RSFSXMV'] - d['RSGASS'] - d['RSBMGESD'] - d['RSFSDP']
        jul = pd.Series([1.0], index=pd.to_datetime(['2026-07-31']))
        cur = self.growth(pd.concat([jul * 400000, ctrl(new)]))
        prev = self.growth(pd.concat([jul * 400000, ctrl(old)]))
        window = pd.DatetimeIndex(MONTHS)
        actual = pd.Series([2.0, 3.0], index=MONTHS[:2])                  # published real PCE growth through September
        deflator = pd.Series(0.0, index=MONTHS)
        y = components.latest_month_nowcast(actual, cur, prev, deflator, window)
        # August and September: published PCE plus the retail revision (about +4.3 and +4.7 at an annual rate).
        self.assertAlmostEqual(y[MONTHS[0]] - 2.0, cur[MONTHS[0]] - prev[MONTHS[0]])
        self.assertAlmostEqual(y[MONTHS[1]] - 3.0, cur[MONTHS[1]] - prev[MONTHS[1]])
        self.assertGreater(y[MONTHS[0]] - 2.0, 4.0)
        self.assertGreater(y[MONTHS[1]] - 3.0, 4.0)
        # The bug: previous == current gives no revision at all.
        z = components.latest_month_nowcast(actual, cur, cur, deflator, window)
        np.testing.assert_allclose(z[MONTHS[:2]].to_numpy(), [2.0, 3.0])


if __name__ == '__main__':
    unittest.main()
