# 2026-04-03 OTA BlackBox Runbook

## Purpose

Validate externally (black-box) that strict-target OTA is not disturbed by RECV background behavior.

## Preconditions

- 52840 controller is flashed with current `master_control` build.
- Target UUID is known (32 hex).
- USB CDC port is available, example:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00`

## Run

```bash
python3 scripts/loop_test_ota_targeting.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_XXXXXXXX-if00 \
  --target-uuid <TARGET_UUID_32HEX> \
  --trials 3 \
  --trial-timeout-s 260 \
  --skip-flash \
  --direct-ota-mode \
  --out-dir logs/ota_blackbox_$(date +%Y%m%d_%H%M%S)
```

Then run checker:

```bash
python3 scripts/check_ota_blackbox.py --run-dir logs/ota_blackbox_<timestamp>
```

For full completion requirement:

```bash
python3 scripts/check_ota_blackbox.py --run-dir logs/ota_blackbox_<timestamp> --require-complete
```

## PASS Criteria

- `wrong_target_trials == 0`
- `recv_bg_interference_trials == 0`
- strict UUID match for all trials (`target_match_count == trial_count`)
- if a trial logs first upload TX (`first_upload_tx_seen=1`), then first upload response must be seen (`first_upload_rsp_seen=1`)
- if first upload response is seen, upload must progress beyond early chunks (`upload_progressed=1`)

## FAIL Classes

- `wrong_target_trials != 0`
- `recv_bg_interference_trials != 0`
- strict UUID mismatch
- first upload response missing
- upload not progressed beyond early chunks
- (with `--require-complete`) not all trials OTA-complete end-to-end

## Key External Evidence

- `logs/.../trial_XX/ota_trial.log`
- `logs/.../summary.json`
- checker stdout PASS/FAIL with reason list

