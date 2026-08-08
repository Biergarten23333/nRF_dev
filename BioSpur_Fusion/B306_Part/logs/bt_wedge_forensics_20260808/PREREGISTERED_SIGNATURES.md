# PREREGISTERED_SIGNATURES — written before §4–§10 were computed

**Committed at P2 completion. Never edited afterwards.** Everything computed
from §4 onward is scored against this table in
`WEDGE_HYPOTHESIS_SCORECARD.md`; where observation contradicts expectation,
the scorecard records it and this file stays as written.

Inputs already available when this was written: `COUNTER_SEMANTICS.md`,
`DATAFLOW_MAP.md`, the registry (4 wedges, 11 depletion, 22 near-misses, all
near-misses inside depletion cascades), and the exposure table. Inputs **not**
yet computed: every §4 event packet, §5 census, §6 latency, §7 triggers, §8
terminal sequences, §9 pool timing, §10 boundaries.

## Hypotheses

| id | mechanism |
|---|---|
| **H1** | Sudden TX-completion stop / ATT–ACL-TX resource seizure. Number-Of-Completed-Packets stops or credits are lost, `chan_sent_cb` never runs, `att_pool` empties, the notify worker parks in the `K_FOREVER` allocation at `att.c:747`. |
| **H2** | BT RX workqueue blockage. `rx_work_handler()` entered and never left — `hci_cmd_pool` `K_FOREVER` (hci_core.c:334), a non-response ATT allocation, or an unbounded wait. |
| **H3** | SDC/MPSL receive-side or `hci_rx_pool` allocation blockage. |
| **H4** | Application notify-worker-only blockage (a node-side defect above the BLE host: the publisher/notify pair deadlocks on its own semaphores). |
| **H5** | External — master, USB/CDC logger, RF, or the DWM producer. |
| **H6** | Depletion / brownout. |

## Structural constraint applied to every row

From `DATAFLOW_MAP.md` §6: `tx_processor`, `conn->tx_complete_work` and
`telemetry_work` (which feeds a 30 s `WDT_FLAG_RESET_SOC` watchdog at its
first statement and re-arms unconditionally at its last) all run on the
**same single-threaded system workqueue**. A wedge that persists for
90+ minutes with zero resets therefore **excludes a blocked system
workqueue**. Every "…because the syswq was stuck" variant of H1/H2/H4 is
dead on arrival, before any of the observations below.

## Signature matrix

Cells give the expected observation **under that hypothesis**. `—` = the axis
cannot distinguish this hypothesis from at least one other; the pairing is
named.

### (a) inbound activity within 10 s of onset — §5, §7.1

| | expectation |
|---|---|
| H1 | **irrelevant.** The TX/completion machinery fires 20–31×/s regardless of inbound; onsets should be distributed uniformly with respect to sparse commands, i.e. coincidence at the base rate. |
| H2 | **enriched.** The BT RX WQ only runs when something inbound arrives. If it is parked inside `rx_work_handler()`, the parking must have been *entered* — so at least one inbound item within a few seconds of onset is close to mandatory. Expect ≥3 of 4 events with inbound activity in the last 10 s, against a base rate of order 1 %. |
| H3 | enriched, same reasoning, plus the *specific* prediction that the inbound item is an HCI **event** rather than ACL data. Not separable from H2 on this axis alone. |
| H4 | irrelevant. Base rate. |
| H5 | onset should coincide with a master-side or logger-side artefact visible in `FUSION_HEALTH` / `cdc_drop_*` / the other nine nodes. |
| H6 | irrelevant. Base rate. |

### (b) precursor ramp vs stationary-then-cliff — §6

| | expectation |
|---|---|
| H1 | **stationary then cliff.** 8 `att_pool` buffers at ~31 notif/s drain in ≈0.26 s; there is no room for a visible latency ramp at record resolution. If anything, a ≤1-record uptick. |
| H2 | stationary then cliff, with the additional prediction that the *last* records may show a brief latency step if the BT RX WQ was already blocking the TX path indirectly. |
| H3 | stationary then cliff. |
| H4 | **ramp expected.** A semaphore deadlock reached through contention should show queue depth and latency rising over seconds to minutes. |
| H5 | fleet-common — the same ramp visible in the matched control nodes. |
| H6 | ramp in the *air* layer and in `reports`/`crc_ok` before the data layer stops. |

### (c) pool-trajectory feasibility — §9

| | expectation |
|---|---|
| H1 | `att_pool` and/or `acl_tx_pool` at **full** in the last delivered sample is *required*, not contradictory: the drain is sub-second and `low_water` is a per-1 s window minimum, so the dip lives in the sample that was never delivered. Prediction: Δt(last pool sample → onset) < 1 s for every event, and the last sample shows `avail == low_water == max`. |
| H2 | same last-sample appearance; no constraint added. — **not separable from H1 on this axis.** |
| H3 | `hci_rx_pool` would have to be held by an identified holder. With `BT_MAX_CONN=1`, BT RX WQ idle (holds none), and inbound PDUs that never fragment, **no holder exists in this capture**. Expected verdict: *undecidable, holder sampling required* — explicitly not a rank change. Holder evidence would be: `hci_rx_pool avail` observed < 10 in any delivered sample on any board, ever. |
| H4 | pools irrelevant — expect them full and steady, and expect `q_hwm_*` to have risen instead. |
| H5 | pools full and steady. |
| H6 | pools full and steady; the node simply stops. |

### (d) terminal stream / packing pattern — §8

| | expectation |
|---|---|
| H1 | the last record should be whatever the publisher's strict `ctl > uwb > imu` order happened to reach; **no shared terminal stream across events**, and the last connection event should contain a *normal* number of notifications. If a shared pattern appears (e.g. all four end on a 150-byte IMU batch, or all four end with ≥2 notifications packed into one connection event) that is a strong, unpredicted H1 refinement. |
| H2 | no prediction — the TX side dies as collateral. |
| H3 | no prediction. |
| H4 | last record should be `ctl`-class disproportionately often (the strict-priority drain re-enters at `q_ctl` after every notify). |
| H5 | terminal record should be truncated or malformed at the master. |
| H6 | terminal record normal, then air stops too. |

### (e) fine-grain simultaneity, incl. telemetry/ctl — §4.x

| | expectation |
|---|---|
| H1 | **all four streams stop within one connection interval (50 ms)**, because the freeze is below the per-stream queues, at the single shared notify worker. Ordering should follow the drain priority only to the extent of one record. `delivered_ctl` stops at the same tick. |
| H2 | same. — **not separable from H1 on this axis.** |
| H3 | same. |
| H4 | same (the shared conduit is the notify worker itself). **This axis cannot separate H1/H2/H3/H4** — it only separates "below the queues" from "above the queues". |
| H5 | streams stop at the master, but `FUSION_QOS` for the connection should show the disturbance too. |
| H6 | all streams stop, and the air stops with them. |

### (f) controller-drain tail — §4.y

| | expectation |
|---|---|
| H1 | **short but nonzero.** At the moment the notify worker parks, up to 8 ATT buffers may already be queued below it; if credits are still available, those drain to the master over ≈8/31 s ≈ 0.26 s. Prediction: 0–8 further records after the last node-side evidence of progress, within ~300 ms. A *long* tail (seconds, tens of records) refutes the "seizure at submission" form. |
| H2 | tail identical to H1 if the TX path was healthy at the instant of the block; that is the expected case. Not separable. |
| H3 | as H1/H2. |
| H4 | **zero tail** — the freeze is at submission, nothing was queued below. |
| H5 | tail should show latency inflation, not a clean stop. |
| H6 | tail may be ragged with retransmissions as the supply collapses. |

### (g) near-miss presence / absence — §2.6 (already computed, see below)

| | expectation |
|---|---|
| H1 | **graded tail expected.** Resource seizure is a race: near-misses where the pool recovered before the 1200 ms `NOTIFY_ACCEPT_TIMEOUT_MS` should be common, and `notify_timeout_drop` (`td=`) should be nonzero somewhere in the fleet. |
| H2 | **no graded tail.** A `K_FOREVER` allocation or a lost wakeup either happens or it does not. |
| H3 | no graded tail. |
| H4 | graded tail expected. |
| H5 | graded tail expected (RF and USB are noisy processes). |
| H6 | graded tail expected and **observed** — the 22 near-misses are all depletion cascades. |

> This is the one axis with a genuinely different prediction per hypothesis,
> and it was computed at P2 before this file was committed: **zero near-misses
> of the wedge phenotype in 107 delivered board-hours.** It is recorded here
> as an input, not as a result of §4+.

### (h) conditions dependence — §11

| | expectation |
|---|---|
| H1 | rate should scale with notification throughput, i.e. with connection count. N8 (10 conns) > N7/N5 (9). |
| H2 | rate should scale with *inbound* work, i.e. command cadence and scan-driven procedures — **measured identical between N5 (0 events) and N7 (1 event)**, so this axis is already weak. |
| H3 | as H2. |
| H4 | rate should scale with producer load, identical across runs. |
| H5 | rate should track master firmware / USB conditions. |
| H6 | rate should track battery state only, i.e. cluster at end of run — **observed exactly, for the depletion class**. |

### (i) node-internal asynchronous events in the pre-onset window — §7.2

| | expectation |
|---|---|
| H1 | irrelevant. Base rate (≈1.8 % per 10 s window for an IMU recovery episode at one per ~9 min). |
| H2 | **enriched if the recovery episode generates BLE-visible work.** It does not obviously do so, so a coincidence here would be an *unpredicted* finding pointing at a shared-resource interaction. |
| H3 | irrelevant. |
| H4 | **enriched.** An IMU chip reset perturbs the producer cadence, which is exactly the input a producer/notify deadlock would need. |
| H5 | irrelevant. |
| H6 | irrelevant. |

## Cells explicitly marked undecidable in advance

1. **"ATT read accepted on-air but never answered" cannot separate RX-side
   death from TX-side death**, because the response leaves via the TX path.
   Only the *conjunction* with "never re-advertised" implicates normal-RX
   processing, and per §0.3 even that is weakened because Disconnect Complete
   splits across two contexts. Encoded: axes (a) and (e) are the RX/TX
   discriminators; the unanswered read is not.
2. **H3 has no identifiable holder in this capture.** Declared undecidable in
   advance (cell (c)/H3). It cannot be promoted by anything in §4–§10; only
   v45 holder sampling can move it.
3. **Axis (e) separates "below the queues" from "above the queues" and
   nothing finer.** Any claim that simultaneity localises to a specific
   thread is a category error and will be scored as such.

## Falsifiers stated in advance

- If any wedge shows **inbound activity in the final 10 s** at above base
  rate across 3+ of 4 events → H2/H3 up, H1 down.
- If **all four** wedges show a **zero controller-drain tail** → H4 up,
  H1/H2/H3 down.
- If any delivered `FUSION_POOL` record **anywhere in 107 board-hours** shows
  `hci_rx_pool avail < 10` → H3 becomes decidable and rises.
- If the last delivered pool sample precedes onset by **more than ~2 s** on
  any event → the sub-second-seizure argument for H1 loses its timing cover
  and H1 must instead predict a dip visible in a delivered sample.
- If a **shared cumulative counter value** (same `notify_ok`, `publisher_count`
  or `sweep` at onset on independent boards with different wall-clock times)
  appears → deterministic bug, all six hypotheses subordinate to it.
- If a **latency ramp** is present in the last 60 s on 3+ of 4 events and
  absent in the matched controls → H4 up, H1/H2/H3 down.
