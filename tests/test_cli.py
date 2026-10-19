"""bess grid: calibrate gates the grid, and every run folder ends DONE or FAILED."""
import pytest

from bess import GateError, calibrate, cli, run_grid


@pytest.fixture
def runs(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "OUTPUTS", tmp_path)
    monkeypatch.setattr(cli, "load_config", lambda path=None: {})
    return tmp_path / "runs"


def test_grid_stops_at_g4_and_marks_the_folder(runs, monkeypatch):
    calls = []

    def fail(cfg, out, accept_gap):
        calls.append("calibrate")
        raise GateError("G4: fake gap")

    monkeypatch.setattr(calibrate, "run", fail)
    monkeypatch.setattr(run_grid, "run", lambda *a, **k: calls.append("grid"))
    assert cli.main(["grid"]) == 2
    assert calls == ["calibrate"]
    (folder,) = runs.iterdir()
    assert (folder / "FAILED.txt").read_text(encoding="utf-8").startswith("GateError: G4")
    assert not (folder / "DONE.txt").exists()


def test_grid_marks_a_finished_run_done(runs, monkeypatch):
    monkeypatch.setattr(calibrate, "run", lambda cfg, out, accept_gap: None)
    monkeypatch.setattr(run_grid, "run", lambda cfg, out: None)
    assert cli.main(["grid"]) == 0
    (folder,) = runs.iterdir()
    assert (folder / "DONE.txt").exists() and not (folder / "FAILED.txt").exists()


def test_any_crash_is_marked_failed(runs, monkeypatch):
    monkeypatch.setattr(calibrate, "run", lambda cfg, out, accept_gap: None)
    monkeypatch.setattr(run_grid, "run", lambda cfg, out: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        cli.main(["grid"])
    (folder,) = runs.iterdir()
    assert "ZeroDivisionError" in (folder / "FAILED.txt").read_text(encoding="utf-8")
