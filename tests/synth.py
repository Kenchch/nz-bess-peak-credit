"""Synthetic EMI-shaped files for the offline tests.

`python tests/synth.py` rewrites the small CSVs in tests/fixtures/; the smoke
test calls write_raw() to lay out a whole data/raw tree in a temp folder.
Every number here is invented.
"""
from pathlib import Path

import numpy as np
import pandas as pd

TZ = "Pacific/Auckland"
TP_COLS = [f"TP{i}" for i in range(1, 51)]
FIXTURES = Path(__file__).parent / "fixtures"

NSP = pd.DataFrame({
    "Current flag": 1,
    "POC code": ["ARA2201", "WVY1101", "EDG0331", "BRB0331", "HAY0331", "BEN2202", "OTA2201"],
    "Island": ["NI", "NI", "NI", "NI", "NI", "SI", "NI"],
})
# (POC, Nwk, Gen_Code, Fuel_Code, Tech_Code, island, mean MW)
UNITS = [
    ("ARA2201", "MRPL", "aratiatia", "Hydro", "Hydro", "NI", 2600.0),
    ("WVY1101", "WPWF", "waipipi", "Wind", "Wind", "NI", 300.0),
    ("EDG0331", "HEDL", "Rangitaiki", "Solar", "Solar", "NI", 0.0),
    ("BRB0331", "MERI", "ruakākā_energy_park", "ELE", "BAT", "NI", 0.0),
    ("BEN2202", "MERI", "benmore", "Hydro", "Hydro", "SI", 900.0),
]
FLEET = pd.DataFrame({
    "PlantName": ["Waipipi", "Rangitaiki Solar Farm", "Ruakākā Energy Park"],
    "PointOfConnectionCode": ["WVY1101", "EDG0331", "BRB0331"],
    "UnitCode": ["WPP0", "RNG0", "RUK0"],
    "TechnologyCode": ["ONW", "PV", "BAT"], "Technology": ["Onshore wind", "Photovoltaic panels", "Chemical battery energy"],
    "FuelCode": ["WIN", "SOL", "ELE"], "Fuel": ["Wind", "Solar", "Electricity"],
    "PeakingPlantFlag": "N", "PlantOperatorCode": "X", "PlantOperator": "X",
    "DateCommissioned": ["1/01/2020", "1/01/2020", "null"], "DateDecommissioned": "null",
    "NameplateMegawatts": [600, 50, 100],
    "EffectiveStartDate": "1899-12-31T12:30:00.000Z", "EffectiveEndDate": "9999-12-31T10:59:59.000Z",
})


def periods(day):
    day = pd.Timestamp(day)
    return int((day + pd.Timedelta(days=1)).tz_localize(TZ).value
               - day.tz_localize(TZ).value) // (30 * 60 * 10**9)


def wide(meta, day, values):
    """One EMI row: meta columns, Trading_Date, TP1..TP50 (blank past the day's end)."""
    tps = list(values) + [np.nan] * (50 - len(values))
    return {**meta, "Trading_Date": pd.Timestamp(day).strftime("%Y-%m-%d"), **dict(zip(TP_COLS, tps))}


def daily_shape(n, base, rng):
    """kWh per half-hour with a morning and an evening peak."""
    h = (np.arange(n) + 0.5) / 2
    mw = base * (1 + 0.15 * np.exp(-((h - 8) / 1.5) ** 2) + 0.25 * np.exp(-((h - 18.5) / 1.8) ** 2)
                 - 0.2 * np.exp(-((h - 3.5) / 2.5) ** 2)) + rng.normal(0, base * 0.01 + 1, n)
    return np.round(np.clip(mw, 0, None) * 500)


def generation_month(days, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for day in days:
        n = periods(day)
        for poc, nwk, gen, fuel, tech, _, mw in UNITS:
            meta = {"Site_Code": poc[:3], "POC_Code": poc, "Nwk_Code": nwk, "Gen_Code": gen,
                    "Fuel_Code": fuel, "Tech_Code": tech}
            if fuel == "Solar":
                h = (np.arange(n) + 0.5) / 2
                vals = np.round(np.clip(30 * np.sin(np.pi * (h - 7) / 10), 0, None) * 500)
            elif tech == "BAT":
                vals = np.zeros(n)
                vals[36:40] = 50 * 500          # 18:00-20:00 discharge
            elif fuel == "Wind":
                vals = np.round(rng.uniform(50, 550, n) * 500)
            else:
                vals = daily_shape(n, mw, rng)
            rows.append(wide(meta, day, vals))
    return pd.DataFrame(rows)


def hvdc_month(days, north_mw=400.0):
    rows = []
    for day in days:
        n = periods(day)
        for poc, direction, mw in [("HAY2201", "I", north_mw), ("HAY2201", "X", 0.0),
                                   ("BEN2201", "I", 0.0), ("BEN2201", "X", north_mw * 1.03)]:
            meta = {"POC": poc, "Nwk_Code": "TPNZ", "Generation_Type": "DC", "Trader": "TPNZ",
                    "Unit_Measure": "kWh", "Flow_Direction": direction, "Status": "F"}
            rows.append(wide(meta, day, np.full(n, mw * 500)))
    return pd.DataFrame(rows)


def grid_export_month(gen, hvdc, share=0.92):
    """One NI GXP taking `share` of NI supply."""
    ni = gen[gen.POC_Code.isin([u[0] for u in UNITS if u[5] == "NI"])]
    supply = ni.groupby("Trading_Date")[TP_COLS].sum(min_count=1)
    hay = hvdc[hvdc.POC == "HAY2201"].set_index(["Trading_Date", "Flow_Direction"])[TP_COLS]
    supply = supply + hay.xs("I", level=1) - hay.xs("X", level=1)
    rows = [wide({"POC": "OTA2201", "Nwk_Code": "VECT", "Generation_Type": "GN", "Trader": "TPNZ",
                  "Unit_Measure": "kWh", "Flow_Direction": "X", "Status": "F"},
                 day, np.round(v.dropna().to_numpy() * share))
            for day, v in supply.iterrows()]
    return pd.DataFrame(rows)


def write_raw(raw, start, end, seed=0):
    """A full data/raw tree for months start..end, named like the real files."""
    from bess.ingest import REFERENCE
    for d in ("generation_md", "hvdc_flows", "grid_export", "nsp", "fleet"):
        (raw / d).mkdir(parents=True, exist_ok=True)
    NSP.to_csv(raw / "nsp" / REFERENCE["nsp"][1], index=False)
    FLEET.to_csv(raw / "fleet" / REFERENCE["fleet"][1], index=False)
    for i, p in enumerate(pd.period_range(start, end, freq="M")):
        days = pd.date_range(p.start_time, p.end_time.normalize(), freq="D")
        ym = p.strftime("%Y%m")
        gen, hv = generation_month(days, seed + i), hvdc_month(days)
        gen.to_csv(raw / "generation_md" / f"{ym}_Generation_MD.csv", index=False)
        hv.to_csv(raw / "hvdc_flows" / f"{ym}_HVDC_flows.csv", index=False)
        grid_export_month(gen, hv).to_csv(raw / "grid_export" / f"{ym}_Grid_export.csv", index=False)


def dirty_generation():
    """Three days (48, 50 and 46 half-hours) plus one bad row per rule."""
    days = ["2025-04-05", "2025-04-06", "2025-09-28"]
    df = generation_month(days)
    unit = df.iloc[0].to_dict()
    bad = []
    bad.append({**unit, "Trading_Date": "2025-13-01"})                       # 1 date
    short = dict(unit); short["Trading_Date"] = "2025-04-07"; short["TP48"] = np.nan
    bad.append(short)                                                         # 2 tp count
    neg = dict(unit); neg["Trading_Date"] = "2025-04-08"; neg["TP10"] = -5
    bad.append(neg)                                                           # 3 negative
    bad.append({**unit, "POC_Code": "ZZZ9999", "Site_Code": "ZZZ"})           # 4 unknown POC
    dup = dict(unit); dup["TP1"] = unit["TP1"] + 1
    bad.append(dup)                                                           # 5 conflicting copy
    bad.append({**unit, "Gen_Code": "mystery", "Fuel_Code": "Unobtanium"})    # 6 unknown fuel
    return pd.concat([df, pd.DataFrame(bad)], ignore_index=True)


if __name__ == "__main__":
    FIXTURES.mkdir(exist_ok=True)
    NSP.to_csv(FIXTURES / "nsp.csv", index=False)
    dirty_generation().to_csv(FIXTURES / "generation_dirty.csv", index=False)
    hvdc_month(["2025-04-06"]).to_csv(FIXTURES / "hvdc_dst.csv", index=False)
