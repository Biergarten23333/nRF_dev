# Ref115 Autopositioning Preparation (BLE OTA Era)

## Why this preparation is required now

After lower-anchor physical movement:
- the old inter-anchor matrix is stale,
- current runtime `TagSummary xyz` from BLE tracking is **not** geometric truth for calibration,
- Ref115 must be put into a dedicated calibration profile again before fresh solve.

This document defines the practical preparation workflow before running a new
autopositioning solve/deploy cycle.

## Preconditions and physical references

- Anchor `A` is frame origin `(0,0,0)`.
- Anchor `A` floor height ≈ `280 mm`.
- Ref Tag `115` floor height ≈ `1100 mm`.
- Therefore Ref115 Z prior in A-frame is `820 mm` (soft prior only).

## Gap that existed before

Repo had generic OTA build scripts, but no dedicated **Ref115 calibration OTA
profile** entrypoint for the current BLE workflow.

Added:
- [`scripts/build_ref115_calibration_ota_profile.sh`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/build_ref115_calibration_ota_profile.sh)

## Fresh matrix workflow (new)

Because lower anchors moved, build a fresh matrix from fresh pair samples.

Added:
- [`scripts/generate_inter_anchor_matrix.py`](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/generate_inter_anchor_matrix.py)

Input CSV schema (`--pairs-csv`):
- `a,b,dist_mm[,quality]`
- One row per pairwise sample.

Output:
- `data/inter_anchor_matrix_ah.json` (updated)
- includes `pair_stats` and source notes (fresh/fallback/missing).

## End-to-end operator workflow

1. **Collect fresh inter-anchor pair samples**
- Collect new pairwise distance samples after lower-anchor movement.
- Export CSV as:
  - `logs/anchor_matrix/<session>/pairs.csv`

2. **Generate fresh inter-anchor matrix**
```bash
python3 scripts/generate_inter_anchor_matrix.py \
  --pairs-csv logs/anchor_matrix/<session>/pairs.csv \
  --existing-matrix data/inter_anchor_matrix_ah.json \
  --output data/inter_anchor_matrix_ah.json \
  --min-samples-per-pair 8 \
  --max-mad-mm 180
```

3. **Build Ref115 calibration OTA package**
```bash
./scripts/build_ref115_calibration_ota_profile.sh \
  build-tag-ota-ref115-calibration \
  build-master-ota-ref115-calibration
```

4. **Switch nRF52840 to OTA mode**
- Flash master OTA image:
  - `build-master-ota-ref115-calibration/zephyr/zephyr.hex`
- Use your non-interactive master flash path:
  - `scripts/flash_master_noninteractive.sh <hex>`

5. **OTA Ref115 into calibration profile**
- Run OTA session targeting token for Ref115 (`111` in current mapping).
- Verify upload complete + reset + reconnect.

6. **Switch nRF52840 back to receiver mode**
- Build/flash receiver image (apps/master):
```bash
west build -b nrf52840dk/nrf52840 -s apps/master -d build-master-receiver --no-sysbuild --pristine=always
scripts/flash_master_noninteractive.sh build-master-receiver/zephyr/zephyr.hex
```

7. **Capture fresh Ref115 calibration session**
- Capture static Ref115 range session and generate `ranges.csv`:
  - `logs/tag_sessions/<new_ref115_session>/ranges.csv`

8. **Run host-side solve with confidence/acceptance**
```bash
python3 scripts/recalibrate_anchor_layout_with_ref115.py \
  --session-dir logs/tag_sessions/<new_ref115_session> \
  --input data/inter_anchor_matrix_ah.json \
  --skip-build --skip-flash \
  --anchor-a-floor-height-mm 280 \
  --ref115-floor-height-mm 1100 \
  --multi-start 12 \
  --start-jitter-mm 600 \
  --adaptive-edge-reweight-rounds 3
```

9. **Check acceptance before deployment**
- Inspect:
  - `logs/tag_sessions/<new_ref115_session>/anchor_layout_acceptance.json`
  - `data/anchor_layout_ah_calibrated.json` fields:
    - `score`
    - `distance_residual_by_class`
    - `uncertainty_mm`
- Deploy runtime layout only if acceptance passes.

## Deployment rule

Only replace runtime anchor layout when:
- fresh matrix exists,
- fresh Ref115 calibration session exists,
- acceptance gate passed.

Do **not** deploy from stale matrix or from runtime BLE tracking xyz alone.
