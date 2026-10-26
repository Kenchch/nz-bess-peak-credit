"""Turn the wide EMI files into long tables and quarantine bad rows (gate G2).

One input row is one meter (or plant) for one trading day, with TP1..TP50 in
kWh. Each row is checked against six rules, in order, and tagged with the
first one it breaks:

  1  Trading_Date parses as a date
  2  the row has exactly as many trading periods as that day has on the
     daylight-saving calendar (46, 48 or 50), with no gaps
  3  every kWh value is >= 0
  4  the POC maps to an island in the NSP table
  5  the (date, POC, network, unit) key is not repeated; exact copies keep
     the first, conflicting copies are all quarantined
  6  the fuel code (HVDC: flow direction and unit) is a known one

Rows that break a rule go to quarantine.parquet with the rule number. If more
than gates.max_reject_rate of a source's rows are rejected, the run stops and
the previous staging tables are left as they were.
"""
import json
import logging

import numpy as np
import pandas as pd

from . import RAW, STAGING, TZ, GateError, load_config
from .ingest import REFERENCE, months

log = logging.getLogger(__name__)

TP_COLS = [f"TP{i}" for i in range(1, 51)]
RULES = {1: "date", 2: "tp_count", 3: "negative_kwh", 4: "unknown_poc",
         5: "duplicate", 6: "unknown_code"}

# Exactly the Fuel_Codes in Generation_MD 2021-04..2026-09, grouped. Plants
# added from 2024 use three-letter codes (HYD, GEO, SOL, WIN, ELE) while older
# plants keep the long names. A code not listed here is quarantined (rule 6),
# so a new fuel code shows up in the quality report instead of vanishing.
# Batteries are Fuel_Code ELE with Tech_Code BAT.
FUEL_GROUP = {
    "Hydro": "hydro", "HYD": "hydro",
    "Geo": "geo", "GEO": "geo",
    "Wind": "wind", "WIN": "wind",
    "Solar": "solar", "SOL": "solar",
    "ELE": "battery",
    "Gas": "gas", "Coal": "coal", "Diesel": "diesel", "Wood": "wood",
}

GEN_KEY = ["Trading_Date", "POC_Code", "Nwk_Code", "Gen_Code"]
HVDC_KEY = ["Trading_Date", "POC", "Flow_Direction"]


def periods_in_day(dates):
    """46, 48 or 50: the number of half-hours in each NZ trading day."""
    day = pd.DatetimeIndex(dates)
    start = day.tz_localize(TZ)
    end = (day + pd.Timedelta(days=1)).tz_localize(TZ)
    return np.asarray((end - start) / pd.Timedelta(minutes=30), dtype=int)


def island_map(nsp_path=None):
    """POC code -> 'NI' / 'SI' from the NSP table (current and historical rows).

    The HVDC pole meters (BEN2201, HAY2201) are not NSPs, so the map also holds
    each three-letter site code: every POC at one substation is on one island.
    """
    nsp_path = nsp_path or RAW / "nsp" / REFERENCE["nsp"][1]
    nsp = pd.read_csv(nsp_path, usecols=["POC code", "Island"], dtype=str).dropna()
    nsp = nsp.drop_duplicates()
    per_poc = nsp.groupby("POC code").Island
    clash = per_poc.nunique()
    if (clash > 1).any():
        raise GateError(f"NSP table maps POCs to two islands: {list(clash[clash > 1].index)}")
    per_site = nsp.groupby(nsp["POC code"].str[:3]).Island
    one_island = per_site.nunique() == 1
    return {**per_site.first()[one_island].to_dict(), **per_poc.first().to_dict()}


def island_of(pocs, islands):
    """Island for each POC: exact NSP match, else the POC's site code."""
    pocs = pd.Series(pocs, dtype=str)
    return pocs.map(islands).fillna(pocs.str[:3].map(islands))


def tp_values(df):
    """TP1..TP50 as a float array; blanks and non-numbers become NaN."""
    tps = df.reindex(columns=TP_COLS)
    return tps.apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)


def check(df, key, code_ok):
    """Tag each wide row with the first rule it breaks (0 = clean).

    df must already carry an "island" column (NaN where the POC is unknown).
    Returns (rule, date, n_expected, values) as arrays aligned with df.
    """
    n = len(df)
    rule = np.zeros(n, dtype=int)
    date = pd.to_datetime(df["Trading_Date"], format="%Y-%m-%d", errors="coerce").to_numpy()
    rule[np.isnat(date)] = 1

    expected = np.zeros(n, dtype=int)
    ok = rule == 0
    expected[ok] = periods_in_day(date[ok])
    vals = tp_values(df)
    present = ~np.isnan(vals)
    want = np.arange(1, 51)[None, :] <= expected[:, None]
    bad_count = (present != want).any(axis=1)
    rule[(rule == 0) & bad_count] = 2

    negative = (np.where(want, vals, 0) < 0).any(axis=1)
    rule[(rule == 0) & negative] = 3

    rule[(rule == 0) & df["island"].isna().to_numpy()] = 4

    keyed = df[key].astype(str).agg("|".join, axis=1)
    dup_key = keyed.duplicated(keep=False).to_numpy()
    if dup_key.any():
        content = pd.util.hash_pandas_object(pd.DataFrame(vals, index=df.index), index=False)
        full = keyed + "|" + content.astype(str)
        exact_extra = full.duplicated(keep="first").to_numpy()
        conflicting = dup_key & ~full.groupby(keyed).transform("nunique").eq(1).to_numpy()
        rule[(rule == 0) & (exact_extra | conflicting)] = 5

    rule[(rule == 0) & ~code_ok(df)] = 6
    return rule, date, expected, vals


def to_long(df, keep, date, expected, vals, meta):
    """Wide TP1..TP50 -> one row per (row, tp) for the kept rows and real periods."""
    df, date, expected, vals = df[keep], date[keep], expected[keep], vals[keep]
    rows, cols = np.nonzero(np.arange(1, 51)[None, :] <= expected[:, None])
    out = {m: df[m].to_numpy()[rows] for m in meta}
    out["trading_date"] = date[rows]
    out["tp"] = (cols + 1).astype("int16")
    out["kwh"] = vals[rows, cols]
    return pd.DataFrame(out)


def quarantine_rows(df, rule, source, file):
    bad = np.flatnonzero(rule)
    return pd.DataFrame({
        "source": source, "file": file, "line": bad + 2,  # +1 header, +1 one-based
        "rule": rule[bad], "reason": [RULES[r] for r in rule[bad]],
        "row": [json.dumps({k: (None if pd.isna(v) else v) for k, v in rec.items()})
                for rec in df.iloc[bad].to_dict("records")],
    })


def report_row(source, file, rule):
    counts = np.bincount(rule, minlength=7)
    n = len(rule)
    return {"source": source, "file": file, "rows_in": n, "rows_ok": int(counts[0]),
            "rejected": int(n - counts[0]),
            **{f"r{i}_{RULES[i]}": int(counts[i]) for i in RULES},
            "reject_rate": (n - counts[0]) / n if n else 0.0}


def stage_generation(path, islands):
    df = pd.read_csv(path, dtype={c: str for c in GEN_KEY[1:] + ["Site_Code", "Fuel_Code", "Tech_Code"]})
    df["island"] = island_of(df["POC_Code"], islands)
    df["fuel"] = df["Fuel_Code"].map(FUEL_GROUP).where(df["Tech_Code"].ne("BAT"), "battery")
    rule, date, expected, vals = check(
        df, GEN_KEY, lambda d: d["Fuel_Code"].isin(FUEL_GROUP.keys()).to_numpy())
    long = to_long(df, rule == 0, date, expected, vals,
                   ["POC_Code", "Nwk_Code", "Gen_Code", "Fuel_Code", "Tech_Code", "island", "fuel"])
    long.columns = ["poc", "nwk", "gen_code", "fuel_code", "tech_code", "island", "fuel",
                    "trading_date", "tp", "kwh"]
    return long, rule, df


def stage_hvdc(path, islands):
    df = pd.read_csv(path, dtype={c: str for c in ["POC", "Nwk_Code", "Flow_Direction", "Unit_Measure"]})
    code_ok = lambda d: (d["Flow_Direction"].isin(["I", "X"]) & d["Unit_Measure"].eq("kWh")).to_numpy()
    df["island"] = island_of(df["POC"], islands)
    rule, date, expected, vals = check(df, HVDC_KEY, code_ok)
    long = to_long(df, rule == 0, date, expected, vals, ["POC", "Flow_Direction", "island"])
    long.columns = ["poc", "flow_direction", "island", "trading_date", "tp", "kwh"]
    return long, rule, df


SOURCES = {
    "generation": ("generation_md", "{ym}_Generation_MD.csv", stage_generation),
    "hvdc": ("hvdc_flows", "{ym}_HVDC_flows.csv", stage_hvdc),
}
CATEGORICAL = ["poc", "nwk", "gen_code", "fuel_code", "tech_code", "island", "fuel", "flow_direction"]


def run(cfg=None, raw=RAW, out=STAGING, nsp_path=None):
    cfg = cfg or load_config()
    islands = island_map(nsp_path or raw / "nsp" / REFERENCE["nsp"][1])
    yms = months(cfg["period"]["start"], cfg["period"]["end"])
    tables, quarantine, report = {}, [], []
    for source, (folder, pattern, stage_one) in SOURCES.items():
        parts = []
        for ym in yms:
            name = pattern.format(ym=ym)
            long, rule, df = stage_one(raw / folder / name, islands)
            parts.append(long)
            quarantine.append(quarantine_rows(df, rule, source, name))
            report.append(report_row(source, name, rule))
        tables[source] = pd.concat(parts, ignore_index=True)

    report = pd.DataFrame(report)
    totals = report.groupby("source", sort=False).sum(numeric_only=True).reset_index()
    totals["file"] = "ALL"
    totals["reject_rate"] = totals.rejected / totals.rows_in
    report = pd.concat([report, totals[report.columns]], ignore_index=True)

    out.mkdir(parents=True, exist_ok=True)
    report.to_csv(out / "quality_report.csv", index=False)
    pd.concat(quarantine, ignore_index=True).to_parquet(out / "quarantine.parquet", index=False)

    limit = cfg["gates"]["max_reject_rate"]
    failed = totals[totals.reject_rate > limit]
    if len(failed):
        raise GateError("G2: reject rate above {:.1%} for {}; previous staging tables kept. "
                        "See quality_report.csv and quarantine.parquet."
                        .format(limit, dict(zip(failed.source, failed.reject_rate.round(4)))))

    for source, table in tables.items():
        for c in CATEGORICAL:
            if c in table:
                table[c] = table[c].astype("category")
        tmp = out / f"{source}.parquet.tmp"
        table.to_parquet(tmp, index=False)
        tmp.replace(out / f"{source}.parquet")

    for _, t in totals.iterrows():
        log.info(f"stage: {t.source}: {t.rows_in:,} rows, {t.rejected:,} quarantined "
                 f"({t.reject_rate:.3%})")
    for source, table in tables.items():
        by_site = site_code_pocs(table.poc, islands)
        if by_site:
            log.info(f"stage: {source}: {len(by_site)} POCs are not NSPs and were placed on "
                     f"an island by site code: {by_site[:10]}{' ...' if len(by_site) > 10 else ''}")
    return report


def site_code_pocs(pocs, islands):
    """The POCs that island_of placed by their three-letter site code, not an NSP row."""
    return sorted(set(pd.Series(pocs, dtype=str).unique()) - islands.keys())
