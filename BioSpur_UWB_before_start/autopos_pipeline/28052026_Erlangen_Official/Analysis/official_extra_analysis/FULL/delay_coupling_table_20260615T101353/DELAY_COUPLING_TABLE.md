# Delay-Coupling Table Reproducer

This run reproduces `main_EN.tex` table `tab:delay_coupling` through one focused median-estimator path. It does not use the session-mean 72.7 mm headline path.

## Computed Four Rows

| Case | Configuration | RMSE mm | Median mm | P95 mm |
| --- | --- | ---: | ---: | ---: |
| D1 | Vicon coords, no residual correction | 311.319 | 307.271 | 453.448 |
| D2 | Vicon coords, transplanted AutoPos delays | 252.165 | 254.878 | 394.571 |
| C | Vicon coords, re-estimated delays in-frame | 77.672 | 64.090 | 128.384 |
| A | AutoPos v4-io coords, co-fitted delays | 108.931 | 69.624 | 174.066 |

## Delta Versus Frozen Report

| Case | Delta RMSE mm | Delta median mm | Delta P95 mm |
| --- | ---: | ---: | ---: |
| D1 | 0.019 | -0.029 | 0.048 |
| D2 | -0.035 | -0.022 | -0.029 |
| C | -0.028 | -0.010 | -0.016 |
| A | 0.031 | -0.176 | 0.166 |

## Historical A-Row References

| Source | RMSE mm | Median mm | P95 mm |
| --- | ---: | ---: | ---: |
| `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_4way_comparison/reporting_checklist/tables/checklist_ablation.csv` | 108.931 | 69.624 | 174.066 |
| `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis/FULL_4way_comparison/reporting_checklist/tables/checklist_tag_static.csv` | 108.909 | 69.692 | 173.926 |

The fresh A row is expected to match `checklist_ablation.csv`, because both use the `scale_to_vicon/original_rigid_no_scale/solver_delay/T4` ablation identifier. `checklist_tag_static.csv` is the sibling raw-replay median-estimator row. The frozen report's 69.8 mm median is a sub-millimetre historical-row/rounding drift, not a 72.7 mm session-mean contamination.
