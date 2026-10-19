"""bess ingest | stage | series | calibrate | grid | analyse | all

Each analysis run writes into outputs/runs/<run_id>/ and ends with DONE.txt
or FAILED.txt. `grid` runs calibrate first in the same folder, so the LP grid
never runs past a failed G4; `analyse` (and `all`) include the grid, about
7 minutes.
Publishing a run folder to outputs/current (gate G6) comes with publish.py.
"""
import argparse
import datetime as dt
import subprocess
import sys

from . import OUTPUTS, ROOT, GateError, load_config


# Anything that can change a result: an edit here (tracked or new) marks the run -dirty
CODE_PATHS = ["src", "scripts", "config.yaml", "pyproject.toml", "uv.lock"]


def git_tag():
    """Short git sha, with -dirty when CODE_PATHS have uncommitted or untracked changes."""
    try:
        git = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True,
                                        check=True).stdout.strip()
        sha = git("rev-parse", "--short", "HEAD")
        if git("status", "--porcelain", "--untracked-files=all", "--", *CODE_PATHS):
            sha += "-dirty"
        return sha
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def new_run_dir():
    """outputs/runs/<UTC time>_<git tag>."""
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + git_tag()
    path = OUTPUTS / "runs" / run_id
    path.mkdir(parents=True)
    return path


def calibrate(cfg, run_dir, accept_gap):
    from . import calibrate as cal
    return cal.run(cfg, out=run_dir, accept_gap=accept_gap)


def grid(cfg, run_dir, accept_gap):
    from . import run_grid
    calibrate(cfg, run_dir, accept_gap)
    run_grid.run(cfg, out=run_dir)
    print(f"outputs: {run_dir}")


def analyse(cfg, run_dir, accept_gap):
    from . import peak_shape, reconcile, run_grid, top200
    reconcile.run(cfg, out=run_dir)
    calibrate(cfg, run_dir, accept_gap)
    peak_shape.run(cfg, out=run_dir)
    top200.run(cfg, out=run_dir)
    run_grid.run(cfg, out=run_dir)
    print(f"outputs: {run_dir}")


def guarded(step, cfg, accept_gap):
    """Run one analysis step in a fresh run folder: DONE.txt if it finished, FAILED.txt if not.

    Only folders with DONE.txt count as results (the trace compares against them)."""
    run_dir = new_run_dir()
    try:
        step(cfg, run_dir, accept_gap)
    except Exception as e:
        (run_dir / "FAILED.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
        raise
    (run_dir / "DONE.txt").write_text(f"{step.__name__}\n", encoding="utf-8")
    return run_dir


def main(argv=None):
    p = argparse.ArgumentParser(prog="bess")
    p.add_argument("step", choices=["ingest", "stage", "series", "calibrate", "grid", "analyse",
                                    "all"])
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--update-manifest", action="store_true",
                   help="ingest: accept files EMI has revised since they were pinned")
    p.add_argument("--accept-gap", metavar="REASON",
                   help="calibrate: carry on past a G4 gap, recording REASON")
    a = p.parse_args(argv)
    cfg = load_config(a.config)

    try:
        if a.step in ("ingest", "all"):
            from . import ingest
            ingest.run(cfg, update_manifest=a.update_manifest)
        if a.step in ("stage", "all"):
            from . import stage
            stage.run(cfg)
        if a.step in ("series", "all"):
            from . import series
            series.run(cfg)
        if a.step == "calibrate":
            guarded(calibrate, cfg, a.accept_gap)
        if a.step == "grid":
            guarded(grid, cfg, a.accept_gap)
        if a.step in ("analyse", "all"):
            guarded(analyse, cfg, a.accept_gap)
    except GateError as e:
        print(f"STOPPED at gate: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
