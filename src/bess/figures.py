"""The two memo figures, drawn from outputs/current.

fig_credit_curve.png   two-hour CC against fleet size: the 2021-2025 mean on
                       gross and net demand, the range across those winters,
                       SOSA's 60% and the ~335 MW fleet
fig_p_star_by_winter.png  where each winter's two-hour curve falls to 60%

Colours are the first two slots of a colour-blind-checked categorical palette
(blue, orange); reference lines are grey and every series is also labelled.
"""
import logging

import pandas as pd

from . import OUTPUTS, ROOT, load_config, logs

log = logging.getLogger(__name__)

GROSS, NET, GREY, INK, MUTED = "#2a78d6", "#eb6834", "#8a8984", "#0b0b0b", "#52514e"
COLOURS = {"gross": GROSS, "net": NET}
LABELS = {"gross": "Gross demand", "net": "Net demand (minus wind and solar)"}
FLEET_2026 = 335


def _style(plt):
    plt.rcParams.update({
        "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": GREY,
        "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.color": "#e4e3df", "grid.linewidth": 0.6, "legend.frameon": False,
    })


def credit_curve(table, cross, cfg, path):
    import matplotlib.pyplot as plt
    _style(plt)
    base = cfg["credit"]["base_winters"]
    level = cfg["sosa_reference"]["bess_factor"]["le_2h"]
    t = table[(table.sensitivity == "base") & (table.window == "all") & (table.d_h == 2)
              & table.winter.isin(base)]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    for basis, colour in COLOURS.items():
        g = t[t.basis == basis].groupby("p_mw").cc
        lo, mean, hi = g.min() * 100, g.mean() * 100, g.max() * 100
        ax.fill_between(mean.index, lo, hi, color=colour, alpha=0.12, linewidth=0)
        ax.plot(mean.index, mean, color=colour, linewidth=2, marker="o", markersize=4,
                label=f"{LABELS[basis]}: {len(base)}-winter mean, band = range")
        r = cross[(cross.basis == basis) & (cross.sensitivity == "base") & (cross.window == "all")
                  & (cross.d_h == 2) & (cross.winters == "base") & (cross.stat == "mean")].iloc[0]
        if r.p_star_note == "in_grid":
            ax.plot([r.p_star_mw], [level * 100], marker="o", markersize=7, color=colour,
                    markeredgecolor="white", markeredgewidth=1.5, zorder=5)
            ax.annotate(f"p* ≈ {r.p_star_mw:,.0f} MW", (r.p_star_mw, level * 100),
                        xytext=(8, 8) if basis == "gross" else (-88, -16),
                        textcoords="offset points", color=INK, fontsize=8.5)
    ax.axhline(level * 100, color=GREY, linestyle="--", linewidth=1)
    ax.text(1500, level * 100 + 1.2, "SOSA 2026: 60% for batteries of 2 h or less",
            ha="right", color=MUTED, fontsize=8)
    ax.axvline(FLEET_2026, color=GREY, linestyle=":", linewidth=1)
    ax.text(FLEET_2026 + 15, 25, "~335 MW on the grid\nonce Huntly's battery\nis commissioned",
            color=MUTED, fontsize=8, va="bottom")
    ax.set_xlim(0, 1550)
    ax.set_ylim(20, 100)
    ax.set_xlabel("North Island grid battery fleet, two-hour batteries (MW)")
    ax.set_ylabel("Capacity credit ceiling (% of MW)")
    ax.set_title("A two-hour battery counts for less as the fleet grows",
                 loc="left", color=INK, fontsize=10.5, fontweight="bold")
    ax.legend(loc="upper right", fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def p_star_by_winter(cross, cfg, path):
    import matplotlib.pyplot as plt
    import numpy as np
    _style(plt)
    c = cross[(cross.sensitivity == "base") & (cross.window == "all") & (cross.d_h == 2)
              & (cross.stat == "single")].copy()
    c["winter"] = c.winters.astype(int)
    winters = sorted(c.winter.unique())
    check = set(cfg["credit"]["check_winters"])
    x = np.arange(len(winters))
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    for i, (basis, colour) in enumerate(COLOURS.items()):
        v = c[c.basis == basis].set_index("winter").reindex(winters).p_star_mw
        bars = ax.bar(x + (i - 0.5) * 0.38, v, width=0.36, color=colour,
                      label=LABELS[basis], edgecolor="white", linewidth=1)
        for b, w in zip(bars, winters):
            if w in check:
                b.set_hatch("///")
                b.set_alpha(0.6)
            if np.isfinite(b.get_height()):  # no p* (curve never reaches 60% in the grid): no bar
                ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 12,
                        f"{b.get_height():.0f}", ha="center", fontsize=7.5, color=MUTED)
    # Fixed x range: with no p* at all there are no bars to autoscale on, the axis shrinks
    # to +-0.05 and the fleet label at -0.55 lands outside the figure (tight_layout gives up)
    ax.set_xlim(x[0] - 0.66, x[-1] + 0.66)
    ax.axhline(FLEET_2026, color=GREY, linestyle=":", linewidth=1)
    ax.text(-0.55, FLEET_2026 + 12, "~335 MW fleet", ha="left", color=MUTED, fontsize=8)
    if pd.notna(c.p_star_mw.max()):          # room above the bars for the legend
        ax.set_ylim(0, c.p_star_mw.max() * 1.3)
    ax.set_xticks(x, [f"{w}" + (" (to Sep,\ncross-check)" if w in check else "") for w in winters])
    ax.set_ylabel("p*: fleet size where the\nceiling falls to 60% (MW)")
    ax.set_title("Each winter on its own: the spread is wide, and the worst winter binds first",
                 loc="left", color=INK, fontsize=10.5, fontweight="bold")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper center", ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=200)
    plt.close(fig)


def run(cfg=None, current=OUTPUTS / "current", out=ROOT / "memo"):
    cfg = cfg or load_config()
    import matplotlib
    matplotlib.use("Agg")
    table = pd.read_csv(current / "credit_table.csv")
    cross = pd.read_csv(current / "crossings.csv")
    out.mkdir(parents=True, exist_ok=True)
    credit_curve(table, cross, cfg, out / "fig_credit_curve.png")
    p_star_by_winter(cross, cfg, out / "fig_p_star_by_winter.png")
    log.info(f"figures: {logs.rel(out / 'fig_credit_curve.png')}, "
             f"{logs.rel(out / 'fig_p_star_by_winter.png')}")
    return out
