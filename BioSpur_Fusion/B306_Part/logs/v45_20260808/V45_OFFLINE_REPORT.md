# V45_OFFLINE_REPORT

**OFFLINE IMPLEMENTATION AND BUILD ONLY. No hardware was touched: no J-Link,
no SWD, no flashing, no OTA, no BLE commands, no serial ports.**

| | |
|---|---|
| starting HEAD (as actually checked out) | `d19538c94ab4bf193177e3f2ce23ce6104187258` |
| branch | `feature/b306-bringup` |
| marker | `b306-imu-relay-v45`, VERSION `0.1.45-imu-relay` |
| SDK | `/home/zekaixiao/ncs/v2.8.0`, nrf 2.8.0, zephyr 3.7.99 (see §uncertainty 1) |
| master firmware | **FROZEN at dk-v36 and untouched** — `git diff` over `B306_Part/host/` against the starting HEAD is empty, asserted by the source contract on every run |

---

## 1. Headline

**All §15 gates PASS.** The context audit is complete and returned three
CORRECTED items, each of which changed the design rather than being smoothed
over. Sixteen test suites pass. Two clean builds are byte-identical in the
unsigned application and MCUboot. FLASH 46.47 %, RAM 52.88 %, and the entire
`.noinit` growth is accounted for byte for byte.

One requirement could not be *deployed* as specified, and it is not hidden:
**flash persistence ships disabled because the board has zero free flash.** The
code is complete, compiles, links, and passes an overlap checker against a real
generated partition map; enabling it needs an SWD reflash of MCUboot on every
board, which the OTA-only Stage C cannot do.

## 2. What the audit found, and what it cost

| item | verdict | consequence |
|---|---|---|
| 1 receive worker | PASS | `MPSL_RX` binds to `mpsl_work_q.thread` ("MPSL Work"), cooperative prio 6, stack 1024 |
| 2 K_FOREVER on the receive path | PASS | two unbounded allocations confirmed; the design's central premise holds |
| 3 `sync_evt_pool` + pool hashes | PASS | **exactly one buffer**; 88 pool names, 88 distinct hashes, 0 collisions; `0x27b70977`→`sync_evt_pool`, `0xef427c73`→`pkt_pool`. The forensics' last open pool item is closed by construction. |
| 4 priority path inline; NCP chain | PASS | NCP runs inline on MPSL Work with the scheduler locked; the per-packet loop is where `ncp_packet_total` lives |
| 5 completion context | PASS | `k_sys_work_q`; **three unlocked mutation contexts** for `tx_pending` → shadow atomics are mandatory, not a preference |
| 6 disconnect halves | PASS | normal half on BT RX WQ, and `start_advertising()` is called directly from it |
| 7 does `bt_gatt_notify()` block | **CORRECTED** | **ONE** unbounded wait, not two. `FINAL_BT_WEDGE_FORENSICS.md` §4's `free_tx` claim is wrong for NCS v2.8.0 (`K_NO_WAIT`, conn.c:550). Rank-1 conclusion unaffected; the §4 wait-object table is. |
| 8 existing stage writers | PASS | ≥3 concurrent writers on the v44 global channel, confirmed. Retired as an authority. |
| 9 ring sampler context | **CORRECTED** | system-clock ISR, not the syswq — *stronger* than required, kept |
| 10 flash / MPSL | **CORRECTED** | the brief's premise is false (MPSL inits at `PRE_KERNEL_1`); the conclusion survives on a better argument |
| 11 free flash | PASS as an audit, **NEGATIVE** as a result | zero free bytes |

## 3. Gate results

| gate | result | evidence |
|---|---|---|
| context audit complete, incl. flash-partition and flash-before-`bt_enable` proofs | **PASS** | `CONTEXT_AUDIT.md` / `.json`, 11 items, all resolved |
| all unit / contract / decoder tests | **PASS** | 11 C suites + 5 Python suites, listed in §4 |
| patch manager apply / verify / revert / re-apply | **PASS** | `sdk_patch.sh selftest` → `apply=pass verify=pass revert=pass reapply=pass files=5` |
| two clean builds byte-reproducible (unsigned app, MCUboot) | **PASS** | `zephyr.bin` `5f80fd8976e4dcd0…` and MCUboot `3ce8194b94b3f89f…` identical across `-a`/`-b` |
| FLASH < 95 %, RAM < 85 % | **PASS** | FLASH 231 976 / 499 200 = **46.47 %**; RAM 138 628 / 262 144 = **52.88 %** |
| map-file delta accounted | **PASS** | see §5 |
| no accidental raw logs committed; `git diff --check` clean | **PASS** | only source, tests, tools and this log directory are staged |
| marker `b306-imu-relay-v45` present | **PASS** | `MARKER_GUARD_PASS marker=b306-imu-relay-v45` on both builds |
| master untouched (verify by diff) | **PASS** | zero changes under `B306_Part/host/` |

**On the signed binary.** `zephyr.signed.bin` differs between `-a` and `-b`
(`ff2ce82c…` vs `fbd2747e…`). That is imgtool's ECDSA nonce, not a build
difference: the same is true of the existing v44 pair, while their unsigned
binaries match exactly. The gate is on the unsigned app and MCUboot, and both are
identical.

## 4. Tests

| suite | result |
|---|---|
| `test_bsf_v45_detector.c` — 14 groups | **PASS** — healthy/no-trigger, all six arm conditions, producer-stopped, notify_exit arm at exactly 20 s, ncp arm at exactly 20 s, both-frozen single capture, one-reboot-per-power-cycle over 200 passes, counter wrap, uptime wrap, epoch replacement, normal disconnect, suspicion mark, forced, jitter determinism |
| `test_v45_source_contract.py` — 16 sections | **PASS** — pool hashes, K_FOREVER survival, inline priority path, single-writer enforcement *and drop*, law-7 bans, per-channel writer sets, watermark stage/order, no `_cb` variant, no list traversal in capture, irq_lock scope, law-4 hook, frozen Kconfig, neutralisation, patch-manager hashes, schema/geometry, capture-never-touches-flash, master untouched, marker |
| `test_bsf_v45_decoder.py` | **PASS** — DWARF-vs-model layout check on every wire struct and eight CORE offsets; round trip; ten refusal cases; flash container incl. the brownout partial; `pended_on` naming; contamination reporting; stale-owner reporting; two decision-table rows |
| `test_v45_partition_overlap.py` | **PASS** — default map proven to have zero free bytes; overlay proven overlap-free, sector-aligned, two erasable slots, equal MCUboot slots; the generated map from the real flash build cross-checked |
| `test_stall_ring_policy.c` | **PASS** — updated to the 510-entry geometry, assertions' intent preserved |
| `test_bt_stage_contract.py`, `test_v36_source_contract.py`, and 8 other pre-existing C policy suites | **PASS** — no regressions |

## 5. Map-file delta

`.noinit` **85 188 → 107 076 B = +21 888**, accounted exactly:

| region | bytes | note |
|---|--:|---|
| trajectory ring 200 → 510 entries | +12 400 | `(510−200) × 40` |
| four channel structs | +8 400 | `4 × (52 state + 128 × 16 trace)` |
| CORE | +944 | matches `sizeof(bsf_v45_core_t)` from the ELF (`0x3b0`) |
| five bank headers | +140 | `5 × 28` |
| alignment | +4 | |
| **total** | **+21 888** | |

`.bss` +404 B. Whole-image RAM 116 336 → 138 628 B (44.4 % → 52.9 %).

## 6. What is complete but not deployable

**Flash persistence.** `pm_static.yml` tiles the full 1 MiB:
`mcuboot 0x0–0xc000`, `mcuboot_primary 0xc000–0x86000`,
`mcuboot_secondary 0x86000–0x100000`. Nothing is left.

Rejected alternatives, each for a stated reason: the tail of `mcuboot_secondary`
holds the swap trailer and a corpse there could corrupt a **staged OTA image**
(fleet brick risk); the tail of `app` is inside the swapped slot and is erased by
the next OTA; UICR is one-shot.

The one clean carve is `pm_static_v45_corpse.yml` — both slots shrunk 8 KiB,
16 KiB at `0xfc000`, slots kept equal so `boot_slots_compatible()` is trivially
satisfied for swap-using-move. It **builds and links**
(`b306-imu-relay-v45-flash`: FLASH 47.38 % of the shrunken slot, RAM 56.01 %) and
Partition Manager materialises the map exactly as specified. But MCUboot compiles
its own flash map in, so it needs an SWD reflash of every board.

So `BSF_CORPSE_FLASH_ENABLED=0` ships. Consequence, stated plainly: a corpse
captured and then power-cycled before collection is lost, and a second corpse in
one power cycle is lost when the operator power-cycles the still-wedged board.
The self-reboot narrows the first window from hours to ~40 s.

## 7. Corrections to prior documents

1. **`FINAL_BT_WEDGE_FORENSICS.md` §4 rank 1** — the `bt_conn_tx` allocation from
   the 8-deep `free_tx` FIFO is **not** a second unbounded wait in NCS v2.8.0.
   `conn_tx_alloc()` is `K_NO_WAIT`. The mechanism is unchanged; the wait-object
   table is not.
2. **The brief's §9 justification** — "flash writes before `bt_enable()` do not
   require MPSL sync" is false. MPSL initialises at `PRE_KERNEL_1`.
3. **The brief's §2 pre-verified note** on `0xef427c73` said it matches no Zephyr
   host BT pool. Correct, and now fully resolved: it is `pkt_pool`, the MCUmgr
   SMP transport, found in the local tree.
4. **The brief names `log_migration_20260808/PATH_REMAP.json`**; the batch is
   `log_relocation_20260808`. No input path was missing, so nothing needed
   remapping.

## 8. Where I am not certain

Carried verbatim from `DECISIONS.md`, because it is the part most worth reading:

1. **The detector's home.** Following the brief puts it on the system workqueue,
   which also runs `tx_complete_work` — one of the things it measures. A wedge
   that blocks the syswq is invisible to it. That class is excluded for all four
   observed events and for no future one. The corpse records syswq liveness
   explicitly so a reader can always tell; if the fleet runs and nothing
   triggers, re-examine this first.
2. **`true_min_avail` under concurrency** is a lower bound on the observed
   minimum, not provably the exact minimum — the hook uses a plain compare-and-
   store rather than an atomic RMW so it stays near-free on the allocating thread.
3. **Rejoin timing after the jittered reboot** is inherited (~20.7 s), not
   measured, because no hardware was touched. The host script's 60 s grace covers
   it with margin.
4. **`hci_rx_pool` owner tracking may never fire.** The forensics say no holder
   can exist in this configuration. A permanently empty field is the expected
   outcome, not a defect.
5. **Flash persistence has never executed** — not on hardware, not in emulation.
   Stage B injection 1 on an SWD-reflashed board is its first real test.

---

V45 OFFLINE GATE PASS — CONTEXT-CORRECT CORPSE, DUAL-WATERMARK DETECTOR, FLASH PERSISTENCE READY — NO HARDWARE TOUCHED
