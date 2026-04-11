# 2026-04-09 CM BlackBox Runbook

## Goal

Confirm that `CM` output can be produced on the BLE Master (`nRF52840 DK`) side using only the BLE chain:

1. Anchor side runs as pure BLE Anchor.
2. All anchors are switched to `responder`.
3. Tag `BSF66F` is driven into calibration mode through BLE command `MCAL`.
4. BLE Master receives calibration output.

## Final Conclusion

`CM` output is present.

The important blackbox finding is:

- Tag `BSF66F` accepts `MCAL`.
- Tag replies with:
  - `MODE_OK MODE=CAL LIVE=1`
- After that, BLE Master receives `CM` notifications.

However, the current `CM` stream is **not ASCII `CM;...` text**.
It is a **binary calibration packet** with `CM` magic bytes at the front.

Therefore, any host-side script that only looks for text lines matching:

```text
CM;...
```

will falsely conclude that `CM` is missing.

## Runtime Evidence

Session artifact:

- [raw.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/tag115_mcal_direct_20260409_230450/raw.log)

Key lines:

```text
[RECV] BLE cmd sent[0]: MCAL
[RECV] cmd rc=1 payload=MCAL
[RECV] BSF66F notify: MODE_OK MODE=CAL LIVE=1
```

Immediately after `MODE_OK`, the log shows repeated `CM` payloads:

```text
[RECV] BSF66F notify: CM..o[.....`.............]...
[RECV] BSF66F notify: CM..o[.....d.W...L...\\...c...
...
```

This is the stopping condition for the blackbox objective:

- BLE Master has live `CM` output from the tag.

## Why Existing CM Parser Missed It

Current host parser:

- [capture_master_ble_session.py](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/scripts/capture_master_ble_session.py)

Current regex expects textual records of the form:

```text
CM;<ver>;<sweep>;<anchor>;<status>;<raw>;<filt>;<q>;<ok>;<fail>
```

That assumption is wrong for the currently running tag build.

## Actual Tag-Side CM Encoding

Tag-side implementation:

- [uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c#L1008)
- [uwb_tag_ble.c](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/apps/tag/src/uwb_tag_ble.c#L1824)

Relevant constants:

- `UWB_TAG_BLE_CAL_MAGIC0 = 0x43` (`'C'`)
- `UWB_TAG_BLE_CAL_MAGIC1 = 0x4d` (`'M'`)
- `UWB_TAG_BLE_CAL_VERSION = 1`
- `UWB_TAG_BLE_CAL_HEADER_LEN = 5`
- `UWB_TAG_BLE_CAL_RECORD_LEN = 24`

Tag publishes calibration data through:

- `uwb_tag_ble_publish_calibration_range(...)`

which batches records and sends them with:

- `uwb_tag_ble_send_payload(packet, packet_len)`

So the payload is:

- binary
- `CM`-magic-prefixed
- not semicolon-delimited text

## Working BLE-Only Flow

### 1. Make anchors responder

This was verified using host BLE control:

- [session.log](/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/logs/ble_anchor_responder_all_20260409_224907/session.log)

Verified end state:

- `A-H = responder`

### 2. Put BLE Master in Tag receive path

Commands used on 52840 UART:

```text
device kind tag
mode recv
oneshot CFG_STATUS
conn
```

After the scan acceptance fix, BLE Master connects to `BSF66F` and receives live `TS`.

### 3. Send MCAL to connected tag

Direct command used:

```text
cmd MCAL
```

Observed response:

```text
MODE_OK MODE=CAL LIVE=1
```

### 4. Observe CM output

Observed on BLE Master log as binary `CM` packets:

```text
[RECV] BSF66F notify: CM...
```

## Important Operational Notes

1. `BSF66F` currently reports:
   - `CFG tag=1 bs=BSF66F ...`
   - not `tag=115`

2. This does **not** block `MCAL`.
   The successful criterion is:
   - connected peer is `BSF66F`
   - `MCAL` is sent
   - `MODE_OK MODE=CAL LIVE=1` is returned

3. The previous failure was not "no CM".
   It was:
   - wrong gating logic waiting for `tag=115`
   - plus host parser only recognizing textual `CM;...`

## Current Known Good Result

The BLE chain is proven working up to calibration output:

1. BLE Master connects to `BSF66F`
2. `MCAL` is accepted
3. Tag enters calibration mode
4. BLE Master receives `CM` packets

What remains, if needed later, is only:

- host-side decode/parsing of binary `CM` payloads into structured records

