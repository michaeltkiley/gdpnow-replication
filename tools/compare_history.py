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


def snap(asof):
    from gdpnow import store
    con = store.connect()
    df = con.execute('SELECT source, series, date, value FROM raw_pulls WHERE as_of = ?', [asof]).fetchdf()
    con.close()
    df['date'] = pd.to_datetime(df['date'])
    return df


def raw_diff(before, after):
    """What the fresh pulls changed against the pulls in the restored state (taken when the production run of the day ran)."""
    m = before.merge(after, on=['source', 'series', 'date'], how='outer', suffixes=('_0', '_1'), indicator=True)
    both = m[m['_merge'] == 'both']
    rel = (both['value_1'] - both['value_0']).abs() / both['value_0'].abs().clip(lower=1.0)
    ch = both[rel > 1e-9].assign(rel=rel[rel > 1e-9])
    out('RAWDIFF rows before', len(before), 'after', len(after), 'changed', len(ch), 'new', int((m['_merge'] == 'right_only').sum()), 'removed', int((m['_merge'] == 'left_only').sum()))
    for src, g in m.groupby('source'):
        n_ch = int(((g['_merge'] == 'both') & ((g['value_1'] - g['value_0']).abs() / g['value_0'].abs().clip(lower=1.0) > 1e-9)).sum())
        n_new, n_rm = int((g['_merge'] == 'right_only').sum()), int((g['_merge'] == 'left_only').sum())
        if n_ch or n_new or n_rm:
            out('RAWDIFF source', src, 'changed', n_ch, 'new', n_new, 'removed', n_rm)
    for (src, ser), g in ch.groupby(['source', 'series']):
        out('RAWDIFF changed', src, ser, 'rows', len(g), 'max rel', f"{g['rel'].max():.4g}", 'first', str(g['date'].min())[:10], 'last', str(g['date'].max())[:10])
    nw = m[m['_merge'] == 'right_only']
    for (src, ser), g in nw.groupby(['source', 'series']):
        out('RAWDIFF new', src, ser, 'rows', len(g), 'dates', str(g['date'].min())[:10], str(g['date'].max())[:10])
    rm = m[m['_merge'] == 'left_only']
    for (src, ser), g in rm.groupby(['source', 'series']):
        out('RAWDIFF removed', src, ser, 'rows', len(g))


def np_diff(x):
    import numpy as np
    return np.log(x.where(x > 0)).diff() if (x > 0).all() else x.diff()


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
    before = snap(asof)
    sh('02_build_public.py', '--asof', asof, '--last-price-month', lpm, '--ism', 'public', '--tag', tag)
    raw_diff(before, snap(asof))
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
    try:
        import numpy as np
        from gdpnow import inputs
        con = store.connect()
        wbi = inputs.from_workbook(con, store.latest_vintage(con))
        con.close()
        res['wb_series'] = {t: info(getattr(wbi, t)) for t in TABLES}
        keep = ['growth', 'levels', 'q_hist', 'nominal', 'nipa']
        res['ours_tables'] = {t: getattr(inp, t) for t in keep}
        res['wb_tables'] = {t: getattr(wbi, t) for t in keep}
        eff = {}
        for sname in ['SFZ_USNA', 'SNOZ_USNAqtrExtrap']:      # inventory.farm_other: AR(4) 'from 1985Q1 (P11)'
            g = 400 * np.log(inp.nipa[sname] / inp.nipa[sname].shift(1))
            d = pd.concat([g] + [g.shift(k) for k in range(1, 5)], axis=1).loc['1985-03-31':inp.T].dropna()
            eff['farm_other AR(4) ' + sname] = (str(d.index.min())[:10], len(d))
        res['eff'] = eff
        con = store.connect()
        try:
            chk = con.execute("SELECT name, layer, corr, n_overlap, accepted FROM hist_splice_checks WHERE name = 'SNOZ_USNAqtrExtrap' QUALIFY row_number() OVER (PARTITION BY layer ORDER BY as_of DESC) = 1").fetchall()
        except Exception:
            chk = []
        con.close()
        res['eff']['splice checks SNOZ_USNAqtrExtrap'] = str(chk)
    except Exception as e:                  # exploration only: never lose the main results
        out('WB-COMPARE-ERROR', repr(e)[:300])
    pickle.dump(res, open(DATA / f'compare_{variant}.pkl', 'wb'))
    out('RUN', variant, asof, 'GDP', res['agg'].get('GDP'))


def variants_summary(b):
    """One line per extra variant: nowcast and component contributions against base, and how many estimates moved."""
    for path in sorted(glob.glob(str(DATA / '**/compare_*.pkl'), recursive=True)):
        v = pickle.load(open(path, 'rb'))
        if v['variant'] in ('base', 'full'):
            continue
        keys = [c for c in b['comps'].columns if c not in ('run_id', 'growth_pct', 'contribution')]
        m = b['comps'].merge(v['comps'], on=keys, suffixes=('_b', '_v'))
        d = ' '.join(f"{r.id}={r.contribution_v - r.contribution_b:+.4f}" for r in m.itertuples() if abs(r.contribution_v - r.contribution_b) > 5e-4)
        dm = b['diag'].merge(v['diag'], on=['block', 'item', 'term'], how='outer', suffixes=('_b', '_v'))
        n = int((pd.to_numeric(dm['ours_v'], errors='coerce') - pd.to_numeric(dm['ours_b'], errors='coerce')).abs().gt(1e-9).sum())
        out('VARIANT', v['variant'], 'GDP', round(v['agg']['GDP'], 5), 'diff vs base', f"{v['agg']['GDP'] - b['agg']['GDP']:+.5f}", '| contributions moving >0.0005:', d, '| estimates changed', n)


def compare():
    b = pickle.load(open(glob.glob(str(DATA / '**/compare_base.pkl'), recursive=True)[0], 'rb'))
    variants_summary(b)
    f = pickle.load(open(glob.glob(str(DATA / '**/compare_full.pkl'), recursive=True)[0], 'rb'))
    out('asof', b['asof'], f['asof'])
    out('GDP nowcast base', b['agg']['GDP'], 'full', f['agg']['GDP'], 'diff', f['agg']['GDP'] - b['agg']['GDP'])
    # components
    cols = [c for c in b['comps'].columns if c != 'run_id']
    num = [c for c in cols if pd.api.types.is_numeric_dtype(b['comps'][c])]
    keys = [c for c in cols if c not in num]
    m = b['comps'].merge(f['comps'], on=keys, suffixes=('_b', '_f'))
    for c in num:
        m['d_' + c] = m[c + '_f'] - m[c + '_b']
    dcols = ['d_' + c for c in num]
    m['maxd'] = m[dcols].abs().max(axis=1)
    out('components: keys', keys, 'numeric', num, 'rows', len(m))
    for r in m.sort_values('maxd', ascending=False).head(14).itertuples():
        out('component', *[getattr(r, k) for k in keys], 'maxabs', f'{r.maxd:.5f}', *[f'{c}={getattr(r, c):+.5f}' for c in dcols])
    # estimation diagnostics (blend_sample rows hold the blend regression sample: term = first date, ours = number of quarters)
    bd, fd = b['diag'], f['diag']
    bsb, bsf = bd[bd['block'] == 'blend_sample'], fd[fd['block'] == 'blend_sample']
    bsm = bsb[['item', 'term', 'ours']].merge(bsf[['item', 'term', 'ours']], on='item', how='outer', suffixes=('_b', '_f'))
    out('blend samples (component, start base -> full, n base -> full):', len(bsm), 'differing:', int(((bsm.term_b != bsm.term_f) | (bsm.ours_b != bsm.ours_f)).sum()))
    for r in bsm.itertuples():
        if r.term_b != r.term_f or r.ours_b != r.ours_f:
            out('blend_sample', r.item, 'start', r.term_b, '->', r.term_f, 'n', r.ours_b, '->', r.ours_f)
    bd, fd = bd[bd['block'] != 'blend_sample'], fd[fd['block'] != 'blend_sample']
    dm = bd.merge(fd, on=['block', 'item', 'term'], how='outer', suffixes=('_b', '_f'))
    dm['d'] = pd.to_numeric(dm['ours_f'], errors='coerce') - pd.to_numeric(dm['ours_b'], errors='coerce')
    ch = dm[dm['d'].abs() > 1e-9]
    out('estimates compared', len(dm), 'changed (>1e-9):', len(ch))
    for blk, g in ch.groupby('block'):
        out('estimates block', blk, 'changed', len(g), 'of', int((dm['block'] == blk).sum()), 'max abs change', f"{g['d'].abs().max():.5f}")
    for r in ch.reindex(ch['d'].abs().sort_values(ascending=False).index).head(14).itertuples():
        out('estimate', r.block, r.item, r.term, f'base={float(r.ours_b):.5f} full={float(r.ours_f):.5f}')
    # effective samples of regressions with a documented start
    for k in sorted(set(b.get('eff', {})) | set(f.get('eff', {}))):
        out('effective sample', k, 'base', b.get('eff', {}).get(k), 'full', f.get('eff', {}).get(k))
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
        wbi = f.get('wb_series', {}).get(t, {}).get(c)
        line = ['series', t, c, 'base', vb, 'full', vf, '| workbook', wbi]
        try:
            if t in f.get('ours_tables', {}) and c in f['ours_tables'][t].columns and c in f['wb_tables'][t].columns:
                o, w = f['ours_tables'][t][c].dropna(), f['wb_tables'][t][c].dropna()
                if t in ('levels', 'nominal', 'nipa') or (o > 0).all() and (w > 0).all():
                    go, gw = np_diff(o), np_diff(w)
                else:
                    go, gw = o.diff(), w.diff()
                j = pd.concat([go, gw], axis=1, keys=['o', 'w']).dropna()
                ext = j[j.index < pd.Timestamp(vb[0]) + pd.offsets.QuarterEnd(0)] if isinstance(vb, tuple) and vb[0] else j.iloc[0:0]
                flat = len(ext) > 2 and (ext.o.std() == 0 or ext.w.std() == 0)
                line += ['| overlap', len(j), 'corr', round(float(j.o.corr(j.w)), 4) if len(j) > 2 else None,
                         '| extension part', len(ext), 'corr', 'FLAT (zero variance)' if flat else (round(float(ext.o.corr(ext.w)), 4) if len(ext) > 2 else None)]
        except Exception as e:
            line += ['| overlap error', repr(e)[:80]]
        out(*line)


if __name__ == '__main__':
    {'run': run, 'compare': compare}[sys.argv[1]]()
