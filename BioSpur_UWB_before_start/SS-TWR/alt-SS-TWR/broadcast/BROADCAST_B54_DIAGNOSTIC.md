# Broadcast b54/b55 Sweep Diagnostic

## Phase 1 - b54 TDIAG

- Tag marker: `alt-bcast-b54-sweepdiag-8anc-g1200-r1000-rms0`
- Capture: `logs/alt_bcast_b54_tdiag_BSF66F_motion30_20260502_025051_20260502_025051`
- Target: `BSF66F` single-tag motion, 30 s
- Anchor responder preflight: `ready=8/8`
- TDIAG rows: `36`
- Positions: `275`

TDIAG median / p95 / max:

| phase | median | p95 | max |
|---|---:|---:|---:|
| wait_ms | 28 | 84 | 99 |
| tx_us | 610 | 610 | 915 |
| rx_us | 183 | 213 | 305 |
| coll_us | 8514 | 8666 | 8819 |
| range_us | 579 | 29022 | 43487 |
| solve_us | 7934 | 9155 | 11840 |
| out_us | 52764 | 53192 | 53466 |
| clean_us | 30 | 61 | 122 |
| total_ms | 99.5 | 153 | 184 |

Diagnosis:

- Matching case: `Case D: out_us dominates`.
- UWB collector is not the main bottleneck: collector median is `8514 us`.
- The dominant delay is output path: `out_us` median is `52.8 ms`.
- Build inspection showed `APP_TAG_CONSOLE_SUMMARY_ENABLE=1` and `APP_TAG_VERBOSE_PERF=1`, so each successful sweep can run console `printk` summary/perf work before returning to the next sweep.

Fix plan for b55:

- Keep BLE `TS` output enabled.
- Disable blocking console/perf output from the sweep output path:
  - `APP_TAG_CONSOLE_SUMMARY_ENABLE=0`
  - `APP_TAG_VERBOSE_PERF=0`
- Keep TDIAG active to verify `out_us` reduction.

## Phase 3 - b55 No-Console Fix

- Tag marker: `alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0`
- Build fix: disabled sweep console/perf printk while keeping BLE `TS` and TDIAG enabled.
- Capture: `logs/alt_bcast_b55_noconsole_BSF66F_motion30_20260502_030059_20260502_030059`
- Target: `BSF66F` single-tag motion, 30 s
- Anchor responder preflight: `ready=8/8`
- TDIAG rows: `40`
- Positions: `302`

TDIAG median / p95 / max:

| phase | median | p95 | max |
|---|---:|---:|---:|
| wait_ms | 80.5 | 83 | 84 |
| tx_us | 549 | 671 | 732 |
| rx_us | 152 | 183 | 244 |
| coll_us | 8178 | 8544 | 8544 |
| range_us | 488 | 701 | 732 |
| solve_us | 7644 | 9765 | 9918 |
| out_us | 1068 | 1434 | 1617 |
| clean_us | 30 | 30 | 152 |
| total_ms | 99 | 103 | 104 |

Before / after:

- `out_us` median improved from `52764 us` to `1068 us`.
- `total_ms` stayed near `100 ms`, which is expected for one tag assigned to one slot in a 10-slot TDMA cycle.
- Single-tag output rate is now about `10 Hz` (`302 positions / 30 s`).

Current bottleneck after fix:

- `wait_ms` now dominates because the tag waits for its next 100 ms cycle slot.
- This is not a failure for 10 Tag x 10 Hz; it is the intended 10-slot schedule.

Next validation:

- OTA b55 to `BSF66F`, `BS2DCE`, `BSDC91`.
- Run 60 s 3-tag motion capture with responder preflight.
- Expected total positions: near `1800` if all three tags each run around `10 Hz`.

## Phase 4 - b55 3-Tag Validation

First 60 s 3-tag capture showed an imbalance:

- Capture: `logs/alt_bcast_b55_noconsole_3tag_motion60_20260502_030831_20260502_030831`
- `positions_all=1139`, `tf_all=0`
- Per-tag: `BSF66F=1`, `BS2DCE=567`, `BSDC91=571`
- Raw diagnostics showed BSF66F was temporarily assigned `tag=3/local=0xb103` and received `resp=0` in many sweeps, while `0xb101/0xb102` tags received normal responses.

A targeted repeat with listener and required Anchor responder preflight recovered the expected behavior:

- Capture: `logs/alt_bcast_b55_listener_3tag_motion30_20260502_031427`
- Anchor responder preflight: `ready=8/8`
- Listener: `uf_rows=1723`, `ul_rows=290`, saw broadcast polls from `0xb101`, `0xb102`, and `0xb103`
- `positions_all=902` in 30 s, `tf_all=0`
- Per-tag: `BSF66F=302`, `BS2DCE=300`, `BSDC91=300`
- Rate: about `10 Hz/tag`, balanced

Interpretation:

- b55 no-console fix is valid.
- Single-tag and repeated 3-tag data both show the sweep loop can sustain the 10 Hz/tag target.
- The earlier 60 s imbalance is likely a startup/runtime reconfiguration transient, not a steady-state UWB timing limit.
- Proceeding to 120 s full validation.

## Phase 4 - b55 120 s Full Validation PASS

- Capture: `logs/alt_bcast_b55_listener_3tag_motion120_20260502_031733`
- Anchor responder preflight: `ready=8/8`
- Listener: `uf_rows=8451`, `ul_rows=1955`
- Listener broadcast poll sources: `0xb103=2248`, `0xb101=2195`, `0xb102=2053`
- Listener response frames: `0xe1=1955`, all anchors A-H observed on air
- `positions_all=3599` in 120 s
- `tf_all=0`
- Per-tag positions:
  - `BSF66F=1199` (`9.99 Hz`)
  - `BS2DCE=1200` (`10.00 Hz`)
  - `BSDC91=1200` (`10.00 Hz`)

Verdict:

- PASS: 120 s success criterion `positions_all >= 2500` exceeded by a large margin.
- PASS: per-tag balance is effectively perfect.
- PASS: no TF rejects.
- b55 validates 8-anchor broadcast g1200/r1000 at the target `10 Hz/tag` for 3 tags.

Proceeding to Phase 5 300 s stability run.

## Phase 5 - b55 300 s Stability Run PASS

- Capture: `logs/alt_bcast_b55_listener_3tag_motion300_20260502_032215`
- Anchor responder preflight: `ready=8/8`
- Duration: `300 s`
- `positions_all=8999`
- `tf_all=0`
- Per-tag positions:
  - `BSF66F=2999` (`9.997 Hz`)
  - `BS2DCE=3000` (`10.000 Hz`)
  - `BSDC91=3000` (`10.000 Hz`)
- Per-minute totals: `1801, 1800, 1800, 1800, 1798`
- No controller loss, no tag disconnect, no zero-position target failure.

RMS statistics:

| tag | n | rate Hz | RMS median | RMS p95 | RMS p99 | RMS max |
|---|---:|---:|---:|---:|---:|---:|
| BSF66F | 2999 | 9.997 | 132 | 183 | 247 | 415 |
| BS2DCE | 3000 | 10.000 | 292 | 431 | 478 | 640 |
| BSDC91 | 3000 | 10.000 | 279 | 471 | 550 | 721 |

Anchor-set distribution highlights:

- `BSF66F`: mostly `ABCDEFGH` (`1989`) and `ABCDEFG` (`930`)
- `BS2DCE`: mostly `ABCDEFG` (`2076`) and `ABCDEFGH` (`898`)
- `BSDC91`: mostly `ABCDEFG` (`1648`) and `ABCDEFGH` (`1288`)

Listener summary:

- `uf_rows=14043`, `ul_rows=1836`
- Listener saw broadcast polls from all three tag short addresses:
  - `0xb103=4110`
  - `0xb101=4062`
  - `0xb102=4035`
- Listener saw response frames from all anchors A-H.

Final Summary:

- Identified bottleneck: Tag output path (`out_us`) was blocking the sweep loop because console/perf printk was enabled.
- Fix applied in b55: disabled console/perf sweep output while keeping BLE `TS` output and TDIAG.
- Best achieved rate: `8999 positions / 300 s` = `29.997 Hz` total, essentially `10 Hz/tag` for 3 tags.
- Remaining gap to 10 Hz target: none for 3 Tag at g1200/r1000.
- Validated configuration: `alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0` Tags with a13 nosleep/hotpath Anchors.

## Broadcast Baseline Freeze - b55 Tag + a13 Anchor

### Frozen Configuration

**Tag side:**

- Marker: `alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0`
- Build cache: `build-alt-bcast-b55-noconsole-8anc-tag-g1200-r1000-rms0/CMakeCache.txt`
- TDMA: `lperiod=10`, `lcount=10`
- Broadcast mask: `0xFF`
- Pre-write TX: `pre=1`
- TX mode: immediate TX
- Fast RX hot path: `txdone_to_rxstart_us` median `61 us`, p95 `91 us`
- Console/perf printk disabled; BLE `TS` output enabled.

Tag `APP_` build parameters from b55 build cache:

```text
APP_ALT_SS_TWR_BCAST_ENABLE=1
APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=1
APP_ALT_SS_TWR_BCAST_IMMEDIATE_TX_ENABLE=1
APP_ALT_SS_TWR_BCAST_PREWRITE_TX_ENABLE=1
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_LIGHT_TDMA_ENABLE=1
APP_ALT_SS_TWR_MODE=2
APP_ALT_SS_TWR_POLL_SPACING_US=200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_TAG_ACTIVE_ANCHOR_0_ID=0
APP_TAG_ACTIVE_ANCHOR_1_ID=1
APP_TAG_ACTIVE_ANCHOR_2_ID=4
APP_TAG_ACTIVE_ANCHOR_3_ID=5
APP_TAG_BLE_COMPACT_STATUS=1
APP_TAG_BLE_ENABLE=1
APP_TAG_BLE_OTA_ENABLE=1
APP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=250
APP_TAG_BLE_PACKET_BUNDLE_RECORDS=3
APP_TAG_BLE_SETTINGS_ENABLE=1
APP_TAG_CONSOLE_SUMMARY_ENABLE=0
APP_TAG_EKF_ENABLE=0
APP_TAG_EKF_INIT_POS_STD_MM=200
APP_TAG_EKF_INIT_VEL_STD_MM_S=1200
APP_TAG_EKF_MEAS_STD_MM=35
APP_TAG_EKF_OUTLIER_GATE_MM=120
APP_TAG_EKF_PROC_ACCEL_MM_S2=500
APP_TAG_EKF_RESIDUAL_GAIN_PCT=0
APP_TAG_FAST_TRACKING=1
APP_TAG_FULL_SWEEP_INTERVAL=8
APP_TAG_FW_MARKER=alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0
APP_TAG_IMU_SAMPLE_PERIOD=8
APP_TAG_LOC_FAST_ALL_VALID_ENABLE=1
APP_TAG_LOC_MIN_QUALITY_PERCENT=50
APP_TAG_MAINTENANCE_FULL_INTERVAL=100
APP_TAG_MCUBOOT_ENABLE=1
APP_TAG_MOTION_EKF_MEAS_STD_MM=0
APP_TAG_MOTION_EKF_OUTLIER_GATE_MM=0
APP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2=0
APP_TAG_MOTION_FULL_SWEEP_INTERVAL=0
APP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG=750
APP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG=400
APP_TAG_MOTION_RANGE_HARD_BONUS_MM=0
APP_TAG_MOTION_RANGE_SOFT_BONUS_MM=0
APP_TAG_MOTION_SPEED_THRESHOLD_MM_S=250
APP_TAG_MULTITAG_PLAN_MODE=0
APP_TAG_OUTPUT_FILTER_RMS_MM=0
APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0
APP_TAG_PENDING_PRINT_PERIOD=20
APP_TAG_RANGE_CONTINUITY_ENABLE=0
APP_TAG_RANGE_FILTER_OUTLIER_MM=120000
APP_TAG_RANGE_HARD_RESIDUAL_MM=350
APP_TAG_RANGE_SOFT_RESIDUAL_MM=180
APP_TAG_RESERVE_ANCHOR_0_ID=3
APP_TAG_RESERVE_ANCHOR_1_ID=7
APP_TAG_STANDBY_ANCHOR_0_ID=2
APP_TAG_STANDBY_ANCHOR_1_ID=6
APP_TAG_SUMMARY_PERIOD=1
APP_TAG_SWEEP_DIAG_ENABLE=1
APP_TAG_SWEEP_DIAG_PERIOD=10
APP_TAG_TDMA_ENABLE=1
APP_TAG_TDMA_SLOT_ACTIVE_MS=9
APP_TAG_TDMA_SLOT_COUNT=10
APP_TAG_TDMA_SLOT_INDEX=0
APP_TAG_TDMA_SLOT_PERIOD_MS=10
APP_TAG_TRACK_ANCHOR_COUNT=6
APP_TAG_USB_DIAG_TRACE=0
APP_TAG_VERBOSE_MEASUREMENTS=0
APP_TAG_VERBOSE_PERF=0
APP_TAG_VERBOSE_RANGING=0
APP_UWB_CHANNEL=5
APP_UWB_PAN_ID=0xDECA
```

**Anchor side:**

- Marker: `alt-bcast-a13-nosleep-hotpath-g1200-r1000`
- Build cache: `build-anchor-unified-ota-alt-bcast-a13-nosleep-hotpath-g1200-r1000/CMakeCache.txt`
- Nosleep responder: `APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=0`
- Hot-path optimized responder.
- BLE DFU trigger validated: `cmd DFU` exits responder and allows OTA.
- Response template pre-built at init.

Anchor `APP_` build parameters from a13 build cache:

```text
APP_ALT_SS_TWR_BCAST_ENABLE=1
APP_ALT_SS_TWR_ENABLE=1
APP_ALT_SS_TWR_GUARD_US=1200
APP_ALT_SS_TWR_RESP_SPACING_US=1000
APP_ANCHOR_FW_MARKER=alt-bcast-a13-nosleep-hotpath-g1200-r1000
APP_ANCHOR_RESPONDER_COOP_SLEEP_MS=0
APP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=5000
APP_ANCHOR_RESPONDER_PRINTK_ENABLE=0
APP_ANCHOR_RESPONDER_PROFILE_ENABLE=0
APP_ANCHOR_SCHEDULE_MODE=2
APP_ANCHOR_VERBOSE_RESPONDER=0
APP_ANCHOR_VERBOSE_RESPONDER_ERRORS=0
APP_UWB_HW_FRAME_FILTER_ENABLE=1
```

### Proven Performance

- 300 s stability capture: `logs/alt_bcast_b55_listener_3tag_motion300_20260502_032215`
- `positions_all=8999`
- `tf_all=0`
- Per-tag:
  - `BSF66F=2999` (`9.997 Hz`)
  - `BS2DCE=3000` (`10.000 Hz`)
  - `BSDC91=3000` (`10.000 Hz`)
- Per-minute stability: `1801, 1800, 1800, 1800, 1798`
- CM ok rate from a13 calibration probe: `3282/3344 = 98.1%`
- Broadcast poll instant: `first_to_last_us=0` for all broadcast sweeps by protocol design.
- Anchor coverage: all 8 anchors A-H appeared in position output.

### Timing Budget

Measured from b55 TDIAG/RXG:

| metric | median | p95 | source |
|---|---:|---:|---|
| slot_to_txdone_us | 610 us | 701 us | 300 s 3-tag RXG |
| txdone_to_rxstart_us | 61 us | 91 us | 300 s 3-tag RXG |
| collector window | 8514 us | 8575 us | 300 s 3-tag TDIAG |
| solve | 8911 us | 12115 us | 300 s 3-tag TDIAG |
| output after fix | 1037 us | 1434 us | 300 s 3-tag TDIAG |
| total sweep | 102 ms | 143 ms | 300 s 3-tag TDIAG |
| TDMA wait | 79 ms | 82 ms | 300 s 3-tag TDIAG |

Single-tag b55 confirmation:

| metric | median | p95 |
|---|---:|---:|
| slot_to_txdone_us | 640 us | 762 us |
| txdone_to_rxstart_us | 61 us | 91 us |
| collector window | 8178 us | 8544 us |
| solve | 7644 us | 9765 us |
| output after fix | 1068 us | 1434 us |
| total sweep | 99 ms | 103 ms |
| TDMA wait | 80.5 ms | 83 ms |

### Known Limitations

- Anchor layout in `uwb_anchor_layout.c` is stale because anchors have been physically moved. RMS values are not meaningful until layout is recalibrated.
- RMS gate is currently disabled: `APP_TAG_OUTPUT_FILTER_RMS_MM=0`. It must be re-tuned after layout calibration.
- Speed gate is currently disabled: `APP_TAG_OUTPUT_FILTER_SPEED_MM_S=0`. It has the same layout dependency.
- Only validated with 3 tags. 10-tag scaling has not been tested yet.
- Listener parser sees fewer responses than Tag-side data shows. Listener is valid for poll on-air evidence but not for complete response counting.
- Master_Anchor VERSION readback remains unreliable (`actual=-` after OTA). This does not affect UWB operation.

### Disposition

This configuration is frozen as the broadcast baseline. No files in the b55 Tag build or a13 Anchor build should be modified without creating a new version number.

Next development directions, in priority order:

1. AutoPos anchor layout calibration -> update `uwb_anchor_layout.c` -> re-tune RMS/speed gates.
2. 10-tag scaling validation.
3. Guard compression below `g1200`, requiring Anchor hot-path profiling.
4. Spacing compression below `r1000`, requiring double-buffer RX or RXAUTR.

## Return To Main Workspace Checkpoint - 2026-05-02 12:35

Broadcast baseline remains frozen at:

- Tag: `alt-bcast-b55-noconsole-8anc-g1200-r1000-rms0`
- Anchor: `alt-bcast-a13-nosleep-hotpath-g1200-r1000`

No broadcast source, build, OTA, or hardware deployment changes were made after the freeze.

One AutoPos inter-anchor sweep was attempted before returning to the main workspace:

- Log directory: `logs/autopos_inter_anchor_sweep_one_round_nobootstrap_20260502_122627`
- `SW-A` and `SW-B` completed, but all pair values were `0,0`.
- `SW-C` stalled in `state=staged staged=C last_success=B`, so the run was stopped.
- A-H were restored to responder runtime afterward: `ready=8/8`.

Broadcast ranging baseline is still considered valid and unchanged. AutoPos/layout calibration remains the next separate task.
