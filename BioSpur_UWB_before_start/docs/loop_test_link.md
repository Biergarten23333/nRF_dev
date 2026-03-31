# Loop Test Link (Master->Peer)

## Purpose
`scripts/loop_test_link.py` automates repeated single-link stability testing for matrix logs:
- set one anchor as `master`
- set others as `matrix`
- capture sweeps
- extract one target link (`master` -> `peer`)
- score parameter combinations
- run one confirm round for the best combo

## Anchor Probe Safety Policy (hard rule)
- Anchor tests are **anchor-only probe path**:
  - allowed probe serials: `7xxxxxx` only
  - forbidden: any `6xxxxxx` (including `683234364`, nRF52840 DK)
- Probe-map load preflight enforces this rule and aborts immediately on violation.
- Runtime command dispatch also re-checks every target probe/port before each command.
- `flash_master_noninteractive.sh` path is forbidden in anchor loop tests.

Troubleshooting:
- VSCode Nordic extension background hotplug may still trigger popup attempts independently.
- This script will hard-fail before using forbidden probes; if popup still appears, stop other external tooling and rerun only this script.

Default target is `A -> H`.

## Example (hardware)
```bash
python3 scripts/loop_test_link.py \
  --master A --peer H \
  --method provision \
  --probe-map mapping.csv \
  --rounds 3 \
  --sweeps-per-round 3 \
  --min-samples 50 \
  --params tdma_slot_period_ms=10,20,30 tdma_slot_active_ms=6,9 \
  --out-dir logs/loop_test_A_H_$(date +%Y%m%d_%H%M%S)
```

## Example (dry-run on existing raw log)
```bash
python3 scripts/loop_test_link.py \
  --master A --peer H \
  --dry-run \
  --dry-run-source logs/real_positioning_rotate_20260330_161846/matrix/anchor_A_master_runtime.log \
  --rounds 2 \
  --sweep-grid 1,2,3,4,5,6,7,8 \
  --min-samples 5 \
  --out-dir logs/loop_test_A_H_dryrun_$(date +%Y%m%d_%H%M%S)
```

## Parameter Grid
- `--params` is a list of dimensions:
  - `k=v1,v2,v3`
  - script builds Cartesian product
- optional `--sweep-grid` adds sweep-count as an optimization dimension

## Outputs
For each parameter combo and round:
- `<out-dir>/params/<param_key>/round_<i>/raw.log`
- `<out-dir>/params/<param_key>/round_<i>/pairs_master_<M>.csv`
- `<out-dir>/params/<param_key>/round_<i>/metrics.json`

Global results:
- `<out-dir>/grid_results.json`
- `<out-dir>/best_param_confirm.json`
- `<out-dir>/ranking_plot.png` (if matplotlib exists)
- `<out-dir>/sweep_stability_plot.png` (if sweep-grid used and matplotlib exists)

## Verdict Rules
- PASS:
  - `count >= min_samples`
  - `pstdev_mm <= pass_pstdev_mm` (default `10`)
  - `quality_median >= pass_quality_median` (default `70`)
- WARN:
  - PASS conditions met but `ci95_mm > warn_ci95_mm` (default `2.0`)
- FAIL:
  - otherwise
