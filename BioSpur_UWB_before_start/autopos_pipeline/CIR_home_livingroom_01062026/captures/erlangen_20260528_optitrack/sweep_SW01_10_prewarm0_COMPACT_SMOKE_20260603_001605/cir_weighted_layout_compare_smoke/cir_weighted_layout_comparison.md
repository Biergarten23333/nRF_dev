# CIR Weighted Layout Comparison

- Sweep dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605`
- CIR pair weights: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605/compact_cir_analysis/cir_pair_weights.json`
- Pairs CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605/cir_weighted_layout_compare_smoke/pairs_all.csv`

| solve | RMS edges mm | RMS inlier mm | outliers | CIR pairs | layout |
|---|---:|---:|---:|---:|---|
| baseline | 79.437 | 79.437 | 0 | 0 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605/cir_weighted_layout_compare_smoke/baseline_v3_box/anchor_layout_v3_box.json` |
| cir_weighted | 82.467 | 82.467 | 0 | 28 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605/cir_weighted_layout_compare_smoke/cir_weighted_v3_box/anchor_layout_v3_box.json` |

## Notes

- This report only proves the same SW matrix can be solved with and without CIR-derived pair weights.
- Position repeatability must be evaluated in a separate BSF66F static-tag capture using these two layouts.
- FULL CIR waveform captures are for calibration/inspection; the solver consumes compact/full-derived pair weights.
