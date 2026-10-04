"""Fingerprint search: which public Census / BEA series reproduces a workbook series?

For each target workbook series (default: every series of the workbook data sheets that the public build does not
match exactly, plus unbuilt ones), compare its recent growth rates against EVERY monthly series of the Census
economic-indicator time series (all datasets, all categories and data types, SA and NSA; archived in data/) and of
BEA IDS-0182 (all end-use codes), and report the best matches (growth correlation, and whether the levels are
proportional). Writes registry/match_search.csv.

Usage: python tools/search_matches.py [--asof 20261001] [--targets KEY[,KEY...]] [--refresh]
"""
import argparse
import json
import os
import pickle
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store  # noqa: E402
from gdpnow.config import load_env  # noqa: E402

load_env()
SKIP = {'qfr', 'qpr', 'qss', 'qtax', 'bfs'}              # quarterly or unrelated


def fetch_census(path, refresh):
    if path.exists() and not refresh:
        return pickle.load(open(path, 'rb'))
    key = os.environ['CENSUS_API_KEY']
    meta = json.load(urllib.request.urlopen('https://api.census.gov/data/timeseries/eits.json', timeout=60))
    series = {}
    for d in meta['dataset']:
        ds = d['c_dataset'][-1]
        if ds in SKIP:
            continue
        for sa in ('yes', 'no'):
            q = dict(get='cell_value,category_code,data_type_code,time_slot_id', time='from 2013-01', seasonally_adj=sa, key=key)
            q['for'] = 'us:*'
            try:
                rows = json.load(urllib.request.urlopen(f'https://api.census.gov/data/timeseries/eits/{ds}?' + urllib.parse.urlencode(q), timeout=180))
            except Exception as e:
                print(f'  {ds} sa={sa}: {str(e)[:60]}')
                continue
            h = rows[0]
            ic, ik, idt, it = h.index('cell_value'), h.index('category_code'), h.index('data_type_code'), h.index('time')
            tmp = {}
            for r in rows[1:]:
                try:
                    v = float(r[ic])
                except ValueError:
                    continue
                try:
                    t = pd.Period(r[it], 'M').end_time.normalize()
                except Exception:
                    continue
                tmp.setdefault((r[ik], r[idt]), {})[t] = v
            for (cat, dt), vals in tmp.items():
                if len(vals) >= 24:
                    series[(f'census:{ds}', cat, dt, sa)] = pd.Series(vals).sort_index()
            print(f'  {ds} sa={sa}: {len(tmp)} series')
    pickle.dump(series, open(path, 'wb'))
    return series


def growth(s):
    s = s.dropna()
    return np.log(s).diff() if (s > 0).all() else s.diff()


def build_matrix(cands, start='2015-01-31', end='2026-12-31'):
    """Monthly growth matrix (candidates x months, NaN where undefined) and level matrix, on one grid."""
    idx = pd.date_range(start, end, freq='ME')
    keys, G, Lv = [], [], []
    for k, s in cands.items():
        s = s.reindex(idx)
        if s.notna().sum() < 24:
            continue
        pos = (s.dropna() > 0).all()
        g = (np.log(s) if pos else s).diff()
        keys.append(k); G.append(g.to_numpy(float)); Lv.append(s.to_numpy(float))
    return idx, keys, np.array(G), np.array(Lv)


def best_matches(w, idx, keys, G, Lv, top=4, min_overlap=24):
    w = w.reindex(idx)
    pos = (w.dropna() > 0).all()
    gw = ((np.log(w) if pos else w).diff()).to_numpy(float)
    ok = ~np.isnan(G) & ~np.isnan(gw)[None, :]
    n = ok.sum(axis=1)
    Gm = np.where(ok, G, 0.0); Wm = np.where(ok, gw[None, :], 0.0)
    with np.errstate(invalid='ignore', divide='ignore'):
        mg, mw = Gm.sum(1) / n, Wm.sum(1) / n
        cg, cw = np.where(ok, G - mg[:, None], 0.0), np.where(ok, gw[None, :] - mw[:, None], 0.0)
        corr = (cg * cw).sum(1) / np.sqrt((cg ** 2).sum(1) * (cw ** 2).sum(1))
    corr[(n < min_overlap) | ~np.isfinite(corr)] = -2
    out = []
    for i in np.argsort(-corr)[:top]:
        if corr[i] < 0.98:
            break
        lvl = ok_l = ~np.isnan(Lv[i]) & ~np.isnan(w.to_numpy(float))
        r = w.to_numpy(float)[ok_l] / Lv[i][ok_l]
        cv = float(np.std(r) / abs(np.mean(r))) if len(r) > 3 and np.mean(r) != 0 else np.nan
        out.append((float(corr[i]), cv, int(n[i]), keys[i]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', default='20261001')
    ap.add_argument('--targets', default='')
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args()
    con = store.connect()
    v = store.latest_vintage(con)
    cands = fetch_census(Path('data') / f'{a.asof}_census_eits_all.pkl', a.refresh)
    # IDS-0182 candidates (exports/imports x basis x SA)
    from gdpnow import ids0182, public_monthly as PM
    cx = PM.Ctx(con, '2026-10-01', None)
    for (flow, basis, sa), df in ids0182.load(cx).items():
        for code, g in df.groupby('code'):
            s = g.set_index('date').v.sort_index()
            if len(s) >= 24:
                cands[(f'ids0182:{flow}:{basis}', code, 'value', sa)] = s
    # BEA candidates: every NIPA / underlying-detail table line archived in the store (monthly or quarterly->skip)
    bea = con.execute("SELECT series, date, value FROM raw_pulls WHERE source = 'bea'").fetchdf()
    bea['date'] = pd.to_datetime(bea.date)
    nb = 0
    for ser, g in bea.groupby('series'):
        if ':M|' not in ser and ':M ' not in ser and not ser.split('|')[0].endswith(':M'):
            continue
        sr = g.drop_duplicates('date').set_index('date').value.sort_index()
        sr.index = sr.index + pd.offsets.MonthEnd(0)
        if len(sr) >= 24:
            cands[('bea:' + ser.split('|')[0], ser.split('|')[1] if '|' in ser else ser, 'value', 'sa?')] = sr; nb += 1
    print(nb, 'BEA monthly series added')
    idx, keys, G, Lv = build_matrix(cands)
    print(len(keys), 'candidate series on the grid')
    audit = pd.read_csv('registry/input_audit_all.csv')
    if a.targets:
        keys = a.targets.split(',')
        tgt = audit[audit.key.isin(keys)]
    else:
        tgt = audit[audit.sheet.isin(['MonthlyLevels', 'InventoryRaw', 'ConsMonthlyLevels']) &
                    (audit.status.str.startswith(('POOR', 'FAIR', 'NOT-BUILT', 'MISSING')) | audit.status.str.contains('ENDS-EARLY'))]
    rows = []
    for t in tgt.itertuples():
        wb = store.series_frame(con, v, t.sheet)
        if t.key not in wb.columns:
            continue
        w = wb[t.key].dropna()
        if len(w) < 36:
            continue
        res = best_matches(w, idx, keys, G, Lv)
        for corr, cv, n, ck in res:
            rows.append(dict(sheet=t.sheet, target=t.key, status=t.status, used=t.used, corr=round(corr, 5), ratio_cv=round(cv, 4) if cv == cv else None,
                             n=n, source=ck[0], category=ck[1], data_type=ck[2], sa=ck[3]))
        if not res:
            rows.append(dict(sheet=t.sheet, target=t.key, status=t.status, used=t.used, source='NO MATCH >= 0.98'))
    out = pd.DataFrame(rows)
    out.to_csv('registry/match_search.csv', index=False)
    pd.set_option('display.width', 250, 'display.max_rows', 500, 'display.max_colwidth', 40)
    print(out.to_string(index=False))


if __name__ == '__main__':
    main()
