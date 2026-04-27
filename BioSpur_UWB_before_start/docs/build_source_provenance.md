# Build Source Provenance

Every generated build directory should have a sibling provenance file with the same stem:

- `build-tag-ref115-reautopos/` -> `build-tag-ref115-reautopos.source`

The provenance file is JSON and records:

- the build directory name
- the source script that generated it
- the invocation summary
- the generation time

This repo now writes the provenance file automatically from the main build entrypoints, including:

- `scripts/build_tag_usb.sh`
- `scripts/build_tag_adaptive.sh`
- `scripts/build_tag_ble_motion.sh`
- `scripts/build_tag_tdma.sh`
- `scripts/build_tag_multitag.sh`
- `scripts/build_ref115_monitor_4.sh`
- `scripts/build_ref115_monitor_4_fast.sh`
- `scripts/build_ref115_fast.sh`
- `scripts/build_ble_ota_test.sh`
- `scripts/build_uwb_tag_ota_test.sh`
- `scripts/recalibrate_anchor_layout_with_ref115.py`
- `scripts/evaluate_ref115_fixed_subset_live.py`

For older build directories that already existed, the provenance file can be backfilled with:

```bash
python3 scripts/write_build_source.py --build-dir <build-dir> --source <script> --command '<command>'
```
