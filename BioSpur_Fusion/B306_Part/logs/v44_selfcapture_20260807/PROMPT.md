# V44 — make the blind region visible, then run

**Batch:** `v44_selfcapture_20260807` · Evidence root: `B306_Part/logs/v44_selfcapture_20260807/` ·
**Copy this file to `<evidence root>/PROMPT.md` as your first action.**

**You are pre-authorized for this entire batch. NEVER prompt the operator for a decision, never
render a choice, never wait for input.** Section 9 covers every branch. Anything ambiguous and not
covered: **record it as `INSUFFICIENT`, take the safest covered branch, keep going, and put it in
the report.**

**Build while the boards charge.** Nothing in Stages 0–2 needs hardware, and starting the run on a
full charge is worth more than starting it two hours earlier on a half-empty one.

---

# 1. What happened, and what it cost

A genuine BT RX wedge hit **BSF6C53 at 12:16:25**, 29.6 minutes into N7. Signature identical to
BSF44AD's: `DATA_PLANE_SILENT` on both streams at once, the `STALL READ` submitted at onset+0
accepted onto the bearer and **never returned**, `terminal=timeout` at 25 s, the bearer torn down at
30 s, 219 `FUSION_QOS` reports continuing afterwards — so the radio stayed healthy while application
data went to exactly zero.

**The trap did not fire. No corpse exists.**

The reason: **`TX_NOTIFY_EXIT` was classified terminal.** That is correct when
`bt_conn_tx_notify()` is called standalone from `bt_conn_set_state()`. It is **wrong** when called
from the head of `bt_conn_recv()`, because the thread then descends into `bt_acl_recv()` → L2CAP →
ATT → GATT — **a region carrying no stage marks at all**. The last mark stays `TX_NOTIFY_EXIT`, the
monitor reads "quiescent", and `unchanged_ms` never accumulates no matter how long the thread is
stuck.

**The root error is classifying a stage by its name instead of by its context**: one stage ID, two
meanings, and the quiescent set can only express one of them.

**And the unmarked region is exactly where the fault lives** — `bt_att_chan_create_pdu()`
(`att.c:722/725`) allocates ATT responses with a 30 s timeout and everything else with `K_FOREVER`.
The 25 s bound and the 30 s ATT timeout both firing on schedule is the signature of a thread parked
precisely there.

---

# 2. Stage 0 — verify the elimination before you instrument

**One result from N7 changes where to aim, and it must be verified rather than taken on trust.**

`TX_NOTIFY_BEFORE_FLUSH` is a **non-terminal** stage and therefore inside the monitor's watched set.
The monitor was **armed** — the 12:13 read showed `rcc=72673/0/0/0/0`, over seventy thousand
successful notifications against a 64-notify arming threshold. **It did not fire.**

**If that holds, the thread was not parked in `k_work_flush()`** — which eliminates the hypothesis
that has been ranked top since J1.

**Verify all three legs from the recorded data and the deployed image, and report each:**

1. `TX_NOTIFY_BEFORE_FLUSH` is genuinely absent from the quiescent set in the **deployed v43** image,
   not merely in today's source.
2. The monitor was genuinely **armed** on BSF6C53 at 12:16:25 — from the armed flag if it is
   recorded, not inferred solely from the notify count.
3. The monitor genuinely **did not fire** — no alarm, no corpse, no reset on that board.

**If all three hold, record `k_work_flush()` as ELIMINATED.** If any fails, say so plainly and put
the flush hypothesis back in play — it changes what §4's optional marker is for.

**Also correct the N7 report:** §2.1 and §5 still say BSF31CC's battery is implausible at 11 minutes
off a full charge. **The operator has since established it never charged — a POGO contact fault.**
It is an ordinary battery death, not a second unexplained dropout. Amend it so it does not sit in
the record as a mystery.

---

# 3. Stage 1 — coverage, not a spot fix

Reclassifying one stage fixes this instance. **It does not fix the class**, and the class is what
cost a wedge.

## 3.1 The audit that must come first

**Enumerate every code path the BT RX workqueue thread can take, and for each state that it can
remain in for an unbounded time, confirm it is either marked or provably non-blocking.**

Any unmarked region is invisible to the monitor by construction, exactly as `bt_acl_recv()` was.
**Report the list.** If a second blind region exists, it is far cheaper to find now than after
another wedge.

## 3.2 Then the marks

Add `ACL_RECV_ENTER` / `ACL_RECV_EXIT` around `bt_acl_recv()`. **Both non-terminal** —
`ACL_RECV_EXIT` is immediately followed by `CONN_RECV_EXIT`, so it is transient.

**Append new enum values. Never renumber existing ones** — decoded corpses depend on the numeric
values, and a renumber silently reinterprets every corpse ever captured.

## 3.3 Reclassify `TX_NOTIFY_EXIT` as non-terminal — but prove it first

The argument is that once §3.2 exists, the only path leaving `TX_NOTIFY_EXIT` as the last mark is
the standalone call from `bt_conn_set_state()`, which is immediately followed by `RESET_RX_BEFORE`.

**That argument depends on there being no third caller. Enumerate every caller of
`bt_conn_tx_notify()` in the deployed tree and confirm the reclassification is safe for all of
them.** If a caller exists where `TX_NOTIFY_EXIT` really is terminal, marking the call sites
distinctly is the correct fix, not reclassifying the shared ID.

After the change the terminal set should be exactly **`IDLE`, `CONN_RECV_EXIT`,
`DEFERRED_RESCHEDULE_AFTER`** — state it explicitly.

## 3.4 The ATT allocation marker — required, not optional

The N7 report lists this as optional on the grounds that §3.2 alone makes the wedge visible.
**Promote it.**

The evidence already points at the ATT allocation path: the 25 s bound and the 30 s ATT timeout both
fired on schedule. `ACL_RECV_ENTER` alone yields "stuck somewhere in ACL / L2CAP / ATT / GATT" — a
large region. **A mark immediately around `bt_l2cap_create_pdu_timeout()` inside
`bt_att_chan_create_pdu()` turns a visible wedge into a named one, for one more line.** Another
blind round costs a day; this costs minutes.

Distinguish the two allocation branches if the code allows — the 30 s response path and the
`K_FOREVER` everything-else path are different verdicts.

## 3.5 The contract test

`test_bt_stage_contract.py:48` currently lists `TX_NOTIFY_EXIT` in `must_be_quiescent` — **the test
enforces the bug.** Move it, add the new stages, and **write the reasoning into the test file**: the
existing "every stage must be classified" rule worked and forced a decision; **what failed was the
decision itself, made by name rather than by context.** A future stage must be classified by which
call sites can leave it as the last mark, not by what it is called.

## 3.6 Freeze everything else

`BT_CONN_TX_NOTIFY_WQ=n`, ACL flow control off, `BT_RX_STACK_SIZE=1024`, `BT_CONN_FRAG_COUNT=1`,
`BT_MAX_CONN=1`, every buffer count unchanged. **v44 is about being able to see the fault, not about
changing it.** The workaround comes after the mechanism is on record.

The host patch stays a repository artifact with its apply/verify script gating the build.

---

# 4. Stage 2 — build and validate

Marker **`b306-imu-relay-v44`**; advance rather than reuse — v43's hashes are published. Add
`confirm_b306_v44.py`, leave `SUPERSEDED.txt` in v43's build dir, remove the stale confirm tool
rather than leaving it as a trap.

**Commit and push before any OTA.** No image enters the fleet unless its exact source tree is
recoverable from git. Report FLASH and RAM against v43 (222,904 B / 44.65 % and 116,164 B /
44.31 %). Two pristine builds, byte-identical on the **unsigned application and MCUboot only** — the
signed artifact cannot be reproducible; gate against the frozen file and never verify by rebuilding.

**Single-board validation, as in v43 — it is what caught the last defect before it reached the
fleet.** Confirm baseline behaviour is unchanged, measure healthy dwell for **every** stage
including the new ones, and force the trigger artificially to verify the whole pipeline end to end:
corpse captured, CRC valid, soft reset, retained record survives, BLE reconnects, corpse exported,
ACK clears it.

**The artificial trigger validates the recorder only. It is never a reproduction of the fault.**

**Note it costs that board its reboot budget for the power cycle** — the budget lives in `.noinit`
and survives the reset it authorises. Charging clears it, so schedule the validation before the
final charge, or accept that one board runs the night without a self-reset and say so.

---

# 5. Stage 3 — deploy and run

**All ten in one batch, no board goes first.** Stage 2 is the canary.

- **Gate on an end-to-end PING from every target, not a connection count.**
- **Target confirmation depends only on the target** — `--preflight-require target-only`. That
  coupling once rolled back nine good images because one board was quarantined.
- Corrected upload bound **417.874 s**, zero write retries, quarantine-and-continue, DK restored to
  canonical after every transaction. Record board IDs, upload durations, `RESETREAS`, hashes.
- **Tag Master physically absent**, confirmed four ways.
- **Spacing `ON / 5000`, positive generation** — dk-v36 derives it at boot and the restore rebuilds
  it, but keep the check; it is the only thing that has ever caught a spacing fault.
- **BSF6C53 will not answer** — it has been wedged since 12:16 and nothing on the board can recover
  it. Quarantine and continue. **BSF31CC** needs its charge verified before it can join; if it
  answers, include it.
- Record the cold-start signature per board: **`init=cold`, `CORPSE present=0`, `reboot_owner=0`.**

Then run: `COUNT=12 PERIOD=10`, slots 1–10, slot 11 empty, main beacon 120,000 µs, sub `SLAVED`,
batch 10, IMU 200/10, listener array capturing throughout. **Abort conditions — only three:** all
nodes gone; Fusion Master down; disk near full. **Run to fleet depletion.**

**On a wedge the board handles itself.** Do not issue `RECONNECT` — it removes a board permanently
while adding nothing. Do not power cycle, dock or intervene.

**Shutdown order:** before stopping the run driver, run a final `CORPSE STATUS` sweep and confirm
every reachable board reads `present=0`. **If any reads `present=1`, do not stop** — keep running
until it has handed the corpse over and been acknowledged. `.noinit` does not survive a charge, and
the sweep is only every 90 s, so the last 90 seconds are a blind spot. State the per-board result in
the report.

---

# 6. If a corpse is captured

**That is the deliverable. Decode it, classify it, put it first.**

```
ATT_ALLOC_RESPONSE_STALL     ACL_RECV_STALL
ATT_ALLOC_FOREVER_STALL      TX_NOTIFY_FLUSH_STALL
RX_RESET_STALL               DEFERRED_RESCHEDULE_STALL
BT_RX_OTHER                  DIAGNOSTIC_FALSE_POSITIVE
                             INSUFFICIENT
```

**Do not force an outcome.** A corpse that names something unexpected is worth more than one made to
agree with the hypothesis — and note that N7 already eliminated the previous favourite.

---

# 7. Deliverables

Yield ladder per node per hour: UWB delivered → **8/8 (histogram; 7/8 second tier)** → position
solved → usable epoch; IMU delivered → no sequence gap → fresh → placeable. Reference: N7 reached
**8/8 = 99.8880 %, 7+/8 = 100.0000 %, zero UWB sweep loss**, all nodes at 8.3334–8.3344 Hz.
**Freshness `INSUFFICIENT` on a static bench.**

**Count losses from sequence jumps, not `q_drop`** — the understatement is tenfold. **IMU `seq` is
16-bit**, wrapping every 327.68 s at 200 Hz; **on a badly degraded board large gaps alias past a
wrap and silently understate the loss** — that is what made BSFAA61 read 35 % when it was 8 %.
**Report the nominal denominator alongside the sequence-accounted one whenever they disagree.**

**Watch BSFAA61's IMU.** It recovered to 98.57 % today after a full power cycle, against 8 % the
night before. Today's window was only 40 minutes; **confirm it holds over a full run** rather than
creeping back with uptime.

`V44_REPORT.md`: any corpse first, then the Stage 0 verification verdict, the coverage audit, the
deployment record, the hourly ladder and the event log. Evidence index with SHA-256. Then **STOP**.

---

# 8. Hard stop

After the first corpse is decoded, **report it**. Do not enable `CONFIG_BT_CONN_TX_NOTIFY_WQ`, raise
`BT_RX_STACK_SIZE`, alter flow control or tune buffers. The workaround comes after the mechanism is
on record.

---

# 9. Standing decisions — never ask, never render a choice

- **Never ask whether to power-cycle, dock, reboot, reconnect or recover any node.** Boards
  stalling, dropping out or dying on battery are measured samples.
- **Never ask whether to stop early.** Only the three abort conditions apply, and the shutdown sweep
  overrides even a scheduled stop when a corpse is outstanding.
- **Never ask whether to run with fewer than ten boards.** Quarantine and continue.
- **Never ask about tool prerequisites.** Build what is missing from the documented recipe with the
  usual gates, record it as a deviation, proceed.
- **If v44 cannot be built or fails validation, deploy v43 unchanged and run.** An exposure night on
  a trap with a known blind spot still accumulates board-hours and still catches anything outside
  that spot. **Never end with nothing running because a decision was unavailable.**
