"""Dynamic factor model (registry P01, T01; Higgins 2014 step 2b; Mods Apr-2018 eqs. 6-8, Apr-2020).

    f_t = rho1 f_{t-1} + rho2 f_{t-2} + rho3 f_{t-3} + u_t
    z_it = gamma_i f_t + e_it,   e_it = phi_i e_{i,t-1} + v_it

z_it are standardized stationary series with missing values. Estimation: (1) first principal component
with the Stock-Watson (2002, App. A) EM treatment of missing values; (2) OLS for rho, gamma, phi and the
variances; (3) Kalman filter and smoother with the idiosyncratic AR(1) terms in the state; (4) AR(3)
forecasts of f beyond the last month with data.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class FactorResult:
    factor: pd.Series        # smoothed through last data month, AR(3) forecast after
    last_data: pd.Timestamp
    rho: np.ndarray
    sigma_u: float
    gamma: pd.Series
    phi: pd.Series
    sigma_v: pd.Series
    mean: pd.Series          # standardization constants (T01)
    sd: pd.Series
    edge_weight: float = 1.0  # weight on the newest month's data against the AR prediction (see edge_weight)


def standardize(panel):
    mean, sd = panel.mean(), panel.std()
    return (panel - mean) / sd, mean, sd


def pca_em(z, tol=1e-8, max_iter=500):
    """First principal component of a panel with missing values (Stock-Watson 2002 App. A EM)."""
    mask = z.notna().to_numpy()
    x = z.fillna(0.0).to_numpy()
    f_old = None
    for _ in range(max_iter):
        u, s, vt = np.linalg.svd(x, full_matrices=False)
        f = u[:, 0] * s[0]
        lam = vt[0]
        fit = np.outer(f, lam)
        x = np.where(mask, z.to_numpy(), fit)
        if f_old is not None and np.max(np.abs(f - f_old)) < tol:
            break
        f_old = f
    return pd.Series(f / f.std(), index=z.index)


def _ols(y, X):
    ok = ~np.isnan(y) & ~np.isnan(X).any(axis=1)
    b, *_ = np.linalg.lstsq(X[ok], y[ok], rcond=None)
    resid = np.full_like(y, np.nan)
    resid[ok] = y[ok] - X[ok] @ b
    return b, resid


def edge_weight(panel, f_all, n_months=36, start='1990-01-31', min_months=18, full_share=0.9):
    """How much of the newest month's reading to believe when only some series report it.

    Pseudo real time with re-estimation: for each of the last `n_months` complete months t, estimate the model on data through t with month t
    reported only by the series that report the newest month now, and compare that month-t factor with the factor from all the data (`f_all`),
    both measured from the AR prediction of month t. The weight is the slope of the second on the first (through the origin, between 0 and 1).
    With few reporting series the filter overreacts (in the 2026-10 backtest 5 series earned 0.15, 30 earned 1.0), and the in-sample filter
    cannot show it because its loadings were fit on the same months. 1 when most of the usual series report."""
    last = panel.dropna(how='all').index.max()
    edge = panel.loc[last].notna().to_numpy()
    prior = panel.loc[:last].iloc[:-1].dropna(how='all')
    usual = prior.iloc[-1].notna().sum() if len(prior) else 0
    if usual == 0 or edge.sum() >= full_share * usual:
        return 1.0
    cols = panel.columns[edge]
    num = den = 0.0
    n = 0
    for t in prior.index[-n_months:]:
        p = panel.loc[start:t].copy()
        others = [c for c in p.columns if c not in cols]
        p.loc[t, others] = np.nan
        if p.loc[t].notna().sum() == 0:
            continue
        try:
            r = estimate(p, t, shrink=False)
        except Exception:
            continue
        if t not in r.factor.index or t not in f_all.index or len(r.factor) < 5:
            continue
        pred = float(np.dot(r.rho, r.factor.iloc[[-2, -3, -4]].to_numpy()))
        x, y = r.factor.iloc[-1] - pred, f_all[t] - pred
        num, den, n = num + x * y, den + x * x, n + 1
    return float(np.clip(num / den, 0.0, 1.0)) if n >= min_months and den > 0 else 1.0


def estimate(panel, forecast_to, n_lags=3, shrink=True):
    """panel: DataFrame (month-end index) of stationary series; NaN where unreleased.
    Returns FactorResult with the factor extended to `forecast_to`."""
    panel = panel.dropna(axis=1, how="all")
    z, mean, sd = standardize(panel)
    last_data = z.dropna(how='all').index.max()
    z = z.loc[:last_data]
    f0 = pca_em(z)

    # OLS: factor AR(n_lags), loadings, idiosyncratic AR(1).
    F = np.column_stack([f0.shift(k).to_numpy() for k in range(1, n_lags + 1)])
    rho, u = _ols(f0.to_numpy(), F)
    sigma_u = np.nanstd(u, ddof=n_lags)
    gamma, phi, sig_v = {}, {}, {}
    for c in z:
        g, e = _ols(z[c].to_numpy(), f0.to_numpy()[:, None])
        p, v = _ols(e[1:], e[:-1, None])
        gamma[c], phi[c], sig_v[c] = g[0], p[0], np.nanstd(v, ddof=1)
    gamma, phi, sig_v = pd.Series(gamma), pd.Series(phi), pd.Series(sig_v)

    # State: [f_t, f_{t-1}, f_{t-2}, e_1t .. e_Nt]
    N, k = z.shape[1], n_lags + z.shape[1]
    A = np.zeros((k, k)); A[0, :n_lags] = rho; A[1:n_lags, :n_lags - 1] = np.eye(n_lags - 1)
    A[n_lags:, n_lags:] = np.diag(phi.to_numpy())
    Q = np.zeros((k, k)); Q[0, 0] = sigma_u ** 2; Q[n_lags:, n_lags:] = np.diag(sig_v.to_numpy() ** 2)
    H = np.zeros((N, k)); H[:, 0] = gamma.to_numpy(); H[:, n_lags:] = np.eye(N)
    R = 1e-8
    a = np.zeros(k)
    P = np.eye(k); P[:n_lags, :n_lags] *= 10.0
    P[n_lags:, n_lags:] = np.diag(sig_v.to_numpy() ** 2 / np.maximum(1 - phi.to_numpy() ** 2, 1e-3))
    Y = z.to_numpy()
    T = len(Y)
    a_f, P_f, a_p, P_p = np.zeros((T, k)), np.zeros((T, k, k)), np.zeros((T, k)), np.zeros((T, k, k))
    for t in range(T):
        a, P = A @ a, A @ P @ A.T + Q
        a_p[t], P_p[t] = a, P
        obs = ~np.isnan(Y[t])
        if obs.any():
            Ht = H[obs]
            S = Ht @ P @ Ht.T + R * np.eye(obs.sum())
            K = np.linalg.solve(S, Ht @ P).T
            a = a + K @ (Y[t, obs] - Ht @ a)
            P = P - K @ Ht @ P
        a_f[t], P_f[t] = a, P
    a_s = a_f.copy()
    for t in range(T - 2, -1, -1):          # Rauch-Tung-Striebel smoother
        J = np.linalg.solve(P_p[t + 1].T, (P_f[t] @ A.T).T).T
        a_s[t] = a_f[t] + J @ (a_s[t + 1] - a_p[t + 1])
    f = pd.Series(a_s[:, 0], index=z.index)
    # The newest month is often reported by few series. Shrink its factor toward the filter's prediction by the weight those series earn in
    # pseudo real time (edge_weight); the smoother already carried the month back to earlier ones.
    w_edge = edge_weight(panel.loc[:last_data], f) if shrink else 1.0
    f.iloc[-1] = a_p[-1][0] + w_edge * (f.iloc[-1] - a_p[-1][0])

    # AR(n_lags) forecasts beyond the last month with data.
    future = pd.date_range(last_data + pd.offsets.MonthEnd(1), forecast_to, freq='ME')
    vals = list(f.to_numpy()[-n_lags:])
    for _ in future:
        vals.append(float(np.dot(rho, vals[::-1][:n_lags])))
    f = pd.concat([f, pd.Series(vals[n_lags:], index=future)])
    return FactorResult(f, last_data, rho, sigma_u, gamma, phi, sig_v, mean, sd, w_edge)
