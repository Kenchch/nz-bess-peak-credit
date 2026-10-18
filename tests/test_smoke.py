"""A synthetic winter through stage -> series -> reconcile -> calibrate -> analysis."""
import pandas as pd
import pytest
from pandas.api import types

from bess import calibrate, load_config, peak_shape, reconcile, series, stage, top200
from synth import write_raw

CFG = {**load_config(), "period": {"start": "2024-06", "end": "2024-07"},
       "sosa_reference": {**load_config()["sosa_reference"],
                          "wind_cf": {2024: 0.31}, "solar_cf": {2024: 0.0}}}
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
                 "peak_shape.png", "top200_summary.csv"]:
        assert (out / name).stat().st_size > 0, name


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
