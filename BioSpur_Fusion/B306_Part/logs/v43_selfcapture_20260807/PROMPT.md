# V43 — commit, instrument, deploy, run overnight

**Batch:** `v43_selfcapture_20260807` · Evidence root: `B306_Part/logs/v43_selfcapture_20260807/` ·
**Copy this file to `<evidence root>/PROMPT.md` as your first action.**

**You are pre-authorized for this entire batch from start to finish. NEVER prompt the operator for a
decision, never render a choice, never wait for input.** He is asleep. Section 14 decides every
branch you might otherwise ask about. Anything genuinely ambiguous and not covered there:
**record it as `INSUFFICIENT`, take the safest covered branch, keep going, and put it in the
report.**

**The operator has undocked all ten boards and gone to sleep. The battery clock is already
running — endurance is 6–7 h.** Every minute of offline work is a minute of run time lost.
**Get to Stage 3 as fast as is consistent with doing Stages 0–2 correctly, and defer anything not on
the critical path to the report.**

---

# 0. Stage 0 — commit and push, before anything else

**Do this first, before reading further into the build.**

The firmware's git history ends at `b306-imu-relay-v32`. **Everything from v33 to v42 exists only as
uncommitted working-tree state**, so no image deployed since v32 has a recoverable source snapshot —
which is why v36 had to be reconstructed out of DWARF. The working tree is a single state, currently
v42, so **v41's source text — running on nine boards until tonight — no longer exists anywhere.**

1. **Commit the current working tree as the v42 baseline** and push it. Do not tidy, refactor or
   reformat anything on the way — commit what actually built v42, and say so in the message.
2. Record the commit hash, the west manifest revision, the toolchain identity, and the SDK path.
3. **Commit and push again at the end of Stage 1**, so the exact v43 source is recoverable before
   any OTA.

**No image may enter the fleet unless its exact source tree can be recovered from git.** State v33–v41
as permanently unrecoverable as source text rather than pretending otherwise — DWARF supports
comparison, not regeneration.

---

# 1. What this round is for

The B306 occasionally stops exporting UWB and IMU while its BLE link stays alive and its tag keeps
transmitting. Six-plus events, roughly **one per 26.5 board-hours**, one board at a time.

The failure is now localised. The controller terminated BSF44AD's link 67 ms after being told to,
and the peripheral's application **still believed it was connected** — so the host never consumed the
Disconnection Complete. `disconnected()` is dispatched from the **system workqueue**, which is
provably alive throughout (it feeds the watchdog and no board ever reset). So the callback was
dispatched from a healthy queue and still never ran, which means **BT RX WQ never reached the
`k_work_reschedule()` at the end of `bt_conn_set_state(DISCONNECTED)`**.

The three operations there are:

```
bt_conn_tx_notify(conn, true)   ← the only one that can block: k_work_flush(), unbounded
bt_conn_reset_rx_state(conn)
k_work_reschedule(&conn->deferred_work, K_NO_WAIT)   ← never reached
```

And `bt_conn_recv()` calls the same `bt_conn_tx_notify(conn, true)` **before processing every
incoming ACL packet** — so a wedge there explains the export stall itself, not just the disconnect
failure. One wedge point, all four symptoms.

**Ten boards in parallel give a mean time to first event of about 2.65 h.** This round makes every
sealed board its own debugger: wedge → corpse to retained RAM → soft reset → reconnect → upload the
previous life's corpse. **No enclosure opened, no SWD.**

---

# 2. Hard freeze — do not fix the fault you are trying to observe

Keep every one of these exactly as deployed:

```
CONFIG_BT_MAX_CONN=1                 CONFIG_BT_ATT_TX_COUNT=8
CONFIG_BT_BUF_ACL_RX_COUNT=6         CONFIG_BT_CONN_TX_MAX=8
CONFIG_BT_BUF_EVT_RX_COUNT=10        CONFIG_BT_L2CAP_TX_BUF_COUNT=8
CONFIG_BT_BUF_ACL_TX_COUNT=8         CONFIG_BT_RX_STACK_SIZE=1024
CONFIG_BT_HCI_ACL_FLOW_CONTROL=n     CONFIG_BT_RX_PRIO=8
CONFIG_BT_CONN_TX_NOTIFY_WQ=n        CONFIG_BT_CONN_FRAG_COUNT=1
CONFIG_BT_RECV_WORKQ_BT=y            CONFIG_BT_RECV_WORKQ_SYS=n
```

**Do not** enable `BT_CONN_TX_NOTIFY_WQ` or ACL flow control, resize any Bluetooth buffer or the RX
stack, change BT RX priority, connection parameters, MTU, notification pacing, producer behaviour,
UART behaviour, or move flash operations. **Do not remove `boot_write_img_confirmed()`.** Add no
runtime flash or NVS write for diagnostics.

`BT_CONN_TX_NOTIFY_WQ=y` is Nordic's documented workaround for exactly this
RX→TX-notify→system-workqueue dependency (NCSDK-29354, nRF52 Series, NCS 2.8.0). **Enabling it now
would very likely hide the fault before it has been recorded.**

---

# 3. Naming, and the two reset authorities

**The marker is `b306-imu-relay-v43`.** `v42` is taken — built, hashes published,
`confirm_b306_v42.py` exists. Two byte sequences under one marker is what retired v19. Add
`confirm_b306_v43.py`; leave v42's build dirs with `SUPERSEDED.txt` and remove its confirm tool
rather than leaving it as a trap.

**v42 already has a reset authority**: the ring's `k_timer` ISR does one `sys_reboot()` per power
cycle after `bsf_stall_ring_freeze()` returns true, bounded by `isr_resets` in retained RAM, with two
refund paths deliberately closed.

**v43 adds a second detector with its own reset. Reconcile them in code, not by timing luck:**

- state which authority wins and enforce the precedence explicitly
- **keep both** — they capture different things. v42's ring is a 50 ms trajectory of queue depth,
  heartbeat, publisher entry/exit and pool availability; v43's corpse is BT-host stage state. **A
  corpse carrying the ring tail is strictly better than either alone.**
- **one reboot budget shared between them**, never one each

---

# 4. Provenance of the Host patch — the mistake that must not repeat

**`~/ncs/v2.8.0` is a shared SDK install outside the repository.** Editing
`zephyr/subsys/bluetooth/host/conn.c` in place would affect every other project built against that
SDK, would **not** be captured by this project's git, and would be **silently lost** on an SDK
reinstall or update. Given §0, that is the same provenance failure one level deeper, and it would
make a deployed image unreproducible by construction.

Required:

- keep the Host modification as a **patch file inside the repository**, with a script that applies
  and verifies it
- **the build gates on the patch being applied and hashing correctly, and refuses to build
  otherwise**
- record SDK identity alongside; commit both before any OTA

---

# 5. Stage instrumentation, in the actual v2.8.0 Host source

Instrument the **deployed** tree, not upstream main. Find this build's equivalents of
`bt_conn_recv()`, `bt_conn_tx_notify()`, `bt_conn_reset_rx_state()`, the disconnect-complete state
path, and `deferred_work` scheduling.

Minimum stages:

```
IDLE
CONN_RECV_ENTER
TX_NOTIFY_ENTER
TX_NOTIFY_BEFORE_SUBMIT     TX_NOTIFY_AFTER_SUBMIT
TX_NOTIFY_BEFORE_FLUSH      TX_NOTIFY_AFTER_FLUSH
TX_NOTIFY_EXIT
RESET_RX_BEFORE             RESET_RX_AFTER
DEFERRED_RESCHEDULE_BEFORE  DEFERRED_RESCHEDULE_AFTER
CONN_RECV_EXIT
```

If the actual implementation has materially different substeps, **instrument those and document the
difference** rather than forcing this list onto it.

Each transition records a monotonic sequence number, the stage ID, and a cycle timestamp.

**On the normal path each transition may only write RAM.** No `LOG_*`, no allocation, no sleep, no
mutex, no semaphore, no work submission, no flash. Atomic or single-copy-safe integer stores only.
**The instrumented build must behave like v41.**

---

# 6. RAM flight recorder

Lock-free fixed circular trace, 64–128 entries, roughly
`{ uint32_t cycles; uint16_t stage; uint16_t event; uint32_t arg; }` — 128 × 12 B ≈ 1.5 KiB. Never
blocks; overwriting the oldest is fine.

**Not RTT.** RTT is already full and skipping on every board, which is exactly how the thread-analyzer
output has been silently discarded all campaign.

---

# 7. The independent monitor

A dedicated thread that must **not** run on the BT RX workqueue or the system workqueue, and must not
depend on BLE for its own liveness. Wakes at about 1 Hz, reads stage + sequence + timestamp, sleeps.

**Wedge criterion: a BT RX stage representing an in-progress operation that should complete in
microseconds or milliseconds has not changed for ≥ 5 s.**

**Do not trigger merely because application notifications stopped.** That readmits the producer, RF,
connection scheduling, the central and the application — all already excluded, and each a source of
false positives this criterion does not have.

**Measure the maximum healthy duration of every instrumented region during Stage 2** and confirm the
threshold has large margin. Report the measured distribution rather than assuming one.

---

# 8. The corpse

Captured **before** any recovery.

**Header** — magic, schema version, firmware version and hash, board ID, uptime, wedge counter, and
`RESETREAS` captured as early in startup as possible, before anything clears it.

**BT RX** — stage, stage sequence, stage age, thread state, thread address, saved stack pointer and
context where safely available, stack size, stack high-water or unused bytes if safely available.

**Connection** — `bt_conn` address, `state`, `err`, `handle`, `rx` pointer and state.

**TX synchronisation** — `k_work_busy_get(&conn->tx_complete_work)`, which returns the
`K_WORK_DELAYED` / `QUEUED` / `RUNNING` / `CANCELING` / `FLUSHING` bits directly; `tx_pending` and
`tx_complete` state and counts; `in_ll` if present.

**Deferred disconnect** — `conn->deferred_work` busy/state flags. **If it was never scheduled while
the controller had already completed the disconnect, that independently confirms
`bt_conn_set_state()` never reached its final `k_work_reschedule()`.**

**Liveness** — system-workqueue heartbeat, producer sequence, notification sequence, last successful
BT RX sequence.

**Trace** — the flight-recorder tail and the v42 ring tail.

**Read every internal field against the actual v2.8.0 struct layout**, never against current Zephyr
main.

**Optional, high value, must not block the round:** if this SDK and architecture support safely
walking the parked BT RX thread with the architecture stack-walk API and it validates, capture a
bounded array of return PCs. If not, **do not force a fragile unwinder into production** — preserve
the saved thread context, the saved PSP and the BT RX stack memory so the context can be
reconstructed offline.

---

# 9. Retained RAM, then reset

Store the corpse in a dedicated `.noinit` region: magic, schema version, payload length, CRC32,
**valid flag written last**.

**Do not write it to NVS, settings, internal flash or external flash.** Flash × system workqueue ×
`bt_conn_tx_notify` is still in the suspicion tree; a diagnostic that creates a new flash operation
would contaminate the experiment it serves.

After the corpse is complete and its CRC finalised, perform a **software** reset that preserves the
retained region. **Do not wait for the existing watchdog** — it is fed from `telemetry_work_handler`
on the system workqueue, which stays alive through this failure, so it is **structurally blind** to a
BT RX wedge.

Verify on hardware in Stage 2 that the reset preserves the region, that BSS/data init does not
overwrite it, and that a power-on or brownout leaves a stale or corrupt corpse **safely rejected**.
**Never trust retained data without magic and CRC** — a plausible-looking corpse assembled from
uninitialised RAM is worse than none.

---

# 10. Post-boot export

On every boot, if magic and CRC validate: **retain until positively acknowledged**, advertise and
connect normally, and export through the existing B306 → BLE → Fusion Master path using the smallest
backward-compatible diagnostic frame. If the Fusion Master must change to carry it, make that a
separate documented change with its own marker.

**Only a positive ACK may clear the valid marker.** No ACK ⇒ retransmit after the next reconnect or
reboot.

The host logger writes one immutable record per corpse: board ID, firmware hash, corpse sequence,
reset reason, decoded stage, raw payload. A symbolisation script takes a payload plus **the exact v43
`zephyr.elf`**; never symbolise against a different build, and gate against the frozen signed file
rather than a rebuild — the signed artifact is not byte-reproducible.

---

# 11. Stage 2 — single-board validation, then straight on

Before the fleet OTA, on one board:

- data behaviour equivalent to baseline; UWB at its ceiling; IMU healthy; BLE throughput not
  measurably degraded; no new pool or buffer failures; monitor CPU cost negligible
- **healthy stage durations measured** — this is what justifies the 5 s threshold
- **artificially force the trigger without reproducing the BLE failure** and verify the whole
  pipeline: corpse captured, CRC valid, software reset, retained record survives, BLE reconnects,
  corpse exported, ACK clears it

**The artificial trigger validates the recorder and recovery pipeline only. It must never be reported
as a reproduction of the actual fault.**

**This single-board step is the canary. Do not add a second staged rollout.**

---

# 12. Stage 3 — recover BSF44AD, OTA all ten, run

## 12.1 BSF44AD

BSF44AD has been held wedged and powered on a charging POGO that deliberately does not cut power.
**The operator has now power-cycled it and returned it to the fleet.** Its retained ring is gone with
the power removal, and that is accepted — it was unreadable without SWD and that route was declined.

Treat it as an ordinary fleet member. If it does not come back after the power cycle, **quarantine
and continue with the rest** — do not spend the night on it.

BSFC2CC likewise: it was wedged and has since been charged, so it may or may not return. Same rule.

## 12.2 Rollout

**All ten in one batch, no board goes first.** Stage 2 was the canary; a second staged rollout would
violate the standing rule — staged rollouts have twice produced asymmetries that made a later failure
hard to diagnose.

- **Gate the pre-rollout check on an end-to-end PING from every target, not a connection count.** The
  Master once reported `count=10 ready=10` while a board could not answer a PING at all.
- **Target confirmation depends only on the target** — identity, bridge readiness, two-command round
  trip. **Never on a fleet-wide ready count**: that coupling once rolled back nine good images
  because one board was quarantined. Use `--preflight-require target-only`.
- Corrected upload bound **417.874 s**, zero write retries, quarantine-and-continue, DK restored to
  canonical after every updater transaction. Record board IDs, upload durations, `RESETREAS`, and
  resulting hashes.
- **If the DK is reflashed, rebuild spacing to `ON / 5000` and confirm a positive generation before
  anything else** — flashing the DK wipes its runtime configuration, and this has invalidated a
  window before.
- **Tag Master is physically unplugged** — the operator has removed both cables. Confirm and record
  the absence rather than assuming it.

## 12.3 The run

`COUNT=12 PERIOD=10`, slots 1–10, slot 11 empty, main beacon **120,000 µs**, sub `SLAVED`, batch 10,
IMU 200/10. **Start the listener array capture and keep it running.**

**Abort conditions — only three:** all nodes gone; Fusion Master down; disk near full. **Everything
else is a measured sample.** Run to fleet depletion.

**Poll throughout with bounded deadlines** and print per-node `DATA_PLANE_SILENT` transitions.

**On a wedge, the board handles itself** — corpse, reset, reconnect, upload. **Do not issue
`RECONNECT`**: it has been shown to remove a board permanently while adding nothing. **Do not power
cycle, dock or otherwise intervene.**

**Stop the root-cause phase after the first high-quality natural corpse is captured and decoded** —
but keep the run going for the remaining exposure and the yield data.

---

# 13. Deliverables

Yield ladder per node per hour: UWB delivered → **8/8 valid links (histogram; 7/8 second tier)** →
position solved → usable absolute epoch; IMU delivered → no sequence gap → fresh → placeable.
Reference: the last runs reached **99.9053 %** and **ge7 100.0 %** at 8/8. **Freshness is
`INSUFFICIENT` on a static bench** — label it, do not report a number.

Event log classified against measured signatures: **stall** = perfect radio link (`crc_ok` 18–19,
`rx_timeout` 0) with zero application data; **dying board** = link collapses (`crc_ok` → 0,
`rx_timeout` → 16); **abrupt power loss** = one clean `0x08` with no reboot preamble; **battery death
with preamble** = `0x08` plus repeated, progressively failing reconnects. **The last two are the same
cause, not two causes** — two boards died 12 minutes apart on identical cells with opposite
signatures and neither was touched. **A clean `0x08` without a preamble never implies human
intervention.**

**Count losses from sequence jumps, not `q_drop`** — the understatement is tenfold. **IMU `seq` is
16-bit**, wrapping every 327.68 s at 200 Hz:
`real_gap = mod_gap + 65536 × round((elapsed_s × 200 − mod_gap) / 65536)`.

Every ratio carries numerator and denominator. Every rate uses
`(records − 1) / (last timestamp − first timestamp)`, never a division by the nominal window, and a
locked `COUNT=12 × PERIOD=10` schedule **cannot exceed 8.3333 Hz**. Captures often open with a short
stale block drained at attachment followed by a jump to live records — **using those as endpoints
understates the rate by about 10 %**; split at timestamp discontinuities and exclude the stale prefix.

Classification for any corpse:

```
TX_NOTIFY_BEFORE_SUBMIT_STALL   RX_RESET_STALL
TX_NOTIFY_SUBMIT_STALL          DEFERRED_RESCHEDULE_STALL
TX_NOTIFY_FLUSH_STALL           BT_RX_OTHER
TX_NOTIFY_OTHER                 DIAGNOSTIC_FALSE_POSITIVE
                                INSUFFICIENT
```

If `stage == TX_NOTIFY_BEFORE_FLUSH` persists until the monitor fires **and** the work state supports
it, classify `TX_NOTIFY_FLUSH_WEDGE_CONFIRMED`. **Do not force this outcome** — a corpse that says
something else is worth more than one made to agree with the hypothesis.

`V43_REPORT.md` with the corpse first if there is one, then the git and provenance record, the
measured healthy stage durations, the deployment record, the hourly ladder and the event log.
Evidence index with SHA-256.

---

# 14. Standing decisions — never ask, never render a choice

- **Never ask whether to power-cycle, dock, reboot, reconnect or recover any node.** The firmware
  handles wedges itself. Boards stalling, dropping out, disconnecting or dying on battery are
  measured samples, not failures.
- **Never ask whether to stop early.** Only the three abort conditions apply.
- **Never ask whether to run with fewer than ten boards.** Quarantine and continue — a run with seven
  is worth far more than no run.
- **Never ask about tool prerequisites.** If a marker port or a missing helper blocks the rollout,
  build it from the documented recipe with the payload-hash and frozen-core gates, record it as a
  deviation, and proceed.
- **Never ask about scope.** If part of §5–§10 cannot be implemented safely, implement the rest,
  record precisely what was omitted and why, and continue.

## 14.1 The fallback, so the night is not wasted

**If v43 cannot be built and validated cleanly, deploy v42 instead and run.**

v42 is already built, canonical and verified: FLASH 219,596 B, RAM 112,452 B, unsigned
`41a11013…`, MCUboot `aa252296…`, `confirm_b306_v42.py` present. It carries the 50 ms ring, the
geometry-validated retained region, and the ISR-driven soft reset that freezes the ring and preserves
it across the reset. **It will capture a trajectory and self-recover on the next wedge** — less than
v43 gives, but far more than an idle night.

Decision order, no input required:

1. v43 builds and passes Stage 2 → **deploy v43, run.**
2. v43 fails to build, or fails Stage 2 → **deploy v42, run.** Record why v43 was not deployed.
3. Neither can be deployed → **stop cleanly, leave the field resumable, write the report.**

**Never end the night with nothing running because a decision was unavailable.**

---

# 15. Traps — each has cost a session

**15.1** Flashing the DK wipes its runtime configuration → `SPACING OFF / 7500`. Rebuild to ON/5000
and confirm a positive generation.

**15.2** The Tag Master must be **physically** unplugged — a Path-M connection contends with UWB
timing on the tag's nRF52832. Confirm absence; a previous round's authorization claimed it while the
host still had both functions enumerated.

**15.3** Per-target operations must never wait on a fleet-wide condition. One quarantined board once
prevented `ready=10` and **nine successfully uploaded images were never confirmed and all rolled
back.**

**15.4** A connection count is not an end-to-end check. Gate on PING.

**15.5** Rate arithmetic and the stale-prefix artifact — see §13.

**15.6** Outer bounds must exceed the operations they wrap. A 60 s bound once killed an upload
needing ~21 s of transfer.

**15.7** A diagnostic that shares fate with what it diagnoses is not a diagnostic. This is why the
corpse lives in retained RAM and is exported after a reset, not through the path that fails.

**15.8** Do not change `CONFIG_BT_CONN_FRAG_COUNT`. It stays at 1.

---

# 16. Hard stop

After the first natural corpse is decoded: **report it.** Do **not** enable
`CONFIG_BT_CONN_TX_NOTIFY_WQ`, increase `BT_RX_STACK_SIZE`, alter flow control or tune buffers. The
workaround comes after the mechanism is on record, not before.

End with:

`=== V43 COMPLETE — <CLASSIFICATION> — <DEPLOYED IMAGE> — WORKAROUND NOT APPLIED — STOP ===`
