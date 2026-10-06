"""BEA International Accounts detailed goods trade data, IDS-0182 (public download, monthly, 1999 to the latest
full trade report, ~470 end-use codes, Census and balance-of-payments (BOP) basis, seasonally adjusted by BEA).

Replaces the Census end-use API + our own X-13 for goods trade (the API starts in 2013 and our seasonal
adjustment did not match the Atlanta Fed's: core capital goods growth correlated 0.55 with the workbook; with
BEA's SA series it is 0.99-1.00). Also supplies BOP-basis nonmonetary gold (XNMGLD, MNMGLD).

Current-vintage download (the file is overwritten each month): cached per as-of date in data/.
"""
import io
import zipfile
from pathlib import Path

import openpyxl
import pandas as pd

from . import public_data as P

URL = 'https://apps.bea.gov/international/zip/{}.zip'
FILES = {'Historical': 'IDS0182-Hist', 'Current': 'IDS0182'}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _parse(blob, flow):
    out = {}
    wb = openpyxl.load_workbook(io.BytesIO(blob), read_only=True, data_only=True)
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(min_row=3, values_only=True):
            if r[0] is None or not isinstance(r[1], (int, float)):
                continue
            for i in range(12):
                v = _num(r[2 + i])
                if v is not None:
                    rows.append((str(r[0]).strip(), pd.Timestamp(int(r[1]), i + 1, 1) + pd.offsets.MonthEnd(0), v))
        out[ws.title] = pd.DataFrame(rows, columns=['code', 'date', 'v'])
    return out


def load(cx):
    """{(flow, 'Census-based'|'BP-based', 'SA'|'NSA'): DataFrame(code, date, v)}; cached in cx.cache and data/."""
    if 'ids0182' in cx.cache:
        return cx.cache['ids0182']
    path = Path('data') / f'{cx.asof.replace("-", "")}_ids0182.pkl'
    if path.exists() and not P.REFRESH:
        cx.cache['ids0182'] = pd.read_pickle(path)
        return cx.cache['ids0182']
    res = {}
    for part, name in FILES.items():
        z = zipfile.ZipFile(io.BytesIO(P.get_bytes(URL.format(name), timeout=300)))
        for member in z.namelist():
            low = member.lower()
            if not low.endswith('.xlsx') or 'exports' not in low and 'imports' not in low:
                continue
            flow = 'exports' if 'exports' in low else 'imports'
            for title, df in _parse(z.read(member), flow).items():
                basis, sa = [s.strip() for s in title.split(',')]
                res.setdefault((flow, basis, sa), []).append(df)
    res = {k: pd.concat(v).drop_duplicates(['code', 'date'], keep='last') for k, v in res.items()}
    pd.to_pickle(res, path)
    cx.cache['ids0182'] = res
    return res


def series(cx, flow, code, basis='Census-based', sa='SA'):
    """Monthly $ million series for an end-use code (without the X/M prefix: '21300' or '2' or 'NMGLD')."""
    d = load(cx)[(flow, basis, sa)]
    full = ('X' if flow == 'exports' else 'M') + code
    s = d[d.code == full].set_index('date').v.sort_index()
    return s.loc[:pd.Timestamp(cx.asof) - pd.offsets.MonthEnd(1)]
