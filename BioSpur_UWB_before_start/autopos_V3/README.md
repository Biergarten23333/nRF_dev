#!/usr/bin/env markdown
# autopos_V3 (Capture + Solve)

This bundle is for **AutoPos V3-lite** experiments:
- Capture fresh **Anchor sweep** (`A-H`, default 100 sets)
- Capture fresh **Tag115 CM** (default 100 aggregated CM notify lines)
- Run **V3-lite** offline chain (`prepare_autopos_v3_lite.py`)

Note: V3-lite is a pragmatic stepping stone (tighter fusion floors + more aggressive solve).
It is **not** the full V3 described in `docs/AutoPos_V1_to_V5_Implementation_Guide.md`
(no SDP init / antenna delay / Tukey IRLS loop implemented yet).

## V3_full (Repo Implementation)

If you want the **V3_full** implementation (V3 fusion + SDP/MDS seed + antenna-delay bias + Tukey IRLS),
use the repo-level wrapper:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/prepare_autopos_v3_full.py \
  --pairs-csv <PATH_TO_pairs_all.csv> \
  --out-dir <OUT_DIR> \
  --floating-reference-session <PATH_TO_floating_ref115_from_cm> \
  --floating-reference-z-prior-mm 820 \
  --bias-sigma-mm 200 \
  --sigma-dist-mm 80 \
  --sigma-ref-mm 150 \
  --max-iters 15 \
  --verbose 1
```

## V3-box / V3-free

Two geometry-specialized variants are now available on top of the same `V3_full`
core solver:

- `V3-box`: keeps approximate upper/lower paired-column structure
- `V3-free`: keeps only lower-band / upper-band separation

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/prepare_autopos_v3_box.py \
  --pairs-csv <PATH_TO_pairs_all.csv> \
  --out-dir <OUT_DIR>
```

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/prepare_autopos_v3_free.py \
  --pairs-csv <PATH_TO_pairs_all.csv> \
  --out-dir <OUT_DIR>
```

See [docs/20260419_V3_box_V3_free.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/20260419_V3_box_V3_free.md).

## Run

```bash
export PORT=/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXXXXXX-if00
bash autopos_V3/scripts/run_v3_capture_and_solve.sh
```


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
