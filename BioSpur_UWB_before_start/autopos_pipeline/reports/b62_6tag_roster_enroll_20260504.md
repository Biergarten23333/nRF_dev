# b62 6-Tag Roster Enrollment Capture - 2026-05-04

## Purpose

Validate the new general roster/enrollment flow for scaling beyond the original 5 tags.

This test intentionally did not use a target-specific reconnect workaround for `BSF8E0`.

## Setup

- Master_Tag carrier: rebuilt/flashed with roster enrollment support
- Master_Tag CDC used for capture: `/dev/serial/by-id/usb-BioSpur_1_BioSpur_BLE_Control_6918E0384172A49F-if00`
- Anchor preflight: ready `8/8`
- Tags:
  - `BSF66F`
  - `BS2DCE`
  - `BSDC91`
  - `BSE88E`
  - `BS6F3A`
  - `BSF8E0`
- Duration: 180 s
- Mode: motion profile, 10 Hz target, TR/TS/TF architecture

## General Flow Fix Validated

The capture flow now performs a general enrollment phase:

1. Silence resident Tag links with `MODE AOTA`
2. `tdma clear`
3. Add all requested targets via `tdma roster <tag> <profile>`
4. Connect broad `BS*`
5. Wait for all requested links
6. Apply TDMA profiles and rebalance only after all links are ready

Result:

- Link setup reached `6/6`
- `BSF8E0` joined without target-specific reconnect
- Capture completed successfully

## Capture Result

Capture directory:

`SS-TWR/alt-SS-TWR/broadcast/logs/tdma_6tag_motion180_b62_roster_enrollfw_portfix_20260504_004114/recv_20260504_004116`

Summary:

- `positions_all=8528`
- `tr_all=81784`
- `tr_valid_all=65633`
- `tf_all=0`
- `cm_all=0`, `cs_all=0`, `cr_all=0`, `cf_all=0`

Per-tag:

| Tag | TS rows | TR rows | Valid TR | Notes |
|---|---:|---:|---:|---|
| BSF66F | 1677 | 13712 | 13113 | Good |
| BS2DCE | 1713 | 13704 | 13377 | Good |
| BSDC91 | 1715 | 13728 | 12184 | Good, more timeouts than others |
| BSE88E | 1703 | 13720 | 13117 | Good |
| BS6F3A | 1709 | 13736 | 13332 | Good |
| BSF8E0 | 11 | 13184 | 510 | Joined TDMA, but UWB ranging mostly timed out |

## BSF8E0 Breakdown

`BSF8E0` is no longer a BLE enrollment problem. It produces steady TR rows, but nearly all anchor responses are timeout.

TR status:

- `O=510`
- `T=12636`
- `R=38`

By anchor:

| Anchor | O | T | R |
|---:|---:|---:|---:|
| 0 | 441 | 1169 | 38 |
| 1 | 7 | 1641 | 0 |
| 2 | 7 | 1641 | 0 |
| 3 | 11 | 1637 | 0 |
| 4 | 11 | 1637 | 0 |
| 5 | 11 | 1637 | 0 |
| 6 | 11 | 1637 | 0 |
| 7 | 11 | 1637 | 0 |

Interpretation:

- TDMA/BLE enrollment works for six tags.
- The remaining `BSF8E0` issue is UWB receive/range quality, not a Master_Tag connection/allow-list problem.
- Other five tags prove the shared TDMA and BLE path remains healthy under six-tag enrollment.

## Next Debug Direction

For `BSF8E0`, debug the UWB path specifically:

- Confirm antenna orientation and DWM1001C RF path on the custom board.
- Compare `BSF8E0` in the same physical location/orientation as a known-good tag.
- If possible, run a short single-tag TR capture with `BSF8E0` placed where `BS6F3A` passes.
- Inspect whether only anchor 0 succeeds because of geometry/RF path or a frame/timestamp issue.

Do not revert to target-specific BLE connection workarounds; the general roster/enrollment architecture is the correct path for 6/8/10 tags.
