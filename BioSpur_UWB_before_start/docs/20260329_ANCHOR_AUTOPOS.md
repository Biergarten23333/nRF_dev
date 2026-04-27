# 20260329 Anchor Autopositioning Baseline

Date: 2026-03-29  
Scope: Anchor A-H matrix/autopositioning preparation + Ref115 BLE calibration baseline.

## Executive Summary

1. Anchor-side matrix preparation completed with full pair coverage:
   - `unique_pairs=28`, `missing_pairs=0`.
2. Anchor role families `master / matrix / responder` have clear, verified baseline build families.
3. Ref115 BLE calibration has two validated layers:
   - quick-confirm layer (`120` threshold) for mode/transport confirmation.
   - strict baseline layer (`500` threshold) with PASS (`cm_records=504`) as the real production baseline.
4. For Phase 2, use the strict Ref115 build pair (`iter3`) as the baseline.
5. Stage3 / Tag127 is intentionally frozen and out of scope in this document.

## 1) Anchor Role Families and Verified Baselines

### `master` family (matrix initiator)
Verified baseline family:
- `build-anchor-<A..H>-master-full`

Examples used in this cycle:
- [build-anchor-A-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-A-master-full)
- [build-anchor-B-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-B-master-full)
- [build-anchor-C-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-C-master-full)
- [build-anchor-D-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-D-master-full)
- [build-anchor-E-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-E-master-full)
- [build-anchor-F-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-F-master-full)
- [build-anchor-G-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-G-master-full)
- [build-anchor-H-master-full](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-H-master-full)

### `matrix` family (non-initiator matrix responders)
Verified baseline family:
- `build-anchor-<A..H>-matrix`

Examples used in this cycle:
- [build-anchor-A-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-A-matrix)
- [build-anchor-B-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-B-matrix)
- [build-anchor-C-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-C-matrix)
- [build-anchor-D-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-D-matrix)
- [build-anchor-E-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-E-matrix)
- [build-anchor-F-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-F-matrix)
- [build-anchor-G-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-G-matrix)
- [build-anchor-H-matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-H-matrix)

### `responder` family (runtime restore baseline)
Verified baseline family:
- `build-anchor-<A..H>-tag`

Examples used in this cycle:
- [build-anchor-A-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-A-tag)
- [build-anchor-B-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-B-tag)
- [build-anchor-C-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-C-tag)
- [build-anchor-D-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-D-tag)
- [build-anchor-E-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-E-tag)
- [build-anchor-F-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-F-tag)
- [build-anchor-G-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-G-tag)
- [build-anchor-H-tag](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-anchor-H-tag)

## 2) Matrix/Autopositioning Preparation Result

Session:
- [/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/full_autopos_system_test_20260329_230536/matrix](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/full_autopos_system_test_20260329_230536/matrix)

Summary:
- [pairs_summary.txt](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/full_autopos_system_test_20260329_230536/matrix/pairs_summary.txt)
- `total_rows=171`
- `unique_pairs=28`
- `missing_pairs=0`

Produced artifacts:
- [pairs_all.csv](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/full_autopos_system_test_20260329_230536/matrix/pairs_all.csv)
- `pairs_master_A.csv ... pairs_master_H.csv`
- [inter_anchor_matrix_ah_fresh_fulltest_20260329.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/full_autopos_system_test_20260329_230536/matrix/inter_anchor_matrix_ah_fresh_fulltest_20260329.json)

## 3) Ref115 BLE Calibration Baseline Split

### 3.1 Quick-confirm build pair (`120` threshold)
Purpose:
- confirm BLE CM path active, full anchor IDs visible, mode is CM stream.

Build pair:
- Tag: [build-tag-ota-ref115-cal-run-20260329_231903](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-ref115-cal-run-20260329_231903)
- Master: [build-master-ota-ref115-cal-run-20260329_231903](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-ota-ref115-cal-run-20260329_231903)

Evidence:
- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/ref115_ble_cal_run_20260329_231903/summary.json)
- `cm_records=120`, `cm_threshold_result.met=true` (quick-confirm threshold profile)

### 3.2 Strict baseline build pair (`500` threshold, PASS=504)
Purpose:
- production-grade Ref115 BLE calibration baseline.

Build pair:
- Tag: [build-tag-ota-ref115-cal-iter3-20260329](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-tag-ota-ref115-cal-iter3-20260329)
- Master: [build-master-ota-ref115-cal-iter3-20260329](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-ota-ref115-cal-iter3-20260329)

Strict-pass evidence:
- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/master_ble_sessions/blecm_iter3_stage1_ref115_cal_20260329_1912/summary.json)
- PASS facts:
  - `cm_records=504`
  - required anchors `0..7` satisfied
  - strict threshold met

## 4) Recommended Baseline for Phase 2

Use these as the Phase 2 baseline:
1. Anchor roles:
   - initiator: `build-anchor-<X>-master-full`
   - matrix responders: `build-anchor-<X>-matrix`
   - restore runtime responders: `build-anchor-<X>-tag`
2. Ref115 BLE calibration:
   - **strict baseline pair only**:
     - `build-tag-ota-ref115-cal-iter3-20260329`
     - `build-master-ota-ref115-cal-iter3-20260329`

Explicitly:
- `run-20260329_231903` build pair is quick-confirm only.
- `iter3-20260329` build pair is the real strict baseline.

## 5) Scope Freeze Note

Stage3 / Tag127 status in this document:
- out of scope / frozen.
- not evaluated here as baseline input for Phase 2.
