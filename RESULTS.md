# Results

Sections 1–4 were first built by `uv run bess all` from an empty
`data/` (every download matched its pin in `data/manifest.csv`).

Everything below now comes from one run of `uv run bess analyse --accept-gap …`
on commit d29a686, after a four-part review of the §5 credit grid:
`outputs/runs/20261019T032856Z_d29a686/`, published by gate G6 to `outputs/current/` (which is
committed, with `run_manifest.json` recording the code, config and input
hashes). Every LP, check and timing is in `run_grid.log`. A trace run
(`scripts/trace_pipeline.py`) rebuilt every output from the raw files and
matched the run exactly; a run on the reviewed but uncommitted code gave an
identical `credit_table.csv`.

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

> Solar 2024 only: ours 4.98% vs SOSA 1.49% (+3.5 pp). Every demand/window/fleet definition gives 5.0-5.7% from the two farms then reporting (Kohira, Rangitaiki: 48 MW, 2.4-2.7 MW mean output in the top 200). SOSA's 1.49% implies a denominator near 160-180 MW, close to the solar fleet in force by mid-2025 (166 MW), not the one generating in 2024. Wind (both years) and solar 2025 are within 2 pp. Solar averaged 2-14 MW in the NI top 200 in 2024-2026, too little to move the battery analysis.

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
350 / 210 MW, morning 400 / 180 MW). Winter by winter, the median evening
figure runs from 265 MW (2026) to 420 MW (2022). At roughly the 2026 fleet size
(~335 MW), two hours covers a median evening peak but not the widest tenth.
That is the shape behind a falling credit curve; the LP in §5 puts numbers
on it. Figure: [`outputs/current/peak_shape.png`](outputs/current/peak_shape.png).

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

## 5. Battery capacity credit

For each winter, a battery fleet of P MW and d hours charges and discharges
on every half-hour from 1 April (starting full) to minimise H100, the mean of
the 200 highest half-hours in the NI-WCM window (07:00–22:00). It knows the
load in advance, so this is a **physical ceiling**, not a forecast of what
batteries will do. CC = (H100 before − H100 after) / P.

Conventions:

- **A d-hour battery delivers P × d MWh to the grid.** The store therefore
  holds P × d / √η, with η = 0.88 round trip. This matches SOSA's
  description of the existing batteries as running "about two hours at full
  generation output". Counting P × d as stored energy instead (the first
  version of this grid) lowers every CC by about 3 pp and moves p* down by
  60–80 MW.
- **Load is taken before the batteries already on the grid**
  (`l_* + bess_mw`). At the gross top 200 this adds 0.0 MW on average in
  2021–2024, 2.4 MW in 2025 and 23 MW in 2026.
- **Base winters are 2021–2025.** 2026 is the cross-check: the winter is
  incomplete (to 30 September) and Glenbrook's 100 MW battery has no row in
  Generation_MD.

1,218 LPs (756 base + 462 sensitivity), all optimal, all through G5:
- replayed H100 matches the LP to 1e-4 MW
- state of charge matches to 2e-10 MWh
- no off-window half-hour pushed above the H100 line
- none of the 174 curves rises with P
- no longer battery gets less CC than a shorter one at the same P

### Two-hour batteries against SOSA's 60%

CC for the NI-WCM window, mean over 2021–2025:

| Fleet P | 35 MW | 135 MW | 335 MW | 500 MW | 750 MW | 1,000 MW | 1,500 MW |
|---|---|---|---|---|---|---|---|
| Gross demand | 81.8% | 76.8% | 66.7% | 59.9% | 50.5% | 42.9% | 33.6% |
| Net demand | 77.4% | 72.5% | 63.5% | 57.7% | 50.2% | 43.4% | 34.2% |

**Headline: on the 2021–2025 average, a two-hour fleet's physical ceiling
falls to SOSA's 60% at about 500 MW on gross demand and 430 MW on net
demand.** Both figures are interpolated between the 335 and 500 MW grid
points. The North Island will have about 330–335 MW of grid batteries once
Huntly's is commissioned: Rotohiko 35 MW, which is a one-hour battery,
Ruakākā 100 MW, Glenbrook 100 MW and Huntly about 100 MW. So p* lies above
today's fleet but inside the battery pipeline SOSA 2026 already counts as
committed.

The average hides a wide spread between winters:

| Winter | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 (check) |
|---|---|---|---|---|---|---|
| Gross p* | 234 MW | 459 MW | 611 MW | 610 MW | 642 MW | 492 MW |
| Net p* | 265 MW | 362 MW | 507 MW | 559 MW | 568 MW | 408 MW |

2021 is the lowest winter at every P on both bases. In a winter like 2021,
the ceiling is already below 60% at today's fleet size.

The same result, read three ways:

- **(a) The ceiling against 60% (the headline above).** Real batteries also
  hold reserves, do not know the load in advance and are not always full.
  Their contribution is therefore at or below these numbers, so above p* the
  flat 60% is too high whatever the behaviour.
- **(b) Same behaviour, bigger fleet.** If SOSA's 60% is right for a 35 MW
  battery the size of Rotohiko, keep that behaviour fixed and scale it by
  CC(P) / CC(35 MW). That gives about **49% at 335 MW** and **31–34% at
  1,000 MW** (gross 48.7% / 31.3%, net 49.2% / 33.6%). The ratio
  CC(335) / CC(35) is 0.81–0.82 under either energy convention and either
  basis, so this reading is the most robust of the three.
- **(c) SOSA's own way of scoring.** SOSA scores wind and solar by their mean
  output over the original top 200, and makes no re-ranking after the
  resource acts. Scoring the same two-hour dispatch that way gives 72.4% at
  335 MW gross and 68.4% net, about 3–6 pp above CC. p* then rises to about
  615 MW gross and 580 MW net. This score is higher because it does not
  count the shoulder half-hours that rise into the top 200 once the peak has
  been flattened.

Even a 35 MW fleet does not reach 100%. The top 200 half-hours fall on 35–46
days, four or five a day at the median and up to 18. A two-hour battery
cannot cover the longest of those days.

### Durations, windows and sensitivities

These are 2021–2025 means. "below" means below the factor even at 35 MW.

| Case | Gross | Net |
|---|---|---|
| 2 h vs 60%, p* | **497 MW** | **434 MW** |
| 2 h, lowest single winter (2021) | 234 MW | 265 MW |
| 2 h, 2026 cross-check | 492 MW | 408 MW |
| 1 h vs 60% | below (48.6% at 35 MW) | below (46.0%) |
| 4 h vs 80%, p* | 529 MW | 550 MW |
| 2 h, H100 over the top 50 half-hours instead of 200 | 690 MW (CC 90% at 35 MW) | 786 MW |

Notes on the rows:

- **1 hour.** SOSA 2026 kept SOSA 2025's assumption for batteries: 60% for
  2 hours or less, 80% for longer. Its appendix adds that an analysis of
  Rotohiko's offers, with a state-of-charge model, over two winters "aligns
  closely" with that assumption. Rotohiko is 35 MW / 35 MWh, a one-hour
  battery. On the H100 measure, a one-hour battery's ceiling is 46–49% even
  at 35 MW. SOSA's state-of-charge model may debit energy across consecutive
  peak half-hours differently, or may score per half-hour like reading (c).
  This is a question to ask SOSA, not a conclusion to assert.
- **Top 50.** If the risk sits in the extreme tail, the 25 tightest hours
  rather than 100, batteries count for more and p* moves out to roughly
  700–800 MW. Which tail the standard should weigh is a policy choice, and
  H100 is the one the SSAD makes.
- **usable70.** If only 70% of the rated energy is usable at the peak, the
  battery behaves like a 1.4-hour one. Its CC sits between the 1-hour and
  2-hour results: 64% at 35 MW and 51% at 335 MW on gross, 60% and 48% on
  net. No p* is given. Comparing a hold-back with SOSA's 60% would count
  behaviour twice, because the 60% already includes it.
- **Solar.** The North Island had no grid-scale solar in 2021–2023. In 2024
  it peaked at 48 MW, in 2025 at 81 MW, and in 2026 at 258 MW. Tripling
  observed solar therefore only matters in 2025–2026. It lifts the morning
  CC by 2–6 pp, lifts the whole-window CC by up to 4 pp at large P, and
  leaves the evening within 0.75 pp. The whole-window p* for 2026 rises from
  408 MW to 465 MW.

**Morning and evening on their own** are diagnostics of peak shape, not SOSA
measures, so no p* is headlined for them. On the 2021–2025 mean, the two-hour
CC at 335 MW is:

| Window | Gross | Net |
|---|---|---|
| Morning (07–12) | 64.9% | 60.6% |
| Evening (16–22) | 62.7% | 59.2% |

The two are close. On net demand, both fall below 60% somewhat earlier than
the whole window does.

### The charging rule, and why it is there

H100 only looks at 07:00–22:00. The first version of the LP was free to
charge at full power just before 07:00. In the two-hour 335 MW runs it left
72 such half-hours on gross demand and 83 on net demand above the H100 line,
across six winters, all at 06:00–06:30. `credit.min_top_k_no_new_peaks` now
applies the following rule to every off-window half-hour:

- **Below the H100 line:** charging may lift it at most to the line. This is
  a constraint inside the LP.
- **At or above the line:** no charging.

Which half-hours sit below the line depends on where the line ends up, so the
split is iterated to a fixed point. Every LP converged, in at most 7 passes.
An earlier version froze the charging cap at the lowest line it had seen.
That version ranked some four-hour batteries below two-hour ones; the new
duration gate in G5 now catches that. `cc_uncapped` keeps the first-pass
value, so the rule's effect is visible:

- **Two-hour batteries, whole window:** no change at all.
- **Four-hour batteries, whole window:** up to 5.9 pp lower.
- **Single windows with large four-hour fleets:** up to 36 pp lower in the
  morning and 23 pp in the evening. A fleet that charged through the other
  peak would otherwise have been credited for flattening this one.

The saved dispatch (`dispatch_d2.parquet`) comes from a tidy second pass. It
keeps the same H100 and uses the least charge plus discharge, so it shows no
cycling that the objective cannot see.

## 6. What the visible batteries actually delivered

Generation_MD carries two grid batteries as their own units: Ruakākā (100 MW /
200 MWh) and Rotohiko (35 MW / 35 MWh). Glenbrook (100 MW, from December 2025)
and Huntly (100 MW, commissioning from July 2026) have no rows. Over the 200
tightest half-hours of the load before batteries (gross, NI-WCM window):

| Winter | Units counted | Nameplate | Mean output | Share of nameplate | Ceiling for a 135 MW, 2 h fleet (SOSA-style score) |
|---|---|---|---|---|---|
| 2025 | Ruakākā (operating from July) | 100 MW | 2.5 MW | 2.5% | 81.8% |
| 2026 (to Sep) | Ruakākā + Rotohiko | 135 MW | 25.6 MW | **19.0%** | **75.5%** |

The batteries delivered about a quarter of the energy a perfect-foresight
fleet of their size would have at those half-hours. That is not a behavioural
factor on its own: Generation_MD is energy only, and Transpower's Winter 2025
security of supply review
notes that New Zealand batteries earn much of their peak value as FIR / SIR
reserve, which never appears as energy. It does say that energy at the peak
is a small part of what today's batteries do, so a capacity factor near 60%
rests on their reserve role and on availability, not on discharge.

## Limitations

1. **Grid-view demand.** `l_gross_mw` is North Island Generation_MD injection
   plus HVDC. Rooftop solar and embedded generation that Generation_MD does
   not carry are already netted off, so H100 here (~4,400–4,550 MW) is lower
   than SOSA's gross, loss-inclusive H100. Shapes are comparable; levels are
   not.
2. **Existing batteries are in the demand.** By the series contract,
   `gen_ni_mw` leaves out battery output, so `l_gross_mw` is demand after
   visible battery discharge: 2.4 MW on average across the 2025 top 200 and
   23 MW in 2026. The grid adds `bess_mw` back. Glenbrook's 100 MW battery
   has no row, so its discharge stays invisible and lowers 2026 demand; that
   is why 2026 is a cross-check rather than part of the base result (2025 is
   in the base: Glenbrook came online after that winter).
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
   by the window's length (5 h morning, 6 h evening). The chronological LP in
   §5 drops these simplifications.
7. **History is not the future.** Demand growth, electrification and
   time-of-use pricing will change peak shapes; 2026 is an incomplete winter.
8. **The credit is a ceiling.** It assumes perfect foresight, one aggregate
   battery, no reserve co-optimisation (FIR/SIR), no network or HVDC limits,
   and a full battery on 1 April. Forcing the battery to end the winter full
   changes CC by less than 1e-12. Real batteries can only do worse, so the
   useful conclusion runs one way: where the ceiling is below 60%, 60% is
   too high.
9. **The measure.** CC is the fall in H100 per MW. This matches how NI-WCM
   adds capacity against H100 demand, and it re-ranks the top 200 after the
   battery has acted. SOSA scores intermittent generation by mean output
   over the original top 200, and its battery method uses offers plus a
   state-of-charge model. Reading (c) shows that the choice of measure moves
   p* by about 120–150 MW, and the top-50 sensitivity shows that the choice
   of tail moves it by more.
10. **Energy convention.** Rated MWh is taken as energy delivered to the
    grid. Counting it as stored energy lowers CC by about 3 pp.
11. **Winters.** p* ranges from 234 MW to 642 MW (gross) across single
    winters. The headline is a five-winter mean, not a prediction for any
    one winter.
12. **p\* is interpolated** linearly between grid points (35, 135, 335, 500,
    750, 1,000, 1,500 MW). Solving the earlier convention directly at the
    interpolated p* showed an error of about 5 MW.
