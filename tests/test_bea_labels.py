"""bea_bulk.register(): labels are shared only between the metrics of one concept (codes ending in R + a metric letter)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import bea_bulk as B

# code, label, metric, calc, scale, TableId:LineNo, parents
REGISTER = '''%SeriesCode,SeriesLabel,MetricName,CalculationType,DefaultScale,TableId:LineNo,SeriesCodeParents
A191RC,"Gross domestic product","Current Dollars","Level",-6,T10105:1,NONE
A191RG,"Equals: Gross domestic product","Fisher Price Index","Level",0,T10104:1,NONE
A191RX,"Gross domestic product","Chained Dollars","Level",-6,T10106:1,NONE
LA001080,"Federal Reserve banks","Current Dollars","Level",-6,T71700:4,NONE
LA001081,"New domestic light trucks","Current Dollars","Level",-6,U20405:10,NONE
IA001081,"New domestic light trucks","Fisher Price Index","Level",0,U20404:10,NONE
LA001085,"Agricultural foods, feeds, and beverages","Current Dollars","Level",-6,T40205B:4,NONE
'''


class Labels(unittest.TestCase):
    def setUp(self):
        B._TEXT[B.REGISTER] = REGISTER
        B._REG.clear()
        self.reg = B.register()

    def tearDown(self):
        B._REG.clear()
        B._TEXT.pop(B.REGISTER, None)

    def label(self, table, line):
        return self.reg[table][line][1]

    def test_metrics_of_one_concept_share_the_current_dollar_label(self):
        self.assertEqual(self.label('T10104', '1'), 'Gross domestic product')    # not 'Equals: ...'
        self.assertEqual(self.label('T10106', '1'), 'Gross domestic product')

    def test_unrelated_codes_with_the_same_first_characters_keep_their_own_label(self):
        # LA001080 sorts first and used to rename the lines of LA001081 and LA001085 (shared stem LA00108)
        self.assertEqual(self.label('U20405', '10'), 'New domestic light trucks')
        self.assertEqual(self.label('T40205B', '4'), 'Agricultural foods, feeds, and beverages')
        self.assertEqual(self.label('T71700', '4'), 'Federal Reserve banks')

    def test_price_and_nominal_line_of_a_numeric_code_still_match_by_description(self):
        self.assertEqual(self.label('U20404', '10'), self.label('U20405', '10'))

    def test_stem(self):
        self.assertEqual(B.stem('A191RC'), 'A191R')
        self.assertEqual(B.stem('LA001081'), 'LA001081')
        self.assertEqual(B.stem('C4214X'), 'C4214X')
        self.assertEqual(B.stem('X'), 'X')


if __name__ == '__main__':
    unittest.main()
