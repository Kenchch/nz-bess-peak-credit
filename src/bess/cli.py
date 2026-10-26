"""bess ingest | stage | series | calibrate | grid | analyse | publish | figures | all

Each analysis run writes into outputs/runs/<run_id>/ and ends with DONE.txt
or FAILED.txt (the error, the step it came from and its traceback); run.log in
the same folder holds everything the run logged. `grid` runs calibrate first in
the same folder, so the LP grid never runs past a failed G4; `analyse` (and
`all`) include the grid, about 7 minutes. `publish` checks the latest finished
run against its contracts (gate G6) and swaps it into outputs/current;
`figures` draws the memo figures from outputs/current. `all` runs every step in
that order. Every command is appended to outputs/bess.log.
"""
import argparse
import datetime as dt
import logging
import subprocess
import sys
import time
import traceback

from . import OUTPUTS, ROOT, GateError, load_config, logs

log = logging.getLogger(__name__)

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
    with logs.step("calibrate"):
        return cal.run(cfg, out=run_dir, accept_gap=accept_gap)


def grid(cfg, run_dir, accept_gap):
    from . import run_grid
    calibrate(cfg, run_dir, accept_gap)
    with logs.step("run_grid"):
        run_grid.run(cfg, out=run_dir)
    log.info(f"outputs: {logs.rel(run_dir)}")


def analyse(cfg, run_dir, accept_gap):
    from . import observed, peak_shape, reconcile, run_grid, top200
    with logs.step("reconcile"):
        reconcile.run(cfg, out=run_dir)
    calibrate(cfg, run_dir, accept_gap)
    for name, module in [("peak_shape", peak_shape), ("top200", top200), ("run_grid", run_grid),
                         ("observed", observed)]:
        with logs.step(name):
            module.run(cfg, out=run_dir)
    log.info(f"outputs: {logs.rel(run_dir)}")


def guarded(step, cfg, accept_gap, command=""):
    """Run one analysis step in a fresh run folder: DONE.txt if it finished, FAILED.txt if not.

    Only folders with DONE.txt count as results (the trace compares against them)."""
    run_dir = new_run_dir()
    t0 = time.perf_counter()
    with logs.to_file(run_dir / "run.log"):
        log.info(f"run {run_dir.name}: bess {command or step.__name__}")
        try:
            step(cfg, run_dir, accept_gap)
        except Exception as e:
            where = getattr(e, "bess_step", step.__name__)
            (run_dir / "FAILED.txt").write_text(
                f"{type(e).__name__}: {e}\nstep: {where}\n\n{traceback.format_exc()}",
                encoding="utf-8")
            log.error(f"run {run_dir.name}: FAILED in {where}", exc_info=True)
            e.bess_logged = True
            raise
        (run_dir / "DONE.txt").write_text(f"{step.__name__}\n", encoding="utf-8")
        log.info(f"run {run_dir.name}: DONE in {time.perf_counter() - t0:,.0f} s")
    return run_dir


def main(argv=None):
    p = argparse.ArgumentParser(prog="bess")
    p.add_argument("step", choices=["ingest", "stage", "series", "calibrate", "grid", "analyse",
                                    "publish", "figures", "all"])
    p.add_argument("--config", help="path to config.yaml")
    p.add_argument("--update-manifest", action="store_true",
                   help="ingest: accept files EMI has revised since they were pinned")
    p.add_argument("--accept-gap", metavar="REASON",
                   help="calibrate: carry on past a G4 gap, recording REASON")
    args = sys.argv[1:] if argv is None else list(argv)
    a = p.parse_args(args)
    logs.utf8_streams()
    logs.console()
    logs.capture_warnings()
    cfg = load_config(a.config)
    command = " ".join(args)

    with logs.to_file(OUTPUTS / "bess.log"):
        log.info(f"bess {command}")
        try:
            if a.step in ("ingest", "all"):
                from . import ingest
                with logs.step("ingest"):
                    ingest.run(cfg, update_manifest=a.update_manifest)
            if a.step in ("stage", "all"):
                from . import stage
                with logs.step("stage"):
                    stage.run(cfg)
            if a.step in ("series", "all"):
                from . import series
                with logs.step("series"):
                    series.run(cfg)
            if a.step == "calibrate":
                guarded(calibrate, cfg, a.accept_gap, command)
            if a.step == "grid":
                guarded(grid, cfg, a.accept_gap, command)
            if a.step in ("analyse", "all"):
                guarded(analyse, cfg, a.accept_gap, command)
            if a.step in ("publish", "all"):
                from . import publish
                with logs.step("publish"):
                    publish.run(cfg)
            if a.step in ("figures", "all"):
                from . import figures
                with logs.step("figures"):
                    figures.run(cfg)
        except GateError as e:
            log.error(f"STOPPED at gate: {e}")
            return 2
        except Exception as e:
            if not getattr(e, "bess_logged", False):
                log.error(f"bess {command}: crashed", exc_info=True)
            raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
