"""Stage 09: write the day's record (our nowcast, GDPNow's published nowcast, integrity check) to
docs/data/runs/<asof>.json and update docs/data/history.csv (one row per as-of date).

Needs the L3 run for --asof (stage 05), GDPNow's published path (stage 08) and, for the integrity line, the L1 run
on the same workbook vintage.

Usage: python scripts/09_record.py --asof YYYY-MM-DD [--vintage YYYYMMDD] [--force]
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import store
from gdpnow.config import ROOT

OUT = ROOT / 'docs' / 'data'
KEYS = ['CTG', 'CS', 'FNE', 'FNP', 'FNS', 'FR', 'GF', 'GS', 'XM', 'XS', 'MM', 'MS']


def published_on(con, quarter, asof):
    """GDPNow's latest published update on or before asof: (date, {kind: {key: value}})."""
    df = con.execute("SELECT date, kind, key, value FROM gdpnow_published WHERE quarter = ? AND date <= ?",
                     [quarter, asof]).fetchdf()
    if df.empty:
        return None, {}
    d = df.date.astype(str).max()
    x = df[df.date.astype(str) == d]
    return d, {k: dict(zip(g.key, g.value)) for k, g in x.groupby('kind')}


def benchmark(con, quarter, asof, gdp, vintage):
    """GDPNow's published numbers as of `asof` and the L1 integrity line, as (gdpnow, integrity, row fields)."""
    pdate, pub = published_on(con, quarter, asof)
    l1 = con.execute('SELECT gdp FROM runs WHERE run_id = ?', [f'L1_{vintage}']).fetchone()
    l1_pub = published_on(con, quarter, '9999-12-31')[1].get('growth', {}).get('GDP') if l1 else None
    gdpnow = {'date': pdate, 'gdp': pub.get('growth', {}).get('GDP'),
              'growth': {k: pub.get('growth', {}).get(k) for k in KEYS},
              'contribution': {k: pub.get('contribution', {}).get(k) for k in KEYS + ['V']}} if pdate else None
    integrity = {'l1_gdp': l1[0], 'gdpnow_latest': l1_pub, 'l1_minus_gdpnow': (l1[0] - l1_pub) if l1_pub is not None else None,
                 'workbook_vintage': vintage} if l1 else None
    row = {'gdpnow_date': pdate, 'gdpnow': gdpnow['gdp'] if pdate else None,
           'l1_minus_gdpnow': integrity['l1_minus_gdpnow'] if integrity else None}
    row['diff'] = (gdp - row['gdpnow']) if row['gdpnow'] is not None else None
    if pdate:
        for k in KEYS:
            row[f'gd_{k}'], row[f'cd_{k}'] = pub['growth'].get(k), pub['contribution'].get(k)
        row['cd_V'] = pub['contribution'].get('V')
    return gdpnow, integrity, row


def carry_forward(asof, prev, vintage):
    """No new public data since the run for `prev`: the day's record repeats that run's nowcast (flagged
    `unchanged`); only the benchmark (GDPNow's published numbers, the L1 integrity line) is refreshed."""
    hist = OUT / 'history.csv'
    df = pd.read_csv(hist)
    old = df[df['asof'] == prev].iloc[0].to_dict()
    rec = json.loads((OUT / 'runs' / f'{prev}.json').read_text())
    con = store.connect()
    vintage = vintage or store.latest_vintage(con)
    gdpnow, integrity, upd = benchmark(con, rec['quarter'], asof, rec['nowcast']['gdp'], vintage)
    since = rec.get('unchanged', {}).get('since', prev)
    rec.update(asof=asof, gdpnow=gdpnow, integrity=integrity, unchanged={'since': since})
    (OUT / 'runs' / f'{asof}.json').write_text(json.dumps(rec, indent=1))
    row = {**old, 'asof': asof, **upd, 'unchanged': True}
    df = pd.concat([df[df['asof'] != asof], pd.DataFrame([row])], ignore_index=True).sort_values('asof')
    df.to_csv(hist, index=False)
    print(f'{asof}: no new public data since {since}; nowcast {old["nowcast"]:.3f} carried forward, '
          f'GDPNow {upd["gdpnow"]} (update {upd["gdpnow_date"]})')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--asof', required=True)
    ap.add_argument('--vintage', help='workbook vintage of the L1 integrity run (default: latest loaded)')
    ap.add_argument('--carry-from', metavar='ASOF', help='no new data: repeat that day\'s nowcast, refresh the benchmark only')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    asof = a.asof
    if a.carry_from:
        return carry_forward(asof, a.carry_from, a.vintage)
    out = OUT / 'runs' / f'{asof}.json'
    if out.exists() and not a.force:
        print(f'{out.name} exists; use --force to rewrite')
        return
    con = store.connect()
    run_id = f'L3_{asof.replace("-", "")}'
    run = con.execute('SELECT T, T1, gdp FROM runs WHERE run_id = ?', [run_id]).fetchone()
    if run is None:
        sys.exit(f'run {run_id} not found: run stages 02, 04, 05 first')
    T, T1, gdp = run
    quarter = f'{T1.year}Q{(T1.month - 1) // 3 + 1}'
    comps = con.execute('SELECT id, label, growth_pct, contribution FROM nowcast_components WHERE run_id = ?', [run_id]).fetchdf()
    agg = dict(con.execute('SELECT key, value FROM nowcast_aggregates WHERE run_id = ?', [run_id]).fetchall())
    pdate, pub = published_on(con, quarter, asof)
    vintage = a.vintage or store.latest_vintage(con)
    l1 = con.execute('SELECT gdp FROM runs WHERE run_id = ?', [f'L1_{vintage}']).fetchone()
    l1_pub = published_on(con, quarter, '9999-12-31')[1].get('growth', {}).get('GDP') if l1 else None
    rec = {
        'asof': asof, 'quarter': quarter, 'last_actual_quarter': str(T),
        'nowcast': {'gdp': gdp, 'aggregates': agg,
                    'components': {r.id: {'label': r.label, 'growth': r.growth_pct, 'contribution': r.contribution}
                                   for r in comps.itertuples()}},
        'gdpnow': {'date': pdate, 'gdp': pub.get('growth', {}).get('GDP'),
                   'growth': {k: pub.get('growth', {}).get(k) for k in KEYS},
                   'contribution': {k: pub.get('contribution', {}).get(k) for k in KEYS + ['V']}} if pdate else None,
        'integrity': {'l1_gdp': l1[0], 'gdpnow_latest': l1_pub, 'l1_minus_gdpnow': (l1[0] - l1_pub) if l1_pub is not None else None,
                      'workbook_vintage': vintage} if l1 else None,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1))
    # one wide row per as-of date
    row = {'asof': asof, 'quarter': quarter, 'nowcast': gdp, 'gdpnow_date': pdate,
           'gdpnow': rec['gdpnow']['gdp'] if pdate else None}
    row['diff'] = (gdp - row['gdpnow']) if row['gdpnow'] is not None else None
    row['l1_minus_gdpnow'] = rec['integrity']['l1_minus_gdpnow'] if rec['integrity'] else None
    row['unchanged'] = False
    for r in comps.itertuples():
        row[f'g_{r.id}'], row[f'c_{r.id}'] = r.growth_pct, r.contribution
    if pdate:
        for k in KEYS:
            row[f'gd_{k}'], row[f'cd_{k}'] = pub['growth'].get(k), pub['contribution'].get(k)
        row['cd_V'] = pub['contribution'].get('V')
    hist = OUT / 'history.csv'
    df = pd.read_csv(hist) if hist.exists() else pd.DataFrame(columns=['asof'])
    df = pd.concat([df[df['asof'] != asof], pd.DataFrame([row])], ignore_index=True).sort_values('asof')
    df.to_csv(hist, index=False)
    print(f'{asof} {quarter}: nowcast {gdp:.3f}  GDPNow {row["gdpnow"]} (update {pdate})  '
          f'L1 check {row["l1_minus_gdpnow"]}  -> {out.relative_to(ROOT)}, history rows {len(df)}')


if __name__ == '__main__':
    main()
