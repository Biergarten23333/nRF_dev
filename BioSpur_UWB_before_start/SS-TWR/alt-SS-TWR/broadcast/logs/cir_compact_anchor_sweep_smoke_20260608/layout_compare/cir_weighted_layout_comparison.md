# CIR Weighted Layout Comparison

- Sweep dir: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/cir_compact_anchor_sweep_smoke_20260608/sweep`
- CIR pair weights: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/cir_compact_anchor_sweep_smoke_20260608/cir_pair_weights.json`
- Pairs CSV: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/cir_compact_anchor_sweep_smoke_20260608/layout_compare/pairs_all.csv`

| solve | RMS edges mm | RMS inlier mm | outliers | CIR pairs | layout |
|---|---:|---:|---:|---:|---|
| baseline | 109.828 | 109.828 | 0 | 0 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/cir_compact_anchor_sweep_smoke_20260608/layout_compare/baseline_v3_box/anchor_layout_v3_box.json` |
| cir_weighted | 109.455 | 109.455 | 0 | 28 | `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast/logs/cir_compact_anchor_sweep_smoke_20260608/layout_compare/cir_weighted_v3_box/anchor_layout_v3_box.json` |

## Notes

- This report only proves the same SW matrix can be solved with and without CIR-derived pair weights.
- Position repeatability must be evaluated in a separate BSF66F static-tag capture using these two layouts.
- FULL CIR waveform captures are for calibration/inspection; the solver consumes compact/full-derived pair weights.
