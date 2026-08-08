# COMMAND_TRIGGER_ANALYSIS — §7.1, with base rates

## 1. What was sent, and when, before each onset

Post-onset operator actions (`STALL_READ` retries, the 17:28:20 `RECONNECT`
on BSFEC35) are consequences and are excluded from candidacy; they are
listed in `DOWNTIME_LEDGER.md`.

```
N7 BSF6C53   onset 12:16:23.012
  -210.414 s  COMMAND_TX  BSF6C53 QUEUE PUB HIST=0
  -210.264 s  COMMAND_TX  BSF6C53 QUEUE PUB HIST=1
  -210.105 s  COMMAND_TX  BSF6C53 QUEUE PUB HIST=2
  -209.950 s  COMMAND_TX  BSF6C53 QUEUE PUB HIST=3
  -134.853 s  COMMAND_TX  BSF6C53 CORPSE STATUS
  - 44.820 s  COMMAND_TX  BSF6C53 CORPSE STATUS      <- last
N8 BSF1120   onset 16:53:08.533
  -123.6..-123.2 s  COMMAND_TX  QUEUE PUB HIST=0..3
  -110.436 s  COMMAND_TX  CORPSE STATUS
  - 20.433 s  COMMAND_TX  CORPSE STATUS              <- last
N8 BSF44AD   onset 19:51:58.733
  - 58.397 s  COMMAND_TX  STALL STATUS  (read completed at -58.045 s, elapsed_ms=192, att_err=0)
  - 57.944 .. -57.155 s   COUNTERS / STACKS / QUEUE PUB HIST=0..3
  - 40.802 s  COMMAND_TX  CORPSE STATUS              <- last
N8 BSFEC35   onset 15:46:08.521
  -140.060 s  COMMAND_TX  CORPSE STATUS
  - 50.065 s  COMMAND_TX  CORPSE STATUS
  -  0.340 s  COMMAND_TX  CORPSE STATUS
  -  0.189 s  STALL_READ_START generation=7 timeout_ms=25000, handle=24 err=0
  -  0.096 s  STALL_READ  att_err=0 len=232 v=2 reason=0 armed=1  (SUCCEEDED)
  -  0.095 s  STALL_POOLS count=8 usage=1 sent_cb=0
  -  0.094 s  STALL_READ_DONE generation=7 elapsed_ms=88 terminal=callback
  -  0.034 s  COMMAND_TX  BSFEC35 STALL STATUS       <- last
```

## 2. Base rates and the arithmetic

Inbound operations arrive at one per 22.3–25.3 s per node. Modelling them as
Poisson at that rate:

| window | P(≥1 op by chance, per event) | expected over 4 events | observed events with ≥1 |
|---|---|---|---|
| ≤ 60 s | 0.91 | 3.6 | 4 |
| ≤ 10 s | 0.33 | 1.3 | **1** |
| ≤ 1 s | 0.039 | 0.16 | **1** |
| ≤ 0.05 s | 0.0020 | 0.008 | **1** |

Observing exactly one event with an operation inside 1 s has
P ≈ 1 − (1−0.039)⁴ ≈ **0.15**. Inside 50 ms, P ≈ 0.008 — small, but it is a
single node and the *same* node also supplies the ≤1 s and ≤10 s hits, so
these are not independent tests; there is one coincidence, examined at four
different zoom levels.

## 3. Per-command-class enrichment, whole campaign

| class | executions (all runs, all nodes) | followed by a wedge ≤5 s |
|---|---|---|
| `COMMAND_TX … STATUS` (CORPSE/STALL/QUEUE) | 7 341 | **2** (both BSFEC35, 0.34 s and 0.034 s) |
| `STALL_READ_START` | 2 689 | **2** (both lines of the same BSFEC35 read) |
| `COMMAND_TX … COUNTERS` | 1 378 | 0 |
| `COMMAND_TX … STACKS` | 1 378 | 0 |
| `COMMAND_TX … QUEUE PUB HIST=0/1/2/3` | 1 378 each | 0 |

**Verdict per class: not necessary, not enriched, not absent — unrelated.**
No command class precedes more than one of the four events, and the one that
does precedes it once out of thousands of executions.

## 4. Cadence comparison N5 vs N7 vs N8

The brief asks whether diagnostic polling changed. It did not:

- N5 160.0 ops/node-hour, **0 wedges in 54.0 bh**
- N7 161.2 ops/node-hour, **1 wedge in 5.8 bh**
- N8 142.5 ops/node-hour, **3 wedges in 47.2 bh**

N8 has the **lowest** command cadence and the highest wedge rate. Polling
cadence does not modulate the rate in the observed direction.

## 5. The BSFEC35 coincidence, stated fairly

It is the only piece of trigger evidence in the whole analysis, and it is a
GATT **read that succeeded**: 232 bytes returned, `terminal=callback`,
`elapsed_ms=88`, 94 ms before the node stopped producing. Under H2 that is
awkward — the BT RX WQ had just completed a full request/response cycle
including an `att_pool` allocation on the 30 s-bounded response path. Under
H1 it is a normal ATT buffer round-trip that happened to be the last one.

The honest reading: **a 4/4 coincidence would have been a race candidate; a
1/4 coincidence at p≈0.04 is not.** It is recorded as the single cheapest
v45 trigger arm (§14) and as nothing more.

One asymmetry worth noting for that arm: the wedge did *not* follow the
`STALL STATUS` **read**; it followed the `STALL STATUS` **write** 34 ms
later, which never produced a reply. That write is a GATT write to the
control characteristic — inbound ACL data on the BT RX WQ — and its handler
enqueues into `control_queue`. It is the last thing the master ever got the
node to acknowledge on any path.
