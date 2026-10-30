"""Update new housing value-put-in-place with housing starts before Census publishes it (Higgins 2014, appendix, eq. A2).

C30 residential spending is built by Census from starts and average sales prices, which are released one to two weeks earlier, so the
months between the two releases are estimated here. Single family: the sum over the 12 months of activity of the Census progress pattern
(by calendar month of start) times starts times average sales price, scaled to the last published month (Census values a start at cost,
not at the sales price, so the levels differ by a stable factor; the month-to-month change is what carries over). Multifamily: a two
variable BVAR of multifamily starts and spending with the month's starts known.
"""
import numpy as np
import pandas as pd

from . import bvar

# Percent of the value of single-family units started in a calendar month (columns Jan..Dec) put in place in the 1st..12th month of
# activity (rows). Census, Value Put in Place Methodology, "Monthly Progress Patterns for Private New Single Family Residential
# Buildings, by Month of Start".
PROGRESS = np.array([
    [15.8, 15.8, 15.8, 15.8, 15.9, 16.9, 16.9, 16.9, 16.9, 16.9, 16.9, 15.8],
    [21.7, 23.2, 22.5, 22.5, 22.9, 23.8, 23.8, 23.8, 23.8, 23.8, 21.7, 21.7],
    [21.0, 20.1, 20.8, 20.8, 20.8, 20.8, 20.8, 20.8, 20.8, 18.3, 18.3, 18.3],
    [16.3, 16.0, 16.0, 16.0, 16.0, 16.0, 16.0, 16.0, 13.7, 13.7, 13.7, 16.8],
    [10.6, 10.3, 10.3, 10.3, 10.3, 9.5, 9.5, 8.8, 8.8, 8.8, 11.7, 11.7],
    [6.1, 6.1, 6.1, 6.1, 6.1, 5.8, 5.8, 5.1, 5.1, 7.4, 7.6, 6.7],
    [3.5, 3.5, 3.5, 3.5, 3.5, 2.7, 2.7, 2.7, 4.3, 4.8, 4.3, 4.0],
    [2.0, 2.0, 2.0, 2.0, 1.5, 1.5, 1.5, 2.5, 2.8, 3.3, 2.8, 2.0],
    [1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2, 1.6, 2.0, 1.2, 1.2, 1.2],
    [0.8] * 12, [0.6] * 12, [0.4] * 12]) / 100

MF_LAGS, MF_LAMBDA = 2, 0.2          # BVAR on log differences of multifamily spending and starts (tightness: monthly-model default scale)


def _sf_index(starts, price, month):
    """Progress-weighted starts x price for one month (units: thousand starts x $ / 1000 = $ million SAAR), NaN if an input is missing."""
    tot = 0.0
    for k in range(12):
        t = month - pd.offsets.MonthEnd(k)
        if t not in starts.index or t not in price.index or np.isnan(starts[t]) or np.isnan(price[t]):
            return np.nan
        tot += PROGRESS[k, t.month - 1] * starts[t] * price[t] / 1000
    return tot


def extend_single_family(vpip, starts, price):
    """vpip: published single-family spending ($ million SAAR); starts: single-family starts (thousands SAAR); price: average sales price ($).
    Returns vpip extended through the last month with starts. A price not yet published is held at its last value."""
    vpip, starts = vpip.dropna(), starts.dropna()
    last = vpip.index.max()
    months = pd.date_range(last + pd.offsets.MonthEnd(1), starts.index.max(), freq='ME')
    if not len(months):
        return vpip
    price = price.dropna()
    price = price.reindex(price.index.union(pd.date_range(price.index.min(), starts.index.max(), freq='ME'))).ffill()
    base = _sf_index(starts, price, last)
    out = vpip.copy()
    for h in months:
        idx = _sf_index(starts, price, h)
        if np.isnan(idx) or np.isnan(base):
            break
        out[h] = vpip[last] * idx / base
    return out.sort_index()


def extend_multifamily(vpip, starts):
    """vpip: published multifamily spending ($ million SAAR); starts: multifamily starts (thousands SAAR). Months with starts but no spending
    are forecast by a two-variable BVAR (log differences) with that month's starts imposed."""
    vpip, starts = vpip.dropna(), starts.dropna()
    last = vpip.index.max()
    months = pd.date_range(last + pd.offsets.MonthEnd(1), starts.index.max(), freq='ME')
    if not len(months):
        return vpip
    both = pd.concat([np.log(vpip), np.log(starts.clip(lower=1))], axis=1, keys=['v', 's']).dropna()
    hist = 100 * both.diff().dropna()
    hist = hist.loc[:last]
    known = np.full((len(months), 2), np.nan)
    known[:, 1] = 100 * (np.log(starts.clip(lower=1)).reindex(months).to_numpy() - np.log(starts.clip(lower=1)).shift(1).reindex(months).to_numpy())
    B = bvar.fit(hist.to_numpy(), MF_LAGS, MF_LAMBDA, np.zeros(2), sum_coef=False)
    fc = bvar.conditional_forecast(hist.to_numpy(), B, MF_LAGS, len(months), known)
    out = vpip.copy()
    level = vpip[last]
    for h, g in zip(months, fc[:, 0]):
        level = level * np.exp(g / 100)
        out[h] = level
    return out.sort_index()
