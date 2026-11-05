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


if __name__ == '__main__':
    unittest.main()
