# FINAL_BT_WEDGE_FORENSICS

**B306 BT wedge — consolidated raw-data forensics, 2026-08-08.**
Offline analysis only. No firmware built, flashed or modified; no hardware,
J-Link, BLE command or serial port touched; no raw file altered.

Corpus: 107.12 delivered board-hours over four runs (N5 v43 54.00 bh,
N6 v43 0.11 bh, N7 v43 5.80 bh, N8 v44 47.21 bh): 10.64 M IMU/UWB delivery
records, 1.13 M 1 Hz telemetry/queue/pool/QoS records, 6.46 M listener tag
polls, 1 289 unbiased node-state strobes.

---

## 1. What is proven

1. **Four wedges, four different boards.** N7 BSF6C53 12:16:23; N8 BSFEC35
   15:46:08, BSF1120 16:53:08, **BSF44AD 19:51:58**. The third N8 event,
   never previously pinned, is BSF44AD.
2. **The node stays powered and its UWB half keeps working.** Listener air
   ratio across the onset: 1.024 / 1.048 / 0.959 / 1.096, against 0.001–0.185
   for all eleven depletion events. No overlap, five-fold gap.
3. **The Link Layer stays alive for hours.** Master QoS keeps reporting
   16–20 connection events per second with `crc_error`≈0 and `nak`≈0 for
   615 s, 3 439 s, 16 071 s and 19 669 s after onset. BSF1120 held its link
   **4 h 38 min**, disconnecting only on supervision timeout when the battery
   finally died.
4. **The node's system workqueue never stops.** `telemetry_work_handler()`
   feeds a 30 s `WDT_FLAG_RESET_SOC` watchdog at its first statement
   (main.c:3232) and re-arms itself unconditionally at its last (main.c:3420)
   with no early return in the body. No wedged board ever reset. It therefore
   completed that handler ~5 400 consecutive times on BSF1120 alone.
5. **Both data streams stop inside one connection event** — last IMU and last
   UWB are **0.5–1.4 ms apart** on all four. IMU `seq` steps by exactly 10 and
   UWB `sweep` by exactly 1 through the final twelve records: not one sample
   or sweep was lost before the stop.
6. **The node was completely idle 96 ms before it froze.** BSFEC35's last
   successful stall read: `e == x` (notify worker not in a call),
   `q = 0/0/0` (all three publisher queues empty), `qd = 0/0/0`,
   `td = 0/0/0` (`bt_gatt_notify()` had never once exceeded its 1 200 ms
   accept timeout on that board), `rcc = 61951/0/0/0/0` (every notify in the
   board's life returned 0), all pools full, stall detector silent.
   Backlog residual at the last observable instant is **exactly one record**
   on all four events.
7. **The failure is latching and has no graded tail.** Zero near-misses of the
   wedge phenotype in 107 board-hours at any threshold from 2 s to 20 s. The
   22 near-misses that exist are all inside N8's depletion cascades.
8. **The post-onset master sequence is identical on all four**, to within a
   second: read submitted at +2 s → **times out at exactly 25 001 ms** →
   bearer warning → master unsubscribes at +32 s → every subsequent operation
   returns `-ENOMEM` from then on.
9. **The wedge is 82.9 % of all lost node-time** in four runs — 695 node-
   minutes against 143 for everything else combined. All three boards that
   ever hit `delivering = 0` at full charge were wedges. N5 and N6 have
   **zero** downtime of any cause, so a stable multi-hour 9-node run is
   demonstrably achievable.

## 2. What is ruled out or demoted

- **Gradual pool exhaustion — refuted.** Every pool reads full at every
  scheduled strobe across all 385 366 `FUSION_POOL` records. But this only excludes a
  *progressive* leak: `att_pool` drains in ≈258 ms at the measured
  31.4 notifications/s, and the node-time gap between the last pool strobe
  and the last record is 224–1 415 ms. **A sub-second seizure is permitted at
  every event and is excluded at none.**
- **The pool instrument itself is biased, and that is a finding.**
  `sample_pool_usage()` runs on the system workqueue immediately after that
  same queue has freed the buffers. In N5 the scheduled record reads
  `att_pool 8/8` in 194 255 of 194 255 cases, while the unbiased stall strobe
  on the same boards in the same run reads it **empty 12.3 % of the time**.
  `low_water` is a two-point 1 Hz strobe, not a window minimum — there is no
  sub-second observer anywhere in the firmware.
- **`att_pool` exhaustion as *the cause* — refuted by the fleet.** It is 25×
  more frequent in N5, which had **zero** wedges, than in N8, which had three.
  `bt_gatt_notify()` calls of 100–400 ms are routine (30+ in-run instances on
  healthy boards, up to 4.1 s during DFU) and **every one of them returned**.
  The wedge is not that process running long; it is that process failing to
  recover.
- **A blocked system workqueue — excluded** for every event, by proof 4.
  That kills the "TX processing stopped because the syswq was stuck" and
  "completion callbacks stopped because the syswq was stuck" variants of H1
  and H2 outright.
- **A command or procedure trigger — not found.** 3 of 4 events had no
  inbound operation for 20–45 s against a base rate of one per 22–25 s. No
  command class precedes more than one event out of 1 378–7 341 executions.
  No channel-map update, no CI change, no PHY or DLE event at any onset — all
  four channel-shift values sit inside their same-window control ranges.
- **A node-internal trigger — not found.** 0 of 4 IMU-recovery episodes
  within 60 s of onset, against a 1.4–1.9 % chance rate per 10 s window.
  `uart_restarts` is zero on every board in every run.
- **A counter or timer boundary — not found.** No 16- or 32-bit wrap, no
  TIMER2 71.58 min proximity, no shared cumulative value; the four onsets
  span 8.4× in node uptime and 25× in cumulative notifications.
- **A latency precursor — not found.** Stationary-then-cliff on all four.
  The one candidate (BSF1120, −15 ms IMU step in the last 60 s) is smaller
  than the largest step among its own healthy controls in the same window.
- **H4 (notify-worker/producer deadlock) — refuted** by proof 6.
- **H5 (master / USB / RF / DWM) — refuted.** 7–9 matched controls unaffected
  in every window; master health clean; tag on air; RSSI −66…−71 dBm.
- **H6 (depletion) — refuted for these four**, and it is the correct label
  for eleven other events.
- **The v43/v44 stage traps contributed nothing.** `rings.jsonl` is 0 bytes
  in all four runs; `CORPSE present=0` on every board. **No conclusion in this
  report rests on their silence**, per §0.3.

## 3. Do all four share one phenotype, and where did the third N8 event land?

**Yes — one phenotype, and it is unusually tight.** All four share: powered
node with a working UWB half; a live Link Layer that keeps ACKing for
hours; both data streams stopping within 1.4 ms of each other mid-cadence
with no lost sample; an empty, idle, error-free node pipeline immediately
before; no drain tail; an ATT request that is delivered but never answered,
timing out at exactly 25 001 ms; `-ENOMEM` on every subsequent master
operation; and no reset for as long as the board is observed. **Not one of
the four departs from this on any axis.**

Two elements of the phenotype in circulation are **not** shared, and the
report must not imply they are. "Never re-advertises after a disconnect" and
"only a power cycle restores it" were each tested on **exactly one** board,
BSFEC35 — the only one that was force-disconnected while wedged (17:28:20,
`reason=0x16`, no re-advertise in 120 s of continuous scanning). BSF1120 and
BSF44AD were never disconnected by anyone; their links ended on supervision
timeout when their batteries died 4 h 38 min and 57 min later, and N7 ended
while BSF6C53 was still wedged and still connected. BSFEC35 did eventually
reappear — at 21:13:56, after a brownout power-cycled it, 3 h 46 min later
(`DOWNTIME_LEDGER.md` §3). So "power cycle is the only known cure" is
supported by one observation, not four.

**BSF44AD, 19:51:58, landed cleanly in the wedge class and not near the
depletion boundary.** It wedged 58 minutes before the first depletion
casualty, held its link a further 57 minutes, and its tag polled at ratio
1.096 across the onset. The depletion-contamination risk the brief flagged is
real for the 20:49–21:16 cluster and does not touch this event.

## 4. Ranked remaining internal mechanisms

**Rank 1 — H1′: TX-completion processing stops permanently, and the notify
worker parks in an unbounded allocation.** Moved up by axes (a), (b), (e) and
by proofs 4, 5, 6; moved *down* on its naive form by the `att_pool` census
(axis c′). The precise claim is not "`att_pool` ran out" but "**the thing
that refills `att_pool` stopped**". Both unbounded waits reachable from
`bt_gatt_notify()` — `att.c:747` `K_FOREVER` on `att_pool`, and the
`bt_conn_tx` allocation from the 8-deep `free_tx` FIFO — are released only by
`tx_notify_process()` on the system workqueue, driven only by HCI
Number-Of-Completed-Packets arriving on the MPSL workqueue. That single
dependency explains every observation, including why the ATT *response* also
never appears (it needs the same pool, on a 30 s bound the master's 25 s
timer beats) and why the syswq stays healthy (it has nothing to do, not
nothing it can do). **What it does not explain is why the completion source
stops and never restarts — and nothing in this capture can see that.**

**Rank 2 — H2: BT RX workqueue block.** Kept alive by the near-miss absence
(a `K_FOREVER` either happens or does not) and by the no-re-advertise
observation. Contradicted by the activity census: the BT RX WQ only runs on
inbound work, and 3 of 4 events had none for 20–45 s; `hci_cmd_pool` was
never below 2/2 in 1 289 unbiased strobes; and on BSFEC35 that thread
completed a full ATT read-request/response cycle 96 ms before the freeze.

**Rank 3 — H3: MPSL receive-side / `hci_rx_pool`. Undecidable, as
pre-registered.** `hci_rx_pool` never observed below 10/10 anywhere, and no
holder can exist in this configuration (`BT_MAX_CONN=1`, BT RX WQ idle holds
none, no inbound PDU ever fragments — largest inbound operation is 24 bytes
against MTU 247 / DLE 251). Neither promoted nor demoted. Note H1′ and H3 are
not disjoint: "the MPSL receive path stops delivering events" is an
H3-flavoured *cause* of H1′.

**Refuted: H4, H5, H6** (§2).

## 5. What v45 must capture, and the exposure required

The whole experiment reduces to four counters, in `V45_REQUIREMENTS_DELTA.md`
Δ1: `ncp_events` at `hci_num_completed_packets()` entry, `ncp_handles` in its
per-handle loop, `tx_notify_runs` at `tx_notify_process()` entry, and
`tx_cb_calls` at the callback invocation — each in both the 1 Hz telemetry and
the stall characteristic. If `ncp_events` freezes at onset, H1′ is proven and
the fault is below the host. If it advances while `tx_notify_runs` does not,
the fault is the work-queue hand-off. If both advance and `att_pool` still
empties, the leak is elsewhere and the whole ranking is wrong.

Supporting deltas: move the pool strobe off the system workqueue and make
`low_water` a real minimum (Δ2); add MPSL-side progress counters and add
`SYS_WQ` as a fourth instrumented channel — the baseline three-channel design
omits the thread where both TX processing and completion callbacks actually
live (Δ3); add `notify_timeout_drop` advancing as an early predicate, 18.8 s
ahead of the 20 s freeze test (Δ5); write the corpse to flash rather than
`.noinit`, because the only thing that ever cleared a wedge was the brownout
that destroys `.noinit` (Δ7).

**Deletions from the assumed v45 scope**, each paid for by a measurement:
no `hci_rx_pool` holder sampling (no holder can exist); no command-class
trigger arm and no IMU-recovery trigger arm (no enrichment at any threshold);
no per-stream freeze predicate (the streams stop 0.5–1.4 ms apart).

**Validation exposure.** Pooled rate 1 per 26.8 delivered board-hours
(95 % CI 1-per-9.8 to 1-per-98); N8-only 1 per 15.7. For P(≥2 events) ≥ 0.9:
61 bh at the optimistic rate, **104 bh at the point estimate**, 381 bh at the
pessimistic bound. A 10-node run is capped at ~6 h by battery (≈60 bh), so
**plan three to six full-fleet runs**, and build the capture to survive a run
in which nothing happens.

## 6. The confound, stated as required

> **N8 is simultaneously the only v44 run and the only 10-connection run; the
> v43-to-v44 rate difference cannot be separated from a capacity effect with
> this capture.**

It is in fact worse: N8 is also the only dk-v36 master run. Three candidate
causes — node firmware, connection count, master firmware — are perfectly
confounded in one run, and no re-analysis of this data can separate them.

## 7. Where I am not certain

- **Why the completion source would stop.** Rank 1 is the only hypothesis
  that fits everything, but it is selected by elimination and by mechanism
  plausibility, not by a positive measurement. Nothing here observes
  MPSL/SDC.
- **BSF1120's 224 ms pool gap.** Three events leave comfortable room for a
  sub-second `att_pool` drain; BSF1120's does not (224 ms against a 258 ms
  drain). Either its seizure began within ~34 ms of a strobe that read the
  pool completely full, or its freeze was not at `att_pool`. I cannot
  distinguish these.
- **Whether `hci_cmd_pool` (2 buffers, `K_FOREVER` at hci_core.c:334) ever
  emptied.** Never observed below 2/2, but two buffers can be taken and
  returned entirely between strobes. `INSUFFICIENT`.
- **Whether the BSFEC35 coincidence means anything.** A successful 232-byte
  stall read 96 ms before the freeze, and a control write 34 ms before, on
  1 of 4 events at p ≈ 0.04. I treat it as an accident; a fifth event landing
  the same way would change that.
- **BSFEC35 and BSF44AD wedged 1.0 s apart on the 327.68 s IMU-`seq` cycle**
  (`seq` 3 643 and 3 443). P ≈ 3.7 % for some pair of four. The only numeric
  coincidence in the entire boundary scan; recorded, not believed.
- **The N5 zero.** Real, and statistically unremarkable (P = 0.133). It does
  not support "v43 was fine", and I could not find any condition that
  distinguishes N5 from N7 — polling cadence and scan cadence are identical
  to within 1 %.
- **Master-side counter semantics beyond what the source states.** The
  master's `-ENOMEM` is its own bearer state and is used only as such.

---

**BT WEDGE RAW-DATA FORENSICS COMPLETE — NO FIRMWARE OR HARDWARE CHANGED**
