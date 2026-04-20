# Soft-Constraint Sensitivity (From Existing 100-set Sweep)

This note answers: "Why does the solved layout look almost coplanar, even though anchors are hand-placed?"

## Key Point: What Is "Coplanar" Here?

The solver chooses a **gauge** (coordinate frame) by fixing `A,B,D` on `z=0`. Any **three points always define a plane**, so this does **not** claim that the real-world installation is level. It is simply the coordinate system the solver uses.

The *actual* "lower-plane non-coplanarity" that the current solver can express is mainly **`C_z`** (deviation of `C` from plane `ABD`), controlled by `--lower-plane-sigma-mm`.

For the upper plane, the solver applies two separate soft priors:

1. **Upper plane prior** (`--upper-plane-sigma-mm`): upper anchors should lie near a common best-fit plane.
2. **Upper level prior** (`--upper-level-sigma-mm`): upper anchors' Z values should be close to each other (mean-centered).

By default, `--upper-level-sigma-mm=35` is very strong (forces only a few mm of spread).

## Experiment Setup

Input distances:
- `../v1/inter_anchor_matrix_v1.json`

Initial layout prior:
- `../v1/anchor_layout_v1_soft_iterative.json`

We solved 3 times using `scripts/solve_anchor_layout.py`, varying only the soft-constraint sigmas.

## Results (Derived From Output JSON)

All metrics below are read from the produced layout JSON (`rms_error_mm`, `anchors`, `vertical_pairs`).

| Variant | rms_error_mm | upper_z_spread (m) | C_z (m) | vertical_pair_dz_spread (m) | Notes |
|---|---:|---:|---:|---:|---|
| baseline | 51.89 | 0.006 | -0.008 | 0.012 | Nearly levelled upper plane (prior dominates). |
| relaxed2 | 44.54 | 0.051 | -0.042 | 0.077 | Moderate relaxation: upper plane can tilt/bow ~5 cm. |
| relaxed1 | 34.33 | 0.129 | -0.094 | 0.175 | Strong relaxation: upper z spread ~13 cm; best fit to the distance matrix in this sweep. |

## Optional Cuboid/Rectangle Shape Priors (New)

`scripts/solve_anchor_layout.py` now supports additional *optional* structural priors
for the common “stacked rectangle / cuboid” assumption:

- Lower quad closure: `A + C ≈ B + D`
- Upper quad closure: `E + G ≈ F + H`
- Vertical translation consistency: `(E-A),(F-B),(G-C),(H-D)` should be similar (does NOT force exact XY match)
- Rectangle diagonal equality: `AC ≈ BD` and `EG ≈ FH`
- Space diagonal equality: `A-G, B-H, C-E, D-F` should be similar
- Orthogonality (unitless cos prior): `AB ⟂ AD`, `EF ⟂ EH`

These are OFF by default (sigma=0). If enabled too aggressively (sigma too small),
they can fight the measured distance matrix and worsen fit RMS. As a rule of thumb,
start with *large* sigmas (hundreds of mm) so they remain true soft priors.

### Sigmas Used

Baseline:
- `lower_plane_sigma_mm=80`
- `upper_plane_sigma_mm=160`
- `upper_level_sigma_mm=35`
- `pair_height_sigma_mm=45`
- `height_sigma_mm=300`

Relaxed2:
- `lower_plane_sigma_mm=200`
- `upper_plane_sigma_mm=300`
- `upper_level_sigma_mm=120`
- `pair_height_sigma_mm=120`
- `height_sigma_mm=500`

Relaxed1:
- `lower_plane_sigma_mm=500`
- `upper_plane_sigma_mm=500`
- `upper_level_sigma_mm=250`
- `pair_height_sigma_mm=180`
- `height_sigma_mm=800`

## Interpretation

- If you believe the upper anchors are **not** close to the same height, the current default `upper_level_sigma_mm=35` is too tight and will "flatten" the solution.
- In this dataset, relaxing the priors **reduced** the fit RMS (`51.9mm -> 34.3mm`), meaning the flattened solution is not even the best explanation of the inter-anchor ranges.
- However, without an external gravity reference (or a ground-truth point), the absolute meaning of "level" is limited: the solver's Z axis is defined by its constraints.

## Files Generated

- `layout_baseline.json` (+ `baseline.stdout.txt`)
- `layout_relaxed2.json` (+ `relaxed2.stdout.txt`)
- `layout_relaxed1.json` (+ `relaxed1.stdout.txt`)
