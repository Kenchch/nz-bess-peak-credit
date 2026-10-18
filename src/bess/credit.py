"""Two ways to compute how much of a battery's MW counts at the peak.

shave_peak   one peak window, the "lower a flat line" method (bisection)
min_top_k    a whole winter, chronological state of charge, minimise H100 (LP)

This module only sees numpy arrays; it knows nothing about EMI.
"""
import numpy as np
import scipy.sparse as sp
from scipy.optimize import linprog


def shave_peak(load, p_mw, e_mwh, dt_h):
    """MW by which a battery starting full can hold down one window's peak."""
    load = np.asarray(load, float)
    peak = load.max()

    def energy_above(line):
        return np.clip(load - line, 0, None).sum() * dt_h

    lo, hi = peak - p_mw, peak          # the line can never drop more than P
    if energy_above(lo) <= e_mwh:       # power binds before energy does
        return p_mw
    for _ in range(60):                 # energy binds: find where it runs out
        mid = (lo + hi) / 2
        if energy_above(mid) > e_mwh:
            lo = mid
        else:
            hi = mid
    return peak - hi


def min_top_k(load, in_window, p_mw, e_mwh, k, dt_h=0.5, eta=0.88, soc0=1.0):
    """Lowest achievable mean of the k highest in-window values of load - battery.

    Variables, in order: discharge d[n], charge c[n], state of charge s[n],
    excess u[m] for the m in-window periods, and one threshold tau.
    Sum of the k largest x equals min over tau of k*tau + sum(max(0, x - tau)),
    which turns the top-k objective into a linear one.
    """
    load = np.asarray(load, float)
    n = len(load)
    w = np.flatnonzero(in_window)
    m = len(w)
    ec = ed = np.sqrt(eta)              # split round-trip loss across charge/discharge
    iD, iC, iS, iU, iT = 0, n, 2 * n, 3 * n, 3 * n + m
    nv = 3 * n + m + 1

    cost = np.zeros(nv)
    cost[iU:iU + m] = 1.0
    cost[iT] = k

    # u_t >= L_t - d_t + c_t - tau   ->   -d_t + c_t - u_t - tau <= -L_t
    r = np.arange(m)
    A_ub = sp.coo_matrix(
        (np.r_[-np.ones(m), np.ones(m), -np.ones(m), -np.ones(m)],
         (np.r_[r, r, r, r], np.r_[iD + w, iC + w, iU + r, np.full(m, iT)])),
        shape=(m, nv)).tocsr()
    b_ub = -load[w]

    # s_t - s_{t-1} - dt*ec*c_t + dt/ed*d_t = 0, with s_{-1} = soc0 * E
    t = np.arange(n)
    A_eq = sp.coo_matrix(
        (np.r_[np.ones(n), -np.ones(n - 1), -dt_h * ec * np.ones(n), dt_h / ed * np.ones(n)],
         (np.r_[t, t[1:], t, t], np.r_[iS + t, iS + t[:-1], iC + t, iD + t])),
        shape=(n, nv)).tocsr()
    b_eq = np.zeros(n)
    b_eq[0] = soc0 * e_mwh

    bounds = [(0, p_mw)] * (2 * n) + [(0, e_mwh)] * n + [(0, None)] * m + [(None, None)]
    res = linprog(cost, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                  bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(res.message)
    battery = res.x[iD:iD + n] - res.x[iC:iC + n]
    return res.fun / k, battery


def top_k_mean(x, k):
    return np.sort(x)[-k:].mean()


def capacity_credit(load, in_window, p_mw, hours, k=200, **kw):
    before = top_k_mean(load[in_window], k)
    after, _ = min_top_k(load, in_window, p_mw, p_mw * hours, k, **kw)
    return (before - after) / p_mw
