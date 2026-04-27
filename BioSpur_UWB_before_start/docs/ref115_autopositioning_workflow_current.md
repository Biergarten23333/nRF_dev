# Ref115 Anchor Autopositioning (Current BLE OTA Workflow)

## Executive Summary

This workflow is for the **current system state** where:
- anchor topology is unchanged,
- only lower-layer anchors moved slightly in XY,
- all tags are BLE OTA-capable,
- `A` remains origin `(0,0,0)` in runtime frame.

It performs a **new solve** using current measurements. The previous layout is
used as a **weak structural prior**, not as strong coordinate truth.

The solver now uses:
- multi-start optimization,
- staged coarse-to-robust refinement,
- adaptive edge-class reweighting,
- soft topology regularization,
- post-solve acceptance checks.

It also applies a floating Ref115 height prior derived from floor heights:

- Anchor A floor height: `280 mm`
- Ref Tag 115 floor height: `1100 mm`
- Ref115 Z prior in A frame: `1100 - 280 = 820 mm`

Preparation-first guide (fresh matrix + OTA calibration profile for Ref115):
- [`docs/ref115_autopositioning_preparation_ble_ota.md`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/ref115_autopositioning_preparation_ble_ota.md)

## Current Behavior (Pipeline)

Entrypoint:
- `scripts/recalibrate_anchor_layout_with_ref115.py`

Solver chain:
1. `scripts/recalibrate_anchor_layout_with_ref115.py`
2. `scripts/solve_anchor_layout_iterative.py`
3. `scripts/solve_anchor_layout.py`

Outputs:
- `data/anchor_layout_ah_calibrated.json`
- `data/anchor_layout_ah_runtime.json`
- `src/uwb_anchor_layout.c`
- acceptance report per session:
  - `logs/tag_sessions/<session>/anchor_layout_acceptance.json`

## What Was Updated For Current System

1. **Previous-layout prior is weak by default**
- Large prior sigmas are used to preserve identity/topology without locking
  coordinates to old numeric values.

2. **Staged + multi-start optimization**
- Stage 1: coarse linear fit with weaker constraints.
- Stage 2: robust fit (`soft_l1`).
- Multi-start (`--multi-start`) avoids single local minimum bias.

3. **Adaptive edge-class weighting**
- Edge classes:
  - same-plane
  - cross-plane
  - vertical-pair
- Class sigmas are adaptively relaxed based on residual statistics.

4. **Ref115 height prior is grounded in physical installation**
- Default `ref_z_prior_mm` is auto-derived from floor heights:
  - `ref115_floor_height_mm - anchor_a_floor_height_mm`
- Defaults: `1100 - 280 = 820 mm`.

5. **Acceptance gates before replacing runtime layout**
- Maximum lower/upper shifts,
- maximum lower lateral shift,
- minimum upper-lower separation,
- maximum global RMS fit error.
- If gates fail, runtime layout is not replaced unless `--force-accept`.

6. **Confidence/health outputs**
- `distance_residual_by_class`
- anchor uncertainty estimate from local Jacobian (`uncertainty_mm`)
- composite score block in solution JSON (`score`)

7. **Safer default post behavior**
- `--post-mode` default is now `none` (no automatic rebuild/reflash after solve).

## Data Capture (What to capture)

Required:
- A Ref115 static session directory containing:
  - `ranges.csv`
  - (optional) `summary.json` for initial floating-reference guess

Recommended capture path:
- Use `capture_mode=calibration` for Ref115 if new session is needed.
- Existing valid session can be reused via `--session-dir`.

Note:
- Current master BLE TS logs are useful for runtime monitoring but do not replace
  Ref115 `ranges.csv` for anchor-layout solve.

## Run Commands

### A) Reuse existing Ref115 session (recommended first)

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --session-dir logs/tag_sessions/<your_ref115_session> \
  --skip-build --skip-flash
```

### B) Capture a fresh Ref115 session + solve

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --capture-mode calibration \
  --post-mode none
```

### C) Higher-robustness solve settings (recommended for moved lower anchors)

```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --session-dir logs/tag_sessions/<your_ref115_session> \
  --skip-build --skip-flash \
  --multi-start 12 \
  --start-jitter-mm 600 \
  --adaptive-edge-reweight-rounds 3
```

## Key Inputs and How They Are Used

- `A` origin `(0,0,0)`:
  - fixed in solver parameterization.
- Ref115 static reference:
  - used as floating reference with range residuals from `ranges.csv`.
- Ref115 height `1100 mm` and A floor `280 mm`:
  - default floating Z prior = `820 mm`.
- Previous layout:
  - used as weak prior/identity guide, not as hard truth.

## Accuracy/Robustness Improvements

- New-solve behavior after anchor movement (not just local nudging).
- Better local-minimum avoidance via multi-start.
- Better noise handling via robust loss and adaptive edge-class reweighting.
- Topology-preserving but weak coordinate prior.
- Acceptance gating before layout replacement.

## Acceptance Criteria Before Replacing Runtime Layout

From `anchor_layout_acceptance.json`:

- `max_lower_shift_mm <= accept_max_lower_shift_mm`
- `max_upper_shift_mm <= accept_max_upper_shift_mm`
- `max_lower_lateral_shift_mm <= accept_max_lower_lateral_shift_mm`
- `upper_lower_separation_mm >= accept_min_upper_lower_separation_mm`
- `rms_error_mm <= accept_max_rms_error_mm`

If any check fails:
- do **not** replace runtime layout,
- inspect session quality / constraints / priors,
- rerun with corrected data or parameters.

Also inspect in calibrated solution:
- `score`
- `distance_residual_by_class`
- `uncertainty_mm`

## Relevant Source Locations

- `scripts/recalibrate_anchor_layout_with_ref115.py`
- `scripts/solve_anchor_layout_iterative.py`
- `scripts/solve_anchor_layout.py`
- `data/anchor_layout_ah_calibrated.json`
- `data/anchor_layout_ah_runtime.json`
- `src/uwb_anchor_layout.c`
