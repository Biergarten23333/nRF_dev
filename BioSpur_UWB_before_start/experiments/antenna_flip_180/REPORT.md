# 180 deg Antenna Flip Experiment -- Report

- Date: 2026-07-13T15:56:09
- Tags: BS9336, BS955A, BSCCF4  (CCF4 antenna mounted 180 deg opposite to 9336/955A)
- Wand pose: T-plane vertical, room center
- Phase 1 (0-120s): 9336_955A_face_ADHE
- Phase 2 (140-260s): 9336_955A_face_BCFG
- Ranges: RAW, status=='O', bounds [500,7000] mm
- TR lines parsed: 6609, ranges kept: 41943

## Per-tag per-anchor delta (phase2 - phase1), mean mm

| anchor | BS9336 D (mm) | BS955A D (mm) | BSCCF4 D (mm) | max\|D\| | note |
|---|---|---|---|---|---|
| A | 215.1 | -220.0 | -223.0 | 223.0 | large |
| B | 319.1 | -38.0 | -384.2 | 384.2 | large |
| C | -821.5 | 28.4 | 369.9 | 821.5 | large |
| D | -476.2 | 33.0 | 378.6 | 476.2 | large |
| E | 775.3 | 283.5 | -392.9 | 775.3 | large |
| F | 282.6 | -111.5 | -220.2 | 282.6 | large |
| G | -478.7 | 13.2 | 270.1 | 478.7 | large |
| H | -452.4 | -31.9 | 168.1 | 452.4 | large |

## Q1 -- Do 9336/955A shift opposite to CCF4?

- corr(D_9336, -D_CCF4)  = 0.957
- corr(D_955A, -D_CCF4)  = 0.006
- mean anti-correlation  = 0.482  (n=8 anchors)
- sibling check corr(D_9336, D_955A) = 0.170 (expect strongly +)
- **no clear anti-correlation**  (>0.7 => antenna directionality)

## Q2 -- Effect size

- max\|D\|   = 821.5 mm
- median\|D\|= 276.4 mm
- RMS(D)    = 358.9 mm  -> **major (RMS>30mm)**
- mean signed D = -29.7 mm (common-mode across all tag x anchor)

## Q3 -- Which anchors are most affected? (rank by mean |D|)

| rank | anchor | mean \|D\| (mm) |
|---|---|---|
| 1 | E | 483.9 |
| 2 | C | 406.6 |
| 3 | D | 295.9 |
| 4 | G | 254.0 |
| 5 | B | 247.1 |
| 6 | A | 219.4 |
| 7 | H | 217.5 |
| 8 | F | 204.8 |

Anchors on the ADHE/BCFG (flip-facing) axis should dominate; anchors on the perpendicular axis should be near zero. A layout table is in results.json.

## Q4 -- Within-phase scatter

- mean std phase1 = 48.5 mm
- mean std phase2 = 46.5 mm
- mean d(std)     = -2.0 mm (rising scatter on back-facing anchors => SNR drop, as expected)

## 2.4 -- Caliper prediction

Physical displacement of each tag between phases (geometric confound):
- BS9336: 769.1 mm
- BS955A: 103.6 mm
- BSCCF4: 474.0 mm

Measured inter-tag distance (rigid wand => should be constant; change = flip-induced caliper distortion, antenna + residual geometry):
| pair | phase1 (mm) | phase2 (mm) | change (mm) |
|---|---|---|---|
| BS9336-BS955A | 947.7 | 717.5 | -230.2 |
| BS9336-BSCCF4 | 605.1 | 659.1 | 54.0 |
| BS955A-BSCCF4 | 560.5 | 717.3 | 156.8 |

CCF4-specific relative delta (D_CCF4 - mean(D_9336, D_955A), removes the common rigid-body translation shared by all 3 tags):
- RMS = 574.1 mm, max|.| = 922.3 mm
  This residual is the CCF4 antenna-opposite ranging swing a solver absorbs as a position shift, directly distorting the CCF4-955A caliper.

## Confounds

- A 180 deg flip translates any off-pivot tag by 2x its radius, injecting a REAL geometric range change on the same ADHE/BCFG axis. Compare the per-tag displacement above: if it is large (>~50 mm), the raw deltas are geometry-dominated and a positive Q1 is necessary but not sufficient. The CCF4-relative-delta metric cancels the common translation and is the cleaner antenna signal.
- Siblings 9336 & 955A sit at different points on the T-bar, so their deltas match only to the extent geometry is common; a high sibling correlation supports a shared (orientation) mechanism over per-tag geometry.

## VERDICT

**INCONCLUSIVE -- CONFOUNDED BY PHYSICAL DISPLACEMENT: the tags are off the rotation axis (median flip displacement 474 mm, max 769 mm), so the 180 deg flip TRANSLATED them and rigid-body rotation geometry -- not PCB-antenna directionality -- dominates the per-anchor deltas. The antenna effect cannot be isolated from this run. Re-run with the flip axis passing through the tags (or place ONE tag on the rotation axis at a time).**

_runtime: wall=0.16s cpu=0.16s on 12 CPUs_
