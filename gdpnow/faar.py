"""Factor-augmented autoregressions for monthly indicators (registry P02; Higgins 2014 eq. 3; Mods Apr-2022).

    y_t = a + sum_{k=1..q} c_k y_{t-k} + sum_{j=0..r} b_j f_{t-j} + sum_{h=0..9} d_h D(2020-03 + h) + e_t

q and r are chosen by AIC over the documented ranges. Returns coefficients in the same layout the assembly
consumes: const, ar1..ar12, f0..f3 (COVID dummies are estimated but are zero outside 2020).
"""
import itertools

import numpy as np
import pandas as pd

from .config import load_toml

SPEC = load_toml('spec.toml')['faar']


def covid_dummies(index):
    start = pd.Timestamp(SPEC['covid_dummies'][0]) + pd.offsets.MonthEnd(0)
    end = pd.Timestamp(SPEC['covid_dummies'][1]) + pd.offsets.MonthEnd(0)
    months = pd.date_range(start, end, freq='ME')
    return pd.DataFrame({f'covid{m:%Y%m}': (index == m).astype(float) for m in months}, index=index)


def design(y, f, q, r, max_q, max_r, const):
    cols = {f'ar{k}': y.shift(k) for k in range(1, q + 1)}
    cols.update({f'f{j}': f.shift(j) for j in range(r + 1)})
    X = pd.DataFrame(cols, index=y.index)
    if const:
        X.insert(0, 'const', 1.0)
    X = X.join(covid_dummies(y.index))
    # Common estimation sample across all candidate (q, r): requires max_q own lags and max_r factor lags.
    need = pd.concat([y] + [y.shift(k) for k in range(1, max_q + 1)] + [f.shift(j) for j in range(max_r + 1)], axis=1)
    ok = need.notna().all(axis=1)
    return X[ok], y[ok]


def estimate(y, f, consumption=False, const=True):
    """y: monthly growth series (NaN = unreleased); f: factor. Returns (coef dict, q, r, aic)."""
    qmin, qmax = SPEC['own_lags_consumption'] if consumption else SPEC['own_lags']
    rmin, rmax = SPEC['factor_lags']
    y = y.dropna()
    f = f.reindex(y.index.union(f.index))
    y = y.reindex(f.index)
    best = None
    for q, r in itertools.product(range(qmin, qmax + 1), range(rmin, rmax + 1)):
        X, yy = design(y, f, q, r, qmax, rmax, const)
        X = X.loc[:, X.abs().sum() > 0]          # drop dummies for months outside the sample
        if len(yy) < max(3 * X.shape[1], 24):
            continue
        b, *_ = np.linalg.lstsq(X.to_numpy(), yy.to_numpy(), rcond=None)
        ssr = float(((yy.to_numpy() - X.to_numpy() @ b) ** 2).sum())
        n, k = len(yy), X.shape[1]
        aic = n * np.log(ssr / n) + 2 * k
        if best is None or aic < best[0]:
            best = (aic, q, r, dict(zip(X.columns, b)))
    if best is None:
        raise ValueError(f'FA-AR: insufficient data ({y.notna().sum()} obs)')
    aic, q, r, coef = best
    out = {k: v for k, v in coef.items() if not k.startswith('covid')}
    return out, q, r, aic
