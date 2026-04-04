# BLE Master Control Verification 2026-04-04

## Scope
- Target bench model:
  - Host <-> 52840 via USB CDC only
  - 52840 acts as BLE central
  - Anchor / Tag are controlled over BLE
- Goal of this note:
  - record the concrete control commands already proven in logs
  - record the exact live blocker encountered in today's re-validation

## 1. Proven Tag Control Commands

This path has direct runtime evidence in:
- [docs/BlackBox_20260331.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/BlackBox_20260331.md)
- [step2_mode_switch_verify_after_direct_flash.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_pmode3_fix_20260401_003909/step2_mode_switch_verify_after_direct_flash.log)

Observed working USB CDC command sequence:

```text
mode recv
device kind tag
scan
conn
cmd MODE?
cmd MODE AOTA
cmd MODE?
cmd CFG_STATUS
```

Observed runtime proof:
- `Control mode loaded: RECV`
- `Connected[0]: ... bs=BSF66F`
- `BLE cmd sent[0]: MODE?`
- `BSF66F notify: MODE=MOTION PMODE=0 ...`
- `BLE cmd sent[0]: MODE AOTA`
- `BSF66F notify: MODE_OK MODE=AOTA LIVE=1`
- `BLE cmd sent[0]: MODE?`
- `BSF66F notify: MODE=AOTA PMODE=3 ...`
- `BLE cmd sent[0]: CFG_STATUS`
- `BSF66F notify: CFG ... pmode=3 ...`

Conclusion:
- Tag mode switch through `Host -> 52840 USB CDC -> BLE` is log-proven.
- The concrete mode-switch payload for Tag is `MODE ...` / `CFG_STATUS`.

## 2. Proven Anchor Control Payloads

This path has direct BLE control evidence in:
- [docs/BlackBox_20260331.md](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/docs/BlackBox_20260331.md)
- [ble_loop_B917_set_matrix_commit.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/anchor_ble_ctrl_phase_20260331_1945/ble_loop_B917_set_matrix_commit.json)

Observed working anchor control payloads:

```text
R MATRIX
R MASTER
R RESPONDER
L <A..H>
G <generation>
VALIDATE
COMMIT
REBOOT
SYNC
```

Observed payload/result evidence:
- `R MATRIX -> OK PENDING_ROLE`
- `L B -> OK PENDING_LABEL`
- `G 11 -> OK PENDING_GEN`
- `VALIDATE -> OK VALID`
- `COMMIT -> OK COMMIT REBOOT_REQUIRED`

Observed post-apply readback:
- `label=B role=matrix schema=2 gen=11`

Conclusion:
- Anchor BLE control protocol and concrete role payloads are proven.
- The protocol itself is not speculative.

## 3. Current Best-Fit USB CDC Wrapper for Anchor

The current unified master control parser supports:
- `device kind anchor`
- `cmd <raw>`
- `oneshot <raw>`

So the expected host-side wrapper for anchor role mutation is:

```text
mode recv
device kind anchor
scan
conn
cmd R MATRIX
cmd VALIDATE
cmd COMMIT
```

or for other roles:

```text
cmd R MASTER
cmd VALIDATE
cmd COMMIT
```

```text
cmd R RESPONDER
cmd VALIDATE
cmd COMMIT
```

Important status:
- the anchor raw payloads are proven
- the `cmd <raw>` USB CDC wrapper exists in code
- but this exact host-side anchor sequence was not re-proven live in this session because the current master runtime became unavailable after reflashing

## 4. Live Re-Validation Performed Today

### Actions actually executed
1. Checked latest master build candidates:
   - `build-master-control-anchor-transport-sys-a12`
   - `build-master-control-ota-fix-20260403`
2. Confirmed `build-master-control-anchor-transport-sys-a12.source` describes it as:
   - `anchor-transport-a12 (52840 control center)`
3. Attempted live USB CDC probing.
4. Reset 52840 with explicit non-interactive target selection:
   - `JLinkExe -NoGui 1 -SelectEmuBySN 683234364 -CommandFile /tmp/jlink_reset_683234364.cmd`
5. Reflashed 52840 with explicit non-interactive target selection:
   - `JLinkExe -NoGui 1 -SelectEmuBySN 683234364 -CommandFile /tmp/jlink_flash_master_control_center.cmd`

### Actual runtime result after flash
After the flash completed:
- `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00` did not reappear
- only this CDC endpoint remained:
  - `/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00 -> ../../ttyACM0`

`udevadm` on `/dev/ttyACM0` confirmed:
- `ID_SERIAL=SEGGER_J-Link_000683234364`
- not `BioSpur_BLE_Control`

### What this means
Today's live blocker is not “which command should I send”.
It is:

> The flashed `build-master-control-anchor-transport-sys-a12` image did not come back as the expected BioSpur USB CDC control runtime, so live USB CDC command verification could not continue on the current bench state.

## 5. Operational Command Table

### Tag BSF66F
Use:

```text
mode recv
device kind tag
scan
conn
cmd MODE?
cmd MODE AOTA
cmd MODE?
cmd CFG_STATUS
```

### Anchor A
Expected minimal role test:

```text
mode recv
device kind anchor
scan
conn
cmd R MATRIX
cmd VALIDATE
cmd COMMIT
```

Return to responder:

```text
cmd R RESPONDER
cmd VALIDATE
cmd COMMIT
```

## 6. Current Bottom Line

- Tag BLE mode-switch commands are already runtime-proven.
- Anchor BLE role payloads are already runtime-proven.
- The remaining unresolved point is not payload design.
- The remaining unresolved point is recovering the correct live 52840 USB CDC control runtime so those commands can be re-verified on the current bench.

## 7. Runtime Restored and Live Verification Re-Run

The incorrect 52840 image was rolled back and the USB CDC runtime was restored with the same master image used in the successful OTA runs:

- [build-master-control-ota-fix-20260403/master_control/zephyr/zephyr.hex](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-control-ota-fix-20260403/master_control/zephyr/zephyr.hex)

Explicit non-interactive flash command:

```bash
cat >/tmp/jlink_flash_master_ota_fix_20260403.cmd <<'EOF'
device nRF52840_xxAA
si SWD
speed 4000
r
loadfile /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/build-master-control-ota-fix-20260403/master_control/zephyr/zephyr.hex
r
g
qc
EOF

JLinkExe -NoGui 1 -SelectEmuBySN 683234364 -CommandFile /tmp/jlink_flash_master_ota_fix_20260403.cmd
```

Post-flash runtime evidence:

- `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 -> ../../ttyACM1`
- `/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00 -> ../../ttyACM0`

### Live bench verification logs

- [tag_bsf66f.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_mastercontrol_verify_20260404_214800/tag_bsf66f.log)
- [anchor_a.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_mastercontrol_verify_20260404_214800/anchor_a.log)
- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_mastercontrol_verify_20260404_214800/summary.json)

### Tag BSF66F live result

Commands actually sent:

```text
status
mode recv
device kind tag
ota_target token 111
ota_target name -
ota_target uuid -
device show
status
scan
conn
cmd MODE?
cmd MODE AOTA
cmd MODE?
cmd CFG_STATUS
```

Observed runtime behavior:

- `device kind set: tag`
- `SCANNING` continuously sees the Tag as:
  - `bs=BSF66F`
  - `nus=0`
  - `dfu=1`
  - `token=1`
- the receiver path rejects it every time:
  - `RECV candidate rejected: DFU-only peer FC:9A:15:34:AB:20 (random) (no NUS/name match)`
- every command attempt returns no active control link:
  - `cmd rc=-128 payload=MODE?`
  - `cmd rc=-128 payload=MODE AOTA`
  - `cmd rc=-128 payload=CFG_STATUS`

Blackbox conclusion:

- the CDC path is alive
- the command parser is alive
- BSF66F is advertising
- but on this bench state BSF66F is currently **DFU-only**, not an active NUS/control peer
- therefore live Tag mode-switch did **not** complete in this run

### Anchor A live result

Commands actually sent:

```text
status
mode recv
device kind anchor
ota_target uuid 4DC6B8187E33803AE8601FB0D7992B96
device show
status
scan
conn
cmd R MATRIX
cmd VALIDATE
cmd COMMIT
scan
conn
cmd R RESPONDER
cmd VALIDATE
cmd COMMIT
```

Observed runtime behavior:

- `device kind set: anchor (OTA target defaults reset)`
- `OTA target filter: token=-1 name=- prefix=BS uuid=4DC6B8187E33803AE8601FB0D7992B96`
- during the whole run, no Anchor A advertisement or connection appeared in the log
- the only repeatedly observed BLE peer was the same DFU-only Tag:
  - `SCAN hit: ... bs=BSF66F ... nus=0 dfu=1 ...`
- all anchor raw-payload sends therefore had no active BLE config link:
  - `cmd rc=-128 payload=R MATRIX`
  - `cmd rc=-128 payload=VALIDATE`
  - `cmd rc=-128 payload=COMMIT`
  - `cmd rc=-128 payload=R RESPONDER`

Blackbox conclusion:

- the host can set `device kind anchor` and the Anchor UUID filter
- but in this run the master never saw or connected to Anchor A over BLE
- therefore live Anchor role-switch did **not** complete in this run

## 8. Updated Bottom Line

- The correct `BioSpur_BLE_Control` 52840 runtime has been restored.
- The remaining live blocker is not USB CDC and not command syntax.
- Current bench-state blocker:
  - BSF66F is advertising as a DFU-only peer (`nus=0 dfu=1`), so RECV-side mode commands cannot attach.
  - Anchor A was not observed as a connectable control peer in this live run.
