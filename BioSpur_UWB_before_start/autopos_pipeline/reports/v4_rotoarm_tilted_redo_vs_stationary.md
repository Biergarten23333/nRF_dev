# V4 Roto Arm Tilted Redo vs Stationary TR Comparison

Generated from the power-cycle redo capture on 2026-05-03. No APOS/DTAG deployment was performed.

## Inputs

- Stationary data: `autopos_pipeline/logs/v4_data_125216_with_tr.json`
- Roto tilted redo data: `autopos_pipeline/logs/v4_data_rotoarm_tilted_redo_20260503.json`
- Stationary solve: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_stationary_compare_redo.json`
- Roto tilted redo solve: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_rotoarm_tilted_redo.json`
- Init layout for both: `autopos_pipeline/solve_v4_fusion/anchor_layout_interonly_huber_125216.json`
- Solver parameters: Huber, f_scale=30mm, sigma_inter=15mm, sigma_tag=80mm, tag_subsample=10

## Capture Sanity

This redo capture is balanced and suitable for V4 analysis.

| Dataset | TR rows | BSF66F rows / frames | BS2DCE rows / frames | BSDC91 rows / frames |
|---|---:|---:|---:|---:|
| Stationary | 6958 | 2350 / 300 | 2253 / 299 | 2355 / 301 |
| Roto tilted redo | 15504 | 5198 / 677 | 4932 / 675 | 5374 / 680 |

## Solve Quality

| Metric | Stationary TR | Roto Tilted Redo TR |
|---|---:|---:|
| inter_anchor_rms all 28 | 125.8 mm | 119.1 mm |
| inter_anchor_rms inlier <=50mm | 23.0 mm | 19.3 mm |
| inter inlier count <=50mm | 18 | 15 |
| tag_anchor_rms all | 96.4 mm | 110.4 mm |
| tag inlier RMS <=100mm | 53.8 mm (559) | 49.1 mm (1246) |
| d_anchor range | -14.0 .. 8.8 mm | -13.4 .. 4.5 mm |
| d_tag BS2DCE | 0.0 mm | 0.0 mm |
| d_tag BSDC91 | 16.4 mm | 2.6 mm |
| d_tag BSF66F | 19.2 mm | -0.4 mm |
| optimizer n_tag_obs after subsample | 699 | 1563 |
| optimizer n_tag_frames | 90 | 204 |

## Anchor Position Shift

| Anchor | Stationary xyz mm | Roto redo xyz mm | Shift mm | Z shift mm |
|---|---:|---:|---:|---:|
| A | (0, -0, 0) | (0, 0, -0) | 0.0 | -0.0 |
| B | (4460, -0, 0) | (4460, -0, 0) | 0.2 | +0.0 |
| C | (3990, 3749, 0) | (4039, 3755, 0) | 49.0 | -0.0 |
| D | (-188, 2750, 239) | (-514, 2810, -178) | 532.4 | -416.9 |
| E | (54, -128, 1676) | (-110, 19, 1684) | 221.2 | +8.7 |
| F | (4577, -89, 1568) | (4377, 109, 1550) | 281.7 | -18.2 |
| G | (4129, 3673, 1659) | (3898, 3828, 1637) | 279.3 | -22.1 |
| H | (-435, 2696, 1789) | (-584, 2799, 1379) | 447.9 | -409.6 |

Anchors shifted >50mm: D 532.4mm, E 221.2mm, F 281.7mm, G 279.3mm, H 447.9mm

## Per-Anchor Per-Tag Residual Heatmap

Mean residual in mm from a fixed-layout per-frame replay. Positive means predicted range is longer than measured.

### Stationary

| Tag | A | B | C | D | E | F | G | H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BS2DCE | -45 (299) | -13 (299) | +16 (297) | +25 (297) | -13 (299) | +74 (295) | -223 (299) | +10 (168) |
| BSDC91 | +78 (300) | +26 (299) | -247 (301) | +7 (300) | -134 (301) | -27 (297) | +78 (299) | -6 (258) |
| BSF66F | -19 (300) | -11 (300) | +47 (299) | -9 (299) | +70 (297) | -63 (299) | +23 (300) | -87 (256) |

### Roto tilted redo

| Tag | A | B | C | D | E | F | G | H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BS2DCE | -20 (675) | -67 (674) | -58 (673) | -25 (675) | -136 (673) | -38 (672) | -55 (672) | -66 (218) |
| BSDC91 | +0 (677) | -30 (674) | -76 (678) | -4 (676) | -45 (678) | -2 (678) | +17 (676) | -24 (637) |
| BSF66F | +8 (676) | +14 (674) | +60 (675) | -28 (672) | +8 (674) | -15 (672) | -112 (674) | +49 (481) |

## Interpretation

- The redo capture fixed the earlier imbalance: all three tags produced roughly 675-680 TR frames and 1800 total TS positions.
- The V4 tilted redo solve has much more plausible tag delays than the prior bad run: BS2DCE=0.0mm, BSDC91=+2.6mm, BSF66F=-0.4mm.
- Inter-anchor inlier quality remains strong around 19mm RMS, but all-pair inter RMS and all tag RMS are still dominated by outliers. The robust/inlier numbers matter more than all-RMS here.
- Anchor Z shifts are meaningful but large for several anchors. This is a candidate layout, not something I pushed automatically.

## Decision

This redo solve is credible enough to review as an APOS candidate, but I did not deploy it. Suggested next step: host-side replay/validation against the redo TR and one fresh 60s capture before pushing APOS to Tags.
