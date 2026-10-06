"""Quarterly NIPA block of the L3 Inputs from BEA (nominal detail, real detail growth, aggregates, prices,
inventory stocks). Recipes identified by exact value matching against the workbook (tools: see DESIGN §11)."""
import numpy as np
import pandas as pd

from . import public_data as P

# Real detail growth uses BEA quantity-index tables (...03): chained-dollar detail tables (...06) start in
# 2007 in the API, quantity indexes go back to the 1940s-50s and give identical growth rates.
REAL = {'T10105': 'T10103', 'T50305': 'T50303', 'T31005': 'T31003', 'T31105': 'T31103', 'T30905': 'T30903',
        'T70205B': 'T70203B', 'U50405': 'U50404', 'U50505': 'U50504', 'T40205B': 'T40203B'}
PRICE_ROUTE = {'U50405', 'U50505'}
# Lines that include a sign-changing item (CCC inventory change): BEA chained dollars (from 2007) as GDPNow uses.
CHAINED = {'GFNENX_USNAqtr': 'T31006'}   # underlying detail has price indexes only: real = nominal / price
UDT = {'U50405', 'U50406', 'U50505', 'U50506', 'U50403', 'U50503', 'U50404', 'U50504'}

# Nominal detail (NomQtrlyComps): ticker -> list of (table, line, sign). Real counterpart: same lines, real table.
DETAIL = {
    'AINX_USNAqtr': [('T70205B', 14, 1)], 'TIX_USNAqtr': [('T70205B', 15, 1)], 'MVIUTX_USNAqtr': [('T70205B', 18, 1)],
    'FNENPX_USNAqtr': [('U50505', 4, 1)], 'FNETPX_USNAqtr': [('U50505', 26, 1)],
    'nomCoreE': [('U50505', 2, 1), ('U50505', 4, -1), ('U50505', 26, -1), ('T70205B', 14, -1), ('T70205B', 15, -1),
                 ('T70205B', 18, -1)],
    'FNPSX_USNAqtr': [('T50305', 17, 1)], 'FNPRX_USNAqtr': [('T50305', 18, 1)], 'FNPEX_USNAqtr': [('T50305', 19, 1)],
    'nomNResStructExMineWell': [('U50405', 2, 1), ('U50405', 24, -1)],
    'FNSMPX_USNAqtr': [('U50405', 25, 1)], 'FNSMIX_USNAqtr': [('U50405', 26, 1)],
    'FRSPX_USNAqtr': [('U50405', 40, 1)], 'FRSHMX_USNAqtr': [('U50405', 44, 1)], 'FRSHDX_USNAqtr': [('U50405', 45, 1)],
    'FRSBKX_USNAqtr': [('U50405', 47, 1)], 'FRSINX_USNAqtr': [('U50405', 46, 1)], 'FREX_USNAqtr': [('U50505', 46, 1)],
    'GFDESLX_USNAqtr': [('T31105', 6, 1)], 'GFDESVX_USNAqtr': [('T31105', 7, 1)], 'GFDSFX_USNAqtr': [('T31105', 8, 1)],
    'NomNatDefenseTreasCats': [('T31105', 10, 1), ('T31105', 21, 1), ('T31105', 30, 1), ('T31105', 31, 1)],
    'GFDENX_USNAqtr': [('T31105', 17, 1)], 'negGDOAIX_USNAqtr': [('T31105', 27, -1)], 'negGDOSSX_USNAqtr': [('T31105', 28, -1)],
    'GFDIPX_USNAqtr': [('T31105', 38, 1)],
    'GGNESX_USNAqtr': [('T31005', 37, 1)], 'GFNSFX_USNAqtr': [('T31005', 38, 1)], 'GFNEDX_USNAqtr': [('T31005', 40, 1)],
    'GFNENX_USNAqtr': [('T31005', 41, 1)], 'GFNESX_USNAqtr': [('T31005', 44, 1)], 'negGNOAIX_USNAqtr': [('T31005', 45, -1)],
    'negGNOSSX_USNAqtr': [('T31005', 46, -1)], 'GFNISX_USNAqtr': [('T30905', 28, 1)], 'GFNIEX_USNAqtr': [('T30905', 29, 1)],
    'GFNIPX_USNAqtr': [('T30905', 30, 1)],
    'GGSESX_USNAqtr': [('T31005', 50, 1)], 'GSESFX_USNAqtr': [('T31005', 51, 1)], 'GSEPIX_USNAqtr': [('T31005', 52, 1)],
    'negGSOAIX_USNAqtr': [('T31005', 56, -1)], 'negGSOSSX_USNAqtr': [('T31005', 57, -1)],
    'GSISX_USNAqtr': [('T30905', 36, 1)], 'GSIEX_USNAqtr': [('T30905', 37, 1)], 'GSIPX_USNAqtr': [('T30905', 38, 1)],
}
# Real quantity id (dLogQtrlyGrowth / bridge LHS) for each nominal id.
REAL_ID = {k: k.replace('X_USNAqtr', 'Z_USNAqtr') for k in DETAIL if k.endswith('X_USNAqtr')}
REAL_ID.update({'nomCoreE': 'HerzonCoreEQty', 'nomNResStructExMineWell': 'NResStructExMineWellQuant',
                'NomNatDefenseTreasCats': 'NatDefenseTreasCatsQty'})
for k in [k for k in REAL_ID if k.startswith('neg')]:
    REAL_ID[k] = k[3:].replace('X_USNAqtr', 'Z_USNAqtr')

# 13 GDP components and GDP (T10105 / T10106 lines).
AGG = {'GDP': 1, 'CTG': 3, 'CS': 6, 'FNS': 10, 'FNE': 11, 'FNP': 12, 'FR': 13, 'V': 14, 'XM': 17, 'XS': 18,
       'MM': 20, 'MS': 21, 'GF': 23, 'GS': 26}
# Inventory stocks (T50806B real, T50805B nominal, T50809B deflators).
INV = {'SNMDZ': 5, 'SNMNZ': 6, 'SNWMZ': 22, 'SNWWZ': 25, 'SNRDVZ': 11, 'SFZ': 2, 'SZ': 1}
INV_DEF = {'DSNMD': 5, 'DSNMN': 6, 'DSNWM': 21, 'DSNWW': 24, 'DSNRDV': 11, 'DSF': 2}


def _line(tab, n):
    return tab[[c for c in tab.columns if c.split('|')[0] == str(n)][0]]


def _real(tab, t, n):
    """Real (quantity) series for line n of nominal table t: quantity index, or nominal / price index."""
    nom = _line(tab(t), n)
    q = _match(tab(t), tab(REAL[t]), n)
    if t in PRICE_ROUTE:
        return nom / q
    if q.dropna().shape[0] < 0.8 * nom.dropna().shape[0]:          # incomplete quantity index
        price_tab = t[:-2] + '04' if t[-1].isdigit() else t[:-3] + '04' + t[-1]
        return nom / _match(tab(t), tab(price_tab), n)
    return q


def _match(nom_tab, q_tab, n):
    """Line of q_tab matching line n of nom_tab by description (nearest line number if repeated)."""
    desc = [c for c in nom_tab.columns if c.split('|')[0] == str(n)][0].split('|', 1)[1]
    cands = [c for c in q_tab.columns if c.split('|', 1)[1] == desc]
    if not cands:
        near = sorted(q_tab.columns, key=lambda c: abs(int(c.split('|')[0]) - n))[:4]
        raise RuntimeError(f'no line of the quantity/price table matches line {n} {desc!r} of the nominal table; nearest lines: {near}')
    best = min(cands, key=lambda c: abs(int(c.split('|')[0]) - n))
    return q_tab[best]


def tornqvist_growth(parts):
    """400*dlog of a sum of signed chained-dollar parts, by Tornqvist aggregation: parts = list of
    (nominal, real, sign). Used for residual aggregates (Fisher subtraction approximation)."""
    nom = sum(s * n for n, _, s in parts)
    out = 0
    for n, r, s in parts:
        share = (s * n / nom + (s * n / nom).shift(1)) / 2
        out = out + share * 400 * np.log(r / r.shift(1))
    return out


def build(con, asof):
    tabs = {}

    def tab(name):
        if name not in tabs:
            tabs[name] = P.bea_table(con, 'NIUnderlyingDetail' if name in UDT else 'NIPA', name, 'Q', asof)
        return tabs[name]

    nominal, qgrowth = {}, {}
    for k, recipe in DETAIL.items():
        nominal[k] = sum(s * _line(tab(t), n) for t, n, s in recipe)
        parts = [(_line(tab(t), n), _match(tab(t), tab(CHAINED[k]), n) if k in CHAINED else _real(tab, t, n), s)
                 for t, n, s in recipe]
        if len(parts) == 1:
            n_, r_, _ = parts[0]
            qgrowth[REAL_ID[k]] = 400 * np.log(r_.abs() / r_.abs().shift(1))
        else:
            qgrowth[REAL_ID[k]] = tornqvist_growth(parts)
    lt = _line(tab('T70205B'), 16) / _line(tab('T70205B'), 15)
    nominal['LightTruckShare'] = lt
    nipa = {}
    for c, n in AGG.items():
        nom = _line(tab('T10105'), n)
        nipa[f'{c}X_USNA'] = nom
        if c in ('GDP', 'V'):
            nipa[f'{c}Z_USNA'] = _line(tab('T10106'), n)
        else:
            # Chained 2017 dollars = 2017 nominal value x quantity index / 100 (BEA definition); the API's
            # chained-dollar component levels start in 2007, the quantity indexes in 1947.
            qi = _match(tab('T10105'), tab('T10103'), n)
            nipa[f'{c}Z_USNA'] = qi * nom.loc['2017'].mean() / qi.loc['2017'].mean()
    s6, s5, s9 = tab('T50806B'), tab('T50805B'), tab('T50809B')
    for c, n in INV.items():
        nipa[f'{c}_USNA'] = _line(s6, n)
    for c, n in INV_DEF.items():
        nipa[f'{c}_USNA'] = _line(s9, n)
    # Retail excluding motor-vehicle dealers (Fisher/Tornqvist subtraction of real stocks) and its deflator.
    retail_n, mv_n = _line(s5, 10), _line(s5, 11)
    retail_r, mv_r = _line(s6, 10), _line(s6, 11)
    g = tornqvist_growth([(retail_n, retail_r, 1), (mv_n, mv_r, -1)])
    nom_x = retail_n - mv_n
    real_x = _chain_level(g, nom_x)
    nipa['quantRETINVexautoSplice'], nipa['priceRETINVexautoSplice'] = real_x, 100 * nom_x / real_x
    # Construction, mining, utilities and other industries: total less the industries modelled separately.
    parts = [(_line(s5, n), _line(s6, n), 1) for n in (3, 15)]   # mining/utilities/construction + other industries
    g = tornqvist_growth(parts)
    nom_o = _line(s5, 3) + _line(s5, 15)
    real_o = _chain_level(g, nom_o)
    nipa['SNOZ_USNAqtrExtrap'], nipa['DSNO_USNAqtr'] = real_o, 100 * nom_o / real_o
    prices = {f'{c}Z_USNAqtr': 100 * nipa[f'{c}X_USNA'] / nipa[f'{c}Z_USNA'] for c in AGG if c not in ('GDP', 'V')}
    # Inventory price: implicit deflator of private inventory stocks (end of quarter).
    old = tab('T50809A')
    prices['VZ_USNAqtr'] = _splice(_line(s9, 1), _line(old, 1))
    actual = {f'{c}Z': 400 * np.log(nipa[f'{c}Z_USNA'] / nipa[f'{c}Z_USNA'].shift(1)) for c in AGG if c not in ('GDP', 'V')}
    frame = lambda d: pd.DataFrame(d).sort_index()
    return {'nominal': frame(nominal), 'q_hist': frame(qgrowth), 'nipa': frame(nipa), 'prices': frame(prices),
            'actual': frame(actual)}


def _splice(new, old):
    new, old = new.dropna(), old.dropna()
    common = new.index.intersection(old.index)
    if len(common) == 0:
        return new
    t = common[0]
    return new.combine_first(old * new[t] / old[t])


def _chain_level(g, nom):
    """Chained-dollar level consistent with growth g (400*dlog), anchored so the last-year average real
    level equals the average nominal level of the reference year 2017."""
    lvl = np.exp(g.fillna(0).cumsum() / 400)
    ref = (nom.loc['2017'].mean() / lvl.loc['2017'].mean()) if len(nom.loc['2017']) else 1.0
    return lvl * ref
