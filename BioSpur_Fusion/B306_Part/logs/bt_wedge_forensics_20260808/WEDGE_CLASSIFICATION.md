# WEDGE_CLASSIFICATION — one detector, four runs, recomputed from scratch

Detector: `scripts/p2_detect.py` + `scripts/p2_registry.py`. Nothing here uses
`events.jsonl` TICKs, `DATA_PLANE_SILENT` markers, or any derived artifact in
`J_WEDGE/` — those are cross-checks only, because v43 could not see its own
wedge in N7.

## 1. Definitions actually used

| term | definition |
|---|---|
| joint stall | both `FUSION_IMU` and `FUSION_UWB` delivery silent simultaneously for ≥ T |
| `onset_lower` | last delivered record of either stream |
| `onset_upper` | `onset_lower + 120 ms` (one nominal UWB sweep — the first provably missed deadline) |
| wedge candidate | joint stall ≥ **20 s** |
| near miss | joint stall in [T_near, 20 s) that recovered |
| air ratio | listener polls for this node's tag in `[onset, onset+600 s]` ÷ polls in `[onset−600 s, onset]`, recomputed from the five poll-receiving listeners (`scripts/p2_air.py`) |

Classification uses three independent axes and never one alone:
**A** master QoS liveness after onset · **B** listener air ratio ·
**C** node reboot **at onset** (`node_ms` going backwards within −30/+60 s).

> The reboot test must be anchored at the onset, not "anywhere in the stall
> window". A terminal stall can be hours long: BSFEC35's 15:46 event contains
> BSFEC35's own 21:14 depletion reboot 5½ hours later, and the loose form put
> an air-ratio-1.05 wedge into the brownout class on that basis alone. That
> was a live misclassification in the first pass of this analysis, caught and
> fixed.

## 2. Result — 15 joint stalls ≥ 20 s across 107.1 delivered board-hours

| run | node | onset (wall) | dur s | QoS alive after | reboot at onset | air ratio | class |
|---|---|---|---|---|---|---|---|
| N7 | BSF6C53 | 12:16:23 | 614.7 | 614.6 | no | **1.024** | **STEADY_STATE_HOST_WEDGE** |
| N8 | BSFEC35 | 15:46:08 | 19669.2 | 19669.0 | no | **1.048** | **STEADY_STATE_HOST_WEDGE** |
| N8 | BSF1120 | 16:53:08 | 16071.3 | 16071.2 | no | **0.959** | **STEADY_STATE_HOST_WEDGE** |
| N8 | BSF44AD | 19:51:58 | 5341.1 | 3438.8 | no | **1.096** | **STEADY_STATE_HOST_WEDGE** |
| N8 | BSF3C79 | 20:49:31 | 528.6 | 528.5 | no | 0.123 | DEPLETION_OR_BROWNOUT |
| N8 | BSF6C53 | 20:49:40 | 65.1 | 64.8 | **yes** | 0.185 | DEPLETION_OR_BROWNOUT |
| N8 | BSF6C53 | 20:50:45 | 1814.6 | 280.6 | no | 0.167 | DEPLETION_OR_BROWNOUT |
| N8 | BSF31CC | 20:51:43 | 1756.1 | 3.3 | no | 0.008 | DEPLETION_OR_BROWNOUT |
| N8 | BSF3C79 | 20:59:31 | 962.8 | 962.7 | no | 0.016 | DEPLETION_OR_BROWNOUT |
| N8 | BSFC2CC | 21:02:25 | 34.7 | 34.3 | no | 0.128 | DEPLETION_OR_BROWNOUT |
| N8 | BSFC2CC | 21:03:00 | 1079.7 | 852.0 | no | 0.115 | DEPLETION_OR_BROWNOUT |
| N8 | BSFAA61 | 21:06:44 | 855.3 | 3.7 | no | 0.008 | DEPLETION_OR_BROWNOUT |
| N8 | BSF8BC4 | 21:10:10 | 649.1 | 3.2 | no | 0.001 | DEPLETION_OR_BROWNOUT |
| N8 | BSFEC35 | 21:15:19 | 340.8 | 159.2 | no | 0.114 | DEPLETION_OR_BROWNOUT |
| N8 | BSF3C79 | 21:16:32 | 267.6 | 3.7 | no | 0.040 | DEPLETION_OR_BROWNOUT |

Full rows, with the pre/post poll counts behind every ratio, in
`WEDGE_EVENTS.csv` / `.json`.

**The air ratio separates the two classes completely, with no overlap and a
gap of a factor of five**: wedges 0.959–1.096 (n=4), depletion 0.001–0.185
(n=11). Moving the 0.70 threshold anywhere in [0.25, 0.90] changes nothing.
`RF_OR_DISCONNECT` and `PRODUCER_SPECIFIC_STOP` are never reached by any
joint stall.

### The third N8 event is BSF44AD, 19:51:58

Prior reporting never pinned it. It is a wedge on all three axes and is the
**cleanest depletion discriminator in the set**: it wedged at 19:51:58,
almost an hour before the first depletion casualty (BSF3C79, 20:49:31), and
its master QoS then kept reporting for a further **57 minutes** — a depleted
cell cannot hold a supervised BLE link for 57 minutes. Its tag also kept
polling *harder* after onset than before (ratio 1.096).

## 3. Near-miss census (§2.6) — the discriminator that matters

Threshold sensitivity, all runs, joint stalls at each T_near:

| T_near | N5 (54.0 bh) | N7 (5.8 bh) | N8 (47.2 bh) |
|---|---|---|---|
| 0.5 s | 835 | 3 | 39 |
| 1.0 s | **7** | 1 | 36 |
| 2.0 s | **0** | 1 | 36 |
| 4.0 s | **0** | 1 | 36 |
| 8.0 s | **0** | 1 | 35 |

0.5 s is inside normal jitter (a 0.5 s UWB gap is four missed sweeps and is
routine), so the floor is set at **2 s** — comfortably below the 4 s
supervision timeout, comfortably above the healthy tail.

At T_near = 2 s the entire near-miss population is **22 events, and every one
of them lies inside an N8 depletion cascade** (BSF6C53 20:47–20:50, BSFC2CC
21:01–21:02, BSFEC35 21:14–21:15) — repeated 8–18 s stalls with reboots,
i.e. brownout cycling. `NEAR_MISS_EVENTS.csv` classifies all 22 as
`DEPLETION_OR_BROWNOUT`.

> **There is not one single near-miss of the wedge phenotype in 107 delivered
> board-hours.** The four wedges are 614 s, 5341 s, 16071 s and 19669 s long;
> below them, nothing. This is a latching, all-or-nothing failure with no
> graded tail — which is what a state-corruption / lost-wakeup mechanism looks
> like and is *not* what a congestion-class mechanism looks like. §3 axis (g)
> is answered, and it discriminates.

The seven sub-2 s joint stalls in N5 are all on **BSFAA61**, 1.00–1.39 s, and
that one board also delivered only 149 346 IMU records against ~432 000
expected for the run — an IMU producer defect (181 `IMU_ONLY` gaps in
`SINGLE_STREAM_CENSUS.csv`), not a BLE precursor. All seven are shorter than
the 1200 ms `NOTIFY_ACCEPT_TIMEOUT_MS`, so none of them even reached the
point where the node would have counted a `notify_timeout_drop`.

## 4. Single-stream census

181 single-stream stops across all runs, of which 178 are `IMU_ONLY` on
BSFAA61 (N5) — one board, one producer. The three others are the tail of
terminal depletion events. **No wedge is preceded by a single-stream stop on
the same board.** `SINGLE_STREAM_CENSUS.csv`.

## 5. Exposure and rate

Delivered board-hours = Σ over nodes of (last delivered − first delivered)
minus non-terminal stall time:

| run | nodes | delivered bh | wedges |
|---|---|---|---|
| N5 v43 | 9 | 54.00 | 0 |
| N6 v43 | 10 | 0.11 | 0 |
| N7 v43 | 9 | 5.80 | 1 |
| N8 v44 | 10 | 47.21 | 3 |
| **total** | | **107.12** | **4** |

Pooled: **1 wedge per 26.8 delivered board-hours**. N8 alone: 47.21/3 =
**1 per 15.7**. Both figures previously in circulation are correct; they
differ only in denominator, and the report must always say which.
