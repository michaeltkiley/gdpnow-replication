"""Monthly-to-quarterly machinery shared by every component (Higgins 2014, eqs. 3-5).

Conventions: monthly growth is 1200*dlog (tcode 3) or 1200... first difference (tcode 2) as stored in the
transformed panel; quarterly growth is 400*dlog of the quarterly average (SAAR). Dates are month ends.
"""
import numpy as np
import pandas as pd


def months(end, n):
    """The n month-end dates ending at `end`, oldest first."""
    return pd.date_range(end=pd.Timestamp(end), periods=n, freq='ME')


def faar_fill(x, factor, coef, start, end):
    """Fill missing values of monthly growth series x on [start, end] with the factor-augmented AR (eq. 3).

    coef: dict with const, ar1..ar12, f0..f3 (missing terms are zero). Recursion uses already-filled values.
    """
    x = x.reindex(x.index.union(pd.date_range(start, end, freq='ME'))).copy()
    ar = np.array([coef.get(f'ar{k}', 0.0) for k in range(1, 13)])
    fb = np.array([coef.get(f'f{j}', 0.0) for j in range(4)])
    for t in pd.date_range(start, end, freq='ME'):
        if np.isnan(x.get(t, np.nan)):
            lags = x.reindex(months(t - pd.offsets.MonthEnd(1), 12))[::-1].to_numpy()   # lag1..lag12
            flags = factor.reindex(months(t, 4))[::-1].to_numpy()                       # f_t..f_{t-3}
            x[t] = coef.get('const', 0.0) + np.nansum(ar * lags) + np.dot(fb, flags)
    return x


def level_fill(level, growth, start, end, additive=False):
    """Extend a level series over [start, end] using growth where the level is missing."""
    level = level.reindex(level.index.union(pd.date_range(start, end, freq='ME'))).copy()
    for t in pd.date_range(start, end, freq='ME'):
        if np.isnan(level.get(t, np.nan)):
            prev = level[t - pd.offsets.MonthEnd(1)]
            level[t] = prev + growth[t] if additive else prev * np.exp(growth[t] / 1200)
    return level


def q_growth_from_growth(g, q_end):
    """400*ln(sum of quarter-q monthly levels / sum of previous quarter's), from monthly 1200*dlog growth."""
    m = months(q_end, 5)
    cum = np.exp(np.cumsum(g.reindex(m).to_numpy()) / 1200)      # levels of months -4..0 relative to month -5
    lev = np.concatenate([[1.0], cum])
    return 400 * np.log(lev[3:].sum() / lev[:3].sum())


def q_growth_from_levels(level, q_end):
    m = months(q_end, 6)
    v = level.reindex(m).to_numpy()
    return 400 * np.log(v[3:].mean() / v[:3].mean())


def q_mean(level, q_end):
    return level.reindex(months(q_end, 3)).mean()
