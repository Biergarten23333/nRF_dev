# V4 Roto Layout 600s Validation - 2026-05-03

## Run Context

- Layout pushed by APOS: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_rotoarm_tilted_redo.json`
- APOS verify: `SS-TWR/alt-SS-TWR/broadcast/logs/apos_verified_v4_rotoarm_tilted_redo_20260503_182240/summary.json`
- Capture directory: `SS-TWR/alt-SS-TWR/broadcast/logs/motion_3tag_v4_roto_layout_600s_20260503_183255`
- Session directory: `SS-TWR/alt-SS-TWR/broadcast/logs/motion_3tag_v4_roto_layout_600s_20260503_183255/recv_20260503_183256`
- Anchor responder preflight: ready=8/8
- Profiles: BSF66F:motion, BS2DCE:motion, BSDC91:motion
- Duration: 600s

## Throughput Result

The 600s run completed successfully with no controller loss.

| Metric | Value |
|---|---:|
| positions_all | 18002 |
| cm_all / cr_all / cf_all | 0 / 0 / 0 |
| BSF66F TS | 6001 |
| BS2DCE TS | 6001 |
| BSDC91 TS | 6000 |
| BSF66F rate | 10.002 Hz |
| BS2DCE rate | 10.002 Hz |
| BSDC91 rate | 10.000 Hz |

Per-minute total positions:

| Minute | Positions | Mean RMS mm | Median RMS mm |
|---:|---:|---:|---:|
| 0 | 1799 | 121.4 | 112 |
| 1 | 1800 | 118.3 | 110 |
| 2 | 1800 | 118.6 | 110 |
| 3 | 1800 | 119.2 | 111 |
| 4 | 1800 | 117.7 | 110 |
| 5 | 1802 | 115.1 | 108 |
| 6 | 1798 | 116.5 | 107 |
| 7 | 1801 | 115.8 | 109 |
| 8 | 1801 | 115.8 | 108 |
| 9 | 1801 | 171.8 | 151 |

## RMS Result

| Tag | Count | Mean | Median | P90 | P95 | P99 | Max |
|---|---:|---:|---:|---:|---:|---:|---:|
| BSF66F | 6001 | 108.5 | 96 | 156 | 271 | 318 | 386 |
| BS2DCE | 6001 | 128.7 | 120 | 206 | 249 | 327 | 432 |
| BSDC91 | 6000 | 131.8 | 127 | 202 | 228 | 288 | 470 |

Per-tag minute RMS means:

| Minute | BSF66F | BS2DCE | BSDC91 |
|---:|---:|---:|---:|
| 0 | 93.6 | 132.6 | 138.1 |
| 1 | 92.4 | 128.6 | 133.9 |
| 2 | 91.6 | 130.8 | 133.6 |
| 3 | 97.3 | 127.3 | 132.9 |
| 4 | 93.3 | 128.9 | 130.7 |
| 5 | 88.8 | 126.5 | 130.0 |
| 6 | 92.1 | 127.1 | 130.3 |
| 7 | 94.6 | 127.1 | 125.6 |
| 8 | 90.2 | 128.8 | 128.5 |
| 9 | 251.0 | 129.4 | 134.7 |

The last-minute RMS increase is almost entirely BSF66F. BS2DCE and BSDC91 remain stable through minute 9. This does not look like a global layout/Anchor failure.

## Anchor Set Usage

Top anchor sets:

| Tag | Most Common Anchor Sets |
|---|---|
| BSF66F | ABCDEFGH:3986, ABCDEFG:1823 |
| BS2DCE | ABCDEFG:3841, ABCDEFGH:1967 |
| BSDC91 | ABCDEFGH:5325, ABCDEFG:428 |

H is still not present in every solve, especially for BS2DCE. However, throughput is stable and all tags continue producing TS at 10 Hz.

## Comparison To Previous 60s Baselines

| Layout / Capture | BSF66F mean/median | BS2DCE mean/median | BSDC91 mean/median |
|---|---:|---:|---:|
| Huber APOS 60s | 109.4 / 112.5 | 131.8 / 134.0 | 167.3 / 171.0 |
| V4 roto APOS 60s | 98.1 / 98.0 | 128.1 / 116.0 | 133.7 / 126.0 |
| V4 roto APOS 600s | 108.5 / 96.0 | 128.7 / 120.0 | 131.8 / 127.0 |

V4 roto APOS remains better than the Huber APOS baseline on median RMS for all three tags, and especially improves BSDC91. The 600s BSF66F mean is inflated by the last minute; before minute 9 it stayed around 89-97 mm mean RMS.

## Listener

Listener finished successfully and saw UWB traffic throughout:

- UF rows: 21627
- UL rows: 2847
- UF code `0xe0`: 18780
- UL/response code `0xe1`: 2847

As before, listener response counting is incomplete compared with Tag-side data, so it is useful as on-air evidence but not as the primary per-anchor response counter.

## Verdict

The 600s validation passes the throughput/stability target:

- 3 Tags at 10 Hz for 600s
- 18002 TS rows
- No CM/CR/CF legacy rows
- No controller loss
- Balanced per-tag output

Keep the V4 roto layout for now. The only anomaly is BSF66F RMS rising in the final minute, which should be investigated separately with another short BSF66F-focused run or by checking whether the stationary tag/environment moved near the end.
