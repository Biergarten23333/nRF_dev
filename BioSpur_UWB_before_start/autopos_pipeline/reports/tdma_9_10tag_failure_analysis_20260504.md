## TDMA 9/10 Tag Failure Analysis - 2026-05-04

### Runs Compared

- 8 Tag baseline: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_8tag_motion180_b66_cmdall_20260504_110804`
- 10 Tag stress: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_10tag_motion180_tronly_b66_cmdall_20260504_113353`
- 9 Tag stress after BS10CE power-off: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_9tag_motion180_tronly_b66_cmdall_20260504_114121`

### Throughput Summary

| Run | Tags | TR rows | Avg sweep Hz/tag | TS rows |
|---|---:|---:|---:|---:|
| 8 Tag | 8 | 110952 | 9.63 | 10248 |
| 10 Tag | 10 | 64480 | 4.48 | 4826 |
| 9 Tag | 9 | 45384 | 3.50 | 3494 |

The 8 Tag run proves the UWB/broadcast TDMA path can sustain near-10 Hz per tag. Removing BS10CE did not improve the system; the 9 Tag run was worse than the 10 Tag run.

### Per-Tag TR Sweep Rate

#### 10 Tag

| Tag | TR sweep Hz | Valid % | TS Hz |
|---|---:|---:|---:|
| BSF66F | 5.13 | 51.3 | 2.74 |
| BS2DCE | 5.02 | 80.4 | 4.26 |
| BSDC91 | 5.08 | 60.3 | 3.19 |
| BSE88E | 5.15 | 59.7 | 2.93 |
| BS6F3A | 5.02 | 60.4 | 3.34 |
| BSF8E0 | 0.93 | 65.0 | 0.66 |
| BS8251 | 3.48 | 34.3 | 0.94 |
| BS10CE | 4.89 | 54.7 | 2.41 |
| BS7724 | 5.05 | 58.1 | 3.35 |
| BS1396 | 5.02 | 55.9 | 2.99 |

#### 9 Tag

| Tag | TR sweep Hz | Valid % | TS Hz |
|---|---:|---:|---:|
| BSF66F | 4.47 | 69.9 | 3.27 |
| BS2DCE | 4.09 | 54.2 | 2.30 |
| BSDC91 | 4.10 | 50.6 | 2.26 |
| BSE88E | 3.62 | 41.7 | 1.45 |
| BS6F3A | 4.04 | 53.1 | 2.51 |
| BSF8E0 | 0.87 | 56.1 | 0.40 |
| BS8251 | 2.33 | 63.7 | 1.59 |
| BS7724 | 4.05 | 58.7 | 2.51 |
| BS1396 | 3.94 | 68.9 | 3.13 |

### BLE Link Evidence

| Run | Disconnected | BLE write error | CFG send failed | reason=0x08 | reason=0x3e | CONNECT pending | Connected |
|---|---:|---:|---:|---:|---:|---:|---:|
| 10 Tag | 66 | 24 | 54 | 38 | 8 | 198 | 76 |
| 9 Tag | 94 | 38 | 116 | 52 | 26 | 294 | 110 |

The 9 Tag run had more disconnects, more CFG send failures, and more reconnect attempts than the 10 Tag run. This is not a clean UWB capacity failure. It is a BLE central/transport/reconnect storm.

### Cleanup State

After the 9 Tag run, cleanup still saw residual TS/TR output. A manual stop sequence:

```text
tdma hold 1
cmd_all MODE AOTA
tdma clear
```

still produced 42 TS/TR rows in the following 6 seconds, then most Tags reported `anchor_ota`. BSF8E0 immediately disconnected and reconnected again. This confirms stop/cleanup is not yet deterministic under high BLE load.

### Root Cause Judgment

The current bottleneck is not the UWB broadcast sweep itself. The 8 Tag baseline proves the UWB path can run near 10 Hz/tag.

The failure mode at 9-10 Tags is the single Master_Tag B120 acting as BLE central while receiving large text TR notifications from many Tags. At 10 Tags, the expected load is about 100 TR notifications/s, each carrying 8 anchor ranges as ASCII, plus TS/TD/RXG/TDIAG/CFG traffic. Under this load the central enters write errors, CFG failures, disconnects, and reconnect storms.

BS10CE is not the culprit. It was good in the 8 Tag baseline, and removing it made the system worse.

### Suspect Tags

- BSF8E0 is the worst link/load participant in both 9 and 10 Tag tests.
- BS8251 is also weak in the 10 Tag run and degrades further in 9 Tag.
- BSE88E has low valid rate and weaker TS quality, but it is not the primary throughput collapse by itself.

### Next Actions

1. Power-cycle Master_Tag and all Tags before the next high-count run.
2. Re-run a clean 8 Tag baseline to prove the central recovered.
3. Add Tags one by one and stop when reconnect storms begin.
4. For 10 Tag production, prefer one of:
   - split across two Master_Tag B120 centrals, 5+5, or
   - implement a TR-only low-bandwidth mode: disable TS/TD/RXG/TDIAG during stress, batch or binary-pack TR, and add BLE FIFO backpressure.
5. Fix stop/cleanup to repeat `cmd_all MODE AOTA` until all connected Tags are silent for a verified window.

