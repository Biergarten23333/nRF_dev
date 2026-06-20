# AutoPos Solver Audit Summary

Generated: 2026-06-19T11:14:33

## Anchor Layout Solvers Found: 5 core versions

| Version | One-line description | Canonical implementation |
|---|---|---|
| V1 / v1-old | early MDS baseline, no delay | `run_clean_full_compare.py::solve_v1_old`; helper `solve_autopos_v1` |
| V2 | inverse-variance bidirectional fusion, no delay | `fuse_from_directed(v2)`, `solve_autopos_v2` |
| V3-lite | median/MAD/MVUE fusion, no delay | `fuse_from_directed(v3)`, `solve_autopos_v1` |
| V3-full | robust fusion plus alternating per-anchor delays | `solve_v3_full` |
| V4 / v4-io | bounded independent delay Huber inter-anchor solve | `solve_v4` |
| V5 / common-mode | common-mode `d_i=c+e_i` extension of V4 | `solve_v4_common_mode` |

## Tag Position Solvers Found: 4 C enum versions + 1 Python policy variant

| Version | One-line description | Canonical implementation |
|---|---|---|
| T1 | robust WLS multilateration | C method 1 |
| T2 | T1 plus quality-aware sigma inflation | C method 2 |
| T3 | T2 plus residual EMA and temporal prior | C method 3 |
| T4 | adaptive full-anchor T1 / low-redundancy T3 policy | Python wrapper + C method 4 |
| T4_V6_IMU_GATE | T4 plus accelerometer-gated temporal prior | Python wrapper policy over C method 4 |

No production `T5` was found.

## Current Production / Research Configuration

- Current official V4 anchor layout: `v4-io`, bounded independent delays, `d_A=0`, Huber normalized residuals, delay bound `[-60,+60] mm`.
- Current V5 research layout artifact: `v5-commonmode`, `d_i=c+e_i`, existing artifact `c=111.985 mm`, `e_reg=20 mm`, `max |e_i|=15.353 mm`.
- Current tag solver package default after recent edits: method defaults to `T1`; robust loss defaults to Huber with `solver_f_scale_mm=30`.
- T4 is a policy wrapper: full 8-anchor frames use memory-free T1; low-redundancy frames use T3-style memory/prior.

## Main Comparison Tables

- `tables/anchor_solver_versions.csv`
- `tables/tag_solver_versions.csv`
- `tables/parameter_comparison.csv`

## Recommended Changes / Operating Choices From Erlangen Campaign

1. Keep p50/V3-style inter-anchor aggregation for anchor self-calibration; lower-trim inter-anchor calibration was worse in the blind lower-trim experiment.
2. Use lower-tail/tag-side aggregation where appropriate for static tag ranges; recent results show lower-trim tag data plus Huber improves the static median.
3. Treat V5 common-mode as the physically correct scale fix, but report that V4/V5 empirical static ranking is dataset-dependent.
4. Prefer explicit `e_i=0` or low `e_reg` comparisons in future V5 layout generation; do not silently reuse old `e_reg=20` without labeling it.
5. Use Huber(`delta=30 mm`) as default tag solver loss for NLOS-positive tails.
6. Fix/document the C-core loss selector mismatch: Python exposes `linear`, but C validation resets it to Huber.

## Code Quality Issues

1. Anchor solver naming is not one-to-one: `V1`, `v1-old`, and `AutoPos V1` are not identical in all scripts.
2. V5 is spread across common-mode helper functions, generated artifacts, and analysis scripts rather than a single production runner.
3. Hard-coded constants remain in several places: V4 residual sigma `15 mm`, delay prior `20 mm`, bounds `[-60,+60]`, physical layer prior sigmas, T3/T4 temporal prior `180 mm`.
4. T4 rejection configuration fields exist but hard rejection is not active in the current C core.
5. Static range aggregation is outside the tag solver, so reports must record aggregation explicitly.

## Output Code Excerpts

The `code_excerpts/` folder contains compact read-only copies of the core solver algorithms:

- `v1_anchor_core.py`
- `v2_anchor_core.py`
- `v3_lite_anchor_core.py`
- `v3_full_anchor_core.py`
- `v4_anchor_core.py`
- `v5_anchor_core.py`
- `t1_tag_core.py`
- `t2_tag_core.py`
- `t3_tag_core.py`
- `t4_tag_core.py`
- `t4_v6_imu_gate_core.py`
