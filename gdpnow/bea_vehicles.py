"""Motor-vehicle retail sales (FRED DAUTOSAAR, FAUTOSAAR, DLTRUCKSSAAR, FLTRUCKSSAAR, HTRUCKSSAAR, LAUTOSA, LTRUCKSA) from BEA's
'Motor vehicles' workbook (the file FRED's 'Supplemental Estimates, Motor Vehicles' release is made from; linked on the GDP page).
It is current to the month after the sales (BEA's monthly NIPA file lags one more month). Sheets Table 1 to 5 (domestic autos, foreign
autos, domestic light trucks, foreign light trucks, heavy trucks) hold one row per month: month name, year, NSA thousands, seasonal
factor, SA thousands, SAAR millions. Columns are found by header label ('annual rates'); rows are those with a month name and a year.
Rows after the last observation carry only factors and no SAAR, so they drop out. Light-vehicle totals are domestic + foreign.
"""
import calendar
from pathlib import Path

import pandas as pd

from . import public_data as P

URL = 'https://apps.bea.gov/national/xls/gap_hist.xlsx'
SHEET = {'DAUTOSAAR': 'Table 1', 'FAUTOSAAR': 'Table 2', 'DLTRUCKSSAAR': 'Table 3', 'FLTRUCKSSAAR': 'Table 4', 'HTRUCKSSAAR': 'Table 5'}
SUM = {'LAUTOSA': ('DAUTOSAAR', 'FAUTOSAAR'), 'LTRUCKSA': ('DLTRUCKSSAAR', 'FLTRUCKSSAAR')}     # FRED id -> parts (a missing part is 0)
_CACHE = {}
_MONTHS = {m: i for i, m in enumerate(calendar.month_name) if m}


def covers(fred_id):
    return fred_id in SHEET or fred_id in SUM


def xlsx(asof):
    """Path of the workbook downloaded for this as-of date (once per process under a refresh)."""
    path = Path('data') / f'{str(asof).replace("-", "")}_bea_motor_vehicles.xlsx'
    if not path.exists() or P.refresh_once(('bea_vehicles_xlsx', str(path))):
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(P.get_bytes(URL, timeout=300))
    return path


def _sheet(path, name):
    d = pd.read_excel(path, sheet_name=name, header=None)
    hdr = [j for j in range(d.shape[1]) if any('annual rates' in str(d.iat[r, j]).lower() for r in range(min(8, len(d))))]
    if len(hdr) != 1:
        raise RuntimeError(f'BEA motor vehicles {name}: expected one "annual rates" column, found {hdr}')
    j = hdr[0]
    month = d.iloc[:, 0].astype(str).str.strip().map(_MONTHS)
    year = pd.to_numeric(d.iloc[:, 1], errors='coerce')
    val = pd.to_numeric(d.iloc[:, j], errors='coerce')
    ok = month.notna() & year.notna() & val.notna()
    if not ok.any():
        raise RuntimeError(f'BEA motor vehicles {name}: no monthly values')
    idx = pd.to_datetime(pd.DataFrame({'year': year[ok].astype(int), 'month': month[ok].astype(int), 'day': 1}))
    return pd.Series(val[ok].to_numpy(float), index=idx.to_numpy()).sort_index()


def parse(path):
    """{FRED id: monthly Series (first of month), SAAR millions}."""
    out = {fid: _sheet(path, sh) for fid, sh in SHEET.items()}
    for fid, parts in SUM.items():
        a, b = (out[p] for p in parts)
        out[fid] = a.add(b, fill_value=0.0)
    return out


def series(con, fred_id, asof):
    """The series FRED publishes as `fred_id` (index: first day of each month, like FRED)."""
    if fred_id in _CACHE:
        return _CACHE[fred_id]
    if not P.REFRESH:
        s = P._archived(con, 'bea_vehicles', fred_id, asof)
        if s is not None:
            _CACHE[fred_id] = s
            return s
    for fid, s in parse(xlsx(asof)).items():
        _CACHE[fid] = s
        P._archive(con, 'bea_vehicles', fid, asof, s)
    return _CACHE[fred_id]
