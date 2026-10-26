"""What each step leaves behind in its logs, and that logging survives a cp1252 pipe."""
import logging
import os
import subprocess
import sys

import pandas as pd
import pytest

from bess import GateError, ROOT, calibrate, cli, ingest, logs, publish, run_grid

NAME = "Ruakākā"


def python(code, **env):
    """Run code in a fresh interpreter with stdout and stderr piped, as `bess ... > file` is."""
    env = {**{k: v for k, v in os.environ.items() if k != "PYTHONUTF8"}, **env}
    return subprocess.run([sys.executable, "-c", code], capture_output=True, env=env)


def test_a_cp1252_pipe_can_print_station_names():
    shout = (f"logs.console(); logging.getLogger('bess.observed').info('observed: {NAME}'); "
             f"print('{NAME}')")
    broken = python(f"import logging; from bess import logs; {shout}", PYTHONIOENCODING="cp1252")
    assert broken.returncode != 0 and b"UnicodeEncodeError" in broken.stderr
    fixed = python(f"import logging; from bess import logs; logs.utf8_streams(); {shout}",
                   PYTHONIOENCODING="cp1252")
    assert fixed.returncode == 0, fixed.stderr.decode("utf-8", "replace")
    assert fixed.stdout.decode("utf-8").strip() == NAME
    assert f"INFO     observed: {NAME}" in fixed.stderr.decode("utf-8")


def test_paths_in_logs_are_relative_to_the_repository(tmp_path):
    assert logs.rel(ROOT / "data" / "series" / "ni.parquet") == "data/series/ni.parquet"
    assert logs.rel(tmp_path / "x") == str(tmp_path / "x")


def test_console_is_added_once():
    logs.console()
    logs.console()
    assert sum(isinstance(h, logs._Console) for h in logs.log.handlers) == 1


# ------------------------------------------------------------------ cli


@pytest.fixture
def outputs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUTPUTS", tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda path=None: {})
    return tmp_path


def read(path):
    return path.read_text(encoding="utf-8")


def test_run_log_keeps_every_step_with_its_level(outputs, monkeypatch):
    def accept(cfg, out, accept_gap):
        logging.getLogger("bess.calibrate").warning(f"calibrate (G4): gap accepted: {accept_gap}")

    monkeypatch.setattr(calibrate, "run", accept)
    monkeypatch.setattr(run_grid, "run", lambda cfg, out: None)
    assert cli.main(["grid", "--accept-gap", "known cause"]) == 0
    (folder,) = (outputs / "runs").iterdir()
    text = read(folder / "run.log")
    assert f"run {folder.name}: bess grid --accept-gap known cause" in text
    assert "calibrate: start" in text and "run_grid: done in" in text
    assert "WARNING  calibrate (G4): gap accepted: known cause" in text
    assert f"run {folder.name}: DONE" in text
    assert "INFO     bess grid --accept-gap known cause" in read(outputs / "bess.log")


def test_failed_txt_names_the_step_and_keeps_the_traceback(outputs, monkeypatch):
    monkeypatch.setattr(calibrate, "run", lambda cfg, out, accept_gap: None)
    monkeypatch.setattr(run_grid, "run", lambda cfg, out: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        cli.main(["grid"])
    (folder,) = (outputs / "runs").iterdir()
    failed = read(folder / "FAILED.txt")
    assert failed.startswith("ZeroDivisionError: division by zero\nstep: run_grid\n")
    assert "Traceback (most recent call last)" in failed and "1 / 0" in failed
    run_log = read(folder / "run.log")
    assert "ERROR    run_grid: failed after" in run_log and "Traceback" in run_log
    assert read(outputs / "bess.log").count("Traceback (most recent call last)") == 1


def test_steps_outside_a_run_folder_log_their_stop_and_their_crash(outputs, monkeypatch):
    streams = []
    monkeypatch.setattr(logs, "utf8_streams", lambda: streams.append(True))

    def refuse(cfg):
        raise GateError("G6: nothing to publish")

    monkeypatch.setattr(publish, "run", refuse)
    assert cli.main(["publish"]) == 2
    assert streams == [True]
    assert "ERROR    STOPPED at gate: G6: nothing to publish" in read(outputs / "bess.log")

    monkeypatch.setattr(publish, "run", lambda cfg: {}["missing"])
    with pytest.raises(KeyError):
        cli.main(["publish"])
    text = read(outputs / "bess.log")
    assert "ERROR    publish: failed after" in text and "bess publish: crashed" in text
    assert "KeyError: 'missing'" in text and "Traceback" in text


# ------------------------------------------------------------------ ingest


def records(caplog, name):
    return [(r.levelname, r.getMessage()) for r in caplog.records if r.name == name]


def test_every_download_retry_is_logged(tmp_path, monkeypatch, caplog):
    def refuse(req, timeout):
        raise OSError("connection reset")

    monkeypatch.setattr(ingest.urllib.request, "urlopen", refuse)
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    with pytest.raises(OSError):
        ingest.download("https://example.invalid/x.csv", tmp_path / "x.csv")
    got = records(caplog, "bess.ingest")
    assert [level for level, _ in got] == ["WARNING", "WARNING", "ERROR"]
    assert got[0][1] == ("ingest: https://example.invalid/x.csv: attempt 1/3 failed "
                         "(connection reset); retrying in 2 s")
    assert got[2][1].endswith("attempt 3/3 failed (connection reset); giving up")


def test_a_revised_file_logs_its_old_and_new_hash(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ingest, "RAW", tmp_path)

    def download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"revised")

    monkeypatch.setattr(ingest, "download", download)
    target = ("generation_md", "202104_Generation_MD.csv", "https://example.invalid/x.csv")
    pin = {"sha256": "0" * 64, "downloaded": "2026-10-18"}
    with pytest.raises(GateError, match="G1"):
        ingest.fetch(target, pin)
    new = ingest.sha256(tmp_path / target[0] / target[1])
    assert [m for _, m in records(caplog, "bess.ingest")] == [
        f"ingest: {target[1]}: download {i} has sha256 {new}, pin is {'0' * 64}" for i in (1, 2)]

    caplog.clear()
    ingest.fetch(target, pin, update_manifest=True)
    assert [m for _, m in records(caplog, "bess.ingest")] == [
        f"ingest: {target[1]}: the copy on disk does not match its pin; downloading",
        f"ingest: {target[1]}: accepted as revised by EMI (--update-manifest): "
        f"sha256 {'0' * 64} -> {new}"]


def test_ingest_appends_to_its_own_log(tmp_path, monkeypatch):
    path = tmp_path / "ingest.log"
    monkeypatch.setattr(ingest, "INGEST_LOG", path)
    monkeypatch.setattr(ingest, "targets", lambda cfg: [("nsp", "a.csv", "u")])
    monkeypatch.setattr(ingest, "read_manifest", lambda: {})
    monkeypatch.setattr(ingest, "write_manifest", lambda pins: None)
    monkeypatch.setattr(ingest, "fetch",
                        lambda t, pin, update: ({"dataset": "nsp", "file": "a.csv"}, "kept"))
    ingest.run({}, workers=1)
    ingest.run({}, update_manifest=True, workers=1)
    text = read(path)
    assert text.count("ingest: 1 files {'kept': 1}; manifest data/manifest.csv") == 2
    assert "ingest: start with --update-manifest" in text


# ------------------------------------------------------------------ publish


def test_gate_warnings_travel_with_the_published_run(tmp_path):
    pd.DataFrame({"month": ["2024-06", "2024-07"], "in_band": [True, False]}).to_csv(
        tmp_path / "reconcile.csv", index=False)
    pd.DataFrame({"year": [2024, 2024], "tech": ["wind", "solar"], "diff_pp": [1.6, 3.49],
                  "primary": [True, True], "within_tol": [True, False]}).to_csv(
        tmp_path / "calibration.csv", index=False)
    assert publish.gate_warnings(tmp_path) == [
        "G3: 1 of 2 months outside 6%-11%: ['2024-07']",
        "G4: 2024 solar gap +3.49 pp accepted with --accept-gap; see calibration.md"]
