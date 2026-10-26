"""G1 (pinned inputs), G2 (reject rate) and G4 (calibration) stop the run."""
import pandas as pd
import pytest

from bess import GateError, calibrate, ingest, load_config, stage
from synth import write_raw


def test_g1_stops_when_upstream_file_changed(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "RAW", tmp_path)
    def download(url, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"revised")
    monkeypatch.setattr(ingest, "download", download)
    target = ("generation_md", "202104_Generation_MD.csv", "https://example.invalid/x.csv")
    pin = {"sha256": "0" * 64, "downloaded": "2026-10-18"}
    with pytest.raises(GateError, match="G1"):
        ingest.fetch(target, pin)
    row, status = ingest.fetch(target, pin, update_manifest=True)
    assert status == "downloaded" and row["sha256"] == ingest.sha256(tmp_path / target[0] / target[1])


def test_g1_keeps_a_file_that_matches_its_pin(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "RAW", tmp_path)
    monkeypatch.setattr(ingest, "download", lambda url, dest: pytest.fail("should not download"))
    path = tmp_path / "nsp" / "a.csv"
    path.parent.mkdir()
    path.write_text("x")
    pin = {"sha256": ingest.sha256(path), "downloaded": "2026-10-18"}
    assert ingest.fetch(("nsp", "a.csv", "u"), pin) == (pin, "kept")


@pytest.fixture
def raw(tmp_path):
    write_raw(tmp_path / "raw", "2024-06", "2024-06")
    return tmp_path


CFG = {**load_config(), "period": {"start": "2024-06", "end": "2024-06"}}


def test_g2_stops_above_reject_limit_and_keeps_old_tables(raw, caplog):
    out = raw / "staging"
    stage.run(CFG, raw=raw / "raw", out=out)
    assert ("stage: hvdc: 2 POCs are not NSPs and were placed on an island by site code: "
            "['BEN2201', 'HAY2201']") in caplog.text
    before = (out / "generation.parquet").read_bytes()

    path = raw / "raw" / "generation_md" / "202406_Generation_MD.csv"
    df = pd.read_csv(path)
    df.loc[df.index[:5], "TP3"] = -1.0                 # 5 of 150 rows: 3.3% > 1%
    df.to_csv(path, index=False)
    with pytest.raises(GateError, match="G2"):
        stage.run(CFG, raw=raw / "raw", out=out)
    assert (out / "generation.parquet").read_bytes() == before
    report = pd.read_csv(out / "quality_report.csv")
    assert report.query("source == 'generation' and file == 'ALL'").r3_negative_kwh.item() == 5


def test_g4_stops_on_a_calibration_gap_unless_accepted(raw):
    from bess import series
    stage.run(CFG, raw=raw / "raw", out=raw / "staging")
    series.run(CFG, staging=raw / "staging", out=raw / "series")
    cfg = {**CFG, "sosa_reference": {**CFG["sosa_reference"],
                                     "wind_cf": {2024: 0.90}, "solar_cf": {2024: 0.50}}}
    paths = dict(staging=raw / "staging", series_path=raw / "series" / "ni_halfhourly.parquet",
                 fleet_path=next((raw / "raw" / "fleet").iterdir()),
                 nsp_path=next((raw / "raw" / "nsp").iterdir()))
    with pytest.raises(GateError, match="G4"):
        calibrate.run(cfg, out=raw, **paths)
    calibrate.run(cfg, out=raw, accept_gap="synthetic test", **paths)
    assert "accepted: synthetic test" in (raw / "calibration.md").read_text(encoding="utf-8")
