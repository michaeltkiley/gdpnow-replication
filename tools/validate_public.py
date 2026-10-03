"""Benchmark public series against the workbook series they replace (L3 mapping check).

For each FRED id -> workbook ticker in config/public_series.toml [fred]: fetch as of --asof, compare the
monthly log growth with the workbook's (2010 onward): correlation, last month, and ratio of levels.
Usage: python tools/validate_public.py --asof 2026-10-01 [--vintage 20261003]
"""
import argparse, sys
from pathlib import Path
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store, public_data as P
from gdpnow.config import load_toml

ap = argparse.ArgumentParser(); ap.add_argument('--asof', default='2026-10-01'); ap.add_argument('--vintage', default='20261003')
ap.add_argument('--section', default='fred')
a = ap.parse_args()
con = store.connect()
wb = pd.concat([store.series_frame(con, a.vintage, s) for s in ['MonthlyLevels', 'InventoryRaw', 'ConsMonthlyLevels', 'MonthlyPriceLevels']], axis=1)
wb = wb.loc[:, ~wb.columns.duplicated()]
rows = []
for fid, tick in load_toml('public_series.toml')[a.section].items():
    try:
        s = P.fred(con, fid, a.asof)
        s.index = s.index + pd.offsets.MonthEnd(0)
        w = wb[tick].dropna()
        j = pd.concat([np.log(s).diff(), np.log(w).diff()], axis=1, keys=['pub', 'wb']).loc['2010':].dropna()
        rows.append((fid, tick, round(j.pub.corr(j.wb), 4), str(s.index.max().date()), str(w.index.max().date()),
                     round(float((s / w).loc['2015':].median()), 4)))
    except Exception as e:
        rows.append((fid, tick, np.nan, 'ERR ' + str(e)[:40], '', np.nan))
df = pd.DataFrame(rows, columns=['fred', 'workbook', 'corr_dlog', 'pub_last', 'wb_last', 'level_ratio'])
pd.set_option('display.width', 200)
print(df.to_string(index=False))
print('low corr:', list(df[df.corr_dlog < 0.98].fred))
