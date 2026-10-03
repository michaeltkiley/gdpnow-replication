"""Durable long histories for short public series (growth store + layered backward splicing + Denton).

Problem: some public APIs only serve recent history (Census end-use trade from 2013, Treasury outlays from 2015,
Census manufactured-home prices from 2014...). The bridge and blend regressions need decades of data, and
sources can also truncate or change what they serve over time.

Design (decision 2026-10-03):
  * Everything is stored and spliced as GROWTH RATES (log differences, or differences for series that can
    be non-positive), never as levels, so rebasing, unit changes and level revisions at the source never break
    history. Levels are rebuilt each run by anchoring to the live series' first level and chaining backward.
  * Per date, growth comes from the first available layer:  live source  >  stored live growth (seen in an
    earlier run)  >  independent proxy 1  >  proxy 2 ...  Proxies must be real monthly data on a related
    quantity, never an interpolation of the dependent variable (that would make bridge fits circular).
  * Every run upserts what it saw into DuckDB table `hist_growth`: live values always (latest vintage wins);
    proxy values only where no live value exists, and they are replaced automatically when live data arrive.
    If a source later stops serving old months, the stored live growth keeps them.
"""
import numpy as np
import pandas as pd

TABLE = 'hist_growth'
RANK = {'live': 0, 'stored_live': 1}      # proxies get rank 2, 3, ...


def growth(level, kind='log'):
    return np.log(level / level.shift(1)) if kind == 'log' else level.diff()


def _load(con, name):
    try:
        df = con.execute(f'SELECT date, growth, source FROM {TABLE} WHERE name = ?', [name]).fetchdf()
    except Exception:
        return pd.DataFrame(columns=['date', 'growth', 'source'])
    return df


def _save(con, name, g, source, asof):
    df = pd.DataFrame({'name': name, 'date': g.index, 'growth': g.to_numpy(), 'source': source, 'as_of': str(asof)})
    con.register('_h', df)
    con.execute(f'CREATE TABLE IF NOT EXISTS {TABLE} AS SELECT * FROM _h LIMIT 0')
    con.execute(f'DELETE FROM {TABLE} WHERE name = ? AND date IN (SELECT date FROM _h)', [name])
    con.execute(f'INSERT INTO {TABLE} SELECT * FROM _h')
    con.unregister('_h')


CHECKS = 'hist_splice_checks'


def _overlap_corr(g1, g2, min_overlap):
    j = pd.concat([g1, g2], axis=1, keys=['a', 'b']).dropna()
    if len(j) < min_overlap:
        return None, len(j), None
    return float(j.a.corr(j.b)), len(j), (j.index.min(), j.index.max())


def _record_check(con, name, layer, corr, n, window, accepted, asof, note=''):
    df = pd.DataFrame([{'name': name, 'layer': layer, 'corr': corr, 'n_overlap': n,
                        'window_start': window[0] if window else None, 'window_end': window[1] if window else None,
                        'accepted': accepted, 'as_of': str(asof), 'note': note}])
    con.register('_c', df)
    con.execute(f'CREATE TABLE IF NOT EXISTS {CHECKS} AS SELECT * FROM _c LIMIT 0')
    con.execute(f'DELETE FROM {CHECKS} WHERE name = ? AND layer = ? AND as_of = ?', [name, layer, str(asof)])
    con.execute(f'INSERT INTO {CHECKS} SELECT * FROM _c')
    con.unregister('_c')


def splice(con, name, live, proxies=(), asof='', kind='log', reference=None, ref_label='workbook',
           min_corr=0.90, min_overlap=36):
    """Long monthly level series for `name`.

    live: current public series (levels, month-end index; may be short).
    proxies: ordered list of (label, level series): independent related PUBLIC monthly quantities.
    reference: optional long series from the published GDPNow workbook (public-by-availability). Used last,
      only for months nothing else covers (seeded once into the store; production does not need it again).
    Quality gate: a proxy (and the reference) is used only if its growth rates correlate >= min_corr with the
    reference over >= min_overlap overlapping months; every check is logged in hist_splice_checks, including
    live-vs-reference agreement (a low value flags a broken public construction).
    Returns (levels, info).
    """
    live = live.dropna()
    if len(live) == 0:
        raise ValueError(f'{name}: no live data')
    g_live = growth(live, kind).dropna()
    g_ref = growth(reference.dropna(), kind).dropna() if reference is not None else None
    if g_ref is not None:
        c, n, w = _overlap_corr(g_live, g_ref, min_overlap)
        _record_check(con, name, 'live_vs_reference', c, n, w, c is not None and c >= min_corr, asof,
                      'public construction vs GDPNow workbook' if c is not None else 'insufficient overlap')
    stored = _load(con, name)
    g_store = pd.Series(stored.growth.to_numpy(), index=pd.to_datetime(stored.date))
    src = pd.Series(stored.source.to_numpy(), index=pd.to_datetime(stored.date))
    # Definition-change guard: stored 'live' growth that no longer agrees with what the live source gives for the
    # same months means the series was redefined (revisions are small); its stored live rows are then discarded.
    old_live = g_store[src == 'live']
    ov = old_live.index.intersection(g_live.index)
    if len(ov) >= 12:
        c = float(old_live[ov].corr(g_live[ov]))
        mad = float((old_live[ov] - g_live[ov]).abs().median())
        if not (c >= 0.98 and mad <= 0.005):
            con.execute(f"DELETE FROM {TABLE} WHERE name = ? AND source = 'live'", [name])
            _record_check(con, name, 'store_reset', c, len(ov), None, False, asof,
                          f'stored live growth disagreed with current live (corr {c:.3f}, median abs diff {mad:.4f}): redefinition, rows dropped')
            g_store, src = g_store[src != 'live'], src[src != 'live']
    layers = [('live', g_live), ('stored_live', g_store[src == 'live'])]
    for lab, p in proxies:
        gp = growth(p.dropna(), kind).dropna()
        ok = True
        if g_ref is not None:
            c, n, w = _overlap_corr(gp, g_ref, min_overlap)
            ok = c is not None and c >= min_corr
            _record_check(con, name, f'proxy:{lab}', c, n, w, ok, asof)
        if ok:
            layers.append((f'proxy:{lab}', gp))
    layers.append(('stored_proxy', g_store[src.str.startswith('proxy:')]))
    layers.append(('stored_reference', g_store[src.str.startswith('workbook:')]))
    if g_ref is not None:
        layers.append((f'workbook:{ref_label}', g_ref))
    combined, source = pd.Series(dtype=float), pd.Series(dtype=object)
    for lab, g in layers:
        new = g[~g.index.isin(combined.index)]
        combined = pd.concat([combined, new])
        source = pd.concat([source, pd.Series(lab, index=new.index)])
    combined, source = combined.sort_index(), source.sort_index()
    t0 = live.index[0]
    _save(con, name, g_live, 'live', asof)
    persist = source[~source.isin(['live', 'stored_live', 'stored_proxy', 'stored_reference'])]
    have_live = set(g_store[src == 'live'].index) | set(g_live.index)
    for lab in persist.unique():
        idx = [d for d in persist[persist == lab].index if d not in have_live]
        if idx:
            _save(con, name, combined.loc[idx], lab, asof)
    level = live.copy()
    d = t0
    while True:
        gd = combined.get(d)
        if gd is None or np.isnan(gd):
            break
        p = d - pd.offsets.MonthEnd(1)
        level[p] = level[d] / np.exp(gd) if kind == 'log' else level[d] - gd
        d = p
    level = level.sort_index()
    pre = source[source.index <= t0]
    return level, {'start': level.index[0], 'live_start': t0, 'layers': pre.groupby(pre).size().to_dict()}


def denton_pfd(quarterly, indicator, average=True):
    """Proportional first-difference Denton (Bloem et al. 2001, ch. 6): monthly series whose quarterly
    average (or sum) equals `quarterly` and whose movement follows `indicator`; beyond the last quarter the
    series is extrapolated with the indicator's growth. quarterly: quarter-end index; indicator: month-end."""
    q = quarterly.dropna()
    months = indicator.dropna().index
    months = months[(months > q.index.min() - pd.offsets.QuarterEnd(1)) & (months <= q.index.max())]
    months = months[months >= (q.index.min() - pd.offsets.MonthEnd(2))]
    n = len(months)
    pos = {m: i for i, m in enumerate(months)}
    qs = [qe for qe in q.index if all((qe - pd.offsets.MonthEnd(k)) in pos for k in range(3))]
    I = indicator.reindex(months).to_numpy(float)
    J = np.zeros((len(qs), n))
    for r, qe in enumerate(qs):
        for k in range(3):
            J[r, pos[qe - pd.offsets.MonthEnd(k)]] = (1 / 3 if average else 1.0)
    y = q.loc[qs].to_numpy(float)
    D = np.zeros((n - 1, n))
    for i in range(n - 1):
        D[i, i], D[i, i + 1] = -1, 1
    # z = x / I ;  minimise ||D z||^2  s.t.  J diag(I) z = y   (KKT system)
    A = J * I[None, :]
    H = D.T @ D + 1e-12 * np.eye(n)
    K = np.block([[2 * H, A.T], [A, np.zeros((len(qs), len(qs)))]])
    rhs = np.concatenate([np.zeros(n), y])
    sol = np.linalg.solve(K, rhs)
    out = pd.Series(sol[:n] * I, index=months)
    last_q = max(qs)
    ext = indicator.loc[indicator.index > last_q].dropna()
    for t in ext.index:
        p = t - pd.offsets.MonthEnd(1)
        out[t] = out[p] * indicator[t] / indicator[p]
    return out.sort_index()
