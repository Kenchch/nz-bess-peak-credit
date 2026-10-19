"""Two ways to compute how much of a battery's MW counts at the peak.

shave_peak              one peak window, the "lower a flat line" method (bisection)
min_top_k               a whole winter, chronological state of charge, minimise H100 (LP)
min_top_k_no_new_peaks  min_top_k with the rule that charging may not create a new peak

This module only sees numpy arrays; it knows nothing about EMI.
"""
import numpy as np
import scipy.sparse as sp
from scipy.optimize import linprog

TIDY_MW = 1e-4      # how far the tidy pass may let H100 drift above the optimum
PEAK_TOL_MW = 0.05  # off-window load this far above the H100 line counts as a new peak


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


def min_top_k(load, in_window, p_mw, e_mwh, k, dt_h=0.5, eta=0.88, soc0=1.0, detail=False,
              tidy=False, charge_cap=None, line_rows=None):
    """Lowest achievable mean of the k highest in-window values of load - battery.

    Variables, in order: discharge d[n], charge c[n], state of charge s[n],
    excess u[m] for the m in-window periods, and one threshold tau.
    Sum of the k largest x equals min over tau of k*tau + sum(max(0, x - tau)),
    which turns the top-k objective into a linear one. e_mwh is the energy the
    store holds; delivering it to the grid loses a factor sqrt(eta).

    Returns (H100 after, battery MW = discharge - charge). With detail=True a
    third item holds discharge, charge and soc separately, for the G5 checks.

    The objective ignores half-hours outside the window, so the solver is free
    to cycle the battery there (discharging at 3 a.m., say) without changing
    H100. tidy=True solves a second LP that keeps H100 within TIDY_MW of the
    optimum and minimises total charge + discharge, which gives the dispatch
    an operator would recognise. H100 is the first-stage value either way.

    charge_cap (per-half-hour MW, on top of P) and line_rows (indices t that
    must satisfy L_t - d_t + c_t <= tau) are how min_top_k_no_new_peaks
    states its rule; both stay in force in the tidy pass.
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
    if line_rows is not None and len(line_rows):
        # L_t - d_t + c_t <= tau   ->   -d_t + c_t - tau <= -L_t
        q = np.asarray(line_rows)
        j = np.arange(len(q))
        rows = sp.coo_matrix(
            (np.r_[-np.ones(len(q)), np.ones(len(q)), -np.ones(len(q))],
             (np.r_[j, j, j], np.r_[iD + q, iC + q, np.full(len(q), iT)])),
            shape=(len(q), nv)).tocsr()
        A_ub, b_ub = sp.vstack([A_ub, rows]).tocsr(), np.r_[b_ub, -load[q]]

    # s_t - s_{t-1} - dt*ec*c_t + dt/ed*d_t = 0, with s_{-1} = soc0 * E
    t = np.arange(n)
    A_eq = sp.coo_matrix(
        (np.r_[np.ones(n), -np.ones(n - 1), -dt_h * ec * np.ones(n), dt_h / ed * np.ones(n)],
         (np.r_[t, t[1:], t, t], np.r_[iS + t, iS + t[:-1], iC + t, iD + t])),
        shape=(n, nv)).tocsr()
    b_eq = np.zeros(n)
    b_eq[0] = soc0 * e_mwh

    charge_bounds = ([(0, p_mw)] * n if charge_cap is None
                     else [(0, float(c)) for c in np.clip(charge_cap, 0, p_mw)])
    bounds = [(0, p_mw)] * n + charge_bounds + [(0, e_mwh)] * n + [(0, None)] * m + [(None, None)]
    res = linprog(cost, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                  bounds=bounds, method="highs")
    if res.status != 0:
        raise RuntimeError(res.message)
    x = res.x
    if tidy:
        throughput = np.zeros(nv)
        throughput[iD:iD + 2 * n] = 1.0
        res2 = linprog(throughput, A_ub=sp.vstack([A_ub, sp.csr_matrix(cost)]).tocsr(),
                       b_ub=np.r_[b_ub, res.fun + k * TIDY_MW], A_eq=A_eq, b_eq=b_eq,
                       bounds=bounds, method="highs")
        if res2.status != 0:
            raise RuntimeError(f"tidy pass: {res2.message}")
        x = res2.x
    discharge, charge = x[iD:iD + n], x[iC:iC + n]
    if detail:
        return res.fun / k, discharge - charge, {
            "discharge": discharge, "charge": charge, "soc": x[iS:iS + n]}
    return res.fun / k, discharge - charge


def top_k_mean(x, k):
    return np.sort(x)[-k:].mean()


def kth_highest(x, k):
    """The H100 line: the k-th highest value, i.e. the lowest one still in the top k."""
    return np.sort(x)[-k]


def lifted_peaks(load, in_window, charge, battery, line, tol=PEAK_TOL_MW):
    """Off-window half-hours where charging leaves the load above the H100 line."""
    after = np.asarray(load, float) - battery
    off = ~np.asarray(in_window, bool)
    return int((off & (charge > 1e-6) & (after > line + tol)).sum())


def min_top_k_no_new_peaks(load, in_window, p_mw, e_mwh, k, tidy=False, max_passes=8, **kw):
    """min_top_k, with the rule that charging may not create a new peak outside the window.

    H100 only sees 07:00-22:00, so plain min_top_k may charge hard at 06:30 or
    22:00 and leave those half-hours above the H100 line it is credited for
    lowering. The rule, for every off-window half-hour t:

        L_t below the line    charging may lift it at most to the line:
                              L_t - d_t + c_t <= tau, a row inside the LP
        L_t at or above it    no charging at all (c_t = 0)

    Which half-hours sit below the line depends on where the line ends up, so
    the split is iterated to a fixed point: pass 1 is plain min_top_k, each
    later pass re-splits on the previous pass's line, and it stops when the
    split no longer changes (or after max_passes, keeping the last pass, which
    satisfies the rule for its own split either way). The tidy pass keeps the
    last split. Returns (H100, battery, detail, info); info has the uncapped
    H100, the final line, the number of passes and whether the split converged.
    """
    load = np.asarray(load, float)
    win = np.asarray(in_window, bool)
    off = ~win
    h, battery, det = min_top_k(load, win, p_mw, e_mwh, k, detail=True, **kw)
    h_uncapped, passes, converged = h, 1, False
    line = kth_highest((load - battery)[win], k)
    below = None
    while passes < max_passes:
        split = off & (load < line)
        if below is not None and np.array_equal(split, below):
            converged = True
            break
        below = split
        rule = {"charge_cap": np.where(win | below, p_mw, 0.0),
                "line_rows": np.flatnonzero(below)}
        h, battery, det = min_top_k(load, win, p_mw, e_mwh, k, detail=True, **rule, **kw)
        line = kth_highest((load - battery)[win], k)
        passes += 1
    if tidy:
        h, battery, det = min_top_k(load, win, p_mw, e_mwh, k, detail=True, tidy=True,
                                    **rule, **kw)
        line = kth_highest((load - battery)[win], k)
    if lifted_peaks(load, win, det["charge"], battery, line):
        raise RuntimeError("charging lifts off-window half-hours above the H100 line")
    return h, battery, det, {"h100_uncapped": h_uncapped, "line_mw": line, "passes": passes,
                             "converged": converged}


def capacity_credit(load, in_window, p_mw, hours, k=200, **kw):
    before = top_k_mean(load[in_window], k)
    after, _ = min_top_k(load, in_window, p_mw, p_mw * hours, k, **kw)
    return (before - after) / p_mw
