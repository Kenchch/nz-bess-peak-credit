"""Build the North Island half-hourly series (contract: README / architecture 4.1).

    gen_ni_mw      North Island plant injection, batteries excluded
    hvdc_north_mw  net HVDC receipt at Haywards, south -> north positive
    l_gross_mw     gen_ni_mw + hvdc_north_mw
    l_net_mw       l_gross_mw - wind_mw - solar_mw

Energy per half-hour becomes average MW by kWh / 500 (kWh / 0.5 h / 1000).

HVDC sign: in HVDC_Flows, Flow_Direction "I" at a pole POC is energy injected
into that island's AC grid from the link and "X" is energy taken from it. At
HAY2201 (Haywards, North Island) "I" is therefore south -> north; it matches
"X" at BEN2201 (Benmore) less about 3% link losses.
"""
import numpy as np
import pandas as pd

from . import SERIES, STAGING, TZ, GateError, load_config
from .stage import periods_in_day

HVDC_NI_POC = "HAY2201"
KWH_PER_MW = 500.0
COLUMNS = ["ts_local", "trading_date", "tp", "winter", "winter_complete", "period",
           "in_wcm_window", "gen_ni_mw", "hvdc_north_mw", "wind_mw", "solar_mw",
           "bess_mw", "l_gross_mw", "l_net_mw"]


def calendar(start, end):
    """Every (trading_date, tp) from the first day of start to the last of end."""
    days = pd.date_range(pd.Period(start, "M").start_time,
                         pd.Period(end, "M").end_time.normalize(), freq="D")
    n = periods_in_day(days)
    trading_date = np.repeat(days.to_numpy(), n)
    tp = np.concatenate([np.arange(1, k + 1) for k in n]).astype("int16")
    # Same result as pd.date_range(day, day + 1, freq="30min", tz=TZ) per day:
    # step in UTC from local midnight, so DST days come out 46 or 50 long.
    midnight = pd.DatetimeIndex(trading_date).tz_localize(TZ)
    ts = midnight + pd.to_timedelta((tp - 1) * 30, unit="min")
    return pd.DataFrame({"ts_local": ts.tz_convert(TZ), "trading_date": trading_date, "tp": tp})


def hhmm(s):
    h, m = map(int, s.split(":"))
    return h + m / 60


def label(cal, cfg):
    """Winter year, completeness, AM/MID/PM/OFF and the NI-WCM 07-22 window."""
    d = pd.DatetimeIndex(cal.trading_date)
    mmdd = d.strftime("%m-%d")
    in_winter = (mmdd >= cfg["winter"]["start"]) & (mmdd <= cfg["winter"]["end"])
    winter = pd.Series(np.where(in_winter, d.year, 0), index=cal.index).astype("Int64")
    winter[~in_winter] = pd.NA

    last_day = pd.Period(cfg["period"]["end"], "M").end_time.normalize()
    winter_end = pd.to_datetime(winter.astype("string") + "-" + cfg["winter"]["end"],
                                errors="coerce")
    complete = pd.Series(winter_end <= last_day, index=cal.index).astype("boolean")
    complete[winter.isna()] = pd.NA

    ts = pd.DatetimeIndex(cal.ts_local)
    h = ts.hour + ts.minute / 60
    w = {k: tuple(map(hhmm, v)) for k, v in cfg["windows"].items()}
    period = np.full(len(cal), "OFF", dtype=object)
    period[(h >= w["am"][0]) & (h < w["am"][1])] = "AM"
    period[(h >= w["am"][1]) & (h < w["pm"][0])] = "MID"
    period[(h >= w["pm"][0]) & (h < w["pm"][1])] = "PM"
    return cal.assign(winter=winter, winter_complete=complete,
                      period=pd.Series(period, index=cal.index, dtype="string"),
                      in_wcm_window=(h >= w["wcm"][0]) & (h < w["wcm"][1]))


def build(generation, hvdc, cfg):
    cal = label(calendar(cfg["period"]["start"], cfg["period"]["end"]), cfg)
    key = ["trading_date", "tp"]

    ni = generation[generation.island == "NI"]
    by_fuel = (ni.groupby(key + ["fuel"], observed=True).kwh.sum()
               .unstack("fuel", fill_value=0.0) / KWH_PER_MW)
    for f in ("wind", "solar", "battery"):
        if f not in by_fuel:
            by_fuel[f] = 0.0
    mw = pd.DataFrame({
        "gen_ni_mw": by_fuel.drop(columns="battery").sum(axis=1),
        "wind_mw": by_fuel["wind"],
        "solar_mw": by_fuel["solar"],
        "bess_mw": by_fuel["battery"],
    })

    hay = hvdc[hvdc.poc == HVDC_NI_POC]
    flows = hay.groupby(key + ["flow_direction"], observed=True).kwh.sum().unstack(fill_value=0.0)
    mw["hvdc_north_mw"] = (flows.get("I", 0.0) - flows.get("X", 0.0)) / KWH_PER_MW

    out = cal.merge(mw.reset_index(), on=key, how="left", validate="one_to_one")
    out["l_gross_mw"] = out.gen_ni_mw + out.hvdc_north_mw
    out["l_net_mw"] = out.l_gross_mw - out.wind_mw - out.solar_mw
    out["trading_date"] = pd.DatetimeIndex(out.trading_date).date
    return out[COLUMNS]


def run(cfg=None, staging=STAGING, out=SERIES):
    cfg = cfg or load_config()
    generation = pd.read_parquet(staging / "generation.parquet",
                                 columns=["trading_date", "tp", "island", "fuel", "kwh"])
    hvdc = pd.read_parquet(staging / "hvdc.parquet")
    s = build(generation, hvdc, cfg)

    missing = s[["gen_ni_mw", "hvdc_north_mw"]].isna().any(axis=1)
    if missing.any():
        gaps = s.loc[missing, "trading_date"].unique()
        raise GateError(f"series: {missing.sum()} half-hours have no generation or HVDC "
                        f"data, on {len(gaps)} days starting {gaps[:5]}")

    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "ni_halfhourly.parquet.tmp"
    s.to_parquet(tmp, index=False)
    tmp.replace(out / "ni_halfhourly.parquet")
    w = s[s.winter.notna()]
    print(f"series: {len(s):,} half-hours, winters "
          f"{sorted(w.winter.unique().tolist())}, "
          f"max l_gross {s.l_gross_mw.max():,.0f} MW")
    return s
