# Three Dimensions Completion

Generated: 2026-06-20T18:56:36

## 1. Executive Summary

- Facing metadata found in `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/FULL-COMPARE-1000-production-T4-real/tables/static_all_captures.csv`: 24 positions, 4 facing groups (ABEF=6, ADHE=6, BCGF=6, CDHG=6). Format is anchor-face labels; incidence vectors were derived from each anchor-face centroid.
- Baseline reproduced with raw-frame V3 convention: median 44.5 mm, P95 164.1 mm, RMSE 81.5 mm.
- Worst positions: ID06=192.4mm, ID01=164.5mm, ID04=162.3mm, ID12=123.4mm, ID07=95.9mm.
- Best new median pipeline: quality_filtered + lower_trim + V5 (44.2 mm). Best P95 pipeline: V4 p50 LOO (110.6 mm).

## 2. Facing Direction Results

- Kruskal-Wallis across facing groups: H=0.860, p=0.8351. ANOVA: F=0.605, p=0.6194.
- Incidence angle vs range residual Spearman r=0.086, p=0.2369; incidence angle vs raw skewness Spearman r=0.014.
- Worst facing-anchor combinations by median absolute residual are: BCGF/A=122.4mm, CDHG/B=112.8mm, CDHG/C=104.1mm, ABEF/D=100.5mm, ABEF/C=88.6mm.

## 3. Temporal Structure Results

- Top-tail links classified as: {'mixed_or_uniform': 9, 'uniform_low_tail': 1}. Autocorrelation and timeseries plots are in `t6_autocorrelation.csv` and `figures/t6_*`.
- Clean-window positioning: median 62.1 mm, P95 173.3 mm, RMSE 84.7 mm.
- Changepoint LOS-only positioning: median 64.1 mm, P95 175.4 mm, RMSE 85.0 mm.

## 4. Quality_Percent Results

- Valid frame rows analyzed: 228265. quality_percent is saturated at 100 for 216592 frames (94.9%); the bottom20/top80 split uses rank percentiles when the P20 threshold ties at 100. Quality vs frame residual Spearman r=-0.007.
- Quality-weighted positioning: median 63.9 mm, P95 176.7 mm, RMSE 88.7 mm.
- Quality>80 then lower_trim_20: median 44.2 mm, P95 164.6 mm, RMSE 81.6 mm.

## 5. Combined Pipeline Results

| Pipeline | LOO Median mm | P95 mm | RMSE mm | Notes |
| --- | --- | --- | --- | --- |
| V5 p50 baseline | 67.800 | 160.500 | 86.400 | existing locked result |
| V4 p50 LOO | 57.900 | 110.600 | 74.400 | existing locked result |
| lower_trim_20 + V5 + Huber30 | 44.485 | 164.135 | 81.537 | recomputed exact raw-frame V3 LOO convention |
| + residual-gated anchor exclusion | 53.794 | 118.631 | 68.920 | exclude largest truth residual per position; diagnostic/oracle rule |
| clean_window + V5 | 62.060 | 173.300 | 84.687 | K=50, MAD<30 mm, lowest stable median |
| changepoint LOS-only + V5 | 64.143 | 175.416 | 85.010 | local binary segmentation, LOS-like segment median |
| quality_weighted + V5 | 63.927 | 176.746 | 88.695 | quality_percent weighted mean range |
| quality_filtered + lower_trim + V5 | 44.238 | 164.605 | 81.648 | quality_percent>80 then lower_trim_20 |

## 6. New Claims

- Tail behavior is concentrated in a small set of positions and links rather than being evenly distributed.
- Facing direction is now quantifiable through anchor-face incidence angle; the correlation table determines whether this is a strong explanatory variable.
- Temporal structure is not just distributional: top-tail links have measurable autocorrelation/run structure, and clean-window/changepoint variants quantify whether exploiting that helps.
- quality_percent is now tested directly against raw-frame residuals and positioning metrics instead of being treated as unused metadata.

## 7. Updated Engineering Recommendations

- Do not replace lower_trim_20 with quality weighting unless the T12 row beats both median and tail metrics.
- Treat residual-gated exclusion as diagnostic/oracle until a deployable residual proxy is available; it uses held-position truth residuals in this run.
- If facing/incidence remains predictive, add a controlled antenna-directivity calibration sweep before claiming the tail is purely geometric.
- If temporal variants improve P95 without hurting median, promote them to the next blind-validation run.

## 8. Implications for Paper 1

- The narrative should separate median improvement from tail risk. The lower_trim_20 result is still strong on median, but the P95 tail requires explicit attribution.
- Facing direction and temporal coherence are now candidate mechanisms that can either support or constrain the coupling/directivity explanation.
- quality_percent should be reported as tested; if its correlations are weak, that negative result is useful because it explains why raw-frame distribution methods outperform metadata-only filtering.

## Verification

- Facing direction data found and loaded.
- T1-T5 tail, facing, incidence, and anchor exclusion tables written.
- T6-T8 temporal tables and PNG figures written.
- T9-T11 quality tables and PNG figures written.
- T12 master comparison and T13 report written.
- CPU workers used: 12; GPU solve device: cuda:0. See `tables/resource_summary.csv` and `tables/resource_utilization_log.csv`.
