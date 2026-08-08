# V45_REQUIREMENTS_DELTA — what the forensics changes or confirms

The v45 baseline architecture is **not re-derived here**: three isolated
channels (`MPSL_RX` / `BT_RX_WQ` / `APP_NOTIFY`) with per-channel
seq/stage/trace and writer-TID checks; call-site marking only; the end-to-end
predicate `connected ∧ subscribed ∧ producer_seq advancing ∧
notify_success_seq (COMPLETION-stage) frozen 20 s`; shadow atomic counters
replacing lockless `tx_pending`/`tx_complete` traversal; post-OTA fleet
power-cycle after all `confirmed=1`; append-only enums; schema-checked
decoder. Everything below is a **delta**, each item citing the finding that
forces it.

---

## Δ1 — CONFIRMED, and now the single highest-priority item: count the completion source

**Finding:** `WEDGE_HYPOTHESIS_SCORECARD.md` §4 rank 1; `DATAFLOW_MAP.md` §3.
The only mechanism consistent with every observation is that TX-completion
processing stops permanently. Nothing in 107 board-hours of capture observes
it.

**Requirement.** Three free-running 32-bit counters, sampled into the 1 Hz
telemetry *and* into the stall characteristic:

| counter | increment site | why |
|---|---|---|
| `ncp_events` | `hci_num_completed_packets()`, hci_core.c:578, entry | the completion source itself. If this freezes at onset, H1′ is proven and the fault is below the host. |
| `ncp_handles` | same, per-handle loop body | separates "no events" from "events for another handle". |
| `tx_notify_runs` | `tx_notify_process()`, conn.c:294, entry | the syswq consumer. If `ncp_events` advances and this does not, the fault is the `k_work_submit` / workqueue hand-off, not the controller. |
| `tx_cb_calls` | `cb(conn, user_data, 0)`, conn.c:332 | the actual buffer free. If this advances and `att_pool` still empties, the leak is elsewhere. |

**This is the whole experiment.** Everything else below is supporting.

## Δ2 — CHANGED: sample holders, not availability, and never from the syswq

**Finding:** `POOL_DRAIN_CONSTRAINTS.md` §0 and §2. The 1 Hz pool strobe runs
inside `telemetry_work_handler()` on the **same system workqueue that frees
the buffers, immediately after it frees them**. Measured bias: the scheduled
record reads `att_pool 8/8` in **194 255 of 194 255** N5 records, while the
unbiased stall strobe on the same boards in the same run reads it **empty
12.3 % of the time**.

**Requirements:**
1. **Move the pool strobe off the system workqueue** — sample from the
   `APP_NOTIFY` channel (the notify worker) or a dedicated cooperative
   sampler. A biased instrument that reads "full" is worse than no
   instrument, because it was read as evidence.
2. **`low_water` must become a real minimum.** Fold the reading in at every
   allocation, not once per second. Currently `pool_low_water[]` is written
   at exactly two sites (main.c:523/538 and 3530), both at 1 Hz.
3. **Holder sampling, and only for two pools.** `att_pool` and `acl_tx_pool`
   need `sys_slist_len(&conn->tx_pending)` and the `free_tx` FIFO depth.
   **`hci_rx_pool` does NOT need holder sampling** — §9.4 shows there is no
   possible holder in this configuration (BT RX WQ idle holds none,
   `BT_MAX_CONN=1`, no inbound PDU ever fragments: largest inbound operation
   is 24 bytes against MTU 247 / DLE 251). Instrumenting it would cost code
   and answer nothing. *This is a deletion from the assumed v45 scope.*

## Δ3 — CONFIRMED with a correction: MPSL/SDC-side progress counters are required

**Finding:** `PATH_ACTIVITY_CENSUS.md` §4 — on 3 of 4 events the only
provably active host paths were the notify pipeline, the TX/completion
machinery and the system workqueue. The census cannot see below `bt_hci_recv`.

**Requirement:** a counter incremented in `receive_work`'s handler in
`nrf/subsys/bluetooth/controller/hci_driver.c` (the MPSL workqueue entry
point, `receive_signal_raise()` → `mpsl_work_submit(&receive_work)`,
hci_driver.c:325-329), plus a counter of `bt_hci_recv()` calls by buffer type
(ACL / EVT-prio / EVT-normal).

**Correction to the assumed architecture:** the receive context is the
**MPSL workqueue**, not a dedicated `sdc_rx` thread, and
`CONFIG_BT_CONN_TX_NOTIFY_WQ=n` means completion callbacks run on the
**system workqueue**, not in the receive context (`DATAFLOW_MAP.md` §7). The
three v45 channels must be named for the threads that actually exist:
`MPSL_WQ`, `BT_RX_WQ`, `SYS_WQ` + `APP_NOTIFY`. **`SYS_WQ` is a fourth
channel the baseline design does not have, and it is the one where both the
TX processor and the completion callbacks live.**

## Δ4 — NOT REQUIRED: no trigger arm

**Finding:** `COMMAND_TRIGGER_ANALYSIS.md` — 3 of 4 events had no inbound
operation for 20–45 s; no command class precedes more than one event out of
1 378–7 341 executions; the single coincidence (BSFEC35, a *successful*
232-byte stall read 96 ms before, then a control write 34 ms before) has
p ≈ 0.04 per event. `NODE_INTERNAL_TRIGGER_ANALYSIS.md` — 0 of 4 IMU-recovery
episodes within 60 s against a 1.4–1.9 % chance rate; `uart_restarts` is zero
on every board in every run.

**Requirement:** none. Do **not** build heightened capture around a command
class or around IMU recovery. *This is a deletion from the assumed v45 scope.*

The one cheap thing worth keeping: because §7.2 showed **no** enrichment,
there is nothing v45 must timestamp at sub-second resolution on the
node-internal side. Had it shown enrichment, the list would have been
`imu_hreset`, `imu_hrecover_ok` and `imu_i2c_err`. It did not.

## Δ5 — CONFIRMED: the 20 s threshold is right; per-stream granularity is not needed

**Finding:** `WEDGE_CLASSIFICATION.md` §3. The four wedges last 615 s, 5 341 s,
16 071 s and 19 669 s. The entire near-miss population at a 2 s floor is 22
events, **all inside depletion cascades**, none of the wedge phenotype. The
healthy tail dies out between 1 s and 2 s (835 sub-0.5 s joint stalls in N5,
7 at 1 s, 0 at 2 s).

**Requirements:**
- Keep 20 s. It sits in an empty region: nothing legitimate exceeds 2 s, and
  the shortest wedge is 615 s. Even 5 s would be safe.
- **Per-stream granularity is not needed** for detection: the two data
  streams stop **0.5–1.4 ms apart** on all four events
  (`TERMINAL_NOTIFICATION_ANALYSIS.md` §3). A single joint predicate is
  sufficient and cheaper. Keep per-stream *reporting* for diagnosis, drop it
  from the trigger.
- Add one predicate the current design lacks: **`notify_timeout_drop` (`td`)
  advancing**. It was `0/0/0` on every wedged board and is the earliest
  possible node-side signal that `bt_gatt_notify()` has blocked >1.2 s. It
  fires 18.8 s before the 20 s freeze predicate would.

## Δ6 — CHANGED: capture must survive a null run, and there must be at least three runs

**Finding:** `CROSS_RUN_NECESSITY.md` §5. Pooled rate 1 per 26.8 delivered
board-hours (95 % CI 1-per-9.8 to 1-per-98); N8-only 1 per 15.7.

**Requirement:** for P(≥2 events) ≥ 0.9 —

| assumption | delivered board-hours needed | full-fleet 10-node runs at ~6 h |
|---|---|---|
| N8-only rate (optimistic) | 61 bh | 1 |
| pooled point estimate | 104 bh | 2 |
| pessimistic 95 % bound | 381 bh | **6–7** |

**Plan for three to six runs, not one.** Battery caps a single 10-node run at
about 6 h at full rate (N8: first depletion at 5 h 35 m). The capture harness
must therefore be restartable across runs without losing the corpse, and must
tolerate a run in which nothing happens.

## Δ7 — CONFIRMED and sharpened: `.noinit` is not enough, and the ring must be readable without ATT

**Finding:** `rings.jsonl` is **0 bytes in all four runs** and `CORPSE
present=0` on every board — the v43/v44 traps produced literally nothing, and
`WEDGE_CLASSIFICATION.md`/`DOWNTIME_LEDGER.md` §3 show why: the only thing
that ever cleared a wedge was a **brownout power cycle**, which destroys
`.noinit` (`charging-cuts-power-erases-noinit`). BSFEC35 sat wedged for
3 h 46 min and its state died with the battery.

**Requirement:** the corpse must be retrievable by a path that does not
depend on the wedged node answering ATT. Two options, and the forensics
prefers the first:
1. **Write the ring to internal flash** on the freeze predicate, not to
   `.noinit`. It then survives the power cycle that is the only known cure.
2. If `.noinit` is kept, the operator procedure must be **SWD read before any
   charge or power cycle**, and the run brief must say so — the N8 run lost
   three corpses to exactly this.

## Δ8 — Housekeeping recommendations (out of scope for this task, recorded here)

- `channel.log` duplicates `fusion_h*.log` unrotated: 2 560 MB of the N8
  capture and 2 806 MB of N5 are a second copy. Stop writing it, or stop
  writing `fusion_h*`.
- The listener `.jsonl` is a ~6× expansion of the `.raw.log` and
  `merged_index.jsonl` is a third copy: ~27 GB holding ~2.7 GB of unique
  content.
- Empty `K1..K3_RUN/` and `K1..K3_LISTENERS/` directories in
  `v44_fleet_20260807/` are residue and can be removed.
- **Nothing was deleted, moved or rewritten by this analysis.**
