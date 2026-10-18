import numpy as np
import pandas as pd
import pytest

from bess import stage
from synth import FIXTURES


@pytest.fixture(scope="module")
def islands():
    return stage.island_map(FIXTURES / "nsp.csv")


@pytest.fixture(scope="module")
def staged(islands):
    return stage.stage_generation(FIXTURES / "generation_dirty.csv", islands)


def test_daylight_saving_days_have_46_and_50_periods():
    days = ["2025-04-05", "2025-04-06", "2025-09-28"]
    assert stage.periods_in_day(pd.to_datetime(days)).tolist() == [48, 50, 46]


def test_long_table_keeps_every_real_period(staged):
    long, _, _ = staged
    per_day = long[long.gen_code == "waipipi"].groupby("trading_date").tp.agg(["count", "max"])
    assert per_day["count"].tolist() == [48, 50, 46]
    assert per_day["max"].tolist() == [48, 50, 46]


def test_each_bad_row_is_quarantined_with_its_rule(staged):
    _, rule, df = staged
    counts = np.bincount(rule, minlength=7)
    # 15 clean rows, then one bad row per rule; the conflicting duplicate also
    # takes its clean twin with it, so rule 5 catches two rows
    assert counts.tolist() == [14, 1, 1, 1, 1, 2, 1]
    q = stage.quarantine_rows(df, rule, "generation", "generation_dirty.csv")
    assert set(q.reason) == set(stage.RULES.values())
    assert q[q.rule == 4].row.str.contains("ZZZ9999").all()


def test_quarantined_rows_never_reach_the_long_table(staged):
    long, _, _ = staged
    assert "ZZZ9999" not in set(long.poc)
    assert "mystery" not in set(long.gen_code)
    assert (long.kwh >= 0).all()
    assert pd.Timestamp("2025-04-08") not in set(long.trading_date)


def test_exact_duplicate_keeps_one_copy(islands, tmp_path):
    df = pd.read_csv(FIXTURES / "generation_dirty.csv").iloc[:15]
    path = tmp_path / "dup.csv"
    pd.concat([df, df.iloc[[3]]]).to_csv(path, index=False)
    long, rule, _ = stage.stage_generation(path, islands)
    assert np.bincount(rule, minlength=7)[5] == 1
    assert long.duplicated(["trading_date", "tp", "poc", "nwk", "gen_code"]).sum() == 0


def test_hvdc_poles_map_to_islands_by_site(islands):
    long, rule, _ = stage.stage_hvdc(FIXTURES / "hvdc_dst.csv", islands)
    assert (rule == 0).all()
    assert long.groupby("poc").island.first().to_dict() == {"BEN2201": "SI", "HAY2201": "NI"}
    assert len(long) == 4 * 50


def test_batteries_are_their_own_fuel(staged):
    long, _, _ = staged
    assert set(long[long.tech_code == "BAT"].fuel) == {"battery"}
