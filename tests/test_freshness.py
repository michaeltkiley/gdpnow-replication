"""freshness.check(): BEA lines are matched by table and line number, so a relabelled line is not a missing one."""
import sys
import unittest
from pathlib import Path

import duckdb
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import freshness as F


def put(con, asof, series):
    for s in series:
        for d in pd.date_range('2026-05-31', periods=5, freq='ME'):
            con.execute('INSERT INTO raw_pulls VALUES (?,?,?,?,?,?)', ['bea', s, asof, 'x', str(d.date()), 1.0])


class Relabel(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect()
        self.con.execute('CREATE TABLE raw_pulls(source VARCHAR, series VARCHAR, as_of VARCHAR, retrieved_at VARCHAR, date VARCHAR, value DOUBLE)')
        put(self.con, '2026-11-04', ['NIPA:T40205B:Q|4|New domestic light trucks', 'NIPA:T10105:Q|1|GDP'])
        self.assertEqual(F.check(self.con, '2026-11-04'), [])

    def test_relabelled_line_passes(self):
        put(self.con, '2026-11-05', ['NIPA:T40205B:Q|4|Federal Reserve banks', 'NIPA:T10105:Q|1|GDP'])
        self.assertEqual(F.check(self.con, '2026-11-05'), [])

    def test_missing_line_is_flagged(self):
        put(self.con, '2026-11-05', ['NIPA:T40205B:Q|4|Federal Reserve banks'])
        self.assertEqual(len(F.check(self.con, '2026-11-05')), 1)


class Conditional(unittest.TestCase):
    """Series read only in the advance-trade window may be absent outside it (config/freshness.toml [conditional])."""
    def setUp(self):
        self.con = duckdb.connect()
        self.con.execute('CREATE TABLE raw_pulls(source VARCHAR, series VARCHAR, as_of VARCHAR, retrieved_at VARCHAR, date VARCHAR, value DOUBLE)')

    def put(self, asof, pairs):
        for src, s in pairs:
            for d in pd.date_range('2026-05-31', periods=5, freq='ME'):
                self.con.execute('INSERT INTO raw_pulls VALUES (?,?,?,?,?,?)', [src, s, asof, 'x', str(d.date()), 1.0])

    def test_window_closing_passes(self):
        both = [('aei', 'exports|Capital Goods'), ('ids0182', 'imports|Census-based|SA|3'), ('fred', 'X')]
        self.put('2026-11-03', both)
        self.assertEqual(F.check(self.con, '2026-11-03'), [])
        self.put('2026-11-05', [('fred', 'X')])
        self.assertEqual(F.check(self.con, '2026-11-05'), [])

    def test_unconditional_ids0182_still_flagged(self):
        self.put('2026-11-03', [('ids0182', 'exports|Census-based|SA|2'), ('ids0182', 'exports|Census-based|SA|3')])
        self.assertEqual(F.check(self.con, '2026-11-03'), [])
        self.put('2026-11-05', [('ids0182', 'exports|Census-based|SA|3')])
        self.assertEqual(len(F.check(self.con, '2026-11-05')), 1)

    def test_conditional_series_still_checked_for_shrinking(self):
        self.put('2026-11-03', [('aei', 'exports|Total')])
        self.assertEqual(F.check(self.con, '2026-11-03'), [])
        for d in pd.date_range('2026-05-31', periods=3, freq='ME'):
            self.con.execute('INSERT INTO raw_pulls VALUES (?,?,?,?,?,?)', ['aei', 'exports|Total', '2026-11-05', 'x', str(d.date()), 1.0])
        self.assertEqual(len(F.check(self.con, '2026-11-05')), 1)


if __name__ == '__main__':
    unittest.main()
