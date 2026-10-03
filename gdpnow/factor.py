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


def estimate(panel, forecast_to, n_lags=3):
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

    # AR(n_lags) forecasts beyond the last month with data.
    future = pd.date_range(last_data + pd.offsets.MonthEnd(1), forecast_to, freq='ME')
    vals = list(f.to_numpy()[-n_lags:])
    for _ in future:
        vals.append(float(np.dot(rho, vals[::-1][:n_lags])))
    f = pd.concat([f, pd.Series(vals[n_lags:], index=future)])
    return FactorResult(f, last_data, rho, sigma_u, gamma, phi, sig_v, mean, sd)
