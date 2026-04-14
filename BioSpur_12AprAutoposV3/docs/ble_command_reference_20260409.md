# BLE Command Reference

Snapshot time: 2026-04-09 Europe/Berlin

This document records the BLE-related command surfaces that exist in the current codebase on April 9, 2026.

Scope:
- 52840 master control UART commands
- Tag BLE NUS commands
- Anchor BLE control commands
- CM / TS / SW output formats

Primary source files:
- `apps/master_control/src/main.c`
- `apps/master/src/master_multi_app.c`
- `apps/tag/src/uwb_tag_ble.c`
- `src/anchors/unified/anchor_ble_ctrl.c`
- `scripts/capture_master_ble_session.py`

## 1. 52840 Master Control UART

Source:
- `apps/master_control/src/main.c`

### 1.1 Base commands

```text
status
mode recv
mode ota
mode autopos
scan
conn
initiate
```

Notes:
- `mode recv` switches the 52840 into receive mode.
- `mode ota` switches to OTA mode.
- `mode autopos` switches to Anchor AUTOPOS orchestration mode.
- `conn` starts connect-and-start behavior for the current runtime target.

### 1.2 OTA runtime commands

```text
ota_reset
```

### 1.3 Runtime raw BLE/NUS commands

```text
cmd <raw>
oneshot <raw>
oneshot show
oneshot clear
```

Behavior:
- `cmd <raw>` sends the raw text command immediately to all currently connected and ready peers.
- For Tag peers, it goes over BLE NUS.
- For Anchor peers, it goes over the Anchor BLE control characteristic.
- `oneshot <raw>` arms a raw command that will be sent once after a future link becomes ready.

### 1.4 Device model commands

```text
device show
device kind anchor
device kind tag
```

Behavior:
- `device kind anchor` configures the runtime target model as Anchor.
- `device kind tag` configures the runtime target model as Tag.

### 1.5 OTA target commands

```text
ota_target show
ota_target token <id|-1>
ota_target name <BSxxxx|->
ota_target prefix <BS|->
ota_target uuid <32hex|->
```

Usage:
- For Tag receive / OTA workflows, `token`, `name`, and `prefix` are relevant.
- For Anchor OTA / BLE-control workflows, `uuid` is the key selector.

### 1.6 Anchor query commands

```text
anchor version <A..H|UUID32|all>
```

### 1.7 AUTOPOS commands

```text
autopos status
autopos map <A..H> <UUID32>
autopos map show
autopos round <A..H>
autopos apply
```

Notes:
- `AUTOPOS` currently supports one `MASTER` and seven `MATRIX` anchors.
- It does not currently expose a formal high-level command like `anchor role all RESPONDER`.

## 2. Tag BLE NUS Commands

Source:
- `apps/tag/src/uwb_tag_ble.c`

### 2.1 Status commands

```text
PING
STATUS
TDMA_STATUS
CFG_STATUS
MODE?
HELP
```

### 2.2 Mode commands

```text
MODE CAL
MODE CALI
MODE CALIBRATION
MODE MOTION
MODE FIXED
MODE AOTA
MCAL
MMOT
```

Behavior:
- `MCAL` switches the Tag into calibration mode.
- `MMOT` switches the Tag into dynamic/motion mode.
- `MODE AOTA` switches the Tag into Anchor-OTA quiet state.
- `MODE CAL`, `MODE CALI`, and `MODE CALIBRATION` all map to calibration mode.

Example response:

```text
MODE_OK MODE=CAL LIVE=1
```

### 2.3 TDMA / runtime config commands

```text
TDMA_SET <slot>
CFG TAG=<id> SLOT=<slot> COUNT=<count> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> GEN=<n> PMODE=<0|1|2|3> FIXED=a,b,c,d
STREAM?
STREAM ON
STREAM OFF
```

Behavior:
- `STREAM OFF` disables BLE runtime stream emission from the Tag.
- While `STREAM OFF` is active, runtime `TS` / `TagSummary` / `CM` payloads are suppressed.
- Command responses are still allowed, so `STREAM?`, `MODE?`, `CFG_STATUS`, and `STREAM ON` still work.
- `STREAM ON` re-enables BLE runtime stream emission.
- `STREAM OFF/ON` is persisted in Tag BLE settings and survives reconnect/reboot.

### 2.4 OTA commands

```text
OTA_STATUS
OTA_PREPARE
OTA_BEGIN
OTA_CANCEL
```

### 2.5 System command

```text
REBOOT
```

### 2.6 Typical Tag BLE responses

```text
MODE_OK MODE=CAL LIVE=1
CFG_OK ...
STREAM_OK OFF
STREAM_OK ON
ERR:BUSY_OTA
MODE_BAD
UNKNOWN_CMD
```

## 3. Anchor BLE Control Commands

Source:
- `src/anchors/unified/anchor_ble_ctrl.c`

### 3.1 Help and sync

```text
HELP
SYNC
```

Typical response:

```text
OK CMDS=PENDING LABEL|PENDING ROLE|PENDING GEN|VALIDATE|COMMIT|REBOOT|SYNC
```

### 3.2 Validation / commit / reboot

```text
VALIDATE
COMMIT
APPLY
REBOOT
```

Typical responses:

```text
OK VALID
OK COMMIT REBOOT_REQUIRED
OK REBOOT
```

### 3.3 Pending configuration commands

```text
PENDING LABEL <A..H>
PENDING ROLE <MASTER|MATRIX|RESPONDER>
PENDING GEN <n>
```

Typical responses:

```text
OK PENDING_LABEL
OK PENDING_ROLE
OK PENDING_GEN
```

### 3.4 Short-form commands

```text
R <MASTER|MATRIX|RESPONDER>
ROLE <MASTER|MATRIX|RESPONDER>
L <A..H>
LABEL <A..H>
G <n>
GEN <n>
```

Typical errors:

```text
ERR:BAD_CMD
ERR:INVALID_ROLE
ERR:INVALID_LABEL
ERR:INVALID_GEN
```

### 3.5 Role change sequence

Changing an Anchor role is not complete after `R RESPONDER` or `R MASTER`.

The full sequence is:

```text
R RESPONDER
VALIDATE
COMMIT
REBOOT
```

Likewise for other roles:

```text
R MASTER
VALIDATE
COMMIT
REBOOT
```

## 4. Runtime Data Output Formats

Source:
- `scripts/capture_master_ble_session.py`

### 4.1 CM format

```text
CM;<ver>;<sweep>;<anchor>;<status>;<raw>;<filt>;<q>;<ok>;<fail>
```

Meaning:
- `<ver>`: protocol version
- `<sweep>`: sweep index
- `<anchor>`: anchor id
- `<status>`: per-anchor capture status
- `<raw>`: raw range
- `<filt>`: filtered range field in stream
- `<q>`: quality percent
- `<ok>`: ok counter
- `<fail>`: fail counter

### 4.2 TS format

```text
TS;...
```

`capture_master_ble_session.py` supports the semicolon TS form and compact/full `TagSummary` forms.

### 4.3 SW format

```text
SW-<MASTER>,<PEER>,<dist>,<quality>,<PEER>,<dist>,<quality>,...
```

Example:

```text
SW-H,A,4141,90,B,5536,80,C,4164,90,D,1661,100,E,3717,90,F,5943,100,G,3874,100
```

## 5. Important Current Limitations

These high-level commands do not currently exist in `master_control`:

```text
anchor role A RESPONDER
anchor role all RESPONDER
anchor cmd <A..H> <raw>
```

So with pure BLE Anchors, bulk role changes to `RESPONDER` are not yet exposed as a formal top-level UART command.

Current workaround logic must:
- select or connect to the intended Anchor
- ensure that Anchor BLE control is ready
- then send raw control commands through `cmd <raw>`

## 6. Minimal Ref115 Calibration / CM Receive Flow

Assumptions:
- Anchors are already in a Tag-compatible responder state
- Tag 115 is the only active Tag, or at least the intended one
- 52840 is using the master control firmware

Minimal UART sequence on the 52840:

```text
device kind tag
mode recv
oneshot MCAL
conn
```

Meaning:
- `device kind tag`: runtime model is Tag
- `mode recv`: receive mode
- `oneshot MCAL`: when the Tag link becomes ready, send `MCAL`
- `conn`: scan and connect

Expected success signs:
- `BLE one-shot command sent[...]`
- Tag notify line containing:

```text
MODE_OK MODE=CAL LIVE=1
```

Then look for `CM;...` lines in the CDC stream.

## 6.1 Sweep Quiet Helper For BSF66F

If Tag115 / `BSF66F` is nearby during Anchor Sweep and must stay powered on without influencing the sweep, send:

```text
device kind tag
mode recv
ota_target name BSF66F
conn
cmd MODE AOTA
cmd STREAM OFF
```

Why both commands are needed:

- `MODE AOTA` puts the Tag into Anchor-OTA quiet state, keeping BLE alive while forcing the UWB loop into no-poll behavior.
- `STREAM OFF` suppresses BLE runtime `TS` / `CM` output when supported by the deployed Tag firmware.
- `STREAM OFF` by itself does not stop the Tag from ranging.
- For anchor sweep isolation, `MODE AOTA` is the required command. `STREAM OFF` is optional best-effort log suppression.

To restore normal Tag runtime streaming later:

```text
cmd MCAL
cmd STREAM ON
```

## 7. Example Timestamped Command Log Format

Recommended manual record style:

```text
[2026-04-09 22:15:31 CEST] device kind tag
[2026-04-09 22:15:34 CEST] mode recv
[2026-04-09 22:15:36 CEST] oneshot MCAL
[2026-04-09 22:15:40 CEST] conn
```

For Anchor BLE control:

```text
[2026-04-09 22:18:02 CEST] cmd R RESPONDER
[2026-04-09 22:18:03 CEST] cmd VALIDATE
[2026-04-09 22:18:05 CEST] cmd COMMIT
[2026-04-09 22:18:06 CEST] cmd REBOOT
```

## 8. Summary

The current BLE command surfaces are split as follows:
- 52840 UART: orchestration and transport-side control
- Tag BLE NUS: runtime mode/config/OTA commands
- Anchor BLE control: role/config/commit/reboot commands

This file is intended as a code-backed command snapshot for the repo state on 2026-04-09.
