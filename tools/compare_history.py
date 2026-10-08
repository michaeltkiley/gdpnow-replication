"""Before/after exploration for 'full history from the current sources' (GDPNOW_FULL_HISTORY=1 drops the BEA table windows, the vehicle-totals start and
the 1959 cut). `run` builds, estimates and nowcasts one variant on a copy of the restored state (tagged, nothing recorded); `compare` prints what differs.
Prints lines prefixed 'X|'. Not part of the pipeline."""
import glob
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / 'data'
TABLES = ['growth', 'levels', 'monthly_prices', 'cons_growth', 'cons_levels', 'inv_raw', 'q_hist', 'nominal', 'nipa', 'inv_deflators']


def out(*a):
    print('X|' + ' '.join(str(x) for x in a), flush=True)


def sh(*args):
    print('\n$', ' '.join(args), flush=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]], check=True, cwd=ROOT)


def info(df):
    rows = {}
    for c in df.columns:
        s = df[c].dropna()
        rows[str(c)] = (str(s.index.min())[:10] if len(s) else None, str(s.index.max())[:10] if len(s) else None, len(s))
    return rows


def run():
    from gdpnow import clock, public_data, store
    variant = os.environ['VARIANT']
    asof = clock.today().isoformat()
    vint = asof.replace('-', '')
    con = store.connect()
    lpm = public_data.fred(con, 'CPIAUCSL', asof, refresh=True).index.max().strftime('%Y-%m')
    con.close()
    tag = f'_{variant}'
    sh('01_ingest_workbook.py', '--date', vint)
    sh('08_published.py', '--date', vint)
    sh('02_build_public.py', '--asof', asof, '--last-price-month', lpm, '--ism', 'public', '--tag', tag)
    sh('04_estimate.py', '--level', 'L3', '--asof', asof, '--tag', tag, '--force')   # --force: the restored state already holds today's untagged L3 estimates
    sh('05_nowcast.py', '--level', 'L3', '--asof', asof, '--tag', tag, '--force')
    stem = vint + tag
    run_id = f'L3_{stem}'
    con = store.connect()
    res = {'variant': variant, 'asof': asof,
           'agg': dict(con.execute('SELECT key, value FROM nowcast_aggregates WHERE run_id = ?', [run_id]).fetchall()),
           'comps': con.execute('SELECT * FROM nowcast_components WHERE run_id = ?', [run_id]).fetchdf(),
           'diag': pd.read_csv(DATA / f'{stem}_diagnostics_{run_id}.csv')}
    con.close()
    d = pickle.load(open(DATA / f'{stem}_public_inputs.pkl', 'rb'))
    inp, panel, act, qprices = d['bundle']
    res['series'] = {t: info(getattr(inp, t)) for t in TABLES}
    res['series']['factor_panel'] = info(panel)
    res['series']['act'] = info(act)
    pickle.dump(res, open(DATA / f'compare_{variant}.pkl', 'wb'))
    out('RUN', variant, asof, 'GDP', res['agg'].get('GDP'))


def compare():
    b = pickle.load(open(glob.glob(str(DATA / '**/compare_base.pkl'), recursive=True)[0], 'rb'))
    f = pickle.load(open(glob.glob(str(DATA / '**/compare_full.pkl'), recursive=True)[0], 'rb'))
    out('asof', b['asof'], f['asof'])
    out('GDP nowcast base', b['agg']['GDP'], 'full', f['agg']['GDP'], 'diff', f['agg']['GDP'] - b['agg']['GDP'])
    # components
    keys = [c for c in b['comps'].columns if b['comps'][c].dtype == object and c != 'run_id']
    num = [c for c in b['comps'].columns if c not in keys and c != 'run_id' and pd.api.types.is_numeric_dtype(b['comps'][c])]
    m = b['comps'].merge(f['comps'], on=keys, suffixes=('_b', '_f'))
    for c in num:
        m['d_' + c] = m[c + '_f'] - m[c + '_b']
    dcols = ['d_' + c for c in num]
    m['maxd'] = m[dcols].abs().max(axis=1)
    out('components columns', keys, num[:8])
    for r in m.sort_values('maxd', ascending=False).head(14).itertuples():
        out('component', *[getattr(r, k) for k in keys], 'maxabs', f'{r.maxd:.5f}', *[f'{c}={getattr(r, c):+.5f}' for c in dcols[:4]])
    # estimation diagnostics
    dm = b['diag'].merge(f['diag'], on=['block', 'item', 'term'], how='outer', suffixes=('_b', '_f'))
    dm['d'] = pd.to_numeric(dm['ours_f'], errors='coerce') - pd.to_numeric(dm['ours_b'], errors='coerce')
    out('diagnostics rows', len(dm), 'changed >1e-9:', int((dm['d'].abs() > 1e-9).sum()))
    for blk, g in dm[dm['d'].abs() > 1e-9].groupby('block'):
        out('diag block', blk, 'changed', len(g), 'max abs', f"{g['d'].abs().max():.5f}")
    for r in dm[dm['d'].abs() > 1e-9].reindex(dm['d'].abs().sort_values(ascending=False).index).dropna(subset=['d']).head(12).itertuples():
        out('diag top', r.block, r.item, r.term, f'base={r.ours_b:.5f} full={r.ours_f:.5f}')
    bs = dm[dm['block'] == 'blend_sample']
    out('blend_sample rows (start, n)', len(bs))
    for r in bs.itertuples():
        out('blend_sample', r.item, 'start', r.workbook_b if hasattr(r, 'workbook_b') else '', '|', r.term, 'n base', r.ours_b, 'n full', r.ours_f)
    # input series coverage
    changed = []
    for t in b['series']:
        for c, vb in b['series'][t].items():
            vf = f['series'][t].get(c)
            if vf is None:
                changed.append((t, c, vb, 'missing'))
            elif vb[0] != vf[0] or vb[2] != vf[2]:
                changed.append((t, c, vb, vf))
        for c in f['series'][t]:
            if c not in b['series'][t]:
                changed.append((t, c, 'missing', f['series'][t][c]))
    out('input series whose first date or count differs:', len(changed))
    for t, c, vb, vf in changed[:150]:
        out('series', t, c, 'base', vb, 'full', vf)


if __name__ == '__main__':
    {'run': run, 'compare': compare}[sys.argv[1]]()
