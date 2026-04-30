# Broadcast b32 Lightweight TDMA Phase 1 Checkpoint

Date: 2026-04-30

## Firmware State

- Anchors A-H: `alt-bcast-a5-g2000-r1000-coop1`
- Tags BSF66F/BS2DCE/BSDC91: `alt-bcast-b32-ltdma10-abce-g2000-r1000`
- Master_Tag carrier: `alt-bcast-b32-ltdma10-abce-g2000-r1000-carrier`
- Master_Tag B120 SNR flashed: `1050070698`
- Tag OTA post-version:
  - BSF66F: `match=True`
  - BS2DCE: `match=True`
  - BSDC91: `match=True`

## What b32 Fixed

b31 still followed the runtime TDMA period/count sent by Master_Tag:

```text
runtime period=40 active=24
```

That made the "lightweight TDMA" loop effectively run at the old 40 ms
period. b32 keeps the runtime slot index assignment but forces the
broadcast lightweight TDMA timing to compile-time constants:

```text
lperiod=10
lcount=10
```

This is visible in every b32 RXG diagnostic line.

## 60s Probe

Log:

```text
logs/alt_bcast_b32_ltdma10_abce_motion_listener_anchorserial_60s_20260430_225903
```

Result:

```text
positions_all = 1430
tf_all        = 6
per_tag       = BSF66F 490, BS2DCE 468, BSDC91 472
RXG           = lperiod=10/lcount=10 on 198/198 rows
```

## 120s Phase 1 Validation

Log:

```text
logs/alt_bcast_b32_ltdma10_abce_motion_listener_anchorserial_120s_20260430_230203
```

Result:

```text
positions_all = 2994
tf_all        = 4
per_tag       = BSF66F 987, BS2DCE 1042, BSDC91 965
```

Per-tag rate:

```text
BSF66F  = 8.22 Hz
BS2DCE  = 8.68 Hz
BSDC91  = 8.04 Hz
```

This meets the Phase 1 criterion:

```text
positions_all >= 2900
```

and slightly exceeds the b29 reference:

```text
b29 positions_all ~= 2981
b32 positions_all  = 2994
```

## Timing Diagnostics

From b32 120s RXG:

```text
RXG rows               = 378
lperiod/lcount         = 10/10 on 378/378 rows
runtime period/active  = 40/24 on 378/378 rows

slot_to_txdone_us:
  min 1586, median 1708, p95 1831, max 2075

txdone_to_rxstart_us:
  min 579, median 701, p95 854, max 1098

rxenable_us:
  min 91, median 122, p95 122, max 213
```

Note: `slot_to_txdone_us` includes delayed TX scheduling plus TX airtime.
It is not a pure `slot_start_to_poll_tx_cmd` metric. If the next phase
needs the true slot-entry overhead, add a separate diagnostic around
`dwt_starttx()` / TX command issue.

## Listener

120s listener result:

```text
UF rows = 5287
UL rows = 62
UF code = 0xe0 poll: 5225, 0xe1 response: 62
UL code = 0xe1 response: 62
```

The listener is active and sees broadcast poll traffic. It only captures a
subset of responses, but Tag-side positions are stable, so Phase 1 does not
block on listener completeness.

## Position Quality

120s RMS:

```text
BSF66F:
  median 18 mm, p95 31 mm, max 167 mm

BS2DCE:
  median 37 mm, p95 163 mm, max 267 mm

BSDC91:
  median 36 mm, p95 152 mm, max 271 mm
```

Filter rejects:

```text
tf_all = 4
reason = rms
```

## Conclusion

Phase 1 is passed.

b32 proves the broadcast-dedicated lightweight TDMA framework works for the
known-good 4-anchor ABCE / g2000 / r1000 setup and reproduces b29-level
3-tag motion output without relying on the old 40 ms runtime TDMA period.

Recommended next step: Phase 2, switch Tag mask to full 8-anchor `0xff`
while keeping anchors on a5 and parameters `g2000/r1000`.
