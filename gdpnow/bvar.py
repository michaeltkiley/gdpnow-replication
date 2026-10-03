"""Bayesian VARs with the Banbura-Giannone-Reichlin (2010) dummy-observation prior (registry P06-P08, P19;
Higgins 2014 appendix).

Minnesota prior: own first lag centred on delta_i (1 = random walk for log levels, 0 = white noise otherwise),
other coefficients on zero, tightness lambda, lag decay 1/l; sum-of-coefficients prior with tau = 10*lambda;
diffuse constant. Posterior mean via OLS on data augmented with dummy observations.
"""
import numpy as np


def ar_sigma(y, p):
    """Residual sd of a univariate AR(p) with constant, used to scale the prior."""
    Y = y[p:]
    X = np.column_stack([np.ones(len(Y))] + [y[p - k:-k] for k in range(1, p + 1)])
    b, *_ = np.linalg.lstsq(X, Y, rcond=None)
    e = Y - X @ b
    return np.sqrt(e @ e / (len(Y) - X.shape[1]))


def lagmat(Y, p):
    T = len(Y)
    X = np.column_stack([Y[p - k:T - k] for k in range(1, p + 1)] + [np.ones((T - p, 1))])
    return Y[p:], X


def dummies(Y, p, lam, delta, sum_coef=True, eps=1e-5):
    n = Y.shape[1]
    sig = np.array([ar_sigma(Y[:, i], p) for i in range(n)])
    rows_y, rows_x = [], []
    # Minnesota: first-lag means and lag decay
    yd = np.zeros((n * p, n)); xd = np.zeros((n * p, n * p + 1))
    yd[:n] = np.diag(delta * sig) / lam
    for l in range(1, p + 1):
        xd[(l - 1) * n:l * n, (l - 1) * n:l * n] = np.diag(sig) * l / lam
    rows_y.append(yd); rows_x.append(xd)
    # residual covariance prior
    rows_y.append(np.diag(sig)); rows_x.append(np.zeros((n, n * p + 1)))
    # diffuse constant
    xc = np.zeros((1, n * p + 1)); xc[0, -1] = eps
    rows_y.append(np.zeros((1, n))); rows_x.append(xc)
    if sum_coef:
        tau = 10 * lam
        mu = Y[:p].mean(axis=0)
        ys = np.diag(delta * mu) / tau
        xs = np.zeros((n, n * p + 1))
        for l in range(p):
            xs[:, l * n:(l + 1) * n] = np.diag(delta * mu) / tau
        rows_y.append(ys); rows_x.append(xs)
    return np.vstack(rows_y), np.vstack(rows_x)


def fit(Y, p, lam, delta, sum_coef=True):
    """Posterior mean coefficients B ((n*p+1) x n): [lag1 ... lagp, const]."""
    y, X = lagmat(Y, p)
    yd, xd = dummies(Y, p, lam, delta, sum_coef)
    Xs, Ys = np.vstack([X, xd]), np.vstack([y, yd])
    B, *_ = np.linalg.lstsq(Xs, Ys, rcond=None)
    return B


def one_step(Y, B, p):
    """In-sample one-step predictions for rows p..T-1 and the forecast for T."""
    y, X = lagmat(Y, p)
    fitted = X @ B
    x_next = np.concatenate([Y[-k] for k in range(1, p + 1)] + [[1.0]])
    return fitted, x_next @ B
