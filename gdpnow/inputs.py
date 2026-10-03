"""The bundle of data and parameters the nowcast assembly consumes.

The assembly code (components.py, aggregate.py) only ever sees an `Inputs`. In L1 every field is read from
the Atlanta Fed workbook (test fixture). In L2/L3 the parameter fields are produced by our own estimation
modules and the data fields by our own ingest/transform stages.
"""
from dataclasses import dataclass, field

import pandas as pd

from . import store


@dataclass
class Inputs:
    T: pd.Timestamp                 # last quarter with a published BEA estimate
    T1: pd.Timestamp                # quarter being nowcast
    growth: pd.DataFrame            # monthly transformed series (1200*dlog or first difference), NaN = unreleased
    levels: pd.DataFrame            # monthly levels, NaN = unreleased
    factor: pd.Series               # dynamic factor, actual + forecast
    faar: dict                      # ticker -> {const, ar1..ar12, f0..f3}
    bridge: dict                    # lhs -> {rhs: coefficient}
    blend: dict                     # component id -> (weight on monthly/bridge model, weight on quarterly BVAR)
    bvar: dict                      # component id -> quarterly BVAR forecast for T1 (log growth SAAR; CIPI/1000)
    q_hist: pd.DataFrame            # quarterly log growth (SAAR) of detailed subcomponents
    nominal: pd.DataFrame           # quarterly nominal values of detailed subcomponents
    nipa: pd.DataFrame              # quarterly NIPA levels (real, nominal, prices, inventory stocks)
    prices_T1: dict                 # component id -> implicit deflator forecast for T1 (quarterly price BVAR)
    prices_T0: dict                 # component id -> published implicit deflator for T
    prices_Tm: dict                 # component id -> published implicit deflator for T-1 (inventory EOQ averaging)
    monthly_prices: pd.DataFrame    # monthly price levels incl. forecasts (deflators, monthly nominal GDP)
    cons_growth: pd.DataFrame       # consumption monthly transformed series
    cons_levels: pd.DataFrame       # consumption monthly levels (nominal and real)
    util_travel: dict               # lhs -> {rhs: coefficient}
    inv_raw: pd.DataFrame           # monthly inventory book values and IVAs
    inv_deflators: pd.DataFrame     # quarterly inventory deflators incl. T1 forecast
    cipi_paths: pd.DataFrame        # monthly nominal (Census industries) / real (others) CIPI forecasts
    iva_paths: pd.DataFrame         # monthly IVA forecasts
    farm_other: dict                # farm / other inventory AR(4) coefficients
    flags: dict = field(default_factory=dict)
    source: str = ''


# Registry entries (registry/parameters.csv) carried by each Inputs field; used for the provenance manifest.
FIELD_REGISTRY = {
    'factor': ['P01', 'T01'], 'faar': ['P02'], 'bridge': ['P03', 'P04'], 'blend': ['P05'], 'bvar': ['P06'],
    'prices_T1': ['P07'], 'monthly_prices': ['P08', 'P09', 'W05', 'W06', 'W07'], 'util_travel': ['P10'],
    'farm_other': ['P11'], 'cipi_paths': ['P12'], 'iva_paths': ['P13'], 'inv_deflators': ['P14'],
    'nominal': ['W01'], 'cons_levels': ['W02'], 'cons_growth': ['W08', 'P08', 'P10'],
    'q_hist': ['W09'], 'growth': ['T02', 'T03', 'T04'], 'levels': ['T02', 'T03'],
}


def _coef_dict(df):
    out = {}
    for r in df.itertuples():
        out.setdefault(r.lhs, {})[r.rhs] = r.value
    return out


def from_workbook(con, vintage):
    """L1: every field from the workbook vintage (test fixture only)."""
    s = lambda sheet: store.series_frame(con, vintage, sheet)
    q_act = s('QtrlyActDLog')
    T = q_act.dropna(how='all').index.max()
    T1 = T + pd.offsets.QuarterEnd(1)
    faar = {}
    for r in store.query(con, 'SELECT ticker, term, value FROM wb_faar WHERE vintage = ?', (vintage,)).itertuples():
        faar.setdefault(r.ticker, {})[r.term] = r.value
    coef = store.query(con, 'SELECT sheet, lhs, rhs, value FROM wb_coef WHERE vintage = ?', (vintage,))
    rls = _coef_dict(coef[coef.sheet == 'RLSweights'])
    bvar_df = s('QtrlyBVARForecasts')
    price_df = s('QtrlyPriceForecasts')
    comp = lambda k: k.replace('_USNAqtr', '')
    return Inputs(
        T=T, T1=T1,
        growth=s('TransformedMonthlySeries'), levels=s('MonthlyLevels'),
        factor=s('Factor')['Actual+Forecast'],
        faar=faar,
        bridge=_coef_dict(coef[coef.sheet == 'BridgeEqnCoeffs']),
        blend={comp(k): (v['monthly'], v['bvar']) for k, v in rls.items()},
        bvar={comp(k): v for k, v in bvar_df.loc[T1].dropna().items()},
        q_hist=s('dLogQtrlyGrowth'), nominal=s('NomQtrlyComps'), nipa=s('QtrlyGDPData'),
        prices_T1={comp(k): v for k, v in price_df.loc[T1].dropna().items()},
        prices_T0={comp(k): v for k, v in price_df.loc[T].dropna().items()},
        prices_Tm={comp(k): v for k, v in price_df.loc[T - pd.offsets.QuarterEnd(1)].dropna().items()},
        monthly_prices=s('MonthlyPriceLevels'),
        cons_growth=s('ConsTransformedMonthlySeries'), cons_levels=s('ConsMonthlyLevels'),
        util_travel=_coef_dict(coef[coef.sheet == 'UtilTravelCoeffs']),
        inv_raw=s('InventoryRaw'), inv_deflators=s('InvDefDatafr'),
        cipi_paths=s('CIPIbeastackFore'), iva_paths=s('ivaBEAForeStack'),
        farm_other=_coef_dict(coef[coef.sheet == 'FarmOtherInvCoeffs']),
        flags={'use_published_prior_cipi': False},   # Inventories!C2 = 0 in this vintage
        source=f'workbook:{vintage}',
    )
