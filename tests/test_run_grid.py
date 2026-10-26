"""run_grid: the load it builds, the G5 checks, and the p* interpolation."""
import numpy as np
import pandas as pd
import pytest

from bess import GateError, load_config, run_grid
from bess.credit import min_top_k_no_new_peaks

CFG = load_config()


def test_plan_counts_base_and_sensitivities():
    # Per winter: base 7 P x 3 d x 2 bases x 3 windows = 126; usable70 7 x 2 x 3 = 42;
    # solar3x net only 7 x 3 = 21; top50 window all only 7 x 2 = 14
    jobs = run_grid.plan(CFG, [2023, 2024])
    count = pd.Series([j[3] for j in jobs]).value_counts().to_dict()
    assert count == {"base": 252, "usable70": 84, "solar3x": 42, "top50": 28}
    assert {j[5] for j in jobs if j[3] != "base"} == {2}
    assert {j[2] for j in jobs if j[3] == "top50"} == {"all"}
    assert run_grid.job_k(CFG, "top50") == 50 and run_grid.job_k(CFG, "base") == 200


def test_rated_energy_is_what_reaches_the_grid():
    rated, stored = run_grid.stored_energy(100, 2, 1.0, CFG)
    assert rated == 200 and stored == pytest.approx(200 / 0.88 ** 0.5)
    old = {**CFG, "credit": {**CFG["credit"], "rated_energy": "stored"}}
    assert run_grid.stored_energy(100, 2, 0.7, old) == pytest.approx((140, 140))


def test_load_adds_back_batteries_and_scales_solar():
    w = pd.DataFrame({"l_gross_mw": [100.0, 200.0], "l_net_mw": [80.0, 150.0],
                      "bess_mw": [0.0, 30.0], "solar_mw": [5.0, 10.0]})
    assert run_grid.winter_load(w, "gross", "base", CFG).tolist() == [100.0, 230.0]
    assert run_grid.winter_load(w, "net", "base", CFG).tolist() == [80.0, 180.0]
    # three times the solar: two more solar's worth comes off net demand
    assert run_grid.winter_load(w, "net", "solar3x", CFG).tolist() == [70.0, 160.0]
    off = {**CFG, "credit": {**CFG["credit"], "add_back_bess": False}}
    assert run_grid.winter_load(w, "gross", "base", off).tolist() == [100.0, 200.0]


def test_windows_pick_the_right_half_hours():
    w = pd.DataFrame({"period": ["OFF", "AM", "MID", "PM", "PM"],
                      "in_wcm_window": [False, True, True, True, False]})
    assert run_grid.window_mask(w, "all").tolist() == [False, True, True, True, False]
    assert run_grid.window_mask(w, "AM").tolist() == [False, True, False, False, False]
    assert run_grid.window_mask(w, "PM").tolist() == [False, False, False, True, False]


@pytest.fixture(scope="module")
def solved_lp(winter):
    load, win = winter
    load, win = load[: 48 * 40], win[: 48 * 40]
    p, e, k, eta = 300.0, 600.0, 200, 0.88
    after, _, det, _ = min_top_k_no_new_peaks(load, win, p, e, k, eta=eta)
    return load, win, p, e, k, eta, after, det


def test_lp_checks_pass_on_a_real_solution(solved_lp):
    out = run_grid.lp_checks(*solved_lp)
    assert out["g5_ok"], out
    assert out["h100_replay_err_mw"] < 1e-3


@pytest.mark.parametrize("tamper, flag", [
    (lambda d: {**d, "soc": d["soc"] + 1e3}, "soc_ok"),
    (lambda d: {**d, "discharge": d["discharge"] * 2}, "power_ok"),
])
def test_lp_checks_catch_a_broken_dispatch(solved_lp, tamper, flag):
    load, win, p, e, k, eta, after, det = solved_lp
    out = run_grid.lp_checks(load, win, p, e, k, eta, after, tamper(det))
    assert not out[flag] and not out["g5_ok"]


def test_energy_check_catches_energy_from_nowhere(solved_lp):
    # Discharge at full power all winter with no charging: far more than the store holds
    load, win, p, e, k, eta, after, det = solved_lp
    n = len(load)
    fake = {"discharge": np.full(n, p), "charge": np.zeros(n), "soc": np.zeros(n)}
    out = run_grid.lp_checks(load, win, p, e, k, eta, after, fake)
    assert out["energy_slack_mwh"] < 0 and "energy" in out["failed_checks"].split(",")


def table(cc):
    return pd.DataFrame({"winter": 2024, "basis": "net", "window": "all", "sensitivity": "base",
                         "d_h": 2, "p_mw": [35, 135, 335][: len(cc)], "cc": cc})


def test_duration_gate():
    rows = [("base", 1, 0.40), ("usable70", 2, 0.55), ("base", 2, 0.70), ("base", 4, 0.65)]
    t = pd.DataFrame([{"winter": 2024, "basis": "net", "window": "AM", "p_mw": 1500,
                       "sensitivity": s, "d_h": d, "cc": c} for s, d, c in rows])
    bad = run_grid.duration_violations(t)
    assert len(bad) == 1 and "('base', 2)" in bad[0][1]
    t.loc[3, "cc"] = 0.71
    assert run_grid.duration_violations(t) == []


def test_monotone_gate():
    assert run_grid.monotone_violations(table([0.9, 0.8, 0.7])) == []
    assert run_grid.monotone_violations(table([0.9, 0.8, 0.8 + 1e-4])) == []   # within tolerance
    bad = run_grid.monotone_violations(table([0.9, 0.7, 0.8]))
    assert len(bad) == 1 and bad[0][1] == "rises"
    assert run_grid.monotone_violations(table([1.2, 0.9, 0.8]))[0][1] == "outside [0, 1]"


@pytest.mark.parametrize("cc, expected, note", [
    ([0.9, 0.7, 0.5], 235.0, "in_grid"),       # 135 + (0.7 - 0.6) / (0.7 - 0.5) * 200
    ([0.9, 0.6, 0.5], 135.0, "in_grid"),       # touches 0.6 exactly at 135 MW
    ([0.9, 0.7, 0.6], 335.0, "in_grid"),       # touches it at the last point: same rule
    ([0.6, 0.5, 0.4], 35.0, "in_grid"),        # ... and at the first
    ([0.5, 0.4, 0.3], np.nan, "below_grid"),
    ([0.9, 0.8, 0.7], np.nan, "above_grid"),
    ([0.9, np.nan, 0.5], np.nan, "incomplete"),
])
def test_crossing(cc, expected, note):
    p, got = run_grid.crossing([35, 135, 335], cc, 0.6)
    assert got == note
    assert (np.isnan(p) and np.isnan(expected)) or p == pytest.approx(expected)


def grid_table():
    """Two base winters and one check winter of 2 h curves, plus a 4 h curve."""
    rows = []
    for wi, ccs in {2021: [0.9, 0.7, 0.5], 2022: [0.9, 0.8, 0.65], 2026: [0.8, 0.55, 0.4]}.items():
        rows += [{"winter": wi, "basis": "net", "window": "all", "sensitivity": "base",
                  "d_h": 2, "p_mw": p, "cc": c} for p, c in zip([35, 135, 335], ccs)]
    rows += [{"winter": 2021, "basis": "net", "window": "all", "sensitivity": "base",
              "d_h": 4, "p_mw": p, "cc": c} for p, c in zip([35, 135, 335], [0.95, 0.85, 0.75])]
    return pd.DataFrame(rows)


def test_crossings_per_winter_mean_and_lowest():
    cfg = {**CFG, "credit": {**CFG["credit"], "base_winters": [2021, 2022],
                             "check_winters": [2026]}}
    x = run_grid.crossings(grid_table(), cfg).set_index(["d_h", "winters", "stat"])
    assert x.loc[(2, "2021", "single"), "p_star_mw"] == pytest.approx(235)
    assert x.loc[(2, "2022", "single"), "p_star_note"] == "above_grid"
    # mean of 2021 and 2022 is [0.9, 0.75, 0.575]: 135 + 0.15 / 0.175 * 200
    assert x.loc[(2, "base", "mean"), "p_star_mw"] == pytest.approx(135 + 0.15 / 0.175 * 200)
    low = x.loc[(2, "base", "min_of_winters")]
    assert low.p_star_mw == pytest.approx(235) and low.worst_winter == 2021
    assert x.loc[(2, "check", "mean"), "n_winters"] == 1
    four = x.loc[(4, "base", "mean")]
    assert four.sosa_factor == 0.8 and four.p_star_mw == pytest.approx(135 + 0.05 / 0.1 * 200)


def test_g5_stops_the_run_after_writing_its_outputs(tmp_path, monkeypatch):
    from conftest import synthetic_winter
    load, win = synthetic_winter(days=40)
    n = len(load)
    ts = pd.date_range("2024-06-01", periods=n, freq="30min", tz="Pacific/Auckland")
    tp = np.tile(np.arange(48), n // 48)
    s = pd.DataFrame({"ts_local": ts, "winter": pd.array([2024] * n, dtype="Int64"),
                      "winter_complete": True, "in_wcm_window": win,
                      "period": np.where(tp < 24, "AM", np.where(tp >= 32, "PM", "MID")),
                      "l_gross_mw": load, "l_net_mw": load - 100, "bess_mw": 0.0,
                      "solar_mw": 0.0})
    sp = tmp_path / "s.parquet"
    s.to_parquet(sp)
    cfg = {**CFG, "grid": {"p_mw": [35, 335], "d_h": [2], "basis": ["gross"], "window": ["all"]},
           "sensitivities": {}, "credit": {**CFG["credit"], "base_winters": [2024],
                                           "check_winters": [], "workers": 1}}
    real = run_grid.min_top_k_no_new_peaks

    def flaky(load, win, p, *a, **kw):
        if p == 335:
            raise RuntimeError("fake infeasible")
        return real(load, win, p, *a, **kw)

    monkeypatch.setattr(run_grid, "min_top_k_no_new_peaks", flaky)
    with pytest.raises(GateError, match="1 LPs not optimal, 0 optimal LPs failed"):
        run_grid.run(cfg, series_path=sp, out=tmp_path)
    t = pd.read_csv(tmp_path / "credit_table.csv")
    assert t.status.str.contains("fake infeasible").sum() == 1
    log = (tmp_path / "run_grid.log").read_text(encoding="utf-8")
    assert "not optimal" in log and "fake infeasible" in log
    assert any("WARNING" in line and "not optimal:" in line for line in log.splitlines())
    assert any("WARNING" in line and "G5 FAIL" in line for line in log.splitlines())
    assert "Traceback (most recent call last)" in log and "RuntimeError: fake infeasible" in log
    assert "traceback" not in t.columns                    # kept in the log, not the table
    x = pd.read_csv(tmp_path / "crossings.csv")
    assert set(x.p_star_note) == {"incomplete"}
    (tmp_path / "run_grid.log").unlink()          # the handler is closed after run()
