# CM Output Format

Timestamp: 2026-04-10 Europe/Berlin

## Scope

This document fixes the intended BLE Master CDC output behavior for Tag calibration mode (`MCAL`) with `BSF66F` / Tag 115.

## Required Behavior

When the Tag is in `MCAL`:

1. Only `CM` output is allowed on BLE Master CDC.
2. `TS` output must not appear.
3. Raw binary `CM` notify payload must not be printed to CDC.
4. BLE Master must aggregate calibration records by `sweep`.
5. A CDC `CM` line is emitted only after the same `sweep` has records for all 8 anchors.

## One-Line Rule

One CDC `CM` line must contain exactly one full sweep:

- same `sweep` id for every record in the line
- anchors `0..7`
- 8 calibration records total

Example:

```text
CM;1;2204;0;ok;2581;2581;100;2181;33|CM;1;2204;1;ok;1447;1447;100;2187;24|CM;1;2204;2;ok;4811;4811;100;2164;43|CM;1;2204;3;ok;4117;4117;93;2165;40|CM;1;2204;4;ok;2447;2447;100;2173;40|CM;1;2204;5;ok;1638;1638;100;2180;29|CM;1;2204;6;ok;3636;3636;100;2161;45|CM;1;2204;7;ok;4141;4141;100;2172;33
```

## Record Format

Each record inside the line is:

```text
CM;<version>;<sweep>;<anchor_id>;<status>;<raw_mm>;<filt_mm>;<quality_percent>;<ok_count>;<fail_count>
```

Fields:

- `version`: packet format version
- `sweep`: sweep id
- `anchor_id`: `0..7`
- `status`: `ok|reject|timeout|error`
- `raw_mm`: raw measured distance in millimeters
- `filt_mm`: filtered distance in millimeters
- `quality_percent`: quality estimate in percent
- `ok_count`: cumulative success counter from Tag
- `fail_count`: cumulative failure counter from Tag

## Current BLE Master Behavior

The current 52840 BLE Master build now does the following:

1. Suppresses raw binary `CM` payload from CDC.
2. Collects binary calibration records from BLE notifications.
3. Buffers records by `sweep`.
4. Emits one CDC line only when all 8 anchors for that sweep are present.

## Validation Conditions

A valid `MCAL` capture session must satisfy all of:

1. `MODE_OK MODE=CAL LIVE=1` appears.
2. `TS;` count is `0`.
3. Binary `CM..` garble does not appear.
4. Each emitted `CM` line contains 8 records.
5. All records in the line share the same `sweep` id.

## Operator Note

If a sweep never reaches 8 anchors, BLE Master does not emit that sweep to CDC.
