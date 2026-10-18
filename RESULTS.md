# Results — Days 1–2

Built by `uv run bess all` from an empty `data/` (every download matched its
pin in `data/manifest.csv`), then `uv run bess analyse --accept-gap …` with the
final wording of the G4 reason. Outputs: `outputs/runs/RUN_ID/`. The battery
credit curve and p* come on Day 3; nothing here is a credit number yet.

## 1. Data and quality gates

| | |
|---|---|
| Inputs | 200 EMI files, 2021-04 … 2026-09, 274 MB, all SHA-256 pinned (G1) |
| Generation_MD | 169,124 unit-days, **0 quarantined** (G2 limit 1%) |
| HVDC_Flows | 8,036 meter-days, **0 quarantined** |
| Series | 96,432 half-hours; six winters, 2021–2025 complete, 2026 to 30 Sep only |
| DST days | 6 days of 46 and 6 of 50 half-hours, timestamps checked |

**Batteries have their own Fuel_Code** (`ELE`, Tech_Code `BAT`), but only two
appear: Rotohiko (HLY0331) and Ruakākā (BRB0331). Rotohiko's output is
essentially zero in Generation_MD until November 2025. The Glenbrook-Ohurua
battery (100 MW, in the fleet file from December 2025) has no row of its own.

**HVDC sign.** Flow_Direction `I` at Haywards (HAY2201) is south → north;
`hvdc_north_mw = (HAY2201 I − X) / 500`.

**Reconciliation (G3).** Every month's North Island supply (generation +
HVDC north) exceeds GXP offtake by 6.8–10.0% (mean 8.3%, sd 0.75 pp); all 66
months sit inside the 6–11% band. Read the HVDC the other way round and the
same gap would swing with a 20.7 pp standard deviation, so the sign is right.
The gap is larger than SOSA's 2.88% North Island loss factor because
Generation_MD includes embedded plants whose output never reaches a GXP.

## 2. Calibration against SOSA 2026 (G4)

SOSA appendix A3.6/A3.7: average output in the 200 highest-demand half-hours
of 2024 and of 2025, as a share of installed capacity.

Primary definition, fixed before the run (North Island grid demand, H100
window, national fleet):

| | 2024 ours | 2024 SOSA | gap | 2025 ours | 2025 SOSA | gap |
|---|---|---|---|---|---|---|
| Wind | 33.03% | 31.4% | **+1.63 pp** | 28.45% | 27.2% | **+1.25 pp** |
| Solar | 4.98% | 1.49% | **+3.49 pp** | 5.98% | 7.14% | **−1.16 pp** |

Three of four within 2 pp; **solar 2024 is not**, and the run carries on with
`--accept-gap` and this reason:

> REASON

What the other definitions showed (all in `calibration.md`):

- **SOSA probably picked peaks on national demand.** With national
  generation as the demand, wind lands at 31.95% / 26.83% (+0.55 / −0.37 pp)
  and solar 2025 at 7.38% (+0.24 pp). Only solar 2024 stays off.
- **The window does not matter.** In both years the 200 highest half-hours on
  either demand fall inside 1 Apr – 31 Oct, 07:00–22:00, so calendar year,
  winter and H100 selections give identical numbers.
- Wind capacity counted: 1,221 MW nationally (1,120 MW North Island).
  Mahinerangi (36 MW) is in the fleet file but has no Generation_MD unit.

## 3. Peak shape: how wide are the peaks?

For each winter day, the hours of storage needed (at that MW) to hold the
morning (07–12) or evening (16–22) peak down by a given amount. Days that hold
at least one of their winter's top-200 half-hours, all six winters pooled
(224 days on gross demand):

| Gross demand | 100 MW | 200 MW | 300 MW | 400 MW | 600 MW | 800 MW |
|---|---|---|---|---|---|---|
| Evening, median day | 1.05 h | 1.44 h | 1.79 h | 2.13 h | 2.80 h | 3.39 h |
| Evening, P90 day | 1.37 h | 1.86 h | 2.25 h | 2.67 h | 3.36 h | 3.88 h |
| Morning, median day | 0.91 h | 1.27 h | 1.63 h | 1.98 h | 2.60 h | 3.20 h |
| Morning, P90 day | 1.49 h | 2.23 h | 2.83 h | 3.22 h | 3.81 h | 4.10 h |

**Headline: two hours of storage holds the median top-200 evening peak down
by about 360 MW, but only about 230 MW on the widest tenth of days**
(morning: 410 MW / 170 MW). Net demand gives almost the same curves (evening
350 / 210 MW, morning 400 / 180 MW). At roughly the 2026 fleet size
(~335 MW), two hours covers a median evening peak but not the widest tenth.
That is the shape behind a falling credit curve; Day 3's LP will put numbers
on it. Figure: `outputs/runs/RUN_ID/peak_shape.png`.

## 4. Top 200 on gross vs net demand

The 200 highest half-hours in the NI-WCM window, picked on gross
(`l_gross_mw`) and on net (`l_net_mw` = gross − wind − solar) demand:

| Winter | Overlap | H100 gross (MW) | Wind CF at gross top 200 | at net top 200 | Evening share gross / net |
|---|---|---|---|---|---|
| 2021 | 64% | 4,434 | 27.8% | 15.5% | 66% / 69% |
| 2022 | 60% | 4,399 | 33.2% | 13.3% | 76% / 65% |
| 2023 | 69% | 4,454 | 28.2% | 14.8% | 66% / 67% |
| 2024 | 56% | 4,424 | 32.4% | 12.9% | 78% / 68% |
| 2025 | 68% | 4,457 | 29.3% | 16.2% | 64% / 64% |
| 2026* | 74% | 4,554 | 26.8% | 15.9% | 66% / 69% |

\* April–September only.

Between a quarter and nearly half of the tightest net-demand half-hours are not
among the tightest gross-demand ones. Wind delivers roughly half as much of
its capacity in the net top 200 as in the gross top 200. Part of that is by
construction (high net demand selects still periods). It is the effect
Mercury's submission pointed at: on a net basis wind's peak factor would be
nearer 13–16% than SOSA's 29.3%. In every winter 64–78% of the top 200 fall
in the evening (16:00–22:00).

## Limitations

1. **Grid-view demand.** `l_gross_mw` is North Island Generation_MD injection
   plus HVDC. Rooftop solar and embedded generation that Generation_MD does
   not carry are already netted off, so H100 here (~4,400–4,550 MW) is lower
   than SOSA's gross, loss-inclusive H100. Shapes are comparable; levels are
   not.
2. **Existing batteries are in the demand.** By the series contract,
   `gen_ni_mw` leaves out battery output, so `l_gross_mw` is demand after
   visible battery discharge: 2.4 MW on average across the 2025 top 200 and
   23 MW in 2026. Glenbrook's 100 MW battery has no row, so any discharge
   from it is invisible and lowers 2026 demand too. Day 3 must decide whether
   to add `bess_mw` back (load before batteries ≈ `l_gross_mw + bess_mw` at
   peak), and should treat 2025–2026 as a cross-check on 2021–2024.
3. **Fleet history is approximate.** The fleet file's in-force dates are
   record dates, not staged commissioning: Turitea counts at 222 MW from 2021
   and Harapaki at 176 MW through its 2024 ramp-up. Wind factors before 2025
   are slightly low.
4. **Solar 2024 calibration gap** of 3.5 pp, explained above, not resolved.
5. **Generation_MD is modelled.** EMI maps metered injection onto plants and
   splits some nodes (e.g. HLY2201) by estimate. Rotohiko is under-recorded
   before November 2025.
6. **Peak shape is a one-window view.** It assumes a full battery at the start
   of each window, no losses, no recharging inside the window, and is capped
   by the window's length (5 h morning, 6 h evening). The chronological LP on
   Day 3 drops these simplifications.
7. **History is not the future.** Demand growth, electrification and
   time-of-use pricing will change peak shapes; 2026 is an incomplete winter.
