# Continuous Fusion capture lifecycle

Stationary validation and comparable production captures use one Fusion CDC
open and one raw writer for the entire run:

```text
COLLECTOR_OPEN
→ RAW_RECORDING_FROM_FIRST_BYTE
→ WARMUP_RECORDING
→ CDC_DRAIN_AND_LIVE_CATCHUP
→ FORMAL_T0_MARKER_IN_SAME_STREAM
→ FORMAL_CAPTURE
→ CLEAN_STOP
```

Startup bytes, decoder fragments, stale records, queue backlog and sequence
gaps are evidence. They are never discarded to manufacture a clean formal
window. Read-only identity observations are non-blocking and occur during
warm-up. The final ten seconds before T0 and the formal window are
command-free.

Live catch-up is not defined by one empty read or a fixed absolute source-age
threshold. The collector continuously drains the serial input and decoder
queue while recording every raw byte. It requires expected source cadence,
continuous IMU/UWB sequences, zero live queue backlog and a stable plateau of
`host_monotonic_ms - Master_receipt_ms`. The plateau threshold is derived from
the recent offset-difference robust distribution and is recorded with the
decision. The evidence must remain continuously valid for 30 seconds after a
minimum 60-second warm-up. At 180 seconds, an unresolved run transitions to
`STARTED_DEGRADED` without closing the stream or attempting recovery.

`FORMAL_T0` records the raw byte offset, decoded record index, last IMU and UWB
source identifiers/timestamps, host monotonic and wall clocks, queue state and
frozen input hashes. Offline formal slicing is exclusive of the last pre-T0
source record and inclusive of the last record observed at the planned T1.
Warm-up faults remain reported but do not fail the formal lossless gate.

No capture-health observation authorizes OTA, reboot, reconnect, configuration
mutation, flashing or physical intervention.
