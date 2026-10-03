"""Nowcast assembly for the 13 GDP components given an `Inputs` bundle (Higgins 2014 steps 3-7).

Every function returns (log growth SAAR for T1, dict of named intermediates). Intermediates are compared
with the workbook's cached cells in L1 and reported in L2/L3.
"""
import numpy as np
import pandas as pd

from . import monthly as m
from .config import load_toml

BRIDGES = load_toml('bridges.toml')
ADDITIVE = set(BRIDGES['monthly']['additive_levels'])


class Monthly:
    """Lazily fills monthly growth (factor-augmented AR) and levels for the quarter being nowcast."""

    def __init__(self, inp, growth, levels, faar):
        self.inp, self.g, self.lv, self.faar = inp, growth, levels, faar
        self.start = inp.T1 - pd.offsets.MonthEnd(24)
        self._g, self._l = {}, {}

    def growth(self, t):
        if t not in self._g:
            x = self.g[t] if t in self.g else pd.Series(dtype=float)
            self._g[t] = m.faar_fill(x, self.inp.factor, self.faar[t], self.start, self.inp.T1)
        return self._g[t]

    def level(self, t):
        if t not in self._l:
            self._l[t] = m.level_fill(self.lv[t], self.growth(t), self.start, self.inp.T1, additive=t in ADDITIVE)
        return self._l[t]


def indicator_growth(name, mon, inp):
    """Quarterly growth (400*dlog, SAAR) of a bridge indicator for T1."""
    spec = BRIDGES['indicators'].get(name)
    q = inp.T1
    if spec is None:
        return m.q_growth_from_growth(mon.growth(name), q)
    if spec['kind'] == 'net_levels':
        lev = sum(sign * mon.level(t) for t, sign in spec['terms'])
        return m.q_growth_from_levels(lev, q)
    p = q - pd.offsets.QuarterEnd(1)
    if spec['kind'] == 'bus_trucks':
        s = inp.nominal[spec['light_share']][inp.T]
        def idx(qe):
            light = m.q_mean(mon.level(spec['business_share']), qe) * m.q_mean(mon.level(spec['light_units']), qe)
            return light ** s * m.q_mean(mon.level(spec['heavy_units']), qe) ** (1 - s)
        return 400 * np.log(idx(q) / idx(p))
    if spec['kind'] == 'bus_autos':
        def idx(qe):
            return (100 - m.q_mean(mon.level(spec['consumer_share']), qe)) * m.q_mean(mon.level(spec['units']), qe)
        return 400 * np.log(idx(q) / idx(p))
    raise ValueError(spec['kind'])


def subcomponent_growth(sub, mon, inp, out):
    c = inp.bridge[sub['lhs']]
    if sub['kind'] == 'ar1':
        hist = inp.q_hist[sub['lhs']]
        g = c.get('Constant', 0.0) + sum(c.get(f'AR{k}', 0.0) * hist[inp.T - pd.offsets.QuarterEnd(k - 1)]
                                         for k in range(1, 5))
    else:
        g = c.get('Constant', 0.0)
        for ind in sub['indicators']:
            ig = indicator_growth(ind, mon, inp)
            out[f'ind:{ind}'] = ig
            g += c[ind] * ig
    return g


def _num(s, t):
    v = s.get(t, np.nan) if s is not None else np.nan
    return not np.isnan(v)


def latest_month_nowcast(actual, source, source_prev, deflator, window):
    """Retail-control style nowcast (Higgins 2014 step 6): where PCE for month t is unreleased but the source
    (nominal retail sales growth) is, use source - deflator; in the two months before such a month, add the
    source's revision (source - previous estimate) to published PCE. NaN where neither applies."""
    y = pd.Series(np.nan, index=window)
    for i, t in enumerate(window):
        nxt = [window[i + k] for k in (1, 2) if i + k < len(window)]
        if not _num(actual, t) and _num(source, t):
            y[t] = source[t] - deflator[t]
        elif any(not _num(actual, u) and _num(source, u) for u in nxt):
            y[t] = actual.get(t, np.nan) + source[t] - source_prev.get(t, np.nan)
        else:
            y[t] = actual.get(t, np.nan)
    return y


def consumption(inp, mon_main):
    """Real PCE goods and services (Higgins 2014 step 6; Mods Oct-2017). Returns ((goods, services), out)."""
    cg, cl, out = inp.cons_growth, inp.cons_levels, {}
    T1, start = inp.T1, inp.T1 - pd.offsets.MonthEnd(24)
    window = pd.date_range(start, T1, freq='ME')
    hist = lambda t: cg[t].loc[:start - pd.offsets.MonthEnd(1)] if t in cg else pd.Series(dtype=float)
    col = lambda df, t: df[t] if t in df else pd.Series(dtype=float)

    def fill(ticker, recent):
        """FA-AR fill of a consumption series whose recent months are given by `recent` (NaN = forecast)."""
        x = pd.concat([hist(ticker), recent]).sort_index()
        return m.faar_fill(x, inp.factor, inp.faar[ticker], start, T1)

    def q(name, series):
        out[f'cons:{name}'] = series
        return m.q_growth_from_growth(series, T1)

    # Goods buckets.
    retail = latest_month_nowcast(col(cg, 'CoreRealRetailPCEQtyExFoodSvc'), col(cg, 'NRSXMI47_USECONlessNRSV2_USECON'),
                                  col(cg, 'NRSXMI47_USECONPrevlessNRSV2_USECONPrev'),
                                  col(cg, 'CoreRealRetailPCEDefExFoodSvcfr'), window)
    g_core = q('core_retail', fill('CoreRealRetailPCEQtyExFoodSvc', retail))
    g_newmv = q('new_mv', new_motor_vehicles(inp, mon_main, window))
    g_used = q('used_mv', fill('CDMVUHM_USNA', col(cg, 'CDMVUHM_USNA').reindex(window)))
    g_gas = q('gasoline', fill('CNEHM_USNA', col(cg, 'CNEHM_USNA').reindex(window)))
    prevq = m.months(inp.T, 3)
    nom = lambda tickers: np.array([cl[t].reindex(prevq).sum() for t in tickers])
    gn = nom(['sumCoreNomRetailPCEExFoodSvc', 'CDMVNM_USNA', 'CDMVUM_USNA', 'CNEM_USNA'])
    goods = float(np.dot(gn / gn.sum(), [g_core, g_newmv, g_used, g_gas]))

    # Services buckets.
    food = latest_month_nowcast(col(cg, 'CSFPHM_USNA'), col(cg, 'NRSV2_USECON'), col(cg, 'NRSV2_USECONPrevious'),
                                col(cg, 'CSFPMDeffr'), window)
    g_food = q('food_services', fill('CSFPHM_USNA', food))
    g_elec = q('electricity_gas', electricity(inp, window, fill))
    g_out = q('travel_out', travel(inp, window, fill, 'CSFTOHM_USNARev', 'CSFTOHM_USNA', 'BMBSMR_USINT', 'CSFTOHM_USNA'))
    g_in = q('travel_in', travel(inp, window, fill, 'CSDTFHM_USNARev', 'CSDTFHM_USNA', 'BMBSXR_USINT', 'CSDTFHM_USNA'))
    g_other = q('other_services', fill('HerzonServicesLessFoodUtilTravelQty',
                                       col(cg, 'HerzonServicesLessFoodUtilTravelQty').reindex(window)))
    sn = nom(['CSFPM_USNA', 'CSEM_USNA', 'CSFTOM_USNA', 'CSDTFM_USNA', 'sumNomServicesPCEExFoodExUtilExForTravel'])
    services = float(np.dot(sn / sn.sum(), [g_food, g_elec, g_out, g_in, g_other]))
    out.update({'goods_shares': gn / gn.sum(), 'services_shares': sn / sn.sum(),
                'goods_q': [g_core, g_newmv, g_used, g_gas], 'services_q': [g_food, g_elec, g_out, g_in, g_other]})
    return (goods, services), out


def new_motor_vehicles(inp, mon, window):
    """Real PCE new motor vehicles: published levels, extended with unit auto/truck sales and consumer shares."""
    cl, ml = inp.cons_levels, inp.levels
    actual = cl['CDMVNHM_USNA']
    autos, trucks = ml['ASTOT@USECON'], ml['TLTSAR@USECON']
    cons_auto, cons_truck = mon.level('ASCPU@USNA'), 100 - mon.level('BusShareTrucks')
    share = cl['CDMNM_USNA'] / (cl['CDMNM_USNA'] + cl['CDMTNM_USNA'])
    level, g = {}, pd.Series(np.nan, index=window)
    last_share = None
    for i, t in enumerate(window):
        p = t - pd.offsets.MonthEnd(1)
        sh_prev = share.get(p, np.nan)
        sh_prev = last_share if np.isnan(sh_prev) else sh_prev
        last_share = sh_prev
        if _num(actual, t):
            level[t] = actual[t]
        elif _num(autos, t):
            base = level.get(p) if t == window[-1] else actual.get(p, np.nan)
            level[t] = (base * ((cons_auto[t] / cons_auto[p]) * (autos[t] / autos[p])) ** sh_prev
                        * ((cons_truck[t] / cons_truck[p]) * (trucks[t] / trucks[p])) ** (1 - sh_prev))
        prev_level = level.get(p, actual.get(p, np.nan))
        if t in level:
            g[t] = 1200 * np.log(level[t] / prev_level)
    hist = inp.cons_growth['CDMVNHM_USNA'] if 'CDMVNHM_USNA' in inp.cons_growth else 1200 * np.log(actual / actual.shift())
    x = pd.concat([hist.loc[:window[0] - pd.offsets.MonthEnd(1)], g]).sort_index()
    return m.faar_fill(x, inp.factor, inp.faar['CDMVNHM_USNA'], window[0], window[-1])


def electricity(inp, window, fill):
    """Electricity + gas PCE: published value, else IP-utilities regression (Mods Oct-2017), else FA-AR."""
    cg, c = inp.cons_growth, inp.util_travel['CSEHM_USNA']
    src = cg['CSEHM_USNAFore'].reindex(window)
    actual, iputl = cg.get('CSEHM_USNA'), cg.get('IPUTL_IP')
    y = src.copy()
    for t in window[-6:]:
        if np.isnan(src[t]) and _num(iputl, t):
            y[t] = c['Constant'] + c['IPUTL_IP'] * iputl[t] + c['CSEHM_USNALag1'] * actual.get(t - pd.offsets.MonthEnd(1), np.nan)
    return fill('CSEHM_USNAFore', y)


def travel(inp, window, fill, coef_ticker, actual_ticker, trade, lhs):
    """Travel PCE (Mods Oct-2017): replace the latest PCE month with the travel-services regression when that
    month's trade data first appear; otherwise add the predicted revision from revised trade data."""
    cg, c = inp.cons_growth, inp.util_travel[lhs]
    actual, tr, tr_prev = cg[actual_ticker], cg[trade], cg[trade + 'Previous']
    slope = c[trade]
    # Revisions are predicted only when PCE and travel-trade data end in the same month; for the older
    # months of the window, not when that month is Feb, Aug or Nov (workbook Consumption rows 54/60; the
    # two state cells it compares are blank in the posted workbook, so its cached travel cells differ).
    latest = actual.dropna().index.max()
    same = latest == tr.dropna().index.max()
    y = pd.Series(np.nan, index=window)
    for i, t in enumerate(window):
        if not _num(actual, t):
            continue
        k = len(window) - 1 - i
        allowed = same and (k <= 2 or latest.month not in (2, 8, 11))
        rev = slope * (tr[t] - tr_prev[t]) if allowed and _num(tr_prev, t) else 0.0
        # Conditional treatment covers the last five months of the nowcast window; for the first three of
        # those the regression also requires that the following PCE month is unpublished.
        nxt_blank = i + 1 >= len(window) or not _num(actual, window[i + 1])
        newest = _num(tr, t) and not _num(tr_prev, t)
        if k <= 4 and newest and (k <= 1 or nxt_blank):
            y[t] = c['Constant'] + slope * tr[t]
        elif k <= 4:
            y[t] = actual[t] + rev
        else:
            y[t] = actual[t]
    return fill(coef_ticker, y)


TRADE = {
    'goods': dict(exp='SplicedGoodsExports', imp='SplicedGoodsImports', contrib='NetExportsGoodsMonthlyContrib',
                  pexp='PXEA_USECONsplicefr', pimp='PMEA_USECONsplicefr', blend='PTXNETMH', bx='XMZ', bm='MMZ'),
    'services': dict(exp='SplicedServiceExports', imp='SplicedServiceImports', contrib='NetSvcExportsMonthlyContrib',
                     pexp='ExpSvcDefmthA1fr', pimp='ImpSvcDefmthA1fr', blend='PTXNETSH', bx='XSZ', bm='MSZ'),
}


def trade(kind, inp, mon):
    """Real exports and imports (Higgins 2014 step 5, eqs. 9-13). Returns ((exports, imports), out)."""
    s = TRADE[kind]
    win = pd.date_range(mon.start, inp.T1, freq='ME')
    ex_g, im_g, c = mon.growth(s['exp']), mon.growth(s['imp']), mon.growth(s['contrib'])
    released = inp.growth[s['exp']].reindex(win).notna()
    px, pm = inp.monthly_prices[s['pexp']], inp.monthly_prices[s['pimp']]
    gdp = inp.monthly_prices['MGDPN_USECONsplicefr']
    nom_x = mon.level(s['exp']) * px
    nom_m = mon.level(s['imp']) * pm
    ex_adj, im_adj = ex_g.copy(), im_g.copy()
    nx, nm, wx, wm = {}, {}, {}, {}
    for t in win:
        p = t - pd.offsets.MonthEnd(1)
        if released[t]:
            alpha = 0.0
        else:   # consistency adjustment (eq. 11): exports/imports forecasts must reproduce the contribution forecast
            alpha = (c[t] + wm[p] * im_g[t] - wx[p] * ex_g[t]) / (wm[p] + wx[p])
        ex_adj[t], im_adj[t] = ex_g[t] + alpha, im_g[t] - alpha
        if released[t]:
            nx[t], nm[t] = nom_x[t], nom_m[t]
        else:
            nx[t] = nx[p] * np.exp(ex_adj[t] / 1200 + np.log(px[t] / px[p]))
            nm[t] = nm[p] * np.exp(im_adj[t] / 1200 + np.log(pm[t] / pm[p]))
        wx[t], wm[t] = 12 / 1000 * nx[t] / gdp[t], 12 / 1000 * nm[t] / gdp[t]
    qx, qm = m.q_growth_from_growth(ex_adj, inp.T1), m.q_growth_from_growth(im_adj, inp.T1)
    w1, w2 = inp.blend[s['blend']]
    out = {'monthly_exports': qx, 'monthly_imports': qm, 'bvar_exports': inp.bvar[s['bx']],
           'bvar_imports': inp.bvar[s['bm']], 'w_monthly': w1, 'w_bvar': w2}
    return (w1 * qx + w2 * inp.bvar[s['bx']], w1 * qm + w2 * inp.bvar[s['bm']]), out


# Inventory industries (Higgins 2014 step 7). Census industries: nominal CIPI = 12 x change in Census book
# value + IVA where book values are released, else the CIPI forecast path.
CENSUS_INV = [
    dict(stock='SNMDZ_USNA', price='DSNMD_USNA', cipi='dBeaNMIDG_USECON', iva='VNMDIM_USNA', book='NMIDG_USECON'),
    dict(stock='SNMNZ_USNA', price='DSNMN_USNA', cipi='dBeaNMING_USECON', iva='VNMNIM_USNA', book='NMING_USECON'),
    dict(stock='SNWMZ_USNA', price='DSNWM_USNA', cipi='dBeaNWIH_USECON', iva='VNWLMIM_USNA', book='NWIH_USECON'),
    dict(stock='quantRETINVexautoSplice', price='priceRETINVexautoSplice', cipi='dBeaNRIXM_USECON',
         iva=('VNRIM_USNA', 'VNRDVIM_USNA', 'IVARetExAutoNAICS'), book='NRIXM_USECON'),
]
REAL_PATH_INV = [dict(stock='SNRDVZ_USNA', price='DSNRDV_USNA', cipi='VNRDVHM_USNA'),
                 dict(stock='SNWWZ_USNA', price='DSNWW_USNA', cipi='VNWWHM_USNA')]
AR_INV = [dict(stock='SFZ_USNA', price='DSF_USNA'), dict(stock='SNOZ_USNAqtrExtrap', price='DSNO_USNAqtr')]


def inventories(inp):
    """Real change in private inventories for T1 ($bn, SAAR), blended with the quarterly BVAR."""
    T, T1 = inp.T, inp.T1
    Q0 = T - pd.offsets.QuarterEnd(1)
    nipa, raw, out = inp.nipa, inp.inv_raw, {}
    qs = [Q0, T, T1]
    stocks, prices = [], []
    q_months = lambda q: m.months(q, 3)

    def iva(spec, t):
        if isinstance(spec, tuple):
            tot, auto, fore = spec
            if _num(raw.get(tot), t):
                return (raw[tot][t] - raw[auto][t]) / 1000
            return inp.iva_paths[fore][t] / 1000
        if _num(raw.get(spec), t):
            return raw[spec][t] / 1000
        return inp.iva_paths[spec][t] / 1000

    for d in CENSUS_INV:
        book = raw[d['book']] / 1000
        cipi = {}
        for t in q_months(T).append(q_months(T1)):
            if _num(book, t):
                cipi[t] = 12 * (book[t] - book[t - pd.offsets.MonthEnd(1)]) + iva(d['iva'], t)
            else:
                cipi[t] = inp.cipi_paths[d['cipi']][t] / 1000
        p = [nipa[d['price']][Q0], nipa[d['price']][T], inp.inv_deflators[d['price']][T1]]
        real = {q: 100 * np.mean([cipi[t] for t in q_months(q)]) / p[i + 1] for i, q in enumerate([T, T1])}
        s0 = nipa[d['stock']][Q0] / 1000
        s = [s0, s0 + real[T] / 4, s0 + real[T] / 4 + real[T1] / 4]
        stocks.append(s); prices.append(p); out[f'inv:{d["stock"]}'] = s
    for d in REAL_PATH_INV:
        s1 = nipa[d['stock']][T] / 1000
        s = [nipa[d['stock']][Q0] / 1000, s1, s1 + inp.cipi_paths[d['cipi']].reindex(q_months(T1)).sum() / 12 / 1000]
        p = [nipa[d['price']][Q0], nipa[d['price']][T], inp.inv_deflators[d['price']][T1]]
        stocks.append(s); prices.append(p); out[f'inv:{d["stock"]}'] = s
    for d in AR_INV:
        lvl = nipa[d['stock']]
        g = 400 * np.log(lvl / lvl.shift(1))
        c = inp.farm_other[d['stock']]
        g1 = c['const'] + sum(c[f'ar{k}'] * g[T - pd.offsets.QuarterEnd(k - 1)] for k in range(1, 5))
        pr = nipa[d['price']]
        p2 = pr[T] * np.exp(np.log(pr[T] / pr[T - pd.offsets.QuarterEnd(12)]) / 12)   # 12-quarter average growth
        s = [lvl[Q0] / 1000, lvl[T] / 1000, lvl[T] / 1000 * np.exp(g1 / 400)]
        stocks.append(s); prices.append([pr[Q0], pr[T], p2]); out[f'inv:{d["stock"]}'] = s
    S, P = np.array(stocks), np.array(prices) / 100
    fisher = lambda a, b: np.sqrt((S[:, b] @ P[:, b] / (S[:, a] @ P[:, b])) * (S[:, b] @ P[:, a] / (S[:, a] @ P[:, a])))
    total0 = nipa['SZ_USNA'][Q0] / 1000
    total1 = total0 * fisher(0, 1)
    total2 = total1 * fisher(1, 2)
    cipi_model = 4 * (total2 - total1)
    w1, w2 = inp.blend['PTVH']
    out.update({'cipi_monthly_model': cipi_model, 'cipi_bvar': inp.bvar['VZ'] / 1000, 'w_monthly': w1, 'w_bvar': w2})
    return w1 * cipi_model + w2 * inp.bvar['VZ'] / 1000, out


def bridge_component(cid, inp, mon):
    """Investment / government component: share-weighted bridge forecasts blended with the BVAR (eqs. 6-8)."""
    subs = BRIDGES['components'][cid]['subcomponents']
    out = {}
    nom = np.array([inp.nominal[s['nominal']][inp.T] for s in subs])
    shares = nom / nom.sum()
    for s, sh in zip(subs, shares):
        out[f'sub:{s["lhs"]}'] = subcomponent_growth(s, mon, inp, out)
        out[f'share:{s["lhs"]}'] = sh
    bridge = sum(out[f'sub:{s["lhs"]}'] * sh for s, sh in zip(subs, shares))
    wm, wb = inp.blend[cid]
    out.update({'bridge': bridge, 'bvar': inp.bvar[cid], 'w_monthly': wm, 'w_bvar': wb})
    return wm * bridge + wb * inp.bvar[cid], out
