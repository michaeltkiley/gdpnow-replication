"""Weights combining the bridge/monthly model with the quarterly BVAR (registry P05; Higgins 2014 eq. 8;
Mods Apr-2022, Jan-2023).

    y_t = d_B * BVAR_t + d_M * Bridge_t + e_t,   d_B + d_M = 1, each clipped to [0, 1]

Weighted least squares from 1985Q1 with weight 1/(1+t/80)^2 for an observation t quarters before the last
published quarter; 2020Q1-2020Q4 excluded (log-growth components).
"""
import numpy as np
import pandas as pd

from . import bridge as BR
from .config import load_toml

SPEC = load_toml('spec.toml')['blend']


def restricted_wls(y, x_bvar, x_model, exclude=True):
    d = pd.concat([y, x_bvar, x_model], axis=1, keys=['y', 'b', 'm']).dropna()
    if exclude:
        lo, hi = (pd.Period(q).end_time.normalize() for q in SPEC['exclude_range'])
        d = d[(d.index < lo - pd.offsets.QuarterEnd(1) + pd.Timedelta(days=1)) | (d.index > hi)]
    t = np.arange(len(d))[::-1]
    w = 1 / (1 + t / 80) ** 2
    z, x = d.y - d.m, d.b - d.m          # impose d_B + d_M = 1
    db = float((w * x * z).sum() / (w * x * x).sum())
    db = min(max(db, 0.0), 1.0)
    return 1 - db, db                    # (weight on monthly/bridge model, weight on BVAR)


def bridge_model_history(cid, inp, coefs, faar, start, hist_cache=None):
    """Historical bridge-model fit of component cid: sum of previous-quarter nominal shares times each
    subcomponent's fitted growth (indicator bridges use the nowcast quarter's availability pattern)."""
    hist_cache = {} if hist_cache is None else hist_cache
    subs = load_toml('bridges.toml')['components'][cid]['subcomponents']
    idx = pd.date_range(start, inp.T, freq='QE')
    fits = {}
    for s in subs:
        c = coefs[s['lhs']]
        if s['kind'] == 'ar1':
            lag = inp.q_hist[s['lhs']].shift(1).reindex(idx)
            f = c['Constant'] + c['AR1'] * lag
            for k, v in c.items():
                if k.startswith('COVIDDum'):
                    f = f + v * (idx == pd.Timestamp(k[8:12] + '-' + k[12:14]) + pd.offsets.MonthEnd(0)).astype(float)
        else:
            f = pd.Series(c['Constant'], index=idx)
            for ind in s['indicators']:
                if ind not in hist_cache:
                    hist_cache[ind] = BR.indicator_history(ind, inp, faar, start)
                f = f + c[ind] * hist_cache[ind][0].reindex(idx)
        fits[s['lhs']] = f
    nom = pd.DataFrame({s['lhs']: inp.nominal[s['nominal']] for s in subs}).shift(1).reindex(idx)
    shares = nom.div(nom.sum(axis=1), axis=0)
    # Quarters where any subcomponent's fit is unavailable are dropped (no partial sums).
    return (pd.DataFrame(fits) * shares).sum(axis=1, min_count=len(subs))
