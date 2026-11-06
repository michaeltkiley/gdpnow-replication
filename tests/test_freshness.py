"""freshness.check(): the expected inputs come from gates and code, not from yesterday's pulls.

Each class below is a failure the daily run really had (or could not tell apart from one); `Cycle` replays two years of release
calendar and asserts the check never holds a day that has no real problem.
"""
import sys
import unittest
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import freshness as F, gates, public_data as P

CODE = 'code-a'


class Base(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        self.con.execute('CREATE TABLE raw_pulls(source VARCHAR, series VARCHAR, as_of VARCHAR, retrieved_at VARCHAR, date VARCHAR, value DOUBLE)')

    def put(self, asof, pairs, n=5, end='2026-09-30'):
        dates = [str(d.date()) for d in pd.date_range(end=end, periods=n, freq='ME')]
        df = pd.DataFrame([(src, s, asof, 'x', d, 1.0) for src, s in pairs for d in dates],
                          columns=['source', 'series', 'as_of', 'retrieved_at', 'date', 'value'])
        self.con.register('_p', df)
        self.con.execute('INSERT INTO raw_pulls SELECT * FROM _p')
        self.con.unregister('_p')

    def day(self, asof, pairs, window=None, code=CODE, **kw):
        """One daily run: record the gate, the pulls, run the check. Returns (holds, notes)."""
        if window is not None:
            gates.gate(self.con, asof, 'aei_window', window)
        self.put(asof, pairs, **kw)
        return F.check(self.con, asof, code)


CORE = [('fred', 'GDP'), ('bea', 'NIPA:T10105:Q|1|GDP')]
WINDOW = [('aei', 'exports|Capital Goods'), ('aei', 'exports|Total'), ('fred', 'CFNAI'), ('ids0182', 'imports|Census-based|SA|3')]


class Relabel(Base):
    """2026-11-05: a new BEA register entry renamed lines; the data was unchanged."""
    def test_relabelled_line_passes(self):
        self.day('2026-11-04', [('bea', 'NIPA:T40205B:Q|4|New domestic light trucks'), ('bea', 'NIPA:T10105:Q|1|GDP')])
        holds, _ = self.day('2026-11-05', [('bea', 'NIPA:T40205B:Q|4|Federal Reserve banks'), ('bea', 'NIPA:T10105:Q|1|GDP')])
        self.assertEqual(holds, [])

    def test_missing_line_is_held(self):
        self.day('2026-11-04', [('bea', 'NIPA:T40205B:Q|4|a'), ('bea', 'NIPA:T10105:Q|1|GDP')])
        holds, _ = self.day('2026-11-05', [('bea', 'NIPA:T40205B:Q|4|a')])
        self.assertEqual(len(holds), 1)


class Window(Base):
    """2026-11-06: the advance-trade window closed, so 25 inputs were not read."""
    def test_window_closing_and_reopening_passes(self):
        self.assertEqual(self.day('2026-11-03', CORE + WINDOW, window=True)[0], [])
        holds, notes = self.day('2026-11-05', CORE, window=False)
        self.assertEqual(holds, [])
        self.assertEqual(len(notes), 1)                                     # said, not held
        self.assertEqual(self.day('2026-11-06', CORE, window=False)[0], [])  # and the next day, and the one after
        self.assertEqual(self.day('2026-11-30', CORE + WINDOW, window=True)[0], [])

    def test_loss_inside_the_window_is_held(self):
        """Gate open, same code, an input the window reads is not read: nothing explains it (the model is degraded)."""
        self.day('2026-11-03', CORE + WINDOW, window=True)
        holds, _ = self.day('2026-11-04', CORE + WINDOW[:2], window=True)
        self.assertEqual(len(holds), 2)

    def test_loss_outside_the_window_is_held(self):
        self.day('2026-11-05', CORE, window=False)
        holds, _ = self.day('2026-11-06', CORE[:1], window=False)
        self.assertEqual(len(holds), 1)

    def test_held_day_does_not_move_the_baseline(self):
        self.day('2026-11-05', CORE, window=False)
        self.assertEqual(len(self.day('2026-11-06', CORE[:1], window=False)[0]), 1)
        self.assertEqual(len(self.day('2026-11-09', CORE[:1], window=False)[0]), 1)       # still held: not forgotten

    def test_baseline_from_before_gates_existed_relearns(self):
        """The table the live database has today: (source, series, n, last) with no regime."""
        self.put('2026-11-03', CORE + WINDOW)
        legacy = F.summary(self.con, '2026-11-03')[['source', 'series', 'n', 'last']]
        self.con.register('_l', legacy)
        self.con.execute(f'CREATE TABLE {F.TABLE} AS SELECT * FROM _l')
        self.con.unregister('_l')
        holds, notes = self.day('2026-11-05', CORE, window=False)
        self.assertEqual(holds, [])
        self.assertEqual(len(notes), 1)


class CodeChange(Base):
    def test_deliberate_removal_of_a_read_passes(self):
        self.day('2026-11-03', CORE + [('fred', 'OLD')])
        holds, notes = self.day('2026-11-04', CORE, code='code-b')
        self.assertEqual(holds, [])
        self.assertEqual(len(notes), 1)

    def test_same_code_removal_is_held(self):
        self.day('2026-11-03', CORE + [('fred', 'OLD')])
        self.assertEqual(len(self.day('2026-11-04', CORE)[0]), 1)


class Revision(Base):
    """2026-10-30: BLS flat file 103 -> 102 observations."""
    def test_one_observation_less_is_a_note(self):
        self.day('2026-10-28', CORE, n=103)
        holds, notes = self.day('2026-10-29', CORE, n=102)
        self.assertEqual(holds, [])
        self.assertEqual(len(notes), 2)

    def test_large_shrink_is_held(self):
        self.day('2026-10-28', CORE, n=103)
        self.assertEqual(len(self.day('2026-10-29', CORE, n=60)[0]), 2)

    def test_last_observation_retreating_is_held(self):
        self.day('2026-10-28', CORE, end='2026-09-30')
        self.assertEqual(len(self.day('2026-10-29', CORE, end='2026-08-31')[0]), 2)

    def test_stale_is_held(self):
        holds, _ = self.day('2026-11-05', CORE, end='2025-01-31')
        self.assertEqual(len(holds), 2)


class Cycle(Base):
    """Two years of release calendar: the window opens around the 28th and closes around the 5th, the code changes monthly, one series is
    revised by an observation now and then, BEA lines are relabelled twice. No day may be held; each injected loss must be."""
    def test_two_years_without_a_false_hold(self):
        days = pd.bdate_range('2025-01-01', '2026-12-31')
        held = []
        for i, d in enumerate(days):
            window = d.day >= 28 or d.day <= 4
            lines = [('bea', f'NIPA:T40205B:Q|4|{"a" if d.year == 2025 else "b"}'), ('bea', f'NIPA:T10105:Q|1|{"x" if d.month < 6 else "y"}')]
            pairs = CORE + lines + (WINDOW if window else [])
            n = 103 - (1 if i % 17 == 0 else 0)
            gates.gate(self.con, str(d.date()), 'aei_window', window)
            self.put(str(d.date()), pairs, n=n, end=str((d - pd.offsets.MonthBegin(1) - pd.Timedelta(days=1)).date()))
            holds, _ = F.check(self.con, str(d.date()), f'code-{d.year}-{d.month}')
            if holds:
                held.append((str(d.date()), holds[:2]))
        self.assertEqual(held, [])


class Gates(Base):
    def test_regime_string(self):
        self.assertEqual(gates.regime(self.con, '2026-11-05'), '')
        gates.gate(self.con, '2026-11-05', 'aei_window', False)
        gates.gate(self.con, '2026-11-05', 'other', True)
        gates.gate(self.con, '2026-11-05', 'aei_window', True)               # re-run of the same day replaces
        self.assertEqual(gates.regime(self.con, '2026-11-05'), 'aei_window=open,other=open')


class Fetch(unittest.TestCase):
    """2026-10-27: census.gov answered one request with HTTP 500 and the whole day failed."""
    def test_retries_server_errors_then_succeeds(self):
        import io
        import urllib.error
        import urllib.request
        calls = []

        class R:
            def read(self):
                return b'ok'

        def fake(req, timeout=None):
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 500, 'Internal Server Error', {}, io.BytesIO())
            return R()
        real, urllib.request.urlopen = urllib.request.urlopen, fake
        try:
            self.assertEqual(P._fetch('http://x', 1, wait=0), b'ok')
        finally:
            urllib.request.urlopen = real
        self.assertEqual(len(calls), 3)

    def test_does_not_retry_client_errors(self):
        import io
        import urllib.error
        import urllib.request
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 404, 'Not Found', {}, io.BytesIO())
        real, urllib.request.urlopen = urllib.request.urlopen, fake
        try:
            with self.assertRaises(urllib.error.HTTPError):
                P._fetch('http://x', 1, wait=0)
        finally:
            urllib.request.urlopen = real
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
