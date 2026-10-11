"""Test of the next-quarter chain (gdpnow/chain.py; workflow chain-check). Nothing is recorded or cached. On one day's data (the first build pulls, the others read
that archive) it runs:
  c0  production: nowcast for T1 (the model as run daily), also writing the pseudo-actual rows for T1
  c3  chained: the same data with c0's rows entered as T1's estimate, nowcasting T1 + 1
  c1  plumbing: data truncated one quarter back, the published values of T (restricted to chain.COLUMNS) entered; must reproduce c0 exactly
  c2a/c2b  chain error: data truncated two quarters back, the model's own nowcast of T entered; compared with c0.
Lines are prefixed 'X|'."""
import os
import pickle
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = ROOT / 'data'


def out(*a):
    print('X|' + ' '.join(str(x) for x in a), flush=True)


def sh(*args, env=None):
    print('\n$', ' '.join(args), flush=True)
    e = {**os.environ, **(env or {})}
    subprocess.run([sys.executable, str(ROOT / 'scripts' / args[0]), *args[1:]], check=True, cwd=ROOT, env=e)


def result(vint, tag):
    from gdpnow import store
    con = store.connect()
    run_id = f'L3_{vint}{tag}'
    r = con.execute('SELECT T, T1, gdp FROM runs WHERE run_id = ?', [run_id]).fetchone()
    comps = con.execute('SELECT id, growth_pct, contribution FROM nowcast_components WHERE run_id = ? ORDER BY id', [run_id]).fetchdf().set_index('id')
    con.close()
    return {'T': r[0], 'T1': r[1], 'gdp': r[2], 'comps': comps}


def stage(asof, vint, lpm, tag, build_args=(), refresh=False, emit=False):
    sh('02_build_public.py', '--asof', asof, '--last-price-month', lpm, '--ism', 'public', '--tag', tag, '--force', *build_args,
       env={'GDPNOW_REFRESH': '1'} if refresh else {'GDPNOW_REFRESH': ''})
    sh('04_estimate.py', '--level', 'L3', '--asof', asof, '--tag', tag, '--force')
    sh('05_nowcast.py', '--level', 'L3', '--asof', asof, '--tag', tag, '--force', *(['--emit-pseudo'] if emit else []))
    return result(vint, tag)


def compare(name, a, b):
    out(f'{name}: GDP {a["gdp"]:.6f} vs {b["gdp"]:.6f} (diff {a["gdp"] - b["gdp"]:+.2e}); T1 {str(a["T1"])[:10]} vs {str(b["T1"])[:10]}')
    d = (a['comps'] - b['comps'])
    for i, r in d.iterrows():
        out(f'  {name} {i:4s} growth {a["comps"].growth_pct[i]:9.4f} vs {b["comps"].growth_pct[i]:9.4f} contrib {a["comps"].contribution[i]:8.4f} vs {b["comps"].contribution[i]:8.4f}')


def main():
    from gdpnow import chain, clock, public_data, store
    asof = clock.today().isoformat()
    vint = asof.replace('-', '')
    con = store.connect()
    lpm = public_data.fred(con, 'CPIAUCSL', asof, refresh=True).index.max().strftime('%Y-%m')
    con.close()
    sh('01_ingest_workbook.py', '--date', vint)
    c0 = stage(asof, vint, lpm, '_c0', refresh=True, emit=True)
    T, T1 = pd.Timestamp(c0['T']), pd.Timestamp(c0['T1'])
    out('c0 production: T', str(T)[:10], 'T1', str(T1)[:10], 'GDP', c0['gdp'])
    pseudo = DATA / f'{vint}_c0_pseudo.pkl'
    c3 = stage(asof, vint, lpm, '_c3', ['--pseudo', str(pseudo)], emit=False)
    out('c3 chained: T', str(c3['T'])[:10], 'T1', str(c3['T1'])[:10], 'GDP', c3['gdp'])
    for i, r in c3['comps'].iterrows():
        out(f'  c3 {i:4s} growth {r.growth_pct:9.4f} contribution {r.contribution:8.4f}')
    # plumbing: the published values of T entered into data truncated at T - 1q
    inp, panel, act, qprices = pickle.load(open(DATA / f'{vint}_c0_public_inputs.pkl', 'rb'))['bundle']
    frames = {'nipa': inp.nipa, 'nominal': inp.nominal, 'q_hist': inp.q_hist, 'prices': qprices, 'actual': act}
    truth = chain.true_rows(frames, T)
    p1 = DATA / f'{vint}_c1_truth.pkl'
    pickle.dump(truth, open(p1, 'wb'))
    Tm = T - pd.offsets.QuarterEnd(1)
    c1 = stage(asof, vint, lpm, '_c1', ['--truncate-to', str(Tm.date()), '--pseudo', str(p1)])
    compare('c1 plumbing (true values of T entered)', c1, c0)
    # chain error: nowcast of T made on data truncated at T - 1q, entered, then nowcast T1
    Tm2 = Tm
    c2a = stage(asof, vint, lpm, '_c2a', ['--truncate-to', str(Tm2.date())], emit=True)
    out('c2a model nowcast of T on data through T-1q: GDP', c2a['gdp'], 'T1', str(c2a['T1'])[:10])
    c2b = stage(asof, vint, lpm, '_c2b', ['--truncate-to', str(Tm2.date()), '--pseudo', str(DATA / f'{vint}_c2a_pseudo.pkl')])
    compare('c2 chain error (own nowcast of T entered)', c2b, c0)
    out('actual vs nowcast growth of T, % SAAR: see c2a comps below')
    for i, r in c2a['comps'].iterrows():
        out(f'  c2a {i:4s} growth {r.growth_pct:9.4f}')


if __name__ == '__main__':
    main()
