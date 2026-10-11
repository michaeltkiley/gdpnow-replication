"""Next-quarter nowcast by chaining (DD8): the production quarter's own nowcast is entered as if it were the published estimate, and the model is run
for the quarter after it.

The production quarter T1 is the quarter after the latest quarter with a BEA estimate. Between the end of T1 and BEA's advance estimate the model could
also be asked for T1 + 1, but its quarterly inputs (levels, deflators, inventory stocks, detailed growth) end at T. `rows` turns a finished production run
into one pseudo-actual row for T1 in each quarterly table; `extend` appends it to the tables `public_nipa.build` returns, and everything downstream
(T, T1, estimation samples, the assembly) follows from the data. The rows hold only what the next run reads at T (COLUMNS); the rest stay missing.
No GDPNow number exists for this quarter, so there is nothing to benchmark against; the figure carries the production nowcast's error.
"""
import numpy as np
import pandas as pd

from . import components as C
from .public_nipa import AGG

NONINV = [c for c in AGG if c not in ('GDP', 'V')]          # the 12 non-inventory components
STOCKS = [d['stock'] for d in C.CENSUS_INV + C.REAL_PATH_INV + C.AR_INV]
INV_PRICES = [d['price'] for d in C.CENSUS_INV + C.REAL_PATH_INV + C.AR_INV]
SUBS = [(cid, s) for cid, comp in C.BRIDGES['components'].items() for s in comp['subcomponents']]

# The table columns that get a pseudo-actual value for T1.
COLUMNS = {
    'nipa': ['GDPZ_USNA', 'GDPX_USNA'] + [f'{c}{k}_USNA' for c in AGG if c != 'GDP' for k in 'ZX'] + STOCKS + INV_PRICES + ['SZ_USNA'],
    'nominal': [s['nominal'] for _, s in SUBS] + ['LightTruckShare'],
    'q_hist': [s['lhs'] for _, s in SUBS],
    'prices': [f'{c}Z_USNAqtr' for c in NONINV] + ['VZ_USNAqtr'],
    'actual': [f'{c}Z' for c in NONINV],
}


def rows(inp, comps, agg, details):
    """Pseudo-actual values for quarter inp.T1 from a finished production run (the Inputs `inp` with its estimates, the components DataFrame and aggregates
    dict nowcast.run returned, and its `details`). Returns {table: one-row DataFrame indexed by T1}."""
    T, T1, nipa = inp.T, inp.T1, inp.nipa
    pct = comps.set_index('id').growth_pct
    z = {c: nipa[f'{c}Z_USNA'][T] * (1 + pct[c] / 100) ** 0.25 for c in NONINV}
    z['V'] = agg['cipi_level'] * 1000
    p1 = {c: inp.prices_T1[f'{c}Z'] for c in NONINV}
    x = {c: z[c] * p1[c] / 100 for c in NONINV}
    x['V'] = z['V'] * (inp.prices_T1['VZ'] + inp.prices_T0['VZ']) / 200          # average end-of-quarter inventory price, as in aggregate.py
    n = {f'{c}Z_USNA': z[c] for c in z}
    n.update({f'{c}X_USNA': x[c] for c in x})
    n['GDPZ_USNA'] = nipa['GDPZ_USNA'][T] * (1 + agg['GDP'] / 100) ** 0.25
    n['GDPX_USNA'] = sum(x[c] for c in NONINV if c not in ('MM', 'MS')) - x['MM'] - x['MS'] + x['V']
    inv = details['INV']
    for st, pr in zip(STOCKS, INV_PRICES):
        n[st], n[pr] = inv[f'inv:{st}'][2] * 1000, inv[f'invp:{st}'][2]
    n['SZ_USNA'] = inv['inv_total'][2] * 1000
    nom, qh = {}, {}
    for cid, s in SUBS:
        g = details[cid][f'sub:{s["lhs"]}']
        qh[s['lhs']] = g
        nom[s['nominal']] = inp.nominal[s['nominal']][T] * np.exp(g / 400) * inp.prices_T1[cid] / inp.prices_T0[cid]
    nom['LightTruckShare'] = inp.nominal['LightTruckShare'][T]                    # slow-moving: carried forward
    pr = {f'{c}Z_USNAqtr': p1[c] for c in NONINV}
    pr['VZ_USNAqtr'] = inp.prices_T1['VZ']
    act = {f'{c}Z': 400 * np.log(z[c] / nipa[f'{c}Z_USNA'][T]) for c in NONINV}
    out = {'nipa': n, 'nominal': nom, 'q_hist': qh, 'prices': pr, 'actual': act}
    for k, v in out.items():
        assert set(v) == set(COLUMNS[k]), (k, set(v) ^ set(COLUMNS[k]))
        out[k] = pd.DataFrame([v], index=pd.DatetimeIndex([T1]))
    return out


def true_rows(frames, t):
    """The published values for quarter t, restricted to COLUMNS, in the form `rows` returns (the plumbing test: entering the actual values must reproduce
    the standard run). `frames` maps table name to DataFrame."""
    return {k: frames[k].loc[[t], COLUMNS[k]] for k in COLUMNS}


def extend(q, add):
    """Append the pseudo-actual rows to the quarterly tables `public_nipa.build` returned (dict of DataFrames)."""
    out = dict(q)
    for k, df in add.items():
        out[k] = pd.concat([q[k], df]).sort_index()
    return out


def truncate(q, t):
    """The quarterly tables as they stood with quarter t the latest actual (test use)."""
    return {k: v.loc[:t] for k, v in q.items()}
