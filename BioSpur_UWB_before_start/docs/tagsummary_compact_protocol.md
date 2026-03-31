# TagSummary Compact Protocol

## Purpose
This protocol defines the compact runtime BLE TagSummary payload used by unified tag firmware.

Goals:
- reduce per-record payload size
- make BLE bundling practical (`2~3` records per notify)
- keep deterministic fixed-order parsing for host tools

## Single Record Format

```
TS;<ver>;<sweep>;<plan>;<x>;<y>;<z>;<rms>;<max>;<anchors>;<slot_idx>;<slot_cnt>;<src>;<cut>;<reason>;<dt>
```

Example:

```
TS;1;279;t;2562;844;723;4;6;BDEH;3;4;M;0;S;96
```

## Field Order And Meaning

| Index | Field | Meaning | Type / Notes |
|---|---|---|---|
| 0 | `TS` | Record type | literal |
| 1 | `ver` | Protocol version | unsigned int (`1`) |
| 2 | `sweep` | Sweep counter | unsigned int |
| 3 | `plan` | Sweep plan code | enum (`t/f/r/x`) |
| 4 | `x` | X position in mm | signed int |
| 5 | `y` | Y position in mm | signed int |
| 6 | `z` | Z position in mm | signed int |
| 7 | `rms` | Residual RMS in mm | unsigned int |
| 8 | `max` | Max residual in mm | unsigned int |
| 9 | `anchors` | Final solve anchor labels | compact text, no commas (example `BDEH`) |
| 10 | `slot_idx` | Active TDMA slot index | unsigned int |
| 11 | `slot_cnt` | Active TDMA slot count | unsigned int |
| 12 | `src` | Slot source code | enum (`M/S/B`) |
| 13 | `cut` | Slot cut-short flag | `0` or `1` |
| 14 | `reason` | Solve reason code | enum (`S/P/R/C/N`) |
| 15 | `dt` | Motion/update interval in ms | unsigned int, `0` means unavailable |

## Enumerations

### `plan`
- `t` = track
- `f` = full
- `r` = refresh
- `x` = fixed

### `src`
- `M` = MASTER
- `S` = SETTINGS
- `B` = BUILD

### `reason`
- `S` = success
- `P` = pending
- `R` = rejected
- `C` = slot_cut_short
- `N` = none

## Bundle Format

Multiple records in one BLE notify are joined by `|`:

```
TS;...|TS;...|TS;...
```

Receiver rule:
- split notify payload on `|`
- parse each fragment as an independent `TS;...` record
- count each parsed record as one sample

## Receiver Parsing Rules

- delimiter is `;`
- field count must be exactly `16`
- unknown `ver` should be rejected or routed to a compatibility path
- unknown enum values should be mapped to safe fallback (`plan=x`, `src=B`, `reason=N`) if needed
- whitespace is not expected in compact records

## Migration Note (Old Verbose Format)

Old runtime payload (verbose text):

```
TagSummary sweep=279 plan=track xyz=(2562,844,723) rms=4 max=6 anchors=[B,D,E,H] slot=3/4 src=MASTER cut=0 reason=success motion_dt=96
```

New runtime payload (compact):

```
TS;1;279;t;2562;844;723;4;6;BDEH;3;4;M;0;S;96
```

Mapping is one-to-one in semantics. The compact form is the active default for BLE runtime transport and is bundle-friendly.
