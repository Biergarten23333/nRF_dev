# Anchor Matrix Workflow (Current, Verified)

## Date
- 2026-03-27

## Status
- **SUCCESS**
- Latest full matrix session:
  - `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_matrix/matrix_full_20260327_145327_with115`
- Result:
  - `total_rows=556`
  - `unique_pairs=28`
  - `missing_pairs=0`

Source:
- `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_summary.txt`

---

## What Was Verified

1. Full A→H initiator rotation executed.
2. Initiator family used: `master-full` for all rounds.
3. Non-initiators used: `matrix`.
4. Output produced complete unordered pair set (28/28).
5. End-of-run restore executed: all anchors flashed back to `tag` baseline.

---

## Current Practical Steps

### Step 1: Ensure required build outputs exist
Required per anchor `A..H`:
- `build-anchor-<X>-matrix/zephyr/zephyr.hex`
- `build-anchor-<X>-master-full/zephyr/zephyr.hex`
- `build-anchor-<X>-tag/zephyr/zephyr.hex`

### Step 2: Prepare flash plan (SNR + serial mapping)
Use:
- `logs/anchor_matrix/matrix_full_20260327_143457/flash_plan.tsv`

Format:
- `anchor_id<TAB>snr<TAB>serial_port`

### Step 3: Matrix baseline
Flash all anchors `A..H` to `matrix`.

### Step 4: Rotation capture
For master in `A,B,C,D,E,F,G,H`:
1. Flash current master to `master-full`.
2. Keep others in `matrix`.
3. Capture master runtime serial window.
4. Extract `Matrix X-Y ... filt=<mm>` rows into `pairs_master_<X>.csv`.

### Step 5: Merge pairs
Merge all `pairs_master_*.csv` into `pairs_all.csv`, then compute:
- unique unordered pair count
- missing pairs list

Expected success criterion:
- `unique_pairs=28`
- `missing_pairs=0`

### Step 6: Restore runtime responder baseline
Flash all anchors back to `tag` after matrix run.

---

## Current Config Snapshot (Matrix Run, 2026-03-27)

Session:
- `logs/anchor_matrix/matrix_full_20260327_145327_with115`

Anchor mapping (`flash_plan.tsv`):
- `A -> SNR 760186071`
- `B -> SNR 760185876`
- `C -> SNR 760185878`
- `D -> SNR 760186081`
- `E -> SNR 760185904`
- `F -> SNR 760186124`
- `G -> SNR 760185889`
- `H -> SNR 760186121`

Matrix role policy used:
- Round baseline: all `A..H -> matrix`
- Active round master: current anchor `-> master-full`
- Round responders: all non-master anchors `-> matrix`
- End-of-flow restore: all `A..H -> tag`

Run evidence:
- `run.log` includes:
  - `baseline: all anchors -> matrix`
  - `round master=A ... H`
  - `restore all anchors -> tag`
  - `matrix done`

---

## Ref115 (BSF66F) Calibration Mode Status (Checked 2026-03-27)

Current state: **Calibration profile is active**.

Evidence:
- `logs/ota_round_20260327/tag115_boot_after_bootstrap.log`
  - `Tag BLE calibration mode: skip settings_load/runtime cfg restore`
  - `Tag calibration mode: runtime/master TDMA overrides disabled`
  - `Tag firmware marker: ref115-calibration-ota`
- `logs/ota_round_20260327/tag115_ota_after_bootstrap_tag_20260327_110015.log`
  - same calibration markers as above

Live no-reset capture check:
- `logs/tag_sessions/check115_mode_20260327_150334/raw.log`
- Behavior observed:
  - calibration-style verbose ranging stream
  - currently only anchor A effective, anchors B..H timeout (capture-quality issue, not mode mismatch)

---

## Evidence Files (Latest Successful Run)

- Run log:
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/run.log`
- Pair summary:
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_summary.txt`
- Merged pairs:
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_all.csv`
- Per-round pairs:
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_A.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_B.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_C.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_D.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_E.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_F.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_G.csv`
  - `logs/anchor_matrix/matrix_full_20260327_145327_with115/pairs_master_H.csv`

---

## Operator Notes

- For matrix stage, use:
  - `master-full` for initiator
  - `matrix` for responders
- Do **not** leave anchors in `master-full` or `matrix` after matrix work.
- Always restore to `tag` baseline at end of matrix session.
