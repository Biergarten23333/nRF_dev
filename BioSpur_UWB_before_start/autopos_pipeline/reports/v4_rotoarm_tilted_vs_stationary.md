# V4 Roto Arm Tilted vs Stationary TR Comparison

Generated after the 2026-05-03 tilted Roto Arm capture. No firmware or APOS/DTAG deployment was performed.

## Inputs

- Stationary data: `autopos_pipeline/logs/v4_data_125216_with_tr.json`
- Roto tilted data: `autopos_pipeline/logs/v4_data_rotoarm_tilted_20260503.json`
- Stationary solve: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_stationary_compare.json`
- Roto tilted solve: `autopos_pipeline/solve_v4_fusion/anchor_layout_v4_rotoarm_tilted.json`
- Init layout for both: `autopos_pipeline/solve_v4_fusion/anchor_layout_interonly_huber_125216.json`
- Solver parameters for both: Huber, f_scale=30mm, sigma_inter=15mm, sigma_tag=80mm, tag_subsample=10

## Capture Sanity

The tilted capture is not a clean 3-tag tilted dataset. BSDC91 produced only a small number of TR frames and BS2DCE was sparse compared with BSF66F.

| Dataset | TR rows | BSF66F rows / frames | BS2DCE rows / frames | BSDC91 rows / frames |
|---|---:|---:|---:|---:|
| Stationary | 6958 | 2350 / 300 | 2253 / 299 | 2355 / 301 |
| Roto tilted | 6272 | 5259 / 680 | 776 / 162 | 237 / 39 |

## Solve Quality

| Metric | Stationary TR | Roto Tilted TR |
|---|---:|---:|
| inter_anchor_rms all 28 | 125.8 mm | 97.1 mm |
| inter_anchor_rms inlier <=50mm | 23.0 mm | 18.7 mm |
| inter inlier count <=50mm | 18 | 22 |
| tag_anchor_rms | 96.4 mm | 107.5 mm |
| d_anchor range | -14.0 .. 8.8 mm | -5.1 .. 8.6 mm |
| d_tag BS2DCE | 0.0 mm | 0.0 mm |
| d_tag BSDC91 | 16.4 mm | -1.0 mm |
| d_tag BSF66F | 19.2 mm | 30.0 mm |
| optimizer n_tag_obs after subsample | 699 | 618 |
| optimizer n_tag_frames | 90 | 83 |

## Anchor Position Shift

| Anchor | Stationary xyz mm | Roto xyz mm | Shift mm | Z shift mm |
|---|---:|---:|---:|---:|
| A | (0, -0, 0) | (0, 0, 0) | 0.0 | +0.0 |
| B | (4460, -0, 0) | (4443, 0, 0) | 16.9 | -0.0 |
| C | (3990, 3749, 0) | (4057, 3706, -0) | 79.7 | -0.0 |
| D | (-188, 2750, 239) | (-245, 2753, -245) | 487.6 | -484.2 |
| E | (54, -128, 1676) | (96, -5, 1682) | 130.6 | +6.0 |
| F | (4577, -89, 1568) | (4520, 107, 1562) | 204.2 | -5.8 |
| G | (4129, 3673, 1659) | (3927, 3827, 1608) | 259.0 | -50.7 |
| H | (-435, 2696, 1789) | (-491, 2712, 1295) | 497.0 | -493.5 |

Anchors shifted >50mm: C 79.7mm, D 487.6mm, E 130.6mm, F 204.2mm, G 259.0mm, H 497.0mm

## Per-Anchor Per-Tag Residual Heatmap

Mean residual in mm from a fixed-layout per-frame replay. Positive means predicted range is longer than measured.

### Stationary

| Tag | A | B | C | D | E | F | G | H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BS2DCE | -45 (299) | -13 (299) | +16 (297) | +25 (297) | -13 (299) | +74 (295) | -223 (299) | +10 (168) |
| BSDC91 | +78 (300) | +26 (299) | -247 (301) | +7 (300) | -134 (301) | -27 (297) | +78 (299) | -6 (258) |
| BSF66F | -19 (300) | -11 (300) | +47 (299) | -9 (299) | +70 (297) | -63 (299) | +23 (300) | -87 (256) |

### Roto tilted

| Tag | A | B | C | D | E | F | G | H |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BS2DCE | +52 (95) | -494 (94) | -13 (99) | -88 (95) | -248 (98) | -24 (75) | -99 (80) | +118 (25) |
| BSDC91 | +35 (39) | -126 (38) | -80 (39) | -56 (39) | -170 (39) | +15 (20) | -42 (20) | -53 (3) |
| BSF66F | -6 (679) | +16 (678) | +14 (679) | -19 (680) | -9 (678) | -2 (677) | -126 (680) | +42 (508) |

## Interpretation

- The tilted capture should not be pushed as APOS/DTAG: BSDC91 is heavily underrepresented and the solve drives BSF66F d_tag to the +30mm bound.
- Roto tilted inter inlier RMS is good (18.7mm) and all-28 inter RMS improves versus the stationary compare (97.1mm vs 125.8mm), but tag residuals get worse and are dominated by sparse/imbalanced moving-tag data.
- The intended Z-axis benefit is not cleanly observable in this run because the two Roto tags did not contribute balanced TR data. Anchor position shifts >50mm are therefore not trustworthy as layout updates.
- Next recommended action: rerun tilted capture only after BS2DCE and BSDC91 both produce steady TR/TS near 10Hz. A valid tilted dataset should have roughly balanced TR rows and frames across all three tags, or at least both Roto tags.

## Decision

Do not deploy `anchor_layout_v4_rotoarm_tilted.json`. Keep the current Huber APOS layout until a balanced tilted Roto capture is collected.
