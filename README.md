# nz-bess-peak-credit

**The question.** SOSA 2026 counts every battery of two hours or less at 60% of
nameplate in the North Island Winter Capacity Margin (NI-WCM), however many are
built. A two-hour battery can only flatten the part of the peak that is no
more than two hours wide. With more batteries the flattened top gets wider and
each extra MW holds the peak down by less. At what fleet size does the flat
60% overstate what a two-hour battery can physically deliver at the peak?

This repo rebuilds six North Island winters (2021–2026) from public Electricity
Authority EMI data and works out the most a battery fleet of a given size could
lower H100, the mean of the 200 tightest winter-daytime half-hours. The
battery has perfect foresight, so this is a ceiling. SOSA's 60% is a
behavioural factor taken from market offers; the two multiply, they do not
compete.

Status: Days 1–2 of the plan are built (data, quality gates, calibration
against SOSA, peak shape, top-200 comparison). The LP grid, publishing,
Power BI and memo are Days 3–4. Numbers are in [RESULTS.md](RESULTS.md).

## Data

All files come from EMI (`https://www.emi.ea.govt.nz/Wholesale/Datasets/…`,
served from `emidatasets.blob.core.windows.net`). Each one is pinned in
[data/manifest.csv](data/manifest.csv) by URL, download date, size and SHA-256.

| Dataset | Files | Used for |
|---|---|---|
| `Generation/Generation_MD` | monthly, 2021-04 … 2026-09 | kWh per half-hour per generating unit, with Fuel_Code and Tech_Code |
| `Metered_data/HVDC_Flows` | monthly | HVDC transfer at Benmore (BEN2201) and Haywards (HAY2201) |
| `Metered_data/Grid_export` | monthly | grid offtake at each GXP (reconciliation only) |
| `MappingsAndGeospatial/NetworkSupplyPointsTable` | `20261016_…` | POC → island |
| `Generation/GenerationFleet/Existing` | `20260702_DispatchedGenerationPlant.csv` | wind, solar and battery nameplate with in-force dates |

What the data turned out to say:

- **Batteries have their own code.** Fuel_Code `ELE`, Tech_Code `BAT`. Only
  Rotohiko (HLY0331) and Ruakākā (BRB0331) appear. The Glenbrook-Ohurua battery
  (GLN0332, 100 MW, in the fleet file from Dec 2025) has no row of its own;
  Huntly's battery was not commissioned before the data ends.
- **HVDC sign.** In `HVDC_Flows`, Flow_Direction `I` at a pole is energy
  injected into that island's AC grid and `X` is energy taken from it. So
  `hvdc_north_mw = (HAY2201 I − HAY2201 X) / 500`, positive south → north,
  net of link losses (Benmore `X` runs about 3% above Haywards `I`). The
  reconciliation confirms the sign (see below).
- Plants added since 2024 use three-letter fuel codes (`HYD`, `GEO`, `SOL`,
  `WIN`, `ELE`) while older plants keep the long names.
- The HVDC pole meters are not NSPs, so the island map also falls back to the
  POC's three-letter site code.

## Pipeline

```
ingest → stage → series → [G3 reconcile] → [G4 calibrate] → peak_shape, top200 → (Day 3) run_grid
```

| Step | Module | Output |
|---|---|---|
| ingest | `ingest.py` | `data/raw/<dataset>/*.csv`, `data/manifest.csv` |
| stage | `stage.py` | `data/staging/generation.parquet`, `hvdc.parquet`, `quarantine.parquet`, `quality_report.csv` |
| series | `series.py` | `data/series/ni_halfhourly.parquet` (one row per half-hour) |
| analyse | `reconcile.py`, `calibrate.py`, `peak_shape.py`, `top200.py` | `outputs/runs/<run_id>/` |
| LP | `credit.py` | `shave_peak`, `min_top_k`, `top_k_mean`, `capacity_credit` (numpy only) |

North Island series: `l_gross_mw = gen_ni_mw + hvdc_north_mw`, where
`gen_ni_mw` is all North Island Generation_MD injection except batteries, and
`l_net_mw = l_gross_mw − wind_mw − solar_mw`. kWh per half-hour ÷ 500 = MW.
Local timestamps are generated per trading day in Pacific/Auckland, so
daylight-saving days have 46 or 50 half-hours.

### Quality gates

| Gate | Where | Rule | On failure |
|---|---|---|---|
| G1 | ingest | every file matches its SHA-256 pin | re-download once, then stop (`--update-manifest` accepts an EMI revision) |
| G2 | stage | six row rules: date parses; TP count matches the DST calendar; kWh ≥ 0; POC maps to an island; (date, POC, network, unit) unique; fuel code known | quarantine the row; stop if > 1% of a source is rejected |
| G3 | reconcile | NI supply − NI GXP offtake within 6–11% of supply each month | warning |
| G4 | calibrate | wind and solar peak factors within 2 pp of SOSA 2026 | stop; `--accept-gap REASON` records why and carries on |

A run stopped at a gate leaves `FAILED.txt` in its run folder.

## Reproduce

Needs [uv](https://docs.astral.sh/uv/). About 275 MB of downloads.

```bash
uv sync
```

```bash
uv run bess all --accept-gap "<reason; see RESULTS.md>"
```

Steps can also run one at a time: `bess ingest`, `bess stage`, `bess series`,
`bess calibrate`, `bess analyse`. Tests run offline on synthetic fixtures:

```bash
uv run pytest -q
```

## Limitations

See [RESULTS.md](RESULTS.md#limitations).
