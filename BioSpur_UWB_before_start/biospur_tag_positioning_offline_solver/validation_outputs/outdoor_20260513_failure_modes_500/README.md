# Outdoor 2026-05-13 T-Series Monte Carlo Failure Modes

This run evaluates T1/T2/T3 against four runtime failure modes using the outdoor 2026-05-13 V4-io layout.

## Inputs

- Dataset: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513`
- Static captures: `23`
- Roto captures: `17`
- Repeats per condition/method: `500`
- Layout: `FULL-COMPARE-1000/v4-io/layout.json`

## Failure Modes

- `MC1_keep_8`: `MC1_random_keep_k`, params `{"keep_k": 8}`
- `MC1_keep_7`: `MC1_random_keep_k`, params `{"keep_k": 7}`
- `MC1_keep_6`: `MC1_random_keep_k`, params `{"keep_k": 6}`
- `MC1_keep_5`: `MC1_random_keep_k`, params `{"keep_k": 5}`
- `MC1_keep_4`: `MC1_random_keep_k`, params `{"keep_k": 4}`
- `MC2_H_weak_p35`: `MC2_anchor_specific_dropout`, params `{"anchor_p": {"7": 0.35}, "default_p": 0.03}`
- `MC2_EBH_weak`: `MC2_anchor_specific_dropout`, params `{"anchor_p": {"1": 0.1, "4": 0.15, "7": 0.35}, "default_p": 0.03}`
- `MC2_upper_tail_weak`: `MC2_anchor_specific_dropout`, params `{"anchor_p": {"4": 0.12, "5": 0.12, "6": 0.12, "7": 0.3}, "default_p": 0.03}`
- `MC3_burst_H_0p5s`: `MC3_burst_dropout`, params `{"duration_s": 0.5, "target_anchors": [7]}`
- `MC3_burst_H_1p0s`: `MC3_burst_dropout`, params `{"duration_s": 1.0, "target_anchors": [7]}`
- `MC3_burst_random_EBH_1p0s`: `MC3_burst_dropout`, params `{"duration_s": 1.0, "target_anchors": [1, 4, 7]}`
- `MC4_nlos_random_anchor_persistent_p100`: `MC4_nlos_positive_bias`, params `{"bias_mm": 100.0, "frame_probability": 1.0, "target_anchors": [0, 1, 2, 3, 4, 5, 6, 7]}`
- `MC4_nlos_random_anchor_persistent_p200`: `MC4_nlos_positive_bias`, params `{"bias_mm": 200.0, "frame_probability": 1.0, "target_anchors": [0, 1, 2, 3, 4, 5, 6, 7]}`
- `MC4_nlos_random_anchor_persistent_p300`: `MC4_nlos_positive_bias`, params `{"bias_mm": 300.0, "frame_probability": 1.0, "target_anchors": [0, 1, 2, 3, 4, 5, 6, 7]}`

## Quick Comparison

Detailed interpretation is in `INTERPRETATION.md`.

| Dataset | Condition | T1 | T2 | T3 | Metric |
| --- | --- | ---: | ---: | ---: | --- |
| static | MC1_keep_8 | 50.3 | 51.8 | 48.6 | 3D repeatability median (mm) |
| roto | MC1_keep_8 | 22.0 | 22.1 | 22.6 | turn-center RMS median (mm) |
| static | MC1_keep_6 | 82.5 | 83.5 | 73.8 | 3D repeatability median (mm) |
| roto | MC1_keep_6 | 52.9 | 54.2 | 42.0 | turn-center RMS median (mm) |
| static | MC1_keep_4 | 168.1 | 176.1 | 129.3 | 3D repeatability median (mm) |
| roto | MC1_keep_4 | 139.1 | 142.6 | 82.7 | turn-center RMS median (mm) |
| static | MC2_EBH_weak | 61.0 | 61.2 | 56.0 | 3D repeatability median (mm) |
| roto | MC2_EBH_weak | 29.1 | 29.4 | 26.4 | turn-center RMS median (mm) |
| static | MC3_burst_random_EBH_1p0s | 50.7 | 50.9 | 49.3 | 3D repeatability median (mm) |
| roto | MC3_burst_random_EBH_1p0s | 19.7 | 19.8 | 20.3 | turn-center RMS median (mm) |
| static | MC4_nlos_random_anchor_persistent_p200 | 52.9 | 53.0 | 51.4 | 3D repeatability median (mm) |
| roto | MC4_nlos_random_anchor_persistent_p200 | 21.9 | 22.2 | 24.6 | turn-center RMS median (mm) |

## Files

- `mc_condition_repeat_summary.csv`: one row per dataset/condition/method/repeat.
- `mc_summary_by_condition.csv`: repeat-aggregated condition summary.
- `mc_static_capture_detail.csv`: per-static-capture detail rows.
- `mc_roto_track_detail.csv`: per-roto-capture/tag detail rows.
- `figures/mc_static_d3_by_condition.png`: static 3D repeatability overview.
- `figures/mc_static_z_by_condition.png`: static Z repeatability overview.
- `figures/mc_roto_center_by_condition.png`: Roto turn-center robustness overview.
- `figures/mc_roto_thickness_by_condition.png`: Roto circle-thickness robustness overview.
