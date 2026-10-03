"""Inventory model estimation (registry P11-P14, part of P05; Higgins 2014 step 7; Mods Oct-2017).

Produces, for the quarter being nowcast, what the assembly in components.inventories consumes:
  iva_paths   monthly IVA for Census industries in months with Census book values but no BEA IVA
  cipi_paths  monthly nominal CIPI (Census industries, months without book values) and real CIPI
              (motor-vehicle dealers, nonmerchant wholesalers)
  inv_deflators  end-of-quarter inventory deflators for the nowcast quarter
  farm_other  AR(4) coefficients for farm and construction/mining/utilities/other inventories

Methods (deviations from Higgins 2014 are flagged in DESIGN.md §10):
  * first month of the quarter: BEA underlying-detail end-of-month stocks (Mods Oct-2017);
  * IVA model: BEA holding-gain identity, IVA_t = -12 * book_{t-1} * sum_k sum_j w_jk dln P_{j,t-k}, with
    non-negative weights over PPIs j and turnover lags k <= 4 (WP step 7e; fn 34-35) and an AR(1)
    discrepancy;
  * CIPI forecasts: per-industry block BVARs (core activity block + scaled inventory investment), documented
    lambda, conditional forecasts given released data (WP step 7b-d, g);
  * deflators: quarterly regression of the inventory deflator on quarterly-average sales prices (stands in
    for the Denton interpolation, WP fn 32).
"""
import numpy as np
import pandas as pd
from scipy.optimize import nnls

from . import bvar
from .config import load_toml

SPEC = load_toml('spec.toml')

CENSUS = {
    'dur': dict(book='NMIDG_USECON', iva='VNMDIM_USNA', cipi='dBeaNMIDG_USECON', sales='NMSDG_USECON',
                sdef='DTSMD_USNA', udt_nom='TIMD_USNA', udt_real='TIMDH_USNA', ip='IPMDG_IP', emp='LADURGA_USECON',
                ppi=['PC1113_PPI', 'PA41312_PPI', 'PA49207_PPI', 'PC1_PPI'], ism=True),
    'nondur': dict(book='NMING_USECON', iva='VNMNIM_USNA', cipi='dBeaNMING_USECON', sales='NMSNG_USECON',
                   sdef='DTSMN_USNA', udt_nom='TIMN_USNA', udt_real='TIMNH_USNA', ip='IPMND_IP', emp='LANDURA_USECON',
                   ppi=['PC1112_PPI', 'PC1_PPI', 'PA49207_PPI', 'sa_PIN_PPI_'], ism=True),
    'whole': dict(book='NWIH_USECON', iva='VNWLMIM_USNA', cipi='dBeaNWIH_USECON', sales='NWSH_USECON',
                  sdef='DTSWM_USNA', udt_nom='TIWM_USNA', udt_real='TIWMH_USNA', ip='IPMFG_IP', emp='LAWTRDA_USECON',
                  ppi=['PC1_PPI', 'PA49207_PPI', 'sa_PIN_PPI_', 'PC1113_PPI', 'PC1112_PPI'], ism=False),
    'retail': dict(book='NRIXM_USECON', iva=('VNRIM_USNA', 'VNRDVIM_USNA'), iva_out='IVARetExAutoNAICS',
                   cipi='dBeaNRIXM_USECON', sales='NRSXM_USECON', sdef='DTSR_USNA', udt_nom=('TIR_USNA', 'TIRI1_USNA'),
                   udt_real=('TIRH_USNA', 'TIRI1H_USNA'), ip='IP51_IP', emp='LARTRDA_USECON',
                   ppi=['UCN_CPIDATA', 'UCD_CPIDATA', 'JCNLGOM_USNA', 'PA49207_PPI'], ism=False),
}
REAL = {
    'mv': dict(cipi='VNRDVHM_USNA', udt_real='TIRI1H_USNA', sales='NRSI1_USECON', sdef='DTSRI1_USNA',
               core=['ADS_USECON', 'AFS_USECON', 'IAU_IP']),
    'nonmerch': dict(cipi='VNWWHM_USNA', udt_real=None, sales='NWSH_USECON', sdef='DTSWM_USNA',
                     core=['IPMFG_IP', 'LAWTRDA_USECON']),
}
DEFLATOR_SOURCE = {'DSNMD_USNA': 'SpliceManTradeDeflatorfr', 'DSNMN_USNA': 'SpliceManTradeDeflatorfr',
                   'DSNWM_USNA': 'SpliceWholesaleTradeDeflatorfr', 'DSNWW_USNA': 'SpliceWholesaleTradeDeflatorfr',
                   'priceRETINVexautoSplice': 'SpliceRetailTradeDeflatorfr', 'DSNRDV_USNA': 'UTW_CPIDATA'}
NAICS_START = pd.Timestamp('1997-02-28')


def _col(raw, spec):
    if isinstance(spec, tuple):
        return raw[spec[0]] - raw[spec[1]]
    return raw[spec]


def iva_history(raw, d):
    """BEA monthly IVA (Mil $ SAAR) plus first-month-of-quarter values from underlying-detail stocks."""
    iva = _col(raw, d['iva'])
    nom, real, book = _col(raw, d['udt_nom']), _col(raw, d['udt_real']), raw[d['book']]
    udt = 12 * real.diff() * (nom / real) - 12 * book.diff()
    return iva.combine_first(udt.where(iva.isna()))


def iva_model(raw, d, prices, last):
    """Holding-gain IVA model with non-negative turnover/price weights and AR(1) discrepancy (P13).
    Returns the IVA path extended through `last` for months with book values."""
    hist = iva_history(raw, d)
    book = raw[d['book']]
    P = pd.concat([prices[p] for p in d['ppi']], axis=1, keys=d['ppi'])
    dp = np.log(P).diff()
    cols = {f'{p}_{k}': -12 * book.shift(1) * dp[p].shift(k) for p in d['ppi'] for k in range(5)}
    X = pd.DataFrame(cols)
    fit_idx = hist.dropna().index.intersection(X.dropna().index)
    fit_idx = fit_idx[fit_idx >= NAICS_START]
    w, _ = nnls(X.loc[fit_idx].to_numpy(), hist.loc[fit_idx].to_numpy())
    pred = X @ w
    e = (hist - pred).loc[fit_idx]
    rho = float(np.dot(e[1:].to_numpy(), e[:-1].to_numpy()) / np.dot(e[:-1].to_numpy(), e[:-1].to_numpy()))
    out = hist.copy()
    t = hist.dropna().index.max()
    resid = hist[t] - pred[t]
    while t < last:
        t = t + pd.offsets.MonthEnd(1)
        if np.isnan(book.get(t, np.nan)):
            break
        resid = rho * resid
        out[t] = pred[t] + resid
    return out, {'weights': dict(zip(X.columns, w)), 'rho': rho}


CORE_START = pd.Timestamp('1983-01-31')


def splice(new, old):
    """Ratio-splice a discontinued series onto its replacement at the first month of the new series."""
    first = new.first_valid_index()
    prev = old.loc[:first].dropna()
    if prev.empty:
        return new
    link = new[first] / (old[first] if not np.isnan(old.get(first, np.nan)) else prev.iloc[-1])
    return new.combine_first(old * link)


SALES_DEFLATORS = {'DTSMD_USNA': 'DTSMD1_USNA', 'DTSMN_USNA': 'DTSMN1_USNA', 'DTSWM_USNA': 'DTSW1_USNA',
                   'DTSR_USNA': 'DTSRX1_USNA', 'DTSRI1_USNA': 'DTSRAD1_USNA'}


def core_panel(raw):
    """Core-quantity variables (WP Table A8a): log differences x1200 except ISM levels."""
    g = lambda s: 1200 * np.log(s / s.shift(1))
    raw = raw.copy()
    for new, old in SALES_DEFLATORS.items():
        raw[new] = splice(raw[new], raw[old])
    autos = raw['ADS_USECON'] + raw['AFS_USECON'] + raw['TLSAR_USECON'] + raw['TMSAR_USECON']
    return pd.DataFrame({
        'IPMDG': g(raw['IPMDG_IP']), 'LADURGA': g(raw['LADURGA_USECON']), 'RealNMSDG': g(raw['NMSDG_USECON'] / raw['DTSMD_USNA']),
        'NAPMII': raw['NAPMII_USECON'], 'NAPMC': raw['NAPMC_USECON'],
        'IPMND': g(raw['IPMND_IP']), 'LANDURA': g(raw['LANDURA_USECON']), 'RealNMSNG': g(raw['NMSNG_USECON'] / raw['DTSMN_USNA']),
        'IPMFG': g(raw['IPMFG_IP']), 'LAWTRDA': g(raw['LAWTRDA_USECON']), 'RealNWSH': g(raw['NWSH_USECON'] / raw['DTSWM_USNA']),
        'IP51': g(raw['IP51_IP']), 'LARTRDA': g(raw['LARTRDA_USECON']), 'RealNRSXM': g(raw['NRSXM_USECON'] / raw['DTSR_USNA']),
        'IAU': g(raw['IAU_IP']), 'Autos': g(autos)}), autos


BLOCK_CORE = {'dur': ['IPMDG', 'LADURGA', 'RealNMSDG', 'NAPMII', 'NAPMC'],
              'nondur': ['IPMND', 'LANDURA', 'RealNMSNG', 'NAPMII', 'NAPMC'],
              'whole': ['IPMFG', 'LAWTRDA', 'RealNWSH'], 'retail': ['IP51', 'LARTRDA', 'RealNRSXM'],
              'mv': ['IAU', 'Autos'], 'nonmerch': ['IPMFG', 'LAWTRDA', 'RealNMSDG', 'RealNMSNG', 'RealNWSH', 'RealNRSXM']}


def core_forecast(raw, T1, lam, lags):
    """Core BVAR (6 lags, from 1983, documented lambda) with conditional forecasts through T1 given every
    core value already released."""
    core, autos = core_panel(raw)
    core = core.loc[CORE_START:T1]
    complete = core.dropna().index.max()
    hist = core.loc[:complete].dropna()
    Y = hist.to_numpy()
    B = bvar.fit(Y, lags, lam, np.zeros(Y.shape[1]), sum_coef=False)
    future = pd.date_range(complete + pd.offsets.MonthEnd(1), T1, freq='ME')
    known = core.reindex(future).to_numpy()
    fc = pd.DataFrame(bvar.conditional_forecast(Y, B, lags, len(future), known), index=future, columns=core.columns)
    return pd.concat([hist, fc]), autos


def block_forecast(scaled, core, cols, T1, lags):
    """Block equation (WP step 7c): scaled inventory investment on its own lags and the industry's core
    variables at lags 0..lags (OLS from 1997, NAICS data); iterated through T1 using core forecasts."""
    X = {f'{c}_{l}': core[c].shift(l) for c in cols for l in range(lags + 1)}
    X.update({f'own_{l}': scaled.shift(l) for l in range(1, lags + 1)})
    X = pd.DataFrame(X)
    d = pd.concat([scaled.rename('y'), X], axis=1).loc[NAICS_START:].dropna()
    A = np.column_stack([np.ones(len(d))] + [d[c] for c in X.columns])
    b = np.linalg.lstsq(A, d.y.to_numpy(), rcond=None)[0]
    s = scaled.copy()
    for t in pd.date_range(s.dropna().index.max() + pd.offsets.MonthEnd(1), T1, freq='ME'):
        row = [core[c].shift(l)[t] for c in cols for l in range(lags + 1)] + [s[t - pd.offsets.MonthEnd(l)] for l in range(1, lags + 1)]
        s[t] = b[0] + np.dot(b[1:], row)
    return s


def census_cipi(raw, d, iva, core, cols, T1, lags):
    """Nominal CIPI (book change + IVA), scaled by six-month-lagged sales (Table A8b)."""
    cipi = 12 * raw[d['book']].diff() + iva
    sales = raw[d['sales']]
    s = block_forecast(cipi / sales.shift(6), core, cols, T1, lags)
    months = pd.date_range(T1 - pd.offsets.MonthEnd(2), T1, freq='ME')
    return pd.Series({t: cipi[t] if not np.isnan(cipi.get(t, np.nan)) else s[t] * sales[t - pd.offsets.MonthEnd(6)]
                      for t in months})


def real_cipi(raw, d, scale, core, cols, T1, lags):
    """Real CIPI for motor-vehicle dealers (scaled by light-vehicle sales) and nonmerchant wholesalers
    (scaled by the lagged real stock); first month of the quarter from underlying-detail stocks if available."""
    real = raw[d['cipi']].copy()
    if d['udt_real']:
        real = real.combine_first((12 * raw[d['udt_real']].diff()).where(real.isna()))
    s = block_forecast(real / scale, core, cols, T1, lags)
    months = pd.date_range(T1 - pd.offsets.MonthEnd(2), T1, freq='ME')
    return pd.Series({t: real[t] if not np.isnan(real.get(t, np.nan)) else s[t] * scale[t] for t in months})


def deflator_forecasts(nipa_defl, raw, monthly_prices, T, T1):
    """End-of-quarter inventory deflators for T1: proportional extrapolation of the end-of-quarter deflator
    with the end-of-quarter-month change in the industry's monthly sales price (spliced trade deflators from
    the price BVAR; CPI new vehicles for motor-vehicle dealers). Stands in for the Denton step (WP fn 32)."""
    out = {}
    for inv, src in DEFLATOR_SOURCE.items():
        s = (monthly_prices[src] if src in monthly_prices else raw[src]).loc[:T1]
        s = s.reindex(pd.date_range(s.index.min(), T1, freq='ME')).ffill()   # series outside the price BVAR: last value
        out[inv] = nipa_defl[inv][T] * s[T1] / s[T]
    return out


def farm_other(nipa, T):
    """AR(4) on quarterly log growth (SAAR) of farm and other real inventory stocks, from 1985Q1 (P11)."""
    out = {}
    for s in ['SFZ_USNA', 'SNOZ_USNAqtrExtrap']:
        g = 400 * np.log(nipa[s] / nipa[s].shift(1))
        d = pd.concat([g] + [g.shift(k) for k in range(1, 5)], axis=1).loc['1985-03-31':T].dropna()
        A = np.column_stack([np.ones(len(d))] + [d.iloc[:, k] for k in range(1, 5)])
        b = np.linalg.lstsq(A, d.iloc[:, 0].to_numpy(), rcond=None)[0]
        out[s] = dict(zip(['const', 'ar1', 'ar2', 'ar3', 'ar4'], b))
    return out


def estimate(inp, monthly_prices):
    """All inventory-model outputs for inp.T1. Returns dict of Inputs fields and diagnostics."""
    raw, T, T1 = inp.inv_raw, inp.T, inp.T1
    lam, lags = SPEC['bvar_inventory_core']['lambda'], SPEC['bvar_inventory_core']['lags']
    prices = raw.join(monthly_prices[[c for c in monthly_prices.columns if c not in raw]], how='outer')
    iva_paths, cipi_paths, diag = {}, {}, {}
    core, autos = core_forecast(prices, T1, lam, lags)
    for name, d in CENSUS.items():
        iva, info = iva_model(prices, d, prices, T1)
        diag[f'iva_{name}'] = info
        iva_paths[d.get('iva_out', d['iva'])] = iva
        cipi_paths[d['cipi']] = census_cipi(prices, d, iva, core, BLOCK_CORE[name], T1, lags)
    # Autos are extended with their core-forecast growth where unreleased.
    auto_lvl = autos.copy()
    for t in pd.date_range(auto_lvl.dropna().index.max() + pd.offsets.MonthEnd(1), T1, freq='ME'):
        auto_lvl[t] = auto_lvl[t - pd.offsets.MonthEnd(1)] * np.exp(core.loc[t, 'Autos'] / 1200)
    cipi_paths[REAL['mv']['cipi']] = real_cipi(prices, REAL['mv'], auto_lvl, core, BLOCK_CORE['mv'], T1, lags)
    stock = inp.nipa['SNWWZ_USNA'].shift(1).resample('ME').bfill()   # lagged quarterly real stock, monthly
    stock = stock.reindex(pd.date_range(stock.index.min(), T1, freq='ME')).ffill()
    cipi_paths[REAL['nonmerch']['cipi']] = real_cipi(prices, REAL['nonmerch'], stock, core, BLOCK_CORE['nonmerch'], T1, lags)
    defl = deflator_forecasts(inp.nipa, prices, monthly_prices, T, T1)
    inv_def = inp.inv_deflators.copy()
    for k, v in defl.items():
        inv_def.loc[T1, k] = v
    return {'iva_paths': pd.DataFrame(iva_paths), 'cipi_paths': pd.DataFrame(cipi_paths),
            'inv_deflators': inv_def, 'farm_other': farm_other(inp.nipa, T)}, diag


def mask_like(df, T1, q):
    """Copy of monthly df truncated at quarter q, with each column's months-in-quarter that are unreleased in
    the nowcast quarter T1 also blanked in quarter q (pseudo-real-time availability pattern)."""
    out = df.loc[:q].copy()
    t1m, qm = pd.date_range(end=T1, periods=3, freq='ME'), pd.date_range(end=q, periods=3, freq='ME')
    for c in out.columns:
        miss = [k for k, mo in enumerate(t1m) if np.isnan(df[c].get(mo, np.nan))]
        for k in miss:
            if qm[k] in out.index:
                out.loc[qm[k], c] = np.nan
    return out


def inventory_blend(inp, monthly_prices, bvar_hist_v):
    """Blend weight for inventories (P05; WP eq. 19; Mods Jan-2023): restricted WLS on the contribution of
    the change in real CIPI, monthly model rebuilt for each past quarter with the nowcast quarter's data
    pattern (parameters re-estimated each quarter); NAICS monthly data limit the sample to 1997Q3 onward."""
    import dataclasses
    from . import components as C, blend
    nipa, T1 = inp.nipa, inp.T1
    model = {}
    for q in pd.date_range('1997-09-30', inp.T, freq='QE'):
        raw_q = mask_like(inp.inv_raw, T1, q)
        q_inp = dataclasses.replace(inp, T=q - pd.offsets.QuarterEnd(1), T1=q, inv_raw=raw_q,
                                    nipa=nipa.loc[:q - pd.offsets.QuarterEnd(1)])
        try:
            est, _ = estimate(q_inp, monthly_prices.loc[:q])
            q_inp = dataclasses.replace(q_inp, nipa=nipa.loc[:q], **est)
            _, out = C.inventories(q_inp)
            model[q] = out['cipi_monthly_model']
        except Exception:
            model[q] = np.nan
    model = pd.Series(model)
    gdp_prev = nipa['GDPZ_USNA'].shift(1) / 1000
    prev = nipa['VZ_USNA'].shift(1) / 1000
    contrib = lambda c: (c - prev) / gdp_prev
    y = contrib(nipa['VZ_USNA'] / 1000)
    b = contrib(bvar_hist_v)
    return blend.restricted_wls(y.loc[model.index], b.loc[model.index], contrib(model)), model
