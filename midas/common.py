"""Shared definitions for the real-time Ridge-MIDAS nowcast (see ../MIDAS.md)."""
import os
import calendar
import datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get('MIDAS_DATA', HERE / 'data'))   # vintage cache (not committed)
OUT = HERE / 'results'

# ALFRED id -> (frequency, transform, label, group)
#   dlog : 100*dlog(level)   diff : first difference   lvl : level
SERIES = {
    'PAYEMS':   ('M', 'dlog', 'Nonfarm payrolls',               'labor'),
    'UNRATE':   ('M', 'diff', 'Unemployment rate',              'labor'),
    'AWHMAN':   ('M', 'diff', 'Avg weekly hours, manufacturing', 'labor'),
    'ICSA':     ('W', 'dlog', 'Initial jobless claims',         'labor'),
    'INDPRO':   ('M', 'dlog', 'Industrial production',          'production'),
    'DGORDER':  ('M', 'dlog', 'Durable goods new orders',       'production'),
    'RSAFS':    ('M', 'dlog', 'Retail & food services sales',   'spending'),
    'PCE':      ('M', 'dlog', 'Personal consumption expenditures (nominal)', 'spending'),
    'UMCSENT':  ('M', 'diff', 'Michigan consumer sentiment',    'spending'),
    'HOUST':    ('M', 'dlog', 'Housing starts',                 'housing'),
    'BOPGSTB':  ('M', 'diffk', 'Goods & services trade balance', 'trade'),
    'BUSINV':   ('M', 'dlog', 'Total business inventories',     'inventories'),
    'CPIAUCSL': ('M', 'dlog', 'CPI',                            'prices'),
    'NFCI':     ('W', 'lvl',  'Chicago Fed NFCI',               'financial'),
    'T10Y3M':   ('D', 'lvl',  '10y-3m Treasury spread',         'financial'),
}
VINTAGE_IDS = [k for k in SERIES if k != 'T10Y3M']      # revised -> ALFRED vintage per origin
TRIO = ['INDPRO', 'NFCI', 'T10Y3M']                      # Chan (2026) tutorial's three predictors
COVID = [(2020, 1), (2020, 2), (2020, 3), (2020, 4), (2021, 1), (2021, 2), (2021, 3), (2021, 4)]


def eom(y, m):
    return dt.date(y, m, calendar.monthrange(y, m)[1])


def add_months(y, m, k):
    t = y * 12 + (m - 1) + k
    return t // 12, t % 12 + 1


def origins(first=(2005, 1), last=dt.date(2026, 10, 31)):
    """(year, quarter, h, origin_date). h=1,2,3: last day of months 1-3; h=4: 15th of next month."""
    out = []
    y, q = first
    while True:
        m1 = 3 * (q - 1) + 1
        for h in (1, 2, 3, 4):
            if h < 4:
                d = eom(*add_months(y, m1, h - 1))
            else:
                yy, mm = add_months(y, m1, 3)
                d = dt.date(yy, mm, 15)
            if d <= last:
                out.append((y, q, h, d))
        if eom(*add_months(y, m1, 0)) > last:
            break
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out
