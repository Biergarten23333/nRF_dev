## Erlangen Baseline Freeze - 2026-05-19

This is the current baseline for the planned Erlangen / OptiTrack field test.

### Frozen Decision

Use the **tail900 start5** broadcast responder timing as the Erlangen baseline.

The first five anchor response slots remain unchanged. Only the tail anchors
F/G/H are compressed from the old 1000 us spacing to 900 us spacing:

| Anchor | Rank | Response slot after poll |
|---|---:|---:|
| A | 0 | 1200 us |
| B | 1 | 2200 us |
| C | 2 | 3200 us |
| D | 3 | 4200 us |
| E | 4 | 5200 us |
| F | 5 | 6100 us |
| G | 6 | 7000 us |
| H | 7 | 7900 us |

This keeps the conservative 1200 us initial guard and avoids compressing A-E,
while improving the late H response window compared with the old
`guard=1200 us, rank_spacing=1000 us` schedule.

### Firmware / Build Marker

- Experimental anchor marker: `us-hc-exp4-tail900-start5`
- Tail compression parameters:
  - `APP_ALT_SS_TWR_TAIL_COMPRESS_ENABLE=1`
  - `APP_ALT_SS_TWR_TAIL_START_RANK=5`
  - `APP_ALT_SS_TWR_TAIL_RESP_SPACING_US=900`
- Guard remains: `APP_ALT_SS_TWR_GUARD_US=1200`
- Normal rank spacing remains: `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
- Ultrasound support is present in this image, but must be explicitly opened
  only during the short ultrasound capture window and closed before Tag/Wand
  capture. Normal responder operation after ultrasound close has been verified.

Implementation files for this freeze:

- `src/ss_twr_resp.c`
- `apps/anchor/CMakeLists.txt`
- `scripts/build_experimental_ultrasound_anchor_carrier_b120.sh`

### Hardware Roster

Current A-H anchor roster for this freeze:

| Anchor | BS code | SNR | UUID |
|---|---|---:|---|
| A | BS1FFC | 760184781 | F3BB7A04104F9CB8561DDDACB9E53714 |
| B | BS592A | 760185876 | B9179575C776C98F1CB132DD6EDC6223 |
| C | BS5380 | 760185878 | CEE5A7EFCB35F8A56B430047629F5309 |
| D | BS20AC | 760184974 | B2B5FA625534A8C617135DCAFC9E036A |
| E | BS4B52 | 760185904 | A892AF05DD59CF0D0D3408AD74F364A1 |
| F | BS928B | 760186124 | 840C68591E90019821AACFF1B73AAA34 |
| G | BSEC88 | 760185889 | B3087BC3D87CCCD316AEDC6B71D6677F |
| H | BS506D | 760184500 | B1E487C2B1FD740D1442206A1857DFA1 |

Anchor H has been replaced. The old H was `BSB77F`, SNR `760184753`,
UUID `CF12E703AC1A118F6AB440AB05B0BA23`, and should not be used unless
explicitly rolling back.

### Validation Evidence

Single-tag BSF66F, center of the current small volume:

- Session:
  `autopos_pipeline/offline_test_motice/test_18052026/bsf66f_center_60s_tail900_start5_rerun`
- Duration: 60 s
- `sweeps_total = 601`
- `8/8 = 600/601 = 99.83%`
- `>=7 = 601/601 = 100.00%`
- `tr_valid_all = 4807/4808`
- Cleanup after capture succeeded with `cmd_all MODE AOTA`.

Single-tag BSF66F, 180 s tail900 start5 test:

- Session:
  `autopos_pipeline/offline_test_motice/test_18052026/bsf66f_180s_us_hc_exp4_tail900_start5`
- `sweeps_total = 1801`
- `8/8 = 1680/1801 = 93.28%`
- `>=7 = 1800/1801 = 99.94%`
- Per-anchor validity:
  - A/r0: 99.72%
  - B/r1: 99.94%
  - C/r2: 99.83%
  - D/r3: 99.83%
  - E/r4: 99.94%
  - F/r5: 98.89%
  - G/r6: 96.22%
  - H/r7: 98.83%

Comparison against tail800 start5:

- tail800 start5 improved H but compressed F/G too aggressively.
- tail900 start5 preserves most of the H improvement while recovering F/G
  stability, so it is the best tested compromise.

### Small-Volume Caveat

The local room used on 2026-05-18/19 is much smaller than the outdoor
experiment volume. The latest SW100 solver results give approximately:

- X span: 1.83 m
- Y span: 2.49 m
- Z span: 1.60 m
- Layout edge RMS: approximately 71-74 mm across three SW100 cycles

This small volume is acceptable for firmware smoke tests, timing validation,
and responder availability checks. It is **not** a reliable environment for
judging RotoArm or Calibration Wand quality, because Tag orientation, body
shadowing, cable/power placement, and mutual occlusion dominate the result.

Examples from the small-volume tests:

- BSF66F at the center gives 99.83% 8/8, proving that the timing and the Tag
  can work very well.
- Roto/Wand multi-tag captures show large tag-to-tag differences under the same
  anchor timing, which points to physical RF visibility rather than broadcast
  slot timing.

Therefore, do not change the tail900 start5 timing based only on poor Roto/Wand
results from this small room. Re-evaluate Roto/Wand in a larger volume,
preferably close to the previous 3 m x 4 m scale or in the Erlangen OptiTrack
setup.

### Erlangen Test Recommendation

For the next Erlangen / outdoor trial:

1. Use `tail900 start5` as the baseline.
2. Start with SW100 and solve the anchor layout.
3. Run one center static Tag check first.
4. Then run RotoArm / Wand only after the static center Tag has high 8/8
   availability.
5. Keep ultrasound disabled during all normal Tag, RotoArm, and Wand captures.

This section supersedes the old `g1200/r1000` timing baseline for new
Erlangen tests, while the older section below remains as historical context.

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
APP_TAG_FW_MARKER=alt-bcast-b55-no当前测试看起来没有破坏正常 UWB responder 行为
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

Broadcast baseline remains frozen at b55 Tag + a13 Anchor. No source, build, OTA, or hardware deployment changes were made after this freeze.

AutoPos inter-anchor sweep note:

- Attempted run: `logs/autopos_inter_anchor_sweep_one_round_nobootstrap_20260502_122627`
- `SW-A` and `SW-B` completed, but pair values were `0,0`.
- `SW-C` stalled in `state=staged staged=C last_success=B`; the run was stopped.
- A-H were restored to responder runtime afterward: `ready=8/8`.

Broadcast ranging is still the frozen working baseline. AutoPos/layout calibration should be treated as the next separate task.
