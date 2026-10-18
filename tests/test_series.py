import numpy as np
import pandas as pd
import pytest

from bess import load_config, series, stage
from synth import NSP, UNITS, generation_month, hvdc_month

CFG = {**load_config(), "period": {"start": "2025-04", "end": "2025-04"}}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("series")
    NSP.to_csv(tmp / "nsp.csv", index=False)
    islands = stage.island_map(tmp / "nsp.csv")
    days = pd.date_range("2025-04-01", "2025-04-30")
    generation_month(days).to_csv(tmp / "g.csv", index=False)
    hvdc_month(days, north_mw=400).to_csv(tmp / "h.csv", index=False)
    gen, _, _ = stage.stage_generation(tmp / "g.csv", islands)
    hv, _, _ = stage.stage_hvdc(tmp / "h.csv", islands)
    return gen, series.build(gen, hv, CFG)


def test_contract_columns(built):
    _, s = built
    assert list(s.columns) == series.COLUMNS
    assert str(s.ts_local.dt.tz) == "Pacific/Auckland"


def test_kwh_to_mw_divides_by_500(built):
    gen, s = built
    first = s.iloc[0]
    wind_kwh = gen[(gen.gen_code == "waipipi") & (gen.trading_date == "2025-04-01") & (gen.tp == 1)].kwh
    assert first.wind_mw == pytest.approx(wind_kwh.iloc[0] / 500)


def test_only_north_island_plant_counts_and_batteries_are_excluded(built):
    gen, s = built
    ni = gen[(gen.island == "NI") & (gen.fuel != "battery")]
    expected = ni.groupby(["trading_date", "tp"]).kwh.sum().to_numpy() / 500
    np.testing.assert_allclose(s.gen_ni_mw.to_numpy(), expected)
    assert s.bess_mw.max() == pytest.approx(50)
    si_units = [u[2] for u in UNITS if u[5] == "SI"]
    assert set(si_units) & set(gen[gen.island == "NI"].gen_code) == set()


def test_hvdc_south_to_north_is_positive(built):
    _, s = built
    assert s.hvdc_north_mw.eq(400).all()


def test_gross_and_net_demand(built):
    _, s = built
    np.testing.assert_allclose(s.l_gross_mw, s.gen_ni_mw + s.hvdc_north_mw)
    np.testing.assert_allclose(s.l_net_mw, s.l_gross_mw - s.wind_mw - s.solar_mw)


def test_daylight_saving_day_and_labels(built):
    _, s = built
    day = s[s.trading_date == pd.Timestamp("2025-04-06").date()]
    assert len(day) == 50
    assert day.ts_local.iloc[0].isoformat() == "2025-04-06T00:00:00+13:00"
    assert day.ts_local.iloc[-1].isoformat() == "2025-04-06T23:30:00+12:00"
    # 07:00 local is TP15 on a 48-period day and TP17 on the 50-period day
    assert day[day.tp == 17].period.item() == "AM"
    assert day[day.tp == 16].period.item() == "OFF"
    normal = s[s.trading_date == pd.Timestamp("2025-04-07").date()]
    assert normal.period.value_counts().to_dict() == {"OFF": 18, "PM": 12, "AM": 10, "MID": 8}
    assert normal.in_wcm_window.sum() == 30
    assert s.winter.eq(2025).all()
    assert not s.winter_complete.any()      # the period stops before 31 October
