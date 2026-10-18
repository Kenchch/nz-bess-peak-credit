"""Gate G3 (warning only): North Island supply against GXP offtake, by month.

    supply   = all North Island Generation_MD injection (batteries included)
               + net HVDC receipt at Haywards
    offtake  = Grid_export at North Island POCs (what the grid delivers to
               distributors and direct-connect customers)

The gap is transmission losses plus Generation_MD plants that are embedded
below a GXP, whose output serves local load without crossing the grid exit.
It should be a small, steady share of supply; a month outside the band points
at a missing file, a unit error or a sign error in the HVDC series.
"""
import numpy as np
import pandas as pd

from . import RAW, SERIES, load_config
from .ingest import REFERENCE, months
from .stage import check, island_map, island_of

# Band for (supply - offtake) / supply, set from the 2021-04..2026-09 run:
# every month landed between 6.8% and 10.0% (mean 8.3%, sd 0.7 pp), so the
# band is roughly mean +/- 3 sd. SOSA's static NI loss factor is 2.88%; the
# rest is embedded Generation_MD output that never reaches a grid exit.
BAND = (0.06, 0.11)


def offtake_by_month(cfg, raw=RAW):
    islands = island_map(raw / "nsp" / REFERENCE["nsp"][1])
    rows = []
    for ym in months(cfg["period"]["start"], cfg["period"]["end"]):
        df = pd.read_csv(raw / "grid_export" / f"{ym}_Grid_export.csv",
                         dtype={"POC": str, "Nwk_Code": str, "Flow_Direction": str})
        df["island"] = island_of(df["POC"], islands)
        rule, _, expected, vals = check(df, ["Trading_Date", "POC", "Nwk_Code"],
                                        lambda d: d["Flow_Direction"].eq("X").to_numpy())
        ok = rule == 0
        real = np.arange(1, 51)[None, :] <= expected[:, None]
        kwh = np.where(real, np.nan_to_num(vals), 0).sum(axis=1)
        ni = ok & df["island"].eq("NI").to_numpy()
        rows.append({"month": f"{ym[:4]}-{ym[4:]}", "offtake_gwh": kwh[ni].sum() / 1e6,
                     "export_rows": len(df), "export_rows_rejected": int((~ok).sum())})
    return pd.DataFrame(rows)


def run(cfg=None, series_path=SERIES / "ni_halfhourly.parquet", raw=RAW, out=None):
    cfg = cfg or load_config()
    s = pd.read_parquet(series_path)
    s["month"] = pd.DatetimeIndex(pd.to_datetime(s.trading_date)).strftime("%Y-%m")
    supply = s.groupby("month").agg(
        gen_ni_gwh=("gen_ni_mw", "sum"), bess_gwh=("bess_mw", "sum"),
        hvdc_north_gwh=("hvdc_north_mw", "sum")) * 0.5 / 1e3
    supply["supply_gwh"] = supply.sum(axis=1)
    rec = supply.reset_index().merge(offtake_by_month(cfg, raw), on="month", validate="one_to_one")
    rec["gap_gwh"] = rec.supply_gwh - rec.offtake_gwh
    rec["gap_share"] = rec.gap_gwh / rec.supply_gwh
    rec["in_band"] = rec.gap_share.between(*BAND)
    # Sign check: had south->north been read the wrong way round, supply would
    # move by twice the transfer and the gap would swing with the HVDC.
    flipped = rec.supply_gwh - 2 * rec.hvdc_north_gwh
    rec["gap_share_if_hvdc_flipped"] = (flipped - rec.offtake_gwh) / flipped
    if out is not None:
        rec.to_csv(out / "reconcile.csv", index=False)
    bad = rec[~rec.in_band]
    print(f"reconcile (G3): gap {rec.gap_share.min():.1%}..{rec.gap_share.max():.1%} of supply; "
          f"{len(bad)} of {len(rec)} months outside {BAND[0]:.0%}-{BAND[1]:.0%}"
          + (f": {bad.month.tolist()}" if len(bad) else "")
          + f"; sd {rec.gap_share.std():.2%} vs {rec.gap_share_if_hvdc_flipped.std():.2%} "
            "with the HVDC sign flipped")
    return rec
