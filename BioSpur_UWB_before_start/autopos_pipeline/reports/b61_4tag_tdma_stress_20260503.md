## b61 4-Tag TDMA Stress Checkpoint - 2026-05-03

### Goal

1. ReOTA BSE88E with the same b61 Tag image to prove OTA capability.
2. If post-OTA marker match succeeds, run a 120s 4-Tag motion capture to check whether TDMA can sustain 10 Hz x 4 Tags.

### Target Image

- Tag marker: `alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0`
- Target Tag: `BSE88E`
- Role note: `Pelvis`

### OTA Attempt

Command:

```bash
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/ota_deploy_tag_set.py \
  --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00 \
  --targets BSE88E \
  --timeout-s 360 \
  --max-attempts 2 \
  --expected-fw-marker alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0 \
  --out-dir SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_20260503_192001
```

Result:

- OTA upload: success observed
- Reboot / controller return to RECV: success observed
- Serial lost: false
- Stage summary reason: `ota_success_observed`
- Post VERSION: `actual=-`
- Post marker match: `False`

Important logs:

- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_20260503_192001/BSE88E/stage1/single_shot.log`
- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_20260503_192001/deploy_summary.json`
- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_20260503_192001/tag_version_query.log`

### Follow-Up Manual Probe

After the OTA script returned `match=False`, a manual Master_Tag serial probe was run with target name cleared and prefix `BS` to scan/connect all visible Tags.

Probe log:

- `SS-TWR/alt-SS-TWR/broadcast/logs/bse88e_manual_version_probe_20260503_192356.log`

Observed visible Tags:

- `BS2DCE`
- `BSF66F`
- `BSDC91`

Observed count in manual probe log:

```text
BSE88E 0
BSF66F 442
BS2DCE 441
BSDC91 431
```

No `VERSION` lines were received during this probe.

### Decision

The 120s 4-Tag TDMA capture was not started.

Reason: the precondition for a valid 4-Tag test was not met. After OTA, Master_Tag could not see or connect to `BSE88E`; running the 120s capture now would produce a false 3-Tag result, not a real 4-Tag stress test.

### Current Interpretation

The OTA data path itself completed for BSE88E, but post-OTA application visibility is not confirmed because `BSE88E` did not reappear in Master_Tag scans. Possible causes to check manually:

- BSE88E is powered off, reset-stuck, or out of BLE range.
- BSE88E is connected to another central, such as the second Linux machine used for scanning.
- BSE88E is advertising differently after the same-image OTA/reboot.
- BSE88E requires a manual power cycle after this ReOTA attempt.

### Next Safe Step

Power-cycle BSE88E only, ensure no other central is connected to it, then rerun a version/APOS probe for `BSE88E`. Only after `BSE88E` is visible and b61 marker or TS/TR output is confirmed should the 120s 4-Tag TDMA capture be started.


## Retry - 2026-05-03 19:26

Command:

```bash
python3 SS-TWR/alt-SS-TWR/broadcast/scripts/ota_deploy_tag_set.py   --port /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00   --targets BSE88E   --timeout-s 360   --max-attempts 2   --expected-fw-marker alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0   --out-dir SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_retry_20260503_192619
```

Result:

- BSE88E was visible and connected by Master_Tag.
- Evidence: scan accepted `DB:AF:30:AA:82:AD`, token `142`, bs `BSE88E`.
- BSE88E emitted b61-style `TS`, `TR`, `RXG`, and `CD` notifications during the attempt.
- `OTA_READY` was observed.
- Upload did not start.
- Failure reason: `ota_gate_failed_after_dfu_ready`.
- Specific gate symptoms:
  - First SMP gate probe: subscribe callback `err=14`, then timeout, `subscribe_not_ready rc=-62`.
  - Second SMP gate probe: subscribe callback `err=14`, then disconnect reason `0x08`, `subscribe_not_ready rc=-13`.
  - Reconnect attempt then had `MTU exchange failed: 14`, `NUS service not found`, discovery `-128`, disconnect `0x3e`.

Important logs:

- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_retry_20260503_192619/BSE88E/stage1/single_shot.log`
- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_retry_20260503_192619/deploy_summary.json`

Decision:

The 120s 4-Tag TDMA capture was still not started, because the requested ReOTA/match gate did not pass. However, BSE88E is now confirmed visible to Master_Tag and running the broadcast ranging app well enough to emit `TS/TR`.


## Retry After B306 Blinky - 2026-05-03 19:34

Context: the custom PCB's u-blox B306 was flashed with blinky to silence its old UART firmware. The BSE88E ReOTA was then retried.

Result:

- BSE88E was visible and connected by Master_Tag.
- Evidence: scan accepted `DB:AF:30:AA:82:AD`, token `142`, bs `BSE88E`.
- BSE88E emitted b61-style `TS`, `TR`, `RXG`, and `CD` notifications during the attempt.
- `OTA_READY` was observed.
- Upload still did not start.
- Failure reason remained: `ota_gate_failed_after_dfu_ready`.
- The gate failure pattern remained the same as before B306 was silenced:
  - SMP subscribe callback `err=14`
  - `subscribe_not_ready rc=-62`
  - disconnect reason `0x08`
  - subsequent `no_conn` / `-128`

Important logs:

- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_after_b306_blinky_20260503_193428/BSE88E/stage1/single_shot.log`
- `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_reota_b61_after_b306_blinky_20260503_193428/deploy_summary.json`

Conclusion:

Silencing B306 did not fix BSE88E OTA. BSE88E is alive and running the broadcast app, but its SMP/DFU gate remains unstable. This points away from B306 UART interference and toward BSE88E BLE DFU/SMP timing, subscription, or connection stability during OTA mode.


## b62 OTA Silent Fix Build - 2026-05-03 19:4x

Root-cause candidate found in Tag firmware:

- Before b62, `OTA_PREPARE` returned `OTA_READY` but left `ota_active=false`.
- If `OTA_BEGIN` over NUS timed out, the Tag continued the normal broadcast loop and kept emitting `TS/TR/RXG/CD` while Master_Tag was trying to subscribe to SMP.
- This matches the BSE88E failure pattern: `OTA_READY` was observed, but BSE88E continued normal telemetry and SMP subscribe/gate failed.

b62 change:

- `OTA_PREPARE` now immediately enters OTA-silent mode by setting `ota_active=true`.
- Pending sample/cal/bundle state is cleared.
- The BLE TX FIFO is purged before `OTA_READY`.
- `ble_notif_enabled()` no longer flushes pending snapshots while OTA mode is active.
- `OTA_BEGIN` also purges queued TX before `OTA_BEGIN_OK`.

Build artifacts:

- Tag marker: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Tag build: `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0`
- Tag DFU zip: `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/dfu_application.zip`
- Master_Tag carrier build: `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b62-otaprep-silent-carrier`
- Master_Tag carrier allowlist confirmed: `BSF66F,BS2DCE,BSDC91,BSE88E`
- LFRC check passed for the carrier build.

Deployment blocker:

- Attempted to flash Master_Tag carrier using SNR `1050070698`.
- J-Link connected to the probe, but could not connect to `NRF5340_XXAA_APP`.
- This is consistent with the current bench wiring: `1050070698` is being used as an external OB-JLink over TC2030 to the BSE88E/DWM1001C target, not to the Master_Tag B120 nRF5340 debug target.
- No Master_Tag carrier write was confirmed. Treat the carrier flash attempt as failed despite the wrapper's final `action=ok` line.

Implication:

- The b62 Tag image is built, but OTA b62 cannot be tested through Master_Tag until the Master_Tag carrier containing the b62 payload is actually flashed.
- Safe next options:
  1. Reconnect the `1050070698` SWD path to the Master_Tag B120 nRF5340 and flash the b62 carrier, then OTA BSE88E to b62 and verify `match=True`.
  2. If TC2030 is still connected to BSE88E and a one-time direct recovery is acceptable, direct-flash b62 to BSE88E first, then later flash the b62 Master_Tag carrier and validate b62 -> b62 ReOTA.


## b62 Deployment and BSE88E OTA Validation - 2026-05-03 19:50

Master_Tag carrier deployment:

- `1050070698` SWD was reconnected to the Master_Tag B120 / nRF5340.
- Carrier flashed: `build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b62-otaprep-silent-carrier/zephyr/merged_domains.hex`
- LFRC precheck passed.
- Carrier allowlist confirmed: `BSF66F,BS2DCE,BSDC91,BSE88E`
- J-Link connected to `NRF5340_XXAA_APP`, flash erase/program/verify completed.

BSE88E b61 -> b62 OTA:

- Command output directory: `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_b62_otaprep_silent_20260503_195052`
- Pre-version: `alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0`
- Target marker: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Upload gate passed.
- Upload progress reached 100%.
- Post-version: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Match: `True`

BSE88E b62 -> b62 ReOTA:

- Command output directory: `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BSE88E_b62_reota_verify_20260503_195249`
- Pre-version: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Target marker: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Upload gate passed.
- Upload progress reached 100%.
- Post-version: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Match: `True`

Conclusion:

- The BSE88E OTA failure was fixed by b62's OTA silent preparation behavior.
- The previous failure (`OTA_READY` observed but SMP upload never started) is no longer present.
- BSE88E can now accept OTA through Master_Tag without TC2030.
- b62 should replace b61 as the Tag OTA baseline before 4-Tag TDMA stress testing.

Recommended next step:

1. Push and verify the current V4 APOS layout to BSE88E, because the new Tag may not have the calibrated layout in NVS.
2. Run the 120s 4-Tag TDMA motion capture with `BSF66F,BS2DCE,BSDC91,BSE88E`.


## BS6F3A / Ankle_R Addition - 2026-05-03

Context:

- A second custom-board pressure-test Tag was brought up after BSE88E.
- Mount / role note: `Ankle_R`
- Stable BLE identity: `BS6F3A`
- BLE address observed by Master_Tag: `FB:BE:DD:5A:88:33` (random)

FICR / identity:

```text
DEVICEID[0] = 0xCE9788BB
DEVICEID[1] = 0x06335B4C
computed BS code = 0x6F3A
computed BLE name = BS6F3A
default tag byte = 0x3A
default local short address = 0xB13A
```

Direct-flash image used:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Master_Tag discovery fix:

- BS6F3A was visible from a second Linux machine as a BLE local name, but the Master_Tag initially did not accept it.
- Root cause: Master_Tag scan path dropped Tag advertisements without a parsed BioSpur manufacturer token.
- Fix: Master_Tag now accepts explicit `BSxxxx` name-derived targets and bypasses boot allowlist only when an explicit target name/UUID is set.
- Validation log: `logs/scan_BS6F3A_after_master_namefallback_20260503.log`

BS6F3A b62 OTA validation:

- Command output directory: `SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BS6F3A_b62_20260503_232316`
- Pre-version: `alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0`
- Target marker: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Upload gate passed.
- Upload progress reached 100%.
- Post-version: `alt-bcast-b62-otaprep-silent-g1200-r1000`
- Match: `True`

5-Tag TDMA pressure test including BS6F3A:

- Capture: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_5tag_motion180_b62_20260503_232731`
- Targets: `BSF66F,BS2DCE,BSDC91,BSE88E,BS6F3A`
- Duration: `180s`
- Total TR rows: `72016`
- Theoretical TR rows: `5 tags * 8 anchors * 10Hz * 180s = 72000`
- Total TS rows: `7472`
- `tf_all=0`

Per-Tag result:

| Tag | Role | TS rows | TS rate | TR rows | TR valid | Notes |
|---|---|---:|---:|---:|---:|---|
| BSF66F | Static Tag | 1800 | 10.00 Hz | 14400 | 13365 / 92.8% | OK |
| BS2DCE | RotoTag | 1800 | 10.00 Hz | 14400 | 14140 / 98.2% | OK |
| BSDC91 | RotoTag | 271 | 1.51 Hz | 14400 | 3027 / 21.0% | TR full-rate but range validity collapsed after ~55s |
| BSE88E | Pelvis | 1800 | 10.00 Hz | 14408 | 13273 / 92.1% | OK |
| BS6F3A | Ankle_R | 1801 | 10.01 Hz | 14408 | 14151 / 98.2% | OK |

Conclusion:

- BS6F3A is now a validated b62 OTA-capable pressure-test Tag.
- BS6F3A sustained full 10 Hz TS output in the 5-Tag TDMA run.
- The 5-Tag TR transport path is effectively full-rate at ~400 TR/s.
- The only failing Tag in that run was BSDC91, whose TR rows were present but mostly timeout/invalid; this is a per-Tag UWB range/solve issue, not a global TDMA/BLE throughput failure.
