# CIR Weighted Layout Comparison

- Sweep dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/sweep`
- CIR pair weights: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/cir_pair_weights.json`
- Pairs CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/layout_compare/pairs_all.csv`

| solve | RMS edges mm | RMS inlier mm | outliers | CIR pairs | layout |
|---|---:|---:|---:|---:|---|
| baseline | 112.994 | 112.994 | 0 | 0 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/layout_compare/baseline_v3_box/anchor_layout_v3_box.json` |
| cir_weighted | 114.611 | 114.611 | 0 | 28 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/mainline_autopos_with_cir_compact_1000_20260608_overnight/layout_compare/cir_weighted_v3_box/anchor_layout_v3_box.json` |

## Notes

- This report only proves the same SW matrix can be solved with and without CIR-derived pair weights.
- Position repeatability must be evaluated in a separate BSF66F static-tag capture using these two layouts.
- FULL CIR waveform captures are for calibration/inspection; the solver consumes compact/full-derived pair weights.
