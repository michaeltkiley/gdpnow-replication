"""Goods and services trade balances of payments (FRED BOPGEXP, BOPGIMP, BOPSEXP, BOPSIMP) from BEA's trade-release time-series workbook,
instead of FRED. The workbook (the file the travel-services series are read from too) is downloaded once per run; its file name carries
the release month, so the link is read off the release page (public_data.bea_trade_xlsx, recorded for the daily probe).

Sheet 'Table 1' has a two-level header (Balance / Exports / Imports over Total / Goods / Services), an 'Annual' block and a 'Monthly'
block of 'YYYY Mon' rows (some carry (R)/(P) markers), in millions of dollars, seasonally adjusted. Columns are found by header label.
The four series match FRED exactly on 24 months (exploration round 21).
"""
import re
from pathlib import Path

import pandas as pd

from . import public_data as P

MAP = {'BOPGEXP': ('Exports', 'Goods'), 'BOPSEXP': ('Exports', 'Services'),
       'BOPGIMP': ('Imports', 'Goods'), 'BOPSIMP': ('Imports', 'Services')}     # FRED id -> (flow, kind) in Table 1
_CACHE = {}


def covers(fred_id):
    return fred_id in MAP


def xlsx(asof):
    """Path of the workbook downloaded for this as-of date (once per process under a refresh)."""
    path = Path('data') / f'{str(asof).replace("-", "")}_bea_trade_time_series.xlsx'
    if not path.exists() or P.refresh_once(('bea_trade_xlsx', str(path))):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(P.bea_trade_xlsx())
    return path


def parse(path):
    """{FRED id: monthly Series (first of month)} from Table 1."""
    d = pd.read_excel(path, sheet_name='Table 1', header=None)
    h0 = d.index[d.iloc[:, 0].astype(str).str.strip() == 'Period'][0]
    top = d.iloc[h0].ffill()
    sub = d.iloc[h0 + 1]
    col = {}
    for j in range(1, d.shape[1]):
        flow = str(top.iloc[j]).strip()
        kind = re.sub(r'[\s\d]+$', '', str(sub.iloc[j]).strip())
        col[(flow, kind)] = j
    m0 = d.index[d.iloc[:, 0].astype(str).str.strip() == 'Monthly'][0]
    labels = d.iloc[m0 + 1:, 0].astype(str).str.replace(r'\(.*?\)', '', regex=True).str.strip().str.replace(r'\s+', ' ', regex=True)
    dates = pd.to_datetime(labels, format='%Y %b', errors='coerce')
    out = {}
    for fid, key in MAP.items():
        if key not in col:
            raise RuntimeError(f'BEA trade workbook Table 1: column {key} not found (headers: {sorted(col)})')
        s = pd.Series(pd.to_numeric(d.iloc[m0 + 1:, col[key]], errors='coerce').to_numpy(), index=dates.to_numpy()).dropna()
        s = s[s.index.notna()]
        if s.empty:
            raise RuntimeError(f'BEA trade workbook Table 1: no monthly values for {fid}')
        out[fid] = s.sort_index().astype(float)
    return out


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id` (index: first day of each month, like FRED)."""
    if fred_id in _CACHE:
        return _CACHE[fred_id]
    if not P.REFRESH:
        s = P._archived(con, 'bea_trade', fred_id, asof)
        if s is not None:
            _CACHE[fred_id] = s
            return s
    for fid, s in parse(xlsx(asof)).items():
        _CACHE[fid] = s
        P._archive(con, 'bea_trade', fid, asof, s)
    return _CACHE[fred_id]
