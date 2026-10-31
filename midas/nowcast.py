"""Real-time Ridge-MIDAS nowcast of US real GDP growth (advance estimate), refit at every forecast origin.

For each origin date O (4 per quarter) and target quarter q:
  1. Load the ALFRED vintage of every predictor *as of O* (the ragged edge is whatever had been published).
  2. Per series s: lag_s = months between O's month and the last complete month published for s.
  3. Regress GDP growth on  [ Legendre(2)-compressed last-6-months windows of each series, ending lag_s months
     before the same relative origin in each past quarter ] + lagged GDP growth, by ridge (LOO-CV penalty).
     -> every historical quarter 'looked like' the target quarter looks at O (same publication pattern).
  4. Predict q.  Same pipeline with the 3-variable set (INDPRO, NFCI, 10y-3m) = Chan's tutorial information set,
     plus an AR(2) benchmark, on the identical vintage and training window.
"""
import os, sys, warnings, concurrent.futures as cf
import numpy as np, pandas as pd
from common import *

warnings.filterwarnings('ignore')
K, DEG = 6, 2                       # monthly lags per series; Legendre degree (DEG+1 weights-parameters per series)
TRAIN_START = pd.Period('1992Q3', 'Q')
LAMS = np.logspace(-1, 4, 60)
SPEC = os.environ.get('MIDAS_SPEC', 'nominal')   # nominal | real (flows deflated by CPI); suffix _w = winsorise standardised predictors at +-3 SD
WINS = 3.0 if SPEC.endswith('_w') else None
DEFLATE = ['RSAFS', 'PCE', 'DGORDER', 'BUSINV']
A_H = {1: 0, 2: 1, 3: 2, 4: 3}      # origin month offset from the quarter's first month
COVID_Q = {pd.Period(f'{y}Q{q}', 'Q') for y, q in COVID}


def legendre_basis(k=K, deg=DEG):
    u = 2 * np.arange(k) / (k - 1) - 1
    B = np.polynomial.legendre.legvander(u, deg)
    Q, _ = np.linalg.qr(B)
    return Q * np.sign(Q[0] if False else np.sign(np.diag(Q.T @ B)))   # keep orientation of each polynomial


BASIS = legendre_basis()


def read_vint(sid, d):
    f = DATA / 'vint' / sid / f'{d:%Y%m%d}.csv'
    if not f.exists():                          # weekly series before their ALFRED history: current data, cut at origin
        f = DATA / 'vint' / f'{sid}.csv'
    x = pd.read_csv(f, na_values=['.', ''])
    x.columns = ['date', 'v']
    x['date'] = pd.to_datetime(x['date'])
    return x.set_index('date')['v'].dropna()


def monthly(sid, raw, origin):
    """-> (transformed monthly series on PeriodIndex M, last complete month)"""
    freq, tr = SERIES[sid][0], SERIES[sid][1]
    raw = raw[raw.index <= pd.Timestamp(origin)]
    if freq == 'M':
        x = raw.copy(); x.index = x.index.to_period('M'); last = x.index[-1]
        # permanent holes (e.g. Oct-2025 UNRATE/CPI, never collected during the shutdown): interpolate the level
        x = x.reindex(pd.period_range(x.index[0], last, freq='M')).interpolate(limit=3)
    else:
        tol = 6 if freq == 'W' else 3
        x = raw.groupby(raw.index.to_period('M')).mean()
        lastobs = raw.index[-1]
        last = lastobs.to_period('M')
        if lastobs < (lastobs + pd.offsets.MonthEnd(0)) - pd.Timedelta(days=tol):
            last = last - 1                       # latest month still incomplete -> not usable
        x = x[x.index <= last]
    if tr == 'dlog': x = 100 * np.log(x).diff()
    elif tr == 'diff': x = x.diff()
    elif tr == 'diffk': x = x.diff() / 1000
    return x, last


def gdp_growth(origin):
    g = read_vint('GDPC1', origin)
    g.index = g.index.to_period('Q')
    return 100 * ((g / g.shift(1)) ** 4 - 1)


_spread = None
def spread_raw():
    global _spread
    if _spread is None:
        x = pd.read_csv(DATA / 'vint' / 'T10Y3M.csv', na_values=['.', ''])
        x.columns = ['date', 'v']; x['date'] = pd.to_datetime(x['date'])
        _spread = x.set_index('date')['v'].dropna()
    return _spread


def ridge_fit_predict(X, y, xn):
    mu, sd = X.mean(0), X.std(0) + 1e-12
    Z, zn = (X - mu) / sd, (xn - mu) / sd
    if WINS: Z, zn = np.clip(Z, -WINS, WINS), np.clip(zn, -WINS, WINS)
    ym = y.mean()
    U, s, Vt = np.linalg.svd(Z, full_matrices=False)
    uy = U.T @ (y - ym)
    best = None
    for lam in LAMS:
        f = s ** 2 / (s ** 2 + lam)
        fit = U @ (f * uy)
        H = (U ** 2) @ f                                      # leverage (centered => intercept absorbed; +1/n)
        press = np.mean(((y - ym - fit) / (1 - H - 1 / len(y))) ** 2)
        if best is None or press < best[0]:
            best = (press, lam)
    lam = best[1]
    beta = Vt.T @ ((s / (s ** 2 + lam)) * uy)
    return ym + zn @ beta, beta, zn, lam


def nowcast_origin(args):
    y_, q_, h, origin = args
    q = pd.Period(f'{y_}Q{q_}', 'Q')
    # ---- data as of origin
    g = gdp_growth(origin)
    gl = g.index[-1]
    lagY = int((q - gl).n)                               # 1 normally; 2 if previous quarter's advance not out yet
    mon = {}
    for sid in VINTAGE_IDS:
        mon[sid] = monthly(sid, read_vint(sid, origin), origin)
    mon['T10Y3M'] = monthly('T10Y3M', spread_raw(), origin)
    if SPEC.startswith('real'):                       # nominal flows -> real: subtract CPI inflation (same vintage)
        cpi, cpi_last = mon['CPIAUCSL']
        for s in DEFLATE:
            x, last = mon[s]
            last = min(last, cpi_last)
            mon[s] = ((x - cpi.reindex(x.index)).loc[:last], last)
    omon = pd.Period(origin, 'M') if h < 4 else pd.Period(origin, 'M')   # month of the origin date
    lag = {s: int((omon - mon[s][1]).n) for s in mon}
    # ---- training quarters
    taus = [t for t in pd.period_range(TRAIN_START, gl, freq='Q') if t not in COVID_Q]
    out = {'y': y_, 'q': q_, 'h': h, 'origin': origin, 'lagY': lagY}

    def feats(ids, tau):
        m1 = pd.Period(year=tau.year, month=3 * (tau.quarter - 1) + 1, freq='M')
        f = []
        for s in ids:
            end = m1 + A_H[h] - lag[s]
            idx = [end - k for k in range(K)]
            w = mon[s][0].reindex(idx).values
            if np.isnan(w).any(): return None
            f.extend(w @ BASIS)
        return f

    def design(ids):
        X, Y = [], []
        for t in taus:
            yl = g.get(t - lagY, np.nan)
            f = feats(ids, t)
            if f is None or np.isnan(yl) or np.isnan(g.get(t, np.nan)): continue
            X.append(f + [yl]); Y.append(g[t])
        xn = feats(ids, q)
        return np.array(X), np.array(Y), np.array(xn + [g.get(q - lagY)])

    for name, ids in (('full', list(mon)), ('trio', TRIO)):
        X, Y, xn = design(ids)
        out[f'n_{name}'] = len(Y)
        pr, beta, zn, lam = ridge_fit_predict(X, Y, xn)
        out[name] = float(pr); out[f'lam_{name}'] = float(lam)
        if name == 'full':               # contribution of each predictor group to (pred - mean)
            nb = DEG + 1
            for i, s in enumerate(ids):
                out[f'c_{s}'] = float(zn[i * nb:(i + 1) * nb] @ beta[i * nb:(i + 1) * nb])
            out['c_gdp_lag'] = float(zn[-1] * beta[-1])
            out['lag_' + 'max'] = max(lag.values())
    # ---- AR(2) benchmark (same training quarters, OLS)
    Xa, Ya = [], []
    for t in taus:
        a, b = g.get(t - lagY, np.nan), g.get(t - lagY - 1, np.nan)
        if np.isnan(a) or np.isnan(b) or np.isnan(g.get(t, np.nan)): continue
        Xa.append([1, a, b]); Ya.append(g[t])
    Xa, Ya = np.array(Xa), np.array(Ya)
    co = np.linalg.lstsq(Xa, Ya, rcond=None)[0]
    out['ar2'] = float(co @ [1, g.get(q - lagY), g.get(q - lagY - 1)])
    out['mean'] = float(Ya.mean())
    return out


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    jobs = origins()
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        jobs = [j for j in jobs if (j[0], j[1]) in ((2008, 3), (2019, 4))]
    with cf.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(nowcast_origin, jobs))
    df = pd.DataFrame(res)
    T = pd.read_csv(OUT / 'targets.csv', index_col='q')
    df['qp'] = [f'{a}Q{b}' for a, b in zip(df.y, df.q)]
    df['actual'] = df['qp'].map(T['adv'])
    A = pd.read_csv(OUT / 'gdpnow_archive.csv', parse_dates=['date'])
    A['qp'] = A['q'].astype(str)
    gn = []
    for r in df.itertuples():
        s = A[(A.qp == r.qp) & (A.date <= pd.Timestamp(r.origin))]
        gn.append(s['gdpnow'].iloc[-1] if len(s) else np.nan)
    df['gdpnow'] = gn
    df.to_csv(OUT / f'forecasts_{SPEC}.csv', index=False)
    print(df[['qp', 'h', 'origin', 'actual', 'full', 'trio', 'ar2', 'gdpnow', 'n_full', 'lam_full']].round(2).to_string())
