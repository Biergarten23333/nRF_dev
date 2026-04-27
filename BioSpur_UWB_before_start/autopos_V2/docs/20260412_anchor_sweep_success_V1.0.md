# 2026-04-12 Anchor Sweep Success V1.0

Timestamp: 2026-04-12 Europe/Berlin

## Scope

This document records the full BLE-side production flow that is now known to work end-to-end on the `nRF52840 DK` master-control path:

1. Run `A-H` Anchor Sweep and obtain the bidirectional matrix
2. Convert all Anchors `A-H` into `responder`
3. Connect Tag115 (`BSF66F`) over BLE
4. Let Tag115 communicate with all 8 Anchors
5. Receive `CM` lines containing per-anchor distance and quality on the BLE Master CDC

This document is intentionally operator-oriented and detailed. It is the reference for repeating the full flow without guessing intermediate state transitions.

## Goal

At the end of the full flow, the BLE Master CDC must show continuous `CM` lines like:

```text
[RECV] BSF66F notify: CM;1;1152;0;ok;2912;2912;100;640;513|CM;1;1152;1;ok;1508;1508;100;532;621|CM;1;1152;2;ok;4126;4126;100;180;973|CM;1;1152;3;ok;4260;4260;100;163;990|CM;1;1152;4;ok;2550;2550;100;139;1014|CM;1;1152;5;ok;1566;1566;100;120;1033|CM;1;1152;6;ok;3838;3838;100;106;1047|CM;1;1152;7;ok;4307;4307;100;93;1060
```

This means:

1. Tag115 is connected to the BLE Master
2. Tag115 is in calibration mode
3. All 8 Anchors are participating as responders
4. Each CDC `CM` line contains:
   - anchor id `0..7`
   - range
   - quality
   - per-anchor status (`ok` / `timeout`)

## Hardware Used

### BLE Master

- Board: `nRF52840 DK`
- CDC port:
  - `/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00`

### Tag

- Tag115
- BLE name:
  - `BSF66F`

### Repo Root

- `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start`

## Stable Anchor UUID Map

Use this exact map for all `AUTOPOS` and Anchor control operations:

- `A = 4DC6B8187E33803AE8601FB0D7992B96`
- `B = B9179575C776C98F1CB132DD6EDC6223`
- `C = CEE5A7EFCB35F8A56B430047629F5309`
- `D = AB14CCA262A092E70EB26B0ACB0A394B`
- `E = A892AF05DD59CF0D0D3408AD74F364A1`
- `F = 840C68591E90019821AACFF1B73AAA34`
- `G = B3087BC3D87CCCD316AEDC6B71D6677F`
- `H = 1EABFBEC28B8053FBB0D5C448112AE93`

## Phase 1: Anchor Sweep (A-H Matrix)

### Purpose

This phase rotates `A..H` as the active master and produces the `SW-X` matrix data.

Important:

- This phase does not require pre-setting all Anchors to `responder`
- During sweep, the system manages role changes internally
- `responder` is only needed later for Tag115 stage

### Quiet Tag Requirement (Tag115 Powered On)

If Tag115 (`BSF66F`) is powered on during sweep, it must be quarantined first so it stays online but does not participate in UWB ranging.

Required behavior:

1. Tag remains connected/online over BLE
2. Tag enters `MODE AOTA` before sweep starts
3. `STREAM OFF` is attempted as best-effort log suppression only

Important:

- The isolation command is `MODE AOTA`.
- `STREAM OFF` does not isolate UWB by itself.
- Some deployed Tag builds may return `UNKNOWN_CMD` for `STREAM OFF`; this does not block sweep if `MODE AOTA` succeeded.

Manual UART sequence (if needed):

```text
mode recv
device kind tag
ota_target name BSF66F
conn
cmd MODE AOTA
cmd STREAM OFF
```

Expected key marker:

```text
BSF66F notify: MODE_OK MODE=AOTA LIVE=1
```

Script default:

- `run_autopos_sweep_loop.py` now performs this quiet-tag precheck automatically before each round (default `--quiet-tag-name BSF66F`).

### Command

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start

python3 scripts/run_autopos_sweep_loop.py \
  --port /dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_8D3AC42D4D90FAE8-if00 \
  --order ABCDEFGH \
  --sw-sets 10 \
  --verbose 0 \
  --out-dir logs/live_autopos_sweep_loop_A_to_H_10sets_reuse_wait_$(date +%Y%m%d_%H%M%S)
```

### Expected Success Markers

For each round:

1. `AUTOPOS apply success: master=<X>`
2. `AUTOPOS sweep listen attach: master=<X>`
3. `SW-<X>,...`

For full completion:

1. `summary.json` exists
2. `rounds.A ... rounds.H` all show `success=true`

### Known Good Artifact

- [summary.json](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/live_autopos_sweep_loop_A_to_H_10sets_reuse_wait_20260412_185506/summary.json)

### Notes

- `warmup_min_quality` is informational only
- low-quality early `SW-X` lines are still valid sweep outputs
- the flow should not be blocked only because some pair quality ramps up

## Phase 2: Convert All Anchors to Responder

### Purpose

After matrix generation is complete, all Anchors must enter Tag-compatible `responder` role so Tag115 can range against them.

### What Was Actually Used on 2026-04-12

The low-level helper:

- `scripts/ble_anchor_control.py`

can configure a single Anchor, but it does not contain the stronger `busy -> STOP -> busy=0 -> COMMIT` handling used by the 52840 master-control firmware.

This mattered because some Anchors, especially the ones still carrying active sweep runtime state, returned:

```text
COMMIT -> ERR:BUSY
```

So the reliable path used on this date was:

1. keep using the 52840 CDC
2. switch into `AUTOPOS`
3. load the UUID map
4. issue the top-level command:

```text
anchor role all responder
```

The current `master_control` firmware internally handles:

1. target Anchor connection
2. `busy=1` polling
3. `STOP`
4. wait for `busy=0`
5. `R RESPONDER`
6. `VALIDATE`
7. `COMMIT`
8. `REBOOT`
9. reconnect and verify

### Operator UART Sequence

Open the BLE Master CDC and send:

```text
status
mode autopos
device kind anchor
autopos map A 4DC6B8187E33803AE8601FB0D7992B96
autopos map B B9179575C776C98F1CB132DD6EDC6223
autopos map C CEE5A7EFCB35F8A56B430047629F5309
autopos map D AB14CCA262A092E70EB26B0ACB0A394B
autopos map E A892AF05DD59CF0D0D3408AD74F364A1
autopos map F 840C68591E90019821AACFF1B73AAA34
autopos map G B3087BC3D87CCCD316AEDC6B71D6677F
autopos map H 1EABFBEC28B8053FBB0D5C448112AE93
anchor role all responder
```

### Expected Success Markers

Per-anchor:

```text
anchor role rc=0 target=A uuid=... role=responder
anchor role rc=0 target=B uuid=... role=responder
...
anchor role rc=0 target=H uuid=... role=responder
```

Final:

```text
anchor role rc=0 target=all role=responder
```

### What You May See During This Step

You will often see:

```text
AUTOPOS anchor <X> busy; requesting runtime stop attempt=1/3
Anchor ctrl sent[0]: STOP uuid=...
AUTOPOS anchor <X> stopped for config window
```

This is expected. It means the current firmware is explicitly draining residual sweep runtime before allowing role commit.

### Known Good Artifact

- [tag115_responder_stage_20260412_master_control.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_responder_stage_20260412_master_control.log)

## Phase 3: Switch Back to RECV / Tag Path

### Purpose

Once all Anchors are in `responder`, the BLE Master stops acting as an Anchor control plane and returns to Tag receive mode.

### Operator UART Sequence

```text
status
device kind tag
ota_target name BSF66F
mode recv
```

Important:

- `mode recv` may trigger a reboot if you are currently in `AUTOPOS`
- this is normal
- after reboot, reopen the same CDC port and continue

### Expected Success Markers After Reboot

```text
[RECV] Control mode loaded: RECV
[RECV] UART control ready
```

## Phase 4: Connect to Tag115 / BSF66F

### Purpose

Re-establish the BLE NUS session to Tag115.

### Operator UART Sequence

After the `RECV` reboot is complete:

```text
status
conn
```

### Expected Success Markers

Typical markers:

```text
[RECV] Connected[0]: ... bs=BSF66F
[RECV] DISC complete[0]: link=nus ...
[RECV] BLE[0] link ready
[RECV] BSF66F notify: CFG_OK ...
```

### Important 2026-04-12 Observation

In the 2026-04-12 live session, `BSF66F` was already connected and immediately resumed `CM` output after the controller returned to `RECV`.

That means:

- the Tag was already in an active calibration session
- the BLE reconnect succeeded
- explicit `MCAL` re-trigger was not required to get data again

So if you already see stable `CM` lines after reconnect, treat that as success and do not force extra commands unless needed.

## Phase 5: Ensure Tag Stream / Calibration Mode

### Nominal Command Sequence

If Tag115 is connected but not yet producing `CM`, use:

```text
cmd STREAM ON
oneshot MCAL
```

If the one-shot path is not appropriate for the current live link, you can directly send:

```text
cmd MCAL
```

### Expected Success Markers

Preferably:

```text
BSF66F notify: STREAM_OK ON
BSF66F notify: MODE_OK MODE=CAL LIVE=1
```

Then:

```text
BSF66F notify: CM;...
```

### Important 2026-04-12 Observation

During the verified session:

- `CM` lines were already flowing before `cmd STREAM ON`
- sending `cmd STREAM ON` returned:

```text
cmd rc=-128 payload=STREAM ON
```

but `CM` streaming continued normally

Interpretation:

- this was not a blocker
- the Tag session was already alive and already producing calibration output
- the practical success criterion is the live `CM` stream itself

So for operators:

1. If `CM` is already flowing, do not overreact to a late `STREAM ON` failure
2. Treat the active `CM` stream as the ground truth success indicator

## Phase 6: Success Criteria for Tag115 Stage

The Tag115 stage is considered successful when all of the following are true:

1. Tag115 / `BSF66F` is connected over BLE
2. CDC continuously prints `CM` lines
3. Each `CM` line contains all anchors `0..7`
4. Each anchor section contains distance and quality
5. The stream is continuous enough for capture/logging

### Example of a Good CM Line

```text
[RECV] BSF66F notify: CM;1;1146;0;ok;2900;2900;100;634;513|CM;1;1146;1;ok;1550;1550;100;526;621|CM;1;1146;2;ok;4086;4086;100;174;973|CM;1;1146;3;ok;4279;4279;100;157;990|CM;1;1146;4;ok;2582;2582;100;133;1014|CM;1;1146;5;ok;1545;1545;100;115;1032|CM;1;1146;6;ok;3848;3848;96;100;1047|CM;1;1146;7;ok;4319;4319;100;87;1060
```

This line proves:

1. all 8 anchors are being reported
2. `distance` is present
3. `quality` is present
4. the Tag is successfully ranging against the responder set

## Known Good Artifacts from 2026-04-12

### Responder Conversion

- [tag115_responder_stage_20260412_master_control.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_responder_stage_20260412_master_control.log)

This log shows:

1. `anchor role all responder`
2. per-anchor `STOP`
3. per-anchor `COMMIT`
4. final:

```text
anchor role rc=0 target=all role=responder
```

### Tag115 / CM Session

- [tag115_mcal_stage_20260412.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_mcal_stage_20260412.log)

This log shows:

1. switch from `AUTOPOS` back to `RECV`
2. Tag115 reconnect
3. sustained `CM` streaming
4. multiple full 8-anchor `CM` lines

## Troubleshooting

### 1. `COMMIT -> ERR:BUSY` While Setting Responder

Meaning:

- the Anchor is still in active runtime work from prior sweep/master state

Reliable fix:

- do not rely only on the low-level helper
- use the 52840 top-level flow:

```text
mode autopos
device kind anchor
autopos map ...
anchor role all responder
```

because this path already contains:

1. `STOP`
2. wait `busy=0`
3. reconnect and verify

### 2. `Disconnected ... reason=0x3e` During Anchor Control Reconnect

This can occur during reconnect attempts after reboot/role changes.

It is acceptable as long as:

1. the controller keeps retrying
2. the target Anchor eventually reaches:

```text
ANCHOR_CTRL[0] link ready
```

and later:

```text
anchor role rc=0 target=<X> ... role=responder
```

### 3. `cmd rc=-128 payload=STREAM ON`

If this happens but `CM` is already flowing:

- do not treat it as fatal
- the Tag is already active enough for the current objective

If this happens and there is no `CM`:

1. reconnect `BSF66F`
2. try:

```text
cmd MCAL
```

3. watch for:

```text
MODE_OK MODE=CAL LIVE=1
```

### 4. No `CM` Lines After Tag Reconnect

Check in this order:

1. Anchors really are all `responder`
2. Tag is the intended `BSF66F`
3. BLE Master is in `RECV`
4. Tag link is actually connected
5. `MCAL` was sent, or Tag stayed in CAL mode from the previous session

## Minimal Operator Checklist

### Anchor Sweep

1. run `run_autopos_sweep_loop.py`
2. verify `summary.json` all success

### Responder Conversion

1. `mode autopos`
2. `device kind anchor`
3. load all `autopos map`
4. `anchor role all responder`
5. wait for:

```text
anchor role rc=0 target=all role=responder
```

### Tag115 / CM

1. `device kind tag`
2. `ota_target name BSF66F`
3. `mode recv`
4. reopen CDC after reboot
5. `conn`
6. if needed:
   - `cmd STREAM ON`
   - `oneshot MCAL`
   - or `cmd MCAL`
7. confirm continuous `CM` lines with anchors `0..7`

## Bottom Line

The full flow is now validated on 2026-04-12:

1. Anchor Sweep completes and matrix is obtained
2. All Anchors can be moved to `responder`
3. Tag115 / `BSF66F` reconnects over BLE
4. Tag115 communicates with all 8 Anchors
5. BLE Master CDC receives per-anchor distance and quality in `CM`

For practical operation, the real final success signal is:

```text
continuous full 8-anchor CM lines on the 52840 CDC
```
