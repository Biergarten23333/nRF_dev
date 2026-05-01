# Broadcast b37 Force-8 Checkpoint

Date: 2026-05-01

## Firmware State

- Tag marker: `alt-bcast-b37-force8-pretx-g2000-r1000`
- Tags OTA verified:
  - `BSF66F match=True`
  - `BS2DCE match=True`
  - `BSDC91 match=True`
- Anchors unchanged: `alt-bcast-a5-g2000-r1000-coop1`
- Master_Tag carrier: `alt-bcast-b37-force8-pretx-g2000-r1000-carrier`

## Code Changes

- Added broadcast immediate TX and prewrite support.
- Fixed RXG `pre=` diagnostic to report the TX path actually used.
- Fixed `APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP=1` under multitag plan mode.

## b35 Result: 4-Anchor ABCE/ABEF Path

Capture:

`logs/alt_bcast_b35_pretx_abce_motion_listener_anchorserial_30s_20260430_234435`

Key result:

- `positions_all=751` in 30s, balanced across three tags.
- RXG timing:
  - `pre=1` effectively used; b35 log showed `pre=0` only because the flag was cleared before reporting.
  - `slot_to_txdone_us median=335us p95=457us`
  - `txdone_to_rxstart_us median=793us p95=946us`

Conclusion: prewriting + immediate TX successfully removed the delayed-TX wait and hot-path frame write cost.

## b36 Result: Force-Full Did Not Apply

Capture:

`logs/alt_bcast_b36_pretx_8anc_motion_listener_anchorserial_30s_20260430_235333`

Key result:

- `positions_all=733` in 30s.
- RXG still showed mostly `mask=0x33 pc=4 win=5300`.

Root cause:

- `APP_TAG_MULTITAG_PLAN_MODE=1` returned early in `ss_twr_init_prepare_sweep_plan()`.
- The existing `APP_ALT_SS_TWR_BCAST_FORCE_FULL_SWEEP` check lived only in the later non-multitag branch.

## b37 Result: True 8-Anchor Broadcast

Capture:

`logs/alt_bcast_b37_force8_pretx_motion_listener_anchorserial_20s_20260501_000222`

Key result:

- RXG confirmed true full broadcast:
  - `mask=0xff` for all RXG rows.
  - `pc=8` for all RXG rows.
  - `win=9300us` for all RXG rows.
  - `pre=1` for all RXG rows.
- Timing stayed good:
  - `slot_to_txdone_us median=335us p95=366us`
  - `txdone_to_rxstart_us median=793us p95=793us`
- Position output:
  - `positions_all=69` in 20s.
  - Per tag: `BSF66F=26`, `BS2DCE=21`, `BSDC91=22`.
  - All positions were `plan_label=full`.
  - Some solves used all anchors, e.g. `ABCDEFGH`, but many frames used subsets.

Conclusion:

- True 8-anchor broadcast works, but not at the needed 10Hz/tag rate with current `g2000/r1000`.
- The bottleneck is no longer slot-entry overhead. It is the 8-anchor response window and/or response collection density inside the 10ms TDMA slot.

## Next Direction

Use b35/b37 evidence to choose the next optimization:

- Keep 4-anchor fast tracking as the high-rate path.
- Use 8-anchor full sweeps as lower-rate refresh/calibration.
- Or reduce true 8-anchor window by changing Anchor guard/spacing after a controlled Anchor OTA build.

