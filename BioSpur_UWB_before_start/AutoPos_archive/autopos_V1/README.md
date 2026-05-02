#!/usr/bin/env markdown
# autopos_V1 (Capture + Solve)

This folder is a runnable bundle for **AutoPos V1** experiments:
- Capture fresh **Anchor sweep** (`A-H`, default 100 sets)
- Capture fresh **Tag115 CM** (default 100 aggregated CM notify lines)
- Run **V1** offline processing (bidirectional fusion v1 + classical MDS)

The source of truth is still the repo-level `scripts/` folder; this is a convenience entry point.

## Run

```bash
export PORT=/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXXXXXX-if00
bash autopos_V1/scripts/run_v1_capture_and_solve.sh
```

Optional knobs:
- `SW_SETS=100` (sweep count)
- `CM_LINES=100` (CM lines)
- `TAG_NAME=BSF66F`
- `ORDER=ABCDEFGH`
- `TIMEOUT_S=1800`
- `OUT_DIR=autopos_V1/logs/v1_run_YYYYmmdd_HHMMSS`

## Outputs
- `${OUT_DIR}/capture_*/sweep/summary.json`
- `${OUT_DIR}/capture_*/tag115_cm/run.log` (contains CM lines)
- `${OUT_DIR}/solve_*/pairs_all.csv`
- `${OUT_DIR}/solve_*/floating_ref115_from_cm/ranges.csv`
- `${OUT_DIR}/solve_*/v1/anchor_coords_v1.json`


This folder is a runnable bundle for AUTOPOS anchor sweep + Tag115 CM capture.

## Dependencies

- Python 3
- `pyserial` (`pip3 install -r requirements.txt`)

## Ports

- Master-control CDC example:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00`

## Full Flow: Sweep (A-H) + Tag115 CM

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3

python3 -u scripts/run_autopos_sweep_then_tag_cm_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 100 \
  --timeout-s 1860 \
  --target-name BSF66F \
  --quiet-tag-name BSF66F \
  --cm-wait-s 300 \
  --cm-capture-s 900 \
  --min-cm-lines 100 \
  --loops 1 \
  --out-dir logs/sweep100_then_tag115_cm100_$(date +%Y%m%d_%H%M%S)
```

Notes:
- `--quiet-tag-name BSF66F` keeps Tag115 powered-on but quarantines it into `MODE AOTA` before each sweep round, so it does not influence the sweep.
- Tag capture success is based on `CM` line count (not on `MODE_OK` markers).

## Post-Sweep Only: Anchor -> Responder + Tag115 CM

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_V3

python3 -u scripts/run_anchor_responder_then_tag_cm.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --target-name BSF66F \
  --cm-lines 100 \
  --quiet-tag-name BSF66F \
  --out-dir logs/anchor_responder_then_tag_cm_$(date +%Y%m%d_%H%M%S)
```

## Docs

- `docs/20260412_anchor_sweep_success_V1.0.md`
- `docs/autopos_sweep_loop_reference_20260412.md`
- `docs/ble_command_reference_20260409.md`
- `docs/20260410_runbook_ble_anchor_sweep_cm.md`
