# Phase 0 Tag Positioning Results

Run directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/AutoPos_simulation/phase0_solver_headroom/runs/20260615_194103`

## Configuration

- Random MC realizations per random case: `200`
- Synthetic sweeps per static position: `200`
- Workers: `8`
- One production solver invocation timing: `0.889 s`
- Estimated MC wall time before run: `111.7 s`

## Required Readouts

1. BASELINE sanity: median 3D `0.274 mm`, P95 `0.556 mm`, pass_lt_5mm `True`.
2. E3 delay/CIR-blind contribution: median 3D `265.651 mm`, P95 `340.336 mm`.
3. E4_REAL residual/CIR-addressable contribution: median 3D `55.204 mm`, P95 `150.571 mm`; E3/E4 median ratio `4.812`.
4. E4_TAIL rejection: injected severe-tail median `217.965 mm`, P95 `405.385 mm`; position median `35.437 mm`, P95 `110.652 mm`; verdict `bounded_by_solver`.
5. Low-redundancy single +400 mm bias on `ID19-G`: position shift `43.056 mm`.
6. COMBINED_REAL vs real 72.7 mm headline: combined median `314.081 mm`, real median `72.691 mm`, ratio `4.321`.

## Aggregate Case Summary

| Case | n | median 3D mm | P95 3D mm | horizontal median mm | vertical median mm |
|---|---:|---:|---:|---:|---:|
| BASELINE | 1 | 0.274 | 0.556 | 0.182 | 0.158 |
| COMBINED_REAL | 200 | 314.081 | 458.069 | 83.004 | 310.879 |
| E1 | 200 | 2.113 | 3.832 | 1.385 | 1.213 |
| E2_K1 | 200 | 31.194 | 47.579 | 20.570 | 21.247 |
| E2_K2 | 200 | 43.283 | 211.354 | 29.515 | 28.697 |
| E2_K3 | 200 | 134.885 | 325.188 | 73.753 | 71.032 |
| E3 | 1 | 265.651 | 340.336 | 84.886 | 256.344 |
| E4_REAL | 1 | 55.204 | 150.571 | 33.538 | 42.790 |
| E4_TAIL | 1 | 35.437 | 110.652 | 18.011 | 28.854 |
| LOW_RED_SINGLE_BIAS | 1 | 0.274 | 0.596 | 0.182 | 0.172 |
