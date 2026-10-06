"""Monthly blocks of the L3 Inputs, built only from public sources (FRED/ALFRED as of --asof, BEA, Census,
Treasury). Each builder reproduces a workbook series by its documented recipe (WP Tables A2, A4, A8;
Mods); tools/validate_public.py and stage 06 benchmark them against the workbook (benchmark only).
"""
import os
import urllib.parse

import numpy as np
import pandas as pd
from statsmodels.tsa.x13 import x13_arima_analysis

from . import bls_flat as BF, history as H, ids0182 as IDS, trade_bvar as TB, public_data as P
from .config import ROOT, load_toml

X13 = str(ROOT / 'tools' / 'x13' / 'x13as' / 'x13as_ascii')
MAP = load_toml('public_series.toml')


PRICE_PREFIXES = ('CPI', 'CUSR', 'WPS', 'WPU', 'PCU', 'IR', 'IQ', 'MEDCPI', 'TRMMEAN', 'PPI')


def shutdown_fill(s):
    """Oct-2025 government-shutdown fill (Mods Dec-2025, registry T05): a missing October 2025 price value
    between available September and November values is the geometric mean of the two."""
    a, o, b = (pd.Timestamp(d) for d in ('2025-09-30', '2025-10-31', '2025-11-30'))
    if o not in s.index and a in s.index and b in s.index:
        s = s.copy()
        s[o] = np.sqrt(s[a] * s[b])
        s = s.sort_index()
    return s


def empty():
    return pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def me(s):
    """Index to month ends."""
    s = s.copy()
    s.index = pd.DatetimeIndex(s.index) + pd.offsets.MonthEnd(0)
    return s[~s.index.duplicated(keep='last')]


def seasadj(s, start='1990'):
    """Census X-13ARIMA-SEATS default seasonal adjustment (decision D3)."""
    x = s.loc[start:].dropna()
    x.index = x.index.to_period('M')
    sa = x13_arima_analysis(x, x12path=X13, outlier=True).seasadj
    sa.index = sa.index.to_timestamp(how='end').normalize()
    return sa


def chain(parts, fisher=True):
    """Fisher (or Tornqvist) chained quantity and price index of a sum of components.
    parts: list of (nominal, price index) monthly series; returns (real level in base-period $, price)."""
    N = pd.concat([n for n, _ in parts], axis=1)
    Pr = pd.concat([p for _, p in parts], axis=1)
    N.columns = Pr.columns = range(len(parts))
    Q = N / Pr
    d = pd.concat([N, Pr], axis=1).dropna().index
    N, Pr, Q = N.loc[d], Pr.loc[d], Q.loc[d]
    lasp = (Pr.shift(1) * Q).sum(axis=1) / (Pr.shift(1) * Q.shift(1)).sum(axis=1)
    paas = (Pr * Q).sum(axis=1) / (Pr * Q.shift(1)).sum(axis=1)
    rel = np.sqrt(lasp * paas).fillna(1.0)
    q = rel.cumprod()
    nom = N.sum(axis=1)
    q = q * nom.loc['2017'].mean() / q.loc['2017'].mean() if len(q.loc['2017']) else q
    return q, 100 * nom / q


def atkeson_ohanian(q_price, months_index):
    """Monthly price index from a quarterly one (WP appendix "Atkeson-Ohanian interpolation"): every month of
    quarter t grows at 1/12 of the quarter t-1 over t-5 inflation rate, chained continuously across quarters
    (no re-anchoring to the quarterly levels; verified against the workbook's implied computers deflator).
    Level anchored at the first quarter-end level; only growth rates matter."""
    q = q_price.dropna()
    g = np.log(q / q.shift(4)) / 12
    months = [t for t in months_index if (t + pd.offsets.QuarterEnd(0) - pd.offsets.QuarterEnd(1)) in g.dropna().index]
    gm = pd.Series({t: g[t + pd.offsets.QuarterEnd(0) - pd.offsets.QuarterEnd(1)] for t in months}).sort_index()
    return np.exp(gm.cumsum()) * float(q[gm.index[0] + pd.offsets.QuarterEnd(0) - pd.offsets.QuarterEnd(1)])


def census(series, dataset, category, data_type, seasonal='yes', time_from='1992'):
    """Census EITS time series (current vintage). Returns monthly Series."""
    q = dict(get='cell_value,time_slot_id', category_code=category, data_type_code=data_type,
             seasonally_adj=seasonal, time=f'from {time_from}', key=os.environ.get('CENSUS_API_KEY', ''))
    q['for'] = 'us:*'
    url = f'https://api.census.gov/data/timeseries/eits/{dataset}?' + urllib.parse.urlencode(q)
    rows = P._get(url)
    hdr, data = rows[0], rows[1:]
    i, j = hdr.index('cell_value'), hdr.index('time')
    s = pd.Series({pd.Period(r[j], 'M').end_time.normalize(): float(r[i]) for r in data if r[i] not in ('', '(S)')})
    return s.sort_index()


class Ctx:
    """Cached access to the sources, as of one date. `ref_vintage` names a GDPNow workbook vintage loaded in
    DuckDB: its series serve as the last backward-splicing layer and as the splice quality reference."""

    def __init__(self, con, asof, ref_vintage=None, ism='public'):
        self.con, self.asof, self.cache, self.ref_vintage, self.ism_mode = con, str(asof), {}, ref_vintage, ism
        self._refs = {}

    def ref(self, sheet, col):
        """Workbook level series for `col`, or None (no reference in production without a workbook)."""
        if self.ref_vintage is None:
            return None
        if sheet not in self._refs:
            from . import store
            self._refs[sheet] = store.series_frame(self.con, self.ref_vintage, sheet)
        f = self._refs[sheet]
        return f[col].dropna() if col in f else None

    def splice(self, name, live, proxies=(), sheet=None, col=None, kind=None):
        """Durable long history for `name` (gdpnow/history.py); reference layer from the workbook if available."""
        live = live.dropna()
        ref = self.ref(sheet, col or name) if sheet else None
        if kind is None:
            pos = (live > 0).all() and (ref is None or (ref > 0).all())
            kind = 'log' if pos else 'diff'
        label = f'workbook:{self.ref_vintage}'
        try:
            return H.splice(self.con, name, live, proxies, self.asof, kind, reference=ref, ref_label=self.ref_vintage)[0]
        except Exception as e:                       # never lose a series because history splicing failed
            print(f'  splice skipped for {name}: {str(e)[:80]}')
            return live

    def ism(self, name, proxy):
        """ISM manufacturing index `name` (licensed; the ISM site is login-only). Core run (ism='public'): the
        regional-survey average rescaled to the ISM 50 = no-change convention. Sensitivity run (ism='seeded'):
        history: ISM actuals seeded once
        from the GDPNow workbook into the durable table hist_levels (flagged by source) and used up to the last
        month released before the as-of date; later months: nowcast from the regional-survey `proxy` by OLS
        fitted on the overlapping actual history, re-estimated every run (the fit is data, not a stored coefficient)."""
        if self.ism_mode == 'public':
            return 50 + proxy / 2
        ref = self.ref('InventoryRaw', name)
        if ref is not None:
            df = pd.DataFrame({'name': name, 'date': ref.index, 'value': ref.to_numpy(), 'source': f'workbook:{self.ref_vintage}'})
            self.con.register('_l', df)
            self.con.execute('CREATE TABLE IF NOT EXISTS hist_levels AS SELECT * FROM _l LIMIT 0')
            self.con.execute('DELETE FROM hist_levels WHERE name = ?', [name])
            self.con.execute('INSERT INTO hist_levels SELECT * FROM _l')
            self.con.unregister('_l')
        st = self.con.execute('SELECT date, value FROM hist_levels WHERE name = ? ORDER BY date', [name]).fetchdf()
        actual = pd.Series(st.value.to_numpy(), index=pd.to_datetime(st.date))
        cutoff = (pd.Timestamp(self.asof) - pd.Timedelta(days=1)).to_period('M').end_time.normalize() - pd.offsets.MonthEnd(1)
        actual = actual.loc[:cutoff]
        j = pd.concat([actual, proxy], axis=1, keys=['y', 'x']).dropna()
        b = np.polyfit(j.x, j.y, 1)
        later = proxy.loc[proxy.index > actual.index.max()].dropna()
        return pd.concat([actual, pd.Series(np.polyval(b, later.to_numpy()), index=later.index)]).sort_index()

    def long_history(self, df, sheet):
        """Splice every column of df backward (reference layer) where the workbook series is longer."""
        out = {}
        for c in df.columns:
            live = df[c].dropna()
            ref = self.ref(sheet, c)
            if len(live) and ref is not None and ref.index.min() < live.index.min() - pd.offsets.MonthEnd(12):
                out[c] = self.splice(c, live, (), sheet, c)
            else:
                out[c] = df[c]
        return pd.DataFrame(out)

    def fred(self, fid):
        if ('f', fid) not in self.cache:
            s = me(P.fred(self.con, fid, self.asof))
            self.cache[('f', fid)] = shutdown_fill(s) if fid.startswith(PRICE_PREFIXES) else s
        return self.cache[('f', fid)]

    def fred_opt(self, fid):
        """Like fred(), but None if the id does not exist on FRED."""
        try:
            return self.fred(fid)
        except Exception:
            return None

    def bea(self, table, freq='M', line=None, ds=None):
        key = ('b', table, freq)
        if key not in self.cache:
            dsn = ds or ('NIPA' if table.startswith('T') else 'NIUnderlyingDetail')
            self.cache[key] = P.bea_table(self.con, dsn, table, freq, self.asof)
        t = self.cache[key]
        if line is None:
            return t
        return t[[c for c in t.columns if c.split('|')[0] == str(line)][0]]

    def census(self, *a, **k):
        """Census API series, archived per as-of date like the other pulls (a re-run of a day reuses its archive)."""
        key = ('c',) + a + tuple(sorted(k.items()))
        if key not in self.cache:
            name = '|'.join(str(x) for x in a[1:]) + '|' + '|'.join(f'{kk}={vv}' for kk, vv in sorted(k.items()))
            src = f'census_eits:{a[1]}'
            s = P._archived(self.con, src, name, self.asof)
            if s is None:
                s = census(*a, **k)
                P._archive(self.con, src, name, self.asof, s)
            self.cache[key] = s
        return self.cache[key]


def asof_cut(s, asof):
    """Drop observations for months not yet ended at the vintage date (safety for current-vintage APIs)."""
    return s.loc[:pd.Timestamp(asof) - pd.offsets.MonthEnd(1)]


# ----------------------------------------------------------------------------------------- price panel
def splice_back(new, old):
    """Extend `new` backwards with the growth of `old` (ratio link at the first common month)."""
    new, old = new.dropna(), old.dropna()
    common = new.index.intersection(old.index)
    if len(common) == 0:
        return new
    t = common[0]
    return new.combine_first(old * new[t] / old[t])


def cleveland_index(rate):
    """Index from an annualized monthly percent-change series (Cleveland Fed median / trimmed-mean CPI)."""
    r = rate.dropna()
    return 100 * np.exp(np.log1p(r / 100).cumsum() / 12)


def build_prices(cx):
    f, b = cx.fred, cx.bea
    out = {
        'PCU_USECONfr': f('CPIAUCSL'), 'PCUSLFE_USECONfr': f('CPILFESL'), 'PCUSND_USECONfr': f('CUSR0000SAN'),
        'PCUSSLE_USECONfr': f('CUSR0000SASLE'), 'UH_CPIDATAfr': f('CPIHOSSL'),
        'PA49207_USECONfr': f('WPSFD49207'), 'PA41312_PPIfr': f('WPSFD41312'), 'PC152_PPIfr': f('WPSID6152'),
        'PC9115_PPIfr': f('WPSID69115'),
        'SplicedMedianfr': cleveland_index(f('MEDCPIM158SFRBCLE')), 'SplicedTrimfr': cleveland_index(f('TRMMEANCPIM158SFRBCLE')),
        'PXEA_USECONsplicefr': f('IQ'),
    }
    out['R5312101_PPIRInterpfr'] = cx.splice('R5312101_PPIRInterpfr', f('PCU5312105312101'), [('cpi_shelter', f('CPIHOSSL'))], 'MonthlyPriceLevels')
    out['saRMFG_PPIRsplicefr'] = seasadj(f('PCUOMFGOMFG'), '1985')
    # Trade sales deflators (NIPA underlying detail 2BUI), spliced with the SIC-based tables (2AUI).
    for name, line_new in [('SpliceManTradeDeflatorfr', 2), ('SpliceWholesaleTradeDeflatorfr', 28),
                           ('SpliceRetailTradeDeflatorfr', 51)]:
        out[name] = b('U002BUI', line=line_new)
    # PCE implicit deflators (NIPA underlying detail 2.4.4U lines).
    for name, line in [('CDMVNMDeffr', 5), ('CDMVUMDeffr', 12), ('CNEMDeffr', 113), ('CSFPMDeffr', 236)]:
        out[name] = b('U20404', line=line)
    cons = pce_aggregates(cx)
    out['CoreRealRetailPCEDeffr'] = cons['core_retail_price_incl_food_svc']
    out['CoreRealRetailPCEDefExFoodSvcfr'] = cons['core_retail_price']
    out['HerzonServicesExFoodDeffr'] = cons['other_services_price']
    out['CPIMajappSplicefr'] = bls(cx, 'CUSR0000SEHK01', start=1967)      # CPI major appliances (BLS only; not on FRED)
    out['SA_HN1PA_USECON_fr'] = seasadj(f('ASPNHSUS'), '1975')
    idx = pd.date_range('1965-01-31', pd.Timestamp(cx.asof), freq='ME')
    out['CCIHD_USECONfr'] = census_house_price(cx, idx)
    out['TornPriceNonResStrMthfr'] = structures_price(cx, idx, out['CCIHD_USECONfr'])
    out['PMEA_USECONsplicefr'] = goods_import_price(cx)
    out['MGDPN_USECONsplicefr'] = monthly_nominal_gdp(cx)
    df = cx.long_history(pd.DataFrame({k: me(v) for k, v in out.items()}).sort_index(), 'MonthlyPriceLevels')
    svc = services_trade_deflators(cx, df)
    df['ImpSvcDefmthA1fr'], df['ExpSvcDefmthA1fr'] = svc
    return df


# Table A9 (WP 2014; Mods Dec-2025): leaf structure types of private nonresidential construction (NIPA 5.4.4U/5.4.5U
# lines) -> monthly price source. Each source is a list of (weight, source): 'ppi:<id>', 'ccihd', 'steel', 'ao'
# (Atkeson-Ohanian extrapolation of the component's own quarterly deflator), or 'geo:<id>+<id>'.
OFFICE, WAREHOUSE, INDUSTRIAL = 'PCU236223236223', 'PCU236221236221', 'PCU236211236211'
STRUCT_PRICE_MAP = {
    4: [(1, f'ppi:{OFFICE}')],                       # office aggregate (before 2020)
    5: [(1, f'geo:{INDUSTRIAL}+{WAREHOUSE}')],       # data centers (from 2020, Mods Dec-2025)
    6: [(1, f'ppi:{OFFICE}')],                       # general, financial and other office (from 2020)
    7: [(.5, 'ccihd'), (.5, 'ao')],                  # health care (Turner index unavailable: AO half)
    12: [(1, f'ppi:{WAREHOUSE}')], 13: [(1, f'ppi:{WAREHOUSE}')], 14: [(1, f'ppi:{WAREHOUSE}')],
    15: [(1, f'ppi:{WAREHOUSE}')], 29: [(1, f'ppi:{WAREHOUSE}')],
    16: [(1, f'ppi:{INDUSTRIAL}')],
    20: [(1, 'ao')], 21: [(1, 'ao')], 23: [(1, 'ao')], 36: [(1, 'ao')],
    22: [(.5, 'steel'), (.5, 'ao')],                 # other power
    28: [(.5, 'ccihd'), (.5, 'ao')], 30: [(.5, 'ccihd'), (.5, 'ao')], 31: [(.5, 'ccihd'), (.5, 'ao')],
    33: [(.5, 'ccihd'), (.5, 'ao')], 35: [(.5, 'ccihd'), (.5, 'ao')],
}
STEEL_PIPE = 'WPU101706'      # steel pipe and tube PPI: NSA, no manual seasonal adjustment (Mods Dec-2013)


def census_house_price(cx, idx):
    """Census price deflator (Fisher) of new single-family houses under construction, monthly NSA, 2005=100
    (CCIHD in the workbook; public file price_uc_cust.xlsx). Months after the last release grow at the average
    monthly rate of the latest 12 months (Atkeson-Ohanian-style extrapolation)."""
    s = P._archived(cx.con, 'census_hist', 'price_uc', cx.asof)
    if s is None:
        import io
        d = pd.read_excel(io.BytesIO(P.get_bytes('https://www.census.gov/construction/nrs/xls/price_uc_cust.xlsx')), header=None, sheet_name='Vertical',
                          skiprows=6, usecols=[0, 3]).dropna()
        d.columns = ['d', 'v']
        d['d'] = pd.to_datetime(d.d, errors='coerce') + pd.offsets.MonthEnd(0)
        s = pd.to_numeric(d.dropna(subset=['d']).set_index('d').v, errors='coerce').dropna().sort_index()
        P._archive(cx.con, 'census_hist', 'price_uc', cx.asof, s)
    s = asof_cut(s, cx.asof)
    g = np.log(s.iloc[-1] / s.iloc[-13]) / 12
    out = s.copy()
    for t in pd.date_range(s.index[-1] + pd.offsets.MonthEnd(1), idx[-1] + pd.offsets.MonthEnd(4), freq='ME'):
        out[t] = out.iloc[-1] * np.exp(g)
    return out


def structures_price(cx, idx, ccihd):
    """Monthly private nonresidential construction price (WP appendix eq. A1, Table A9): previous-quarter
    nominal-share-weighted log change of the monthly prices of the structure types (NIPA 5.4.5U shares);
    each type's monthly price is a PPI where one exists (before its start: the component's quarterly deflator,
    smoothly interpolated), the CCIHD new-home price, or the Atkeson-Ohanian extrapolation of its own deflator."""
    defl, nom = cx.bea('U50404', 'Q'), cx.bea('U50405', 'Q')
    col = lambda t, ln: t[[c for c in t.columns if c.split('|')[0] == str(ln)][0]]
    cache = {}

    def monthly_growth(ln, src):
        d = col(defl, ln)
        if src == 'ao':
            return np.log(atkeson_ohanian(d, idx)).diff()
        if src == 'ccihd':
            return np.log(ccihd).diff()
        if src == 'steel':
            ppi = cx.fred_opt(STEEL_PIPE)
        elif src.startswith('geo:'):
            ps = [cx.fred_opt(i) for i in src[4:].split('+')]
            ppi = None if any(p is None for p in ps) else np.exp(sum(np.log(p) for p in ps) / len(ps))
        else:
            ppi = cx.fred_opt(src[4:])
        ao = np.log(atkeson_ohanian(d, idx)).diff()
        if ppi is None:
            return ao
        g = np.log(ppi.dropna()).diff()
        back = ao.loc[:g.dropna().index.min()]       # before the PPI starts: the component deflator
        return pd.concat([back.iloc[:-1], g.dropna()]).sort_index()

    groups = {}
    for ln, srcs in STRUCT_PRICE_MAP.items():
        g = sum(w * monthly_growth(ln, s).reindex(idx) for w, s in srcs)
        groups[ln] = g
    G = pd.DataFrame(groups)
    N = pd.DataFrame({ln: col(nom, ln) for ln in STRUCT_PRICE_MAP})
    # 2020 on: data centers and general/financial/other replace the office aggregate (Mods Dec-2025)
    new = N.index >= '2020-01-01'
    N.loc[new, 4] = 0.0
    N.loc[~new, [5, 6]] = 0.0
    W = N.div(N.sum(axis=1), axis=0)
    W = W.reindex(W.index.union(pd.date_range(W.index.max(), periods=3, freq='QE')[1:])).shift(1).ffill()
    Wm = W.reindex(idx + pd.offsets.QuarterEnd(0)).set_axis(idx)       # month t uses the previous quarter's shares
    g = (G * Wm).sum(axis=1, min_count=1).where(Wm.notna().all(axis=1))
    g = g.dropna()
    return 100 * np.exp(g.cumsum())


def goods_import_price(cx):
    """Goods import price (W06; WP step 5a): previous-quarter-share-weighted log change of BLS end-use import price
    indexes (petroleum seasonally adjusted). Eight categories, weights = nominal imports from NIPA Table 4.2.5B
    (lines: foods 95, petroleum 107, industrial durable 102, industrial nondurable ex petroleum 108, computers 118,
    capital goods ex computers 114-118, autos 131, consumer goods 134); growth corr with the workbook's 0.9875."""
    t = cx.bea('T40205B', 'Q')
    L = lambda n: t[[c for c in t.columns if c.split('|')[0] == str(n)][0]]
    nom = {'IR0': L(95), 'IR10': L(107), 'IR1DUR': L(102), 'IR1NONDUR': L(108), 'IR213COM': L(118),
           'IR2EXCOM': L(114) - L(118), 'IR3': L(131), 'IR4': L(134)}
    p = {k: (seasadj(cx.fred(k), '1990') if k == 'IR10' else cx.fred(k)) for k in nom}
    W = pd.DataFrame(nom)
    W = W.div(W.sum(axis=1), axis=0).shift(1)                 # previous-quarter shares
    months = pd.date_range('1965-01-31', pd.Timestamp(cx.asof), freq='ME')
    Wm = W.reindex(pd.DatetimeIndex(months.to_period('Q').end_time.normalize())).ffill().set_axis(months)
    G = pd.DataFrame({k: np.log(v).diff() for k, v in p.items()})
    g = (G * Wm.reindex(G.index)).sum(axis=1, min_count=len(p)).dropna()
    lvl = np.exp(g.cumsum())
    return splice_back(100 * lvl / lvl.loc['2017'].mean(), cx.fred('IR'))


def monthly_nominal_gdp(cx):
    """Monthly nominal GDP (D3 substitute for the Macroeconomic Advisers/S&P series): proportional Denton-type
    interpolation of BEA quarterly nominal GDP with monthly nominal PCE (ratio held linear within quarter),
    extended past the last quarter with PCE growth."""
    gdp = cx.bea('T10105', 'Q', line=1)
    pce = cx.bea('T20805', 'M', line=1)
    ratio_q = gdp / pce.resample('QE').mean()
    ratio_m = ratio_q.resample('ME').interpolate().reindex(pce.index).ffill().bfill()
    return pce * ratio_m


def services_trade_deflators(cx, prices):
    """Monthly services import/export deflators (P09; WP Table A2): quarterly regression of the NIPA services
    deflator log change on the goods import (export) price log change and the deflator's lagged 4-quarter
    change; monthly series built with the fitted quarterly relation applied to monthly goods price changes."""
    out = []
    # Goods price in the regression: exports = BLS all-exports index (verified exact); imports = BLS all imports
    # excluding petroleum (IREXPET; R^2 = 1.000 against the workbook's deflator; the weighted PMEA does not fit).
    gprice = {'PXEA_USECONsplicefr': prices['PXEA_USECONsplicefr'],
              'IREXPET': splice_back(cx.fred('IREXPET'), prices['PMEA_USECONsplicefr'])}
    for line_n, line_r, goods in [(21, 21, 'IREXPET'), (18, 18, 'PXEA_USECONsplicefr')]:
        d = 100 * cx.bea('T10105', 'Q', line=line_n) / cx.bea('T10106', 'Q', line=line_r)
        gq = np.log(gprice[goods].resample('QE').mean())
        y, x1, x2 = np.log(d).diff(), gq.diff(), np.log(d).diff(4).shift(1)
        dd = pd.concat([y, x1, x2], axis=1, keys=['y', 'a', 'b']).dropna()
        A = np.column_stack([np.ones(len(dd)), dd.a, dd.b])
        beta = np.linalg.lstsq(A, dd.y.to_numpy(), rcond=None)[0]
        # Every month (history included) follows the fitted relation applied to the monthly goods price change
        # and the previous quarter's 4-quarter deflator change (verified against the workbook: exports corr 1.000).
        gm = np.log(gprice[goods]).diff()
        lag4 = np.log(d).diff(4)
        l4 = pd.Series(lag4.reindex(pd.DatetimeIndex(gm.index.to_period('Q').end_time.normalize()) - pd.offsets.QuarterEnd(1)).to_numpy(),
                       index=gm.index)
        g = (beta[0] / 3 + beta[1] * gm + beta[2] * l4 / 3).dropna()
        m = 100 * np.exp(g.cumsum())
        out.append(m)
    return out


# --------------------------------------------------------------------------------- consumption block
def pce_aggregates(cx):
    """PCE buckets from NIPA underlying detail (2.4.5U nominal, 2.4.4U prices), Fisher-chained."""
    if ('pce',) in cx.cache:
        return cx.cache[('pce',)]
    N, Pr = cx.bea('U20405'), cx.bea('U20404')
    line = lambda t, n: t[[c for c in t.columns if c.split('|')[0] == str(n)][0]]
    pick = lambda n: (line(N, n), line(Pr, n))
    M = cx.bea('T20805')
    Mp = cx.bea('T20804')
    mline = lambda t, n: t[[c for c in t.columns if c.split('|')[0] == str(n)][0]]
    # Retail control goods (PCE goods excluding motor vehicles and parts and gasoline/energy goods).
    core = [(mline(M, n), mline(Mp, n)) for n in (5, 6, 7, 9, 10, 12)]
    core_q, core_p = chain(core)
    food_svc = pick(236)
    core_f_q, core_f_p = chain(core + [food_svc])
    # Services less food services, electricity and gas, and net foreign travel.
    svc = (mline(M, 13), mline(Mp, 13))
    minus = [pick(236), pick(169), pick(336)]
    travel_in = pick(339)
    oth_nom = svc[0] - sum(n for n, _ in minus) + travel_in[0]
    # Fisher subtraction via chaining the services aggregate with negative weights on the removed items.
    parts = [svc] + [(-n, p) for n, p in minus] + [travel_in]
    oth_q, oth_p = chain(parts)
    res = {'core_retail_nominal': core_q * core_p / 100, 'core_retail_real': core_q, 'core_retail_price': core_p,
           'core_retail_price_incl_food_svc': core_f_p,
           'other_services_nominal': oth_nom, 'other_services_real': oth_q, 'other_services_price': oth_p}
    cx.cache[('pce',)] = res
    return res


def build_consumption(cx, prices):
    N, Pr = cx.bea('U20405'), cx.bea('U20404')
    line = lambda t, n: t[[c for c in t.columns if c.split('|')[0] == str(n)][0]]
    real = lambda n: line(N, n) / line(Pr, n) * 100
    agg = pce_aggregates(cx)
    lv = {
        'CDMNM_USNA': line(N, 6), 'CDMTNM_USNA': line(N, 9), 'CDMVNM_USNA': line(N, 5), 'CDMVNHM_USNA': real(5),
        'CDMVUM_USNA': line(N, 12), 'CDMVUHM_USNA': real(12), 'CNEM_USNA': line(N, 113), 'CNEHM_USNA': real(113),
        'CSEM_USNA': line(N, 169), 'CSEHM_USNA': real(169), 'CSFPM_USNA': line(N, 236), 'CSFPHM_USNA': real(236),
        'CSFTOM_USNA': line(N, 336), 'CSFTOHM_USNA': real(336), 'CSDTFM_USNA': -line(N, 339), 'CSDTFHM_USNA': real(339),
        'sumCoreNomRetailPCEExFoodSvc': agg['core_retail_nominal'], 'CoreRealRetailPCEQtyExFoodSvc': agg['core_retail_real'],
        'sumNomServicesPCEExFoodExUtilExForTravel': agg['other_services_nominal'],
        'HerzonServicesLessFoodUtilTravelQty': agg['other_services_real'],
        'IPUTL_IP': cx.fred('IPUTIL'),
    }
    # Retail control (retail & food services ex motor vehicles, gas stations, building materials) and food
    # services, current and previous vintage (for the revision terms of the latest-month nowcast).
    prev_asof = (pd.Timestamp(cx.asof) - pd.DateOffset(months=1)).date()
    fr = lambda fid, asof: me(P.fred(cx.con, fid, asof))
    ctrl = lambda asof: fr('RSFSXMV', asof) - fr('RSGASS', asof) - fr('RSBMGESD', asof)
    lv['NRSXMI47_USECON'], lv['NRSXMI47_USECONPrevious'] = ctrl(cx.asof), ctrl(prev_asof)
    lv['NRSV2_USECON'], lv['NRSV2_USECONPrevious'] = fr('RSFSDP', cx.asof), fr('RSFSDP', prev_asof)
    lv['NRSXMI47_USECONlessNRSV2_USECON'] = lv['NRSXMI47_USECON'] - lv['NRSV2_USECON']
    lv['NRSXMI47_USECONPrevlessNRSV2_USECONPrev'] = lv['NRSXMI47_USECONPrevious'] - lv['NRSV2_USECONPrevious']
    # Monthly travel services trade (BEA trade-release time series). The "Previous" (prior-vintage) columns, used
    # only for the revision terms of the latest month, stay empty: the previous vintage is not archived publicly.
    lv['BMBSXR_USINT'], lv['BMBSMR_USINT'] = bea_travel_monthly(cx)
    for c in ['BMBSXR_USINTPrevious', 'BMBSMR_USINTPrevious']:
        lv[c] = empty()
    L = pd.DataFrame({k: me(v) for k, v in lv.items()}).sort_index()
    g = lambda s: 1200 * np.log(s / s.shift(1))
    nominal_ratio = ['NRSXMI47_USECONlessNRSV2_USECON', 'NRSXMI47_USECONPrevlessNRSV2_USECONPrev']
    G = pd.DataFrame({k: g(L[k]) for k in L.columns if not k.startswith('CSDTFM')})
    G['CSEHM_USNAFore'] = G['CSEHM_USNA']
    G['CSFTOHM_USNARev'], G['CSDTFHM_USNARev'] = G['CSFTOHM_USNA'], G['CSDTFHM_USNA']
    G['CoreRealRetailPCEDefExFoodSvcfr'] = g(prices['CoreRealRetailPCEDefExFoodSvcfr'])
    G['CSFPMDeffr'] = g(prices['CSFPMDeffr'])
    return L, G


def bea_travel_monthly(cx):
    """Monthly seasonally adjusted travel services exports and imports, $ million, 1999+ (BEA trade-release
    time-series file, Tables 2 and 3; the ITA API serves only quarterly data). Current vintage, cached in data/."""
    if 'travel' in cx.cache:
        return cx.cache['travel']
    from pathlib import Path
    path = Path('data') / f'{cx.asof.replace("-", "")}_bea_trade_time_series.xlsx'
    if not path.exists() or P.REFRESH:
        path.write_bytes(P.bea_trade_xlsx())
    out = []
    for sheet in ('Table 2', 'Table 3'):                       # exports, imports of services by category
        d = pd.read_excel(path, sheet_name=sheet, header=None)
        hdr_row = d.index[d.iloc[:, 0].astype(str).str.strip() == 'Period'][0]
        col = [i for i, v in enumerate(d.iloc[hdr_row]) if str(v).strip().startswith('Travel')][0]
        m0 = d.index[d.iloc[:, 0].astype(str).str.strip() == 'Monthly'][0]
        s_ = d.iloc[m0 + 1:, [0, col]].dropna()
        s_.columns = ['p', 'v']
        s_['d'] = pd.to_datetime(s_.p.astype(str).str.replace(r'\(.*?\)', '', regex=True).str.strip().str.replace(r'\s+', ' ', regex=True),
                                format='%Y %b', errors='coerce') + pd.offsets.MonthEnd(0)      # strip (R)/(P) markers
        out.append(pd.to_numeric(s_.dropna(subset=['d']).set_index('d').v, errors='coerce').dropna().sort_index())
    cx.cache['travel'] = (asof_cut(out[0], cx.asof), asof_cut(out[1], cx.asof))
    return cx.cache['travel']


# ------------------------------------------------------------------------------------- inventory block
def advance_overlay(hist, adv):
    """Append advance-report months (Census advance datasets) beyond the end of the full-report history."""
    hist = hist.dropna()
    extra = adv.loc[adv.index > hist.index.max()].dropna()
    return pd.concat([hist, extra])


def build_inventory(cx, prices, regional):
    f, b = cx.fred, cx.bea
    line = lambda t, n, fr='M': b(t, fr, line=n)
    adv = lambda cat, dt: asof_cut(cx.census('x', 'advm3', cat, dt), cx.asof)
    dur_ti, dur_vs = adv('MDM', 'TI'), adv('MDM', 'VS')
    raw = {
        'NMIDG_USECON': advance_overlay(f('AMDMTI'), dur_ti), 'NMSDG_USECON': advance_overlay(f('AMDMVS'), dur_vs),
        # Nondurable manufacturers' stocks/shipments: FRED ends with the previous full M3 report, but Census's M3
        # time series (eits/m3, category MNM) already carries the latest month (the advance-based estimate; the
        # workbook's August values, 364,034 / 324,931, equal this series to within 0.03%).
        'NMING_USECON': advance_overlay(f('AMNMTI'), asof_cut(cx.census('x', 'm3', 'MNM', 'TI'), cx.asof)),
        'NMSNG_USECON': advance_overlay(f('AMNMVS'), asof_cut(cx.census('x', 'm3', 'MNM', 'VS'), cx.asof)),
        'NWIH_USECON': advance_overlay(f('WHLSLRIMSA'), asof_cut(cx.census('x', 'mwtsadv', '42', 'IM'), cx.asof)),
        'NWSH_USECON': f('WHLSLRSMSA'),
        # Retail ex-autos inventories: Census API (FRED's mirror of this series is stale since 2023)
        'NRIXM_USECON': advance_overlay(asof_cut(cx.census('x', 'mrts', '4400A', 'IM'), cx.asof), asof_cut(cx.census('x', 'mrtsadv', '4400A', 'IM'), cx.asof)),
        'NRSXM_USECON': f('RSXFS') - f('RSMVPD'), 'NRSI1_USECON': f('RSMVPD'),
        'ADS_USECON': f('DAUTOSAAR'), 'AFS_USECON': f('FAUTOSAAR'), 'TLSAR_USECON': f('DLTRUCKSSAAR'),
        'TMSAR_USECON': f('FLTRUCKSSAAR'), 'IAU_IP': f('MVAAUTLTTS'),
        'IPMDG_IP': f('IPDMAN'), 'IPMND_IP': f('IPNMAN'), 'IPMFG_IP': f('IPMANSICS'), 'IP51_IP': f('IPCONGD'),
        'LADURGA_USECON': f('DMANEMP'), 'LANDURA_USECON': f('NDMANEMP'), 'LARTRDA_USECON': f('USTRADE'),
        'LAWTRDA_USECON': f('USWTRADE'),
        'PA41312_PPI': f('WPSFD41312'), 'PA49207_PPI': f('WPSFD49207'), 'PC1112_PPI': f('WPSID61112'),
        'PC1113_PPI': f('WPSID61113'), 'PC1_PPI': f('WPSID61'), 'sa_PIN_PPI_': seasadj(f('PPIIDC'), '1985'),
        'UCD_CPIDATA': f('CUSR0000SAD'), 'UCN_CPIDATA': f('CUSR0000SAN'), 'UTW_CPIDATA': f('CUSR0000SETA01'),
        'JCNLGOM_USNA': line('U20404', 115), 'PZTEXP_USECON': f('WTISPLC'),
        # BEA underlying detail: sales deflators (2BUI), real and nominal stocks (1BU / 1BUC), monthly IVA
        # (5.7.5BM3), real CIPI (5.7.6BM).
        'DTSMD_USNA': line('U002BUI', 3), 'DTSMN_USNA': line('U002BUI', 16), 'DTSWM_USNA': line('U002BUI', 28),
        'DTSR_USNA': line('U002BUI', 51), 'DTSRI1_USNA': line('U002BUI', 52),
        'TIMDH_USNA': line('U001B', 3), 'TIMD_USNA': line('U001BC', 3), 'TIMNH_USNA': line('U001B', 16),
        'TIMN_USNA': line('U001BC', 16), 'TIWMH_USNA': line('U001B', 28), 'TIWM_USNA': line('U001BC', 28),
        'TIRH_USNA': line('U001B', 51), 'TIR_USNA': line('U001BC', 51), 'TIRI1H_USNA': line('U001B', 52),
        'TIRI1_USNA': line('U001BC', 52),
        'VNMDIM_USNA': line('U50705BM3', 4), 'VNMNIM_USNA': line('U50705BM3', 5), 'VNWLMIM_USNA': line('U50705BM3', 9),
        'VNRIM_USNA': line('U50705BM3', 15), 'VNRDVIM_USNA': line('U50705BM3', 16),
        'VNRDVHM_USNA': line('U50706BM', 16), 'VNWWHM_USNA': line('U50706BM', 12),
    }
    # ISM indexes are licensed (D3): actuals from the durable store, latest months nowcast from regional Fed surveys.
    raw['NAPMC_USECON'] = cx.ism('NAPMC_USECON', regional['composite'])
    raw['NAPMII_USECON'] = cx.ism('NAPMII_USECON', regional['inventories'])
    raw['NAPMPI_USECON'] = cx.ism('NAPMPI_USECON', regional['prices'])
    # Discontinued SIC-basis sales deflators are not available publicly; the core BVAR runs on NAICS data.
    for c in ['DTSMD1_USNA', 'DTSMN1_USNA', 'DTSW1_USNA', 'DTSRX1_USNA', 'DTSRAD1_USNA']:
        raw[c] = empty()
    return cx.long_history(pd.DataFrame({k: me(v) if len(v) else v for k, v in raw.items()}).sort_index(), 'InventoryRaw')


def regional_surveys(cx):
    """Public regional Fed manufacturing surveys (Philadelphia, New York, Dallas; the only districts on FRED): diffusion
    indexes averaged across available districts (D2/D3 substitutes for ISM components)."""
    f = cx.fred_opt
    comp = [f('GACDFSA066MSFRBPHI'), f('GACDISA066MSFRBNY'), f('BACTSAMFRBDAL')]
    inv = [f('IVCDFSA066MSFRBPHI'), f('IVCDISA066MSFRBNY'), f('FGISAMFRBDAL'), f('MATISAMFRBDAL')]   # every district inventory index
    prc = [f('PPCDFSA066MSFRBPHI'), f('PPCDISA066MSFRBNY'), f('PRMSAMFRBDAL')]
    avg = lambda xs: pd.concat([x for x in xs if x is not None], axis=1).mean(axis=1)
    return {'composite': avg(comp), 'inventories': avg(inv), 'prices': avg(prc),
            'philly_current': f('GACDFSA066MSFRBPHI'), 'philly_future': f('GAFDFSA066MSFRBPHI'),
            'empire': f('GACDISA066MSFRBNY'), 'dallas': f('BACTSAMFRBDAL'), 'michigan': f('UMCSENT')}


# ----------------------------------------------------------------------------------- indicator panel
def bls(cx, sid, start=None):
    """A BLS series FRED does not carry, from BLS's flat file (gdpnow/bls_flat.py; first year per config/bls_series.toml).
    Indexed by month end like the other monthly series here."""
    s = BF.direct(cx.con, cx.asof, sid)
    return s.set_axis(s.index + pd.offsets.MonthEnd(0))


def treasury_defense_outlays(cx):
    """Monthly Treasury Statement: Department of Defense - Military Programs outlays (Treasury Fiscal Data
    API, table MTS 5). Returns monthly $mil (NSA)."""
    s = P._archived(cx.con, 'treasury', 'MTS5_DOD', cx.asof)
    if s is not None:
        return s
    url = ('https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_5'
           '?filter=classification_desc:eq:Total--Department of Defense--Military Programs'
           '&fields=record_date,current_month_gross_outly_amt&page[size]=1000&sort=record_date')
    rows = P._get(url.replace(' ', '%20'))['data']
    s = pd.Series({pd.Timestamp(r['record_date']) + pd.offsets.MonthEnd(0): float(r['current_month_gross_outly_amt']) / 1e6
                   for r in rows if r['current_month_gross_outly_amt'] not in (None, 'null')}).sort_index()
    P._archive(cx.con, 'treasury', 'MTS5_DOD', cx.asof, s)
    return s


def enduse(cx, flow, code):
    """Census-basis end-use trade, seasonally adjusted by BEA (IDS-0182, 1999+; gdpnow/ids0182.py)."""
    return IDS.series(cx, flow, code)


def last_month_like(s, ref):
    """Cut a current-vintage series at the last month of a reference series from the ALFRED vintage."""
    return s.loc[:ref.dropna().index.max()]


def build_indicators(cx, prices, nipa_q, inv):
    f, b = cx.fred, cx.bea
    cpi = prices['PCU_USECONfr']
    cap = f('WPSFD41312')
    L = {}
    # Direct FRED series (validated mappings in config/public_series.toml [fred]).
    direct = {v: k for k, v in MAP['fred'].items()}
    for tick in ['LANAGRA@USECON', 'LAPRIVA@USECON', 'LAGOODA@USECON', 'LAMANUA@USECON', 'LADURGA@USECON',
                 'LANDURA@USECON', 'LAPSRVA@USECON', 'LARTRDA@USECON', 'LAWTRDA@USECON', 'LACONSA@USECON',
                 'LAFIREA@USECON', 'LAMINGA@USECON', 'LALEIHA@USECON', 'LASRVOA@USECON', 'LAPBSVA@USECON',
                 'LAEDUHA@USECON', 'LAFGOVA@USECON', 'LAP15A@USECON', 'LAPTSVA@USECON', 'LAD61A@USECON',
                 'LRPRIVA@USECON', 'LRMANUA@USECON', 'LOMANUA@USECON', 'LE@USECON', 'LENA@USECON', 'LRM25@USECON',
                 'LUMD@USECON', 'IP@IP', 'IPMFG@IP', 'CUMFG@IP', 'CUT@IP', 'IPTP@IP', 'IP54@IP', 'IP53@IP', 'IPFP@IP',
                 'IP521@IP', 'IP51@IP', 'IP511@IP', 'IP512@IP', 'IPB31@IP', 'YPLTPMH@USECON', 'YPDHM@USECON',
                 'CQM@USNA', 'CDQM@USNA', 'CNQM@USNA', 'CSQM@USNA', 'HST@USECON', 'HST1@USECON', 'HSTNE@USECON',
                 'HSTMW@USECON', 'HSTS@USECON', 'HSTW@USECON', 'HPT@USECON', 'HN1US@USECON', 'HN1SUS@USECON',
                 'HN1MT@USECON', 'ADS@USECON', 'ASTOT@USECON', 'TLTSAR@USECON', 'TSHSU@USNA']:
        L[tick] = f(direct[tick])
    L['HSTM@USECON'] = f('HOUST') - f('HOUST1F')
    L['YPWGM@USNA'] = f('B202RC1') / cpi
    L['CPG@USECON'] = f('TLPBLCONS') / cpi
    # Federal and state & local construction: Census historical tables by owner (1993+).
    L['CPGF@USECON'] = census_construction(cx, 'fedsatime') / prices['TornPriceNonResStrMthfr']      # deflator verified: implied corr 1.000
    L['CPGS@USECON'] = census_construction(cx, 'slsatime') / prices['TornPriceNonResStrMthfr']
    L['CPVD@USECON'] = f('PNRESCONS') / prices['TornPriceNonResStrMthfr']
    L['NMS@USECON'] = advance_overlay(f('AMTMVS'), asof_cut(cx.census('x', 'm3', 'MTM', 'VS'), cx.asof)) / prices['SpliceManTradeDeflatorfr']
    L['NRST@USECON'] = f('RSAFS') / prices['SpliceRetailTradeDeflatorfr']
    L['NWSH@USECON'] = f('WHLSLRSMSA') / prices['SpliceWholesaleTradeDeflatorfr']
    L['ManInvShipRatio'] = (advance_overlay(f('AMTMTI'), asof_cut(cx.census('x', 'm3', 'MTM', 'TI'), cx.asof)) /
                            advance_overlay(f('AMTMVS'), asof_cut(cx.census('x', 'm3', 'MTM', 'VS'), cx.asof)))
    L['WholeSaleInvSalesRatio'] = f('WHLSLRIRSA')
    L['RetailInvSalesRatio'] = inv['NRIXM_USECON'] / inv['NRSXM_USECON']
    # Labour-market constructions.
    L['URUnround'] = 100 * f('UNEMPLOY') / f('CLF16OV')
    L['LFPRUnround'] = 100 * f('CLF16OV') / f('CNP16OV')
    L['LoserOnLayoff'] = 100 * f('LNS13023653') / f('CLF16OV')
    ic = f('IC4WSA')
    L['WeeklyClaims'] = ic.groupby(ic.index.to_period('M')).last().pipe(lambda s: s.set_axis(s.index.to_timestamp(how='end').normalize()))
    L['StateLocalEmp'] = f('CES9092000001') + f('CES9093000001')
    payroll = f('PAYEMS')
    # Detailed CES series (DoD civilian; production employees) publish one month after headline payrolls.
    lag1 = payroll.iloc[:-1]
    L['LAFGDA@LABOR'] = last_month_like(bls(cx, 'CES9091911001'), lag1)
    L['LPD61DA@LABOR'] = last_month_like(bls(cx, 'CES2023611806'), lag1)
    # Autos and trucks (NIPA underlying detail 7.2.5S).
    u7 = lambda n: b('U70205S', line=n)
    L['ASCPU@USNA'] = u7(11)
    bus = 100 * (1 - consumer_truck_share(cx))
    flat = pd.Series(bus.dropna().iloc[:12].mean(), index=pd.date_range('1976-01-31', bus.dropna().index[0], freq='ME'))
    L['BusShareTrucks'] = cx.splice('BusShareTrucks', bus, [('constant_share', flat)], 'MonthlyLevels', kind='diff')
    # Housing constructions.
    price_new = prices['SA_HN1PA_USECON_fr']
    L['NomSingleStarts'] = f('HOUST1F') * price_new / prices['CCIHD_USECONfr']
    L['valNewHomeSales'] = f('HSN1F') * price_new / prices['R5312101_PPIRInterpfr']
    # Existing-home sales (NAR): FRED serves only the last 13 months (licensed data), so the live layer is short and
    # the durable growth store / workbook reference layer supply the history (gdpnow/history.py). The workbook's
    # series is unit sales deflated by the real-estate PPI (growth corr 0.99); the level constant is the
    # trailing-12-month average median price, so existing and new sales carry comparable weights.
    ex_units, ex_price = f('EXHOSLUSM495S'), f('HOSMEDUSM052N')
    ex_const = ex_price.iloc[-12:].mean()          # $; new-home sales below are thousands of units x $
    L['valExHomeSales'] = cx.splice('valExHomeSales', ex_units / 1e3 * ex_const / prices['R5312101_PPIRInterpfr'].reindex(ex_units.index),
                                    [], 'MonthlyLevels', kind='log')
    # New single-family + multifamily construction (permanent-site), deflated by the residential structures price.
    L['SplicedNewHousingConstruction'] = (census_construction(cx, 'privsatime', 3) + census_construction(cx, 'privsatime', 4)) / prices['CCIHD_USECONfr']
    # Improvements deflator (WP Table A5b note): geometric mean of the CCIHD house price, the PPI for net inputs
    # to residential maintenance and repair and an Atkeson-Ohanian extrapolation of the construction ECI. The PPI
    # series was discontinued in Dec-2014; from 2015 the workbook's deflator is the mean of the other two
    # (verified: exact, error 0.000 pp every month 2015-2026), so the PPI term is cut at 2014-12.
    eci = f('ECICONCOM')
    eci.index = eci.index.to_period('Q').end_time.normalize()
    eci_m = atkeson_ohanian(eci, prices.index)
    inputs = f('WPUIP2321001').loc[:'2014-12-31']
    parts = pd.concat([np.log(prices['CCIHD_USECONfr']), np.log(inputs), np.log(eci_m)], axis=1)
    gpart = parts.diff().mean(axis=1, skipna=True).where(parts.iloc[:, 0].diff().notna() & parts.iloc[:, 2].diff().notna())
    improv = np.exp(gpart.dropna().cumsum())          # chained growth: no level break when the PPI term ends
    L['SplicedBuildingMaterials'] = f('RSBMGESD') / improv
    L['RetSalesResEquip'] = (f('RSFHFS') + f('RSEAS')) / prices['CPIMajappSplicefr']
    # Manufactured-home shipments value: units x seasonally adjusted average sales price. The Census price series
    # ends a few months before the shipments, so months after it use the trailing-12-month growth rate
    # (Atkeson-Ohanian style). The workbook's series is reproduced (growth corr 0.993) WITHOUT a PPI deflator;
    # the PPI for mobile homes I tried (WPU1553) lowers the match, so none is applied.
    units = f('SHTSAUS')
    mh_price = seasadj(f('SPTNSAUS'), '2014').dropna()
    for t in pd.date_range(mh_price.index[-1] + pd.offsets.MonthEnd(1), units.index[-1], freq='ME'):
        mh_price[t] = mh_price.iloc[-1] * np.exp(np.log(mh_price.iloc[-1] / mh_price.iloc[-13]) / 12)
    mh = units * 12 * mh_price.reindex(units.index)
    L['MobileHomeVal'] = cx.splice('MobileHomeVal', mh, [('shipments_units', units)], 'MonthlyLevels')   # units only before 2014 (no public price history)
    L['HSM@USECON'] = f('SHTSAUS') * 12 / 1000
    # Treasury outlays (X-13 adjusted, CPI-deflated).
    L['saFTO@USECON'] = seasadj(f('MTSO133FMS'), '1990') / cpi
    L['saFTOD@USECON'] = seasadj(treasury_defense_outlays(cx), '2000') / cpi
    # Manufacturers' shipments and orders (M3 / advance M3).
    adv = lambda c, d: asof_cut(cx.census('x', 'advm3', c, d), cx.asof)
    nxa = advance_overlay(f('ANXAVS'), adv('NXA', 'VS'))
    comp_ship = f('ACRPVS')
    comp_defl = atkeson_ohanian(b('U50504', 'Q', line=4), prices.index)   # computers price index
    L['CoreCapGoodsShipments'] = (nxa - comp_ship) / cap
    L['RealCapitalShipments'] = nxa / cap
    L['DefenseShipments'] = advance_overlay(f('ADEFVS'), adv('DEF', 'VS')) / cpi
    L['SplicedDurableGoodsOrders'] = advance_overlay(f('DGORDER'), adv('MDM', 'NO')) / cap
    L['SplicedComputersShipments'] = comp_ship / comp_defl
    anap = advance_overlay(f('ANAPVS'), adv('NAP', 'VS'))
    ship = {'air': anap, 'comp': comp_ship, 'core': nxa - comp_ship - anap}
    trade = build_trade(cx, prices, comp_defl, cap, ship)
    L.update(trade)
    aircraft_ppi = seasadj(f('PCU336411336411'), '1990')
    net_air = (anap - trade['_air_x'] + trade['_air_m'])
    L['NondefenseAircraftNetShipments'] = net_air / aircraft_ppi
    contrib = {'NetExportsGoodsMonthlyContrib': L.pop('_contrib_Goods'), 'NetSvcExportsMonthlyContrib': L.pop('_contrib_Svc')}
    for k in [k for k in L if k.startswith('_')]:
        del L[k]
    # Philly Fed survey levels (BOFGX future activity; BOISM current activity on ISM-style scale).
    L['BOFGX@SURVEYS'] = f('GAFDFSA066MSFRBPHI')
    L['BOISM@SURVEYS'] = 50 + f('GACDFSA066MSFRBPHI') / 2
    levels = cx.long_history(pd.DataFrame({k: me(v) for k, v in L.items()}).sort_index(), 'MonthlyLevels')
    return levels, pd.DataFrame({k: me(v) for k, v in contrib.items()})


def consumer_truck_share(cx):
    """Consumer share of light-truck unit sales: NIPA underlying detail 7.2.5S line 20 (thousands, SAAR) over
    total light-truck sales (domestic + imports, millions SAAR; lines 17 + 18)."""
    t = cx.bea('U70205S')
    ln = lambda n: t[[c for c in t.columns if c.split('|')[0] == str(n)][0]]
    return ln(20) / 1000 / (ln(17) + ln(18))



def census_construction(cx, table, column=1):
    """Census construction put in place, SA annual rate, $ millions, from the public historical tables
    (fedsatime, slsatime, privsatime; 1993+). Needed because the EITS API has no owner split or
    single/multifamily detail. `column` is the position in the table (1 = total). Archived in raw_pulls."""
    key = ('constr', table, column)
    if key in cx.cache:
        return cx.cache[key]
    s = P._archived(cx.con, 'census_hist', f'construction_{table}_{column}', cx.asof)
    if s is None:
        import io
        d = pd.read_excel(io.BytesIO(P.get_bytes(f'https://www.census.gov/construction/c30/xlsx/{table}.xlsx')), header=None, skiprows=4,
                          usecols=[0, column])
        d.columns = ['d', 'v']
        d['d'] = pd.to_datetime(d.d.astype(str).str.replace(r'[pr]$', '', regex=True), format='%b-%y', errors='coerce') + pd.offsets.MonthEnd(0)
        s = pd.to_numeric(d.dropna(subset=['d']).set_index('d').v, errors='coerce').dropna().sort_index()
        P._archive(cx.con, 'census_hist', f'construction_{table}_{column}', cx.asof, s)
    cx.cache[key] = asof_cut(s, cx.asof)
    return cx.cache[key]


def build_trade(cx, prices, comp_defl, cap, ship=None):
    """Real goods and services trade (WP Table A4): BOP goods (less BOP-basis nonmonetary gold, Mods
    Mar-2025) and services, the latest month from the AEI report and gold/capital-goods BVARs; end-use detail for
    computers, core capital goods and aircraft (BEA IDS-0182, seasonally adjusted by BEA)."""
    f = cx.fred
    gx, gm = f('BOPGEXP'), f('BOPGIMP')
    gold_x, gold_m = IDS.series(cx, 'exports', 'NMGLD', 'BP-based'), IDS.series(cx, 'imports', 'NMGLD', 'BP-based')
    gx = gx.sub(gold_x.reindex(gx.index).fillna(0))
    gm = gm.sub(gold_m.reindex(gm.index).fillna(0))
    # AEI window: the month after the last full report is known only from the advance report. The Census-basis
    # ex-gold growth (gold BVAR, registry P15) extrapolates the BOP measure ex gold.
    adv = asof_cut(cx.census('x', 'ftdadv', 'CBG', 'EXP'), cx.asof)
    t_aei = adv.index.max()
    aei = None
    if t_aei > gx.index.max() and ship is not None:
        aei = TB.aei_table(cx, t_aei)
        gg = TB.gold_adjusted_growth(cx, prices, aei, t_aei)
        gx[t_aei] = gx[t_aei - pd.offsets.MonthEnd(1)] * gg['exports']
        gm[t_aei] = gm[t_aei - pd.offsets.MonthEnd(1)] * gg['imports']
    out = {'SplicedGoodsExports': gx / prices['PXEA_USECONsplicefr'], 'SplicedGoodsImports': gm / prices['PMEA_USECONsplicefr'],
           'SplicedServiceExports': f('BOPSEXP') / prices['ExpSvcDefmthA1fr'],
           'SplicedServiceImports': f('BOPSIMP') / prices['ImpSvcDefmthA1fr']}
    eu = lambda flow, codes: pd.concat([enduse(cx, flow, c) for c in codes], axis=1).sum(axis=1, min_count=1)
    comp_x, comp_m = eu('exports', ['21300', '21301']), eu('imports', ['21300', '21301'])
    noncore = ['21300', '21301', '22000', '22010', '22020', '22220', '21320', '21100', '20005']
    core_x = enduse(cx, 'exports', '2') - eu('exports', noncore)
    core_m = enduse(cx, 'imports', '2') - eu('imports', noncore)
    # Histories before 1999 (start of IDS-0182) come from the durable growth store / workbook reference layer.
    sp = lambda name, live, prox=(), kind=None: cx.splice(name, live, prox, 'MonthlyLevels', name, kind)
    out['SplicedExportsComputersAndRelated'] = sp('SplicedExportsComputersAndRelated', comp_x / comp_defl)
    out['SplicedImportsComputersAndRelated'] = sp('SplicedImportsComputersAndRelated', comp_m / comp_defl)
    out['CoreCapGoodsExports'] = sp('CoreCapGoodsExports', core_x / cap)
    out['CoreCapGoodsImports'] = sp('CoreCapGoodsImports', core_m / cap)
    out['_air_x'] = sp('AircraftExportsNominal', eu('exports', ['22000', '22010', '22020']))
    out['_air_m'] = sp('AircraftImportsNominal', eu('imports', ['22000', '22010', '22020']))
    if aei is not None:
        # Capital-goods-shares BVAR (registry P16): month-t category trade after the advance reports.
        cgt = TB.capital_goods_t(cx, ship, aei, t_aei)
        add = lambda ser, v: pd.concat([ser.dropna().loc[:t_aei - pd.offsets.MonthEnd(1)], pd.Series({t_aei: v})])
        out['SplicedExportsComputersAndRelated'] = add(out['SplicedExportsComputersAndRelated'], cgt[('exports', 'comp')] / comp_defl[t_aei])
        out['SplicedImportsComputersAndRelated'] = add(out['SplicedImportsComputersAndRelated'], cgt[('imports', 'comp')] / comp_defl[t_aei])
        out['CoreCapGoodsExports'] = add(out['CoreCapGoodsExports'], cgt[('exports', 'core')] / cap[t_aei])
        out['CoreCapGoodsImports'] = add(out['CoreCapGoodsImports'], cgt[('imports', 'core')] / cap[t_aei])
        out['_air_x'] = add(out['_air_x'], cgt[('exports', 'air')])
        out['_air_m'] = add(out['_air_m'], cgt[('imports', 'air')])
    nomgdp = prices['MGDPN_USECONsplicefr']
    for kind, (x, m, nx, nm) in {'Goods': (out['SplicedGoodsExports'], out['SplicedGoodsImports'], gx, gm),
                                 'Svc': (out['SplicedServiceExports'], out['SplicedServiceImports'], f('BOPSEXP'), f('BOPSIMP'))}.items():
        wx, wm = (12 / 1000 * nx / nomgdp).shift(1), (12 / 1000 * nm / nomgdp).shift(1)
        c = wx * 1200 * np.log(x / x.shift(1)) - wm * 1200 * np.log(m / m.shift(1))
        out[f'_contrib_{kind}'] = c
    return out
