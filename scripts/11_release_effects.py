"""Stage 11: layer-2 decomposition. Effect of each data release ALONE on the nowcast.

For every release with new or revised data between the previous run and --asof, rebuild the data with only that
release's series updated (all other series as archived on the previous as-of date), re-estimate everything, run
the nowcast, and compare with the previous day's run. The effects are measured separately, so they need not add
up to the day's total: the difference is reported as an interaction / unattributed remainder (it also holds
inputs that are not archived per day: the IDS-0182 file, the BEA trade workbook, the AEI report).
Each release's effect is split into a re-estimation part (changes in bridge coefficients, constants and blend
weights) and a data part (everything else, including the factor and BVAR forecasts re-estimated on the new data).

Adds `layer2` to docs/data/decomp/<asof>.json (run stage 10 first). Intermediate runs are deleted afterwards.

Usage: python scripts/11_release_effects.py --asof YYYY-MM-DD [--max-releases 8] [--force]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gdpnow import decompose, releases, store
from gdpnow.config import DATA, ROOT

OUT = ROOT / 'docs' / 'data' / 'decomp'
REEST = ('(coefficient)', '(constant/other)', 'Blend weight', 'bucket weights')


def sh(script, env, *args):
    r = subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *args], cwd=ROOT, env=env, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f'{script} {" ".join(args)} failed:\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}')


def cleanup(run_id, stem):
    con = store.connect()
    for t in ('est_params', 'est_frames', 'est_provenance', 'est_series', 'nowcast_components', 'nowcast_aggregates',
              'nowcast_intermediates', 'provenance', 'runs'):
        if store.table_exists(con, t):
            con.execute(f'DELETE FROM {t} WHERE run_id = ?', [run_id])
    con.close()
    for f in DATA.glob(f'{stem}*'):
        f.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--asof', required=True)
    ap.add_argument('--max-releases', type=int, default=8)
    ap.add_argument('--last-price-month', required=True)
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    path = OUT / f'{a.asof}.json'
    if not path.exists():
        print(f'{path.name} missing (first run of the quarter, or stage 10 not run): no layer 2')
        return
    rec = json.loads(path.read_text())
    if 'layer2' in rec and not a.force:
        print(f'{path.name} already has layer 2; use --force')
        return
    prev, day = rec['previous'], a.asof.replace('-', '')
    con = store.connect()
    ch = releases.changes(con, prev, a.asof)
    prev_run, new_run = f'L3_{prev.replace("-", "")}', f'L3_{day}'
    r0, total = decompose.load(con, prev_run), rec['layer1']['headline']['delta']
    con.close()
    groups = sorted(ch.groupby('release'), key=lambda kv: -len(kv[1]))
    skipped = [n for n, _ in groups[a.max_releases:]]
    todo = [('(inputs not archived per day)', None)] + [(n, g) for n, g in groups[:a.max_releases]]   # baseline first

    def one(k, g):
        tag = f'_r{k}'
        stem, run_id = day + tag, f'{new_run}{tag}'
        cfg = DATA / f'{stem}_override.json'
        rel = [] if g is None else [[s, x] for s, x in zip(g.source, g.series)]
        cfg.write_text(json.dumps(dict(new=a.asof, prev=prev, release=rel)))
        env = {**os.environ, 'GDPNOW_OVERRIDE': str(cfg)}
        try:
            sh('02_build_public.py', env, '--asof', a.asof, '--last-price-month', a.last_price_month, '--ism', 'public', '--tag', tag, '--force')
            sh('04_estimate.py', env, '--level', 'L3', '--asof', a.asof, '--tag', tag, '--force')
            sh('05_nowcast.py', env, '--level', 'L3', '--asof', a.asof, '--tag', tag, '--force')
            con = store.connect()
            r = decompose.load(con, run_id)
            con.close()
            return r
        finally:
            cleanup(run_id, stem)

    res, base, unarchived = [], None, 0.0
    for k, (name, g) in enumerate(todo):
        print(f'[{k + 1}/{len(todo)}] {name}: {0 if g is None else len(g)} series', flush=True)
        r1 = one(k, g)
        if g is None:                      # baseline: previous day's archived data, today's non-archived inputs
            base = r1
            unarchived = decompose.component_effects(r0, base)['headline']['delta']
            print(f'    baseline vs previous run: {unarchived:+.3f}', flush=True)
            continue
        eff = decompose.component_effects(base, r1)
        d = eff['headline']['delta']
        re_pp = sum(x['pp'] for x in eff['drivers'] if x['what'].endswith(REEST))
        res.append(dict(release=name, series=len(g), effect=d, data_effect=d - re_pp, reestimation_effect=re_pp,
                        components={c['id']: c['d_contribution'] for c in eff['components']},
                        drivers=[x for x in eff['drivers'] if abs(x['pp']) > 1e-4][:8]))
        print(f'    effect {d:+.3f} (data {d - re_pp:+.3f}, re-estimation {re_pp:+.3f})', flush=True)
    res.sort(key=lambda r: -abs(r['effect']))
    rec['layer2'] = dict(releases=res, total=total, unarchived_inputs=unarchived,
                         interaction=total - unarchived - sum(r['effect'] for r in res), skipped=skipped)
    path.write_text(json.dumps(rec, indent=1, default=float))
    print(f'layer 2: releases {sum(r["effect"] for r in res):+.3f} + non-archived inputs {unarchived:+.3f} + interaction {rec["layer2"]["interaction"]:+.3f} = day total {total:+.3f}')


if __name__ == '__main__':
    main()
