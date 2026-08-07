# V44 Stage A — offline. Built, verified, pushed. Awaiting `TOKEN: V44 HW GO`.

**Batch:** `v44_selfcapture_20260807` · **2026-08-07 12:40–13:10**
Nothing flashed, no hardware touched. All ten boards on charge throughout.

---

## 0. Headline

**Stage 0 verdict: `k_work_flush()` is ELIMINATED**, verified three ways against the **deployed v43
binary**, not today's source.

**The coverage audit found a second blind region** — the HCI *event* path, which v43 never covered.
That is the answer to the addendum's question, and it changed what v44 contains.

**The obvious fix would have bricked the fleet.** §3.3 told me to prove the `TX_NOTIFY_EXIT`
reclassification before making it. The proof **fails**: there is a third caller. Reclassifying it as
written would have false-positive rebooted every board within 5 s of any pause in traffic.

**v44 therefore does something better than the brief specified**: it brackets `rx_work_handler()`,
making quiescence *provable* instead of a maintained list.

**Nothing found in Stage A should change the plan before hardware** — the plan already changed, here,
and the result is stronger. Stage B can proceed as written on the token.

---

## 1. Stage 0 — `k_work_flush()` ELIMINATED

All three legs, from the deployed v43 image and the recorded N7 data.

**Leg 1 — `TX_NOTIFY_BEFORE_FLUSH` is genuinely outside the quiescent set in the deployed binary.**
Disassembly of `bsf_bt_monitor` in `b306-imu-relay-v43-a/firmware/zephyr/zephyr.elf` at `0x1c344`:

```
1c344:  cmp.w  fp, #7          ; TX_NOTIFY_EXIT        -> quiescent
1c348:  beq.w  1c110
1c34c:  sub.w  r2, fp, #11
1c350:  cmp    r2, #1
1c352:  bls.w  1c110           ; stage in {11,12}      -> quiescent
1c356:  add.w  r9, r5, #1000   ; else unchanged_ms += 1000
1c360:  movw   r3, #4999
1c364:  cmp    r9, r3          ; >= 5000 -> fire
```

The compiled set is `{7, 11, 12}` plus `IDLE` via the `armed` gate. **There is no comparison against
5 anywhere.** Had the thread parked at `TX_NOTIFY_BEFORE_FLUSH` it would have fallen straight through
to the accumulator and fired at 5 s. Verified in the binary, as required.

**Leg 2 — the monitor was armed.** `armed` is a local in the monitor and is *not* recorded, so this
is established from traffic rather than a flag, and is labelled as such: **92 ATT round trips**
completed on BSF6C53 before onset (replies and stall reads in the archive). Every one required an
inbound ATT write → `bt_conn_recv()` → stage marks → `seq` advance → `armed = true`. The 12:13 read
also showed `rcc=72673/0/0/0/0` against a 64-notify arming threshold.

**Leg 3 — it did not fire.** Fleet-wide `present=1` count **0**; `NODE_GONE` **0**; **zero**
disconnect/connect events for BSF6C53 after onset. A `sys_reboot()` would have dropped the link and
re-advertised, and `FUSION_QOS` continued uninterrupted — so there was no reset and no alarm.

**One assumption, and it closes.** The elimination presumes the monitor thread was alive. It was:
`k_sys_fatal_error_handler` is unoverridden and `arch_system_halt()` spins with IRQs locked, so any
fatal error would have stopped the watchdog feed and reset the board within 30 s. The board did not
reset.

> **`k_work_flush()` is eliminated. The hypothesis ranked top since J1 is closed.**

**N7 report corrected.** §2.1 and §5 now record BSF31CC as an ordinary battery death from a **POGO
contact fault**; the `INSUFFICIENT` marking is withdrawn, and the "implausible at 11 minutes off a
full charge" argument is retracted because the premise — that it was on a full charge — was false.

---

## 2. §3.1 — the coverage audit, and the second blind region

**Every blocking primitive reachable from the BT RX workqueue thread**, by path:

| Path | Primitive | Bound | v43 coverage |
|---|---|---|---|
| ACL → L2CAP | `bt_l2cap_create_pdu_timeout` `l2cap.c:464` | `L2CAP_RTX_TIMEOUT` | **BLIND** |
| ACL → ATT | `bt_l2cap_create_pdu_timeout(&att_pool,…)` `att.c:729` | 30 s / `K_FOREVER` | **BLIND** ← the fault |
| ACL → ATT | `net_buf_alloc` `att.c:1191`, `att.c:1399` | fragment allocs | **BLIND** |
| ACL → ATT | `net_buf_alloc(&att_pool, BT_ATT_TIMEOUT)` `att.c:3059` | 30 s | **BLIND** |
| ACL → conn | `k_work_flush` `conn.c:333` | unbounded | covered (and now eliminated) |
| **EVENT** | `net_buf_alloc(&hci_cmd_pool, K_FOREVER)` `hci_core.c:311` | **unbounded** | **BLIND — second region** |
| **EVENT** | `k_sem_take(&sync_sem, HCI_CMD_TIMEOUT)` `hci_core.c:429` | 10 s | **BLIND — second region** |
| **EVENT** | `k_sem_take(&bt_dev.ncmd_sem, …)` `hci_core.c:4721` | bounded | **BLIND — second region** |

**Yes, a second blind region exists.** v43's marks lived entirely in `conn.c`, so the HCI *event*
dispatch path — a different arm of the same `switch` — had no instrumentation at all. Any event
handler that issues a synchronous HCI command can block there for 10 s, or unboundedly on the
command pool, completely invisibly.

Finding it before flashing is exactly what the addendum's checkpoint was for.

---

## 3. §3.2–§3.6 — what v44 actually does

### 3.1 The §3.3 proof FAILS — and that matters more than the fix

The brief's argument was that once `bt_acl_recv()` is marked, the only path leaving `TX_NOTIFY_EXIT`
as the last mark is `bt_conn_set_state()`, immediately followed by `RESET_RX_BEFORE`. It instructed
me to enumerate every caller first.

**There are three, not two:**

| Caller | `wait_for_completion` | Is `TX_NOTIFY_EXIT` the last mark? |
|---|---|---|
| `conn.c:523` — `bt_conn_recv()` head | true | no (followed by `ACL_RECV_ENTER` in v44) |
| `conn.c:1307` — `bt_conn_set_state(DISCONNECTED)` | true | no (followed by `RESET_RX_BEFORE`) |
| **`hci_core.c:608` — `hci_num_completed_packets()`** | **false** | **YES** |

The third runs on the **same BT RX WQ** (`rx_work_handler` → `hci_event` → NCP handler) and returns
straight up the stack. **Number-of-Completed-Packets events arrive on every ACL TX completion**, so
had I reclassified `TX_NOTIFY_EXIT` as non-terminal, **every board in the fleet would have
self-rebooted within 5 s of any pause in traffic** — the same false-positive class that cost two OTA
attempts on v43, but fleet-wide instead of on one canary.

**Recorded as: reclassification alone is unsafe. §3.3 as written must not be done.**

### 3.2 What v44 does instead — bracket the single entry point

`rx_work_handler()` (`hci_core.c:4229`) is the **only** entry point for everything the BT RX WQ ever
does: ACL, HCI events and ISO all dispatch from its one `switch`. v44 brackets that switch:

```
BSF_BT_STAGE_A(RX_WORK_ENTER, bt_buf_get_type(buf));
switch (...) { hci_acl / hci_iso / hci_event }
BSF_BT_STAGE(RX_WORK_EXIT);
```

which makes the monitor's test **provable rather than enumerated**:

> **quiescent ⟺ not inside `rx_work_handler()`**

**Terminal set is now exactly `{IDLE, RX_WORK_EXIT}`.** Both blind regions close at once, and the
`TX_NOTIFY_EXIT` reclassification becomes safe as a consequence rather than as an assumption.

### 3.3 Finer attribution on top

* `ACL_RECV_ENTER` / `ACL_RECV_EXIT` around `bt_acl_recv()` — narrows a wedge to the ACL arm.
* `ATT_ALLOC_RESPONSE` / `ATT_ALLOC_FOREVER` / `ATT_ALLOC_DONE` around the allocation in
  `bt_att_chan_create_pdu()` (§3.4, promoted to required). The two branches are **different
  verdicts**: the bounded 30 s response path versus the unbounded `K_FOREVER` path. The branch is
  selected at the mark by `K_TIMEOUT_EQ(timeout, K_FOREVER)`, so the corpse names which one.

**Seven stages appended (14–20). None renumbered** — decoded corpses carry the numeric value, and a
renumber would silently reinterpret every corpse ever captured.

### 3.4 Contract test

`must_be_quiescent` is now `{IDLE, RX_WORK_EXIT}`; the nine other stages moved to `must_not_be`; the
stage-count floor is 21. The reasoning is written into the file: **the "every stage must be
classified" rule worked and did force a decision — what failed was the decision itself, made by name
instead of by call site.** Classify by call site, never by name.

### 3.5 Freeze — unchanged

`BT_CONN_TX_NOTIFY_WQ=n`, ACL flow control off, `BT_RX_STACK_SIZE=1024`, `BT_CONN_FRAG_COUNT=1`,
`BT_MAX_CONN=1`, every buffer count untouched.

### 3.6 The host patch now spans three files

`conn.c`, `hci_core.c`, `att.c`. `host_patch.sh` gates **each independently**: the tree is
"pristine" or "patched" only if **every** file matches, so a half-applied tree — what an interrupted
`patch` leaves — is neither, and the script refuses instead of guessing. Apply/verify/revert round
trip tested. All three files carry the `__has_include` guard, so other projects on the shared SDK
(the Fusion Master DK among them) still compile the instrumentation out to nothing.

---

## 4. Build

| | v43 | **v44** | Δ |
|---|---|---|--:|
| FLASH | 222,904 B (44.65 %) | **223,256 B (44.72 %)** | **+352 B** |
| RAM | 116,164 B (44.31 %) | **116,228 B (44.34 %)** | **+64 B** |

| Artifact | SHA-256 | |
|---|---|---|
| unsigned app | `49f8e96d96d185e485e870ac046ed39d608d24bbc2e87d161fab86d6724aa9cb` | reproducible |
| MCUboot | `aa252296f1e9bb41802df14c0d48eb1a24a8a814870a64203cac9f78dd46e307` | reproducible, unchanged |
| signed (build A) | `3059339499ca58f87a7ce0e9739a7f95960ea22f446ce7ccb21a997d74e3841c` | **cannot** be reproducible |

Two pristine builds agree on the unsigned application and MCUboot. Verification is against the
**frozen file**, never a rebuild.

**Marker hygiene:** `b306-imu-relay-v44`; `confirm_b306_v44.py` added (target-only by default);
`confirm_b306_v43.py` removed rather than left as a trap; `SUPERSEDED.txt` written into both v43
build directories recording *why* v43 was superseded.

**All six offline contract tests PASS:** BT stage, C1 bearer recovery, E1 stall ring, signed-hash
rule, spacing derivation, host-output non-blocking.

**Committed and pushed before any hardware: `4fd28dd53`** on `feature/b306-bringup`.

---

## 5. Does anything here change the plan before hardware?

**No — the plan already changed, in Stage A, and that was the point of the checkpoint.**

Two things would have gone wrong had this run straight through to flashing:

1. **The §3.3 reclassification would have shipped** and rebooted the fleet every few seconds.
2. **The HCI event path would still be blind**, so a wedge there would have gone uncaptured a second
   time — for a second night.

Both are fixed. Stage B proceeds exactly as written.

### 5.1 Two things Stage B must confirm rather than assume

**The single-board validation costs that board its reboot budget.** The budget lives in `.noinit`,
survives the `sys_reboot()` it authorises, and is cleared only by a power cycle. Since the boards are
undocked at the token, **the canary will run the night without a self-reset unless it is re-docked
briefly first.** Plan: dock the canary for a short charge after validation and before the fleet
rollout. If that is not possible, the report will name which board is reduced-capability rather than
leave it silent.

**Two boards need their state read, not assumed:**

* **BSF6C53** — wedged from 12:16 with nothing on the board able to recover it. A power cycle should
  clear it, since the stall is soft state; BSF3C79 and BSFEC35 both came back this way. If it
  answers a PING it is an ordinary fleet member.
* **BSF31CC** — never charged (POGO contact fault). **Confirm it actually charged this time**, since
  "did not charge" and "died early" are indistinguishable in the logs, and that is exactly how it was
  misread once already.

---

## 6. State

Nothing running, no hardware touched, ten boards on charge. Fusion Master on dk-v36. Host patch
**applied** and verified (three files) — the build gates on it, so it stays applied.

**Awaiting `TOKEN: V44 HW GO`.**

---

## 7. Addendum — two Stage-B residuals, resolved before hardware

**Residual 1 — `rx_work_handler` is the only work item on `bt_workq`. CONFIRMED.**
Only `&rx_work` is ever submitted: `hci_core.c:4109` and `:4314`, the same item, handler
`rx_work_handler`. No other work item exists on that queue. The single-entry-point premise that
provable quiescence rests on holds.

**Residual 2 — the 5 s threshold was unsafe after the bracket. FIXED, threshold now 20 s.**
Not hypothetical: **`hci_disconn_complete()` calls `bt_hci_cmd_send_sync()`**, and that is an event
handler dispatched from `hci_event()` on the BT RX WQ. The sync wait is
`HCI_CMD_TIMEOUT = K_SECONDS(10)`. So after v44 the thread can *legitimately* sit inside the bracket
for ten seconds on any slow disconnect — a guaranteed fleet-wide false trigger at 5 s, spending the
one-per-power-cycle reboot budget on nothing. Third false-trigger round, and the largest.
20 s = 2× the 10 s HCI bound, and 10 s below the 30 s ATT timeout so an ATT wedge is still captured
while stuck. Stage 2 measures dwell at `RX_WORK_ENTER→EXIT` across **both** arms and the threshold
moves again if anything approaches it.

### 7.1 Accepted cost of the 20 s threshold — the ring tail no longer covers onset

The v42 ring is 200 × 50 ms = **10 s**, frozen when the corpse is captured, i.e. at
onset + threshold. At 5 s the frozen ring spanned `[onset−5s, onset+5s]` and **covered the onset**.
At 20 s it spans `[onset+10s, onset+20s]` and **does not**.

**Accepted, not overlooked** — v44's primary evidence is the **stage**, not the ring:
`bsf_bt_stage_id` says where the thread is parked, `k_work_busy_get()` gives
`tx_complete_work`'s real state, and the `bt_conn` fields give the connection's. The ring was always
a secondary trajectory.

Alternatives weighed and deferred: 800 entries buys a 40 s span for +32 KB of the ~146 KB free
(real, bulky, and buys a trajectory we are not currently reading); freezing the ring on a lower
threshold than the reset would freeze it on every legitimate slow disconnect, which is the exact
false-positive class the 20 s threshold exists to avoid; coarsening the period to 200 ms gives 40 s
at zero RAM cost but changes the geometry stamp and invalidates every retained ring. **If the ring
tail ever becomes load-bearing, the 800-entry option is the one.**

**Made structurally non-silent:** `bt_corpse_decode.py` now prints, for every corpse, whether its
ring tail reaches the onset — comparing `stage_age_ms` against the 10 s window and saying
`DOES NOT COVER ONSET` outright when it does not. It cannot be lost by someone not noticing.

### 7.2 Decoder brought to the v44 layout — a wire break that would have corrupted every corpse

`stage_max[]` is sized `BSF_BT_STAGE__COUNT`, which v44 raised from 14 to 21. The decoder still had
`STAGE_COUNT = 14`, so **every field after `stage_max` would have been read at the wrong offset** —
silently, with plausible-looking values. Fixed and verified against the ELF: computed corpse size
**840 B**, `sizeof(bsf_corpse_t)` from `nm` **840 B**. Stage names 14–20 added; the classifier now
emits `ATT_ALLOC_RESPONSE_STALL`, `ATT_ALLOC_FOREVER_STALL` and `ACL_RECV_STALL`. The symbolizer's
frozen-artifact gate was repointed to the v44 hashes.

**Canonical v44 (final):** unsigned `df7a543f…`, signed `52dfc924…`, MCUboot `aa252296…`.
