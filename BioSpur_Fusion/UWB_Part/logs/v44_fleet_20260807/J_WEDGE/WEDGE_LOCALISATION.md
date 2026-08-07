# Wedge localisation — the v44 fleet run caught it twice, and the trap could not see it

**Run** `v44_fleet_20260807` (N8) · **date** 2026-08-07 · **status** run live, 8/10 delivering

This report exists because the N8 run produced a result the batch was not designed
to produce. The run's stated purpose was to roll v44 out and field-test the 20 s
threshold during depletion. Instead two boards wedged in *steady state*, the v44
trap stayed silent through both, and a single authorised intervention converted
that silence from an ambiguity into a localisation.

The reasoning chain is the deliverable. The conclusion is only as good as the
steps, so every step below states what it rules out, not just what it suggests.

---

## 1. What happened

| time | event |
|---|---|
| 15:14:57 | Run opens. **10/10 delivering at 8.333 Hz** — the first time this project has run ten nodes at the TDMA ceiling. |
| 15:46:10 | **BSFEC35 goes silent.** Link stays up. |
| 15:46:35 | Its GATT read times out at 25001 ms. No ATT response was ever produced. |
| 15:51:08 | First `-ENOMEM`. Every command to it fails identically from here on. |
| 16:53:10 | **BSF1120 goes silent.** Same signature, 67 minutes later. |
| 17:28:20 | `BSFEC35 RECONNECT` sent (authorised intervention, §5). |
| 17:30:20 | `outcome=timeout, connect_ms=0`. It never re-advertised. |
| 17:36 | 8 delivering, 9 linked. BSF1120 still wedged and still connected. |

Neither board ever reset. Neither produced a corpse. `reboot_owner=0` on every
reachable board in the fleet.

---

## 2. The observations, and what each one kills

### 2.1 The stop is instantaneous, with no resource precursor

BSF1120's last records are ordinary: IMU `seq=18209`, UWB `sweep=49925`, both at
their normal cadence. The next millisecond produces nothing. No ramp, no partial
degradation, no error counter moving first.

All eight of the node's net_buf pools read **full** on the last record before
silence — and `avail == low_water == max` means they never dipped at any point in
the board's life, not merely that they were full at the end:

```
11597b73:8/8  858969d7:8/8  a14875f8:3/3  2de570ea:1/1
39b3fc03:2/2  20588eb5:10/10  ef427c73:4/4  27b70977:1/1
```

Byte-identical on both boards.

**CORRECTED (L3/A1). The earlier version of this section said the Bluetooth
host's own RX pool was not among the eight and that the app could not see it.
That was wrong, and §6 and §8 were built on it.**

`pool_name_hash()` ([`firmware/src/main.c:498`](../../../../B306_Part/firmware/src/main.c#L498))
is FNV-1a/32. Hashing the candidate names identifies the row directly:

| hash | pool | reported | expected |
|---|---|---|---|
| `20588eb5` | **`hci_rx_pool`** | **10/10** | `BT_BUF_RX_COUNT = MAX(EVT 10, ACL 6) = 10` |
| `11597b73` | `acl_tx_pool` | 8/8 | `BT_BUF_ACL_TX_COUNT=8` |
| `858969d7` | `att_pool` | 8/8 | |
| `a14875f8` | `discardable_pool` | 3/3 | |
| `39b3fc03` | `hci_cmd_pool` | 2/2 | |
| `2de570ea` | `fragments` | 1/1 | `BT_CONN_FRAG_COUNT=1` |
| `27b70977`, `ef427c73` | INSUFFICIENT — not identified | 1/1, 4/4 | |

`20588eb5` is **the pool `bt_buf_get_rx()` allocates from**, its count matches
`BT_BUF_RX_COUNT` exactly, and it was being sampled once a second throughout.

> **Kills:** any slow-leak or gradual-exhaustion story — **including in the host
> RX pool**, which was visible all along and read 10/10 to the last record. The
> §6 mechanism is not excused by an unobserved pool; it has to survive this
> measurement, and §6 now states the constraint numerically.

### 2.2 The peer's controller stays alive for the entire wedge

Master-side QoS, sampled once a second for 90+ minutes after BSFEC35 went silent:

```
reports=20  crc_ok=17..21  crc_error=0..2  nak=0  event_gaps=0
```

Twenty connection events per second, valid packets received from the peer in
essentially all of them, no NAKs, no gaps.

> **Kills:** radio, range, interference, and the peer's link layer. The
> controller on the wedged node is executing the connection correctly. Whatever
> is stuck is above the LL and below the application.

### 2.3 An ATT request reaches the node and is never answered

This is the observation N7 did not have. At the moment of silence the driver
issues one `STALL READ`. On both boards it was **accepted for transmission**
(`err=0`) and then timed out at exactly 25001 ms with no response.

Contrast with the same read three minutes earlier on the same board:

```
16:51:04  gen 20  elapsed_ms=71   att_err=0  e=183855 x=183855  ← healthy
16:53:10  gen 21  submitted err=0
16:53:35  gen 21  terminal=timeout  elapsed_ms=25001            ← nothing came back
```

> **Kills:** the "producer-side stall" reading. If only the app's publisher were
> blocked, the Bluetooth host would still answer ATT reads — that path does not
> go through the publisher. Both directions of the host are dead, not just the
> outbound one.

### 2.4 `-ENOMEM` afterwards, on that connection only

Every subsequent write returns `-12`, indefinitely, while the same master writes
to eight other connections without a single failure.

> **Reads as:** the master's per-connection TX credits are never returned,
> because the peer never completes the packets. Consistent with §2.3 and adds
> nothing independent — recorded so it is not mistaken for a second symptom.

### 2.5 The watchdog is still being fed

`WATCHDOG_TIMEOUT_MS` is 30000 and it is fed only at the head of
`telemetry_work_handler()`, on the **system workqueue**. Ninety minutes without a
reset means that handler is still running.

> **Establishes:** the system workqueue is alive. And because the v44 monitor is
> an independent `K_THREAD_DEFINE` thread rather than a work item, anything that
> lets the system workqueue run lets the monitor run too.
>
> **Therefore the monitor is not dead. It is running, reading the stage, and
> concluding the BT RX WQ is quiescent.** That is a much stronger statement than
> "the trap did not fire", and it is what makes §5 worth doing.

### 2.6 The wedged board's link layer is indistinguishable from a healthy one

Added after the QoS export (§10). Mean connection events per second, over the
same 900 s window, 62 minutes into BSF1120's wedge:

```
BSF1120  18.424   gaps 1.675      <-- wedged
BSF31CC  18.415   gaps 1.684
BSF3C79  18.425   gaps 1.674
BSF8BC4  18.419   gaps 1.680
BSFB165  18.413   gaps 1.685
```

Agreement to three decimal places. A board whose application has been silent for
an hour is carrying its connection **exactly** as well as boards that are
delivering 8.3 sweeps per second.

> **Reinforces §2.2 with a matched control instead of an absolute judgement.**
> The wedge is entirely above the link layer, and nothing about the LL degrades
> as it persists.

**This conclusion is robust to the contamination described in §2.7**, and the
reason should be stated before someone later dismisses it: it is a *same-window
differential*. BSF1120 and its four controls were measured over identical
seconds, under identical scanning disturbance, and the disturbance affects a
connection through its anchor phase (§2.7) — a property none of these five
shares with its wedge state. A contaminated absolute number would be worthless
here; a contaminated *difference* between boards that took the same hit is not.

### 2.7 Contamination: the fleet-wide QoS drop after 17:28:20 is self-inflicted

The fleet's `reports` fell from 20.09 to 16–18 at **17:28:20** — the same second
`RECONNECT` was sent, not the 17:30:50 `NODE_GONE`. With BSFEC35 dropped the
master resumed scanning to re-acquire it, and the scan windows collide with
connection anchor points. The result is quantised into exactly two levels:

| level | boards | reports | event_gaps |
|---|---|---|---|
| A | BSF1120, BSF31CC, BSF3C79, BSF8BC4, BSFB165 | 18.42 | 1.68 |
| B | BSF44AD, BSF6C53, BSFAA61, BSFC2CC | 16.18 | 3.92 |

The drop in `reports` is accounted for, to two decimals, by the rise in
`event_gaps` — these are skipped connection events, not lost packets.

**The tiers map exactly onto anchor phase.** Measuring each connection's phase
within the 50 ms interval, from `master_ms` stamped at BLE reception (circular
mean; concentration 0.99–1.00, so the anchors are rock solid):

```
 phase   node      tier
  2.65   BSF44AD     B
  7.52   BSF8BC4     A
 12.68   BSFAA61     B
 17.58   BSF31CC     A
 22.68   BSFC2CC     B
 27.58   BSF3C79     A
(32.7    BSFEC35     -)   vacated by the RECONNECT
(37.5    BSF1120     A)   wedged: no data records, phase inferred from its tier
 42.75   BSF6C53     B
 47.53   BSFB165     A
```

**Strict alternation, period exactly 10 ms** — twice the 5 ms spacing. The B tier
is the odd 5 ms slots (phase ≈ 2.6 + 10k), the A tier the even ones. Not a
contiguous band, which is why handle order (17,18,19,·,22,23,24,25,26,30) showed
nothing: handle is assignment order, not anchor order.

**BSF1120's row is excluded from the alternation argument (L3/A5).** Its phase is
not measured — the board is wedged and emits no data records — it was *inferred
from its tier*, which is the conclusion it would otherwise be supporting. The
eight measured phases alternate perfectly without it, so nothing is lost by
dropping it. It is left in the table, labelled, only to show where the gap sits.

> **This is what moves the mechanism from plausible to determined.** The loss is
> keyed to *anchor phase* and nothing else. It is not a per-board property —
> not battery, not RF, not distance, not the wedge. Two boards 5 ms apart in the
> same rack, on the same firmware, land in different tiers.

Two things it does **not** establish, said plainly rather than glossed:

- The scan parameters are `BT_GAP_SCAN_FAST_INTERVAL` = 60 ms and
  `BT_GAP_SCAN_FAST_WINDOW` = 30 ms ([`main.c:1781`](../../../../B306_Part/host/fusion_master/src/main.c#L1781)).
  Against a 50 ms connection interval the LCM is 300 ms, over which each
  connection's six events land at six phases spaced 10 ms across the 60 ms scan
  cycle — so **exactly three of six fall inside the window for every phase**.
  That predicts a uniform 50% collision rate, not the observed 2.33:1 split. The
  arbitration rule that produces the split is a SoftDevice scheduling property I
  cannot derive from these parameters, and I am not going to guess it.
- The 10 ms alternation against 5 ms spacing means each 10 ms block holds two
  connections that fare differently. A pairwise arbitration within the block fits
  better than a window-boundary effect — but that is a hypothesis, not a result.

### 2.8 Causation, established out-of-sample instead of by experiment

The clean test would be to stop scanning for 60 s and watch `reports` return to
20.09. **It cannot be run remotely.** The master's console has no scan command
(`LEDEXPECT`, `LEDSTAT`, `LEDCLEAR`, `LIST`, `SPACING STATUS`, `MASTER STATUS`,
`SPACING ON/OFF`, `RESOURCES`, `OUTPUT BINARY/TEXT`, `<NODE> <cmd>`). Scanning
only stops on connect, or inside a spacing transition — and that path is
`disconnect_all_then_apply_before_reconnect`, which would end the run. Adding a
`SCAN STOP` command means reflashing the master, which also drops the fleet.

A natural experiment does the same job on independent data. **N7
(`daylight_20260807`) ran at 9 then 8 boards against a capacity of 10, so the
master was scanning for its entire duration — and no `RECONNECT` was ever
issued in that run.** If scanning is the cause, N7 must already show the same
structure.

| | N7 (independent run, no intervention) | N8 after 17:28:20 |
|---|---|---|
| tier A | **18.411 – 18.426**, gaps **1.674 – 1.688** | 18.413 – 18.425, gaps 1.674 – 1.685 |
| tier B | **16.161 – 16.178**, gaps **3.921 – 3.938** | 16.176 – 16.188, gaps 3.912 – 3.922 |
| split | 5 / 4 | 5 / 4 |

Identical to three decimal places, on a different day, a different fleet
composition, and with no command ever sent.

**And the tier membership reshuffles.** N7's B tier is BSF1120, BSFAA61,
BSFB165, BSFC2CC; N8's is BSF44AD, BSF6C53, BSFAA61, BSFC2CC. Only two boards
appear in both. Anchor phase is assigned by connection order at startup, which
differed between the runs — so a board's tier follows its *phase*, not its
identity. This is the prediction §2.7 makes, tested on data that could not have
been fitted to it.

Within N8 the two events also separate cleanly:

- BSFEC35 **wedges** at 15:46 and stays *connected*. Fleet still 10/10, no
  scanning, `reports` unmoved at 20.09 for the next 100 minutes.
- BSFEC35 **disconnects** at 17:28:20. Fleet 9/10, scanning resumes, `reports`
  drops that second.

So the trigger is **being below capacity**, not the wedge and not the command.
`RECONNECT` caused it only by vacating a slot that can never be refilled.

> This is stronger than the 60 s test would have been. That test could have
> confirmed the mechanism on the same fleet in the same conditions; this
> confirms it on an independent run *and* shows the tiers permuting with
> connection order. **It also validates the §8b.2 fix by implication** — bounded
> scanning would have returned N7 to 20.09 as well, which means the defect has
> been silently taxing every below-capacity run this project has done.

A useful byproduct: concentration 0.99–1.00 at exactly 5 ms separation is direct
confirmation that the S1 spacing work is functioning as designed under load —
see §8b.3.

**Application cost: none.** In the same window all eight boards delivered
1353–1354 sweeps, `ge8` 0.998–1.000. The link had roughly 18% of scheduling
headroom nobody had measured before; spending it cost no data.

**Two consequences, both stated so they are not discovered later:**

1. **All link-layer statistics after 17:28:20 are contaminated by the
   intervention.** Any QoS analysis of this run must split there. Application
   data is unaffected and needs no split.
2. `RECONNECT`'s guarantee — *"the scan/spacing machinery is left exactly as it
   was"* — is true of the call and false of the consequence. Disconnecting a node
   that never returns puts the master into permanent scanning. Worth knowing
   before the next time.

I had flagged BSF1120's `reports` decline as possible battery depletion twenty
minutes before this export. **That was wrong, and the control group refuted it in
one step** — which is precisely why it was asked for.

---

## 3. Where that left the diagnosis

Three hypotheses survived §2, and they were not distinguishable from the log:

1. **BT RX WQ is genuinely idle; the block is below it.** Empty LL PDUs generate
   no host work, so `rx_work_handler()` has nothing to run and the stage
   legitimately rests at `RX_WORK_EXIT`. The monitor would be *correct* to stay
   silent, and the fault would live in a layer v44 does not instrument at all.
2. **The monitor's predicate is wrong.** It measures dwell inside a non-quiescent
   stage, not absence of forward progress. A thread cycling quickly through
   `rx_work_handler()` while completing nothing useful reads as healthy.
3. **The monitor thread is starved.** Refuted by §2.5.

(1) and (2) make opposite predictions about one thing: whether the BT RX WQ can
still *do* work when work is handed to it.

---

## 4. The discriminating test

An LL disconnect initiated by the master forces the node's controller to raise an
HCI Disconnection Complete. That event is handled by `hci_disconn_complete()`,
which runs **on the BT RX WQ**. So:

- Under (1) — idle thread, blocked transport below — the event cannot be
  delivered, nothing is processed, and the node never learns it is disconnected.
  **It will not re-advertise.**
- Under (2) — thread cycling, transport fine — the event is delivered like any
  other, the disconnect is processed, and the app restarts advertising.
  **It comes back.**

`RECONNECT` is the only command that survives the `-ENOMEM` wall: it is executed
entirely on the master ([`main.c:3197`](../../../../B306_Part/host/fusion_master/src/main.c#L3197))
and returns before the GATT write that everything else dies on. Its comment also
guarantees blast radius: *"Only this peer's connection is touched. Every other
link, and the scan/spacing machinery, is left exactly as it was."*

---

## 5. The intervention, and why it was justified

This is outside the N8 brief, which says run to depletion and do not intervene.
That instruction assumed a wedge would either resolve itself or be readable
later. Neither held:

- Both boards refuse every command. There is no software path in.
- The only non-destructive read of their live state is SWD, which needs hands on
  the hardware. The user was away, and then confirmed they would be away for the
  following two days.
- `.noinit` does not survive power loss and the batteries do not last two days.

So the choice was never "preserve versus destroy". It was **"attempt recovery
versus guarantee total loss"**, and every hour of waiting was pure loss with no
compensating gain. The user authorised it explicitly at 17:27.

Sent to **one** board only. BSF1120 was deliberately left untouched as an
uncontaminated second sample, and a pre-intervention forensic snapshot was
written first (`pre_reconnect_snapshot.json`).

**Result:**

```
17:28:20  FUSION_RECONNECT_START         err=0
17:28:20  FUSION_RECONNECT_DISCONNECTED  (+100 ms)
17:30:20  FUSION_RECONNECT_DONE  outcome=timeout  connect_ms=0  total_ms=120001
```

The disconnect executed. **The node never re-advertised** — 120 seconds with the
master scanning throughout.

**Hypothesis (2) is refuted. Hypothesis (1) stands.**

---

## 6. The converged picture

The board is an **nRF52840** — single core, SoftDevice Controller linked in
process. The inbound chain is:

```
SDC (radio ISR)
   -> hci_driver RX thread          ("sdc_rx")   <-- NOT instrumented
        -> bt_recv()
             -> bt_workq
                  -> rx_work_handler()           <-- v44 brackets from here
```

`CONFIG_BT_RECV_WORKQ_BT=y`, so `bt_recv()` only *queues*; the thread that pulls
packets out of the controller and acquires a host RX buffer for them is
`sdc_rx`, a different thread entirely. In NCS's `hci_driver.c` that thread calls
`bt_buf_get_rx()` / `bt_buf_get_evt()` with **`K_FOREVER`**.

If the host's RX buffers are all outstanding, `sdc_rx` blocks there forever. And
then, in order:

- no ACL or event is ever handed up, so `bt_workq` receives no work;
- BT RX WQ sits idle with `bsf_bt_stage_id == RX_WORK_EXIT`, which
  `BSF_BT_STAGE_IS_QUIESCENT()` correctly reports as quiescent (§2.5);
- notifications stop and ATT requests go unanswered (§2.3);
- the LL keeps exchanging empty PDUs, because those need no host buffer (§2.2);
- the master's credits never return (§2.4);
- and the HCI Disconnection Complete is stuck in the same queue as everything
  else, so the node never learns it was disconnected (§5).

Every observation is accounted for, including the two that were hardest — the
monitor's silence and the failed reconnect.

### Residual uncertainty, stated plainly

This is the best-fitting explanation, **not a proven one.** What is proven is
narrower and still decisive:

> The node's Bluetooth stack stops making forward progress at a point **below**
> `rx_work_handler()`, it survives a link-layer teardown, and the v44
> instrumentation cannot observe it.

**The constraint the mechanism must satisfy, numerically (L3/A2).** Since
`hci_rx_pool` was in fact sampled at 1 Hz and read 10/10 to the last record
(§2.1, corrected), and since `avail == low_water == max` means it **never once
dipped to 9 in either board's entire life**, the §6 mechanism requires the pool
to go **10 → 0, and never recover, entirely inside the final sub-second**.

Put a number on how likely that is to hide. At ~20 connection events per second,
a drain that leaks one buffer per event takes about **0.5 s**. Against 1 Hz
sampling, a 0.5 s drain has roughly a **50% chance of being caught by some
sample** — and **both boards missing it is about 25%**.

That is **mild evidence against a progressive drain**, and mildly in favour of
either the buffers being seized as a block, or a different mechanism entirely.
It does not refute §6 — one in four is not a small number — but it is a real
constraint and any candidate explanation must clear it.

The relevant counts from the K1 audit are `BT_BUF_ACL_RX_COUNT=6`,
`BT_BUF_EVT_RX_COUNT=10`, against `BT_MAX_CONN=1` — which is why K1 refuted
DRGN-23518, and that refutation still stands: `6 <= 1` is false. **The mechanism
here is not DRGN-23518.** Same layer, different route, and identifying the route
is v45's job.

---

## 7. Why v43 and v44 both missed it — the structural reason

This is the third trap to be defeated by the same class of error, and the pattern
is worth naming because it will recur.

| version | what it assumed | how it failed |
|---|---|---|
| v43 | the wedge is inside `bt_conn_tx_notify()` | `TX_NOTIFY_EXIT` was classified terminal by **name**; `bt_conn_recv()` calls tx_notify at its head and descends into unmarked ATT. Missed a real 10-minute wedge on BSF6C53. |
| v44 | the wedge is anywhere inside the BT RX WQ | bracketing `rx_work_handler()` made quiescence *provable* — and the proof is sound. The thread really is quiescent. **The wedge is not in that thread.** |

v44 is not wrong. Its logic is valid and its terminal set is genuinely provable.
It answered the question it was asked, correctly, and the answer was "the BT RX
workqueue is fine". We read that as failure because we had assumed the question
was the right one.

**The lesson is about scope, not correctness: an instrument that can only observe
one layer will report that layer healthy no matter how badly the layer beneath it
has failed.** Both v43 and v44 placed every mark inside the Bluetooth *host*. The
fault is below the host. No amount of additional marks in `conn.c`, `att.c` or
`hci_core.c` would have caught it.

---

## 8. What v45 must do

Ranked by cost, cheapest first. The first item alone probably settles it.

1. **Sample the HCI RX thread's state from the monitor.** The monitor already
   runs on its own thread and already reads RAM. Add: the `sdc_rx` thread's
   `k_thread` state and, if it is blocked, the object it is pended on. A thread
   in `K_FOREVER` on a net_buf pool is unambiguous. Cost: a handful of RAM reads
   per second, no new instrumentation in the SDK.
2. **Sample who *holds* the outstanding RX buffers (L3/A3).** The free count is
   *already* sampled — `hci_rx_pool` is one of the eight (§2.1, corrected) — so
   "sample the pool" is not a new capability and was never the gap.

   The gap is a circularity in §6 that the earlier draft never named: **if
   `rx_work_handler()` is genuinely idle, it is not holding RX buffers — so who
   is?** `sdc_rx` blocking in `bt_buf_get_rx(..., K_FOREVER)` requires an empty
   pool, and an empty pool requires a holder. A free count cannot name one; only
   ownership can. First candidate is **`conn->rx`**, an incomplete ACL
   reassembly, which holds a buffer indefinitely — but with `BT_MAX_CONN=1` there
   is at most one of those, so it cannot empty a pool of 10 on its own and cannot
   be the whole story.
3. **Replace the liveness predicate — with a mechanism-independent one (L3/A4).**
   Quiescence is the wrong test when the thread can be legitimately idle. But the
   obvious repair, *"`bsf_bt_stage_seq` frozen for 20 s while the app is still
   producing"*, only covers hypothesis (1) — the idle thread. **It does not catch
   a livelock**, where the thread cycles and `stage_seq` advances normally.
   Hypothesis (2) is refuted for *these two events*; pinning the predicate to the
   mechanism that happened to win is precisely the error that defeated v43 and
   v44, committed a third time.

   The mechanism-independent form is strictly stronger and costs the same:

   > **producer counters advancing AND exported records stopped for 20 s ⇒ wedge**

   It covers (1), (2), and whatever is not yet imagined, because it never
   mentions where the fault is. The standing objection — *"do not trigger merely
   because notifications stopped; it readmits the producer, RF, scheduling, the
   central and the application as false-positive sources"* — **is answered by the
   same protection the dwell predicate already relies on**: a vanished central
   disconnects at the 4000 ms supervision timeout, which clears the
   subscriptions, drops `armed`, and resets the counter, all long before 20 s.
   The contract test's §3 assertion that forbids `notify_ok`/`data_subscribed` in
   the monitor must be relaxed to permit exactly this form and no other. This
   catches both the v44 case and the present one, and it is a smaller change than
   any new marking.
4. **Only then** consider marks inside `hci_driver.c`. That file is SDK code and
   would extend the repository patch to a fourth file; do it last, and only if
   1–3 leave the route ambiguous.

Item 3 deserves emphasis: **had the monitor tested progress instead of dwell, both
of tonight's wedges would have produced a corpse.** That is the single highest-value
change available, and it does not require knowing the root cause first.

---

## 8b. Two findings independent of the wedge

Both fell out of §2.7 and neither is about the wedge. Recording them here because
today is the first time either was visible.

### 8b.1 The link-layer scheduling headroom, measured for the first time

The static arithmetic was always "exactly full": 50 ms connection interval ÷ 10
connections = 5 ms spacing, no slack. That is an arithmetic statement about
*anchor placement*, and it says nothing about how much event loss the application
can absorb. Nobody had measured the second thing.

Now measured, under load:

| | events/s | notifications/s | notif per event |
|---|---|---|---|
| nominal | 20.09 | 31.39 | 1.56 |
| A tier, degraded | 18.42 | 31.39 | 1.70 |
| B tier, degraded | 16.18 | 31.39 | **1.94** |

Notification rate is identical to two decimals across all eight boards and did
not move when the events did — the link simply packs more PDUs per event. At
1.94 notifications per event there is still **no data loss at all**: 1353–1354
sweeps per board, `ge8` 0.998–1.000.

So the per-event capacity ceiling is **at least 1.94 and still not found**.

### The cost is zero for *delivery completeness*, and unmeasured for *latency*

This distinction must not be collapsed, because the collapsed version — "18% of
connection events can be dropped for free" — is how this will get misquoted.

What is measured: **delivery completeness is untouched.** Same sweep counts, same
`ge8`, same notification rate, zero `ring_drop` / `sweep_drop` / `duplicate`.
Nothing is lost.

What is **not** measured: **latency.** The link absorbs the missing events by
packing more PDUs into each surviving one (1.56 → 1.94). That is by definition
data waiting longer in the queue before it gets a transmission opportunity. With
events at 16.18/s instead of 20.09/s, the worst-case wait for a fresh record
rises from ~50 ms to ~62 ms of anchor spacing alone, before queueing is counted.
End-to-end p95 was previously measured at **52.0 ms**; it must be higher under
degraded scheduling, and this run did not measure it.

**For a real-time MVP, latency is the requirement that matters.** So the correct
statement is:

> *Delivery completeness: zero cost, measured. Latency: increased by an unmeasured
> amount.*

Measuring it is cheap — the records already carry both node TIMER2 timestamps and
`master_ms` at BLE reception, so end-to-end delay is a subtraction over data
already on disk. It should be done before any capacity conclusion is drawn from
§8b.1, and before this number is used to argue for 20 nodes.

This is still the first empirical input to the 20-node question. It does not
answer it — 20 nodes at a 50 ms interval needs 2.5 ms spacing, a different
regime — but it replaces "exactly full, therefore no margin" with a measured
throughput number plus a named open question.

### 8b.2 Operational defect: the master scans forever for a node that cannot return

**This is the real defect, and it is not that a command was sent.** The master
resumed scanning when BSFEC35 dropped and will scan for it forever. BSFEC35
cannot come back without a physical power cycle, so the scan never terminates,
and every connection in the fleet pays for it continuously.

The cost is currently free for throughput (§8b.1) but it accumulates. At the
wedge rate this run actually exhibited — two boards in 10 × 2.65 h ≈ **one per
26.5 board-hours** — scan targets pile up over a long deployment, each one
permanently depressing fleet-wide scheduling, until the headroom is gone and the
loss stops being free.

**The fix is a state change, not a timer.** An earlier draft of this section said
"bound the scan retry", and that framing is wrong twice over:

- The correct action is **stop expecting a node that is gone** — lower the
  expected node count so the fleet is at capacity again and scanning stops on its
  own. A timer that keeps scanning and merely gives up later still pays the tax
  for the whole interval, and has to decide when to start paying it again.
- **Any deadline that does exist must exceed the self-reset rejoin time.** The
  canary measured **≈20.7 s** from reset to rejoined. A wedged board is *supposed*
  to reboot and come back — that is the entire recovery arm the v44 trap depends
  on — and it can only come back if the master is scanning when it advertises.
  **A bound set too tight would lock the trap's own recovery out of the fleet**,
  turning a self-healing wedge into a permanent loss.

So: scanning must persist comfortably past ~20.7 s for any node that might
self-recover, and must stop entirely once a node is *declared* gone. The two
requirements are not in tension — they are about different states, which is
exactly why a timer alone cannot express them.

### 8b.2b Scanning is not a precondition for the wedge

A useful negative result that falls out of the §2.8 timeline for free.

Both wedges — BSFEC35 at 15:46 and BSF1120 at 16:53 — occurred **inside** the
at-capacity window (§8b.4), when the fleet was 10/10 and the master was not
scanning at all. Scan-induced scheduling disturbance therefore cannot be a
necessary condition for the wedge, and the whole "was it provoked by scan
preemption?" line of enquiry is closed before it was opened.

### 8b.4 The at-capacity baseline — the only clean link-layer data that exists

**15:14:57 → 17:28:20, 132.7 minutes, ten connections, master not scanning.**

This project has never had this before. Confirmed by checking every run since
mid-July: `linked` peaked at **9** in N7 and in the v43 night run, and at 5 in
the deploy run. **N8 is the only run that ever reached 10, and it held it for
2 h 13 m before the RECONNECT ended it.**

Which means every earlier run — N3, N4, N5, N6, the v43 night, N7 — was below
capacity for its whole duration and therefore carried degraded scheduling
throughout, and nobody knew. Their link-layer numbers were never a baseline;
they were all measurements of the degraded state, taken without a control.

Saved as `baseline_at_capacity_qos.csv` (79 210 rows, all ten nodes) and
`baseline_at_capacity_summary.json`. Per-node means over the full window:

```
node       reports    gaps   crc_err    nak   rx_to
BSF1120     20.096  0.0097    0.6038   0.00   0.618
BSF31CC     20.086  0.0199    1.7393   0.01   0.897
BSF3C79     20.072  0.0333    1.0941   0.01   0.818
BSF44AD     20.093  0.0128    0.6202   0.00   0.564
BSF6C53     20.092  0.0140    1.6638   0.00   1.098
BSF8BC4     20.094  0.0115    0.4825   0.00   0.461
BSFAA61     20.095  0.0110    0.9562   0.01   0.733
BSFB165     20.092  0.0138    1.4593   0.01   1.077
BSFC2CC     20.093  0.0130    1.0211   0.01   0.711
BSFEC35     20.102  0.0039    0.4840   0.01   1.338
```

`reports` 20.07–20.10 across all ten, `event_gaps` essentially zero (0.004–0.033
per second against 1.68/3.92 when scanning). No tier structure — the tiers are a
scanning artefact and do not exist at capacity.

> **Compare every future link-layer number against this window, not against
> other runs.** Comparing two degraded runs to each other has been the implicit
> practice until today and it cannot detect a fleet-wide regression, because the
> degradation is common-mode.

### 8b.3 S1 spacing validated at full load, in the field

The S1 batch (`spacing_default_20260807`) made connection spacing impossible to
lose, and derived 5000 µs rather than hard-coding it. **Its evidence at the time
was bench verification only.** The anchor-phase measurement in §2.7 is the first
field data, and it was taken under the heaviest load the system has ever carried:

```
2.65  7.52  12.68  17.58  22.68  27.58  (32.7)  (37.5)  42.75  47.53   ms
```

Ten slots, nominal separation 5.00 ms, observed separations 4.87–5.16 ms, phase
concentration **0.99–1.00** — the anchors are essentially jitter-free. Every board
reports `spacing=ON spacing_us=5000 spacing_generation=1`, unchanged across a
full fleet power cycle, ten simultaneous connections, two wedges and a forced
disconnect.

This proves something different from §8b.1 and should be cited separately: not
that there is headroom, but that **the placement mechanism itself holds under
load and survives disturbance.** S1's three layers (firmware default, rebuild
folded into the DK restore, pre-window assertion) did what they were built to do.

## 9. State ledger

| | |
|---|---|
| **BSFEC35** | Disconnected, not advertising, out of the fleet. Will not return without a physical power cycle. Live RAM state unrecoverable — but it was unrecoverable before the intervention too, so nothing was traded away. |
| **BSF1120** | **Untouched.** Wedged and still connected. Continues to emit QoS every second — a continuous recording of a wedged link, which this project has never had. Leave it alone: RECONNECT is now known not to recover a board, so sending it would destroy the only wedged sample in a known state and buy nothing. |
| **The other eight** | Healthy, 8.334 sweep/s, `corpse present=0`, `reboot_owner=0`. Run continues to depletion per the brief. |
| **Data** | 2.3 GB fusion + 11 GB listeners at T+141 min; 165 GB free. |

## 10. Files

- `WEDGE_LOCALISATION.md` — this report.
- `ACTION_LOG.md` — intervention timeline and authorisation record.
- `pre_reconnect_snapshot.json` — QoS history, full `STALL_READ` lifecycle, last
  data record and every failing command, both boards, captured before §5.
