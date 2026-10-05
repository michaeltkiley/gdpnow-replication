"""Stage 10: layer-1 decomposition of today's change in the nowcast versus the previous run of the same quarter,
plus the raw data that changed grouped by data release. Writes docs/data/decomp/<asof>.json.

Usage: python scripts/10_decompose.py --asof YYYY-MM-DD [--prev YYYY-MM-DD] [--force]
"""
import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import decompose, releases, store
from gdpnow.config import ROOT

OUT = ROOT / 'docs' / 'data'


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--asof', required=True)
    ap.add_argument('--prev', help='previous as-of date (default: latest earlier run of the same quarter)')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    out = OUT / 'decomp' / f'{a.asof}.json'
    if out.exists() and not a.force:
        print(f'{out.name} exists; use --force to rewrite')
        return
    hist = pd.read_csv(OUT / 'history.csv')
    cur = hist[hist['asof'] == a.asof]
    if cur.empty:
        sys.exit(f'{a.asof} not in history.csv: run stage 09 first')
    q = cur['quarter'].iloc[0]
    prev = a.prev or (hist[(hist['asof'] < a.asof) & (hist['quarter'] == q)]['asof'].max() if len(hist[(hist['asof'] < a.asof) & (hist['quarter'] == q)]) else None)
    if not prev or pd.isna(prev):
        print(f'{a.asof}: first run of {q}, nothing to compare with')
        return
    con = store.connect()
    r0, r1 = decompose.load(con, f'L3_{prev.replace("-", "")}'), decompose.load(con, f'L3_{a.asof.replace("-", "")}')
    res = decompose.component_effects(r0, r1)
    ch = releases.changes(con, prev, a.asof)
    rel = []
    for name, g in ch.groupby('release'):
        rel.append(dict(release=name, series=len(g), new_observations=int(g.n_new.sum()), revised_values=int(g.n_rev.sum()),
                        latest_period=str(g.last_new.max())[:10] if g.last_new.notna().any() else None,
                        examples=sorted(g.series)[:8]))
    rel.sort(key=lambda r: -r['series'])
    rec = dict(asof=a.asof, previous=prev, quarter=q, layer1=res, data_changes=rel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=1, default=float))
    h = res['headline']
    print(f'{prev} -> {a.asof}: {h["from_"]:.3f} -> {h["to"]:.3f} ({h["delta"]:+.3f} pp); {len(rel)} releases with new data; '
          f'top driver: {res["drivers"][0]["component"]} {res["drivers"][0]["what"]} {res["drivers"][0]["pp"]:+.3f}')


if __name__ == '__main__':
    main()
