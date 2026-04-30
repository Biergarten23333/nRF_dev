# Broadcast b15 Checkpoint - Single-Window Collector

Date: 2026-04-29

## Build

- Tag marker: `alt-bcast-b15-collector-g2400-r1000`
- Current anchors stayed on `alt-bcast-a2-g2400-r1000-coop1`.
- No anchor image changes were made for b15.
- b15 Tag RX changed the broadcast response path from per-slot delayed RX to one continuous collector window:
  - no `DWT_START_RX_DELAYED`
  - no RX auto-reenable
  - no double buffer
  - manual immediate RX reenable after each received frame

Current deployed guard is `2400us` because A-H responder serial logs show response slots at
`2400, 3400, 4400, 5400us`.

## OTA

All three tags matched b15:

- BSF66F: `match=True`
- BS2DCE: `match=True`
- BSDC91: `match=True`

## Captures

### 1 Tag Calibration

Directory:

`logs/alt_bcast_b15_collector_BSF66F_capture_20260429_231916`

- `CM ok=504/529`
- A-H were all seen by the Tag.
- `CF first_to_last_us=0` for all rows.
- `poll_count=4`
- `frame_us median=23132us`

This confirms b15 fixed the previous rank0-only problem.

### 1 Tag Motion Positioning

Directory:

`logs/alt_bcast_b15_collector_BSF66F_motion_capture_20260429_232202`

- `positions_all=158`
- BSF66F median RMS: `109mm`
- BSF66F p95 RMS: `137mm`
- Main anchor set: `ABEF`

### 3 Tag Calibration

Directory:

`logs/alt_bcast_b15_collector_3tag_cal_capture_20260429_232655`

- `cm_all=3301`
- `cf_all=1069`
- `CF first_to_last_us=0` for all tags.
- Listener saw both broadcast polls and anchor responses:
  - `uf_rows=730`
  - `ul_rows=58`

Per-tag CM:

- BSF66F: `ok=573`, `timeout=178`
- BS2DCE: `ok=612`, `timeout=833`, `reject=13`
- BSDC91: `ok=938`, `timeout=151`, `reject=3`

BS2DCE has much higher timeout rate in mixed cal/roto profile and needs follow-up.

### 3 Tag Motion Positioning

Directory:

`logs/alt_bcast_b15_collector_3tag_motion_capture_20260429_232842`

- `positions_all=537`
- BSF66F: `184` positions, median RMS `106mm`, p95 RMS `129mm`
- BS2DCE: `180` positions, median RMS `41mm`, p95 RMS `150mm`
- BSDC91: `173` positions, median RMS `40mm`, p95 RMS `169mm`

Main anchor set for all three tags was `ABEF`.

## Current Status

b15 is the first broadcast branch version that works end-to-end:

- Broadcast poll side remains compressed: `first_to_last_us=0`.
- Anchor responses are received by the Tag beyond rank0.
- Single Tag positioning works.
- Three Tag motion positioning works.

Remaining issues:

- Motion positioning has occasional outliers, especially one BSDC91 max-RMS spike.
- Listener still sees far fewer responses than the Tag, so listener parser/capture sensitivity is useful for air evidence but should not be treated as the primary success metric.

## Overnight BS2DCE Isolation Retest

Date: 2026-04-30

Directory:

`logs/overnight_b15_diag_20260429_234317`

This was a capture-only retest. No firmware, anchor, OTA, or flash changes were made.

Per-test CM ok rates:

- Single BSF66F static: `1713/1800 = 95.2%`
- Single BS2DCE roto: `1918/2071 = 92.6%`
- Single BSDC91 roto: `1897/2034 = 93.3%`
- BSF66F + BS2DCE: BSF66F `94.2%`, BS2DCE `94.4%`
- BSF66F + BSDC91: BSF66F `94.5%`, BSDC91 `93.5%`
- BS2DCE + BSDC91: BS2DCE `93.8%`, BSDC91 `94.0%`
- Three-tag mixed cal long: BSF66F `94.8%`, BS2DCE `94.9%`, BSDC91 `94.0%`

Three-tag mixed cal long:

- `cm_all=14377`
- `cf_all=4764`
- `CF first_to_last_us=0` for all tags
- `CF solve_reason`: `success=3056`, `pending=1708`

Conclusion:

- The earlier high BS2DCE timeout rate in `logs/alt_bcast_b15_collector_3tag_cal_capture_20260429_232655` did not reproduce.
- BS2DCE is not a single-tag failure.
- BS2DCE also stayed stable in all two-tag combinations.
- b15 can continue to three-tag motion positioning validation.

## Three-Tag Motion Validation

Date: 2026-04-30

Directory:

`logs/alt_bcast_b15_collector_3tag_motion_capture_20260430_001501`

Capture:

- Duration: `180s`
- Profiles: BSF66F `motion`, BS2DCE `motion`, BSDC91 `motion`
- `positions_all=2379`
- `cm_all=0`, `cf_all=0` because motion profile emits TS positions, not calibration CM/CF rows.
- Listener saw mostly broadcast polls and only a few decoded responses:
  - `UF=2667`
  - `UL=10`

Per-tag TS output:

- BSF66F: `816` positions, RMS median `164mm`, p95 `184mm`, max `207mm`, main anchors `ABEF`
- BS2DCE: `779` positions, RMS median `37mm`, p95 `156mm`, p99 `293mm`, one large outlier, main anchors `ABEF`
- BSDC91: `784` positions, RMS median `38mm`, p95 `194mm`, p99 `1354mm`, nine `>1000mm` outliers, main anchors `ABEF`

Conclusion:

- b15 three-tag motion is running end-to-end and producing stable TS position output.
- BS2DCE is healthy in motion mode as well.
- The next quality issue is not basic ranging/link failure; it is outlier suppression / motion continuity on roto tags, especially BSDC91.
