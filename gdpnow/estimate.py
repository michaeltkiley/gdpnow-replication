"""Estimate model parameters from data and return an Inputs bundle carrying our estimates (L2/L3).

Each step records which Inputs fields it replaced; fields not yet produced by our code keep their source
label, so the provenance check in stage 06 shows exactly what remains workbook-sourced.
"""
import dataclasses

import numpy as np
import pandas as pd

from . import bvar, blend, bridge, faar as FA, factor as FC
from .config import ROOT, load_toml

SPEC = load_toml('spec.toml')
BRIDGE_IDS = ['FNEZ', 'FNPZ', 'FNSZ', 'FRZ', 'GFZ', 'GSZ']
COMPS = ['CTG', 'CS', 'FNE', 'FNP', 'FNS', 'FR', 'XM', 'XS', 'MM', 'MS', 'GF', 'GS']


def factor_panel(growth, tcodes, extra):
    """Factor inputs: monthly series with a transformation code, plus extra level series (e.g. surveys)."""
    return growth[[t for t in tcodes]].join(extra)


def quarterly_bvars(inp, prices):
    """Quantity and price BVARs (5 lags, documented lambda). Returns forecasts, fitted history, T1 prices."""
    nipa, T, p = inp.nipa, inp.T, SPEC['bvar_quarterly']['lags']
    start = pd.Period(SPEC['bvar_quarterly']['sample_start']).end_time.normalize()
    L = pd.DataFrame({c: np.log(nipa[c + 'Z_USNA']) for c in COMPS})
    L['V'] = nipa['VZ_USNA'] / nipa['GDPZ_USNA'].shift(1)
    L = L.loc[start:T]
    Y = L.to_numpy()
    delta = np.array([1.0] * len(COMPS) + [0.0])
    B = bvar.fit(Y, p, SPEC['bvar_quarterly']['lambda_quantities'], delta)
    fitted, fc = bvar.one_step(Y, B, p)
    fc_d = {c + 'Z': 400 * (fc[i] - Y[-1, i]) for i, c in enumerate(COMPS)}
    fc_d['VZ'] = fc[-1] * nipa['GDPZ_USNA'][T]
    hist = pd.DataFrame(400 * (fitted[:, :len(COMPS)] - Y[p - 1:-1, :len(COMPS)]), index=L.index[p:],
                        columns=[c + 'Z' for c in COMPS])
    cols = list(prices.columns)
    LP = np.log(prices.loc[start:T, cols])
    BP = bvar.fit(LP.to_numpy(), p, SPEC['bvar_quarterly']['lambda_prices'], np.ones(len(cols)))
    _, fcp = bvar.one_step(LP.to_numpy(), BP, p)
    prices_T1 = {c.replace('_USNAqtr', ''): float(np.exp(x)) for c, x in zip(cols, fcp)}
    return fc_d, hist, prices_T1


def estimate_core(inp, panel, actual_qgrowth, prices):
    """M2 core: factor -> FA-ARs -> bridges -> quarterly BVARs -> blend weights (investment/government).

    inp: Inputs carrying data; panel: factor input panel; actual_qgrowth: published component log growth
    (for blend regressions); prices: quarterly implicit deflators history. Returns (Inputs, replaced, diag).
    """
    diag = {}
    fres = FC.estimate(panel, inp.T1 + pd.offsets.QuarterEnd(1))
    diag['factor'] = fres
    f = fres.factor
    used = pd.read_csv(ROOT / 'registry' / 'inputs_used.csv')
    used = used[used.sheet.isin(['FactorAugARCoeffs', 'ConsFactorAugARCoeffs'])]
    faar = {}
    for sheet, t in used[['sheet', 'key']].values:
        cons = sheet == 'ConsFactorAugARCoeffs'
        y = (inp.cons_growth if cons else inp.growth)[t]
        coef, q, r, _ = FA.estimate(y, f, consumption=cons, const='const' in inp.faar[t])
        faar[t] = coef
        diag.setdefault('faar_lags', {})[t] = (q, r)
    stage = dataclasses.replace(inp, factor=f, faar=faar)
    coefs, bdiag = bridge.estimate_all(stage, faar)
    diag['bridge'] = bdiag
    fc, hist, prices_T1 = quarterly_bvars(inp, prices)
    start = pd.Period(SPEC['blend']['sample_start']).end_time.normalize()
    stage = dataclasses.replace(stage, bridge=coefs)
    w, cache = dict(inp.blend), {}
    for cid in BRIDGE_IDS:
        mhist = blend.bridge_model_history(cid, stage, coefs, faar, start, cache)
        w[cid] = blend.restricted_wls(actual_qgrowth[cid].loc[start:inp.T], hist[cid].loc[start:inp.T], mhist)
    out = dataclasses.replace(stage, bvar=fc, prices_T1=prices_T1, blend=w)
    replaced = ['factor', 'faar', 'bridge', 'bvar', 'prices_T1', 'blend:investment_government']
    return out, replaced, diag


def attribution(base, est, fields, run):
    """Headline effect of replacing workbook fields with our estimates: each field alone, and cumulatively."""
    g0 = run(base)
    rows, cum = [], base
    for label, names in fields:
        alone = dataclasses.replace(base, **{n: getattr(est, n) for n in names})
        cum = dataclasses.replace(cum, **{n: getattr(est, n) for n in names})
        rows.append((label, run(alone) - g0, run(cum) - g0))
    return pd.DataFrame(rows, columns=['stage', 'alone', 'cumulative'])
