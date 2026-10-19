"""A synthetic winter through stage -> series -> reconcile -> calibrate -> analysis -> grid."""
import pandas as pd
import pytest
from pandas.api import types

from bess import calibrate, load_config, peak_shape, reconcile, run_grid, series, stage, top200
from synth import write_raw

CFG = {**load_config(), "period": {"start": "2024-06", "end": "2024-07"},
       "sosa_reference": {**load_config()["sosa_reference"],
                          "wind_cf": {2024: 0.31}, "solar_cf": {2024: 0.0}}}
# A small grid: base 2 P x 2 d x 2 bases x 2 windows = 16, usable70 8, solar3x 4, top50 4 = 32 LPs
GRID_CFG = {**CFG, "grid": {"p_mw": [35, 335], "d_h": [2, 4], "basis": ["gross", "net"],
                            "window": ["all", "PM"]},
            "credit": {**CFG["credit"], "base_winters": [2024], "check_winters": [], "workers": 1}}
CONTRACT = {
    "ts_local": lambda c: isinstance(c.dtype, pd.DatetimeTZDtype), "tp": types.is_integer_dtype,
    "winter": types.is_integer_dtype, "winter_complete": types.is_bool_dtype,
    "period": types.is_string_dtype, "in_wcm_window": types.is_bool_dtype,
    **{c: types.is_float_dtype for c in ["gen_ni_mw", "hvdc_north_mw", "wind_mw", "solar_mw",
                                          "bess_mw", "l_gross_mw", "l_net_mw"]},
}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("smoke")
    raw = tmp / "raw"
    write_raw(raw, "2024-06", "2024-07")
    stage.run(CFG, raw=raw, out=tmp / "staging")
    series.run(CFG, staging=tmp / "staging", out=tmp / "series")
    out = tmp / "run"
    out.mkdir()
    sp = tmp / "series" / "ni_halfhourly.parquet"
    paths = dict(staging=tmp / "staging", series_path=sp,
                 fleet_path=next((raw / "fleet").iterdir()), nsp_path=next((raw / "nsp").iterdir()))
    reconcile.run(CFG, series_path=sp, raw=raw, out=out)
    calibrate.run(CFG, out=out, accept_gap="synthetic data", **paths)
    peak_shape.run(CFG, series_path=sp, out=out)
    top200.run(CFG, out=out, **paths)
    run_grid.run(GRID_CFG, series_path=sp, out=out)
    return sp, out


def test_series_meets_contract(run):
    sp, _ = run
    s = pd.read_parquet(sp)
    assert list(s.columns) == series.COLUMNS
    for col, check in CONTRACT.items():
        assert check(s[col]), col
    assert isinstance(s.trading_date.iloc[0], type(pd.Timestamp("2024-06-01").date()))
    assert s.l_gross_mw.notna().all()


def test_every_output_is_written(run):
    _, out = run
    for name in ["reconcile.csv", "calibration.csv", "calibration.md", "peak_shape.csv",
                 "peak_shape.png", "top200_summary.csv", "credit_table.csv", "crossings.csv",
                 "dispatch_d2.parquet", "run_grid.log"]:
        assert (out / name).stat().st_size > 0, name


def test_grid_meets_its_contract(run):
    _, out = run
    t = pd.read_csv(out / "credit_table.csv")
    assert list(t.columns) == run_grid.COLUMNS
    assert len(t) == 32 and t.status.eq("optimal").all() and t.g5_ok.all()
    assert set(t.sensitivity) == {"base", "usable70", "solar3x", "top50"}
    assert set(t[t.sensitivity == "top50"].k) == {50} and set(t[t.sensitivity == "top50"].window) == {"all"}
    # delivered convention: the store holds rated / sqrt(eta)
    assert (t.e_stored_mwh - t.e_mwh / 0.88 ** 0.5).abs().max() < 1e-9
    assert t.cc_availability.notna().sum() == 4          # d = 2 h, base, window all: 2 P x 2 bases
    assert set(t[t.sensitivity == "solar3x"].basis) == {"net"}
    assert t.cc.between(0, 1).all()
    smallest = t[t.p_mw == 35]
    assert smallest.cc_ratio.round(12).eq(1).all()
    # usable70 has 70% of the energy, so it can never beat the base case
    two = t[t.d_h == 2].pivot_table(index=["basis", "window", "p_mw"], columns="sensitivity",
                                    values="cc")
    assert (two.usable70 <= two.base + 1e-6).all()
    x = pd.read_csv(out / "crossings.csv")
    assert set(x.winters) == {"base", "2024"} and set(x.stat) == {"mean", "single", "min_of_winters"}
    assert set(x[x.d_h == 4].sosa_factor) == {0.8}
    assert x.p_star_note.isin(["in_grid", "below_grid", "above_grid"]).all()
    d = pd.read_parquet(out / "dispatch_d2.parquet")
    assert set(d.p_mw) == {35, 335} and set(d.basis) == {"gross", "net"}
    assert (d.load_after_mw - (d.load_mw - d.battery_mw)).abs().max() < 1e-9
    hour = pd.to_datetime(d.ts_local, utc=True).dt.tz_convert("Pacific/Auckland").dt.hour
    assert d.battery_mw[(hour < 7) | (hour >= 22)].max() < 1e-6   # tidy: no discharge off-window
    log = (out / "run_grid.log").read_text(encoding="utf-8")
    assert "[  32/32]" in log and "G5 checks" in log and "monotone violations 0" in log
    assert "duration order (1 h <= usable70 <= 2 h <= 4 h): violations 0" in log


def test_more_workers_give_the_same_table(run, tmp_path):
    sp, out = run
    small = {**GRID_CFG, "grid": {**GRID_CFG["grid"], "d_h": [2], "window": ["all"]},
             "sensitivities": {}}
    a, _ = run_grid.run(small, series_path=sp, out=None, workers=1)
    b, _ = run_grid.run(small, series_path=sp, out=None, workers=2)
    cols = [c for c in a.columns if c != "solve_s"]
    pd.testing.assert_frame_equal(a[cols], b[cols])


def test_reconcile_sees_the_synthetic_loss(run):
    _, out = run
    rec = pd.read_csv(out / "reconcile.csv")
    assert rec.gap_share.round(6).eq(0.08).all()


def test_outputs_have_expected_columns(run):
    _, out = run
    assert {"winter", "basis", "window", "days", "delta_mw", "stat", "duration_h", "battery_h",
            "n_days"} == set(pd.read_csv(out / "peak_shape.csv").columns)
    top = pd.read_csv(out / "top200_summary.csv")
    assert set(top.basis) == {"gross", "net"} and top.n.eq(200).all()
    assert top.overlap.between(0, 1).all()
    cal = pd.read_csv(out / "calibration.csv")
    assert cal.primary.sum() == 2
