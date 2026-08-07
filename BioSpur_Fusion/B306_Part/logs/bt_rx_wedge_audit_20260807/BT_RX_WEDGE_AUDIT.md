# BT_RX_WEDGE_AUDIT — K1 (rev. 2)

**Batch:** `bt_rx_wedge_audit_20260807` · **Date:** 2026-08-07
**Mode:** offline source, config and binary reading only. No hardware, no commands, no J-Link,
no deployment, **nothing changed** — not a Kconfig value, not a callback, not a test, not a build.
BSF44AD and BSFC2CC were not touched.

Everything below is read from the **canonical deployed build**
`B306_Part/builds/b306-imu-relay-v41-a/` (`.config` sha256 `886158bc…`, `zephyr.elf`), the exact
Zephyr/NCS tree it was compiled against (`/home/zekaixiao/ncs/v2.8.0`, `ZEPHYR_BASE` read from
`CMakeCache.txt`), and the working tree — with the working tree used only where §3.0 shows it is
byte-identical to v41.

---

## 0. Headline

Three results, ordered by how much they move the diagnosis.

**1. DRGN-23518 is refuted three independent ways.** The value has never been reported in this
project; it is `CONFIG_BT_BUF_ACL_RX_COUNT=6` against `CONFIG_BT_MAX_CONN=1`. The condition
`RX_COUNT <= MAX_CONN` does not hold, NCS 2.8.0 is **not** in the issue's own affected-version list,
and the condition is **unbuildable** — Zephyr carries a `BUILD_ASSERT` for it. The one config value
that could have explained the entire failure does not.

**2. A correction to J1 and to `B5_STALL_01_BSF44AD.md`: `disconnected()` does not run on the BT RX
thread.** It runs on the **system workqueue**. This is proven in the deployed v41 binary, not
inferred: `bt_conn_set_state()` dispatches `notify_connected` directly but has no disconnect
callback dispatch at all; the disconnect callback is reached only through `deferred_work`, which is
a `k_work_delayable` on `k_sys_work_q`. `connected()` is on BT RX WQ; `disconnected()` is not.

**3. That correction strengthens J1's verdict and narrows it by one step.** The system workqueue is
*provably alive* throughout the wedge, because it is where the watchdog is fed. So the failure to
re-advertise is no longer merely consistent with "BT RX wedged" — it **proves** the BT RX WQ never
completed the disconnect state transition. It never even reached the `k_work_reschedule()` that
would have queued the callback onto a healthy queue.

A fourth result, from K6: **the BT RX WQ stack has been measured on every board every 60 seconds
since before this campaign began.** `CONFIG_THREAD_ANALYZER_AUTO=y`, `AUTO_INTERVAL=60`. The
measurement is being written to an RTT buffer that nothing drains.

---

## 1. K1 — the config audit

Read from `b306-imu-relay-v41-a/firmware/zephyr/.config`, not `prj.conf`. Raw extract with the
`.config` hash is in `K1_CONFIG.txt`.

| Symbol | Value | Set by |
|---|---|---|
| `CONFIG_BT_MAX_CONN` | **1** | `prj.conf:34` |
| `CONFIG_BT_BUF_ACL_RX_COUNT` | **6** | Zephyr default (`common/Kconfig:80`) — app does not set it |
| `CONFIG_BT_BUF_ACL_TX_COUNT` | 8 | `prj.conf:50` |
| `CONFIG_BT_HCI_ACL_FLOW_CONTROL` | **not set** | default |
| `CONFIG_BT_CONN_TX_NOTIFY_WQ` | **not set** | default |
| `CONFIG_BT_ATT_TX_COUNT` | 8 | `prj.conf:48` |
| `CONFIG_BT_CONN_TX_MAX` | 8 | derived |
| `CONFIG_BT_L2CAP_TX_BUF_COUNT` | 8 | `prj.conf:49` |
| `CONFIG_BT_RX_STACK_SIZE` | **1024** | NCS default (`nrf/subsys/bluetooth/controller/Kconfig:89`) |
| `CONFIG_BT_ATT_SENT_CB_AFTER_TX` | **not set** | default |
| `CONFIG_BT_CONN_FRAG_COUNT` | **1** | default — unchanged, per trap 8.8 |

Context symbols that turned out to matter more than several of the above:

| Symbol | Value | Why it matters |
|---|---|---|
| `CONFIG_BT_RECV_WORKQ_BT` | **y** | the "BT RX thread" is a workqueue thread named `"BT RX WQ"` |
| `CONFIG_BT_RX_PRIO` | 8 | `K_PRIO_COOP(8)` — cooperative |
| `CONFIG_BT_BUF_EVT_RX_COUNT` | 10 | sets `BT_BUF_RX_COUNT`, the assert actually active here |
| `CONFIG_ASSERT` | **not set** | `__ASSERT` in `bt_conn_tx_notify()` is compiled out |
| `CONFIG_HW_STACK_PROTECTION` / `MPU_STACK_GUARD` | y / y | stack overflow is trapped, not silent |
| `CONFIG_RESET_ON_FATAL_ERROR` | **not set** | see §3.3 — this makes faults *louder*, not quieter |
| `CONFIG_BT_SMP` / `BT_SETTINGS` / `BT_EATT` | not set | no bonding, no EATT — trims the RX surface a lot |
| `CONFIG_SOC_FLASH_NRF_RADIO_SYNC_MPSL` | y | flash writes take an MPSL timeslot |
| `CONFIG_THREAD_ANALYZER_AUTO` | y, 60 s | §6 |

### 1.1 `BT_BUF_ACL_RX_COUNT <= BT_MAX_CONN` — **the condition does not hold**

`6 <= 1` is false. Stated plainly, as asked: **DRGN-23518 does not apply to this firmware.**

It is worth recording *why* this is not a close call, because a bare "6 > 1" invites the question of
whether some other build could have shipped differently:

- **The issue does not list 2.8.0 as affected.** The known-issues file shipped *inside* this SDK
  (`nrf/doc/nrf/releases_and_maturity/known_issues.rst:319`) tags DRGN-23518 with
  `.. rst-class:: v2-7-0 v2-6-2 … v1-7-1`. `v2-8-0` is absent from that list, while the two issues
  immediately following it are tagged `.. rst-class:: v2-8-0`. The brief's premise that "NCS 2.8.0
  is listed as affected" is the one thing in it that does not survive contact with the tree.
- **The condition is unbuildable.** `zephyr/subsys/bluetooth/common/dummy.c:75-82` carries a
  `BUILD_ASSERT` on exactly this relation. With flow control off — our case — the active form is
  `BUILD_ASSERT(BT_BUF_RX_COUNT > CONFIG_BT_MAX_CONN)`, and `BT_BUF_RX_COUNT` resolves through
  `buf.h:93` to `MAX(EVT_RX_COUNT=10, ACL_RX_COUNT=6, 0) = 10`. Margin is 10×, and a firmware that
  violated it would not compile. v41 exists and runs; the condition is refuted by construction.

So a config change would not have fixed this, and no config change is proposed. That is a clean
negative result and it cost one grep — the brief was right to put it first.

### 1.2 DRGN-23511 — applies to 2.8.0, does not bite

"Building of multilink Bluetooth applications fails when `CONFIG_BT_BUF_ACL_RX_COUNT` is not
explicitly set." We *do* leave it unset. But `BT_MAX_CONN=1` is not multilink, and it is a **build**
failure, not a runtime one — v41 built. **Not applicable.** Noted only so a future multi-link
variant does not rediscover it.

### 1.3 NCSDK-30959 — not documented in this SDK; mechanism assessed anyway

**The identifier does not appear anywhere in `known_issues.rst` for v2.8.0.** I cannot confirm its
text, affected versions or workaround from the tree, and I will not restate the brief's summary as
though I had verified it. Recorded as **`INSUFFICIENT` on the citation.**

The *mechanism* as the brief describes it — a deadlock when `BT_HCI_ACL_FLOW_CONTROL` is off **and**
the application blocks a host callback — is assessable regardless, and it splits cleanly:

- **First precondition: met.** `CONFIG_BT_HCI_ACL_FLOW_CONTROL` is not set.
- **Second precondition: not met.** §3.4 enumerates every application callback reachable from BT RX
  WQ — `connected`, `le_param_updated`, both CCC handlers, `control_write`, `stall_status_read` — and
  **none of them blocks**. None takes a mutex, allocates, sends, or waits. `control_write` in
  particular hands off with `K_NO_WAIT`.

So the conjunction does not hold, whatever the issue text says. This one is closed on the
application's own properties rather than on a version tag, which is the stronger form of the answer.

### 1.4 NCSDK-29354 — applies to 2.8.0, applies to nRF52, and K2 promotes it

Verbatim from `known_issues.rst:334`:

> **NCSDK-29354: Bluetooth traffic stalls while writing or erasing flash**
> Using system workqueue for internal flash operations while Bluetooth is in use could result in
> Bluetooth hang or flash operation failures (timeout in MPSL flash synchronization). This happens
> because **Bluetooth RX context waits for the connection TX notify that is done in the system
> workqueue context.**
> **Affected platforms:** nRF52 Series, nRF54L15
> **Workaround:** `CONFIG_BT_CONN_TX_NOTIFY_WQ`

Every precondition is met. Board is `biospur_fusion_nrf52840`. `CONFIG_BT_CONN_TX_NOTIFY_WQ` is
**not set**, so the workaround is not applied. And the coupling it describes is real and verified in
the linked source, not just asserted by the release note:

- `conn.c:262-269` — `tx_notify_workqueue_get()` returns `&k_sys_work_q` when `BT_CONN_TX_NOTIFY_WQ`
  is unset.
- `conn.c:319-337` — `bt_conn_tx_notify(conn, wait_for_completion)` submits `conn->tx_complete_work`
  to that queue and, when `wait_for_completion` is true, calls
  **`k_work_flush(&conn->tx_complete_work, &sync)` — an unbounded wait.**
- `conn.c:487` — `bt_conn_recv()` calls `bt_conn_tx_notify(conn, true)` as its **first statement**,
  before any parsing, for **every incoming ACL packet**.
- `conn.c:1267` — `bt_conn_set_state(BT_CONN_DISCONNECTED)` calls it again, `true`, on the
  disconnect path.

So the BT RX WQ does wait on the system workqueue, twice on the path that matters. §3 works out what
that does and does not explain.

---

## 2. K2 — can the B306 write internal flash after boot?

**Yes. Exactly one path, and it is on the system workqueue.**

Searched `firmware/src/*.c` for `nvs_*`, `flash_*`, `settings_*`, `flash_area_*`, MCUboot confirm and
image-trailer writes. The complete result:

| Site | Call | Flash op | Thread | Can run after boot? |
|---|---|---|---|---|
| `main.c:1832` | `boot_write_img_confirmed()` | **write** (image trailer) | **system workqueue** (`boot_confirm_commit_work_handler`) | **yes** |
| `main.c:1806` | `boot_is_img_confirmed()` | read | system workqueue | yes |
| `main.c:2130`, `2150` | `boot_is_img_confirmed()` | read | control thread | yes |
| `main.c:2944` | `boot_is_img_confirmed()` | read | `main` | boot only |

No NVS. No settings subsystem (`CONFIG_BT_SETTINGS` absent, confirming the earlier partition audit —
the persisted schedule is on the tag's nRF52832, not the B306). No MCUmgr/SMP write path in the
application image. Reads are memory-mapped on nRF52 and need no MPSL timeslot, so only the write
matters.

**NCSDK-29354 is therefore PROMOTED, not demoted.** The earlier partition audit demoted it on the
grounds that nothing writes flash after boot; that was wrong, and this is the correction.
`boot_write_img_confirmed()` runs on the system workqueue, under `CONFIG_SOC_FLASH_NRF_RADIO_SYNC_MPSL=y`,
**while holding `boot_confirm_lock`** (`main.c:1821→1836`). That is the known issue's precondition
reproduced exactly.

Two qualifications, both of which cut against it explaining BSF44AD:

- **It runs at most once per boot**, on the two-command BLE confirmation round trip after an OTA,
  and only while `boot_confirm_policy.required` — not in steady state. BSF44AD's onset was **1.51 h**
  into the run, long after confirmation.
- **Its blocking is bounded** by the flash write plus MPSL timeslot arbitration, not unbounded.

So: a real, live instance of a known deadlock precondition, in a window that does not cover this
episode. It should be closed anyway — see §7 — but it is not the answer here.

**Separately, and independently of the known issue:** `CONFIG_BT_CONN_TX_NOTIFY_WQ=y` is worth
having on general grounds. `bt_conn_recv()` flushes through the system workqueue for **every ACL
packet**, so *any* slow work item there adds latency to the entire BLE RX path. The watchdog
argument only proves work items complete within 30 s; it says nothing about them being fast.

---

## 3. K3 — rescoped: what could BT RX be stuck inside?

`disconnected()` is not re-audited. J1 settled it.

### 3.0 Provenance — which source is safe to read for v41

The working tree is v42. Comparing symbol sizes between `b306-imu-relay-v41-a` and
`b306-imu-relay-v42-a` for every function read below:

| Function | v41 | v42 | |
|---|---:|---:|---|
| `control_write` | 140 | 140 | identical |
| `telemetry_work_handler` | 1660 | 1660 | identical |
| `boot_confirm_commit_work_handler` | 188 | 188 | identical |
| `boot_confirm_timeout_work_handler` | 56 | 56 | identical |
| `connected` | 28 | 28 | identical |
| `make_stall_status` | 308 | 308 | identical |
| `stall_status_read` | 504 | 512 | **+8** — H1's ring change |
| `disconnected` | 428 | 432 | **+4** — H1's ring change |

Both differences are H1's, already characterised in `H1_REPORT.md` and `DISCONNECTED_ORDERING.md`.
Everything else is byte-identical, so the v42 text is a sound reading of v41 for those functions.

### 3.1 The correction — which callback runs on which thread

The Kconfig help for `BT_RX_STACK_SIZE` says the RX thread "is the context from which **all** event
callbacks to the application occur." For this Zephyr version that is **not true of the disconnect
callback**, and the difference is load-bearing here.

Verified in the deployed v41 ELF, disassembled at the correct Thumb boundaries:

- **`bt_conn_set_state`** (`0x28d18`, 0x1b8 bytes) contains **no indirect call at all** — no `blx` —
  so no disconnect callback dispatch. It calls `notify_connected` directly at `0x28e34`. And the
  disconnect path appears verbatim as three consecutive calls:

  | addr | call | `conn.c` |
  |---|---|---|
  | `0x28e14` | `bt_conn_tx_notify` | 1267 — `bt_conn_tx_notify(conn, true)` |
  | `0x28e1a` | `bt_conn_reset_rx_state` | 1269 |
  | `0x28e26` | `k_work_reschedule` | 1272 — `k_work_reschedule(&conn->deferred_work, K_NO_WAIT)` |

- **`deferred_work`** (`0x29628`, 0x11c bytes) calls `bt_l2cap_disconnected` at `0x29638` and then
  **two indirect `blx r3`** at `0x29650` and `0x29674`. That is `notify_disconnected()` inlined:
  `conn.c:1934-1949` is exactly two loops — `SYS_SLIST_FOR_EACH_CONTAINER(&conn_cbs, …)` over the
  dynamic list, then `STRUCT_SECTION_FOREACH(bt_conn_cb, cb)` over the static array, which is where
  the application's `BT_CONN_CB_DEFINE(connection_callbacks)` lives. That is where `disconnected()`
  is reached.
- `deferred_work` is `conn->deferred_work`, a `k_work_delayable` (`conn.c:361`) submitted by
  `k_work_reschedule(&conn->deferred_work, K_NO_WAIT)` (`conn.c:1272`). `k_work_reschedule()` targets
  **`k_sys_work_q`**.

| Application callback | Dispatch context |
|---|---|
| `connected` | **BT RX WQ** |
| `le_param_updated` | **BT RX WQ** |
| `data_ccc_changed` / `telemetry_ccc_changed` | **BT RX WQ** |
| `control_write` (GATT write) | **BT RX WQ** |
| `stall_status_read` (GATT read) | **BT RX WQ** |
| **`disconnected`** | **system workqueue** |

`B5_STALL_01_BSF44AD.md` says "`start_advertising()` is called from the `disconnected()` callback,
which runs on the BT RX thread." The first half is right (`main.c:2883`, last statement); the second
half is wrong. J1 inherited the same assumption. Correcting it does not weaken J1's conclusion — §3.2.

### 3.2 What the watchdog proves, and what it is blind to

The application defines **no workqueue of its own**. `K_THREAD_DEFINE` gives it five threads —
`imu`, `notify_worker`, `publisher`, `uart_parser`, `control` — and every `k_work` item goes to
`k_sys_work_q`.

`watchdog_feed_once()` has exactly **one** call site: `main.c:2621`, the first statement of
`telemetry_work_handler`, a `K_WORK_DELAYABLE` that reschedules itself at 1 Hz on the system
workqueue. The hardware WDT is `WATCHDOG_TIMEOUT_MS 30000` with `WDT_FLAG_RESET_SOC`.

BSF44AD kept PAIRED lit continuously and never re-advertised, so the application never restarted —
a reset would have relit advertising and the Master would have reconnected. Therefore:

> **The system workqueue ran continuously, at better than 30 s granularity, for the whole episode
> and the 20+ minutes after it.**

Three consequences, and they do most of the work in this report:

**(a) The watchdog cannot see a BT RX WQ wedge.** It is fed from a different thread. A permanently
wedged BT RX WQ produces exactly BSF44AD's signature — PAIRED lit forever, no notifications, no ATT
responses, no disconnect callback, no re-advertising — and **no reset**. There is no existing
mechanism on the board that would notice.

**(b) It sharpens J1 by one step.** `disconnected()` is dispatched from a queue that was demonstrably
healthy. So the callback's absence cannot be explained by anything downstream of dispatch, and cannot
be explained by the queue. The only remaining explanation is that
`bt_conn_set_state(conn, BT_CONN_DISCONNECTED)` — which runs on **BT RX WQ** — never reached its
`k_work_reschedule()`. The board's controller had honoured the LL terminate in 67 ms; the host never
processed the resulting Disconnection Complete. **BT RX WQ is wedged upstream of the disconnect
state transition itself**, not merely upstream of callback dispatch.

**(c) It kills a whole class of candidates.** Anything that would block the system workqueue —
`tx_processor` (which is `K_WORK_DEFINE(tx_work, tx_processor)` submitted with `k_work_submit()`,
`hci_core.c:4747-4752`, i.e. the *entire BLE TX engine* runs on the system workqueue), `tx_notify_process`
and its TX-sent callbacks, `boot_confirm_*_work`, `stall_recovery_work`, `relay_timeout_work`,
`deferred_work` — would have stopped the watchdog feed and reset the board within 30 s. None of them
is the mechanism.

### 3.3 Stack overflow is refuted, and `RESET_ON_FATAL_ERROR` is why

`CONFIG_HW_STACK_PROTECTION=y` and `CONFIG_MPU_STACK_GUARD=y`, so a BT RX WQ stack overflow traps.
`CONFIG_RESET_ON_FATAL_ERROR` is **not set**, which sounds like it would make the failure quiet. It
does the opposite:

- `k_sys_fatal_error_handler` is weak at `0x2ff29` in the v41 ELF and is **not overridden** by the
  application or by NCS.
- The weak default (`kernel/fatal.c:37-46`) is `LOG_PANIC(); LOG_ERR("Halting system");
  arch_system_halt(reason);` and `arch_system_halt` — also weak, also not overridden, `0x3a4e7` — is
  `arch_irq_lock()` followed by an infinite spin.
- An IRQ lock does not pause the watchdog. `WDT_OPT_PAUSE_HALTED_BY_DBG` pauses it only for a
  debugger halt.

So **any fatal error on any thread halts the SoC and the watchdog resets it within 30 s.** BSF44AD
did not reset. **No stack overflow, no MPU guard trip, no hard fault occurred.** The `k_thread_abort`
at the tail of `z_fatal_error()` is unreachable here, so there is no "thread quietly dies" path
either.

This bounds K6 usefully: the RX stack margin is still unmeasured, but an overflow would have been
loud. It is not this failure's mechanism.

### 3.4 The table — everything BT RX WQ executes, and whether it can block

Application callbacks on BT RX WQ:

| Callback | Blocking? | Worst-case stack frame | Notes |
|---|---|---|---|
| `connected` | **no** | ~30 B (`BT_ADDR_LE_STR_LEN`) | `bt_addr_le_to_str`, `atomic_set`, `LOG_INF` |
| `le_param_updated` | **no** | trivial | one `LOG_INF` |
| `data_ccc_changed`, `telemetry_ccc_changed` | **no** | trivial | `atomic_set` only |
| `control_write` | **no** | ~204 B (`struct control_request`) | validates, `memcpy`, `k_msgq_put(K_NO_WAIT)`, returns |
| `stall_status_read` | **no** | **232 B** (`bsf_stall_ring_page_t`) | pure policy call, two spinlocks, `bt_gatt_attr_read` memcpy |

The brief flagged the GATT write handler as "the callback most likely to do real work". It is in fact
the cleanest thing here: `control_write` does no work at all on the RX thread — it hands off to
`control_queue` with `K_NO_WAIT` and returns, and `process_control()` (`main.c:2106-2550`, the
445-line command handler that takes `boot_confirm_lock`, talks to the IMU and reboots the board) runs
on the **control thread**, `main.c:2575`. That is precisely the pattern Nordic's workaround asks for, already applied.

`stall_status_read` is the one worth watching — not because it blocks, but because 232 bytes is 23 %
of a 1024-byte stack in a single frame, under the ATT/L2CAP/HCI frames already on it.

**No application callback on BT RX WQ takes a mutex, allocates, sends, or waits.** No ABBA ordering
against the publisher or notify path is possible through application code, because the RX thread
never acquires an application lock. That is a clean negative and it is worth having.

Host-internal, where no application callback is involved:

| Path | Primitive | Bound | Assessment |
|---|---|---|---|
| `bt_conn_recv()` head, every ACL packet — `conn.c:487` | `k_work_flush(&conn->tx_complete_work)` on `k_sys_work_q` | **unbounded** | the NCSDK-29354 coupling. Needs the sysworkq to be wedged, which §3.2(c) rules out. With `CONFIG_ASSERT` off a failed submit is silent, but `k_work_flush` on a non-queued item returns immediately rather than hanging. **Demoted.** |
| `bt_conn_set_state(DISCONNECTED)` — `conn.c:1267` | same | unbounded | same argument. This is the call immediately before the `k_work_reschedule` that never happened, so it remains the single most interesting instruction on the board. |
| ATT response/confirmation alloc — `att.c:705-729` | `bt_l2cap_create_pdu_timeout(&att_pool, 0, BT_ATT_TIMEOUT)` | **30 s per attempt** | matches the observed read behaviour exactly. Self-terminates, so it needs to repeat to explain 20 minutes. **Leading candidate.** |
| ATT notification/command/request/indication alloc — `att.c:725` | same, **`K_FOREVER`** | **unbounded** | this is the permanent one. As a peripheral with no SMP, no bonding and no EATT there is no obvious way to reach it *from the RX thread* — but it is the only unbounded ATT wait, and it is what the notify path (an application thread) parks on. |
| ACL reassembly | `conn->rx` handling | n/a | DRGN-23518's territory; refuted in §1.1 |
| Fragment alloc — `conn.c:1632/1640`, `BT_CONN_FRAG_COUNT=1` | `net_buf_alloc(pool, timeout)` | caller's | `bt_conn_create_pdu_timeout` downgrades any blocking timeout to `K_NO_WAIT` **only when called from `k_sys_work_q`** (`conn.c:1606-1610`). That guard does **not** cover `bt_workq`. With `CONFIG_BT_CONN_LOG_LEVEL_DBG` and `NET_BUF_LOG` both off there is no `K_NO_WAIT` pre-try either — a single blocking alloc. |
| HCI command | `k_sem_take(&sync_sem, HCI_CMD_TIMEOUT)` — `hci_core.c:429` | 10 s | not on the peripheral RX path in steady state |
| L2CAP signalling | `bt_l2cap_create_pdu_timeout(pool, 0, L2CAP_RTX_TIMEOUT)` — `l2cap.c:464` | bounded | |

**Summary of K3.** Nothing the *application* puts on BT RX WQ can block. Every unbounded wait
reachable from that thread is inside the host, and of those, the two `k_work_flush` sites are
excluded by the live system workqueue. What survives is an ATT-pool exhaustion family: a 30 s bound
on responses that matches the measured 30 s read failure, and a `K_FOREVER` on everything else.

---

## 4. K4 — `boot_confirm_lock`, reframed again

The brief reframes this as a latent fault whose predicted signature is "a board whose PAIRED goes
dark and which never re-advertises". **Given §3.1, that prediction is wrong, and the real one is
both different and easier to test.**

Because `disconnected()` runs on the **system workqueue**, and the system workqueue is where the
watchdog is fed, a stall on `k_mutex_lock(&boot_confirm_lock, K_FOREVER)` inside `disconnected()`
does not produce a quietly dark board. It stops the watchdog feed. **The predicted signature is a
watchdog reset within 30 s** — a spontaneous reboot with `RESETREAS` showing the watchdog, and the
board back and advertising immediately after. Self-limiting, and visible in telemetry the fleet
already reports.

Every holder:

| Site | Function | Thread | Held across | Bound |
|---|---|---|---|---|
| `main.c:1805` | `boot_confirm_timeout_work_handler` | **system workqueue** | `boot_is_img_confirmed()` (flash **read**) | `K_FOREVER` |
| `main.c:1821` | `boot_confirm_commit_work_handler` | **system workqueue** | **`boot_write_img_confirmed()` — flash WRITE** | `K_FOREVER` |
| `main.c:2123` | `process_control` — `BOOT CONFIRM STATUS` | control thread | three field reads | `K_FOREVER` |
| `main.c:2143` | `process_control` — `BOOT CONFIRM PREPARE` | control thread | `bsf_boot_confirm_policy_prepare` (pure) | `K_FOREVER` |
| `main.c:2171` | `process_control` — `BOOT CONFIRM COMMIT=` | control thread | `bsf_boot_confirm_policy_commit` (pure) | `K_FOREVER` |
| `main.c:2879` | **`disconnected`** | **system workqueue** | two field assignments | `K_FOREVER` |

**ABBA analysis.** The two work handlers and `disconnected()` are all on the system workqueue, which
is serialized — they cannot contend with each other, so there is no self-deadlock. The only real
contender is the **control thread**. The hazard is therefore one-sided and simple: if the control
thread holds `boot_confirm_lock` and stalls, the system workqueue blocks in `disconnected()`, the
watchdog is not fed, and the board resets in ≤30 s. The three control-thread sites hold the lock only
across pure policy functions and field reads, so today the window is microseconds. It is not zero,
and it is on the watchdog-feeding queue.

`boot_confirm_commit_work_handler` is the more interesting one: it holds the lock across a flash
write, on the queue that feeds the watchdog, on a platform where flash writes contend with the radio
for MPSL timeslots. Same queue means no deadlock with `disconnected()`, but it is the NCSDK-29354
precondition and it makes the watchdog margin depend on flash timing.

**Neither is BSF44AD's fault** — both are after the clear, and both would have caused a reset.
Proposed fix, **stated and not implemented**: a bounded acquire in `disconnected()`
(`k_mutex_lock(&boot_confirm_lock, K_MSEC(...))`) that skips the two policy assignments on timeout,
and moving the flash write out from under the lock in the commit handler.

---

## 5. K5 — the invariant, specified

J1's verdict rested on "statements 1–18 of `disconnected()` do not block today", asserted nowhere.
**§3.1 makes the required invariant both stronger and simpler than J1 could state it.**

Because `disconnected()` runs on the system workqueue, and that queue also carries the watchdog feed
and the entire BLE TX engine, the invariant is not "nothing before statement 19 blocks". It is:

> **Nothing anywhere in `disconnected()` may block for an unbounded time.** A block anywhere in it
> costs the board a watchdog reset within 30 s, whether it is before or after the clear.

And `k_mutex_lock(&boot_confirm_lock, K_FOREVER)` at statement 23 **violates that invariant today**.
J1 classified it as a latent hazard on the strength of it being after the clear; on the corrected
threading it is a latent hazard on much shorter odds, because the consequence is not a diagnostic
inversion but a reboot.

The diagnostic-inversion risk J1 identified is real and remains, in a second form: **lit PAIRED
means "BT RX WQ never completed the disconnect state transition" only for as long as the system
workqueue is provably alive.** If a future round moves the watchdog feed off the system workqueue —
or gives the application its own workqueue and moves `telemetry_work` to it — that proof evaporates
silently and every conclusion in §3.2 with it.

### Specification — `firmware/tests/test_disconnected_contract.py`

Same shape as `test_signed_hash_rule.py` and `test_host_output_nonblocking.py`: a source contract
that bites on the *reasoning*, not on a line number. **Specified here; not implemented in this
batch.**

1. **No unbounded blocking call anywhere in `disconnected()`.** Extract the function body from
   `firmware/src/main.c` and assert that it contains no `K_FOREVER`, and that every call it makes is
   on an explicit allowlist: `LOG_INF`, `k_uptime_get`, `bsf_stall_detector_retract_disconnect`,
   `k_work_cancel_delayable`, `atomic_*`, `memset`, `make_stall_status`, `publish_stall_status`,
   `k_spin_lock`/`k_spin_unlock`, `bsf_stall_ring_retract_disconnect`, `k_mutex_lock`/`k_mutex_unlock`,
   `start_advertising`. A new call in that body fails the test until someone adds it to the
   allowlist, which is the moment to re-derive whether it blocks.
   The `K_FOREVER` assertion **fails today** — it must be introduced together with the bounded-acquire
   fix, not before, or it lands red.
2. **The watchdog feed stays on the system workqueue.** Assert `watchdog_feed_once()` has exactly one
   call site, that it is the first statement of `telemetry_work_handler`, and that `telemetry_work` is
   a `K_WORK_DELAYABLE_DEFINE` scheduled with `k_work_schedule`/`k_work_reschedule` (system queue) and
   never with `k_work_schedule_for_queue`. Assert the application defines no `k_work_q`.
3. **The reasoning goes in the file**, in a header comment: that lit PAIRED is read as "BT RX WQ never
   completed the disconnect transition"; that the reading depends on the system workqueue being
   provably alive; that the proof is the watchdog feed living on that queue; and that if (2) ever
   changes, §3.2 of this report and J1's verdict both need re-deriving.

---

## 6. K6 — the RX stack: measured every 60 s, and thrown away

`CONFIG_BT_RX_STACK_SIZE=1024`.

Two things about that number:

- Upstream Zephyr's default is **1200** (`zephyr/subsys/bluetooth/host/Kconfig:113`). NCS overrides it
  to **1024** for a non-SMP, non-settings, non-mesh build
  (`nrf/subsys/bluetooth/controller/Kconfig:89-99`). So it was not lowered by this project — it is the
  vendor default, 176 bytes below upstream's.
- The same NCS block declares `range 1024 65536`. **The build sits exactly on the floor of the
  permitted range.**

The help text is unusually direct about the risk: this is "the context from which all event callbacks
to the application occur… sufficient for basic operation, but if the application needs to do advanced
things in its callbacks that require extra stack space, this value can be increased." Against that,
`stall_status_read` puts a 232-byte `bsf_stall_ring_page_t` on it and `control_write` a ~204-byte
`struct control_request`.

**Has it ever been sampled? By the application, no — and it cannot be.** `k_thread_stack_space_get`
is called at `main.c:2379-2383` for exactly five threads: publisher, uart_parser, IMU, notify_worker
and `k_sys_work_q.thread`. The brief's recollection is confirmed from source. The BT RX WQ is absent,
and it is not an oversight that a one-line patch fixes: `bt_workq` is `static` in `hci_core.c:83` with
no public accessor, so the application cannot name the thread. Reaching it needs `k_thread_foreach()`
and a name match.

**But it has been sampled, continuously, by something else.** `CONFIG_THREAD_ANALYZER=y`,
`CONFIG_THREAD_ANALYZER_AUTO=y`, `CONFIG_THREAD_ANALYZER_AUTO_INTERVAL=60`,
`CONFIG_THREAD_ANALYZER_ISR_STACK_USAGE=y`, `CONFIG_THREAD_NAME=y`, `CONFIG_THREAD_MONITOR=y`. A
dedicated analyzer thread walks **every** thread — including `"BT RX WQ"` — every 60 seconds and
logs its stack high-water. Every board has been doing this all campaign.

It goes nowhere. `CONFIG_THREAD_ANALYZER_USE_LOG=y` routes it to the logging subsystem;
`CONFIG_UART_CONSOLE=n`; the only backend is `CONFIG_LOG_BACKEND_RTT=y` in
`SEGGER_RTT_MODE_NO_BLOCK_SKIP`. Nothing drains the RTT buffer during field runs, so it fills once
after boot and every message since has been silently skipped.

**The answer to K6 is therefore not `INSUFFICIENT`.** The figure is 1024, at the bottom of its
allowed range; the measurement exists and is being discarded; and **draining the RTT buffer on the
live wedged BSF44AD would produce a fresh dump within 60 seconds** — the current stack high-water of
the wedged thread, plus every other thread, while it is still wedged. See §8.

---

## 7. Ranked — what the evidence now supports

Ordered by how well each survives §3.2. "Cheapest observation" is the *next* one, not the whole proof.

**1. BT RX WQ is parked in a host-internal wait while the system workqueue runs normally.**
*Support: very strong.* Not a hypothesis about a mechanism — a deduction. Sysworkq alive (watchdog),
`disconnected()` dispatched from that queue, callback never ran, so `bt_conn_set_state(DISCONNECTED)`
never reached its `k_work_reschedule`. Everything below is a candidate for *which* wait.
→ **Cheapest observation: halt BSF44AD under J-Link and read the thread list (§8).** Answers it
outright. **BSF44AD can answer this.**

**2. ATT-pool exhaustion, with the RX thread cycling on 30 s response allocations.**
*Support: strong, and it is the only candidate that already predicts a measured number.*
`att_pool` is 8; `bt_att_chan_create_pdu` (`att.c:705-729`) gives responses `BT_ATT_TIMEOUT` = 30 s.
The Master's read died at exactly 30.0 s. If TX credits are gone — the peer's controller stopped
acking — the 8 buffers are held by unsendable notifications, the publisher parks on the `K_FOREVER`
branch of the same function (which is D1's publisher-side verdict, independently), and each queued
ATT request costs the RX thread 30 s. The gap: 30 s is bounded, so this explains minutes, not the
20+ observed, unless the RX backlog is long or something re-blocks.
→ **Cheapest observation: `att_pool`'s free count and the `bt_conn_get_pkts` semaphore count on the
halted board.** Both are static objects at fixed addresses. **BSF44AD can answer this.**

**3. The unbounded `K_FOREVER` branch of `bt_att_chan_create_pdu` reached from the RX thread.**
*Support: moderate — it is the only permanent wait in ATT, but no route to it from RX is identified.*
No SMP, no bonding, no EATT, peripheral-only, so Service Changed and client requests are all
implausible.
→ **Cheapest observation: same halt — the parked PC distinguishes this from (2) immediately, since
the two branches differ only in the timeout argument.** **BSF44AD can answer this.**

**4. `k_work_flush(&conn->tx_complete_work)` — the NCSDK-29354 coupling.**
*Support: weak for this episode, despite every precondition being met.* Excluded by §3.2(c): it needs
the sysworkq stalled, and the watchdog says it was not. Retained because the coupling is real, the
workaround is unapplied, and it is a live hazard in the OTA-confirm window (§2).
→ **Cheapest observation: none needed to rank it. To close it: set `CONFIG_BT_CONN_TX_NOTIFY_WQ=y`.
Not in the same round as anything else.**

**5. `boot_confirm_lock` contention.**
*Support: excluded for this episode.* Predicted signature is a watchdog reset (§4), not a dark board.
→ **Cheapest observation: grep the run's telemetry for `reset_reason` = watchdog across all ten
boards. Zero-cost, offline, on data already captured.** **BSF44AD cannot answer this — the fleet log
can.**

**6. BT RX WQ stack overflow.**
*Support: refuted.* §3.3 — it would have halted the SoC and reset the board.
→ **Cheapest observation: the analyzer dump in §8 gives the high-water anyway, as a by-product.**

**7. DRGN-23518 ACL reassembly deadlock.**
*Support: refuted three ways.* §1.1. **No further observation warranted.**

---

## 8. The BSF44AD debugger session — planned, not improvised

The specimen is preserved indefinitely on a charging POGO, so this can be prepared properly. It can
answer items 1, 2, 3 and 6 in a single session, and item 1 outright.

**Why halting is safe.** `wdt_setup(watchdog, WDT_OPT_PAUSE_HALTED_BY_DBG)` (`main.c:240`) — the
watchdog pauses while the debugger holds the core halted, so a halt cannot reset the board and
destroy the specimen. `.noinit` survives regardless.

**The one thing that must not happen** is a reset. The J-Link session must **attach**, never
`connect` with reset and never `r`. Per the standing rule, non-interactive only:
`-NoGui 1 -ExitOnError 1 -SelectEmuBySN <SNR>`. A reset would relaunch the application, clear the
wedge, and lose the only instance of this failure anyone has ever held.

**What to read, in order:**

1. **`_kernel.threads`** — the linked list of all threads. With `CONFIG_THREAD_MONITOR=y` and
   `CONFIG_THREAD_NAME=y`, find the entry named `"BT RX WQ"`. Its `base.thread_state` gives the
   pended/suspended state, and the saved callee context gives the parked PC. **This is the answer to
   item 1**, directly, and the PC distinguishes items 2 and 3.
2. **The object it is pended on** — from `base.pended_on`, the wait queue address. Resolve against the
   symbol table: if it is inside `att_pool`'s `net_buf_pool.free`, item 2/3 is confirmed; if it is
   `conn->tx_complete_work`'s flusher semaphore, item 4 is resurrected against §3.2(c) and the
   watchdog reasoning needs re-examining.
3. **`att_pool` free count** and the `bt_conn_get_pkts(conn)` semaphore count — item 2's direct test.
4. **Drain the RTT buffer and leave it draining for ~2 minutes.** The buffer itself holds stale
   post-boot data (skip mode, never drained), but once drained the 60 s thread analyzer will emit a
   **fresh** dump — every thread's name and stack high-water, including BT RX WQ, taken while the
   board is still wedged. Two dumps in two minutes. This is item 6 and the K6 figure, free.
5. **The `.noinit` ring**, at the retained-section address — readable directly over SWD without any of
   H1's self-reset machinery, since the debugger does not need the board's cooperation.

Steps 1–3 are register and memory reads and change nothing. Step 4 drains a ring buffer the firmware
treats as write-and-forget. Step 5 is a read. None of it perturbs the wedge.

---

## 9. What this round did not do

No Kconfig value changed. No callback changed. No test written. No build. No hardware touched, no
commands issued, no J-Link session opened. BSF44AD and BSFC2CC remain untouched specimens.

Three changes are *proposed* across §2, §4 and §5 — `BT_CONN_TX_NOTIFY_WQ=y`, the bounded
`boot_confirm_lock` acquire plus moving the flash write out from under it, and the pinned-invariant
test. **None should ship together.** At roughly one event per 26.5 board-hours, a combined change
gives an unattributable result either way, and "it stopped" needs very large exposure before it is a
claim rather than an absence. The diagnosis is not finished, and §8 is cheap.

## 10. Evidence index

| File | SHA-256 |
|---|---|

*(see `EVIDENCE_SHA256.txt` — generated after this file was written, so this table is deliberately
left to that file rather than duplicated stale here)*

Sources read, none modified:

- `B306_Part/builds/b306-imu-relay-v41-a/firmware/zephyr/.config` — sha256 `886158bc63245e313cc533f0874cdf4283e880a5d5d8141d03cd2660731d9f76`
- `B306_Part/builds/b306-imu-relay-v41-a/firmware/zephyr/zephyr.elf` (symbols + disassembly)
- `B306_Part/builds/b306-imu-relay-v42-a/firmware/zephyr/zephyr.elf` (symbol sizes only, §3.0)
- `B306_Part/builds/b306-imu-relay-v41-a/firmware/CMakeCache.txt` (`ZEPHYR_BASE` provenance)
- `B306_Part/firmware/src/main.c`, `firmware/prj.conf`, `include/biospur_fusion_ble.h`
- `~/ncs/v2.8.0/nrf/doc/nrf/releases_and_maturity/known_issues.rst`
- `~/ncs/v2.8.0/nrf/subsys/bluetooth/controller/Kconfig`
- `~/ncs/v2.8.0/zephyr/subsys/bluetooth/{common/Kconfig,common/dummy.c,host/Kconfig,host/conn.c,host/att.c,host/l2cap.c,host/hci_core.c}`
- `~/ncs/v2.8.0/zephyr/kernel/fatal.c`, `~/ncs/v2.8.0/zephyr/include/zephyr/bluetooth/buf.h`
- `UWB_Part/logs/deploy_20260806/B5_STALL_01_BSF44AD.md` (measured signature)
