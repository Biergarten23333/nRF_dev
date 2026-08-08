# COUNTER_SEMANTICS — every counter used in this analysis, from source

Blocking prerequisite for §1.3. Nothing below is inferred from a counter's
name. Every row cites the increment site.

Sources (sha256 in `INPUT_MANIFEST.json`):
- node: `B306_Part/firmware/src/main.c` (v0.1.44-imu-relay), `imu.c`, `strobe_capture.c`
- master: `B306_Part/host/fusion_master/src/main.c`
- stack: `/home/zekaixiao/ncs/v2.8.0/zephyr/subsys/bluetooth/host/*` — note
  this tree carries the project's own `BSF_BT_STAGE_*` patches, i.e. it is
  the tree the boards were actually built from, not stock NCS.

Stage classes used below:
- **SUBMISSION** — increments when work is handed to the next layer.
- **CALL-RETURN** — increments when a *host API call* returns; says nothing
  about the radio.
- **COMPLETION** — increments when the controller has confirmed the packet.
- **MASTER-SIDE** — counted on the DK, not on the node.

> **There is no COMPLETION-stage counter anywhere in this system.** Not on
> the node, not on the master. Every "delivered" number is either a node-side
> call return or a master-side receive count. This is the single most
> important fact in this table and it is why §14 asks for one.

---

## 1. Node — notification pipeline

| counter | site | stage | width / reset | meaning |
|---|---|---|---|---|
| `producer_heartbeat` | main.c:1514, 1518 | SUBMISSION | atomic32, cold boot | +1 per UWB or IMU record handed to `publish_data_record()`. Proves the *producers* (UART parser, IMU pull) are alive independent of BLE. |
| `enq_imu` / `enq_uwb` / `enq_ctl` | main.c:1501, 1482, (ctl equiv.) via `record_enqueue_duration()` | SUBMISSION | atomic32, cold boot | +1 per record placed in the per-stream `k_msgq`. `put_drop_oldest()` (main.c:~1440) never fails, so this equals records offered. |
| `q_drop_imu/uwb/ctl` | main.c `put_drop_oldest()` | SUBMISSION | atomic32 | +1 each time the producer evicted the **oldest** queued item to make room. Understates true loss whenever the same congestion also causes upstream drops. |
| `q_hwm_*` | `update_queue_high_water()` | SUBMISSION | atomic32, monotone max | deepest msgq occupancy seen. |
| `abort_imu/uwb/ctl` | main.c:1494 and siblings | SUBMISSION | atomic32 | record larger than the queue item payload (`-EMSGSIZE`). Pre-queue reject. |
| `notify_timeout_drop[3]` → wire `td=i/u/c` | main.c:1539, from main.c:1565 | SUBMISSION | atomic32 | **+1 every time `k_sem_take(&notify_idle_sem, K_MSEC(1200))` failed**, i.e. the notify worker was still inside a previous `bt_gatt_notify()` for more than `NOTIFY_ACCEPT_TIMEOUT_MS = 1200` (main.c:78). *Any nonzero `td` is direct evidence that `bt_gatt_notify()` blocked >1.2 s.* Exported **only** through the STALL characteristic (master main.c:2807), never in 1 Hz telemetry. |
| `publisher_count` → `pn` | main.c:1594 | **CALL-RETURN** | atomic32 | +1 after `bt_gatt_notify()` **returns**, success or failure. A call that never returns never increments it. |
| `publisher_max_us` | main.c:1595 `atomic_update_max` | CALL-RETURN | atomic32, monotone max | duration of the longest *returned* call. **A permanently blocked final call leaves this at its previous, normal value.** Binding caveat, carried everywhere. |
| `notify_ok` | main.c:1604 | **SUBMISSION** (not completion) | atomic32 | `bt_gatt_notify()` returned 0 = the ATT PDU was allocated from `att_pool` and queued on the connection TX queue. With `CONFIG_BT_ATT_SENT_CB_AFTER_TX=n` (K1) the call does **not** wait for the controller. |
| `drop_err` / `notify_errno` | main.c:1610–1611 | CALL-RETURN | atomic32 | non-zero return; `notify_errno` latches the last errno. |
| `drop_unsub` | main.c:1560 | SUBMISSION | atomic32 | peer not subscribed → early return **before** the worker runs, so `publisher_count` is *not* incremented. This is the whole explanation of `Σenq − publisher_count ≈ drop_unsub` in steady state. |
| `notify_rc_nomem/notconn/again/other` → `rcc=` | main.c:1612–1615 | CALL-RETURN | atomic32 | breakdown of `drop_err`. STALL characteristic only. |
| `entry_count` / `exit_count` → `e=` / `x=` | main.c:1573 (entry), 1600 (exit), in `.noinit` `retained_stall` | CALL-RETURN | uint32, survives soft reset only | `e > x` ⇒ the notify worker is **inside** `bt_gatt_notify()` right now, and `entry_ms`/`in_call_age_ms` say for how long. STALL characteristic only, so unavailable on a node that cannot answer ATT. |

### Backlog arithmetic — the one legal form
`enq_imu + enq_uwb + enq_ctl` (node, SUBMISSION)
` − publisher_count` (node, CALL-RETURN)
` − drop_unsub` (node, pre-worker)
` − (td_i + td_u + td_c)` (node, pre-worker)
` − Σ q_drop_*` (node, pre-worker)
= records currently sitting in the msgqs **plus at most one inside the call**.

`delivered_imu/uwb/ctl` **must never appear in that expression.** Confirmed
master-side: `record.delivered_* = peer->delivered_*` (master main.c:1240-1242,
incremented at master main.c:1988, 2049 when a notification is *received*).
Mixing it with `enq_*` is the exact trap §1.3 warned about, and it is real:
`enq` counts on the node, `delivered` counts on the DK.

---

## 2. Node — liveness, IMU health, UART, timer

| counter | site | notes |
|---|---|---|
| `watchdog_feeds` | main.c:250, 264 (`watchdog_feed_once()`), called at main.c:3232 — the **first statement of `telemetry_work_handler()`** | The handler is a `K_WORK_DELAYABLE` (main.c:3228) and re-arms itself with `k_work_reschedule(&telemetry_work, K_SECONDS(1))` at main.c:3420, the **last** statement, with **zero early returns in the body** (verified by scan of lines 3230–3422). WDT is `WDT_FLAG_RESET_SOC`, `WATCHDOG_TIMEOUT_MS`. **Consequence used throughout this report: an unreset node has a system workqueue that reached the tail of that handler every second.** |
| `reset_reason` | main.c:3265 ← `boot_reset_reason` (hwinfo at boot) | boot cause bitmask; segments the timeline into boot segments. |
| `imu_hreset` … `imu_hi2c` | imu.c health layer; `stats->health_reset = health_state.reset_count` (imu.c:1815) | per-fault-class counters. |
| `imu_hrecover_ok` / `imu_hrecover_fail` | imu.c:381 / 383 | recovery episode outcome. Fleet-wide `ok == reset`, `fail == 0`, i.e. the layer masks every episode. |
| `imu_i2c_err`, `imu_missed_deadlines`, `imu_pull_late_max_us`, `imu_pull_dur_max_us`, `imu_fault_us`, `imu_recovered_us`, `imu_last_good_us` | imu.c stats block | µs fields are node TIMER-domain uptimes, not wall clock. |
| `uart_restarts` | main.c:2111 | DWM-link recovery; `uart_err` latches the last error. |
| `timer_wraps` | strobe_capture.c:221 (`atomic_inc(&timer_wrap_count)`), exported strobe_capture.c:478 | TIMER2 low-32 wrap at 2^32 µs = 71.58 min. |
| `frames`, `crc`, `header`, `ring_drop`, `sweep_drop`, `duplicate`, `reorder` | UART parser | DWM→B306 link integrity, independent of BLE. |

## 3. Node — net_buf pools (`FUSION_POOL`)

`sample_pool_usage()` (main.c) iterates `STRUCT_SECTION_FOREACH(net_buf_pool)`
inside `telemetry_work_handler()`, i.e. **on the system workqueue, once per
second**, and emits `name_hash:available/low_water`.

`low_water` semantics were changed in v43+ from since-boot to per-window,
`atomic_set(&pool_low_water[i], available)` after reading the window minimum.
The source comment describes it as "minimum available since the previous
record". **That description is misleading and the correction is load-bearing
for §9.**

`pool_low_water[]` is written at exactly two places in the entire firmware:
`main.c:523/538` inside `sample_pool_usage()`, and `main.c:3530` at init.
**There is no sub-second observer** — no allocation hook, no ISR fold-in.
Therefore

> `low_water` = `min(previous sample's avail, this sample's avail)` — a
> **two-point minimum over two 1 Hz strobes**, not a continuous minimum.

A dip that begins and ends between two strobes is invisible, and `avail ==
low_water == max` means only "the pool was full at both of the last two
strobes". §9 must be written against a **1 Hz strobe**, not a window minimum.
`FUSION_STALL_POOLS` adds an extra strobe whenever the stall characteristic
is read, which is the only sub-second-adjacent pool evidence that exists.

The delivery limitation is separate and also real: the strobe that would
straddle the onset is generated on the syswq but enqueued behind the wedge,
so it never arrives.

Pool identities — brute-forced FNV-1a/32 (`pool_name_hash()`, main.c:498,
`h = 2166136261; h = (h ^ byte) * 16777619`) over every `NET_BUF_POOL*DEFINE`
name in the NCS tree; each hash has exactly one preimage, and the emitted
order is alphabetical, which is an independent consistency check:

| hash | name | node size | source |
|---|---|---|---|
| `11597b73` | `acl_tx_pool` | 8 | `CONFIG_BT_BUF_ACL_TX_COUNT=8` |
| `858969d7` | `att_pool` | 8 | `CONFIG_BT_ATT_TX_COUNT=8` |
| `a14875f8` | `discardable_pool` | 3 | buf.c:38 |
| `2de570ea` | `fragments` | 1 | conn.c:147, `CONFIG_BT_CONN_FRAG_COUNT=1` |
| `39b3fc03` | `hci_cmd_pool` | 2 | hci_core.c |
| `20588eb5` | `hci_rx_pool` | 10 | `MAX(EVT 10, ACL 6)` |
| `ef427c73` | `pkt_pool` | 4 | **MCUmgr SMP transport**, `zephyr/subsys/mgmt/mcumgr/transport/src/smp.c:42` — the OTA path, node only |
| `27b70977` | `sync_evt_pool` | 1 | buf.c:36 |

§1.5 resolved: `27b70977` = `sync_evt_pool`, `ef427c73` = `pkt_pool`.

## 4. Master (`FUSION_QOS`, master main.c:1206–1272)

`qos_work_handler()` runs every `QOS_WINDOW_MS` (~1005 ms measured) and does
`memset(&peer->qos, 0, ...)` at the end of each window.

| field | reset each window? | meaning |
|---|---|---|
| `reports`, `event_gaps`, `crc_ok`, `crc_error`, `nak`, `rx_timeout`, `first_event`, `last_event`, `channels[0..36]` | **YES — per-window** | from the DK controller's per-connection-event reports. `reports` ≈ connection events actually observed in the window. |
| `delivered_imu/uwb/ctl` | **NO — cumulative** | lives on `peer`, outside the memset'd `peer->qos`. Cumulative count of records the master *received* from this node. |
| `handle` | — | HCI connection handle; changes across reconnects. |
| `spacing_generation` | — | anchor-spacing epoch; `1` throughout N8. |

`FUSION_QUEUE` and `FUSION_POOL` are node payloads **re-printed by the
master**, with the master's `delivered_*` appended (master main.c:1489, 1507).
So one `FUSION_QUEUE` line legitimately mixes node-side and master-side
counters in a single record — the field names do not warn you.

## 5. Master-side `-ENOMEM` (=-12)

Returned by the master's own GATT write/read path once its per-connection
ATT bearer is stuck after a 25 s request timeout. **It is a statement about
the DK's bearer state, not a node counter.** §0.1(4).

## 6. §1.6 provenance — corrected

The brief expected node `FUSION_QUEUE`/`FUSION_POOL` to be v44-only. **It is
not.** Record-type histograms over the complete logs:

| run | fw | FUSION_QUEUE | FUSION_POOL | FUSION_TELEMETRY | FUSION_QOS |
|---|---|---|---|---|---|
| N5 | v43 | 194 260 | 194 255 | 194 261 | 193 437 |
| N6 | v43 | 382 | 382 | 381 | 380 |
| N7 | v43 | 20 888 | 20 887 | 20 887 | 21 420 |
| N8 | v44 | 169 843 | 169 842 | 169 836 | 195 581 |

**§9 pool constraints therefore apply to every run, not only N8.** The
v44-only records are the newer master-side lifecycle lines
(`FUSION_CONNECTED`, `FUSION_DISCONNECTED`, `FUSION_PHY_*`, `FUSION_DLE_*`,
`FUSION_CI_*`, `FUSION_ATT_MTU`, `FUSION_TARGET`, `FUSION_FAIL`,
`FUSION_SCAN_WAITING`) — those are dk-v36 additions, and their absence in
N5/N7 is a **master-firmware** difference, not a node-firmware one.

`pools.jsonl` and `FUSION_MASTER_POOL` are the **master's own** net_buf pools
(7 pools, no `pkt_pool`, larger counts: acl_tx 32, att 32, hci_rx 32). They
say nothing about any node.
