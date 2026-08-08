# V45_DESIGN — context-correct corpse, dual-watermark detector, flash persistence

Starting HEAD, as actually checked out: **`d19538c94ab4bf193177e3f2ce23ce6104187258`**,
branch `feature/b306-bringup`. No commit hash from any other document was trusted.

Marker `b306-imu-relay-v45`, VERSION `0.1.45-imu-relay`.
Fusion Master firmware **untouched** — `git diff` against the starting HEAD over
`B306_Part/host/` is empty, and the source contract asserts it on every run.

---

## 0. What v45 is for, in one paragraph

The fault is not "the node stops sending". It is that **TX-completion processing
stops permanently and never restarts**, and 107 board-hours of capture could not
observe it because *there has never been a completion-stage counter anywhere in
this system*. v45 adds two — one at the controller's confirmation
(`ncp_packet_total`), one at the notify call's return (`notify_exit_total`) —
puts each of four execution contexts on its own single-writer trace channel, and
captures a corpse that says which thread is parked on which named kernel object.
It observes. It does not treat: §13's buffer counts are frozen and contract-tested.

## 1. Design laws, and where each one landed

| law | where it is enforced | how a violation is caught |
|---|---|---|
| 1. no measurement shares context with the measured | detector + capture on the **system workqueue**; four channels each bound to one thread | §5 of `test_v45_source_contract.py` pins which file may write which channel |
| 2. single writer, enforced at runtime | `bsf_v45_mark()` compares `k_current_get()`, **drops** the write, counts it, latches the first offending TID | contract test asserts the drop; decoder prints `*** FOREIGN WRITES ... CONTAMINATED ***` |
| 3. watermarks are completion/exit stage only | `notify_exit_total` after the call returns; `ncp_packet_total` per packet inside the NCP loop | contract test asserts ordering, and that `producer_seq` appears nowhere in the trigger |
| 4. low-water at every allocation | `__weak bsf_v45_net_buf_alloc_hook()` in `zephyr/lib/net_buf/buf.c`, strong definition in `bsf_v45_pools.c` | contract test asserts the hook is on the success path |
| 5. no lockless traversal of live lists | shadow atomics at the three real `tx_pending` mutation sites | contract test greps the capture routine for `sys_slist_*` and `tx_pending` |
| 6. counters global / stage machines single-writer | `bsf_v45_cnt` is all `atomic_t`; channels are per-thread structs | — |
| 7. hot path may only write RAM / atomics / compare TID | marker body | contract test bans `LOG_`, `k_sleep`, `k_mutex`, `k_work_submit`, `net_buf_alloc`, `flash_area`, `printk` inside it |
| 8. seq published last | `__DMB(); c->seq++` after the payload | — |

**Law 1 has a residual risk and it is not papered over.** The system workqueue
also runs `tx_complete_work`, which the TX_WORK channel measures, and a wedge
that blocks the syswq is invisible to a detector living on it. That class is
excluded for all four observed events (BSF1120 fed the watchdog ~5 400
consecutive times while wedged for 4 h 38 min) and for no future one. Mitigation:
the TX_WORK channel and `wdt_feed_count` record syswq liveness explicitly, so a
decoded corpse **states** whether the syswq was running instead of the question
being begged by where the detector lives. Recorded in `DECISIONS.md` #3.

## 2. What the source audit changed

`CONTEXT_AUDIT.md` is the gate and it is complete. Three of its eleven items came
back **CORRECTED**, and each changed the design:

1. **Item 7 — there is ONE unbounded wait reachable from `bt_gatt_notify()`, not
   two.** `FINAL_BT_WEDGE_FORENSICS.md` §4 names the `free_tx` FIFO as a second;
   in NCS v2.8.0 `conn_tx_alloc()` is `K_NO_WAIT` (conn.c:550) and exhaustion is
   `tx_processor` back-pressure. The rank-1 mechanism is unchanged — one
   unbounded wait released only by `tx_notify_process()` is sufficient — but the
   §4 wait-object table must not advertise a wait that cannot happen. v45
   exports the `free_tx` address anyway, precisely so "never matched" is a
   checked result rather than an unexamined claim.
2. **Item 10 — "flash before `bt_enable()` needs no MPSL sync" is false.** MPSL
   initialises at `PRE_KERNEL_1`, so `nrf_flash_sync_is_required()` is true
   everywhere the application can run. §9 below carries the replacement argument.
3. **Item 9 — the 50 ms ring sampler runs in the system-clock ISR**, not on the
   system workqueue. That is *stronger* than the requirement, so it is kept.

Item 3 closed the forensics' last open pool question by construction: 88
`NET_BUF_POOL*` names in the tree, 88 distinct FNV-1a/32 hashes, zero collisions,
and `0x27b70977` → `sync_evt_pool` (**exactly one buffer**), `0xef427c73` →
`pkt_pool`.

## 3. The four channels

| channel | sole writer | marked in | answers |
|---|---|---|---|
| `MPSL_RX` | `mpsl_work_q.thread` ("MPSL Work") | `nrf/.../hci_driver.c` + the inline priority arm in `hci_core.c` | is the controller→host inlet fetching, and which allocation is it inside |
| `BT_RX` | `bt_workq.thread` ("BT RX WQ") | `hci_core.c` `rx_work_handler`, `hci_disconn_complete`; the `k_work_flush` wait in `conn.c` | are normal events and ACL dequeued and completed |
| `TX_WORK` | `k_sys_work_q.thread` ("sysworkq") | `conn.c` `tx_complete_work` / `tx_notify_process` | does NCP-driven completion work actually run |
| `APP_NOTIFY` | notify worker | `main.c`, the single `bt_gatt_notify()` call site | does the notify call enter and exit, with what return code and duration |

`att.c` carries **no** v45 marker: `bt_att_chan_create_pdu()` is reachable from
the notify worker *and* the BT RX WQ, and §3 forbids marking a generic helper
with more than one calling context. The contract test asserts its absence.
`bt_conn_tx_notify()` is likewise unmarked except on the flush branch, which only
the BT RX WQ can reach.

Storage: `128 entries × 16 B × 4 channels = 8 400 B` of `.noinit` including
per-channel state. Traces are frozen **in place**, never copied.

## 4. Thread and wait-object snapshot

Five threads: MPSL Work, BT RX WQ, sysworkq, notify worker, publisher. The first
three are found by name (`CONFIG_THREAD_NAME`/`THREAD_MONITOR` were already on);
the two application threads are handed in directly.

Per thread: `tid`, `thread_state`, `prio`, **`pended_on`**, `callee_saved.psp`,
stack start/size/unused, and the owning channel's last `seq`. Fixed fields are
copied under a **brief `irq_lock`**; the stack scan and every CRC are outside it,
as §4 requires.

The wait-object table is resolved from the application by walking the
`net_buf_pool` iterable section by name — `&pool->free._queue.wait_q`, which is
exactly what `k_lifo_get` pends on. Only `free_tx` needs the patched `conn.c`,
because it is a file-static `k_fifo`. That kept the SDK patch to five files
instead of six for zero loss of information.

## 5. The detector

Arm: `connected ∧ data CCC subscribed ∧ ≥64 completed notifications this
incarnation ∧ ≥10 s since connect ∧ producer advancing ∧ no OTA`.
OTA state comes from MCUmgr's own `MGMT_EVT_OP_IMG_MGMT_*` hooks with a 30 s
keepalive, so an abandoned upload cannot disarm the detector for the rest of a run.

Trigger: producer advancing ∧ connected ∧ subscribed ∧
`[ notify_exit_total frozen ≥20 000 ms OR ncp_packet_total frozen ≥20 000 ms ]`.

20 s uniform, no 5 s fast arm: healthy notify calls of 100–400 ms are routine and
4.1 s was observed during DFU, while the four wedges lasted 615–19 669 s and the
entire near-miss population at a 2 s floor is 22 events, all inside depletion
cascades. Onset context is not lost by waiting — the 25.5 s ring still holds 5.5 s
of run-in at onset + 20 s.

Suspicion mark at the first frozen pass (~1–2 s): records `suspect_start_ms` and
the ring index. Freezes nothing, costs nothing, and points the corpse at the
onset instead of at the trigger.

Recovery: capture → claim the shared one-per-power-cycle budget → deterministic
0–4 000 ms node-derived jitter → `sys_reboot(SYS_REBOOT_COLD)`. The jitter is a
**delayed work item, not `k_sleep`** — sleeping would park the system workqueue,
and `telemetry_work_handler()` (the watchdog feed) is on that same queue. Second
trigger in one power cycle: capture to the other slot, **do not reboot**, stay up
wedged. No boot loops, ever; the unit test asserts exactly one reboot however
long the board stays wedged.

`bsf_v45_detector.h` is a pure function with no kernel calls, which is why
`test_bsf_v45_detector.c` can cover all fourteen required cases on the host,
including counter wrap, uptime wrap, and epoch replacement.

## 6. Buffer ownership

`sync_evt_pool` in full: avail, `true_min_avail`, alloc counters, and the single
buffer's `{ptr, ref, type, len}` plus last owner ∈ {DRIVER_EVT_ALLOC, PRIO_NCP,
PRIO_CMD_COMPLETE, PRIO_CMD_STATUS, PRIO_DISCONNECT, INJECTED, FREE_OR_UNKNOWN}.
The owner is deliberately **not cleared on free** — "who held it last" is worth
more than "nobody holds it" — and the decoder prints `(ref==0, so the owner field
is STALE and must be ignored)` rather than leaving the reader to work it out.

`hci_rx_pool`: ten compact per-buffer entries with owners. This is a
**capture-time** snapshot; the forensics' deletion of runtime 1 Hz holder
*sampling* stands, and Δ2's "hci_rx_pool does NOT need holder sampling" is
respected — it costs nothing at runtime because the hooks sit on paths that run
about once per 22–25 s.

`att` / `acl_tx` / `hci_cmd` / `fragments`: avail + `true_min_avail` only.

## 7. Retired / kept semantics

`notify_ok` stays, documented SUBMISSION-stage, gating nothing. The 1 Hz pool
sampler stays for continuity with its fields documented as biased; `true_min_avail`
is authoritative from v45 on. `publisher_max_us` still only updates on return.
The v43/v44 global stage, its enums, its corpse and its decoder all survive
untouched — retired as an *authority*, not deleted.

## 8. Ring and corpse

Ring 200 → **510** entries × 50 ms = **25.5 s** (`+12 400 B`). Not 512: that is
not divisible by `BSF_STALL_RING_PAGE_ENTRIES` (5), and this file has enforced
"no partial last page" since the ring shipped. Two entries of span is the smaller
concession. Recorded in `DECISIONS.md` #6.

`BSF_V45_SCHEMA = 3`. CORE is **944 B** (target ≤1 KB); five banks each with
`{magic, schema, length, seq, CRC, valid-last}`. Total export image **29 676 B**,
135 pages of 220 B. Every wire struct carries a `_Static_assert` on its size, the
decoder models each with an explicit little-endian format, and
`test_bsf_v45_decoder.py` checks the model against the **real ELF's DWARF** —
three independent legs, because a decoder with a drifted model produces plausible
nonsense.

## 9. Flash persistence — implemented, and shipped OFF

Capture writes `.noinit` only. It **never** touches flash, because the wedged
thread may *be* `mpsl_work_q.thread` and item 10 proves every flash write takes an
MPSL timeslot. The persist runs after the cold reboot, on the `main` thread,
before `bt_enable()`: no BLE role is scheduled, the EARLIEST timeslot is granted
immediately, and the call is bounded by `FLASH_TIMEOUT_MS` and returns an error
on timeout — so a failure degrades to "the corpse stayed in `.noinit`" and can
never hang the boot.

**`BSF_CORPSE_FLASH_ENABLED=0` by default, and that is a finding.** The deployed
`pm_static.yml` tiles the whole 1 MiB with **zero** free bytes. Every carve that
avoids moving MCUboot's boundaries was rejected for cause (the tail of
`mcuboot_secondary` holds the swap trailer — corpse writes there could corrupt a
staged OTA image, i.e. fleet brick risk). The one clean carve,
`pm_static_v45_corpse.yml`, shrinks both slots by 8 KiB and puts 16 KiB at
`0xfc000`; it builds, links, and passes the overlap checker. But **MCUboot
compiles its own flash map in**, so enabling it needs an SWD reflash of every
board, and Stage C is OTA-only. Consequence, stated: a corpse that is captured
and then loses power before collection is lost, and a *second* corpse in one
power cycle is lost when the operator power-cycles the still-wedged board. The
self-reboot narrows the first window from hours to ~40 s.

## 10. Capture sequence

Freeze channels → freeze the ring, record the suspect index → thread snapshots
under a brief `irq_lock` → pool/ownership/conn snapshots → counters and watermark
ages → bank CRCs then bank `valid`, CORE CRC then CORE `valid` **last** → claim
budget, set owner → jittered cold reboot → (next boot) persist before
`bt_enable()` → advertise → collect → ACK-clear only after verified export.

## 11. Collection

`V45 STATUS` / `V45 PAGE=<n>` / `V45 ACK=<seq>` / `V45 FORCE` on the **existing**
vendor command channel, returning the **same 232-byte** envelope every other form
of the stall characteristic uses, distinguished only by `form = 0xC5`. The master
transports an opaque string and an opaque fixed-length read; it needs no change,
and the frozen-dk-v36 requirement is met by construction rather than by luck.

`tools/v45_corpse_collect.py` queries status on reconnect, retrieves, verifies
every page CRC16 and the aggregate CRC32, decodes, writes immutable evidence,
**then** ACKs — and treats a `V45_WEDGE` reset as expected for 60 s with no
quarantine.

## 12. Outcome → conclusion decision table

Implemented in `bsf_v45_corpse_decode.verdict()` and exercised by the decoder
tests, so this table is executable rather than aspirational.

| Corpse shows | Conclusion |
|---|---|
| receive thread `pended_on` = `sync_evt_pool.free`, avail 0, ref 1 | singleton sync buffer held; inlet blocked; the owner field names the holder path |
| same + `last_owner = PRIO_NCP` + `NCP_ENTER > NCP_EXIT` | fault inside NCP / `bt_conn_tx_notify(false)` |
| receive thread `pended_on` = `hci_rx_pool.free`, ≥1 ref ≠ 0 | true RX pool exhaustion; per-buffer owners name the holders |
| MPSL enters/exits balanced, BT_RX ENTER without EXIT | normal host RX WQ blockage; compare `pended_on` with the recorded flush sync object to re-test `k_work_flush` |
| NCP advances, `tx_work` never enters | system-workqueue TX-work scheduling fault |
| notify ENTER > EXIT with all upstream healthy | app/ATT/TX path blockage |
| MPSL healthy and idle, no message available, all watermarks frozen | SDC stopped delivering — below the patchable layer; the K1 Nordic known-issue list becomes primary, and corpse + forensics are the ticket evidence pack |

## 13. Frozen configuration

`BT_CONN_TX_NOTIFY_WQ=n`, `BT_HCI_ACL_FLOW_CONTROL=n`, `BUF_ACL_RX_COUNT=6`,
`BUF_EVT_RX_COUNT=10`, `BUF_ACL_TX_COUNT=8`, `ATT_TX_COUNT=8`,
`L2CAP_TX_BUF_COUNT=8`, `CONN_FRAG_COUNT=1`, `RX_STACK_SIZE=1024`, `MAX_CONN=1`.
Contract-tested in `test_v45_source_contract.py` §10. No pool-count increases, no
workarounds, no erratum mitigations. The upstream comment inviting a larger
`sync_evt_pool` is recorded as a future lever and left alone.

## 14. Patch manager

`patches/sdk_patch.sh` — five files across **two** roots (`zephyr/` and `nrf/`),
three states only (pristine / patched / refuse), and a `selftest` subcommand that
runs apply → verify → revert → re-apply for real and finishes patched. It
supersedes `host_patch.sh`; the old patch file stays in the repository for
provenance. Everything is neutralised for other consumers of the shared SDK by
`CONFIG_BSF_V45_TRACE` (app `Kconfig`, `default n`) **and**
`__has_include(<bsf_v45_trace.h>)`.

## 15. Build results

| gate | result |
|---|---|
| context audit complete, incl. flash-partition and flash-before-`bt_enable` proofs | PASS (two items CORRECTED, both carried into the design) |
| unit / contract / decoder / partition tests | PASS — 11 C suites, 5 Python suites |
| patch manager apply/verify/revert/re-apply | PASS |
| two clean builds byte-reproducible (unsigned app, MCUboot) | PASS — `zephyr.bin` `5f80fd89…`, MCUboot `3ce8194b…` identical across `-a` and `-b`. The **signed** binary differs by design: imgtool's ECDSA nonce is random, and the same is true of the v44 pair. |
| FLASH < 95 %, RAM < 85 % | PASS — FLASH 46.47 % (231 976 / 499 200), RAM 52.88 % (138 628 / 262 144) |
| map-file delta accounted | PASS — `.noinit` 85 188 → 107 076 = **+21 888 B** = ring +12 400, channels +8 400, CORE +944, banks +140, +4 alignment |
| no accidental raw logs committed; `git diff --check` clean | PASS |
| marker `b306-imu-relay-v45` present | PASS |
| master untouched | PASS — `git diff` over `B306_Part/host/` is empty |
