# N7 — daytime exposure run

**Batch:** `daylight_20260807` · Evidence root: `UWB_Part/logs/daylight_20260807/` ·
**Copy this file to `<evidence root>/PROMPT.md` as your first action.**

**You are pre-authorized for this entire batch. NEVER prompt the operator for a decision, never
render a choice, never wait for input.** Section 7 covers every branch you might otherwise ask
about. Anything genuinely ambiguous and not covered there: **record it as `INSUFFICIENT`, take the
safest covered branch, keep going, and put it in the report.**

**Nothing is flashed and nothing is built.** All ten boards are on `b306-imu-relay-v43`; the Fusion
Master is on `dk-fusion-imu-relay-v36`. **That is deliberate** — this is an exposure run on an
unchanged image, and introducing a variable would waste it.

---

# 1. Why this run exists

The B306 BT RX wedge occurs at roughly **one event per 26.5 board-hours**. v43 is the trap: on a
wedge the board captures a corpse to retained RAM, soft-resets, reconnects, and uploads the previous
life's corpse — no enclosure, no SWD, no human.

Last night was clean: **54.0 board-hours, 2.04 expected, zero observed, P(0) = 0.130.** Uncommon but
entirely ordinary, and **not evidence that anything changed** — the workaround was never applied.

**All ten boards are available now**: 10 × ~7 h ≈ **70 board-hours ≈ 2.64 expected, P(0) = 0.071.**
Cumulative across both runs would be 124 board-hours, **P(0) = 0.0093** — so if this one is also
clean, "nothing changed" starts to become hard to sustain, and the next question becomes whether the
instrumentation moved the race timing.

**Exposure is the deliverable. Everything else is secondary to starting the run and keeping it
running.**

---

# 2. Setup — one PING gate, then go

The operator undocks all ten from a full charge and places them at bench positions. **Undocking is
the power-on, so all ten come alive at once and a single sweep sees the whole fleet** — no
board-by-board check is needed, and BSF44AD's status falls out of that sweep naturally.

Two minutes, then start:

- **Ten boards awake**, each answering an **end-to-end PING**. A connection count is **not**
  acceptable — the Master once reported `count=10 ready=10` while a board could not answer a PING at
  all. **Quarantine any board that does not answer and continue with the rest.**
- **All on `fw=b306-imu-relay-v43`.**
- **Spacing still `ON / 5000` with a positive generation.** dk-v36 now derives this at startup and
  the restore step rebuilds it, but **keep the check** — it is the only thing that has ever actually
  caught a spacing fault, twice in one night.
- **Tag Master physically absent** — both cables — confirmed four independent ways (`lsusb`, sysfs
  product/serial, device nodes, `/dev/serial/by-id`). A Path-M connection contends with UWB timing
  on the tag's nRF52832, so any rate measured with it attached is contaminated, and a previous
  round's authorization claimed absence while the host still had both functions enumerated.
- **Record the cold-start signature per board**: `RESETREAS`, the ring boot verdict, `CORPSE
  present`, `reboot_owner`. After a proper power cycle every board should read **`init=cold`,
  `CORPSE present=0`, `reboot_owner=0`** — that triple was verified on hardware this morning and is
  now the expected signature. **Anything else is a finding worth reporting.** Note the reboot budget
  needs no separate check: charging clears `.noinit`, so it is fresh by construction.

Configuration: `COUNT=12 PERIOD=10`, slots 1–10, **slot 11 empty**, main beacon **120,000 µs**, sub
`SLAVED`, batch 10, IMU 200/10. **Start the listener array capture and keep it running for the whole
run.**

---

# 3. Conduct

**Abort conditions — only three:** all nodes gone; Fusion Master down; disk near full. **Everything
else is a measured sample** — stalls, dropouts, disconnects and battery deaths are the phenomena
being measured. **Run to fleet depletion.**

**Poll throughout with bounded deadlines** and print per-node `DATA_PLANE_SILENT` transitions.

**On a wedge the board handles itself** — corpse, soft reset, reconnect, upload on the next
`CORPSE STATUS` sweep. **Do not issue `RECONNECT`**: it was shown to remove a board permanently while
adding nothing. **Do not power cycle, dock or otherwise intervene.** One `STALL READ` per silence
episode is still worth taking to capture the status snapshot at the moment of silence; nothing
escalates past it.

## 3.1 Shutdown order — the corpse comes off before the board goes on charge

**`.noinit` does not survive a charge cycle.** The run driver sweeps `CORPSE STATUS` every 90 s and
only clears the marker on a correct ACK, so a corpse produced mid-run is collected automatically.

**The exposure is the last 90 seconds.** A board that wedges near the end of the run, before the
sweep reaches it, and is then plugged in to charge, **loses its corpse the instant power is
removed** — and that corpse is the one thing this entire campaign exists to obtain.

So, as a rule:

1. **Before stopping the run driver, run one final `CORPSE STATUS` sweep.**
2. **Confirm all ten read `present=0`.**
3. **Only then may the boards be docked.**
4. **If the final sweep returns `present=1` on any board, do not stop.** Keep the driver running
   until that corpse has been handed over and acknowledged, then sweep again. **Finishing on time is
   worth nothing next to the only artifact anyone wants.**

State the final sweep's per-board result explicitly in the report, so "safe to dock" is a recorded
fact rather than an assumption.

## 3.2 If a corpse is captured

**That is the primary deliverable.** Decode it, classify it, and put it **first** in the report.

```
TX_NOTIFY_BEFORE_SUBMIT_STALL   RX_RESET_STALL
TX_NOTIFY_SUBMIT_STALL          DEFERRED_RESCHEDULE_STALL
TX_NOTIFY_FLUSH_STALL           BT_RX_OTHER
TX_NOTIFY_OTHER                 DIAGNOSTIC_FALSE_POSITIVE
                                INSUFFICIENT
```

**Do not force an outcome.** A corpse that says something unexpected is worth more than one made to
agree with the hypothesis.

Two things the corpse can settle that nothing else has. The watchdog proves the **system workqueue
thread** kept running, but says nothing about whether `conn->tx_complete_work`'s own state is sane —
`k_work_busy_get()` reads that directly. And healthy in-flight stage dwell was measured at
**0.0001 ms**, single-digit CPU cycles, because `k_work_flush()` returns immediately when nothing is
queued — **so healthy and wedged differ by seven orders of magnitude**, and a stage parked at
`TX_NOTIFY_BEFORE_FLUSH` is unmistakable.

## 3.3 Two boards to watch specifically

**BSFAA61's IMU.** It ran at roughly **8 % of nominal for six hours** last night — 358,799 delivered
against a 4,320,000 nominal — while its UWB was flawless, and it was already degraded within the
first five minutes, which looks like a startup or configuration problem rather than drift.

**It has since had a full power cycle**, which clears `.noinit` and gives it a clean start. **If it
is normal today, that is the answer and should be recorded as such.** If it is still degraded, split
the cause with four fields already in telemetry: **`imu_pulls`** (is the pull loop running),
**`imu_hreset` / `imu_hrecover_ok` / `imu_hrecover_fail`** (JY61P health — the normal rate is about
one auto-recovered backstep per 9.44 minutes, orders of magnitude below the ~2 gaps per second
observed), and **`q_drop_imu`** (transport starvation). Report which fired.

**BSF44AD.** Unreachable yesterday, took v43 cleanly this morning in one pass, then went flat on
battery. It is an ordinary fleet member today. If it misbehaves, note that it has had the most
disturbed history of any board — **but do not treat that as an explanation without evidence.**

---

# 4. Deliverables

**Yield ladder per node per hour** — hourly so decay is visible rather than averaged away:

| stream | rungs, each strictly narrower |
|---|---|
| UWB | delivered → **8/8 valid links (histogram; 7/8 second tier)** → position solved → usable absolute epoch |
| IMU | delivered → no sequence gap → fresh → placeable on the shared axis |

Reference: last night reached **8/8 = 99.8503 %**, **7+/8 = 99.9712 %**, **zero UWB sweep loss
fleet-wide**, every node locked to 8.3333–8.3336 Hz for six hours. **Freshness is `INSUFFICIENT` on
a static bench** — label it, do not report a number.

**Event log**, classified against measured signatures:

| signature | appearance |
|---|---|
| **stall** | perfect radio link (`crc_ok` 18–19, `rx_timeout` **0**) and zero application data |
| **dying board** | link collapses (`crc_ok` → 0, `rx_timeout` → 16) |
| **abrupt power loss** | one clean `0x08`, no reboot preamble |
| **battery death with preamble** | `0x08` plus repeated, progressively failing reconnects |

**The last two are the same cause, not two causes** — two boards died 12 minutes apart on identical
cells with opposite signatures and neither was touched. **A clean `0x08` without a preamble never
implies human intervention.**

**Count losses from sequence jumps, not `q_drop`** — the understatement is tenfold. **IMU `seq` is
16-bit**, wrapping every **327.68 s** at 200 Hz, so resolve wraps against host elapsed time:
`real_gap = mod_gap + 65536 × round((elapsed_s × 200 − mod_gap) / 65536)`.

**On a badly degraded board, large gaps can alias past a wrap and silently understate the loss** —
that is exactly what made BSFAA61 look like 35 % when it was 8 %. **Report the nominal denominator
alongside the sequence-accounted one whenever they disagree.**

Every ratio carries numerator and denominator. Every rate uses
`(records − 1) / (last timestamp − first timestamp)`, never a division by the nominal window, and a
locked `COUNT=12 × PERIOD=10` schedule **cannot exceed 8.3333 Hz**. Captures often open with a short
stale block drained at attachment followed by a jump to live records — **using those as endpoints
understates the rate by about 10 %**; split at timestamp discontinuities and exclude the stale
prefix.

`N7_REPORT.md`: **any corpse first**, then the cold-start signature per board, the final
`CORPSE STATUS` sweep result, the hourly ladder, the event log, and the BSFAA61 IMU verdict.
Evidence index with SHA-256. Then **STOP**.

---

# 5. Hard stop

**Do not enable `CONFIG_BT_CONN_TX_NOTIFY_WQ`**, raise `BT_RX_STACK_SIZE`, alter ACL flow control,
change `CONFIG_BT_CONN_FRAG_COUNT`, or tune any buffer. **The mechanism is still not on record**, and
the workaround comes after it is, not before. Today is exposure on an unchanged image.

---

# 6. Traps — each has already cost a session

**6.1** The Tag Master must be **physically** unplugged; confirm, do not assume.

**6.2** Per-target operations must never wait on a fleet-wide condition. One quarantined board once
prevented `ready=10` and **nine successfully uploaded images were never confirmed and all rolled
back.**

**6.3** A connection count is not an end-to-end check. Gate on PING.

**6.4** Rate arithmetic and the stale-prefix artifact — see §4.

**6.5** A diagnostic that shares fate with what it diagnoses is not a diagnostic. This is why the
corpse lives in retained RAM and is exported after a reset, not through the path that fails — and
why §3.1 exists.

**6.6** Do not change `CONFIG_BT_CONN_FRAG_COUNT`. It stays at 1.

---

# 7. Standing decisions — never ask, never render a choice

- **Never ask whether to power-cycle, dock, reboot, reconnect or recover any node.** The firmware
  handles wedges itself. Boards stalling, dropping out, disconnecting or dying on battery are
  measured samples, not failures.
- **Never ask whether to stop early.** Only the three abort conditions apply — and §3.1 overrides
  even a scheduled stop when a corpse is outstanding.
- **Never ask whether to run with fewer than ten boards.** Quarantine and continue — a run with
  seven is worth far more than no run.
- **Never ask about tool prerequisites.** If something is missing, build it from the documented
  recipe with the usual gates, record it as a deviation, and proceed.
- **If something is genuinely ambiguous and not covered here, record it as `INSUFFICIENT`, keep
  running, and put it in the report.**
