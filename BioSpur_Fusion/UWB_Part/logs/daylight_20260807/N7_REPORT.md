# N7 — daytime exposure run

**Batch:** `daylight_20260807` · Evidence root: `UWB_Part/logs/daylight_20260807/`
**Run:** 2026-08-07 **11:46:49 → 12:26:41** (39.9 min, stopped on operator instruction)
Nothing flashed, nothing built. Ten boards on `b306-imu-relay-v43`, Fusion Master on
`dk-fusion-imu-relay-v36`.

---

## 0. Headline — the fault was caught, the corpse was not, and the reason is a bug I introduced

**A genuine BT RX wedge occurred on BSF6C53 at 12:16:25, 29.6 minutes in.** Its signature matches
last night's BSF44AD episode line for line. It is still wedged.

**No corpse was produced. v43's monitor never fired.** Fleet-wide `present=1` count: **0**.

**The reason is precise and it is my error.** `TX_NOTIFY_EXIT` is classified as a *terminal* stage.
That is correct when `bt_conn_tx_notify()` is called standalone from `bt_conn_set_state()` — but it
is **wrong** when called from the head of `bt_conn_recv()`, because the thread then descends into
`bt_acl_recv()` and the whole of L2CAP, ATT and the GATT handlers, **none of which carries a stage
mark**. While the thread is in there the last mark is `TX_NOTIFY_EXIT`, so the monitor reads "idle,
nothing in flight" and never accumulates dwell.

**The unwatched region is exactly where the fault lives.** `bt_att_chan_create_pdu()`
(`att.c:722/725`) allocates ATT responses with a 30 s timeout and everything else with `K_FOREVER` —
and the measured timing of this event is precisely a thread parked there.

**Worse: the contract test I wrote yesterday enforces the bug.** `test_bt_stage_contract.py:48`
asserts `TX_NOTIFY_EXIT` must be in the quiescent set. The fix has to change the test first.

§7 states what v44 must change.

---

## 1. The event — BSF6C53, 12:16:25

| Time | Event |
|---|---|
| 12:16:25 | `DATA_PLANE_SILENT` — UWB **and** IMU stop together, 2.04 s each |
| 12:16:25 | `STALL READ` submitted, accepted onto the bearer |
| **12:16:50** | **`FUSION_STALL_READ_DONE terminal=timeout elapsed_ms=25001`** — C1's 25 s bound; the read never returned |
| 12:16:50 | `FUSION_STALL_READ_BEARER_WARNING att_timeout_in_ms=5000 note=cancel_does_not_stop_att_timer` |
| ~12:16:55 | `FUSION_TELEMETRY_UNSUBSCRIBED` + `FUSION_DATA_UNSUBSCRIBED` — the 30 s ATT timeout tears the bearer down |
| 12:17:52 | further reads `submit_error err=-12` |
| 12:26:41 | still silent at run stop — **10.3 minutes, no self-reset, no corpse** |

**Classified `stall`, not `dying board`:** 219 `FUSION_QOS` reports continued to arrive after onset,
so the radio link stayed healthy while application data went to exactly zero. That is §4's stall
signature and it excludes link collapse.

**The publisher was healthy three minutes before onset.** The 12:13 `STALL READ` returned cleanly:

```
e=72673 x=72673          entry == exit: the publisher is NOT stuck inside a notify call
rcc=72673/0/0/0/0        every notify succeeded; zero nomem / notconn / again / other
q=0/0/0  qd=0/0/0        all three queues empty, zero drops
alarm=0@0 recovery=0     the stall detector never armed
hb=65937                 producer heartbeat advancing
```

**That is a real narrowing.** D1 concluded "publisher side"; J1 pointed at `k_work_flush()` inside
`bt_conn_tx_notify()`. This snapshot says the publisher and its queues were pristine minutes before
a sudden onset, so the fault is **downstream of the publisher, on the BLE side** — and the read
dying at onset+0 puts it in the ATT/GATT service path specifically.

**BSF6C53 delivered 14,797 sweeps against a fleet ~19,918** — it simply stopped at the wedge and
never resumed.

### 1.1 Why nothing on the board will recover it

Neither reset authority can fire:

* **The v43 BT RX monitor** — blind, for the reason in §0.
* **The v42 ring's `no_exit` backstop** — needs the publisher stuck *inside* a call for 6 s, but
  `e == x` says it is not. Its criterion is simply not met.

The watchdog is fed from `telemetry_work_handler` on the system workqueue, which is unaffected, so
there is no reset from that direction either. **The board will sit wedged until its battery dies.**

---

## 2. Setup gates

| Gate | Result |
|---|---|
| Tag Master physically absent (§6.1) | **ABSENT** on all four checks — `lsusb`, sysfs `1-5.1`/`1-6.1`, `/dev/serial/by-id`, `ttyACM24/25/26` |
| Fusion Master present | yes, `8D3AC42D4D90FAE8` |
| Spacing | **ON / 5000 / generation 1**, anchors `0…45000` — dk-v36's derived boot default, no command sent |

### 2.1 Cold-start signature per board

Expected after a power cycle: `v43` + `init=cold` + `CORPSE present=0` + `reboot_owner=0`.

**9 of 10 matched exactly.** BSF3C79, BSFC2CC, BSF44AD, BSF6C53, BSF8BC4, BSF1120, BSFAA61,
BSFEC35, BSFB165 — all `boot=1 init=cold`, `present=0 owner=0`, all on `b306-imu-relay-v43`.

**BSF31CC: NO REPLY.** It answered an end-to-end PING at 11:35 and was gone by 11:46 —
`not_connected`, zero records, three attempts, `count=9`. Quarantined per §7 and the run started
without it; the driver kept polling all ten so it could rejoin, and it never did.

**CORRECTED (2026-08-07, operator): this is an ordinary battery death, not a mystery.** The board
never actually charged — a **POGO contact fault**. The original text argued battery was implausible
"eleven minutes off a full charge"; the premise was wrong, because it was never on a full charge.
There is no second unexplained dropout. The `INSUFFICIENT` marking is withdrawn.

The `reboot_owner=0` readings across the fleet also confirm on hardware that **charging clears
`.noinit`**: BSFAA61 read `owner=2` yesterday after its forced corpse and reads `0` today with no
firmware change.

---

## 3. Final `CORPSE STATUS` sweep — §3.1

Run driver stopped first, then an explicit sweep with the CDC free:

| Board | Result |
|---|---|
| BSF3C79, BSFC2CC, BSF44AD, BSF8BC4, BSF1120, BSFAA61, BSFEC35, BSFB165 | `present=0` — **safe to dock** |
| BSF6C53 | **NO REPLY** — wedged, cannot be swept |
| BSF31CC | **not_connected** — cannot be swept |

**present=0: 8 · unreachable: 2 · corpse held: 0 · VERDICT: SAFE TO DOCK.**

The two unreachable boards carry no corpse to lose: fleet-wide `present=1` was 0 for the entire run,
and BSF6C53's monitor demonstrably never fired. Docking them costs nothing that exists.

---

## 4. Yield ladder

39.9 min, 9 delivering nodes. Rates are `(records − 1) / (last − first)` on the live block after the
stale prefix is split off.

| Node | UWB | Rate (Hz) | 8/8 | 7+/8 | UWB delivered | IMU samples | IMU delivered |
|---|--:|--:|---|---|---|--:|---|
| BSF1120 | 19,918 | 8.3340 | 19,862/19,918 | 19,918/19,918 | 19,918/19,918 | 478,037 | 478,037/478,037 |
| BSF3C79 | 19,920 | 8.3344 | 19,895/19,920 | 19,920/19,920 | 19,920/19,920 | 478,055 | 478,055/478,055 |
| BSF44AD | 19,919 | 8.3340 | 19,892/19,919 | 19,919/19,919 | 19,919/19,919 | 478,032 | 478,032/478,032 |
| **BSF6C53** | **14,797** | 8.3341 | 14,794/14,797 | 14,797/14,797 | 14,797/14,797 | 355,090 | 355,090/355,090 |
| BSF8BC4 | 19,919 | 8.3339 | 19,917/19,919 | 19,919/19,919 | 19,919/19,919 | 478,011 | 478,011/478,011 |
| BSFAA61 | 19,918 | 8.3339 | 19,889/19,918 | 19,918/19,918 | 19,918/19,918 | 471,209 | **471,209/478,039** |
| BSFB165 | 19,917 | 8.3334 | 19,905/19,917 | 19,917/19,917 | 19,917/19,917 | 477,290 | 477,290/478,020 |
| BSFC2CC | 19,918 | 8.3335 | 19,906/19,918 | 19,918/19,918 | 19,918/19,918 | 477,985 | 477,985/477,985 |
| BSFEC35 | 19,919 | 8.3343 | 19,890/19,919 | 19,919/19,919 | 19,919/19,919 | 478,020 | 478,020/478,020 |

**Fleet: 8/8 = 173,950/174,145 = 99.8880 %. 7+/8 = 174,145/174,145 = 100.0000 %.**
**Zero UWB sweep loss on every node**, counted from sweep-number jumps, not `q_drop`. Every node
locked to 8.3334–8.3344 Hz, i.e. the `COUNT=12 × PERIOD=10` ceiling of 8.3333 Hz within arrival
jitter.

Marginally better than last night on both rungs (99.8503 % / 99.9712 %). **Freshness: `INSUFFICIENT`
— static bench.** BSF6C53's counts are lower only because it stopped at the wedge; its *rate* while
alive was nominal.

### 4.1 BSFAA61 IMU verdict — normal, the power cycle was the fix

**471,209 / 478,039 = 98.57 %.** Last night it ran at **8 % of nominal for six hours**
(358,799 against 4,320,000). Same board, same image, no firmware change — the only intervention was
a full power cycle.

**Verdict: the degradation was a startup or state condition, cleared by power-cycling. Not drift,
not a hardware fault, not the JY61P.** None of the split-the-cause fields in §3.3 needed to be
consulted, because the symptom did not recur. Worth watching on the next long run to confirm it does
not creep back with uptime, since today's window was only 40 minutes.

---

## 5. Event log

| Board | Time | Signature | Classification |
|---|---|---|---|
| BSF6C53 | 12:16:25 | link healthy (`FUSION_QOS` continuing), application data zero, read dies at onset+0, bearer torn down at +30 s | **stall** — a genuine BT RX wedge |
| BSF31CC | between 11:35 and 11:46 | never joined the run; `not_connected`, zero records, never returned | **battery death** — the board never charged (POGO contact fault, established by the operator after this report was first written). Not a stall, not a wedge. |

No dying-board signature, no `0x08` power loss, no battery death. Nothing was power-cycled, docked,
reconnected or otherwise touched; `RECONNECT` was never issued.

**Corpse classification: not applicable — no corpse.** The classifier was never invoked. No outcome
has been forced.

---

## 6. Exposure accounting

* Run 2,392 s = 0.664 h. Eight boards for the full window, BSF6C53 for 1,776 s.
* **Exposure this run: 5.81 board-hours.** Expected events at 1 per 26.5 bh: **0.22. Observed: 1.**
* **Cumulative with last night: 59.8 board-hours, 1 event observed** — an observed rate of 1 per
  59.8 bh against an assumed 1 per 26.5. Both are consistent with the same underlying rate at these
  sample sizes; **nothing here revises the rate estimate.**

The run was stopped at 39.9 min on operator instruction once it was established that the trap could
not close, not on any abort condition. The three abort conditions never triggered.

---

## 7. Why v43 failed, and exactly what v44 must change

### 7.1 The defect

`bt_conn_recv()` as instrumented:

```c
BSF_BT_STAGE_A(BSF_BT_STAGE_CONN_RECV_ENTER, conn->handle);
bt_conn_tx_notify(conn, true);          // marks TX_NOTIFY_ENTER … TX_NOTIFY_EXIT
LOG_DBG(...);
bt_acl_recv(conn, buf, flags);          // <-- L2CAP + ATT + GATT: NO STAGE MARKS AT ALL
BSF_BT_STAGE(BSF_BT_STAGE_CONN_RECV_EXIT);
```

While the thread is inside `bt_acl_recv()`, the most recent mark is `TX_NOTIFY_EXIT`. The monitor
treats that as quiescent, so:

```c
else if (armed && !BSF_BT_STAGE_IS_QUIESCENT(stage)) { unchanged_ms += tick; }
else                                                 { unchanged_ms = 0;    }
```

takes the `else` branch forever. **`unchanged_ms` never accumulates, and the 5 s threshold is never
approached, no matter how long the thread is stuck.**

**The root error is classifying a stage by its name instead of by its context.** `TX_NOTIFY_EXIT`
genuinely is terminal when `bt_conn_tx_notify()` is called standalone from `bt_conn_set_state()`. It
is mid-operation when called from the head of `bt_conn_recv()`. One stage ID, two meanings, and the
quiescent set can only express one of them.

The blind region is `bt_acl_recv` → `bt_l2cap_recv` → `bt_att_recv` → GATT handler — which contains
`bt_att_chan_create_pdu()` (`att.c:722/725`: `BT_ATT_TIMEOUT` = 30 s for responses, `K_FOREVER`
otherwise). A read submitted at onset that never returns, with the 25 s bound and the 30 s ATT
timeout both firing exactly on schedule, is the signature of a thread parked precisely there.

### 7.2 What v44 changes

**1. Instrument `bt_acl_recv()` — the actual fix.** In the host patch, bracket the call:

```c
BSF_BT_STAGE(BSF_BT_STAGE_ACL_RECV_ENTER);
bt_acl_recv(conn, buf, flags);
BSF_BT_STAGE(BSF_BT_STAGE_ACL_RECV_EXIT);
```

`ACL_RECV_ENTER` must be **non-terminal**; `ACL_RECV_EXIT` is transient (it is immediately followed
by `CONN_RECV_EXIT`) and so also non-terminal. Two new enum values appended — **never renumber the
existing ones**, decoded corpses depend on the values.

**2. Reclassify `TX_NOTIFY_EXIT` as NON-terminal.** Once (1) exists, the only path that leaves
`TX_NOTIFY_EXIT` as the last mark is the standalone call from `bt_conn_set_state()`, which is itself
immediately followed by `RESET_RX_BEFORE`. So it is transient everywhere and must not be quiescent.
After the change the terminal set is exactly: **`IDLE`, `CONN_RECV_EXIT`, `DEFERRED_RESCHEDULE_AFTER`.**

**3. Fix the contract test, which currently enforces the bug.**
`test_bt_stage_contract.py:48` lists `TX_NOTIFY_EXIT` in `must_be_quiescent`. Move it to
`must_not_be`, add both new stages, and record *why* — the test's existing "every stage must be
classified" rule is what will force the decision for any future stage, and it worked; what failed
was the classification itself, made by name.

**4. Consider instrumenting the ATT allocation directly.** A mark immediately around
`bt_l2cap_create_pdu_timeout()` inside `bt_att_chan_create_pdu()` would distinguish
"blocked allocating an ATT response" from "blocked elsewhere in GATT" — turning
`TX_NOTIFY_FLUSH_WEDGE_CONFIRMED`'s equivalent into a directly attributable verdict rather than an
inference. Optional; (1) alone makes the wedge visible, and (4) makes it nameable.

**5. Do not change any Kconfig.** `BT_CONN_TX_NOTIFY_WQ` stays `n`, flow control stays off,
`BT_RX_STACK_SIZE` stays 1024, `BT_CONN_FRAG_COUNT` stays 1. §5 stands: the mechanism is still not
on record, and v44 is about being able to *see* it, not about changing it.

### 7.3 What this run bought

Not a corpse — but three things that were not known this morning:

* **The wedge is reproducible and it is not rare on this fleet.** Two events in 59.8 board-hours,
  the second observed live from onset.
* **The publisher is clean minutes before onset** — `e == x`, empty queues, zero notify failures.
  The fault is sudden and on the BLE side, not a slow producer starvation.
* **The trap was pointed at the wrong region, and the evidence says which region is right.** The
  25 s / 30 s timing points into the ATT service path, and that is exactly the block v44 instruments.

---

## 8. State as left

* **Run and listener array stopped** 12:26:41. Nothing is running; the CDC is free.
* **Final corpse sweep clean — safe to dock.**
* **BSF6C53 is still wedged** and will remain so until its battery dies. Nothing on the board can
  recover it. It holds no corpse.
* **BSF31CC** has been off-air since ~11:46 — flat battery from a POGO contact fault; needs its charge verified before it can rejoin.
* Eight boards healthy on v43.
* Fusion Master on dk-v36, spacing ON/5000/gen 1. Nothing was flashed and nothing was built.
* Captured: `B_RUN` 625 MB, `B_LISTENERS` 2.8 GB.

Evidence index: `EVIDENCE_SHA256.txt`.
