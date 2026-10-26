"""Gate G6: check a finished run against its contracts, then publish it to outputs/current.

A run folder is publishable when it has DONE.txt, no FAILED.txt, every file
in CONTRACT with at least the listed columns, every LP optimal and through G5,
and a dispatch table of the expected size. Publishing copies the folder into
outputs/current.tmp, adds run_manifest.json (what produced it) and the
Power BI helpers (two small dimension tables, and pbi_dispatch.parquet: the
dispatch with naive New Zealand local time, because Power Query cannot apply
NZ daylight saving to a UTC timestamp), then swaps it into place: a reader of
outputs/current sees the previous version in full or the new one in full.

run_manifest.json lists the warnings the run passed with (G3 months outside
the band, a G4 gap accepted with --accept-gap), so they travel with the
published numbers. Each publish overwrites the manifest, so every publish,
every refusal and every publish that failed part-way is also appended to
outputs/publish_log.csv.
"""
import csv
import datetime as dt
import hashlib
import json
import logging
import platform
import shutil
import subprocess

import pandas as pd

from . import DATA, OUTPUTS, ROOT, GateError, load_config, logs
from .reconcile import BAND
from .run_grid import COLUMNS as CREDIT_COLUMNS

log = logging.getLogger(__name__)

DISPATCH = ["ts_local", "winter", "basis", "p_mw", "load_mw", "battery_mw", "load_after_mw"]
CONTRACT = {
    "credit_table.csv": CREDIT_COLUMNS,
    "crossings.csv": ["basis", "window", "sensitivity", "d_h", "sosa_factor", "winters",
                      "n_winters", "stat", "p_star_mw", "p_star_note", "worst_winter"],
    "dispatch_d2.parquet": DISPATCH,
    "calibration.csv": ["year", "tech", "demand", "selection", "fleet", "cf", "sosa_cf",
                        "diff_pp", "primary", "within_tol"],
    "peak_shape.csv": ["winter", "basis", "window", "days", "delta_mw", "stat", "duration_h",
                       "battery_h", "n_days"],
    "top200_summary.csv": ["winter", "basis", "overlap", "h100_mw", "wind_cf", "solar_cf"],
    "reconcile.csv": ["month", "supply_gwh", "offtake_gwh", "gap_share", "in_band"],
    "observed_vs_ceiling.csv": ["winter", "units_counted", "nameplate_mw", "observed_factor",
                                "ceiling_availability"],
}
CURRENT = OUTPUTS / "current"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read(path):
    return pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)


def latest_done(runs=OUTPUTS / "runs"):
    done = sorted(p for p in runs.iterdir() if (p / "DONE.txt").exists()) if runs.is_dir() else []
    if not done:
        raise GateError(f"G6: no finished run (with DONE.txt) under {runs}")
    return done[-1]


def problems(run_dir, cfg):
    """Every way the run folder breaks its contracts; empty when it may be published."""
    found = []
    if not (run_dir / "DONE.txt").exists():
        found.append("no DONE.txt")
    if (run_dir / "FAILED.txt").exists():
        found.append("FAILED.txt present")
    tables = {}
    for name, cols in CONTRACT.items():
        path = run_dir / name
        if not path.exists():
            found.append(f"{name} missing")
            continue
        t = tables[name] = read(path)
        missing = [c for c in cols if c not in t.columns]
        if missing:
            found.append(f"{name} lacks columns {missing}")
        if t.empty:
            found.append(f"{name} is empty")
    ct = tables.get("credit_table.csv")
    if ct is not None and set(CREDIT_COLUMNS) <= set(ct.columns):
        if not (ct.status.eq("optimal").all() and ct.g5_ok.astype(bool).all()):
            found.append(f"credit_table has {int((~ct.g5_ok.astype(bool)).sum())} LPs failing G5")
    d = tables.get("dispatch_d2.parquet")
    if d is not None and ct is not None and set(DISPATCH) <= set(d.columns):
        expected = (ct[(ct.d_h == 2) & (ct.sensitivity == "base") & (ct.window == "all")]
                    .groupby(["winter"]).size())
        if d.groupby(["basis", "p_mw"]).size().nunique() != 1:
            found.append("dispatch_d2 has uneven series per basis and P")
        if len(d.groupby(["winter", "basis", "p_mw"]).size()) != expected.sum():
            found.append("dispatch_d2 does not hold one series per saved LP")
    return found


def git(*a):
    try:
        return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def gate_warnings(run_dir):
    """The gates the run passed with a warning or an override."""
    found = []
    rec = pd.read_csv(run_dir / "reconcile.csv")
    out_of_band = rec[~rec.in_band.astype(bool)]
    if len(out_of_band):
        found.append(f"G3: {len(out_of_band)} of {len(rec)} months outside "
                     f"{BAND[0]:.0%}-{BAND[1]:.0%}: {out_of_band.month.tolist()}")
    cal = pd.read_csv(run_dir / "calibration.csv")
    gaps = cal[cal.primary.astype(bool) & ~cal.within_tol.astype(bool)]
    for r in gaps.itertuples():
        found.append(f"G4: {r.year} {r.tech} gap {r.diff_pp:+.2f} pp accepted with "
                     "--accept-gap; see calibration.md")
    return found


def record(current, run_id, result, detail=""):
    """Append one line to publish_log.csv next to outputs/current."""
    path = current.parent / "publish_log.csv"
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["at_utc", "run_id", "git_head", "result", "detail"])
        w.writerow([dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), run_id,
                    git("rev-parse", "--short", "HEAD"), result, detail])


def manifest(run_dir, cfg):
    inputs = pd.read_csv(DATA / "manifest.csv")
    crossings = pd.read_csv(run_dir / "crossings.csv")
    head = crossings[(crossings.sensitivity == "base") & (crossings.window == "all")
                     & (crossings.d_h == 2) & (crossings.winters == "base")]
    files = {}
    for p in sorted(run_dir.iterdir()):
        if p.is_file():
            entry = {"bytes": p.stat().st_size, "sha256": sha256(p)}
            if p.suffix in (".csv", ".parquet"):
                entry["rows"] = len(read(p))
            files[p.name] = entry
    return {
        "run_id": run_dir.name,
        "code_tag": run_dir.name.partition("_")[2] or None,
        "published_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "git_head_at_publish": git("rev-parse", "--short", "HEAD"),
        "config_sha256": sha256(ROOT / "config.yaml"),
        "inputs": {"files": len(inputs), "bytes": int(inputs.bytes.sum()),
                   "manifest_sha256": sha256(DATA / "manifest.csv")},
        "python": platform.python_version(),
        "headline_p_star_mw": {f"{r.basis}_{r.stat}": (None if pd.isna(r.p_star_mw)
                                                       else round(float(r.p_star_mw), 1))
                               for r in head.itertuples()},
        "warnings": gate_warnings(run_dir),
        "files": files,
    }


def dimensions(cfg):
    """Small lookup tables the Power BI model joins on."""
    base, check = cfg["credit"]["base_winters"], cfg["credit"]["check_winters"]
    winters = pd.DataFrame({"winter": sorted(base + check)})
    winters["winter_set"] = winters.winter.map(lambda w: "base" if w in base else "check")
    winters["label"] = winters.apply(
        lambda r: f"{r.winter}" + (" (check)" if r.winter_set == "check" else ""), axis=1)
    fleet = pd.DataFrame({"p_mw": cfg["grid"]["p_mw"]})
    fleet["label"] = fleet.p_mw.map(lambda p: f"{p:,} MW")
    return {"dim_winter.csv": winters, "dim_p.csv": fleet}


def pbi_dispatch(run_dir):
    """dispatch_d2 with local wall-clock time, date and trading period for Power BI."""
    d = pd.read_parquet(run_dir / "dispatch_d2.parquet")
    local = pd.to_datetime(d.ts_local, utc=True).dt.tz_convert("Pacific/Auckland")
    out = d.drop(columns="ts_local")
    out.insert(0, "ts_local", local.dt.tz_localize(None))
    out.insert(1, "date", local.dt.date)
    out.insert(2, "time_of_day", local.dt.strftime("%H:%M"))
    return out


def run(cfg=None, run_dir=None, current=CURRENT):
    cfg = cfg or load_config()
    try:
        run_dir = run_dir or latest_done()
    except GateError as e:
        record(current, "", "refused", str(e))
        raise
    found = problems(run_dir, cfg)
    if found:
        record(current, run_dir.name, "refused", "; ".join(found))
        raise GateError(f"G6: {run_dir.name} not published: " + "; ".join(found))
    tmp = current.with_name(current.name + ".tmp")
    old = current.with_name(current.name + ".old")
    try:
        for p in (tmp, old):
            if p.exists():
                shutil.rmtree(p)
        shutil.copytree(run_dir, tmp)
        for name, table in dimensions(cfg).items():
            table.to_csv(tmp / name, index=False)
        pbi_dispatch(run_dir).to_parquet(tmp / "pbi_dispatch.parquet", index=False)
        (tmp / "run_manifest.json").write_text(
            json.dumps(manifest(run_dir, cfg), indent=2) + "\n", encoding="utf-8")
        if current.exists():
            current.rename(old)
        tmp.rename(current)
        if old.exists():
            shutil.rmtree(old)
    except Exception as e:               # passed G6 but did not land: say so in the history
        record(current, run_dir.name, "failed", f"{type(e).__name__}: {e}")
        raise
    passed_with = gate_warnings(current)
    record(current, run_dir.name, "published", "; ".join(passed_with))
    log.info(f"publish (G6): {run_dir.name} -> {logs.rel(current)}")
    for w in passed_with:
        log.warning(f"publish: passed with {w}")
    return current
