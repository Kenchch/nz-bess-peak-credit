"""Gate G4: reproduce SOSA 2026's wind and solar peak capacity factors.

SOSA 2026 appendix A3.6 / A3.7: average observed output during "the top 200
highest half hour demand peaks" of 2024 and 2025, as a share of installed
capacity. Published: wind 31.4% / 27.2%, solar 1.49% / 7.14%.

SOSA does not say which demand, which months or which fleet, so the run fixes
one reading in advance (PRIMARY) and gates on it, and reports the others so a
gap can be traced to a definition rather than argued about:

    demand     ni_gross  North Island grid demand (l_gross_mw)
               nz_gross  all New Zealand Generation_MD injection
    selection  h100      1 Apr - 31 Oct, 07:00-22:00 (the SSAD H100 window)
               winter    1 Apr - 31 Oct, all hours
               year      calendar year, all hours
    fleet      nz | ni   wind / solar plants counted

Capacity is the GenerationFleet nameplate in force at each half-hour. A farm
counts (in both numerator and denominator) only while it has nameplate in the
fleet file and rows in Generation_MD at its POC that day; farms missing from
either side are listed in calibration.md.
"""
import numpy as np
import pandas as pd

from . import RAW, SERIES, STAGING, TZ, GateError, load_config
from .ingest import REFERENCE
from .series import KWH_PER_MW
from .stage import island_map, island_of

TECHS = ("wind", "solar")
FLEET_FUEL = {"WIN": "wind", "SOL": "solar", "ELE": "battery"}
DEMANDS = {"ni_gross": "l_gross_mw", "nz_gross": "nz_gen_mw"}
SELECTIONS = ("h100", "winter", "year")
FLEETS = ("nz", "ni")
PRIMARY = {"demand": "ni_gross", "selection": "h100", "fleet": "nz"}


def load_fleet(path, islands):
    """Fleet rows for wind, solar and batteries with the window each was in force."""
    f = pd.read_csv(path, dtype=str)
    f = f[f.FuelCode.isin(FLEET_FUEL)].copy()
    out = pd.DataFrame({
        "plant": f.PlantName, "poc": f.PointOfConnectionCode,
        "fuel": f.FuelCode.map(FLEET_FUEL), "mw": f.NameplateMegawatts.astype(float),
    })
    out["island"] = island_of(out.poc, islands).to_numpy()
    eff_start = pd.to_datetime(f.EffectiveStartDate, utc=True).dt.tz_convert(TZ)
    eff_end = pd.to_datetime(f.EffectiveEndDate, utc=True).dt.tz_convert(TZ)
    commissioned = pd.to_datetime(f.DateCommissioned.replace("null", None),
                                  format="%d/%m/%Y").dt.tz_localize(TZ)
    retired = pd.to_datetime(f.DateDecommissioned.replace("null", None),
                             format="%d/%m/%Y").dt.tz_localize(TZ)
    out["start"] = pd.concat([eff_start, commissioned], axis=1).max(axis=1)
    out["end"] = pd.concat([eff_end, retired], axis=1).min(axis=1)
    return out.reset_index(drop=True)


# Generation_MD unit (POC, Gen_Code) -> GenerationFleet PlantName, for every
# wind and solar unit in 2021-04..2026-09. Matching on POC alone is not enough:
# KOE1101 hosts three solar farms that came online at different times, and
# MTO0331 two. Units or plants missing on one side are listed in the note.
UNITS = {
    ("BPE0331", "twf_12"): "Tararua stage II",
    ("LTN0331", "twf_12"): "Tararua stage I",
    ("TWC2201", "twf_3"): "Tararua Wind Farm 3",
    ("TWC2201", "te_rere_hau"): "Te Rere Hau",
    ("HRP2201", "Harapaki"): "Harapaki",
    ("LTN2201", "turitea"): "Turitea Wind Farm",
    ("TWH0331", "te_uku"): "Te Uku",
    ("WDV1101", "te_apiti"): "Te Āpiti",
    ("WIL0331", "mill_creek"): "Mill Creek",
    ("WVY1101", "waipipi"): "Waipipi",
    ("WWD1102", "west_wind"): "West Wind 1",
    ("WWD1103", "west_wind"): "West Wind 2",
    ("GOR0331", "KaiweraDowns"): "Kaiwera Downs Wind Farm",
    ("KIW2201", "kaiwera_downs_(stage_2)"): "Kaiwera Downs (Stage 2)",
    ("NMA0331", "white_hill"): "White Hill",
    ("EDG0331", "Rangitaiki"): "Rangitaiki Solar Farm",
    ("KOE1101", "Kohira"): "Kohirā Solar Farm",
    ("KOE1101", "Pukenui"): "Pukenui Solar Farm",
    ("MTO0331", "papareireiā_solar_at_maungaturoto"): "Papareireiā",
    ("WAI0331", "te_herenga_o_te_rā_solar_farm"): "Te Herenga o Te Rā Solar Farm",
    ("ASB0661", "Lauriston"): "Lauriston Solar Farm",
    ("DVK0111", "dannevirke_solar_farm"): "Dannevirke Solar Farm",
    ("KPU0661", "pāmu_rā_ki_whitianga_solar_farm"): "Pāmu Rā ki Whitianga Solar Farm",
    ("WHU1101", "tauhei_solar_farm"): "Tauhei Solar Farm",
    ("TMK0331", "clandeboye_solar_farm"): "Clandeboye Solar Farm",
}


def renewables(cfg=None, staging=STAGING, series_path=SERIES / "ni_halfhourly.parquet",
               fleet_path=None, nsp_path=None):
    """Half-hourly frame: NI series + national generation + matched wind/solar.

    Adds, for fuel in wind/solar and scope in ni/nz:
        {fuel}_{scope}_gen_mw   output of the farms counted
        {fuel}_{scope}_cap_mw   their nameplate
    A farm counts in a half-hour when its nameplate is in force and its unit
    has a Generation_MD row that day. Also returns the list of farms left out.
    """
    islands = island_map(nsp_path or RAW / "nsp" / REFERENCE["nsp"][1])
    fleet = load_fleet(fleet_path or RAW / "fleet" / REFERENCE["fleet"][1], islands)
    s = pd.read_parquet(series_path)
    key = ["trading_date", "tp"]
    s["trading_date"] = pd.to_datetime(s.trading_date)

    g = pd.read_parquet(staging / "generation.parquet",
                        columns=key + ["poc", "gen_code", "island", "fuel", "kwh"])
    nz = (g[g.fuel != "battery"].groupby(key, observed=True).kwh.sum() / KWH_PER_MW)
    s = s.merge(nz.rename("nz_gen_mw").reset_index(), on=key, how="left")

    g = g[g.fuel.isin(TECHS)]
    fuel_of = g.groupby(["poc", "gen_code"], observed=True).fuel.first().to_dict()
    per_unit = (g.groupby(key + ["poc", "gen_code"], observed=True).kwh.sum() / KWH_PER_MW
                ).unstack(["poc", "gen_code"])     # NaN = no row that day
    per_unit = per_unit.reindex(pd.MultiIndex.from_frame(s[key]))
    utc = lambda t: t.tz_convert("UTC").tz_localize(None).to_datetime64()
    ts = s.ts_local.dt.tz_convert("UTC").dt.tz_localize(None).to_numpy()

    for fuel in TECHS:
        for scope in FLEETS:
            s[f"{fuel}_{scope}_gen_mw"] = 0.0
            s[f"{fuel}_{scope}_cap_mw"] = 0.0
    left_out = [(fuel_of[u], f"{u[1]} at {u[0]}", "Generation_MD unit not in the fleet file")
                for u in per_unit.columns if u not in UNITS]
    named = set(UNITS.values())
    left_out += [(r.fuel, f"{r.plant} at {r.poc}", "in the fleet file, no Generation_MD unit")
                 for r in fleet[fleet.fuel.isin(TECHS)].drop_duplicates("plant").itertuples()
                 if r.plant not in named]

    for unit, plant in UNITS.items():
        rows = fleet[fleet.plant == plant]
        if rows.empty or unit not in per_unit.columns:
            continue
        cap = np.zeros(len(s))
        for r in rows.itertuples():
            cap += r.mw * ((ts >= utc(r.start)) & (ts < utc(r.end)))
        gen = per_unit[unit].to_numpy()
        counted = (cap > 0) & ~np.isnan(gen)
        fuel, island = rows.fuel.iloc[0], rows.island.iloc[0]
        for scope in FLEETS:
            if scope == "nz" or island == "NI":
                s[f"{fuel}_{scope}_gen_mw"] += np.where(counted, gen, 0.0)
                s[f"{fuel}_{scope}_cap_mw"] += np.where(counted, cap, 0.0)
    return s, fleet, left_out


def select(s, year, selection, demand, k):
    """Index of the k highest-demand half-hours in one year under one definition."""
    d = s.trading_date
    mask = d.dt.year == year
    if selection in ("h100", "winter"):
        mask &= s.winter.eq(year).fillna(False).to_numpy()
    if selection == "h100":
        mask &= s.in_wcm_window
    return s.loc[mask, DEMANDS[demand]].nlargest(k).index


def factors(s, cfg):
    k = cfg["k_top"]
    ref = {"wind": cfg["sosa_reference"]["wind_cf"], "solar": cfg["sosa_reference"]["solar_cf"]}
    rows = []
    for year in sorted(ref["wind"]):
        for demand in DEMANDS:
            for selection in SELECTIONS:
                top = select(s, year, selection, demand, k)
                t = s.loc[top]
                for fleet in FLEETS:
                    for tech in TECHS:
                        gen, cap = t[f"{tech}_{fleet}_gen_mw"], t[f"{tech}_{fleet}_cap_mw"]
                        cf = (gen / cap).mean()
                        rows.append({
                            "year": year, "tech": tech, "demand": demand,
                            "selection": selection, "fleet": fleet, "n": len(t),
                            "share_am": t.period.eq("AM").mean(),
                            "share_pm": t.period.eq("PM").mean(),
                            "mean_gen_mw": gen.mean(), "mean_cap_mw": cap.mean(),
                            "cf": cf, "sosa_cf": ref[tech][year],
                            "diff_pp": 100 * (cf - ref[tech][year]),
                        })
    out = pd.DataFrame(rows)
    out["primary"] = ((out.demand == PRIMARY["demand"]) & (out.selection == PRIMARY["selection"])
                      & (out.fleet == PRIMARY["fleet"]))
    out["within_tol"] = out.diff_pp.abs() <= cfg["gates"]["calibration_tol_pp"]
    return out


def write_note(path, cal, left_out, accepted):
    p = cal[cal.primary]
    lines = ["# Calibration against SOSA 2026 (gate G4)", "",
             f"Primary definition: demand `{PRIMARY['demand']}`, selection "
             f"`{PRIMARY['selection']}`, fleet `{PRIMARY['fleet']}`.", "",
             "| year | tech | ours | SOSA | gap (pp) |", "|---|---|---|---|---|"]
    for r in p.itertuples():
        lines.append(f"| {r.year} | {r.tech} | {r.cf:.2%} | {r.sosa_cf:.2%} | {r.diff_pp:+.2f} |")
    lines += ["", f"Gate: {'PASS' if p.within_tol.all() else 'FAIL'}"
              + (f" (accepted: {accepted})" if accepted and not p.within_tol.all() else ""), "",
              "## Which definition fits SOSA best", "",
              "Largest absolute gap (pp) over 2024 and 2025:", "",
              "| demand | selection | fleet | wind | solar |", "|---|---|---|---|---|"]
    fit = (cal.assign(gap=cal.diff_pp.abs())
           .pivot_table(index=["demand", "selection", "fleet"], columns="tech", values="gap",
                        aggfunc="max").sort_values("wind"))
    for (demand, selection, fleet), r in fit.iterrows():
        lines.append(f"| {demand} | {selection} | {fleet} | {r.wind:.2f} | {r.solar:.2f} |")
    lines += ["", "## Every definition tried", "",
              "| year | tech | demand | selection | fleet | AM share | cf | gap (pp) |",
              "|---|---|---|---|---|---|---|---|"]
    for r in cal.sort_values(["tech", "year", "demand", "selection", "fleet"]).itertuples():
        lines.append(f"| {r.year} | {r.tech} | {r.demand} | {r.selection} | {r.fleet} | "
                     f"{r.share_am:.0%} | {r.cf:.2%} | {r.diff_pp:+.2f} |")
    lines += ["", "## Farms not counted", ""]
    lines += [f"- {fuel}: {what}: {why}" for fuel, what, why in left_out] or ["- none"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(cfg=None, out=None, accept_gap=None, **paths):
    cfg = cfg or load_config()
    s, _, left_out = renewables(cfg, **paths)
    cal = factors(s, cfg)
    if out is not None:
        cal.to_csv(out / "calibration.csv", index=False)
        write_note(out / "calibration.md", cal, left_out, accept_gap)
    p = cal[cal.primary]
    for r in p.itertuples():
        print(f"calibrate: {r.year} {r.tech:5s} ours {r.cf:6.2%}  SOSA {r.sosa_cf:6.2%}  "
              f"gap {r.diff_pp:+.2f} pp")
    if not p.within_tol.all():
        tol = cfg["gates"]["calibration_tol_pp"]
        if not accept_gap:
            raise GateError(f"G4: calibration gap above {tol} pp; see calibration.md. "
                            "Re-run with --accept-gap REASON once the cause is understood.")
        print(f"calibrate: gap above {tol} pp accepted: {accept_gap}")
    return cal
