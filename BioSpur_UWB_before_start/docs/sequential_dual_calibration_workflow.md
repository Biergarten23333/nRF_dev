# Sequential Dual Calibration Workflow (Ref115 -> Tag127)

## Purpose
Run two calibration tags **sequentially** in UWB calibration:
- `Ref115` (`760186115`) = static calibration tag
- `Tag127` (`760186127`) = rotation calibration tag

Hard rule: **never run 115 and 127 as active UWB calibration initiators at the same time**.

---

## Current Support Audit (2026-03-27)

### Already supported
1. **Ref115 calibration OTA profile**
   - Script: `scripts/build_ref115_calibration_ota_profile.sh`
   - Marker: `ref115-calibration-ota`
   - Behavior: calibration mode, TDMA disabled, full 8-anchor capture-oriented ranging.

2. **Ref115 host-side calibration capture/solve pipeline**
   - Script: `scripts/recalibrate_anchor_layout_with_ref115.py`
   - Uses `ranges.csv`, session sufficiency gate, acceptance-gated promotion path.

3. **Tag127 BLE/OTA path**
   - Existing motion wrapper: `scripts/build_tag127_ble_motion.sh`
   - Existing generic motion OTA builder: `scripts/build_motion_tag_ota_profile.sh`

### Gaps fixed in this round
1. **Ref115 deactivation from UWB participation (between stages)**
   - Added `APP_TAG_UWB_ENABLE` build switch to tag runtime (`apps/tag`).
   - Added OTA profile builder:
     - `scripts/build_ref115_idle_ota_profile.sh`
   - Effect: Ref115 can stay BLE/OTA-online while UWB scheduler is disabled.

2. **Tag127 dedicated rotation-calibration OTA profile**
   - Added:
     - `scripts/build_tag127_rotation_calibration_ota_profile.sh`
   - Effect: Tag127 can run calibration-style (not motion) capture in a separate stage.

---

## Required Stage Order

## Stage 0 — Preconditions
1. Inter-anchor matrix already done.
2. Anchors restored to responder baseline (`allow_tag_polls=1`).

## Stage 1 — Ref115 static calibration
1. Build OTA package:
```bash
./scripts/build_ref115_calibration_ota_profile.sh \
  build-tag-ota-ref115-calibration \
  build-master-ota-ref115-calibration
```
2. Flash master OTA image (non-interactive SN pin):
```bash
./scripts/flash_master_noninteractive.sh \
  683234364 build-master-ota-ref115-calibration/zephyr/zephyr.hex
```
3. Observe OTA completion and Ref115 reboot marker (`ref115-calibration-ota`).
4. Capture Ref115 calibration session (separate session dir).

## Stage 2 — Deactivate Ref115 from UWB
1. Build Ref115 idle profile:
```bash
./scripts/build_ref115_idle_ota_profile.sh \
  build-tag-ota-ref115-idle \
  build-master-ota-ref115-idle
```
2. OTA-deliver idle profile via master.
3. Verify Ref115 log contains:
   - `Tag UWB disabled by build profile (BLE/OTA only)`
   - `Tag app handoff: UWB scheduler skipped (UWB disabled)`

This is the current practical "deactivate from UWB" implementation.

### Stage2 isolation fix + verification (2026-03-27)

Root cause of the earlier Stage2 FAIL was a **verification-path bug**:
- Ref115 bootcheck capture was run with `snr=760186115` but on the master port (`/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00`).
- So the log showing continuous `Range anchor=...` was not Ref115 evidence.

Fixes applied:
1. `scripts/capture_tag_session.py` now hard-fails on SEGGER by-id port/SNR mismatch.
2. Ref115 idle profile build kept with:
   - `APP_TAG_UWB_ENABLE=0`
   - `APP_TAG_FW_MARKER=ref115-idle-uwb-off-ota`
   - sign version bumped to `0.0.2+115` for explicit OTA replacement.

Stage2-only retest artifacts:
- Session root:
  - `logs/sequential_dual_cal/stage2_isolation_retest_20260327_230230`
- Master OTA capture:
  - `logs/master_ble_sessions/stage2_isolation_master_ota_20260327_230230/raw.log`
  - Evidence: `OTA upload complete`, `OTA reset request`, reconnect then `NUS notify: OTA_READY`, no `TagSummary`.
- Ref115 serial capture (correct port):
  - `logs/tag_sessions/stage2_isolation_ref115_idle_bootcheck_20260327_230230/raw.log`
  - Evidence: no `Range anchor=...`, no `Initiator RX timeout/error ...`, no motion summaries.

Operator verification rule for "Ref115 exited UWB":
1. Master OTA log shows upload complete + reset + reconnect (`OTA_READY`).
2. Ref115 capture uses `/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00` (not master port).
3. Ref115 raw log has zero range/motion lines over the observation window.

## Stage 3 — Activate Tag127 rotation calibration
1. Build Tag127 rotation-calibration profile:
```bash
./scripts/build_tag127_rotation_calibration_ota_profile.sh \
  build-tag-ota-tag127-rotation-calibration \
  build-master-ota-tag127-rotation-calibration
```
2. Flash master OTA image:
```bash
./scripts/flash_master_noninteractive.sh \
  683234364 build-master-ota-tag127-rotation-calibration/zephyr/zephyr.hex
```
3. Verify Tag127 boot marker:
   - `tag127-rotation-calibration-ota`
4. Capture Tag127 rotation calibration session in a **separate** session folder.

## Stage 4 — Compare 115 vs 127 calibration outputs
Compare sessions separately; do not merge raw captures:
1. Anchor coverage count (valid anchors per session)
2. Range continuity / timeout ratio per anchor
3. Residual quality metrics (`rms`, `max` if solve stage is run later)
4. Geometry plausibility vs expected upper/lower structure

---

## Artifact Separation Rules
Keep outputs independent:
- Ref115 static session:
  - `logs/tag_sessions/ref115_*`
- Tag127 rotation session:
  - `logs/tag_sessions/tag127_rotation_*`

Never reuse one tag's raw session as the other's calibration evidence.

---

## What Is Supported vs Missing

### Supported now
- Sequential staging with explicit 115 deactivation step.
- OTA-based profile switching for both tags.
- Explicit calibration profile for Tag127.
- Master flashing path pinned to SN `683234364`.

### Still missing (not implemented in this round)
- One-click orchestration script that executes all OTA/capture steps end-to-end.
- Automatic guard that blocks Stage 3 if Ref115 still has UWB enabled.

---

## Minimal Operator Checklist
1. Complete matrix + anchor restore.
2. Run Ref115 calibration stage and capture output.
3. OTA Ref115 to idle (`UWB off`) and verify UWB-disabled markers.
4. Run Tag127 rotation calibration stage and capture output.
5. Compare two sessions offline; only then decide solve/deploy actions.

If any step fails, stop and resolve before activating the other calibration tag.

## End-to-End Validation Run (2026-03-27, session `20260327_231750`)

Session root:
- `logs/sequential_dual_cal/session_20260327_231750`

### Stage1 — Ref115 static calibration
- Master OTA log:
  - `logs/master_ble_sessions/seqdual2_stage1_ref115_ota_20260327_231750/raw.log`
  - Contains `OTA upload complete`, `OTA reset request`, reconnect + `OTA_READY`.
- Ref115 calibration capture:
  - `logs/tag_sessions/seqdual2_stage1_ref115_calib_20260327_231750/raw.log`
  - `logs/tag_sessions/seqdual2_stage1_ref115_calib_20260327_231750/ranges.csv`
  - `logs/tag_sessions/seqdual2_stage1_ref115_calib_20260327_231750/summary.json`
- Result: PASS (dense multi-anchor ranging).

### Stage2 — Ref115 idle (UWB off)
- Master OTA log:
  - `logs/master_ble_sessions/seqdual2_stage2_ref115_idle_ota_20260327_231750/raw.log`
  - Contains `OTA upload complete`, `OTA reset request`, reconnect + `OTA_READY`.
- Ref115 idle verification capture:
  - `logs/tag_sessions/seqdual2_stage2_ref115_idle_20260327_231750/raw.log`
  - `logs/tag_sessions/seqdual2_stage2_ref115_idle_20260327_231750/summary.json`
- Result: PASS (`position_samples=0`, `range_samples=0`, no UWB stream lines).

### Stage3 — Tag127 rotation calibration
- Master OTA + runtime log:
  - `logs/master_ble_sessions/seqdual2_stage3_tag127_rotcal_ota_20260327_231750/raw.log`
  - Contains token206 targeting, `OTA upload complete`, `OTA reset request`, reconnect + `OTA_READY`, then rotation calibration `TagSummary`.
- Master summary:
  - `logs/master_ble_sessions/seqdual2_stage3_tag127_rotcal_ota_20260327_231750/summary.json`
- Result: PASS (rotation calibration stream active after OTA).

### Ref115 vs Tag127 Comparison (this run)

| Metric | Ref115 static calibration | Tag127 rotation calibration |
|---|---:|---:|
| Session path | `logs/tag_sessions/seqdual2_stage1_ref115_calib_20260327_231750` | `logs/master_ble_sessions/seqdual2_stage3_tag127_rotcal_ota_20260327_231750` |
| Calibration-usable | Yes | Yes |
| Valid anchors observed | 8/8 (IDs 0..7) | 8/8 (A..H in TagSummary anchor sets) |
| Per-anchor counts | `{0:54,1:45,2:41,3:48,4:51,5:44,6:41,7:47}` | anchor usage in summaries: `{A:15,B:19,C:13,D:14,E:7,F:4,G:10,H:19}` |
| Timeout/error counts | `{0:8,1:17,2:20,3:13,4:11,5:17,6:20,7:14}` from Ref115 raw log | N/A on master-only path (no direct per-anchor timeout counters) |
| Residual quality | `rms_mean=42.21 mm`, `max_mean=59.06 mm` | `rms_mean≈22.79 mm`, `max_mean≈33.63 mm` from 19 TagSummary samples |
| Coverage quality | Strong static multi-anchor coverage | Strong dynamic/rotation coverage, wider geometry spread |

Interpretation:
- Ref115 session is stronger for stable static reference capture and per-anchor diagnostic depth.
- Tag127 session is stronger for motion/rotation geometric diversity.
- Both are calibration-usable and complementary.
