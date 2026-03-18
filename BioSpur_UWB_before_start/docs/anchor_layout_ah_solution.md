# A-H Anchor Layout Solution

Date: 2026-03-18

Source inputs:
- Distance matrix: `data/inter_anchor_matrix_ah.json`
- Solver: `scripts/solve_anchor_layout.py`

## First Pass

Result summary:
- Unconstrained nonlinear least-squares solve completed.
- RMS residual error: `135.49 mm`

Solved relative coordinates:

| Anchor | x (m) | y (m) | z (m) |
| --- | ---: | ---: | ---: |
| A | 0.000 | 0.000 | 0.000 |
| B | 3.901 | 0.000 | 0.000 |
| C | 3.780 | 3.651 | -0.047 |
| D | -0.063 | 4.005 | 0.000 |
| E | -0.057 | 0.434 | 1.894 |
| F | 3.670 | -0.096 | 1.535 |
| G | 3.973 | 3.801 | 1.468 |
| H | 0.057 | 3.983 | 1.534 |

Interpretation:
- The solver recovered the expected two-layer structure:
  - `A/B/C/D` stay close to `z ~= 0`
  - `E/F/G/H` stay at positive `z`
- `C` has a small negative `z` offset (`-47 mm`), which is acceptable for a first pass and indicates measurement noise / geometry mismatch rather than a topology failure.
- `E` came out higher than the other upper-plane anchors, which suggests either:
  - the `A-E` vertical link is particularly strong and pulling that corner upward, or
  - the current distance matrix still contains enough bias that a constrained second-pass solve would help.

Recommended next solver improvements:
- Add weighting by link quality / stability.
- Add a soft prior that `ABCD` lie on one plane and `EFGH` lie on another.
- Add a soft prior that the two planes are separated by about `1.4 m`.
- Re-measure noisy pairs, especially `F-H`.

## Calibrated v2

This is the current recommended anchor layout for runtime use.

How it differs from the first pass:
- `ABCD` are constrained to a shared lower plane.
- `EFGH` are constrained to a shared upper plane.
- The upper plane has a soft height prior near `1.4 m`.
- Vertical pairs `A-E`, `B-F`, `C-G`, `D-H` are softly encouraged to stay aligned in XY.

Chosen solver settings:
- `distance_sigma_mm = 90`
- `height_prior_m = 1.4`
- `height_sigma_mm = 300`
- `vertical_sigma_mm = 900`

Result summary:
- Constrained least-squares solve completed.
- RMS residual error: `176.73 mm`
- Shared upper-plane height: `1.552 m`

Recommended calibrated coordinates:

| Anchor | x (m) | y (m) | z (m) |
| --- | ---: | ---: | ---: |
| A | 0.000 | 0.000 | 0.000 |
| B | 3.768 | 0.000 | 0.000 |
| C | 3.880 | 3.705 | 0.000 |
| D | 0.001 | 4.064 | 0.000 |
| E | 0.060 | 0.363 | 1.552 |
| F | 3.751 | -0.073 | 1.552 |
| G | 3.998 | 3.797 | 1.552 |
| H | 0.075 | 3.981 | 1.552 |

Vertical-pair sanity check:

| Pair | dx (m) | dy (m) | dz (m) | XY offset (m) |
| --- | ---: | ---: | ---: | ---: |
| A-E | 0.060 | 0.363 | 1.552 | 0.367 |
| B-F | -0.017 | -0.073 | 1.552 | 0.075 |
| C-G | 0.118 | 0.093 | 1.552 | 0.150 |
| D-H | 0.074 | -0.083 | 1.552 | 0.112 |

Interpretation:
- The constrained solve gives a cleaner long-term anchor geometry than the first pass.
- Residual error is higher than the unconstrained fit because the solver now prefers a physically plausible two-plane structure over a looser best fit.
- For your use case, where anchors will remain fixed for a long time, this is the better baseline to carry into tag localization.

Runtime artifacts:
- Human-readable summary: this file
- Full machine-readable result: `data/anchor_layout_ah_calibrated.json`
- Runtime-friendly mm config: `data/anchor_layout_ah_runtime.json`
