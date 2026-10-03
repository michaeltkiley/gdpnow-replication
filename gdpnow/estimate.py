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
        src = inp.cons_growth if cons else inp.growth
        if t not in src or src[t].dropna().shape[0] < 60:
            continue                       # series not available (e.g. existing-home sales in L3)
        # Constant in every equation except the net-export contribution ones (WP eq. 10).
        coef, q, r, _ = FA.estimate(src[t], f, consumption=cons, const=not t.startswith('NetExports') and not t.startswith('NetSvc'))
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
    diag['bvar_hist'] = hist
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


# ---------------------------------------------------------------- M3: prices, consumption regressions, trade
MGDP = 'MGDPN_USECONsplicefr'
CONS_DEFLATORS = ['CoreRealRetailPCEDefExFoodSvcfr', 'CSFPMDeffr']


def monthly_price_bvar(prices, conditioners, last_actual, mgdp_last):
    """Monthly price BVAR (P08; WP step 2a, Table A2): log differences of the monthly price panel plus
    conditioning series (oil, ISM prices), 12 lags, documented lambda, Waggoner-Zha conditional forecasts for
    months where only the conditioners are known. Monthly nominal GDP is extended at a constant 4.5% SAAR
    after its last actual month (WP fn 21; registry R10). Returns price levels with model tails."""
    spec = SPEC['bvar_monthly_prices']
    lv = prices.drop(columns=[MGDP]).loc[:last_actual]
    D = 1200 * np.log(lv / lv.shift(1))
    cond = conditioners.copy()
    D = D.join(cond.loc[:last_actual])
    start = pd.Timestamp(spec['sample_start']) + pd.offsets.MonthEnd(0)
    Y = D.loc[start:].dropna(axis=1, how='any')
    horizon = 4
    future = pd.date_range(last_actual + pd.offsets.MonthEnd(1), periods=horizon, freq='ME')
    known = np.full((horizon, Y.shape[1]), np.nan)
    for c in cond.columns:
        if c in Y:
            known[:, list(Y.columns).index(c)] = cond[c].reindex(future).to_numpy()
    B = bvar.fit(Y.to_numpy(), spec['lags'], spec['lambda'], np.zeros(Y.shape[1]), sum_coef=False)
    fc = pd.DataFrame(bvar.conditional_forecast(Y.to_numpy(), B, spec['lags'], horizon, known),
                      index=future, columns=Y.columns)
    out = lv.copy().reindex(lv.index.union(future))
    for c in lv.columns:
        if c in fc:
            for t in future:
                out.loc[t, c] = out.loc[t - pd.offsets.MonthEnd(1), c] * np.exp(fc.loc[t, c] / 1200)
    g = prices[MGDP].loc[:mgdp_last]
    ext = pd.date_range(mgdp_last + pd.offsets.MonthEnd(1), future[-1], freq='ME')
    vals = g.iloc[-1] * np.exp(np.arange(1, len(ext) + 1) * 4.5 / 1200)
    out[MGDP] = pd.concat([g, pd.Series(vals, index=ext)])
    return out


def util_travel(cg, travel_quarterly=None):
    """Electricity/gas and travel PCE regressions (P10; Mods Oct-2017; sample from Jan 2000). If monthly
    travel-services trade is unavailable (public data), the travel regressions use quarterly growth rates."""
    start = pd.Timestamp(SPEC['travel_util']['sample_start']) + pd.offsets.MonthEnd(0)

    def ols(y, xs, names):
        d = pd.concat([y] + xs, axis=1).dropna().loc[start:]
        A = np.column_stack([np.ones(len(d))] + [d.iloc[:, k] for k in range(1, d.shape[1])])
        return dict(zip(['Constant'] + names, np.linalg.lstsq(A, d.iloc[:, 0].to_numpy(), rcond=None)[0]))
    out = {'CSEHM_USNA': ols(cg['CSEHM_USNA'], [cg['IPUTL_IP'], cg['CSEHM_USNA'].shift(1)], ['IPUTL_IP', 'CSEHM_USNALag1'])}
    if cg['BMBSXR_USINT'].notna().sum() > 60:
        out['CSDTFHM_USNA'] = ols(cg['CSDTFHM_USNA'], [cg['BMBSXR_USINT']], ['BMBSXR_USINT'])
        out['CSFTOHM_USNA'] = ols(cg['CSFTOHM_USNA'], [cg['BMBSMR_USINT']], ['BMBSMR_USINT'])
    else:
        xq, mq = travel_quarterly
        q = lambda g: g.resample('QE').mean()          # quarterly average of monthly SAAR log growth
        gq = lambda s: 400 * np.log(s / s.shift(1))
        start_q = start + pd.offsets.QuarterEnd(0)
        def qols(y, x, name):
            d = pd.concat([y, x], axis=1).dropna().loc[start_q:]
            A = np.column_stack([np.ones(len(d)), d.iloc[:, 1]])
            return dict(zip(['Constant', name], np.linalg.lstsq(A, d.iloc[:, 0].to_numpy(), rcond=None)[0]))
        out['CSDTFHM_USNA'] = qols(q(cg['CSDTFHM_USNA']), gq(xq), 'BMBSXR_USINT')
        out['CSFTOHM_USNA'] = qols(q(cg['CSFTOHM_USNA']), gq(mq), 'BMBSMR_USINT')
    return out


TRADE_NIPA = {'goods': ('XM', 'MM'), 'services': ('XS', 'MS')}
TRADE_SERIES = {'goods': ['SplicedGoodsExports', 'SplicedGoodsImports', 'NetExportsGoodsMonthlyContrib'],
                'services': ['SplicedServiceExports', 'SplicedServiceImports', 'NetSvcExportsMonthlyContrib']}


def trade_model_history(kind, inp, faar, start):
    """Historical monthly-model quarterly growth of exports and imports, each past quarter rebuilt with the
    nowcast quarter's data-availability pattern (WP step 5; eq. 12 'going backwards through time')."""
    from . import components as C
    s = C.TRADE[kind]
    names = [s['exp'], s['imp'], s['contrib']]
    pattern = sorted({k for t in names for k, mo in enumerate(C.m.months(inp.T1, 3), 1)
                      if np.isnan(inp.growth[t].get(mo, np.nan))})
    rows = {}
    for qe in pd.date_range(start, inp.T, freq='QE'):
        mask = [C.m.months(qe, 3)[k - 1] for k in pattern]
        g = inp.growth.copy()
        for t in names:
            g.loc[g.index.isin(mask), t] = np.nan
        q_inp = dataclasses.replace(inp, T1=qe, T=qe - pd.offsets.QuarterEnd(1), growth=g)
        mon = C.Monthly(q_inp, g, inp.levels, faar, q_end=qe, mask=mask)
        _, out = C.trade(kind, q_inp, mon)
        rows[qe] = (out['monthly_exports'], out['monthly_imports'])
    return pd.DataFrame(rows, index=['x', 'm']).T, pattern


def trade_blend(kind, inp, faar, actual_qgrowth, bvar_hist):
    """Blend weights for net exports (P05; WP eq. 13; Mods Jan-2023): restricted WLS on contributions to
    growth (previous-quarter nominal shares of GDP), 2020 excluded."""
    x, m_ = TRADE_NIPA[kind]
    start = pd.Period(SPEC['blend']['sample_start']).end_time.normalize()
    s_ = TRADE_SERIES[kind]
    first = max(inp.growth[t].first_valid_index() for t in s_) + pd.offsets.QuarterEnd(10)
    hist, _ = trade_model_history(kind, inp, faar, max(start, first))
    nipa = inp.nipa
    sx = (nipa[x + 'X_USNA'] / nipa['GDPX_USNA']).shift(1)
    sm = (nipa[m_ + 'X_USNA'] / nipa['GDPX_USNA']).shift(1)
    contrib = lambda gx, gm: sx * gx - sm * gm
    y = contrib(actual_qgrowth[x + 'Z'], actual_qgrowth[m_ + 'Z']).loc[start:inp.T]
    b = contrib(bvar_hist[x + 'Z'], bvar_hist[m_ + 'Z']).loc[start:inp.T]
    mm = contrib(hist.x, hist.m).loc[start:inp.T]
    return blend.restricted_wls(y, b, mm)


def clean_consumption_inputs(cg):
    """Model-derived consumption series in the workbook (registry inputs_used: *Fore, *Rev) are replaced by
    the published series they extend; the regressions that produce them are re-estimated (P10)."""
    cg = cg.copy()
    for derived, actual in [('CSEHM_USNAFore', 'CSEHM_USNA'), ('CSFTOHM_USNARev', 'CSFTOHM_USNA'),
                            ('CSDTFHM_USNARev', 'CSDTFHM_USNA')]:
        cg[derived] = cg[actual]
    return cg


def estimate_all(inp, panel, actual_qgrowth, prices, last_price_month, mgdp_last):
    """Every group-1 parameter (registry P01-P14, P05 in full) from data. Returns (Inputs, diag)."""
    from . import inventory
    inp = dataclasses.replace(inp, cons_growth=clean_consumption_inputs(inp.cons_growth))
    est, _, diag = estimate_core(inp, panel, actual_qgrowth, prices)
    # Monthly price BVAR (P08) conditioned on oil and ISM prices released for later months.
    cond = pd.DataFrame({'WTI': 1200 * np.log(inp.inv_raw['PZTEXP_USECON']).diff(), 'ISMP': inp.inv_raw['NAPMPI_USECON']})
    mp = monthly_price_bvar(inp.monthly_prices, cond, last_price_month, mgdp_last)
    cg = est.cons_growth.copy()
    for c in CONS_DEFLATORS:
        g = 1200 * np.log(mp[c]).diff()
        cg[c] = cg[c].where(cg.index <= last_price_month, g.reindex(cg.index))
    est = dataclasses.replace(est, monthly_prices=mp, cons_growth=cg,
                              util_travel=util_travel(cg, inp.flags.get('travel_quarterly')))
    # Trade blend weights (P05, net exports).
    blend_w = dict(est.blend)
    blend_w['PTXNETMH'] = trade_blend('goods', est, est.faar, actual_qgrowth, diag['bvar_hist'])
    blend_w['PTXNETSH'] = trade_blend('services', est, est.faar, actual_qgrowth, diag['bvar_hist'])
    # Inventory system (P11-P14) and its blend weight.
    inv, diag['inventory'] = inventory.estimate(est, mp)
    est = dataclasses.replace(est, **inv)
    bv_v = diag['bvar_hist'] if 'VZ' in diag['bvar_hist'] else None
    v_hist = inventory_bvar_history(inp)
    blend_w['PTVH'], diag['inventory_model_history'] = inventory.inventory_blend(est, mp, v_hist)
    est = dataclasses.replace(est, blend=blend_w)
    return est, diag


def inventory_bvar_history(inp):
    """Quarterly BVAR in-sample one-step fits of real CIPI ($bn), from the quantity BVAR's ratio equation."""
    nipa, T, p = inp.nipa, inp.T, SPEC['bvar_quarterly']['lags']
    start = pd.Period(SPEC['bvar_quarterly']['sample_start']).end_time.normalize()
    L = pd.DataFrame({c: np.log(nipa[c + 'Z_USNA']) for c in COMPS})
    L['V'] = nipa['VZ_USNA'] / nipa['GDPZ_USNA'].shift(1)
    L = L.loc[start:T]
    B = bvar.fit(L.to_numpy(), p, SPEC['bvar_quarterly']['lambda_quantities'], np.array([1.0] * len(COMPS) + [0.0]))
    fitted, _ = bvar.one_step(L.to_numpy(), B, p)
    return pd.Series(fitted[:, -1], index=L.index[p:]) * nipa['GDPZ_USNA'].shift(1).reindex(L.index[p:]) / 1000
