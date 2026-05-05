# Broadcast TDMA 10-Tag Baseline Freeze - 2026-05-04

This document freezes the first proven BioSpur broadcast TDMA pressure-test
baseline:

```text
10 Tags x 10 Hz/tag x 8 anchors = 800 TR rows/s
```

The baseline is focused on `TR` throughput. `TS` position quality is a separate
solver/layout/range-quality topic.

## Frozen Configuration

### Tag firmware

Marker:

```text
alt-bcast-b62-otaprep-silent-g1200-r1000
```

Direct-flash image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Important behavior:

- Broadcast 8-anchor ranging.
- `TR` / `TS` / `TF` output architecture.
- EKF disabled.
- Guard: `APP_ALT_SS_TWR_GUARD_US=1200`.
- Response spacing: `APP_ALT_SS_TWR_RESP_SPACING_US=1000`.
- Lightweight TDMA: `10 ms` slot period, `10` slots, `9 ms` active.
- Runtime APOS layout via NVS is supported.

### Anchor firmware

Anchors remain on the broadcast responder baseline used for b62 validation:

```text
alt-bcast-a13-nosleep-hotpath-g1200-r1000
```

Important behavior:

- No permanent responder `coop_sleep`.
- Hot-path optimized delayed response.
- Runtime responder force must be run before capture.

### Master_Tag behavior

Master_Tag must not release TDMA immediately after the last Tag appears.

The required startup gate is:

```text
1. Enter recv/tag mode.
2. Put TDMA on hold.
3. Silence resident Tag links with MODE AOTA.
4. Clear and preseed TDMA roster.
5. Start/maintain BLE connections.
6. Wait until all requested Tags are ready.
7. Keep all requested Tags ready for a stable-settle interval.
8. Release TDMA.
```

The capture script now enforces this with:

```text
--tag-link-stable-s 8
```

This was the key fix for clean all-Tag power-cycle startup.

## Validated Tag Set

The 10-Tag pressure-test roster:

```text
BSF66F
BS2DCE
BSDC91
BSE88E
BS6F3A
BS8251
BSF8E0
BS1396
BS7724
BS10CE
```

Inventory details live in:

```text
docs/broadcast_tag_inventory.md
```

## Success Evidence

### 10-Tag staged add validation

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_add_BS10CE_motion60_20260504_125335
```

Result:

```text
TR actual: 47992 / 48000
TR achievement: 99.98%
Equivalent rate: ~10.0 Hz/tag
Cleanup: success
```

Per-tag TR:

```text
BSF66F  4800
BS2DCE  4800
BSDC91  4792
BSE88E  4800
BS6F3A  4800
BS8251  4800
BSF8E0  4800
BS1396  4808
BS7724  4800
BS10CE  4792
```

### 10-Tag all-power-cycle validation with stable gate

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_after_powercycle_stablegate_motion60_20260504_130532
```

Startup evidence:

```text
ready=(9/10)
all 10/10 ready; settle 8.0s before TDMA release
ready=(10/10)
release TDMA
```

Result:

```text
TR actual: 48032 / 48000
TR achievement: 100.07%
Equivalent rate: ~10.0 Hz/tag
Cleanup: success, stop_notify_rows=0
```

Per-tag TR:

```text
BS10CE   4800  10.000 Hz
BS1396   4800  10.000 Hz
BS2DCE   4808  10.017 Hz
BS6F3A   4808  10.017 Hz
BS7724   4808  10.017 Hz
BS8251   4800  10.000 Hz
BSDC91   4808  10.017 Hz
BSE88E   4800  10.000 Hz
BSF66F   4800  10.000 Hz
BSF8E0   4800  10.000 Hz
```

### Why the first 10-Tag attempts failed

The early failures were not a hard BLE/TDMA throughput limit.

Root cause:

- TDMA was released too soon after full power-cycle startup.
- The script could proceed after a non-stable link state.
- Some Tags were still settling/reconnecting while TDMA started.
- Previous startup checks were polluted by `TS`/valid-solve behavior, even
  though the desired pressure-test metric was `TR`.

The stable-link gate fixed this startup problem.

## Mid-Run Dropout Test

Capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_poweroff_one_midrun_motion180_20260504_130930
```

Test behavior:

- Capture started with 10 Tags.
- During the run, non-core pressure-test Tags were manually powered off.
- The final active set was the traditional 3 Tags:
  - `BSF66F`
  - `BS2DCE`
  - `BSDC91`

Final 30 seconds:

```text
150-180s:
BSF66F  2400 TR = 10.0 Hz
BS2DCE  2400 TR = 10.0 Hz
BSDC91  2400 TR = 10.0 Hz
Total   7200 TR = 3 Tag full speed
```

Interpretation:

- The system did not crash.
- Master_Tag stayed alive.
- Cleanup succeeded.
- Remaining Tags eventually continued at full speed.
- However, the system currently does not automatically remove dead Tags from
  the active TDMA roster.

## Known Limitations

### Runtime dropout handling is not elastic yet

When a Tag drops mid-run, its slot is not automatically removed from the active
plan. This wastes airtime until the system is manually reconfigured or the run
ends.

Needed future behavior:

```text
If Tag has no TR/notify for N seconds:
  mark inactive
  remove from active TDMA roster
  release a new plan for still-live Tags
```

Expected interruption for a clean rebalance:

```text
~0.5-2 s
```

For BioSpur, this short gap should be bridged by IMU propagation.

### TR throughput and TS quality are separate

This freeze proves `TR` transport and TDMA scheduling capacity. It does not
claim all 10 Tags produce high-quality `TS` positions.

Observed examples:

- Some Tags can output full-rate `TR` while producing weak `TS`.
- This is a range quality / layout / antenna / placement issue, not a BLE
  throughput issue.

### Listener is auxiliary

The listener is useful for on-air evidence, but Master_Tag-side `TR` logs are
the primary throughput source.

## Recommended 10-Tag Capture Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/SS-TWR/alt-SS-TWR/broadcast

python3 scripts/run_recv_tdma_capture_with_listener.py \
  --listener-port /dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00 \
  --listener-extra-s 40 \
  --out-dir logs/tdma_10tag_motion60_$(date +%Y%m%d_%H%M%S) \
  -- \
  --port /dev/serial/by-id/usb-BioSpur_1_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --controller-reset-snr - \
  --duration 60 \
  --targets BSF66F,BS2DCE,BSDC91,BSE88E,BS6F3A,BS8251,BSF8E0,BS1396,BS7724,BS10CE \
  --profiles BSF66F:motion,BS2DCE:motion,BSDC91:motion,BSE88E:motion,BS6F3A:motion,BS8251:motion,BSF8E0:motion,BS1396:motion,BS7724:motion,BS10CE:motion \
  --motion-hz 10 \
  --skip-cm-probe \
  --allow-zero-positions \
  --tag-link-stable-s 8 \
  --anchor-preflight-port /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00 \
  --anchor-preflight-retries 2 \
  --anchor-preflight-launch-retries 2
```

## Disposition

This configuration is frozen as the first BioSpur broadcast TDMA 10-Tag
baseline.

Do not change the baseline firmware markers without creating a new versioned
record.

Next development directions:

1. Add runtime Tag health monitoring and TDMA roster rebalancing.
2. Decide the exact IMU bridge strategy for 0.5-2 s UWB gaps.
3. Continue TS/offline-solver work using `TR` as the primary measurement feed.
4. Re-test 10-Tag long-run battery behavior after dropout rebalance exists.
