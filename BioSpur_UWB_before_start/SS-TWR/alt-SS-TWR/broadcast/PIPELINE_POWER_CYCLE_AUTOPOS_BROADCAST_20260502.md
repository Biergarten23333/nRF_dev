# Power-Cycle Pipeline Validation - AutoPos then Broadcast

Date: 2026-05-02

## Scope

No code changes, no OTA, no flash. This run validates the pipeline after a
manual full hardware power cycle:

1. AutoPos Matrix sweep: 100 sets + 10 prewarm.
2. Switch Anchors back to responder mode.
3. Broadcast SS-TWR 3-Tag positioning capture for 600 seconds.

## AutoPos Matrix 100-Set Sweep

Log:

```text
autopos_pipeline/logs/a17_powercycle_full_sweep_100set_20260502_193353
```

Result:

```text
Session guard: matrix ready=8/8
Session finalizer: responder ready=8/8
Requested sets: 100
Prewarm sets: 10
Device sets: 110
Zeros: none
Slow switch rounds: none
Reconnect retry rounds: none
```

Per-master result:

```text
SW-A: sw=100/100 minq=90
SW-B: sw=100/100 minq=89
SW-C: sw=100/100 minq=89
SW-D: sw=100/100 minq=88
SW-E: sw=100/100 minq=90
SW-F: sw=100/100 minq=87
SW-G: sw=100/100 minq=81
SW-H: sw=100/100 minq=88
```

Warnings:

```text
Anchor C low quality as Matrix in round G, minq=81
Anchor H low quality as Matrix in round G, minq=81
```

Interpretation:

AutoPos Matrix survived cold power cycle and completed all A-H rounds without
zero-distance failures. The low-quality warnings are localized to G round pairs
and are not state-machine failures.

## Responder Verification Before Positioning

Log:

```text
logs/pipeline_post_autopos_responder_verify_20260502_193811
```

Result:

```text
sent=8 ready=8/8
```

The 600s capture also ran its own anchor responder preflight:

```text
logs/pipeline_after_autopos_broadcast_3tag_600s_20260502_193908/recv_20260502_193909/anchor_responder_preflight_launch1_20260502_193909
sent=8 ready=8/8
```

## Broadcast 3-Tag Positioning 600s

Log:

```text
logs/pipeline_after_autopos_broadcast_3tag_600s_20260502_193908
```

Capture result:

```text
success=true
duration=600s
positions_all=18002
tf_all=0
cm_all=0
cs_all=0
cr_all=0
cf_all=0
controller_lost=false
controller_recovery_attempts=0
```

Per-tag result:

```text
BSF66F: 6002 rows, 10.003 Hz
BS2DCE: 6001 rows, 10.002 Hz
BSDC91: 5999 rows, 9.998 Hz
```

Per-minute position count:

```text
[1802, 1801, 1799, 1800, 1800, 1799, 1802, 1800, 1800, 1799]
```

RMS summary:

```text
BSF66F: median=102 mm, p95=138 mm, p99=178 mm, max=243 mm
BS2DCE: median=294 mm, p95=448 mm, p99=507 mm, max=645 mm
BSDC91: median=255 mm, p95=436 mm, p99=520 mm, max=789 mm
```

Anchor coverage:

```text
8-anchor solves: 6861
7-anchor solves: 11035
6-anchor solves: 105
5-anchor solves: 1
```

Top anchor sets:

```text
ABCDEFG: 10927
ABCDEFGH: 6861
```

Individual anchor appearances:

```text
A: 17953
B: 17980
C: 17970
D: 17973
E: 17955
F: 17989
G: 17979
H: 6969
```

Listener:

```text
UF rows: 26705
UL rows: 6948
```

## Conclusion

The power-cycle pipeline passed:

- AutoPos Matrix 100-set sweep is stable after cold boot.
- Anchors can switch back to responder mode after AutoPos.
- Broadcast SS-TWR positioning remains stable for 600 seconds with 3 Tags at
  10 Hz/tag.
- No rejected TF rows, no controller loss, no recovery needed.

Remaining observation:

Anchor H appears much less often in broadcast position anchor sets than A-G.
This does not prevent stable 10 Hz positioning, but it should be investigated
later as an RF/placement/coverage/layout issue.
