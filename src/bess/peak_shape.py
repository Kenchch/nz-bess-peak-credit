"""How wide are North Island winter peaks? Depth-duration curves.

For every winter day and each peak window (AM 07-12, PM 16-22), take the
window's highest half-hour and a line delta MW below it:

    duration_h  time the load spends above the line (half-hours x 0.5)
    battery_h   energy above the line / delta: the storage, in hours at
                delta MW, needed to hold that window down by delta MW

battery_h is the "to shave X MW you need Y hours of battery" number; it is at
most duration_h because the load is not flat above the line. Both are
summarised as P50 and P90 across days, for all winter days and for the days
that hold at least one of that winter's top-200 half-hours.
"""
import logging

import numpy as np
import pandas as pd

from . import SERIES, load_config

log = logging.getLogger(__name__)

DELTAS = np.arange(50, 801, 50)
BASES = {"gross": "l_gross_mw", "net": "l_net_mw"}
WINDOWS = ("AM", "PM")
STATS = {"p50": 50, "p90": 90}


def day_matrix(w, col):
    """(days, half-hours) array for one window, NaN-padded, and the day labels."""
    slot = w.groupby("trading_date").cumcount()
    m = w.assign(slot=slot).pivot(index="trading_date", columns="slot", values=col)
    return m.to_numpy(), m.index


def curves(load):
    """duration_h and battery_h for each day (rows) and delta (columns)."""
    peak = np.nanmax(load, axis=1)
    line = peak[:, None, None] - DELTAS[None, :, None]      # day, delta, 1
    above = np.clip(load[:, None, :] - line, 0, None)       # day, delta, slot
    duration = (load[:, None, :] >= line).sum(axis=2) * 0.5
    battery = np.nansum(above, axis=2) * 0.5 / DELTAS[None, :]
    return duration, battery


def top_days(s, col, k):
    """Days holding at least one of each winter's k highest in-window half-hours."""
    w = s[s.winter.notna() & s.in_wcm_window]
    top = w.groupby("winter")[col].nlargest(k).index.get_level_values(-1)
    return set(s.loc[top, "trading_date"])


def summarise(s, k):
    rows = []
    s = s[s.winter.notna()]
    winters = sorted(s.winter.unique().tolist())
    for basis, col in BASES.items():
        peak_days = top_days(s, col, k)
        for window in WINDOWS:
            load, days = day_matrix(s[s.period == window], col)
            duration, battery = curves(load)
            winter_of = s.groupby("trading_date").winter.first().reindex(days).to_numpy()
            is_top = np.array([d in peak_days for d in days])
            for which in ("all", "top200"):
                for label in [*winters, "all"]:
                    pick = np.ones(len(days), bool) if label == "all" else winter_of == label
                    if which == "top200":
                        pick &= is_top
                    for stat, q in STATS.items():
                        dur = np.percentile(duration[pick], q, axis=0)
                        bat = np.percentile(battery[pick], q, axis=0)
                        for i, delta in enumerate(DELTAS):
                            rows.append({"winter": str(label), "basis": basis, "window": window,
                                         "days": which, "delta_mw": int(delta), "stat": stat,
                                         "duration_h": dur[i], "battery_h": bat[i],
                                         "n_days": int(pick.sum())})
    return pd.DataFrame(rows)


def depth_for_hours(table, hours, **where):
    """Largest delta a battery of the given duration can shave, from battery_h."""
    t = table
    for k, v in where.items():
        t = t[t[k] == v]
    t = t.sort_values("delta_mw")
    return float(np.interp(hours, t.battery_h, t.delta_mw, right=np.nan))


def plot(table, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), sharey=True)
    for ax, basis in zip(axes, BASES):
        for window, colour in zip(WINDOWS, ("#1f77b4", "#d62728")):
            for stat, style in (("p50", "-"), ("p90", "--")):
                t = table[(table.winter == "all") & (table.days == "top200") & (table.basis == basis)
                          & (table.window == window) & (table.stat == stat)]
                ax.plot(t.delta_mw, t.battery_h, style, color=colour, label=f"{window} {stat.upper()}")
        for h in (1, 2, 4):
            ax.axhline(h, color="grey", lw=0.5)
        ax.set_title(f"{basis} demand, top-200 days, winters 2021-2026")
        ax.set_xlabel("MW shaved off the window peak")
    axes[0].set_ylabel("hours of storage needed at that MW")
    axes[0].legend()
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def run(cfg=None, series_path=SERIES / "ni_halfhourly.parquet", out=None):
    cfg = cfg or load_config()
    s = pd.read_parquet(series_path)
    table = summarise(s, cfg["k_top"])
    if out is not None:
        table.to_csv(out / "peak_shape.csv", index=False)
        plot(table, out / "peak_shape.png")
    for basis in BASES:
        for window in WINDOWS:
            d = {stat: depth_for_hours(table, 2, winter="all", days="top200", basis=basis,
                                       window=window, stat=stat) for stat in STATS}
            log.info(f"peak_shape: 2 h of storage shaves {d['p50']:.0f} MW (P50 day) / "
                     f"{d['p90']:.0f} MW (P90 day) off the {basis} {window} peak")
    return table
