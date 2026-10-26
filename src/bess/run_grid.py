"""Day 3: the LP grid (architecture 3, 4.2, 4.3; gate G5).

For every winter x basis x window x P x d, plus the sensitivities at d = 2 h,
solve credit.min_top_k_no_new_peaks and record how far a battery fleet of
P MW and d hours lowers H100. Then run gate G5 and interpolate p*.

    load       demand before batteries: l_gross_mw + bess_mw (gross) or
               l_net_mw + bess_mw (net). Batteries already on the grid
               discharge at the peak, so without the add-back they would hide
               part of the peak a new fleet is meant to cover
               (config credit.add_back_bess).
    energy     a "d-hour" battery delivers P x d MWh to the grid
               (credit.rated_energy: delivered), so the store holds
               P x d / sqrt(eta); with "stored" it holds P x d.
    window     all = NI-WCM 07:00-22:00, AM = 07:00-12:00, PM = 16:00-22:00.
               H100 is the mean of the k highest half-hours inside the window;
               the battery charges and discharges on every half-hour of the
               winter, starting full on 1 April. AM and PM are diagnostics of
               peak shape, not SOSA measures.
    usable70   only 70% of the rated energy can be used, i.e. a 1.4 h battery
    solar3x    net basis with three times the observed solar output
    top50      H100 over the 50 highest half-hours instead of 200
    no new peaks  charging may not lift a half-hour outside the window above
               the H100 line (credit.min_top_k_no_new_peaks). cc_uncapped
               keeps the first-pass value so the effect is visible.

cc is the fall in H100 per MW. cc_availability, for the saved dispatch only,
scores that same dispatch the way SOSA scores wind: mean battery output over
the k highest in-window half-hours of the load before the battery, over P.

Gate G5, per LP: HiGHS reports optimal; replaying the dispatch gives the
LP's H100; state of charge follows its recursion and stays in [0, E]; power
stays in [0, P]; energy delivered <= sqrt(eta) * E + eta * energy charged; no
off-window half-hour that charges ends above the final H100 line. Per curve:
CC never rises with P and stays in [0, 1]. Per winter, basis, window and P:
CC never falls as duration grows (1 h <= usable70 <= 2 h <= 4 h).

Outputs in the run folder:
    credit_table.csv     one row per LP (contract 4.2, plus check columns)
    crossings.csv        p* where CC falls to SOSA's factor (contract 4.3)
    dispatch_d2.parquet  d = 2 h, base, window all: battery MW per half-hour,
                         from the tidy second pass (least charge + discharge
                         at the same H100)
    run_grid.log         every LP with its result and timing, and every check
"""
import logging
import os
import textwrap
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
import pandas as pd

from . import SERIES, GateError, load_config, logs
from .credit import kth_highest, lifted_peaks, min_top_k_no_new_peaks, top_k_mean

BASES = {"gross": "l_gross_mw", "net": "l_net_mw"}
WINDOWS = ("all", "AM", "PM")
DT_H = 0.5
TOL_CC = 1e-3          # CC = dH100 / P; HiGHS tolerances move H100 by ~1e-3 MW
TOL_MW = 1e-2          # replayed H100 against the LP objective
TOL_REL = 1e-6         # power and state-of-charge bounds, relative to P and E
DURATION_ORDER = [("base", 1), ("usable70", 2), ("base", 2), ("base", 4)]   # 1 h, 1.4 h, 2 h, 4 h

log = logging.getLogger("bess.run_grid")


def get_logger(out):
    """Log to out/run_grid.log, replacing earlier handlers; the console and run.log get the
    same records through the "bess" logger (logs.console, cli.guarded)."""
    close_logger()
    if out is not None:
        h = logging.FileHandler(out / "run_grid.log", encoding="utf-8")
        h.setFormatter(logs.FORMAT)
        log.addHandler(h)
    return log


def close_logger():
    for h in list(log.handlers):
        log.removeHandler(h)
        h.close()


# ------------------------------------------------------------------ inputs

def window_mask(w, window):
    if window == "all":
        return w.in_wcm_window.to_numpy(bool)
    return (w.period == window).to_numpy(bool) & w.in_wcm_window.to_numpy(bool)


def winter_load(w, basis, sensitivity, cfg):
    """Half-hourly load for one winter on one basis, before batteries."""
    load = w[BASES[basis]].to_numpy(float).copy()
    if cfg["credit"]["add_back_bess"]:
        load += w.bess_mw.to_numpy(float)
    if sensitivity == "solar3x":
        scale = cfg["sensitivities"]["solar3x"]["solar_scale"]
        load -= (scale - 1) * w.solar_mw.to_numpy(float)
    return load


def stored_energy(p_mw, d_h, frac, cfg):
    """MWh the store must hold for a P MW, d-hour battery (frac of it usable)."""
    rated = p_mw * d_h * frac
    if cfg["credit"].get("rated_energy", "delivered") == "delivered":
        return rated, rated / np.sqrt(cfg["eta_roundtrip"])
    return rated, rated


def plan(cfg, winters):
    """Every LP to solve, as (winter, basis, window, sensitivity, p_mw, d_h).

    Sensitivities run at d = 2 h; each may narrow basis and window."""
    g = cfg["grid"]
    jobs = [(wi, b, wn, "base", p, d) for wi in winters for b in g["basis"]
            for wn in g["window"] for p in g["p_mw"] for d in g["d_h"]]
    for sens, spec in cfg["sensitivities"].items():
        for wi in winters:
            for b in spec.get("basis", g["basis"]):
                for wn in spec.get("window", g["window"]):
                    for p in g["p_mw"]:
                        jobs.append((wi, b, wn, sens, p, 2))
    return jobs


def job_k(cfg, sensitivity):
    return cfg["sensitivities"].get(sensitivity, {}).get("k_top", cfg["k_top"])


# ------------------------------------------------------------------ one LP

def lp_checks(load, win, p, e, k, eta, h100_after, det):
    """Gate G5 for one solved LP. e is the stored energy. Returns measures and a pass flag."""
    d, c, s = det["discharge"], det["charge"], det["soc"]
    ec = ed = np.sqrt(eta)
    replay = top_k_mean((load - d + c)[win], k)
    soc_replay = e + np.cumsum(DT_H * (ec * c - d / ed))
    tol_p, tol_e = TOL_REL * max(p, 1) + 1e-6, TOL_REL * max(e, 1) + 1e-6
    out = {
        "h100_replay_err_mw": abs(replay - h100_after),
        "soc_err_mwh": float(np.abs(soc_replay - s).max()),
        "power_ok": bool(d.min() >= -tol_p and c.min() >= -tol_p
                         and d.max() <= p + tol_p and c.max() <= p + tol_p),
        "soc_ok": bool(s.min() >= -tol_e and s.max() <= e + tol_e),
        "energy_slack_mwh": float(ed * e + eta * c.sum() * DT_H - d.sum() * DT_H),
        "new_peaks": lifted_peaks(load, win, c, d - c, kth_highest((load - d + c)[win], k)),
    }
    out["failed_checks"] = ",".join(name for name, bad in (
        ("h100_replay", out["h100_replay_err_mw"] > TOL_MW),
        ("soc_recursion", out["soc_err_mwh"] > tol_e),
        ("power", not out["power_ok"]),
        ("soc_bounds", not out["soc_ok"]),
        ("energy", out["energy_slack_mwh"] < -tol_e),
        ("new_peaks", out["new_peaks"] > 0)) if bad)
    out["g5_ok"] = out["failed_checks"] == ""
    return out


def solve(job):
    """One LP. Runs in a worker process, so it only sees plain arrays."""
    t0 = time.perf_counter()
    load, win, k, eta = job["load"], job["win"], job["k"], job["eta"]
    p, e = job["p_mw"], job["e_stored_mwh"]
    row = {key: job[key] for key in ("winter", "basis", "window", "sensitivity", "p_mw", "d_h",
                                     "k", "e_mwh", "e_stored_mwh")}
    row.update(n_window=int(win.sum()), h100_before=top_k_mean(load[win], k))
    try:
        if not np.isfinite(load).all():
            raise ValueError(f"{int((~np.isfinite(load)).sum())} non-finite load values")
        # Dispatch that gets saved is tidied: same H100, no pointless cycling
        after, battery, det, info = min_top_k_no_new_peaks(
            load, win, p, e, k, dt_h=DT_H, eta=eta, tidy=job["keep_dispatch"])
    except Exception as exc:                    # report it in the row; never kill the grid
        row.update(h100_after=np.nan, cc=np.nan, status=f"{type(exc).__name__}: {exc}",
                   g5_ok=False, failed_checks="solve", solve_s=time.perf_counter() - t0,
                   traceback=traceback.format_exc())   # for run_grid.log; not a table column
        return row, None
    row.update(h100_after=after, cc=(row["h100_before"] - after) / p,
               cc_uncapped=(row["h100_before"] - info["h100_uncapped"]) / p,
               passes=info["passes"], converged=info["converged"], status="optimal",
               **lp_checks(load, win, p, e, k, eta, after, det),
               solve_s=time.perf_counter() - t0)
    if job["keep_dispatch"]:
        top = np.flatnonzero(win)[np.argsort(load[win], kind="stable")[-k:]]
        row["cc_availability"] = battery[top].mean() / p
        return row, battery
    return row, None


def solved(payloads, workers):
    """Yield (job, result) as LPs finish; in-process when workers == 1."""
    if workers == 1:
        for job in payloads:
            yield job, solve(job)
        return
    pool = ProcessPoolExecutor(workers)
    try:
        futures = {pool.submit(solve, job): job for job in payloads}
        for fut in as_completed(futures):
            yield futures[fut], fut.result()
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


# ------------------------------------------------------------------ after the grid

GROUP = ["winter", "basis", "window", "sensitivity", "d_h"]


def monotone_violations(table):
    """Curves where CC rises with P by more than TOL_CC, or leaves [0, 1]."""
    bad = []
    for key, g in table.sort_values("p_mw").groupby(GROUP):
        rise = np.diff(g.cc.to_numpy())
        if (rise > TOL_CC).any():
            bad.append((key, "rises", float(np.nanmax(rise))))
        if g.cc.min() < -TOL_CC or g.cc.max() > 1 + TOL_CC:
            bad.append((key, "outside [0, 1]", float(g.cc.min()), float(g.cc.max())))
    return bad


def duration_violations(table):
    """Same winter, basis, window and P: CC must not fall as the battery gets longer."""
    bad = []
    for key, g in table.groupby(["winter", "basis", "window", "p_mw"]):
        g = g.set_index(["sensitivity", "d_h"]).cc
        seq = [(lab, g[lab]) for lab in DURATION_ORDER if lab in g.index]
        for (a, ca), (b, cb) in zip(seq, seq[1:]):
            if cb < ca - TOL_CC:
                bad.append((key, f"{a} {ca:.4f} > {b} {cb:.4f}"))
    return bad


def crossing(p, cc, level):
    """P at which a falling CC curve first reaches level, linear between grid points.

    in_grid: interpolated between the last point above the level and the first
    at or below it. below_grid: below the level already at the smallest P.
    above_grid: never reaches it. incomplete: the curve has a missing value."""
    order = np.argsort(p)
    p, cc = np.asarray(p, float)[order], np.asarray(cc, float)[order]
    if np.isnan(cc).any():
        return np.nan, "incomplete"
    if cc[0] < level:
        return np.nan, "below_grid"
    at_or_below = np.flatnonzero(cc <= level)
    if not len(at_or_below):
        return np.nan, "above_grid"
    i = int(at_or_below[0])
    if i == 0:
        return p[0], "in_grid"
    p0, p1, c0, c1 = p[i - 1], p[i], cc[i - 1], cc[i]
    return p0 + (c0 - level) * (p1 - p0) / (c0 - c1), "in_grid"


def min_of_winters(per_winter):
    """The lowest single-winter p*, from [(winter, p_star, note)]."""
    notes = [n for _, _, n in per_winter]
    below = [w for w, _, n in per_winter if n == "below_grid"]
    if below:
        return np.nan, "below_grid", below[0]
    inside = [(p, w) for w, p, n in per_winter if n == "in_grid"]
    if inside:
        p, w = min(inside)
        return p, "in_grid", w
    return np.nan, ("incomplete" if "incomplete" in notes else "above_grid"), None


def crossings(table, cfg):
    """p* per curve: the mean over each winter set, each single winter, and the lowest winter."""
    ref = cfg["sosa_reference"]["bess_factor"]
    sets = {"base": cfg["credit"]["base_winters"], "check": cfg["credit"]["check_winters"]}
    rows = []
    for (basis, window, sens, d_h), g in table.groupby(["basis", "window", "sensitivity", "d_h"]):
        level = ref["le_2h"] if d_h <= 2 else ref["gt_2h"]
        common = {"basis": basis, "window": window, "sensitivity": sens, "d_h": d_h,
                  "sosa_factor": level}

        def row(winters, n, stat, curve, p_star, note, worst=None):
            return {**common, "winters": winters, "n_winters": n, "stat": stat,
                    "p_star_mw": p_star, "p_star_note": note, "worst_winter": worst,
                    **{f"cc_at_{int(p)}": v for p, v in curve.items()}}

        singles = {}
        for wi, gw in g.groupby("winter"):
            curve = gw.set_index("p_mw").cc.sort_index()
            singles[wi] = (curve, *crossing(curve.index.to_numpy(), curve.to_numpy(), level))
            rows.append(row(str(wi), 1, "single", curve, *singles[wi][1:]))
        for set_name, winters in sets.items():
            gs = g[g.winter.isin(winters)]
            if gs.empty:
                continue
            curve = gs.groupby("p_mw").cc.mean()
            rows.append(row(set_name, gs.winter.nunique(), "mean", curve,
                            *crossing(curve.index.to_numpy(), curve.to_numpy(), level)))
            p_star, note, worst = min_of_winters(
                [(wi, *singles[wi][1:]) for wi in sorted(gs.winter.unique())])
            curve = singles[worst][0] if worst is not None else curve * np.nan
            rows.append(row(set_name, gs.winter.nunique(), "min_of_winters", curve,
                            p_star, note, worst))
    out = pd.DataFrame(rows)
    out["worst_winter"] = out.worst_winter.astype("Int64")
    return out


def add_ratio(table):
    """CC relative to the smallest fleet on the same curve: reading (b) in the plan."""
    smallest = table.loc[table.groupby(GROUP).p_mw.idxmin(), GROUP + ["cc"]]
    smallest = smallest.rename(columns={"cc": "cc_smallest_p"})
    t = table.merge(smallest, on=GROUP, how="left", validate="many_to_one")
    t["cc_ratio"] = t.cc / t.cc_smallest_p
    return t.drop(columns="cc_smallest_p")


COLUMNS = ["winter", "basis", "window", "sensitivity", "p_mw", "d_h", "k", "e_mwh",
           "e_stored_mwh", "n_window", "h100_before", "h100_after", "cc", "cc_ratio",
           "cc_uncapped", "cc_availability", "passes", "converged", "solve_s", "status",
           "g5_ok", "failed_checks", "h100_replay_err_mw", "soc_err_mwh", "energy_slack_mwh",
           "power_ok", "soc_ok", "new_peaks"]


# ------------------------------------------------------------------ run

def run(cfg=None, series_path=SERIES / "ni_halfhourly.parquet", out=None, workers=None):
    cfg = cfg or load_config()
    get_logger(out)
    try:
        return _run(cfg, series_path, out, workers)
    finally:
        close_logger()


def _run(cfg, series_path, out, workers):
    t_start = time.perf_counter()
    eta = cfg["eta_roundtrip"]
    workers = workers or cfg["credit"].get("workers") or max(1, (os.cpu_count() or 2) - 1)

    log.info("run_grid: config")
    log.info(f"  grid {cfg['grid']}")
    log.info(f"  sensitivities {cfg['sensitivities']} (d = 2 h only)")
    log.info(f"  credit {cfg['credit']}; k_top {cfg['k_top']}; eta {eta}; workers {workers}")

    s = pd.read_parquet(series_path)
    s = s[s.winter.notna()].reset_index(drop=True)
    winters = sorted(int(w) for w in s.winter.unique())
    by_winter = {wi: s[s.winter == wi].reset_index(drop=True) for wi in winters}
    log.info(f"input {logs.rel(series_path)}: {len(s):,} winter half-hours, winters {winters}")
    for wi, w in by_winter.items():
        sizes = {wn: int(window_mask(w, wn).sum()) for wn in WINDOWS}
        top = w.loc[window_mask(w, "all")].nlargest(cfg["k_top"], "l_gross_mw")
        log.info(f"  {wi}: {len(w):,} half-hours, window sizes {sizes}, "
                 f"complete {bool(w.winter_complete.iloc[0])}; existing batteries at the gross "
                 f"top {cfg['k_top']}: mean {top.bess_mw.mean():.1f} MW, "
                 f"max {w.bess_mw.max():.1f} MW; grid solar max {w.solar_mw.max():.1f} MW")

    jobs = plan(cfg, winters)
    n_base = sum(j[3] == "base" for j in jobs)
    log.info(f"plan: {len(jobs):,} LPs ({n_base:,} base + {len(jobs) - n_base:,} sensitivity); "
             f"rated energy is {cfg['credit'].get('rated_energy', 'delivered')}")

    payloads = []
    for wi, basis, window, sens, p, d_h in jobs:
        w = by_winter[wi]
        frac = cfg["sensitivities"]["usable70"]["soc_max_frac"] if sens == "usable70" else 1.0
        e_rated, e_stored = stored_energy(p, d_h, frac, cfg)
        payloads.append({
            "winter": wi, "basis": basis, "window": window, "sensitivity": sens,
            "p_mw": p, "d_h": d_h, "k": job_k(cfg, sens), "eta": eta,
            "e_mwh": e_rated, "e_stored_mwh": e_stored,
            "load": winter_load(w, basis, sens, cfg), "win": window_mask(w, window),
            "keep_dispatch": d_h == 2 and sens == "base" and window == "all",
        })

    rows, dispatch = [], []
    for i, (job, (row, battery)) in enumerate(solved(payloads, workers), 1):
        rows.append(row)
        if battery is not None:
            w = by_winter[job["winter"]]
            dispatch.append(pd.DataFrame({
                "ts_local": w.ts_local, "winter": job["winter"], "basis": job["basis"],
                "p_mw": job["p_mw"], "load_mw": job["load"], "battery_mw": battery,
                "load_after_mw": job["load"] - battery}))
        verdict = "ok" if row["g5_ok"] else f"G5 FAIL [{row['failed_checks']}] {row['status']}"
        say = log.info if row["g5_ok"] else log.warning
        say(f"  [{i:>4}/{len(payloads)}] {row['winter']} {row['basis']:<5} "
            f"{row['window']:<3} {row['sensitivity']:<8} P={row['p_mw']:>5} "
            f"d={row['d_h']} k={row['k']}  CC {row['cc']:6.3f} "
            f"(uncapped {row.get('cc_uncapped', np.nan):.3f}, {row.get('passes', 0)} pass"
            f"{'' if row.get('converged', True) else ', split not converged'})  "
            f"H100 {row['h100_before']:,.1f} -> {row['h100_after']:,.1f}  "
            f"{row['solve_s']:5.2f}s  {verdict}")
        if row.get("traceback"):
            log.warning(textwrap.indent(row.pop("traceback").rstrip(), "    "))

    table = add_ratio(pd.DataFrame(rows))
    table = table.sort_values(["sensitivity", "basis", "window", "d_h", "winter", "p_mw"])
    table = table.reindex(columns=COLUMNS).reset_index(drop=True)
    cross = crossings(table, cfg)

    log.info("G5 checks")
    optimal = table.status.eq("optimal")
    lp_fail = table[optimal & ~table.g5_ok.astype(bool)]
    mono, dur = monotone_violations(table), duration_violations(table)
    log.info(f"  solver: {int(optimal.sum()):,}/{len(table):,} optimal")
    for r in table[~optimal].head(10).itertuples():
        log.warning(f"    not optimal: {r.winter} {r.basis} {r.window} {r.sensitivity} "
                 f"P={r.p_mw} d={r.d_h}: {r.status}")
    log.info(f"  replayed H100 vs LP objective: max error {table.h100_replay_err_mw.max():.2e} MW "
             f"(tolerance {TOL_MW})")
    log.info(f"  state of charge recursion: max error {table.soc_err_mwh.max():.2e} MWh; "
             f"bounds held in {int(table.soc_ok.fillna(False).sum()):,}/{len(table):,}; "
             f"power bounds held in {int(table.power_ok.fillna(False).sum()):,}/{len(table):,}")
    log.info(f"  energy: min slack {table.energy_slack_mwh.min():,.3f} MWh (>= 0 means "
             "delivered <= sqrt(eta) E + eta x charged)")
    for r in lp_fail.head(10).itertuples():
        log.warning(f"    failed: {r.winter} {r.basis} {r.window} {r.sensitivity} P={r.p_mw} "
                 f"d={r.d_h}: {r.failed_checks}")
    drop = (table.cc_uncapped - table.cc) * 100
    changed = drop > TOL_CC * 100
    log.info(f"  no new peaks: the rule changed CC in {int(changed.sum()):,}/{len(table):,} LPs "
             f"(by {drop[changed].mean() if changed.any() else 0:.2f} pp on average, "
             f"{drop.max() if changed.any() else 0:.2f} pp at most); "
             f"{int(table.passes.fillna(0).max())} passes at most; split not converged in "
             f"{int((table.converged == False).sum())} LPs; off-window half-hours still above "
             f"the line: {int(table.new_peaks.fillna(0).sum())}")
    for (window, d_h), g in table.groupby(["window", "d_h"]):
        dg = (g.cc_uncapped - g.cc) * 100
        log.info(f"    window {window:<3} d={d_h}: CC changed in {int((dg > TOL_CC * 100).sum()):>3}"
                 f"/{len(g)} LPs, by {dg.max():.2f} pp at most")
    log.info(f"  CC curves: {table.groupby(GROUP).ngroups:,}, monotone violations {len(mono)}")
    for v in mono[:20]:
        log.warning(f"    {v}")
    log.info(f"  duration order (1 h <= usable70 <= 2 h <= 4 h): violations {len(dur)}")
    for v in dur[:20]:
        log.warning(f"    {v}")
    avail = table.dropna(subset=["cc_availability"])
    if len(avail):
        log.info(f"  availability score (saved dispatch only, {len(avail)} LPs): exceeds CC by "
                 f"{(avail.cc_availability - avail.cc).mean() * 100:.1f} pp on average")

    log.info("outputs")
    log.info(f"  credit_table: {table.shape[0]:,} rows x {table.shape[1]} columns")
    log.info(f"  crossings: {cross.shape[0]:,} rows x {cross.shape[1]} columns")
    disp = pd.concat(dispatch, ignore_index=True) if dispatch else pd.DataFrame()
    log.info(f"  dispatch_d2: {disp.shape[0]:,} rows x {disp.shape[1]} columns")
    if out is not None:
        table.to_csv(out / "credit_table.csv", index=False)
        cross.to_csv(out / "crossings.csv", index=False)
        if len(disp):
            disp.sort_values(["basis", "p_mw", "ts_local"]).to_parquet(
                out / "dispatch_d2.parquet", index=False)

    headline = cross[(cross.sensitivity == "base") & (cross.window == "all") & (cross.d_h == 2)
                     & cross.stat.isin(["mean", "min_of_winters"])]
    for r in headline.to_dict("records"):
        p_star = f"{r['p_star_mw']:,.0f} MW" if r["p_star_note"] == "in_grid" else r["p_star_note"]
        curve = ", ".join(f"{c[6:]} MW {v:.3f}" for c, v in r.items()
                          if c.startswith("cc_at_") and pd.notna(v))
        who = f" (winter {r['worst_winter']})" if r["stat"] == "min_of_winters" else ""
        log.info(f"p*: {r['basis']:<5} {r['winters']:<5} {r['stat']:<14} (n={r['n_winters']}) "
                 f"d=2h window all: falls to {r['sosa_factor']:.0%} at {p_star}{who}; CC {curve}")
    log.info(f"run_grid: done in {time.perf_counter() - t_start:,.0f} s")

    not_optimal = int((~optimal).sum())
    if not_optimal or len(lp_fail) or mono or dur:
        raise GateError(f"G5: {not_optimal} LPs not optimal, {len(lp_fail)} optimal LPs failed "
                        f"a dispatch check, {len(mono)} CC curves not monotone in P, {len(dur)} "
                        "duration-order violations; see run_grid.log and credit_table.csv")
    return table, cross
