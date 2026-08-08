# CONTEXT_AUDIT — §2 hard gate

Every claim below is read from **the tree that is actually checked out**, not
from the pinned upstream tags and not from any prior report.

| what | value |
|---|---|
| repo HEAD at start | `d19538c94ab4bf193177e3f2ce23ce6104187258` |
| branch | `feature/b306-bringup` |
| SDK root | `/home/zekaixiao/ncs/v2.8.0` |
| `nrf/VERSION` | `2.8.0` |
| `zephyr/VERSION` | `3.7.99`, `EXTRAVERSION` **empty** (the manifest pins `v3.7.99-ncs1`; the checked-out `VERSION` file carries no extraversion — recorded, see §A1) |
| B306 firmware | `B306_Part/firmware`, `VERSION` = 0.1.44-imu-relay, marker `b306-imu-relay-v44` |
| reference build for `.config` facts | `B306_Part/builds/b306-imu-relay-v44-b/firmware/zephyr/.config` |

**The SDK is already patched.** `hci_core.c`, `conn.c` and `att.c` carry this
project's `BSF_BT_STAGE_*` marks (hci_core.c:80-88/4279/4302, conn.c:62-70/345…365,
att.c:761-763). The audit therefore describes a patched tree, which is correct:
it is the tree the boards were built from and the tree v45 extends.

Verdicts: **PASS** = claim confirmed as stated. **CORRECTED** = the underlying
fact is now proven but differs from what the brief or an earlier report
expected; the difference is a design input. **INSUFFICIENT** = not resolvable
offline.

---

## Item 1 — the controller→host receive worker — **PASS**

| fact | proof |
|---|---|
| `receive_signal_raise()` = `mpsl_work_submit(&receive_work)` | `nrf/subsys/bluetooth/controller/hci_driver.c:326-329` |
| handler is `receive_work_handler()` → `hci_driver_receive_process()` | hci_driver.c:635-640 |
| work item registered | `k_work_init(&receive_work, receive_work_handler)`, hci_driver.c:1239 |
| SDC callback is the same function | `sdc_enable(receive_signal_raise, sdc_mempool)`, hci_driver.c:1291 |
| `mpsl_work_submit()` targets `mpsl_work_q` | `nrf/include/mpsl/mpsl_work.h:45` |
| the thread is named **"MPSL Work"** | `nrf/subsys/mpsl/init/mpsl_init.c:464` `k_work_queue_start(&mpsl_work_q, …)`, `:467` `k_thread_name_set(&mpsl_work_q.thread, "MPSL Work")` |
| stack | `CONFIG_MPSL_WORK_STACK_SIZE=1024` (v44 `.config`), `mpsl_init.c:153` |
| priority | `CONFIG_MPSL_THREAD_COOP_PRIO=6`, i.e. **cooperative** |

`hci_driver_receive_process()` processes **exactly one** HCI message per
invocation and re-submits itself (hci_driver.c:623-632) — "Let other threads of
same priority run in between." So the inlet is a self-re-arming one-shot, and a
`MPSL_WORK_ENTER` without a matching `EXIT` is an unambiguous stall of the inlet.

`BSF_CH_MPSL_RX` binds to `mpsl_work_q.thread`. **The name `sdc_rx` appears
nowhere in this design.**

## Item 2 — K_FOREVER allocations on the receive path — **PASS**

| site | call | line |
|---|---|---|
| ACL inbound | `bt_buf_get_rx(BT_BUF_ACL_IN, K_FOREVER)` | hci_driver.c:434 |
| ISO inbound | `bt_buf_get_rx(BT_BUF_ISO_IN, K_FOREVER)` | hci_driver.c:471 (not reachable: no ISO configured) |
| events | `bt_buf_get_evt(hdr->evt, discardable, discardable ? K_NO_WAIT : K_FOREVER)` | hci_driver.c:572-573 |

Both non-discardable paths are unbounded, **on the cooperative MPSL Work
thread**. This is the structural fact the whole v45 design is built on: a stall
here stops every inbound HCI message, including Number Of Completed Packets,
without stopping the Link Layer.

## Item 3 — `sync_evt_pool`, and the pool-hash table — **PASS**

```
zephyr/lib/net_buf/… ; zephyr/subsys/bluetooth/host/buf.c:36
NET_BUF_POOL_FIXED_DEFINE(sync_evt_pool, 1, SYNC_EVT_SIZE, sizeof(struct bt_buf_data), NULL);
```

**Exactly one buffer.** Routed events, buf.c:90-97:

```
case BT_HCI_EVT_NUM_COMPLETED_PACKETS:   /* :92 */
case BT_HCI_EVT_CMD_STATUS:              /* :94 */
case BT_HCI_EVT_CMD_COMPLETE:            /* :95 */
        buf = net_buf_alloc(&sync_evt_pool, timeout);   /* :96 */
```

The upstream comment (buf.c:28-35) says the count "may be increased as an
optimization to allow the HCI transport to fill buffers in parallel with
`bt_recv` consuming them". **Recorded as a future mitigation lever. Not touched
— §13 freezes buffer counts and v45 observes rather than treats.**

Hash function: `pool_name_hash()` (`main.c:498-506`) is plain FNV-1a/32 over the
NUL-terminated name **excluding** the terminator. Every `NET_BUF_POOL*DEFINE`
name in `zephyr/`, `nrf/`, `modules/` and `bootloader/` was extracted (**88
names**) and hashed: **88 distinct hashes, 0 collisions**. All eight hashes ever
emitted by a node resolve to exactly one preimage:

| hash | pool | count | defined at |
|---|---|--:|---|
| `27b70977` | **`sync_evt_pool`** | **1** | buf.c:36 |
| `20588eb5` | `hci_rx_pool` | 10 = `MAX(EVT 10, ACL 6)` | buf.c:51, `buf.h:93` |
| `a14875f8` | `discardable_pool` | 3 | buf.c:38 |
| `858969d7` | `att_pool` | 8 | att.c:299 |
| `11597b73` | `acl_tx_pool` | 8 | conn.c:121 |
| `2de570ea` | `fragments` | 1 | conn.c:147 |
| `39b3fc03` | `hci_cmd_pool` | 2 | hci_core.c:187 |
| `ef427c73` | **`pkt_pool`** | 4 | `zephyr/subsys/mgmt/mcumgr/transport/src/smp.c:42` |

`0x27b70977 == FNV1a("sync_evt_pool")` — **the forensics' §1.5 open item is
closed by construction, not by assumption.** `0xef427c73` is resolved too: it is
the MCUmgr SMP transport pool, i.e. the OTA path. The brief's pre-verified note
("matches NO Zephyr host BT pool") is correct and not in conflict — `pkt_pool`
is not a BT pool.

## Item 4 — priority events are inline, and the NCP chain — **PASS**

```
bt_hci_recv()                 hci_core.c:4156   k_sched_lock()  ← scheduler locked
  bt_recv_unsafe()            hci_core.c:4116
    BT_BUF_ACL_IN         -> rx_queue_put()                :4125
    BT_BUF_EVT + RECV_PRIO-> hci_event_prio()   INLINE     :4133-4134
    BT_BUF_EVT + RECV     -> rx_queue_put()                :4137-4138
```

`hci_event_prio()` (`:4075`) dispatches from the `prio_events[]` table, which
contains `hci_num_completed_packets` at **`:4069-4071`**. So NCP executes
**inline on MPSL Work, with the scheduler locked**.

`hci_num_completed_packets()` — `hci_core.c:578`:

```
for each handle:                                   :594
    conn = bt_conn_lookup_handle(handle, …)        :603
    while (count--):                               :609
        k_sem_give(bt_conn_get_pkts(conn))         :612
        node = sys_slist_get(&conn->tx_pending)    :617
        sys_slist_append(&conn->tx_complete, node) :627
        atomic_dec(&conn->in_ll)                   :631
        bt_conn_tx_notify(conn, false)             :634
```

**`ncp_packet_total` is incremented at the top of that `while` body** — one per
completed packet on our handle. That is the completion watermark of §5, and it
needs no application-path change.

Two consequences recorded now because they constrain the markers:
- the scheduler is locked here, so a marker may not sleep or yield (law 7 already
  forbids it), and
- `bt_conn_tx_notify(conn, false)` is called from **three** contexts (item 8).

## Item 5 — where completions actually run — **PASS**

`tx_notify_workqueue_get()`, conn.c:285-292:

```
#if defined(CONFIG_BT_CONN_TX_NOTIFY_WQ)
        return &conn_tx_workq;
#else
        return &k_sys_work_q;      ← our build: CONFIG_BT_CONN_TX_NOTIFY_WQ=n
#endif
```

`bt_conn_tx_notify()` (conn.c:342):
- caller **is** the tx-notify wq thread → `tx_notify_process(conn)` inline (`:347-349`);
- otherwise → `k_work_submit_to_queue(tx_notify_workqueue_get(), &conn->tx_complete_work)` (`:355`), then optionally `k_work_flush()` (`:361`) when `wait_for_completion`.

`tx_complete_work` → `tx_notify_process()` (conn.c:1704-1708 / 294). It asserts
single-context (`:297`), pops `conn->tx_complete` under `irq_lock` (`:305-312`),
`tx_free(tx)` then `cb(conn, user_data, 0)` (`:325-334`) — **the callback is
where the ATT buffer is unref'd** — then `bt_tx_irq_raise()` (`:337`).

Mutation sites of the two lists, with contexts:

| list | site | context |
|---|---|---|
| `tx_pending` **append** | conn.c:792 (`send_buf`) | system WQ (`tx_processor`) |
| `tx_pending` **remove** (error unwind) | conn.c:809 | system WQ |
| `tx_pending` → `tx_complete` **move** | conn.c:617/627 in `hci_num_completed_packets` | **MPSL Work** |
| `tx_pending` **drain** on disconnect | conn.c:1179 (`process_unack_tx`) | BT RX WQ |
| `tx_complete` **pop** | conn.c:309 under `irq_lock` | system WQ |

Locking: only the `tx_complete` pop takes `irq_lock`. `tx_pending` is mutated
from three contexts with **no lock** — the list is protected by the
non-preemptible/`k_sched_lock` discipline, not by a mutex. **This is exactly why
§1.5 forbids walking these lists at corpse time and mandates shadow atomics.**

## Item 6 — Disconnect Complete, and where advertising restarts — **PASS**

`BT_HCI_EVT_DISCONN_COMPLETE` is registered **twice**:
- priority half `hci_disconn_complete_prio` — `hci_core.c:4065`, in `prio_events[]` → **MPSL Work, inline**;
- normal half `hci_disconn_complete` — `hci_core.c:2942`, in `normal_events[]` → **BT RX WQ**, definition at `:994`.

The normal half calls `bt_conn_set_state(conn, BT_CONN_DISCONNECTED)` (`:1013`),
which reaches `process_unack_tx()` (conn.c:1170) and the application
`disconnected()` callback.

Application side, `B306_Part/firmware/src/main.c`: `disconnected()` ends with
`(void)start_advertising();` — **called directly, still on the BT RX WQ**.
`start_advertising()` is defined at main.c:3423 and reaches
`bt_le_adv_start()` → `bt_hci_cmd_send_sync()` → `bt_hci_cmd_create()` →
`net_buf_alloc(&hci_cmd_pool, K_FOREVER)` (hci_core.c:334, pool size **2**).

So "disconnected but never re-advertised" localises to the BT RX WQ, and the
`DISCONN_NORMAL_ENTER/EXIT` + `pended_on` pair in `BSF_CH_BT_RX` is what
separates "never got there" from "parked inside it".

## Item 7 — does `bt_gatt_notify()` block? — **CORRECTED (one wait, not two)**

Grep for `K_FOREVER` across the entire notify path in the checked-out tree:

| file | `K_FOREVER` occurrences |
|---|---|
| `gatt.c` | **none** |
| `l2cap.c` | **none** |
| `att.c` | `:748` (the timeout selection) and `:761` (the stage marker's own test) |
| `conn.c` | `:1649` — a **comment** only |

`att.c:743-749`:

```
switch (att_op_get_type(op)) {
case ATT_RESPONSE:
case ATT_CONFIRMATION:
        timeout = BT_ATT_TIMEOUT;      /* 30 s */
        break;
default:
        timeout = K_FOREVER;           /* ← includes ATT_NOTIFICATION */
}
…
buf = bt_l2cap_create_pdu_timeout(&att_pool, 0, timeout);        att.c:765
```

> **So `bt_gatt_notify()` genuinely blocks, and there is exactly ONE unbounded
> wait on the path, not two.**

**This corrects `FINAL_BT_WEDGE_FORENSICS.md` §4 rank 1**, which names a second
unbounded wait — "the `bt_conn_tx` allocation from the 8-deep `free_tx` FIFO".
In NCS v2.8.0 that allocation is **`K_NO_WAIT`**:

```
conn.c:548  static struct bt_conn_tx *conn_tx_alloc(void)
conn.c:550          struct bt_conn_tx *ret = k_fifo_get(&free_tx, K_NO_WAIT);
```

and exhaustion is handled as *backpressure*, not as a wait: `dont_have_tx_context()`
(conn.c:543-546) makes `tx_processor` self-suspend, and the only caller
(conn.c:726) treats a NULL result as an assertion-guarded `-ENOMEM` on a path
that pre-acquires the resource. **The correction does not change the rank-1
conclusion** — one unbounded wait released only by `tx_notify_process()` is
sufficient for the whole mechanism — but it does change the wait-object table
of §4, which must not advertise a `free_tx` wait that cannot happen.

The wait object itself: `net_buf_alloc_len()` blocks in
`k_lifo_get(&pool->free, timeout)` (`zephyr/lib/net_buf/buf.c:304/307`).
A thread parked there reports `pended_on == &pool->free._queue.wait_q`.
That is the address the §4 table resolves, for each of the six pools.

**Detector consequence:** the `notify_exit_total` arm is well founded — a
blocked notify worker never increments it, and nothing else on that thread can.

## Item 8 — every existing BSF stage write site, and the multi-writer violations — **PASS (violations confirmed, as expected)**

The v44 channel is a **single global** `{bsf_bt_stage_id, seq, cycles, arg,
trace[]}` (`src/bsf_bt_stage.h:137-154`, storage in `main.c:730-737`).

| write site | function | executing thread(s) |
|---|---|---|
| hci_core.c:4279 / 4302 | `rx_work_handler()` | BT RX WQ |
| conn.c:345, 365 | `bt_conn_tx_notify()` enter/exit | **BT RX WQ *and* MPSL Work *and* system WQ** |
| conn.c:348 | `TX_NOTIFY_DIRECT` | system WQ |
| conn.c:354, 356 | before/after submit | BT RX WQ, MPSL Work |
| conn.c:360, 362 | before/after flush | BT RX WQ |
| conn.c:517, 524, 534, 536, 540 | `bt_conn_recv()` | BT RX WQ |
| conn.c:1309-1320 | `bt_conn_set_state(DISCONNECTED)` | BT RX WQ |
| att.c:761, 766 | `bt_att_chan_create_pdu()` | **notify worker** (notifications) *and* **BT RX WQ** (responses) |

> **Confirmed multi-writer violations: at least three distinct threads write the
> one v44 channel, and two of them (`bt_conn_tx_notify` via NCP on MPSL Work,
> and `bt_att_chan_create_pdu` on the notify worker) are precisely the paths v45
> needs to read.** The v44 global stage is therefore retired as an authority.
> Its enums, storage and decoder support are **kept and never renumbered**.

## Item 9 — the 50 ms trajectory ring sampler's context — **CORRECTED (stronger than required)**

`stall_ring_sample()` is a **`k_timer` expiry function** —
`K_TIMER_DEFINE(stall_ring_timer, stall_ring_sample, NULL)`, main.c:710 —
so it runs in the **system-clock ISR**, not on the system workqueue.

The requirement was "must be syswq-alive class". An ISR is a **strictly
stronger** class: it cannot be blocked by any thread, including the system
workqueue. The existing comment (main.c:598-614) already states and justifies
this. Its one reboot path (`sys_reboot()` at main.c:706) is ISR-legal.

**Kept as is. v45 raises capacity 200 → 512 and changes nothing else about it.**

## Item 10 — flash writes and MPSL — **CORRECTED: the brief's premise is false, the conclusion survives**

```
zephyr/drivers/flash/soc_flash_nrf.c:213, :262
        if (nrf_flash_sync_is_required()) { … nrf_flash_sync_exe(…) }

nrf/drivers/mpsl/flash_sync/flash_sync_mpsl.c:164-167
bool nrf_flash_sync_is_required(void)
{
        return mpsl_is_initialized() && !is_in_fault_isr();
}
```

and

```
nrf/subsys/mpsl/init/mpsl_init.c:537
SYS_INIT(mpsl_lib_init_sys, PRE_KERNEL_1, CONFIG_KERNEL_INIT_PRIORITY_DEFAULT);
```

> **MPSL is initialised at `PRE_KERNEL_1`, i.e. before `main()` runs and long
> before `bt_enable()`. Therefore `nrf_flash_sync_is_required()` is TRUE at
> every point the application can execute, and the brief's §9 justification —
> "flash writes before `bt_enable()` do not [need MPSL sync], radio off" — is
> WRONG.**

Config confirms the path is compiled in:
`CONFIG_SOC_FLASH_NRF_RADIO_SYNC_MPSL=y`,
`CONFIG_SOC_FLASH_NRF_RADIO_SYNC_MPSL_TIMESLOT_SESSION_COUNT=1`
(the app itself requests `CONFIG_MPSL_TIMESLOT_SESSION_COUNT=0`; the flash
driver's session is the one that exists).

**The §9 conclusion is nevertheless sound, on a different and better argument:**

1. The hazard the brief actually identified is real and unchanged — at *capture*
   time the wedged thread may **be** `mpsl_work_q.thread`, and a timeslot
   request serviced from that path could never complete. **v45 therefore never
   writes flash at capture time.** Capture writes `.noinit` only.
2. After `sys_reboot(SYS_REBOOT_COLD)` the wedged thread does not exist. The
   early-boot persist runs on the **`main` thread** — a thread context, where
   blocking is legal.
3. At that point `bt_enable()` has not been called, so **no BLE role is
   scheduled** and nothing competes for the radio. The `EARLIEST` timeslot
   request (flash_sync_mpsl.c:186-190, `MPSL_TIMESLOT_PRIORITY_NORMAL`,
   `timeout_us = 30 000`) is granted essentially immediately.
4. It is **bounded, and it fails safe**:
   `k_sem_take(&_context.timeout_sem, K_MSEC(FLASH_TIMEOUT_MS))`
   (flash_sync_mpsl.c:205) → on timeout the session is closed (`:213`) and an
   error is returned. `FLASH_TIMEOUT_MS = FLASH_PAGE_ERASE_MAX_TIME_US *
   FLASH_PAGE_MAX_CNT / 1000 * CONFIG_SOC_FLASH_NRF_TIMEOUT_MULTIPLIER(10) / 10`
   (`soc_flash_nrf.h:35-37`). **A failed persist degrades to "corpse stayed in
   `.noinit`". It can never hang the boot.**

This replaces the brief's argument in `V45_DESIGN.md`; the safety property it
was trying to establish is established.

## Item 11 — partition map — **PASS as an audit; NEGATIVE as a result**

`CONFIG_FLASH_SIZE=1024` (KiB), erase-block **4096 B**
(`zephyr/dts/arm/nordic/nrf52840.dtsi:416-419`). No `partitions` node exists in
the board DTS — Partition Manager governs, via `B306_Part/firmware/pm_static.yml`,
which the file itself declares "freezes the first-flash ABI".

| partition | start | end | size |
|---|---|---|--:|
| `mcuboot` | `0x00000` | `0x0c000` | 48 KiB |
| `mcuboot_pad` | `0x0c000` | `0x0c200` | 512 B |
| `app` / `mcuboot_primary_app` | `0x0c200` | `0x86000` | `0x79e00` |
| `mcuboot_primary` (= pad + app) | `0x0c000` | `0x86000` | `0x7a000` |
| `mcuboot_secondary` | `0x86000` | `0x100000` | `0x7a000` |

`0xc000 + 0x7a000 = 0x86000`; `0x86000 + 0x7a000 = 0x100000`.

> **There are ZERO free bytes of flash. The map tiles the whole 1 MiB exactly.**
> `CONFIG_NVS is not set`, `CONFIG_SETTINGS is not set` — so nothing else is
> hiding there either; there is simply nothing left.

Ruled out, each for a stated reason:

| candidate | why not |
|---|---|
| tail of `mcuboot_secondary` | MCUboot's swap trailer lives at the end of that slot. A corpse written there can corrupt a **staged OTA image**, i.e. brick risk across a 10-node fleet. Fails "overlaps nothing" outright. |
| tail of `app` inside `mcuboot_primary` | erased by the next OTA, and inside the slot MCUboot swaps. |
| shrink `mcuboot` (48 KiB) | changes the bootloader's own placement. |
| UICR | one-shot writes, erasable only by full chip erase. |

The **only** clean carve is to take it off the end and shrink both slots
equally, which keeps `boot_slots_compatible()` trivially true
(`bootloader/mcuboot/boot/bootutil/src/swap_move.c:284-291`, mode
`CONFIG_MCUBOOT_BOOTLOADER_MODE_SWAP_WITHOUT_SCRATCH=y` → swap-using-move, which
accepts `num_sectors_pri == num_sectors_sec`):

| partition | start | end | size |
|---|---|---|--:|
| `mcuboot` | `0x00000` | `0x0c000` | unchanged |
| `mcuboot_primary` | `0x0c000` | `0x84000` | `0x78000` (−8 KiB) |
| `mcuboot_secondary` | `0x84000` | `0xfc000` | `0x78000` (−8 KiB) |
| **`bsf_corpse`** | **`0xfc000`** | **`0x100000`** | **16 KiB = 2 slots × 8 KiB = 4 sectors** |

Headroom check: the v44 signed image is **223 920 B**; the shrunken slot holds
`0x77e00` = 491 008 B — **45.6 % used**, so the 8 KiB is free of charge.

> **DEPLOYMENT BLOCKER, stated plainly.** MCUboot compiles its own flash map in.
> Changing `mcuboot_secondary` therefore requires rebuilding **and
> SWD-reflashing MCUboot on every board**. It cannot ship over OTA, and Stage C
> as specified is OTA-only.
>
> v45 therefore ships the complete flash-persistence implementation
> **`BSF_CORPSE_FLASH_ENABLED=0` by default**, with the carve supplied as a
> separate, non-default `pm_static_v45_corpse.yml` and an overlap checker in the
> test suite. Enabling it is a deliberate SWD campaign, not a side effect.
> The consequence of shipping it off is bounded and is stated in
> `V45_DESIGN.md` §9 and `HARDWARE_STAGE_PLAN.md`.

---

## A1 — recorded uncertainties

1. **`zephyr/VERSION` has an empty `EXTRAVERSION`** where the pinned manifest
   says `v3.7.99-ncs1`. Every file:line above was read from this tree, so the
   audit is self-consistent regardless; but the tree cannot be *proven* to be
   exactly `sdk-zephyr v3.7.99-ncs1` from the `VERSION` file alone. It is
   additionally patched by this project. Treated as: **the tree is authoritative,
   the tag name is not.**
2. `hci_rx_pool` per-buffer *holder* attribution (item §6 of the brief) has no
   existing hook; v45 adds one. Whether any holder can exist at all in this
   configuration was answered NO by the forensics (§9.4) and v45 instruments it
   anyway at capture time only, which costs nothing at runtime.
