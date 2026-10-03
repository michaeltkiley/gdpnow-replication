"""Bridge equations for detailed investment/government subcomponents (registry P03, P04; Higgins 2014 eqs.
5-6; Mods Apr-2022).

Indicator bridges: OLS from 1985Q1 of subcomponent log growth on the quarterly growth of its indicator(s),
where the historical indicator values are rebuilt with the current quarter's data-availability pattern (months
not yet released for the nowcast quarter are replaced, in every past quarter, by factor-augmented AR forecasts).
Subcomponents without an indicator: AR(1) plus four 2020 quarterly dummies, OLS from 1985Q1.
"""
import numpy as np
import pandas as pd

from . import components as C
from .config import load_toml

SPEC = load_toml('spec.toml')['bridge']


def missing_months(inp, series):
    """Months of the nowcast quarter for which any of `series` is unreleased (1, 2, 3 = month in quarter)."""
    ms = C.m.months(inp.T1, 3)
    return sorted({k for t in series for k, mo in enumerate(ms, 1) if np.isnan(inp.growth[t].get(mo, np.nan))})


def indicator_history(name, inp, faar, start):
    """Quarterly history of an indicator built with the nowcast quarter's availability pattern."""
    series = [t for t in C.indicator_series(name) if t not in set(inp.flags.get('drop_terms', []))]
    pattern = missing_months(inp, [t for t in series if t in inp.growth])
    # Quarters before every underlying series has a year of data are skipped (no fabricated history).
    firsts = [inp.growth[t].first_valid_index() for t in series if t in inp.growth]
    first_ok = max(firsts) + pd.offsets.QuarterEnd(4) if firsts else start
    out = {}
    for qe in pd.date_range(max(start, first_ok), inp.T, freq='QE'):
        mask = [C.m.months(qe, 3)[k - 1] for k in pattern]
        mon = C.Monthly(inp, inp.growth, inp.levels, faar, q_end=qe, mask=mask)
        out[qe] = C.indicator_growth(name, mon, inp)
    return pd.Series(out), pattern


def ols(y, X):
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    return b


def estimate_all(inp, faar):
    """Returns ({lhs: {rhs: coef}}, diagnostics)."""
    start = pd.Period(SPEC['sample_start']).end_time.normalize() + pd.offsets.QuarterEnd(0)
    bridges = load_toml('bridges.toml')['components']
    coefs, diag, hist_cache = {}, {}, {}
    for comp in bridges.values():
        for sub in comp['subcomponents']:
            y = inp.q_hist[sub['lhs']].loc[start:inp.T]
            if sub['kind'] == 'ar1':
                d = pd.DataFrame({'y': y, 'AR1': inp.q_hist[sub['lhs']].shift(1).loc[start:inp.T]})
                for q in SPEC['no_indicator_dummies']:
                    qe = pd.Period(q).end_time.normalize()
                    d[f'COVIDDum{qe:%Y%m}'] = (d.index == qe).astype(float)
                d = d.dropna()
                X = np.column_stack([np.ones(len(d))] + [d[c] for c in d.columns[1:]])
                b = ols(d.y.to_numpy(), X)
                coefs[sub['lhs']] = dict(zip(['Constant'] + list(d.columns[1:]), b))
                diag[sub['lhs']] = {'n': len(d), 'pattern': None}
                continue
            cols = {}
            for ind in sub['indicators']:
                if ind not in hist_cache:
                    hist_cache[ind] = indicator_history(ind, inp, faar, start)
                cols[ind] = hist_cache[ind][0]
            d = pd.concat([y.rename('y'), pd.DataFrame(cols)], axis=1).dropna()
            X = np.column_stack([np.ones(len(d))] + [d[i] for i in sub['indicators']])
            b = ols(d.y.to_numpy(), X)
            coefs[sub['lhs']] = dict(zip(['Constant'] + sub['indicators'], b))
            diag[sub['lhs']] = {'n': len(d), 'pattern': {i: hist_cache[i][1] for i in sub['indicators']}}
    return coefs, diag
