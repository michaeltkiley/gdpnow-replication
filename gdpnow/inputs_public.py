"""L3: assemble the Inputs bundle from public data only (decisions D2, D3, D5). Data fields come from
public_nipa / public_monthly; parameter fields are placeholders that stage 04 replaces with estimates."""
import numpy as np
import pandas as pd

from . import estimate, public_monthly as PM, public_nipa as PN
from .config import load_toml
from .inputs import Inputs, FIELD_REGISTRY

TC = load_toml('transforms.toml')['tcode']


def transform(levels):
    out = {}
    for c in levels.columns:
        tc = TC.get(c, 3)
        s = levels[c]
        out[c] = 1200 * np.log(s / s.shift(1)) if tc == 3 else (s.diff() if tc == 2 else s)
    return pd.DataFrame(out).replace([np.inf, -np.inf], np.nan)


def build(con, asof, last_price_month, ref_vintage='latest'):
    """Returns (Inputs with public data, factor panel, actual component growth, quarterly deflators)."""
    from . import store
    if ref_vintage == 'latest':
        ref_vintage = store.latest_vintage(con) if store.table_exists(con, 'wb_vintage') else None
    cx = PM.Ctx(con, asof, ref_vintage)
    q = PN.build(con, asof)
    T = q['nipa']['GDPZ_USNA'].dropna().index.max()
    T1 = T + pd.offsets.QuarterEnd(1)
    prices = PM.build_prices(cx)
    # Monthly price BVAR first (P08), so nominal monthly indicators can be deflated in months whose prices
    # are not yet published, as GDPNow does; its tails are re-estimated in stage 04 on the same data.
    reg = PM.regional_surveys(cx)
    cond = pd.DataFrame({'WTI': 1200 * np.log(cx.fred('WTISPLC')).diff(), 'ISMP': 50 + reg['prices'] / 2})
    mgdp_last = prices['MGDPN_USECONsplicefr'].dropna().index.max()
    prices_ext = estimate.monthly_price_bvar(prices, cond, last_price_month, mgdp_last)
    inv = PM.build_inventory(cx, prices_ext, reg)
    levels, contrib = PM.build_indicators(cx, prices_ext, q, inv)
    # Indicator levels are actual data only: drop months after each source's own last release.
    growth = transform(levels).join(contrib)
    cons_L, cons_G = PM.build_consumption(cx, prices_ext)
    # Factor panel: every transformed series with a documented transformation code, the regional-survey
    # substitutes for ISM (D2/D3) and Michigan sentiment.
    panel = growth[[c for c in growth.columns if c in TC]].copy()
    panel['NAPMC_USECON'] = inv['NAPMC_USECON']
    panel['NAPMII_USECON'] = inv['NAPMII_USECON']
    panel['EMPIRE'], panel['DALLAS'] = reg['empire'], reg['dallas']
    panel['MICHIGAN'] = reg['michigan'].diff()
    panel = panel.loc['1967-02-28':]
    nipa = q['nipa']
    qp = q['prices']
    comp = lambda d, t: {k.replace('_USNAqtr', ''): v for k, v in d.loc[t].dropna().items()}
    inv_defl = nipa[['DSNMD_USNA', 'DSNMN_USNA', 'DSNWM_USNA', 'DSNWW_USNA', 'DSNRDV_USNA', 'priceRETINVexautoSplice']].copy()
    empty = {}
    inp = Inputs(
        T=T, T1=T1, growth=growth, levels=levels, factor=pd.Series(dtype=float), faar=empty, bridge=empty,
        blend={k: (1.0, 0.0) for k in ['FNEZ', 'FNPZ', 'FNSZ', 'FRZ', 'GFZ', 'GSZ', 'PTVH', 'PTXNETMH', 'PTXNETSH']},
        bvar={}, q_hist=q['q_hist'], nominal=q['nominal'], nipa=nipa, prices_T1={},
        prices_T0=comp(qp, T), prices_Tm=comp(qp, T - pd.offsets.QuarterEnd(1)),
        monthly_prices=prices, cons_growth=cons_G, cons_levels=cons_L, util_travel={},
        inv_raw=inv, inv_deflators=inv_defl, cipi_paths=pd.DataFrame(), iva_paths=pd.DataFrame(), farm_other={},
        flags={'drop_terms': ['valExHomeSales'], 'travel_quarterly': PM.travel_quarterly(cx)},
        source=f'public:{asof}', provenance={f: f'public:{asof}' for f in FIELD_REGISTRY})
    return inp, panel, q['actual'], qp.loc[:T]
