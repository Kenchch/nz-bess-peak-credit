"""Run every module in pipeline order and log how the data changes shape.

    uv run python -I scripts/trace_pipeline.py

Everything is written under outputs/trace/<run_id>/ (staging, series, the
analysis outputs including the full LP grid, a published copy and the
figures), so data/, outputs/runs/, outputs/current/ and memo/ are never
touched. At the end each trace copy is compared with the latest pipeline run
that wrote the same file, and the published copy with outputs/current, so the
log doubles as a reproducibility check. The grid makes this take about 8
minutes.

    trace.log              full log: per step, input and output shape, columns,
                           dtypes, nulls, ranges, sample rows, what changed;
                           the steps' own log lines (not each LP) in between
    analysis/run_grid.log  every LP of the grid, with its result and checks
    current/               what `bess publish` would put in outputs/current
    figures/               the two memo figures, drawn from current/
    data_lineage.md        one table, one row per step
"""
import datetime as dt
import json
import logging
import re
import sys
import textwrap
import time

import numpy as np
import pandas as pd

from bess import OUTPUTS, RAW, SERIES, STAGING, load_config
from bess.cli import git_tag
from bess import (calibrate, credit, figures, ingest, logs, observed, peak_shape, publish,
                  reconcile, run_grid, series, stage, top200)

SAMPLE_MONTH = "202507"
ACCEPT_GAP = "trace run: the solar 2024 gap accepted in RESULTS.md"
TP_COL = re.compile(r"^TP\d+$")
STEPS = 11                       # config is step 0

log = logging.getLogger("trace")
lineage = []


# ---------------------------------------------------------------- formatting

def setup(out):
    logs.utf8_streams()          # a cp1252 pipe cannot print Ruakākā
    logs.console()               # the steps' own lines, as `bess` shows them
    logs.capture_warnings()
    out.mkdir(parents=True)
    log.setLevel(logging.INFO)
    fmt = logs.formatter()       # UTC, with the level, like every bess log
    file = logging.FileHandler(out / "trace.log", encoding="utf-8")
    screen = logging.StreamHandler(sys.stdout)
    for h in (file, screen):
        h.setFormatter(fmt)
        log.addHandler(h)
    # The steps' own lines go into trace.log too; each LP's line stays in run_grid.log
    file.addFilter(not_an_lp_line)
    logs.log.addHandler(file)
    logs.WARNINGS.addHandler(file)


def not_an_lp_line(record):
    """False for run_grid's line per LP ("  [  12/1218] ..."), unless it is a warning."""
    return not (record.name == "bess.run_grid" and record.levelno < logging.WARNING
                and record.getMessage().startswith("  ["))


def header(n, name, module, what):
    log.info("")
    log.info("=" * 78)
    log.info(f"[{n}/{STEPS}] {name}   ({module})   {what}")
    log.info("=" * 78)


def frame(label, df, sample=3, max_cols=10):
    """Shape, memory, per-column dtype / nulls / range, and a few rows."""
    mem = df.memory_usage(deep=True).sum() / 2**20
    log.info(f"  {label}: {len(df):,} rows x {df.shape[1]} columns, {mem:,.1f} MB in memory")
    tps = [c for c in df.columns if TP_COL.match(str(c))]
    if tps:
        vals = df[tps].apply(pd.to_numeric, errors="coerce")
        log.info(f"      {tps[0]}..{tps[-1]:<15} {len(tps)} kWh columns, "
                 f"nulls {int(vals.isna().sum().sum()):,}, range "
                 f"[{np.nanmin(vals.to_numpy()):,.4g}, {np.nanmax(vals.to_numpy()):,.4g}]")
    for c in df.columns:
        if c in tps:
            continue
        col = df[c]
        info = f"{str(col.dtype):<22} nulls {int(col.isna().sum()):>8,}"
        if pd.api.types.is_bool_dtype(col):
            info += f"  True {int(col.fillna(False).sum()):,}"
        elif pd.api.types.is_numeric_dtype(col) or pd.api.types.is_datetime64_any_dtype(col):
            if col.notna().any():
                lo, hi = col.min(), col.max()
                fmt = (lambda v: f"{v:,.4g}") if pd.api.types.is_numeric_dtype(col) else str
                info += f"  range [{fmt(lo)}, {fmt(hi)}]"
        else:
            info += f"  {col.nunique():,} distinct values"
        log.info(f"      {str(c):<24} {info}")
    shown = [c for c in df.columns if c not in tps][:max_cols]
    extra = f"  ({len(tps)} TP columns not shown)" if tps else ""
    log.info(f"      first {sample} rows{extra}:")
    log.info(textwrap.indent(df[shown].head(sample).to_string(max_colwidth=22), "      | "))


def array(label, a):
    a = np.asarray(a)
    finite = a[np.isfinite(a)] if a.dtype.kind == "f" else a
    rng = f", range [{finite.min():,.4g}, {finite.max():,.4g}]" if finite.size else ""
    nan = f", NaN {int(np.isnan(a).sum()):,}" if a.dtype.kind == "f" else ""
    log.info(f"  {label}: ndarray {a.shape} {a.dtype}{nan}{rng}")


def change(text):
    log.info(f"  -> what changed: {text}")


def record(step, module, inp, out, rows, what):
    lineage.append({"step": step, "module": module, "input": inp, "output": out,
                    "rows": rows, "change": what})


def timed(fn, *args, **kw):
    t0 = time.perf_counter()
    result = fn(*args, **kw)
    log.info(f"  took {time.perf_counter() - t0:,.1f} s")
    return result


# ---------------------------------------------------------------- steps

def step_config(cfg):
    header(0, "config", "config.yaml", "parameters every module shares")
    for k, v in cfg.items():
        log.info(f"  {k}: {v}")


def step_ingest(cfg):
    header(1, "ingest", "ingest.py", "G1 pinned inputs: every file present, SHA-256 as pinned")
    pins = ingest.read_manifest()
    todo = ingest.targets(cfg)
    by_ds = {}
    for ds, name, _ in todo:
        path = RAW / ds / name
        pin = pins.get((ds, name))
        ok = path.exists() and pin is not None and ingest.sha256(path) == pin["sha256"]
        d = by_ds.setdefault(ds, {"files": 0, "ok": 0, "bytes": 0})
        d["files"] += 1
        d["ok"] += ok
        d["bytes"] += path.stat().st_size if path.exists() else 0
    for ds, d in by_ds.items():
        log.info(f"  {ds:<14} {d['files']:>3} files, {d['ok']:>3} match their pin, "
                 f"{d['bytes'] / 2**20:,.1f} MB")
    bad = sum(d["files"] - d["ok"] for d in by_ds.values())
    log.info(f"  G1: {'passed' if not bad else f'{bad} files do not match'} "
             "(checked only, nothing downloaded)")

    log.info("")
    log.info(f"  What the raw files look like ({SAMPLE_MONTH} as the example):")
    samples = {
        "generation_md": RAW / "generation_md" / f"{SAMPLE_MONTH}_Generation_MD.csv",
        "hvdc_flows": RAW / "hvdc_flows" / f"{SAMPLE_MONTH}_HVDC_flows.csv",
        "grid_export": RAW / "grid_export" / f"{SAMPLE_MONTH}_Grid_export.csv",
        "nsp": RAW / "nsp" / ingest.REFERENCE["nsp"][1],
        "fleet": RAW / "fleet" / ingest.REFERENCE["fleet"][1],
    }
    for ds, path in samples.items():
        frame(f"{ds} ({path.name})", pd.read_csv(path, dtype=str), sample=2, max_cols=8)
    change("none. ingest only puts the files in data/raw/ and records URL, date, bytes and "
           "SHA-256 in manifest.csv. The raw format is wide: one row = one unit (or meter) for "
           "one day, TP1..TP50 = kWh per half-hour")
    total = sum(d["files"] for d in by_ds.values())
    record("ingest", "ingest.py", "EMI website", f"data/raw/, {total} CSVs",
           f"{total} files", "downloaded and pinned by hash; the data itself is unchanged")


def step_stage(cfg, out):
    header(2, "stage", "stage.py", "G2 wide to long, six row rules, quarantine instead of drop")
    islands = timed(stage.island_map, RAW / "nsp" / ingest.REFERENCE["nsp"][1])
    log.info(f"  island_map: {len(islands):,} POC / site codes -> island "
             f"({pd.Series(islands).value_counts().to_dict()})")

    log.info("")
    log.info(f"  One month unpivoted ({SAMPLE_MONTH}, Generation_MD):")
    path = RAW / "generation_md" / f"{SAMPLE_MONTH}_Generation_MD.csv"
    long, rule, wide = stage.stage_generation(path, islands)
    frame("input, wide (island and fuel added)", wide, sample=2, max_cols=10)
    log.info(f"  row rules: {dict(zip(*np.unique(rule, return_counts=True)))} "
             f"(0 = passed; 1..6 = {stage.RULES})")
    frame("output, long", long, sample=3)
    days = pd.to_datetime(wide.Trading_Date).nunique()
    change(f"{len(wide):,} rows x 50 TP columns -> {len(long):,} rows x {long.shape[1]} columns. "
           "A row goes from \"one unit for one day\" to \"one unit for one half-hour\"; each row "
           "unpivots into as many half-hours as its day really has (46, 48 or 50), so TP49 and "
           f"TP50 survive only on the day daylight saving ends. {days} days, "
           f"{len(long) / len(wide):.2f} rows out per row in")

    log.info("")
    n_months = len(ingest.months(cfg["period"]["start"], cfg["period"]["end"]))
    log.info(f"  All {n_months} months x {len(stage.SOURCES)} sources:")
    report = timed(stage.run, cfg, raw=RAW, out=out)
    tot = report[report.file == "ALL"]
    frame("quality_report.csv, totals", tot, sample=2, max_cols=12)
    gen = pd.read_parquet(out / "generation.parquet")
    hvdc = pd.read_parquet(out / "hvdc.parquet")
    q = pd.read_parquet(out / "quarantine.parquet")
    frame("generation.parquet", gen)
    frame("hvdc.parquet", hvdc)
    log.info(f"  quarantine.parquet: {len(q):,} rows")
    for r in tot.itertuples():
        adds_up = r.rows_in == r.rows_ok + r.rejected
        log.info(f"  check {r.source}: rows_in {r.rows_in:,} = rows_ok {r.rows_ok:,} + "
                 f"rejected {r.rejected:,} -> {'adds up' if adds_up else 'DOES NOT ADD UP'}")
    change("string columns become category (poc and the like have a few hundred values), kWh "
           "becomes one float column; Trading_Date goes from string to datetime64")
    g, h = tot.set_index("source").loc["generation"], tot.set_index("source").loc["hvdc"]
    record("stage", "stage.py", f"wide, {g.rows_in:,} + {h.rows_in:,} rows",
           f"generation {len(gen):,} rows, hvdc {len(hvdc):,} rows, quarantined {len(q):,} rows",
           f"x{len(gen) / g.rows_in:.1f} (wide -> long)",
           "TP1..TP50 unpivoted to rows; six rules; strings -> category")
    return gen, hvdc


def step_series(cfg, staging, out):
    header(3, "series", "series.py", "the North Island half-hourly series (data contract 4.1)")
    cal = timed(series.calendar, cfg["period"]["start"], cfg["period"]["end"])
    frame("calendar(): the time skeleton", cal)
    per_day = cal.groupby("trading_date").size()
    log.info(f"  half-hours per day: {per_day.value_counts().sort_index().to_dict()} "
             "(46 = daylight saving starts, 50 = it ends)")
    lab = series.label(cal, cfg)
    frame("label(): winter, period and NI-WCM window added", lab)
    log.info(f"  periods: {lab.period.value_counts().to_dict()}")

    gen = pd.read_parquet(staging / "generation.parquet",
                          columns=["trading_date", "tp", "island", "fuel", "kwh"])
    hvdc = pd.read_parquet(staging / "hvdc.parquet")
    key = ["trading_date", "tp"]
    ni = gen[gen.island == "NI"]
    log.info(f"  North Island only: {len(gen):,} -> {len(ni):,} rows "
             f"({len(gen) - len(ni):,} South Island rows dropped)")
    by_fuel = (ni.groupby(key + ["fuel"], observed=True).kwh.sum()
               .unstack("fuel", fill_value=0.0) / series.KWH_PER_MW)
    by_fuel.columns = by_fuel.columns.astype(str)
    frame("pivoted to (date, TP) x fuel, kWh / 500 = MW", by_fuel.reset_index())
    hay = hvdc[hvdc.poc == series.HVDC_NI_POC]
    flows = hay.groupby(key + ["flow_direction"], observed=True).kwh.sum().unstack(fill_value=0.0)
    flows.columns = flows.columns.astype(str)
    frame("HAY2201 pivoted to I / X (kWh)", flows.reset_index())

    s = timed(series.run, cfg, staging=staging, out=out)
    frame("ni_halfhourly.parquet", s)
    log.info(f"  formula check: l_gross = gen_ni + hvdc_north, max error "
             f"{(s.l_gross_mw - s.gen_ni_mw - s.hvdc_north_mw).abs().max():.2e}; "
             f"l_net = l_gross - wind - solar, max error "
             f"{(s.l_net_mw - s.l_gross_mw + s.wind_mw + s.solar_mw).abs().max():.2e}")
    w = s[s.winter.notna()]
    log.info(f"  rows per winter: {w.groupby('winter').size().to_dict()}")
    change(f"about {len(gen):,} rows (unit x half-hour) summed to {len(s):,} rows (one per "
           "half-hour); fuels go from rows to columns (gen_ni / wind / solar / bess); time labels "
           "and the two demand series are added")
    record("series", "series.py", f"generation {len(gen):,} + hvdc {len(hvdc):,} rows",
           f"ni_halfhourly {len(s):,} rows x {s.shape[1]} columns",
           f"/{len(gen) / len(s):.0f} (summed)",
           "units -> North Island totals; fuel rows -> columns; kWh -> MW; winter and period "
           "labels")
    return s


def step_reconcile(cfg, series_path, out):
    header(4, "reconcile", "reconcile.py",
           "G3 reconciliation (warning only): North Island supply vs GXP offtake")
    path = RAW / "grid_export" / f"{SAMPLE_MONTH}_Grid_export.csv"
    frame(f"input, Grid_export wide ({path.name})", pd.read_csv(path, dtype=str), sample=2)
    rec = timed(reconcile.run, cfg, series_path=series_path, raw=RAW, out=out)
    frame("reconcile.csv", rec)
    n = len(pd.read_parquet(series_path, columns=["tp"]))
    change(f"{n:,} half-hours summed by month into {len(rec)} rows (GWh); each month's "
           "Grid_export is reduced to one number too, and the difference is the share lost to "
           "losses and embedded generation, next to a column for \"what if the HVDC sign were "
           "flipped\"")
    record("reconcile", "reconcile.py", f"series + {len(rec)} Grid_export files",
           f"reconcile.csv {len(rec)} rows", "half-hour -> month",
           "monthly energy balance; sign check")


def step_calibrate(cfg, staging, series_path, out):
    header(5, "calibrate", "calibrate.py",
           "G4 calibration: reproduce SOSA's wind and solar peak factors")
    fleet = calibrate.load_fleet(RAW / "fleet" / ingest.REFERENCE["fleet"][1],
                                 stage.island_map(RAW / "nsp" / ingest.REFERENCE["nsp"][1]))
    frame("load_fleet(): wind, solar and battery nameplate with in-force dates", fleet)
    s, _, left_out = timed(calibrate.renewables, cfg, staging=staging, series_path=series_path)
    base = set(series.COLUMNS)
    added = [c for c in s.columns if c not in base]
    frame(f"renewables(): {len(added)} columns added to the series", s[["ts_local"] + added])
    log.info(f"  plants left out: {len(left_out)}")
    for fuel, what, why in left_out:
        log.info(f"      {fuel}: {what}: {why}")
    cal = timed(calibrate.run, cfg, out=out, accept_gap=ACCEPT_GAP,
                staging=staging, series_path=series_path)
    frame("calibration.csv (one row per definition)", cal)
    change(f"the series widens from {len(series.COLUMNS)} to {s.shape[1]} columns (national "
           "generation, and output and nameplate per fuel and scope); year x demand x selection x "
           f"scope x technology then gives a {len(cal)}-row result table")
    record("calibrate", "calibrate.py", "series + generation + fleet",
           f"calibration.csv {len(cal)} rows (+ calibration.md)",
           f"columns {len(series.COLUMNS)} -> {s.shape[1]}",
           f"nameplate and output columns; {len(cal)} definitions against SOSA")


def step_peak_shape(cfg, series_path, out):
    header(6, "peak_shape", "peak_shape.py", "how wide the peaks are: depth-duration curves")
    s = pd.read_parquet(series_path)
    w = s[s.winter.notna()]
    for window in peak_shape.WINDOWS:
        load, days = peak_shape.day_matrix(w[w.period == window], "l_gross_mw")
        array(f"day_matrix({window}), days x half-hours", load)
        duration, battery = peak_shape.curves(load)
        array(f"curves({window}) battery_h, days x {len(peak_shape.DELTAS)} depths", battery)
    table = timed(peak_shape.run, cfg, series_path=series_path, out=out)
    frame("peak_shape.csv", table)
    change("long series -> one (day x half-hour) matrix per window -> broadcast over "
           f"{len(peak_shape.DELTAS)} depths (day x depth x half-hour) -> P50 and P90 across days "
           f"-> a {len(table):,}-row summary")
    record("peak_shape", "peak_shape.py", "series (winters)",
           f"peak_shape.csv {len(table):,} rows + png", "long -> matrix -> summary",
           f"day x half-hour matrix; {len(peak_shape.DELTAS)} depths; P50/P90")


def step_top200(cfg, staging, series_path, out):
    header(7, "top200", "top200.py", "the top-200 half-hours on gross vs net demand")
    table = timed(top200.run, cfg, out=out, staging=staging, series_path=series_path)
    frame("top200_summary.csv", table, max_cols=12)
    change("in each winter, the 200 highest in-window half-hours on each basis, their overlap "
           f"and their means -> {len(table)} rows (winter x basis)")
    record("top200", "top200.py", "series + nameplate columns",
           f"top200_summary.csv {len(table)} rows", "~6,400 per winter -> 200 -> 1",
           "top 200 on each basis; overlap, morning/evening split, wind and solar factors")


def step_grid(cfg, series_path, out, winter=2025, p_mw=335, hours=2):
    header(8, "run_grid", "run_grid.py + credit.py",
           "G5 LP grid: one LP per parameter set, then the lookup table and p*")
    s = pd.read_parquet(series_path)
    w = s[s.winter.eq(winter).fillna(False)].reset_index(drop=True)
    log.info(f"  Inside one LP ({winter} winter, gross, window all, P = {p_mw} MW, {hours} h):")
    load = run_grid.winter_load(w, "gross", "base", cfg)
    in_window = run_grid.window_mask(w, "all")
    array("load = l_gross_mw + bess_mw (existing batteries added back)", load)
    log.info(f"  adding the batteries back raises demand by at most {w.bess_mw.max():.1f} MW, "
             f"{w.bess_mw.mean():.2f} MW on average over the winter")
    array("in_window (07:00-22:00)", in_window)

    captured = {}
    real_linprog = credit.linprog

    def spy(c, A_ub, b_ub, A_eq, b_eq, bounds, method):
        captured.update(nv=len(c), ub=A_ub.shape, ub_nnz=A_ub.nnz, eq=A_eq.shape, eq_nnz=A_eq.nnz)
        t0 = time.perf_counter()
        res = real_linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq,
                           bounds=bounds, method=method)
        captured.update(solve_s=time.perf_counter() - t0, status=res.message)
        return res

    k = cfg["k_top"]
    e_rated, e_stored = run_grid.stored_energy(p_mw, hours, 1.0, cfg)
    log.info(f"  energy basis {cfg['credit'].get('rated_energy')}: rated {e_rated:,.0f} MWh "
             f"delivered to the grid, a store of {e_stored:,.1f} MWh (= rated / sqrt(eta))")
    before = credit.top_k_mean(load[in_window], k)
    credit.linprog = spy
    try:
        after, battery = credit.min_top_k(load, in_window, p_mw, e_stored, k,
                                          eta=cfg["eta_roundtrip"])
    finally:
        credit.linprog = real_linprog
    log.info(f"  LP variables {captured['nv']:,} = 3 x {len(load):,} (discharge / charge / state "
             f"of charge) + {int(in_window.sum()):,} (excess u) + 1 (line tau)")
    log.info(f"  inequality constraints A_ub {captured['ub']}, {captured['ub_nnz']:,} non-zeros; "
             f"equality constraints A_eq {captured['eq']}, {captured['eq_nnz']:,} non-zeros")
    log.info(f"  HiGHS: {captured['status']}, {captured['solve_s']:.2f} s")
    array("battery (discharge positive, charge negative) MW", battery)
    cc = (before - after) / p_mw
    log.info(f"  pass 1 (charging unrestricted): H100 {before:,.1f} MW -> {after:,.1f} MW; "
             f"CC = {cc:.1%}")
    line = credit.kth_highest((load - battery)[in_window], k)
    charge = np.clip(-battery, 0, None)
    lifted = credit.lifted_peaks(load, in_window, charge, battery, line)
    log.info(f"  H100 line ({k}th highest) {line:,.1f} MW; off-window half-hours charged above "
             f"the line: {lifted}")
    after2, battery2, _, info = credit.min_top_k_no_new_peaks(
        load, in_window, p_mw, e_stored, k, eta=cfg["eta_roundtrip"])
    new_peaks = credit.lifted_peaks(load, in_window, np.clip(-battery2, 0, None), battery2,
                                    info["line_mw"])
    log.info(f"  min_top_k_no_new_peaks: {info['passes']} passes (split converged "
             f"{info['converged']}), CC {cc:.1%} -> {(before - after2) / p_mw:.1%}; new "
             f"off-window peaks {new_peaks}")
    change(f"{len(load):,} load values -> a sparse LP with {captured['nv']:,} variables (solved "
           f"again with a charging cap when needed) -> one {len(battery):,}-long dispatch -> one "
           "number, CC")

    log.info("")
    winters = sorted(int(x) for x in s.winter.dropna().unique())
    jobs = run_grid.plan(cfg, winters)
    log.info(f"  full grid: {len(jobs):,} LPs, one per (winter, basis, window, sensitivity, P, d); "
             f"each LP's inputs, result and G5 checks are in {logs.rel(out / 'run_grid.log')}")
    table, cross = timed(run_grid.run, cfg, series_path=series_path, out=out)
    frame("credit_table.csv (one row per LP)", table, max_cols=12)
    frame("crossings.csv (one row per curve and statistic)", cross, max_cols=11)
    disp = pd.read_parquet(out / "dispatch_d2.parquet")
    frame("dispatch_d2.parquet (one row per half-hour x P x basis)", disp)
    change(f"{len(jobs):,} parameter sets x about 10,000 half-hours each -> {len(table):,} result "
           "rows (one per LP: the series shrinks to H100 before and after) -> curves interpolated "
           f"into {len(cross)} rows of p*; the d = 2 h dispatch is kept, {len(disp):,} rows, for "
           "Power BI")
    record("run_grid", "run_grid.py + credit.py",
           f"series, {len(winters)} winters, {len(jobs):,} parameter sets",
           f"credit_table {len(table):,} rows, crossings {len(cross)} rows, "
           f"dispatch {len(disp):,} rows",
           "~10,000 per LP -> 1 row -> p*", f"{captured['nv']:,} variables per LP; every G5 check")


def step_observed(cfg, staging, series_path, out, **paths):
    header(9, "observed", "observed.py",
           "what the batteries on the grid delivered at the peak, against the ceiling")
    units = observed.battery_output(staging).reset_index()
    units.columns = units.columns.astype(str)
    frame(f"battery_output(): MW per (date, TP) for {', '.join(observed.UNITS.values())}", units)
    table = timed(observed.run, cfg, series_path=series_path, staging=staging, out=out, **paths)
    frame("observed_vs_ceiling.csv (one row per winter)", table, max_cols=12)
    counted = table[table.units_counted != "none"]
    change(f"battery output at each winter's top {cfg['k_top']} half-hours, over the nameplate "
           "Generation_MD can see, next to the ceiling the LP gives a fleet that size; "
           f"{len(counted)} of {len(table)} winters have a battery to count")
    record("observed", "observed.py", "staging generation + series + credit_table",
           f"observed_vs_ceiling.csv {len(table)} rows",
           f"{cfg['k_top']} half-hours -> 1 per winter",
           "battery output at the peak vs the LP ceiling")
    return table


def step_publish(cfg, analysis, out):
    header(10, "publish", "publish.py",
           "G6: check the run against its contracts, then publish it into the trace folder")
    done = analysis / "DONE.txt"
    if not done.exists():                    # cli.guarded writes it at the end of a real run
        done.write_text("trace\n", encoding="utf-8")
    found = publish.problems(analysis, cfg)
    log.info(f"  G6 contracts: {'all met' if not found else '; '.join(found)}")
    current = out / "current"
    timed(publish.run, cfg, run_dir=analysis, current=current)
    m = json.loads((current / "run_manifest.json").read_text(encoding="utf-8"))
    log.info(f"  run_manifest.json: headline p* {m['headline_p_star_mw']}; "
             f"warnings {m['warnings']}")
    for name in ("dim_winter.csv", "dim_p.csv"):
        frame(name, pd.read_csv(current / name))
    pbi = pd.read_parquet(current / "pbi_dispatch.parquet")
    frame("pbi_dispatch.parquet", pbi)
    change("the run's files are copied as they are; added: run_manifest.json (code, config and "
           "input hashes, headline p*, gate warnings), two small dimension tables, and "
           "pbi_dispatch.parquet: dispatch_d2 with naive New Zealand local time, date and time of "
           "day, because Power Query cannot apply daylight saving to a UTC timestamp")
    record("publish", "publish.py", f"{logs.rel(analysis)}, {len(m['files'])} files",
           f"{logs.rel(current)} + run_manifest.json, dim_winter, dim_p, pbi_dispatch",
           f"pbi_dispatch {len(pbi):,} rows (as dispatch_d2)",
           "G6 contracts; UTC -> NZ local time for Power BI")
    return current


def step_figures(cfg, out):
    header(11, "figures", "figures.py", "the two memo figures, drawn from the published tables")
    figs = out / "figures"
    timed(figures.run, cfg, current=out / "current", out=figs)
    pngs = sorted(figs.glob("*.png"))
    for p in pngs:
        log.info(f"  {p.name}: {p.stat().st_size / 1024:,.0f} KB")
    change("credit_table and crossings drawn as two PNGs; memo/memo.qmd includes the copies in "
           "memo/")
    record("figures", "figures.py", "published credit_table + crossings", f"{len(pngs)} PNGs",
           "-", "drawn, not transformed")


# ---------------------------------------------------------------- checks

def compare_with_committed(out):
    log.info("")
    log.info("=" * 78)
    log.info("Reproducibility check: the trace's outputs vs the pipeline's own")
    log.info("=" * 78)
    pairs = [(out / "staging" / f, STAGING / f) for f in ("generation.parquet", "hvdc.parquet")]
    pairs.append((out / "series" / "ni_halfhourly.parquet", SERIES / "ni_halfhourly.parquet"))
    root = OUTPUTS / "runs"
    # Only runs that finished (DONE.txt, written by cli.guarded) count as results
    runs = sorted(p for p in root.iterdir() if (p / "DONE.txt").exists()) if root.is_dir() else []
    log.info(f"  finished runs to compare with: {[r.name for r in runs][-3:] or 'none'}")
    for f in ("reconcile.csv", "calibration.csv", "peak_shape.csv", "top200_summary.csv",
              "credit_table.csv", "crossings.csv", "dispatch_d2.parquet",
              "observed_vs_ceiling.csv"):
        have = [r / f for r in runs if (r / f).exists()]       # latest run that wrote this file
        pairs.append((out / "analysis" / f, have[-1] if have else out / "missing" / f))
    # What publish adds, against the published copy
    for f in ("dim_winter.csv", "dim_p.csv", "pbi_dispatch.parquet"):
        pairs.append((out / "current" / f, OUTPUTS / "current" / f))
    for new, old in pairs:
        if not old.exists():
            log.info(f"  {old.name}: nothing to compare with")
            continue
        read = pd.read_parquet if new.suffix == ".parquet" else pd.read_csv
        a, b = read(new), read(old)
        # wall-clock time differs from run to run
        a, b = a.drop(columns="solve_s", errors="ignore"), b.drop(columns="solve_s", errors="ignore")
        if a.equals(b):         # fast path; assert_frame_equal takes minutes on 8M rows
            log.info(f"  {logs.rel(old)}: identical ({len(a):,} rows)")
            continue
        try:
            pd.testing.assert_frame_equal(a, b, check_exact=False, rtol=1e-9, check_categorical=False)
            log.info(f"  {logs.rel(old)}: matches within 1e-9 ({len(a):,} rows)")
        except AssertionError as e:
            log.warning(f"  {logs.rel(old)}: DIFFERS: {str(e).splitlines()[0]}")


def write_lineage(out):
    lines = ["# Data lineage (trace run)", "",
             "| Step | Module | Input | Output | Rows | What happened to the structure |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['step']} | `{r['module']}` | {r['input']} | {r['output']} | {r['rows']} | "
              f"{r['change']} |" for r in lineage]
    (out / "data_lineage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + git_tag()
    out = OUTPUTS / "trace" / run_id
    setup(out)
    staging, series_dir, analysis = out / "staging", out / "series", out / "analysis"
    analysis.mkdir()
    series_path = series_dir / "ni_halfhourly.parquet"
    log.info(f"trace run {run_id}; everything is written under {logs.rel(out)}")

    t0 = time.perf_counter()
    cfg = load_config()
    step_config(cfg)
    step_ingest(cfg)
    step_stage(cfg, staging)
    step_series(cfg, staging, series_dir)
    step_reconcile(cfg, series_path, analysis)
    step_calibrate(cfg, staging, series_path, analysis)
    step_peak_shape(cfg, series_path, analysis)
    step_top200(cfg, staging, series_path, analysis)
    step_grid(cfg, series_path, analysis)
    step_observed(cfg, staging, series_path, analysis)
    step_publish(cfg, analysis, out)
    step_figures(cfg, out)
    write_lineage(out)
    compare_with_committed(out)
    log.info("")
    log.info(f"done in {time.perf_counter() - t0:,.0f} s; log {logs.rel(out / 'trace.log')}")


if __name__ == "__main__":
    main()
