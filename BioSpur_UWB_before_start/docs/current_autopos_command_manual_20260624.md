# BioSpur AutoPos Command Manual

Date: 2026-06-24

This manual separates commands into two layers:

- UI / host commands: commands run on the Ubuntu computer, Flutter UI actions, shell helpers, and Python scripts.
- Hardware / firmware commands: text commands actually parsed by the Master, Tag, or Anchor firmware.

Important rule:

- `Static`, `Roto`, `Wand`, and `Free` are capture scenes on the UI/host side only.
- They are not Tag firmware modes.
- Current normal ranging capture is `PMODE=0` / motion TDMA.
- CIR is an output option: `off`, `compact`, or `full`.
- Tag and Anchor body firmware updates are OTA-only in normal operation.

---

## A. UI / Host Commands

These commands do not exist inside the Tag or Anchor firmware. They are convenience wrappers on the PC.

### A1. Flutter UI Actions

The Flutter UI is a front end. Buttons usually call host scripts or send Master serial commands.

Typical UI actions:

```text
Connect
USB On
Read Latest
All Responder
System Reset
Clear Data
Start Sweep
Stop
CIR Capture toggle
Static / Roto / Wand / Free capture
Full / Compact / Off CIR selection
Anchor CDC selection
Tag CDC selection
```

Meaning:

```text
Connect
  Select or verify Master_Anchor and Master_Tag CDC ports.

USB On
  Host-side USB power-management helper. Not a firmware command.

Read Latest
  Host-side filesystem summary reader.

All Responder
  Sends hardware command through Master_Anchor:
  anchor role all responder cir 0

System Reset
  Host-side reset of Master_Anchor / Master_Tag boards by J-Link SNR.
  This is not direct flashing of Tag/Anchor body firmware.

Start Sweep
  Starts AutoPos sweep host script.

CIR Capture toggle
  UI option for whether the next capture includes CIR.
  It should not start ranging by itself.

Static / Roto / Wand / Free
  Experiment scene labels and default target groups only.
  They do not become Tag firmware modes.
```

### A2. Erlangen Shell Helpers

Load helpers:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
source autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh
bio_setup my_session_name
bio_ports
```

Default ports:

```text
Master_Anchor CDC:
  /dev/serial/by-id/usb-Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02-if00

Master_Tag CDC:
  /dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00

Master_Anchor SNR:
  960148546

Master_Tag SNR:
  1050070698
```

Host helper commands:

```bash
bio_setup [session_name]
bio_ports
bio_usb_on
bio_reset_masters
bio_all_anchor_responder
bio_check_latest
bio_note ID "free text"
```

Capture scene helpers:

```bash
static -id ID01 [-s 120] [-cir off|compact|full]
roto   -id R01  [-s 120] [-cir off|compact|full]
wand   -id W01  [-s 120] [-cir off|compact|full]
free   -id F01  -targets BSF66F,BS9336 [-s 120] [-cir off|compact|full]
```

Default target sets:

```text
static -> BSF66F
roto   -> BS2DCE,BSDC91
wand   -> BS9336,BS955A,BSCCF4
free   -> explicit -targets list
```

AutoPos sweep helper:

```bash
sweep -id SW01 [-n 1000] [-p 10]
```

Standalone ultrasound helper:

```bash
us30 -id US01
```

Current helper defaults:

```text
BIOSPUR_RESET_MASTERS_BEFORE_CAPTURE=1
BIOSPUR_RESET_ANCHOR_BEFORE_CAPTURE=0
BIOSPUR_RESET_TAG_BEFORE_CAPTURE=1
BIOSPUR_RESET_ANCHOR_BEFORE_SWEEP=1
BIOSPUR_SKIP_ANCHOR_PREFLIGHT_FOR_CAPTURE=1
BIOSPUR_REUSE_TAG_LINKS_FOR_CAPTURE=1
```

### A3. Host Capture Scripts

Dual-master capture:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/run_dual_master_tdma_capture.py \
  --anchor-port "$BIOSPUR_ANCHOR_PORT" \
  --anchor-snr "$BIOSPUR_ANCHOR_SNR" \
  --tag-port "$BIOSPUR_TAG_PORT" \
  --tag-snr "$BIOSPUR_TAG_SNR" \
  --duration 120 \
  --targets BSF66F,BS9336,BS955A,BSCCF4 \
  --tr-hz 10 \
  --tdma-profile motion \
  --tag-cir off \
  --out-dir /tmp/biospur_capture
```

Single Master_Tag receive/capture script:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/run_recv_tdma_capture.py \
  --port "$BIOSPUR_TAG_PORT" \
  --controller-reset-snr "$BIOSPUR_TAG_SNR" \
  --duration 120 \
  --targets BSF66F,BS9336 \
  --tr-hz 10 \
  --tdma-profile motion \
  --tag-cir off \
  --out-dir /tmp/biospur_tag_capture
```

CIR meaning in host scripts:

```text
--tag-cir off
  Range-only capture.

--tag-cir compact
  Range capture plus compact CIR features over BLE.

--tag-cir full
  Range phase first with CIR off, then deferred USB full-CIR phase.
```

### A4. Host OTA Scripts

Tag OTA:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/ota_deploy_tag_set.py \
  --port "$BIOSPUR_TAG_PORT" \
  --targets BS9336,BS955A,BSCCF4 \
  --out-dir logs/ota_tag_$(date +%Y%m%d_%H%M%S) \
  --timeout-s 420 \
  --max-attempts 2
```

Anchor OTA:

```bash
cd SS-TWR/alt-SS-TWR/broadcast
python3 scripts/ota_deploy_anchor_set.py \
  --port "$BIOSPUR_ANCHOR_PORT" \
  --order ABCDEFGH \
  --out-dir logs/ota_anchor_$(date +%Y%m%d_%H%M%S) \
  --timeout-s 420
```

The OTA scripts check payload kind before deployment.

---

## B. Hardware / Firmware Commands

These are the real serial/BLE text commands parsed by firmware.

There are three hardware command surfaces:

```text
1. Master control CDC commands
   Typed to Master_Anchor or Master_Tag CDC.

2. Tag BLE runtime commands
   Parsed by Tag firmware over BLE NUS.
   Usually sent through Master_Tag using `cmd` / `cmd_all`.

3. Anchor BLE runtime commands
   Parsed by Anchor firmware over BLE control characteristic.
   Usually sent through Master_Anchor using `anchor ...` wrappers or raw `cmd`.
```

---

## B1. Master Control CDC Commands

These are parsed by `apps/master_control`.

General:

```text
status
mode recv
mode ota
mode autopos
scan
conn
initiate
```

Meaning:

```text
status
  Print current Master control mode.

mode recv
  Put Master into receiver/tag-control mode.

mode autopos
  Put Master_Anchor into anchor-control / AutoPos mode.

mode ota
  Put Master into OTA mode.

scan
  Scan only.

conn
  Connect and start.

initiate
  Start OTA when already in OTA mode.
```

Device model:

```text
device show
device kind anchor
device kind tag
```

OTA bundle:

```text
ota show
ota version
ota_reset
```

OTA target filter:

```text
ota_target show
ota_target token <id|-1>
ota_target name <BSxxxx|->
ota_target prefix <BS|->
ota_target uuid <32hex|->
```

Raw forwarding:

```text
cmd <raw_command>
cmd_all <raw_command>
oneshot <raw_command>
oneshot show
oneshot clear
```

Examples:

```text
cmd VERSION
cmd CIR?
cmd MODE RUN
cmd_all MODE IDLE
```

Tag CIR convenience wrappers:

```text
tag cir status
tag cir off
tag cir compact
tag cir full
tag cir all status
tag cir all off
tag cir all compact
tag cir all full
```

TDMA:

```text
tdma show
tdma hold <0|1>
tdma roster <BSxxxx> motion
tdma profile <BSxxxx> motion
tdma freq motion <hz>
tdma rebalance
tdma clear
```

Current valid TDMA profile for normal capture:

```text
motion
```

Do not use `static`, `roto`, or `wand` as TDMA profiles.

Tag anchor-layout forwarding:

```text
APOS <id> <x_mm> <y_mm> <z_mm>
APOS_TO <BSxxxx> APOS <id> <x_mm> <y_mm> <z_mm>
APOS_COMMIT
APOS_STATUS
APOS_RESET
```

Anchor wrapper commands:

```text
anchor version <A..H|UUID32|all>
anchor role <A..H|UUID32|all> <master|matrix|responder> [cir <0|compact|full>]
anchor role <A..H|UUID32|all> <master|matrix|responder> [cir=<0|compact|full>]
anchor reset <A..H|UUID32|all> <autopos|responder>
```

AutoPos wrapper commands:

```text
autopos status
autopos detach
autopos cir <0|compact|full>
autopos map <A..H> <UUID32>
autopos map show
autopos round <A..H> [sets]
autopos apply
autopos result show
autopos result clear
```

Typical AutoPos hardware sequence:

```text
mode autopos
autopos map show
autopos cir 0
autopos round A 100
autopos apply
autopos status
autopos result show
```

Typical all-anchor responder repair:

```text
mode autopos
anchor role all responder cir 0
anchor version all
```

---

## B2. Tag Firmware BLE Commands

These commands are parsed by the Tag firmware over BLE NUS.

Status:

```text
PING
STATUS
VERSION
TDMA_STATUS
CFG_STATUS
MODE?
HELP
```

Expected version/status style:

```text
VERSION fw=<fw_marker> bs=BS9336 tag=1 mode=RUN pmode=0 anchor_plan=dynamic cir=OFF caps=ota,run...
```

CIR:

```text
CIR?
CIR OFF
CIR COMPACT
CIR FULL
TAG CIR OFF
TAG CIR COMPACT
TAG CIR FULL
```

Mode:

```text
MODE RUN
MODE IDLE
```

Current normal capture uses:

```text
MODE RUN
PMODE=0
```

TDMA config:

```text
TDMA_SET <slot>
CFG TAG=<id> SLOT=<slot> COUNT=<count> MASK=<hex> PERIOD=<ms> ACTIVE=<ms> EPOCH=<ms> GEN=<n> RUN=<0|1> PMODE=<0|3>
CFG_RUN
CFG_STOP
```

Current production path uses `PMODE=0`.

Anchor layout stored on Tag:

```text
APOS <id> <x_mm> <y_mm> <z_mm>
APOS_COMMIT
APOS_STATUS
APOS_RESET
```

Tag OTA control:

```text
OTA_STATUS
OTA_PREPARE
OTA_BEGIN
OTA_CANCEL
REBOOT
```

Typical raw debug through Master_Tag:

```text
device kind tag
ota_target name BSF66F
mode recv
conn
cmd VERSION
cmd CIR?
cmd MODE RUN
cmd TDMA_STATUS
```

Do not use these as current Tag firmware modes:

```text
MODE STATIC
MODE ROTO
MODE WAND
tag mode static
tag mode roto
tag mode wand
```

---

## B3. Anchor Firmware BLE Control Commands

These commands are parsed by the Anchor firmware over the Anchor BLE control characteristic.

Status/config:

```text
HELP
VERSION
SYNC
PENDING LABEL <A..H>
PENDING ROLE <MASTER|MATRIX|RESPONDER>
PENDING GEN <n>
VALIDATE
COMMIT
REBOOT
```

Expected version style:

```text
ANCHOR_FW fw=<fw_marker> bs=<bs_code> uuid=<uuid> label=A role=responder cir=off cfg_valid=1 busy=0 caps=ota,range...
```

Runtime role switching:

```text
RUNTIME MASTER [FORCE|RESTART] [SWEEP <n>] [CIR=0|CIR=COMPACT|CIR=FULL]
RUNTIME MATRIX [FORCE|RESTART] [CIR=0|CIR=COMPACT|CIR=FULL]
RUNTIME RESPONDER [FORCE|RESTART] [CIR=0|CIR=COMPACT|CIR=FULL]
```

Reset / stop:

```text
RESET AUTOPOS
RESET RESPONDER
STOP
```

OTA / DFU:

```text
DFU
ENTER_DFU
OTA
ENTER_OTA
```

Ultrasound diagnostics:

```text
US?
USON [seconds]
USOFF
```

Typical raw commands through Master_Anchor:

```text
mode autopos
cmd VERSION
cmd RUNTIME RESPONDER CIR=0
cmd RUNTIME MASTER SWEEP 100 CIR=0
cmd STOP
```

Preferred wrapper for normal operation:

```text
anchor role all responder cir 0
anchor role A master cir 0
anchor role A responder cir 0
anchor version all
```

---

## B4. Anchor Local UART Role Commands

These are parsed by Anchor local UART role-switch code when that serial path is active.
They are lower-level than the Master_Anchor BLE wrapper.

```text
ROLE?
ROLE
STATUS
US?
USON
USON <seconds>
USOFF
MASTER
MATRIX
RESPONDER
ANCHOR <A..H>
ROLE SET <MASTER|MATRIX|RESPONDER>
ANCHOR SET <A..H>
CONFIG SAVE
REBOOT
```

For normal field operation, prefer Master_Anchor BLE control wrappers instead of local Anchor UART commands.

---

## C. Legacy Commands Not In Current Flow

Do not use these as current AutoPos / Tag capture concepts:

```text
CAL_STATIC
MODE CAL
MODE AOTA
MODE STATIC
MODE ROTO
MODE WAND
tag mode static
tag mode roto
tag mode wand
static_cal
roto_cal
```

If these appear in old logs, docs, or old scripts, treat them as legacy wording unless the current source path explicitly documents them.

---

## D. Expected 10 Hz Output Check

Range rows during 10 Hz TDMA capture should scale like:

```text
rows_per_second = tag_count * 8 anchors * 10 Hz
```

Examples:

```text
1 tag  -> about 80 TR rows/s
4 tags -> about 320 TR rows/s
60 s 4-tag capture -> about 19200 TR rows
```

For current TR2 path, the validated state is:

```text
Tag fw: tr2-rangefullfix-20260623
TDMA profile: motion
Tag PMODE: 0
Range phase CIR: off
Full CIR: deferred USB phase after range capture
```

