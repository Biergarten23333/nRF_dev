# Ref115 Calibration Output Observation (2026-03-27)

## 1. Session Path
- `logs/tag_sessions/ref115_cali_output_test_20260327_174346`

## 2. Raw Log Path
- `logs/tag_sessions/ref115_cali_output_test_20260327_174346/raw.log`

## 3. Capture Scope
- Duration: 60 s
- Mode of capture: no reset (`--no-reset`)
- Operation constraints respected:
  - no anchor matrix run
  - no firmware flashing
  - no solve/promotion
  - no Ref115 build/mode change

## 4. Detected Message Types

From `raw.log`:
- `Range anchor=...` lines: **740**
- `Initiator RX timeout/error ...` lines: **5175**
- `Tag solve pending ...` lines: **37**

Calibration markers in this 60 s window:
- `calibration`/`ref115-calibration` strings: **0**  
  Note: this capture is no-reset runtime window, so boot marker lines are not expected to reappear.

TDMA/runtime signatures (must not appear):
- `src=MASTER`: **0**
- `TS;`: **0**
- `slot guard`: **0**

## 5. Example Real Lines (from raw.log)

Range lines:
- `5:Range anchor=0 addr=0xa100 raw=2598 mm filt=2618 mm ok=190071 fail=49555 q=100%`
- `13:Range anchor=0 addr=0xa100 raw=2641 mm filt=2614 mm ok=190072 fail=49555 q=100%`
- `21:Range anchor=0 addr=0xa100 raw=2629 mm filt=2618 mm ok=190073 fail=49555 q=100%`

Timeout lines:
- `6:Initiator RX timeout/error anchor=4 addr=0xa104 status=0x008200f2 ok=0 fail=239510 q=0%`
- `7:Initiator RX timeout/error anchor=1 addr=0xa101 status=0x008200f2 ok=0 fail=239510 q=0%`
- `8:Initiator RX timeout/error anchor=5 addr=0xa105 status=0x008200f2 ok=0 fail=239510 q=0%`

Solve-pending line:
- `93:Tag solve pending: need >=4 valid anchors across both planes plan=full active=8 sweep_ms=81 valid=[A]`

## 6. Field Breakdown (Range Line)

For:
- `Range anchor=0 addr=0xa100 raw=2598 mm filt=2618 mm ok=190071 fail=49555 q=100%`

Fields:
- `anchor_id`: `0`
- `addr`: `0xa100`
- `raw_mm`: `2598`
- `filtered_mm`: `2618`
- `ok_count`: `190071`
- `fail_count`: `49555`
- `quality_percent`: `100`

## 7. Anchor Coverage Summary

Observed anchors with valid range lines:
- `{0}` only (Anchor A)

Observed anchors in timeout lines:
- `{1,2,3,4,5,6,7}` repeatedly timing out

Coverage metrics:
- anchors with valid ranges: **1 / 8**
- anchors missing valid ranges: **7 / 8**

## 8. Temporal Behavior

Estimated from 60 s capture:
- overall valid range rate: `740 / 60 = 12.33 Hz`
- per-anchor valid range rate:
  - anchor 0: **12.33 Hz**
  - anchors 1..7: **0 Hz valid** (timeouts only)

## 9. Calibration Mode Validity Check

Validation criteria:
- >=4 anchors with valid ranges: **FAIL**
- no TDMA runtime signatures: **PASS**
- output dominated by ranging (not motion TS): **PASS**
- continuous data: **PASS** (continuous, but mostly single-anchor valid)

## 10. Final Verdict

- **INVALID Calibration Mode output quality**

Exact failure reason:
- **single-anchor collapse / missing anchors**  
  Ref115 runtime stream is active and non-TDMA-contaminated, but effective ranging is only from Anchor A; B..H are timeout-dominant.
