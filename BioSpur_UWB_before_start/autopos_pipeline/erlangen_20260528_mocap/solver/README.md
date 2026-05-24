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
solver/outputs/v4io_field_check/v4-io/layout_us_height.json
solver/outputs/v4io_field_check/tables/version_summary.csv
solver/outputs/v4io_field_check/v4-io/static_all_captures.csv
solver/outputs/v4io_field_check/tables/roto_physical_consistency_summary.csv
solver/outputs/v4io_field_check/v4-io/wand_static_summary.csv
```

## Run V1 To V4-io Progression

Use this when you want the field comparison across solver generations, not only
the current V4-io result:

```bash
python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v1_to_v4_io.py
```

This runs:

```text
v1-old   -> V1
v2       -> V2
v3-lite  -> V3-lite
v3-full  -> V3-full
v4-io    -> V4-io
```

Main output:

```text
solver/outputs/v1_to_v4_io_field_check/FIELD_V1_TO_V4_IO.md
```

Useful files:

```text
solver/outputs/v1_to_v4_io_field_check/tables/version_summary.csv
solver/outputs/v1_to_v4_io_field_check/tables/autopos_quality_summary.csv
solver/outputs/v1_to_v4_io_field_check/tables/delay_sanity.csv
solver/outputs/v1_to_v4_io_field_check/v1-old/layout.json
solver/outputs/v1_to_v4_io_field_check/v1-old/layout_us_height.json
solver/outputs/v1_to_v4_io_field_check/v2/layout.json
solver/outputs/v1_to_v4_io_field_check/v2/layout_us_height.json
solver/outputs/v1_to_v4_io_field_check/v3-lite/layout.json
solver/outputs/v1_to_v4_io_field_check/v3-lite/layout_us_height.json
solver/outputs/v1_to_v4_io_field_check/v3-full/layout.json
solver/outputs/v1_to_v4_io_field_check/v3-full/layout_us_height.json
solver/outputs/v1_to_v4_io_field_check/v4-io/layout.json
solver/outputs/v1_to_v4_io_field_check/v4-io/layout_us_height.json
```

`layout.json` is the raw AutoPos gauge frame. `layout_us_height.json` is the
field-friendly physical-height frame. The post-process rigidly aligns the raw
layout to the latest F/G/H ultrasound antenna-center heights, while enforcing
the hard z convention `mean_z(ABCD) < mean_z(EFGH)`. It also writes US-Z RMS,
max residual, and per-anchor residuals into `extra.ultrasound_height_alignment`.

## Offline Field Command

Use this exact block on site when there is no Codex and no internet:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 - <<'PY'
import scipy, numpy, pandas, matplotlib
print("solver python dependencies: OK")
PY

python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/stage_field_dataset.py \
  --session erlangen_20260528_optitrack

python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v4io_field_check.py

python3 autopos_pipeline/erlangen_20260528_mocap/solver/scripts/run_v1_to_v4_io.py

echo
echo "Quick report:"
cat autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check/FIELD_V4IO_CHECK.md

echo
echo "V1 to V4-io report:"
cat autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v1_to_v4_io_field_check/FIELD_V1_TO_V4_IO.md

echo
echo "Anchor layout:"
cat autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check/v4-io/layout.json

echo
echo "Anchor layout, ultrasound height-aligned:"
cat autopos_pipeline/erlangen_20260528_mocap/solver/outputs/v4io_field_check/v4-io/layout_us_height.json
```

The staging step now accepts the field sweep format directly. If a sweep folder
does not already contain `pairs_all.csv`, it automatically finds the newest
complete `sweep*/sweep1000/summary.json` and derives:

```text
solver/work/field_dataset_staged/sweep1000/pairs_all.csv
```

Do not use interrupted sweep folders for final judgement. A complete sweep has
all `A,B,C,D,E,F,G,H` rounds marked successful in `summary.json`.

## Offline Dependency Note

Do this before going to the lab, while internet is still available:

```bash
python3 -m pip install --user --break-system-packages scipy
```

If this dependency check passes, the solver run itself does not need internet:

```bash
python3 - <<'PY'
import scipy, numpy, pandas, matplotlib
print("solver python dependencies: OK")
PY
```

## What Is Good Enough On Site

Do not over-optimize on site. The quick check is enough if:

- V4-io layout solves without crashing.
- Inter-anchor residuals are physically reasonable.
- At least one static capture appears in `static_all_captures.csv`.
- Roto/Wand tables appear if those captures were already collected.
- No obvious missing-anchor or bad-folder mistake is visible.

Final OptiTrack alignment and publication analysis can happen later.
