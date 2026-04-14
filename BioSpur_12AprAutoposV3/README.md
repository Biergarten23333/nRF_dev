# BioSpur_12AprAutoposV3

Goal: a small runnable bundle for AUTOPOS anchor sweep + Tag115 CM capture on the nRF52840 master-control CDC.

## Dependencies

- Python 3
- `pyserial` (`pip3 install -r requirements.txt`)

## Ports

- Master-control CDC (example):
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00`

## Full Flow (Sweep 100-set -> Tag115 CM 100 lines)

This assumes the master-control firmware is already flashed and anchors are powered.

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_12AprAutoposV3

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

## Post-Sweep Only (Convert anchors to responder -> Tag115 CM 100 lines)

Use this when sweep already finished and you only want the Tag stage:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_12AprAutoposV3

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
