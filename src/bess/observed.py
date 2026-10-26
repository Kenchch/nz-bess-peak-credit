"""What the visible batteries actually delivered at the peak, against the ceiling.

Generation_MD carries two grid batteries as their own units: Ruakākā
(BRB0331, 100 MW / 200 MWh) and Rotohiko (HLY0331, 35 MW / 35 MWh). Glenbrook
(100 MW, from December 2025) and Huntly (100 MW, commissioning from July 2026)
have no rows, so only the first two can be observed.

For each winter, a unit counts if it reported output on at least half the
winter's days. Over the k highest in-window half-hours of the load before
batteries (gross basis), observed_factor = mean output of the counted units /
their nameplate. That is scored the way SOSA scores wind, so it is compared
with the ceiling's cc_availability for a 2-hour fleet of the grid size nearest
that nameplate (135 MW on the real grid), in the same winter, with its cc for
reference.

Generation_MD is energy only. Batteries earn much of their peak value as
FIR / SIR reserve, which never shows up as energy, so observed_factor is a
lower bound on what they contribute, not a behavioural factor on its own.
"""
import logging

import numpy as np
import pandas as pd

from . import RAW, SERIES, STAGING, load_config
from .calibrate import load_fleet
from .ingest import REFERENCE
from .series import KWH_PER_MW
from .stage import island_map

log = logging.getLogger(__name__)

UNITS = {"BRB0331": "Ruakākā", "HLY0331": "Rotohiko"}
MIN_DAY_SHARE = 0.5
COLUMNS = ["winter", "winter_complete", "units_counted", "nameplate_mw", "fleet_in_force_mw",
           "k", "observed_mean_mw", "observed_factor", "ceiling_p_mw", "ceiling_cc",
           "ceiling_availability", "observed_over_ceiling"]


def battery_output(staging):
    """MW per (trading_date, tp) for each visible battery unit, wide by POC."""
    g = pd.read_parquet(staging / "generation.parquet",
                        columns=["trading_date", "tp", "poc", "fuel", "kwh"])
    g = g[(g.fuel == "battery") & g.poc.isin(list(UNITS))]
    mw = g.groupby(["trading_date", "tp", "poc"], observed=True).kwh.sum() / KWH_PER_MW
    return mw.unstack("poc", fill_value=0.0).reindex(columns=list(UNITS), fill_value=0.0)


def run(cfg=None, series_path=SERIES / "ni_halfhourly.parquet", staging=STAGING,
        fleet_path=None, nsp_path=None, out=None, credit_table=None):
    cfg = cfg or load_config()
    k = cfg["k_top"]
    s = pd.read_parquet(series_path)
    s["trading_date"] = pd.to_datetime(s.trading_date)
    units = battery_output(staging)
    s = s.merge(units.reset_index(), on=["trading_date", "tp"], how="left")
    s[list(UNITS)] = s[list(UNITS)].fillna(0.0)

    islands = island_map(nsp_path or RAW / "nsp" / REFERENCE["nsp"][1])
    fleet = load_fleet(fleet_path or RAW / "fleet" / REFERENCE["fleet"][1], islands)
    fleet = fleet[fleet.fuel == "battery"]
    nameplate = fleet.groupby("poc").mw.max()

    if credit_table is None and out is not None and (out / "credit_table.csv").exists():
        credit_table = pd.read_csv(out / "credit_table.csv")

    rows = []
    for winter, w in s[s.winter.notna()].groupby("winter"):
        winter = int(winter)
        days = w.groupby("trading_date")[list(UNITS)].max()
        share = (days > 0).mean()
        counted = [u for u in UNITS if share[u] >= MIN_DAY_SHARE and u in nameplate.index]
        mid = pd.Timestamp(f"{winter}-07-15", tz="Pacific/Auckland")
        in_force = fleet[(fleet.start <= mid) & (fleet.end > mid)].mw.sum()
        row = {"winter": winter, "winter_complete": bool(w.winter_complete.iloc[0]),
               "units_counted": "+".join(UNITS[u] for u in counted) or "none",
               "nameplate_mw": float(nameplate[counted].sum()) if counted else 0.0,
               "fleet_in_force_mw": float(in_force), "k": k}
        if counted:
            win = w.in_wcm_window.to_numpy(bool)
            load = (w.l_gross_mw + w.bess_mw).to_numpy()
            top = np.flatnonzero(win)[np.argsort(load[win], kind="stable")[-k:]]
            observed = w[counted].sum(axis=1).to_numpy()[top].mean()
            row.update(observed_mean_mw=observed, observed_factor=observed / row["nameplate_mw"])
        if credit_table is not None and counted:
            c = credit_table[(credit_table.winter == winter) & (credit_table.basis == "gross")
                             & (credit_table.window == "all")
                             & (credit_table.sensitivity == "base") & (credit_table.d_h == 2)]
            if len(c):
                c = c.iloc[[int((c.p_mw - row["nameplate_mw"]).abs().argmin())]]
                row.update(ceiling_p_mw=int(c.p_mw.iloc[0]), ceiling_cc=float(c.cc.iloc[0]),
                           ceiling_availability=float(c.cc_availability.iloc[0]))
        rows.append(row)
    table = pd.DataFrame(rows).reindex(columns=COLUMNS)
    table["observed_over_ceiling"] = table.observed_factor / table.ceiling_availability
    if out is not None:
        table.to_csv(out / "observed_vs_ceiling.csv", index=False)
    for r in table[table.units_counted != "none"].itertuples():
        log.info(f"observed: {r.winter} {r.units_counted} ({r.nameplate_mw:.0f} MW of "
                 f"{r.fleet_in_force_mw:.0f} MW in force): {r.observed_mean_mw:.1f} MW at the "
                 f"top {k} = {r.observed_factor:.1%} of nameplate; ceiling availability "
                 f"{r.ceiling_availability:.1%} at {r.ceiling_p_mw:.0f} MW")
    return table
