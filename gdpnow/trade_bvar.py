"""AEI-window goods trade (registry P15, P16): the month after the last full trade report is known only from
the Census Advance Economic Indicators (AEI) report, which gives total goods trade and six end-use aggregates.

  * Gold BVAR (Mods Mar-2025): industrial supplies contain gold, so the AEI value is discarded and
    deflated industrial supplies ex gold are forecast by a 5-variable BVAR conditional on month-t CFNAI and the
    other five AEI aggregates. The Census-basis ex-gold total's growth then extrapolates BOP goods ex gold.
  * Capital-goods-shares BVAR (Mods Oct-2017): shares of aircraft, computers and core in total capital goods
    exports and imports, conditional on month-t shipments and AEI total capital goods (11 variables).

Public data: the six aggregates for month t come from the AEI report's Excel Table 1 (tab1adv.xlsx; cached in data/); the history,
BOP-basis gold and capital-goods detail from BEA's IDS-0182 (gdpnow/ids0182.py), whose Census-basis SA series
equal the AEI's published SA values.
"""
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import bvar
from . import public_data as P
from . import ids0182 as IDS
from .config import load_toml

SPEC = load_toml('spec.toml')
CATS = ['Foods, Feeds, & Beverages', 'Industrial Supplies', 'Capital Goods', 'Automotive Vehicles, etc.',
        'Consumer Goods', 'Other Goods']
CODES = ['0', '1', '2', '3', '4', '5']


AEI_XLSX = 'https://www.census.gov/econ/indicators/tab1adv.xlsx'     # Table 1 of the current Advance Economic Indicators report
_LABEL = {'Foods': CATS[0], 'Industrial': CATS[1], 'Capital': CATS[2], 'Automotive': CATS[3], 'Consumer': CATS[4], 'Other': CATS[5]}


def aei_table(cx, month):
    """Seasonally adjusted AEI Table 1 for `month` (month-end Timestamp): {(flow, category|'Total'): [t, t-1, t-2]}.
    Read from the report's Excel table at its fixed 'current' URL (the file holds the latest advance month only; its
    header must name `month`). Values in $ millions, in the order of the file's three monthly columns."""
    import io
    import openpyxl
    path = Path('data') / f'{cx.asof.replace("-", "")}_aei_{month.year}{month.month:02d}.xlsx'
    if not path.exists() or P.refresh_once(('aei', path.name)):
        path.write_bytes(P.get_bytes(AEI_XLSX))
    ws = openpyxl.load_workbook(io.BytesIO(path.read_bytes()), data_only=True).worksheets[0]
    rows = [[c for c in r if c is not None and str(c).strip() != ''] for r in ws.iter_rows(values_only=True)]     # cells in column order
    hi = next((i for i, r in enumerate(rows) if r and str(r[0]).strip() == month.strftime('%B')), None)
    if hi is None or str(rows[hi + 1][0]).strip() != str(month.year):
        raise ValueError(f'AEI Table 1 ({AEI_XLSX}) is not for {month:%B %Y}: header {rows[hi][:3] if hi is not None else None}')
    sa = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == 'Seasonally Adjusted')
    nsa = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == 'Not Seasonally Adjusted')
    out, flow = {}, None
    for r in rows[sa + 1:nsa]:
        if not r:
            continue
        label = re.sub(r'\s*\(\d\)', '', str(r[0]).strip())
        vals = [float(v) for v in r[1:4] if isinstance(v, (int, float))]
        if len(vals) != 3:
            continue
        if label in ('Exports', 'Imports'):
            flow = label.lower()
            out[(flow, 'Total')] = vals
        elif flow and label.split(',')[0].split()[0] in _LABEL:
            out[(flow, _LABEL[label.split(',')[0].split()[0]])] = vals
    if len(out) != 14:
        raise ValueError(f'AEI table not parsed ({len(out)} rows): {AEI_XLSX}')
    return out


def _sa(cx, flow, code):
    return IDS.series(cx, flow, code)


def _six(cx, flow):
    """The six AEI end-use aggregates (BEA IDS-0182, Census basis SA; exports 'Other goods' = codes 5 + 6)."""
    six = pd.DataFrame({c: _sa(cx, flow, c) for c in CODES})
    if flow == 'exports':
        six['5'] = six['5'] + _sa(cx, flow, '6')
    return six


def _fit_forecast(Y, known, lags, lam, delta):
    B = bvar.fit(Y.to_numpy(), lags, lam, np.asarray(delta, float), sum_coef=True)      # BGR sum-of-coefficients prior
    return bvar.conditional_forecast(Y.to_numpy(), B, lags, 1, known)[0]


def choose_lambda(hist, lags, grid, n_test):
    """Tightness for a BVAR whose lambda the documentation does not give: the grid value with the lowest
    recursive pseudo-out-of-sample error over the last n_test months, forecasting the first 6 variables (the
    shares) one step ahead conditional on the other variables of that month (re-estimated every run)."""
    Y = hist.to_numpy()
    err = {}
    for lam in grid:
        e = []
        for m in range(len(Y) - n_test, len(Y)):
            known = np.full((1, Y.shape[1]), np.nan)
            known[0, 6:] = Y[m, 6:]
            B = bvar.fit(Y[:m], lags, lam, np.ones(Y.shape[1]), sum_coef=True)
            f = bvar.conditional_forecast(Y[:m], B, lags, 1, known)[0]
            e.append(np.mean((f[:6] - Y[m, :6]) ** 2))
        err[lam] = np.mean(e)
    return min(err, key=err.get)


def gold_adjusted_growth(cx, prices, aei, t):
    """Growth of Census-basis goods exports/imports ex gold from t-1 to t (gold BVAR). Returns {'exports': g, 'imports': g}."""
    spec = SPEC['bvar_gold']
    tm1 = t - pd.offsets.MonthEnd(1)
    cfnai = cx.fred('CFNAI')
    out = {}
    for flow, pcol in (('exports', 'PXEA_USECONsplicefr'), ('imports', 'PMEA_USECONsplicefr')):
        six = _six(cx, flow)
        gold = IDS.series(cx, flow, 'NMGLD', 'BP-based').reindex(six.index).fillna(0)     # BOP-basis gold (Mods Mar-2025)
        price = prices[pcol].reindex(six.index.union([t]))
        a = {c: aei[(flow, c)] for c in CATS}
        other_t = sum(a[c][0] for c in CATS if c != 'Industrial Supplies')
        other = (six.drop(columns='1').sum(axis=1)).reindex(six.index.union([t]))
        other[t] = other_t
        isx = (six['1'] - gold)
        out[flow] = dict(is_exgold=isx, other=other, price=price, six=six, gold=gold)
    start = pd.Timestamp(spec['sample_start']) + pd.offsets.MonthEnd(0)
    idx = pd.date_range(start, t, freq='ME')
    cols = {}
    for flow in ('exports', 'imports'):
        o = out[flow]
        cols[f'is_{flow}'] = np.log(o['is_exgold'].reindex(idx) / o['price'].reindex(idx)).where(idx < t)
        cols[f'oth_{flow}'] = np.log(o['other'].reindex(idx) / o['price'].reindex(idx))
    cols['cfnai'] = cfnai.reindex(idx)
    Y = pd.DataFrame(cols)[['is_exports', 'is_imports', 'cfnai', 'oth_exports', 'oth_imports']]
    hist = Y.loc[:tm1]
    ok = hist.dropna().index
    hist = hist.loc[ok.min():]
    known = Y.loc[[t]].to_numpy()
    f = _fit_forecast(hist, known, spec['lags'], spec['lambda'], [1, 1, 0, 1, 1])
    res = {}
    for flow, k in (('exports', 0), ('imports', 1)):
        o = out[flow]
        is_t = np.exp(f[k]) * o['price'][t]
        exg_t = o['other'][t] + is_t
        exg_prev = o['six'].loc[tm1].sum() - o['gold'][tm1]
        res[flow] = exg_t / exg_prev
    return res


def capital_goods_t(cx, ship, aei, t):
    """Month-t nominal capital goods trade by category from the 11-variable shares BVAR.
    ship: nominal manufacturer shipments {'air','comp','core'} (month-end Series, may end before t).
    Returns {('exports'|'imports', 'air'|'comp'|'core'): nominal $mil}."""
    spec = SPEC['bvar_capital_goods_shares']
    tm1 = t - pd.offsets.MonthEnd(1)
    idx = pd.date_range(pd.Timestamp(spec['sample_start']) + pd.offsets.MonthEnd(0), t, freq='ME')
    eu = lambda flow, codes: pd.concat([_sa(cx, flow, c) for c in codes], axis=1).sum(axis=1, min_count=1)
    noncore = ['21300', '21301', '22000', '22010', '22020', '22220', '21320', '21100', '20005']
    cols, tot = {}, {}
    for flow in ('exports', 'imports'):
        total = _sa(cx, flow, '2').reindex(idx)
        a = aei[(flow, 'Capital Goods')]
        total[t] = a[0]
        tot[flow] = total
        parts = {'air': eu(flow, ['22000', '22010', '22020']),
                 'comp': eu(flow, ['21300', '21301'])}
        parts['core'] = _sa(cx, flow, '2') - eu(flow, noncore)
        for k, v in parts.items():
            cols[f'sh_{k}_{flow}'] = (v.reindex(idx) / total).where(idx < t)
        cols[f'ltot_{flow}'] = np.log(total)
    for k in ('air', 'comp', 'core'):
        cols[f'lship_{k}'] = np.log(ship[k].reindex(idx))
    order = [f'sh_{k}_{fl}' for fl in ('exports', 'imports') for k in ('air', 'comp', 'core')] + \
            ['lship_air', 'lship_comp', 'lship_core', 'ltot_exports', 'ltot_imports']
    Y = pd.DataFrame(cols)[order]
    hist = Y.loc[:tm1].dropna()
    lam = choose_lambda(hist, spec['lags'], spec['lambda_grid'], spec['cv_months'])
    f = _fit_forecast(hist, Y.loc[[t]].to_numpy(), spec['lags'], lam, np.ones(len(order)))
    res = {}
    for j, name in enumerate(order[:6]):
        _, k, flow = name.split('_')
        res[(flow, k)] = float(np.clip(f[j], 0, 1)) * float(tot[flow][t])
    return res
