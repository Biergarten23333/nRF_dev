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
