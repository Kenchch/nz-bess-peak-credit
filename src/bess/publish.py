"""Gate G6: check a finished run against its contracts, then publish it to outputs/current.

A run folder is publishable when it has DONE.txt, no FAILED.txt, every file
in CONTRACT with at least the listed columns, every LP optimal and through G5,
and a dispatch table of the expected size. Publishing copies the folder into
outputs/current.tmp, adds run_manifest.json (what produced it) and two small
dimension tables for Power BI, then swaps it into place: a reader of
outputs/current sees the previous version in full or the new one in full.
"""
import datetime as dt
import hashlib
import json
import platform
import shutil
import subprocess

import pandas as pd

from . import DATA, OUTPUTS, ROOT, GateError, load_config
from .run_grid import COLUMNS as CREDIT_COLUMNS

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


def manifest(run_dir, cfg):
    def git(*a):
        try:
            return subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                                  check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

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


def run(cfg=None, run_dir=None, current=CURRENT):
    cfg = cfg or load_config()
    run_dir = run_dir or latest_done()
    found = problems(run_dir, cfg)
    if found:
        raise GateError(f"G6: {run_dir.name} not published: " + "; ".join(found))
    tmp = current.with_name(current.name + ".tmp")
    old = current.with_name(current.name + ".old")
    for p in (tmp, old):
        if p.exists():
            shutil.rmtree(p)
    shutil.copytree(run_dir, tmp)
    for name, table in dimensions(cfg).items():
        table.to_csv(tmp / name, index=False)
    (tmp / "run_manifest.json").write_text(
        json.dumps(manifest(run_dir, cfg), indent=2) + "\n", encoding="utf-8")
    if current.exists():
        current.rename(old)
    tmp.rename(current)
    if old.exists():
        shutil.rmtree(old)
    print(f"publish (G6): {run_dir.name} -> {current}")
    return current
