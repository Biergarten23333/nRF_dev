# Erlangen Field Solver

This folder is for on-site sanity checking, not final paper analysis.

Goal: after at least one AutoPos sweep and a few Tag captures, run a quick V4-io
check to confirm that the dataset is usable before leaving Erlangen.

## Expected Input

Run capture commands first:

```bash
bio_setup erlangen_20260528_optitrack
sweep -id SW01
us30 -id US01
static -id ID13
roto -id R01
wand -id W01
```

The capture session should be under:

```text
autopos_pipeline/erlangen_20260528_mocap/captures/erlangen_20260528_optitrack
```

## Stage the Dataset

From repo root:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/stage_field_dataset.py \
  --session erlangen_20260528_optitrack
```

This creates:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/work/field_dataset_staged
```

The staging script converts the field folder names into the older solver shape:

```text
static_ID13_... -> Static_Test/ID13_.../tr_all.csv
roto_R01_...   -> Roto_Test/ID25_.../tr_all.csv
wand3_W01_...  -> Wand_Test/W01_.../tr_all.csv
```

## Run Minimal V4-io Check

```bash
python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py
```

Main output:

```text
autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check/FIELD_V4IO_CHECK.md
```

Also useful:

```text
solver/outputs/v4io_field_check/v4-io/layout.json
solver/outputs/v4io_field_check/tables/version_summary.csv
solver/outputs/v4io_field_check/v4-io/static_all_captures.csv
solver/outputs/v4io_field_check/tables/roto_physical_consistency_summary.csv
solver/outputs/v4io_field_check/v4-io/wand_static_summary.csv
```

## What Is Good Enough On Site

Do not over-optimize on site. The quick check is enough if:

- V4-io layout solves without crashing.
- Inter-anchor residuals are physically reasonable.
- At least one static capture appears in `static_all_captures.csv`.
- Roto/Wand tables appear if those captures were already collected.
- No obvious missing-anchor or bad-folder mistake is visible.

Final OptiTrack alignment and publication analysis can happen later.
