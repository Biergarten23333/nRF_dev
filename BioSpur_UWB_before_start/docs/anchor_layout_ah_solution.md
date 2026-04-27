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

## Calibrated v3 Soft Planes

This is the new runtime baseline.

How it differs from v2:
- `ABCD` are no longer treated as perfectly coplanar in the solver.
- The runtime frame still uses `A` as origin, `B` as x-axis, and `D` to define the `xy` plane.
- `C` is allowed to deviate slightly from the lower reference plane.
- `EFGH` are no longer forced to share one exact `z`; they are only encouraged to stay close to a common best-fit plane.
- The upper cluster still has a soft average-height prior near `1.4 m`.

Chosen solver settings:
- `distance_sigma_mm = 90`
- `height_prior_m = 1.4`
- `height_sigma_mm = 300`
- `vertical_sigma_mm = 900`
- `lower_plane_sigma_mm = 80`
- `upper_plane_sigma_mm = 160`

Result summary:
- Soft-plane constrained least-squares solve completed.
- RMS residual error: `160.35 mm`
- Upper-plane mean height: `1.563 m`
- Upper-plane z spread: `1.444 .. 1.773 m`
- Lower-plane `C` offset: `-0.035 m`

Recommended calibrated coordinates:

| Anchor | x (m) | y (m) | z (m) |
| --- | ---: | ---: | ---: |
| A | 0.000 | 0.000 | 0.000 |
| B | 3.765 | 0.000 | 0.000 |
| C | 3.846 | 3.714 | -0.035 |
| D | -0.036 | 4.033 | 0.000 |
| E | 0.100 | 0.391 | 1.773 |
| F | 3.777 | -0.060 | 1.478 |
| G | 4.008 | 3.829 | 1.444 |
| H | 0.078 | 3.986 | 1.558 |

Interpretation:
- This keeps the same global geometry and vertical pairing as before.
- It removes the unrealistic assumption that the upper layer is perfectly level.
- It also avoids forcing the lower layer to be mathematically perfect while preserving a stable runtime coordinate frame.
- For your physical installation, where anchors are approximately but not perfectly coplanar, this is the more realistic runtime model.

## Calibrated v4 Near-Level + Ref115

This is the current recommended runtime baseline.

How it differs from v3:
- The upper cluster is still not treated as mathematically exact, but it is now softly constrained to stay near one shared height in the runtime frame.
- The four vertical pairs `A-E`, `B-F`, `C-G`, `D-H` are softly encouraged to have similar height separation.
- A static floating reference session from Tag `115` is incorporated using only:
  - its measured `Tag -> Anchor` ranges
  - a soft prior that the tag height is near `700 mm`
- A bug in the floating-reference parameter unpacking was fixed before generating this result.

Chosen solver settings:
- `distance_sigma_mm = 90`
- `height_prior_m = 1.4`
- `height_sigma_mm = 300`
- `vertical_sigma_mm = 180`
- `lower_plane_sigma_mm = 40`
- `upper_plane_sigma_mm = 0`
- `upper_level_sigma_mm = 20`
- `pair_height_sigma_mm = 25`
- `reference_sigma_mm = 60`
- `floating_reference_z_prior_mm = 700`
- `floating_reference_z_sigma_mm = 80`

Result summary:
- Constrained least-squares solve completed.
- RMS residual error: `172.27 mm`
- Upper-plane mean height: `1.559 m`
- Upper-plane z spread: `1.558 .. 1.562 m`
- Solved floating reference: `(5.260, 0.322, 0.683) m`

Recommended calibrated coordinates:

| Anchor | x (m) | y (m) | z (m) |
| --- | ---: | ---: | ---: |
| A | 0.000 | 0.000 | 0.000 |
| B | 3.786 | 0.000 | 0.000 |
| C | 3.828 | 3.744 | 0.002 |
| D | -0.027 | 4.042 | 0.000 |
| E | 0.036 | 0.335 | 1.562 |
| F | 3.685 | -0.090 | 1.558 |
| G | 3.976 | 3.754 | 1.558 |
| H | 0.034 | 3.971 | 1.559 |

Interpretation:
- This matches the physical expectation much better than v3: the upper anchors are no longer split by hundreds of millimetres in `z`.
- The lower cluster remains effectively coplanar, but not by hard force.
- The result is still a model-driven estimate, not survey-grade truth, because only one static floating reference session is available.
- For the current fixed installation, this is the most realistic baseline in the repo.
