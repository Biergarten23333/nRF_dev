# Ground-Truth Workflow

Use this workflow to validate absolute Tag accuracy after anchor self-calibration is fixed.

## Goal

Measure how far the solved Tag position is from a real, known point in the runtime anchor coordinate frame.

## Recommended Minimum Point Set

At least `6` static points:

1. center, mid height
2. left side, mid height
3. right side, mid height
4. center, low height
5. center, higher height
6. one corner-like position

If you care most about body-motion tracking, later add:

- several walking-path points
- several arm/torso-height points
- points near likely NLOS regions

## Point Definition

Start from:

- `data/ground_truth_points_template.json`
- `data/ground_truth_points_suggested.json`
- `docs/ground_truth_easy_points.md`

Fill each point with real measured `x/y/z` coordinates in the same frame as:

- `data/anchor_layout_ah_runtime.json`

If you do not want to invent points manually, use the suggested points directly.

## Capture One Point

Example:

```bash
python3 scripts/run_ground_truth_point.py \
  760186127 \
  /dev/serial/by-id/usb-SEGGER_J-Link_000760186127-if00 \
  --label P1_center_mid \
  --truth-x 1730 \
  --truth-y 3220 \
  --truth-z 800 \
  --duration 180 \
  --skip-sweeps 2
```

This will:

1. reset the Tag
2. capture a session
3. write `summary.json`, `positions.csv`, `ranges.csv`
4. compute absolute position error
5. write `ground_truth.json`

## Analyze One Point

You can also analyze an existing session directly:

```bash
python3 scripts/analyze_ground_truth_session.py \
  logs/ground_truth/gt_P1_center_mid_YYYYMMDD_HHMMSS \
  --label P1_center_mid \
  --truth-x 1730 \
  --truth-y 3220 \
  --truth-z 800
```

## Analyze A Batch

```bash
python3 scripts/analyze_ground_truth_batch.py --root logs/ground_truth
```

## What To Optimize Next

If ground-truth error is still too large, optimize in this order:

1. antenna delays
2. anchor layout fine adjustment
3. anchor subset scoring
4. dynamic sweep rate / multi-tag scheduler

Do not pull BLE control-plane work back in before this is understood.
