# Calibration against SOSA 2026 (gate G4)

Primary definition: demand `ni_gross`, selection `h100`, fleet `nz`.

| year | tech | ours | SOSA | gap (pp) |
|---|---|---|---|---|
| 2024 | wind | 33.03% | 31.40% | +1.63 |
| 2024 | solar | 4.98% | 1.49% | +3.49 |
| 2025 | wind | 28.45% | 27.20% | +1.25 |
| 2025 | solar | 5.98% | 7.14% | -1.16 |

Gate: FAIL (accepted: Solar 2024 only: ours 4.98% vs SOSA 1.49% (+3.5 pp). Every demand/window/fleet definition gives 5.0-5.7% from the two farms then reporting (Kohira, Rangitaiki: 48 MW, 2.4-2.7 MW mean output in the top 200). SOSA's 1.49% implies a denominator near 160-180 MW, close to the solar fleet in force by mid-2025 (166 MW), not the one generating in 2024. Wind (both years) and solar 2025 are within 2 pp. Solar averaged 2-14 MW in the NI top 200 in 2024-2026, too little to move the battery analysis.)

## Which definition fits SOSA best

Largest absolute gap (pp) over 2024 and 2025:

| demand | selection | fleet | wind | solar |
|---|---|---|---|---|
| nz_gross | h100 | ni | 0.33 | 4.17 |
| nz_gross | winter | ni | 0.33 | 4.17 |
| nz_gross | year | ni | 0.33 | 4.17 |
| nz_gross | h100 | nz | 0.55 | 4.17 |
| nz_gross | year | nz | 0.55 | 4.17 |
| nz_gross | winter | nz | 0.55 | 4.17 |
| ni_gross | h100 | nz | 1.63 | 3.49 |
| ni_gross | winter | nz | 1.63 | 3.49 |
| ni_gross | year | nz | 1.63 | 3.49 |
| ni_gross | year | ni | 2.08 | 3.49 |
| ni_gross | h100 | ni | 2.08 | 3.49 |
| ni_gross | winter | ni | 2.08 | 3.49 |

## Every definition tried

| year | tech | demand | selection | fleet | AM share | cf | gap (pp) |
|---|---|---|---|---|---|---|---|
| 2024 | solar | ni_gross | h100 | ni | 22% | 4.98% | +3.49 |
| 2024 | solar | ni_gross | h100 | nz | 22% | 4.98% | +3.49 |
| 2024 | solar | ni_gross | winter | ni | 22% | 4.98% | +3.49 |
| 2024 | solar | ni_gross | winter | nz | 22% | 4.98% | +3.49 |
| 2024 | solar | ni_gross | year | ni | 22% | 4.98% | +3.49 |
| 2024 | solar | ni_gross | year | nz | 22% | 4.98% | +3.49 |
| 2024 | solar | nz_gross | h100 | ni | 27% | 5.66% | +4.17 |
| 2024 | solar | nz_gross | h100 | nz | 27% | 5.66% | +4.17 |
| 2024 | solar | nz_gross | winter | ni | 27% | 5.66% | +4.17 |
| 2024 | solar | nz_gross | winter | nz | 27% | 5.66% | +4.17 |
| 2024 | solar | nz_gross | year | ni | 27% | 5.66% | +4.17 |
| 2024 | solar | nz_gross | year | nz | 27% | 5.66% | +4.17 |
| 2025 | solar | ni_gross | h100 | ni | 36% | 7.64% | +0.50 |
| 2025 | solar | ni_gross | h100 | nz | 36% | 5.98% | -1.16 |
| 2025 | solar | ni_gross | winter | ni | 36% | 7.64% | +0.50 |
| 2025 | solar | ni_gross | winter | nz | 36% | 5.98% | -1.16 |
| 2025 | solar | ni_gross | year | ni | 36% | 7.64% | +0.50 |
| 2025 | solar | ni_gross | year | nz | 36% | 5.98% | -1.16 |
| 2025 | solar | nz_gross | h100 | ni | 41% | 9.31% | +2.17 |
| 2025 | solar | nz_gross | h100 | nz | 41% | 7.38% | +0.24 |
| 2025 | solar | nz_gross | winter | ni | 41% | 9.31% | +2.17 |
| 2025 | solar | nz_gross | winter | nz | 41% | 7.38% | +0.24 |
| 2025 | solar | nz_gross | year | ni | 41% | 9.31% | +2.17 |
| 2025 | solar | nz_gross | year | nz | 41% | 7.38% | +0.24 |
| 2024 | wind | ni_gross | h100 | ni | 22% | 32.40% | +1.00 |
| 2024 | wind | ni_gross | h100 | nz | 22% | 33.03% | +1.63 |
| 2024 | wind | ni_gross | winter | ni | 22% | 32.40% | +1.00 |
| 2024 | wind | ni_gross | winter | nz | 22% | 33.03% | +1.63 |
| 2024 | wind | ni_gross | year | ni | 22% | 32.40% | +1.00 |
| 2024 | wind | ni_gross | year | nz | 22% | 33.03% | +1.63 |
| 2024 | wind | nz_gross | h100 | ni | 27% | 31.58% | +0.18 |
| 2024 | wind | nz_gross | h100 | nz | 27% | 31.95% | +0.55 |
| 2024 | wind | nz_gross | winter | ni | 27% | 31.58% | +0.18 |
| 2024 | wind | nz_gross | winter | nz | 27% | 31.95% | +0.55 |
| 2024 | wind | nz_gross | year | ni | 27% | 31.58% | +0.18 |
| 2024 | wind | nz_gross | year | nz | 27% | 31.95% | +0.55 |
| 2025 | wind | ni_gross | h100 | ni | 36% | 29.28% | +2.08 |
| 2025 | wind | ni_gross | h100 | nz | 36% | 28.45% | +1.25 |
| 2025 | wind | ni_gross | winter | ni | 36% | 29.28% | +2.08 |
| 2025 | wind | ni_gross | winter | nz | 36% | 28.45% | +1.25 |
| 2025 | wind | ni_gross | year | ni | 36% | 29.28% | +2.08 |
| 2025 | wind | ni_gross | year | nz | 36% | 28.45% | +1.25 |
| 2025 | wind | nz_gross | h100 | ni | 41% | 27.53% | +0.33 |
| 2025 | wind | nz_gross | h100 | nz | 41% | 26.83% | -0.37 |
| 2025 | wind | nz_gross | winter | ni | 41% | 27.53% | +0.33 |
| 2025 | wind | nz_gross | winter | nz | 41% | 26.83% | -0.37 |
| 2025 | wind | nz_gross | year | ni | 41% | 27.53% | +0.33 |
| 2025 | wind | nz_gross | year | nz | 41% | 26.83% | -0.37 |

## Farms not counted

- wind: kaiwaikawe_wind_farm at MPE1101: Generation_MD unit not in the fleet file
- solar: kōwhai_park_solar_farm at ISL0661: Generation_MD unit not in the fleet file
- wind: Mahinerangi (Waipori B) at HWB0331: in the fleet file, no Generation_MD unit
- solar: Omeheu Solar Farm at EDG0331: in the fleet file, no Generation_MD unit
- solar: Taiohi Solar Farm at HLY0331: in the fleet file, no Generation_MD unit
- solar: Te Puna Mauri ō Omaru at Ruawai at MTO0331: in the fleet file, no Generation_MD unit
- solar: Twin Rivers Solar Farm at KOE1101: in the fleet file, no Generation_MD unit
