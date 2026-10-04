"""Systematic audit of every DATA input the nowcast reads (registry/inputs_used.csv) against the GDPNow workbook.

For each raw / constructed / transformed input: is it built from public data at all, how far back does the public
series go, does it end in the same month, and how closely do its growth rates and recent levels match the
workbook's. Writes registry/input_audit.csv and prints a summary. The workbook is used only as a benchmark here.

Usage: python tools/audit_inputs.py [--asof 20261001] [--vintage latest] [--all]
--all audits EVERY series of the workbook's data sheets (not only the registered inputs) and writes
registry/input_audit_all.csv with a `used` flag.
"""
import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store  # noqa: E402

DATA_CLASSES = {'raw', 'constructed', 'constructed(transform of raw)', 'data+model_output'}


def growth(s, positive):
    return np.log(s).diff() if positive else s.diff()


def compare(pub, wb, since='2013-01-01'):
    pub, wb = pub.dropna(), wb.dropna()
    row = {'pub_start': pub.index.min().date() if len(pub) else None, 'pub_end': pub.index.max().date() if len(pub) else None,
           'wb_start': wb.index.min().date(), 'wb_end': wb.index.max().date()}
    if len(pub) == 0:
        return {**row, 'status': 'MISSING'}
    positive = bool((pub > 0).all() and (wb > 0).all())
    j = pd.concat([growth(pub, positive), growth(wb, positive)], axis=1, keys=['p', 'w']).dropna()
    jj = j[j.index >= since]
    row['n_overlap'] = len(jj)
    row['growth_corr'] = round(float(jj.p.corr(jj.w)), 4) if len(jj) > 24 and jj.p.std() > 0 and jj.w.std() > 0 else np.nan
    tail = j.tail(12)
    scale = tail.w.std() if len(tail) > 2 and tail.w.std() > 0 else np.nan
    row['recent_mad_over_sd'] = round(float((tail.p - tail.w).abs().mean() / scale), 3) if scale == scale else np.nan
    lv = pd.concat([pub, wb], axis=1, keys=['p', 'w']).dropna().tail(12)
    row['level_ratio_cv'] = round(float((lv.p / lv.w).std() / abs((lv.p / lv.w).mean())), 4) if len(lv) > 3 and (lv.w != 0).all() else np.nan
    row['hist_years_short'] = round((pub.index.min() - wb.index.min()).days / 365.25, 1)
    row['end_gap_months'] = int(round((wb.index.max() - pub.index.max()).days / 30.4))
    c = row['growth_corr']
    if c != c:
        status = 'CHECK'
    elif c >= 0.999 and (row['recent_mad_over_sd'] != row['recent_mad_over_sd'] or row['recent_mad_over_sd'] < 0.05):
        status = 'EXACT'
    elif c >= 0.99:
        status = 'GOOD'
    elif c >= 0.90:
        status = 'FAIR'
    else:
        status = 'POOR'
    if row['hist_years_short'] > 5 and status in ('EXACT', 'GOOD', 'FAIR'):
        status += '+SHORT'
    if row['end_gap_months'] >= 1:
        status += '+ENDS-EARLY'
    return {**row, 'status': status}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', default='20261001')
    ap.add_argument('--vintage', default='latest')
    ap.add_argument('--all', action='store_true')
    a = ap.parse_args()
    con = store.connect(read_only=True) if 'read_only' in store.connect.__code__.co_varnames else store.connect()
    v = store.latest_vintage(con) if a.vintage == 'latest' else a.vintage
    inp = pickle.load(open(f'data/{a.asof}_public_inputs.pkl', 'rb'))['bundle'][0]
    frames = {'MonthlyLevels': inp.levels, 'MonthlyPriceLevels': inp.monthly_prices, 'InventoryRaw': inp.inv_raw,
              'ConsMonthlyLevels': inp.cons_levels, 'TransformedMonthlySeries': inp.growth,
              'ConsTransformedMonthlySeries': inp.cons_growth, 'QtrlyGDPData': inp.nipa, 'NomQtrlyComps': inp.nominal,
              'dLogQtrlyGrowth': inp.q_hist, 'InvDefDatafr': inp.inv_deflators}
    reg = pd.read_csv('registry/inputs_used.csv')
    used = set(zip(reg.sheet, reg.key))
    if a.all:
        targets = []
        for sheet in ('MonthlyLevels', 'InventoryRaw', 'ConsMonthlyLevels', 'MonthlyPriceLevels', 'QtrlyGDPData', 'NomQtrlyComps',
                      'TransformedMonthlySeries', 'ConsTransformedMonthlySeries'):
            wb_f = store.series_frame(con, v, sheet)
            targets += [(sheet, k, 'all') for k in wb_f.columns]
        reg_rows = pd.DataFrame(targets, columns=['sheet', 'key', 'class'])
    else:
        reg_rows = reg[reg['class'].isin(DATA_CLASSES)]
    out = []
    for sheet, g in reg_rows.groupby('sheet'):
        pub_f = frames.get(sheet)
        wb_f = store.series_frame(con, v, sheet)
        for key in g.key.unique():
            r = {'sheet': sheet, 'key': key, 'class': g[g.key == key]['class'].iloc[0], 'used': (sheet, key) in used}
            if wb_f is None or key not in wb_f.columns or wb_f[key].dropna().empty:
                out.append({**r, 'status': 'NO-WORKBOOK-SERIES'})
                continue
            if pub_f is None or key not in pub_f.columns:
                out.append({**r, 'status': 'NOT-BUILT'})
                continue
            out.append({**r, **compare(pub_f[key], wb_f[key])})
    df = pd.DataFrame(out)
    df.to_csv('registry/input_audit_all.csv' if a.all else 'registry/input_audit.csv', index=False)
    print(df.status.value_counts().to_string())
    print('\nPOOR / NOT-BUILT / MISSING / CHECK:')
    bad = df[df.status.str.startswith(('POOR', 'NOT-BUILT', 'MISSING', 'CHECK'))]
    print(bad[['sheet', 'key', 'status', 'growth_corr']].to_string(index=False))


if __name__ == '__main__':
    main()
