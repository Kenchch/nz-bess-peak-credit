"""bess ingest | stage | series | calibrate | analyse | all

Each analysis run writes into outputs/runs/<run_id>/. Publishing that folder to
outputs/current (gate G6) comes with publish.py.
"""
import argparse
import datetime as dt
import subprocess
import sys

from . import OUTPUTS, ROOT, GateError, load_config


def new_run_dir():
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sha = "nogit"
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + sha
    path = OUTPUTS / "runs" / run_id
    path.mkdir(parents=True)
    return path


def calibrate(cfg, run_dir, accept_gap):
    from . import calibrate as cal
    return cal.run(cfg, out=run_dir, accept_gap=accept_gap)


def analyse(cfg, run_dir, accept_gap):
    from . import peak_shape, reconcile, top200
    reconcile.run(cfg, out=run_dir)
    calibrate(cfg, run_dir, accept_gap)
    peak_shape.run(cfg, out=run_dir)
    top200.run(cfg, out=run_dir)
    print(f"outputs: {run_dir}")


def guarded(step, cfg, accept_gap):
    """Run one analysis step in a fresh run folder; mark the folder if a gate stops it."""
    run_dir = new_run_dir()
    try:
        step(cfg, run_dir, accept_gap)
    except GateError as e:
        (run_dir / "FAILED.txt").write_text(f"{e}\n", encoding="utf-8")
        raise


def main(argv=None):
    p = argparse.ArgumentParser(prog="bess")
    p.add_argument("step", choices=["ingest", "stage", "series", "calibrate", "analyse", "all"])
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
        if a.step in ("analyse", "all"):
            guarded(analyse, cfg, a.accept_gap)
    except GateError as e:
        print(f"STOPPED at gate: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
