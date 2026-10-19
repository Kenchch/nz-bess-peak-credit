# Power BI report

`bess_peak_credit.pbix` is built by hand in Power BI Desktop from the published
tables in `outputs/current/`. It reads them through one Power Query parameter,
`DataFolder`, so pointing it at another copy of `outputs/current/` is a single
change.

| Page | Question | Main visuals |
|---|---|---|
| Credit curve | How much does each MW count as the fleet grows, and where does it fall to 60%? | CC against fleet size (5-winter mean, SOSA-style score, ratio reading), 60% and 335 MW reference lines, p* cards |
| Peak explorer | What does the battery do to one day's peak? | load before and after the battery, charge and discharge, for one fleet size and basis |
| Winters & checks | How much do winters differ, and can the data be trusted? | p* by winter, top-200 gross vs net, calibration against SOSA, observed batteries vs ceiling |

Model: `credit_table`, `crossings` and `pbi_dispatch` as facts; `dim_winter`,
`dim_p`, a basis dimension and a date dimension shared between them.
`pbi_dispatch.parquet` carries naive New Zealand local time, because Power
Query cannot apply NZ daylight saving to the UTC timestamps in
`dispatch_d2.parquet`.

Numbers to reconcile against `RESULTS.md` (gross, 2 h, whole window, base,
2021–2025): p* 497 MW (lowest winter 234 MW, 2021), CC 66.7% at 335 MW and
81.8% at 35 MW; net p* 434 MW. `credit_table` has 1,218 rows and
`pbi_dispatch` 842,016.
