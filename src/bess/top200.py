"""Top-200 half-hours on gross and on net demand, winter by winter.

Mercury's submission on SOSA 2026 asked whether the risk periods are the
highest gross-demand periods at all. For each winter this picks the 200
highest half-hours inside the NI-WCM window (1 Apr - 31 Oct, 07:00-22:00) on
l_gross_mw and on l_net_mw, and reports how many they share, when in the day
they fall, and what wind and solar delivered in them.

wind_cf / solar_cf use the North Island farms and nameplate matched in
calibrate.renewables, so they are comparable with calibration.csv.
"""
import logging

import pandas as pd

from . import load_config
from .calibrate import renewables

log = logging.getLogger(__name__)

BASES = {"gross": "l_gross_mw", "net": "l_net_mw"}


def summarise(s, k):
    rows = []
    w = s[s.winter.notna() & s.in_wcm_window]
    for winter, g in w.groupby("winter"):
        top = {b: set(g[col].nlargest(k).index) for b, col in BASES.items()}
        shared = len(top["gross"] & top["net"])
        for basis, idx in top.items():
            t = s.loc[sorted(idx)]
            rows.append({
                "winter": int(winter), "winter_complete": bool(t.winter_complete.iloc[0]),
                "basis": basis, "n": len(t), "overlap": shared / k,
                "h100_mw": t[BASES[basis]].mean(),
                "l_gross_mw": t.l_gross_mw.mean(), "l_net_mw": t.l_net_mw.mean(),
                "share_am": t.period.eq("AM").mean(), "share_mid": t.period.eq("MID").mean(),
                "share_pm": t.period.eq("PM").mean(),
                "wind_mw": t.wind_mw.mean(), "solar_mw": t.solar_mw.mean(),
                "bess_mw": t.bess_mw.mean(), "hvdc_north_mw": t.hvdc_north_mw.mean(),
                "wind_cap_mw": t.wind_ni_cap_mw.mean(), "solar_cap_mw": t.solar_ni_cap_mw.mean(),
                "wind_cf": (t.wind_ni_gen_mw / t.wind_ni_cap_mw).mean(),
                "solar_cf": (t.solar_ni_gen_mw / t.solar_ni_cap_mw).mean(),
            })
    return pd.DataFrame(rows)


def run(cfg=None, out=None, **paths):
    cfg = cfg or load_config()
    s, _, _ = renewables(cfg, **paths)
    table = summarise(s, cfg["k_top"])
    if out is not None:
        table.to_csv(out / "top200_summary.csv", index=False)
    for r in table[table.basis == "gross"].itertuples():
        net = table[(table.winter == r.winter) & (table.basis == "net")].iloc[0]
        log.info(f"top200: {r.winter} overlap {r.overlap:.0%}; wind cf gross {r.wind_cf:.1%} "
                 f"vs net {net.wind_cf:.1%}; PM share {r.share_pm:.0%} vs {net.share_pm:.0%}")
    return table
