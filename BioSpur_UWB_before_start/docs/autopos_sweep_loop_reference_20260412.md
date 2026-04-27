# AUTOPOS Sweep Loop Reference

Timestamp: 2026-04-12 Europe/Berlin

## Scope

This document is the operational reference for:

- [run_autopos_sweep_loop.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/run_autopos_sweep_loop.py)

It covers:

1. `A-H` AUTOPOS sweep loop behavior
2. timeout behavior
3. live stdout verbosity
4. expected per-round control flow
5. powered-on Tag quieting behavior

## Primary Use

Run a full `A-H` anchor sweep:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 1 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_$(date +%Y%m%d_%H%M%S)
```

## Current Behavior

Per round, the script does:

1. open the 52840 CDC port
2. if `--quiet-tag-name` is enabled, switch to `RECV`, connect the Tag, force:
   - `MODE AOTA`
   - `STREAM OFF`
3. query `status`
4. keep the controller inside `AUTOPOS`
5. reassert `mode autopos`
6. wait until `autopos status` reports:
   - `AUTOPOS: mode=AUTOPOS state=idle`
7. set `device kind anchor`
8. load the `A-H` UUID map
9. stage `autopos round <master>`
10. send `autopos apply`
11. wait until:
   - `AUTOPOS apply success: master=<X>`
   - and `SW-<X>` count reaches `--sw-sets`

Important:

- The script no longer forces a per-round `AUTOPOS -> RECV -> AUTOPOS` reboot boundary.
- Sweep role rotation stays inside `AUTOPOS`.
- This makes round-to-round behavior cleaner and closer to the intended AUTOPOS flow.
- By default, the script assumes powered-on `BSF66F` must not influence sweep and actively quarantines it into `MODE AOTA` before each round. `STREAM OFF` is attempted as best-effort BLE log suppression only.

## Powered-On Tag Handling

Default behavior:

- `--quiet-tag-name BSF66F`

This is now enabled by default.

The script will:

1. enter `RECV`
2. target `BSF66F`
3. wait for the Tag link to become ready
4. send `cmd MODE AOTA`
5. verify `MODE_OK MODE=AOTA`
6. send `cmd STREAM OFF`
7. if supported, verify `STREAM_OK OFF`; if not supported, continue as long as `MODE AOTA` succeeded
8. return to `AUTOPOS`

Why:

- `STREAM OFF` only suppresses BLE runtime output
- it does not stop Tag-side UWB polls
- `MODE AOTA` is required if the Tag must stay online but not interfere with anchor sweep
- some deployed Tag builds may not implement `STREAM OFF`; that does not block sweep once `MODE AOTA` is active

To disable this behavior explicitly:

```bash
--quiet-tag-name -
```

## Timeout Behavior

`--timeout-s` is optional.

If omitted, the script auto-scales timeout from `--sw-sets`:

```text
timeout_s = max(480, 360 + 15 * sw_sets)
```

Examples:

- `--sw-sets 10` -> `510 s`
- `--sw-sets 100` -> `1860 s`

If you want to override it manually, pass:

```bash
--timeout-s <seconds>
```

Recommended:

- `10 sets`: default is usually enough
- `100 sets`: default is acceptable, but `2400` is a reasonable manual override if the BLE environment is unstable

## Verbosity Control

`--verbose` controls only live stdout.

Raw per-round logs are still fully written to:

- `round_<X>/master.log`

Levels:

- `--verbose 0`
  - only `SW-X`, success, failure, and key completion markers
- `--verbose 1`
  - normal operator mode
  - suppresses noisy ignored-anchor scan lines
- `--verbose 2`
  - full live flow

## Quality Handling

`--warmup-min-quality` is informational only.

- Low-quality early `SW-X` lines are still captured.
- They are still counted toward `--sw-sets`.
- They are annotated in `summary.json` under:
  - `warmup_sw_lines`
  - `warmup_sw_count`
  - `pairs_below_quality`
- Sweep success is controlled by:
  - `AUTOPOS apply success`
  - `AUTOPOS sweep listen attach`
  - enough `SW-X` lines to reach `--sw-sets`

It is not blocked by quality ramp-up.

Recommended default:

```bash
--verbose 1
```

## Examples

### Quiet Operator Mode

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --verbose 0 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_100sets_quiet_$(date +%Y%m%d_%H%M%S)
```

### Normal Sweep Operator Mode

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --verbose 1 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_100sets_normal_$(date +%Y%m%d_%H%M%S)
```

### Full Debug Flow

```bash
python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 2 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_full_$(date +%Y%m%d_%H%M%S)
```

## Notes On Scan Noise

The very noisy live line:

```text
[AUTOPOS] ANCHOR candidate ignored: ...
```

is no longer part of the intended operator-facing flow.

For normal sweep work:

- use current firmware
- use `--verbose 1`

That combination keeps live output readable while preserving the full raw log on disk.

## Vx Capture + Solve Pipeline (V1/V2/V3)

If you need an end-to-end, versioned run that includes:

- fresh Anchor sweep capture (`A-H`, `--sw-sets N`)
- then Anchors -> `responder`
- then Tag115 (`BSF66F`) `oneshot MCAL` and CM capture
- then run an offline solver chain (V1/V2/V3)

use:

- `scripts/run_autopos_vx_capture_and_solve.py`

Example (V2, 100 sets sweep + 100 CM lines):

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_vx_capture_and_solve.py \
  --version v2 \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --cm-lines 100 \
  --timeout-s 1800 \
  --out-dir autopos_V2/logs/v2_run_$(date +%Y%m%d_%H%M%S)
```

Output structure (inside `--out-dir`):

- `capture_*/sweep/`
  - per-round logs under `round_<X>/master.log`
  - `summary.json` (used by extractors)
- `capture_*/tag115_cm/run.log`
  - includes the `MODE AOTA` quarantine (best-effort `STREAM OFF`) and the `oneshot MCAL` + CM capture
- `solve_*_<vx>/`
  - extracted + fused files (`pairs_all.csv`, `ranges.csv`, etc.)
  - version-specific layout outputs (json/csv, depending on Vx)

Notes:

- Tag115 can stay powered-on during sweep, but it must be quarantined via `MODE AOTA` so it does not interfere with Anchor sweep.
- Tag builds may not implement `STREAM OFF`; this is non-fatal as long as `MODE AOTA` succeeded.
- For Tag CM capture, the script must ensure `ota_target name BSF66F` is in effect right before `oneshot MCAL` (otherwise firmware returns `rc=-128` and CM capture will be empty).

## Layout Quality Evaluation (V1–V5)

After you produce a layout (V1/V2/V3/...), you should verify it is **self-consistent**
with the measured inter-anchor distances, and optionally check that it can explain
Tag115 CM ranges as a floating reference.

### Why You Must Filter `dist=0` / `quality=0`

In practice, sweep may emit tuples like `peer,0,0`. This means "no valid ranging"
for that pair in that set. If you treat those as real distances, the solver/evaluator
will be corrupted (a 0-distance constraint is physically wrong).

Repo rule used by current tooling:

- `dist_mm <= 0` OR `quality_percent <= 0` is treated as **missing**, not data.

### Script: `autopos_eval_layout_quality.py`

Path: `scripts/autopos_eval_layout_quality.py`

What it does:

- Computes layout distance residuals vs measured distances:
  - `rms_err_mm`, `max_abs_err_mm`, `p50_abs_err_mm`, `p90_abs_err_mm`
  - worst pairs list (top 8)
- Optional: fits a floating Tag position using `ranges.csv` (Tag115 CM converted) and
  reports RMS/max residual in mm.

Inputs supported for `--distances`:

- `final_pair_distances*.csv` (fused output)
- `pairs_all.csv` (raw samples; evaluator averages per pair)
- `inter_anchor_matrix*.json`

Example (single layout):

```bash
python3 scripts/autopos_eval_layout_quality.py \
  --distances autopos_V1/logs/<run>/solve_*/v1/final_pair_distances.csv \
  --layout V1=autopos_V1/logs/<run>/solve_*/v1/anchor_coords_v1.json \
  --out-md autopos_V1/logs/<run>/v1_quality.md \
  --out-json autopos_V1/logs/<run>/v1_quality.json
```

Example (compare multiple layouts V1–V5):

```bash
python3 scripts/autopos_eval_layout_quality.py \
  --distances <some final_pair_distances.csv> \
  --layout V1=<path/to/v1_layout.json> \
  --layout V2=<path/to/v2_layout.json> \
  --layout V3=<path/to/v3_layout.json> \
  --layout V4=<path/to/v4_layout.json> \
  --layout V5=<path/to/v5_layout.json> \
  --out-md logs/layout_compare.md \
  --out-json logs/layout_compare.json
```

Optional: add Tag115 CM as floating reference (needs `ranges.csv`):

```bash
python3 scripts/autopos_eval_layout_quality.py \
  --distances <some final_pair_distances.csv> \
  --layout V2=<path/to/v2_layout.json> \
  --floating-ref-session <dir/with/ranges.csv> \
  --out-md logs/layout_compare.md
```
