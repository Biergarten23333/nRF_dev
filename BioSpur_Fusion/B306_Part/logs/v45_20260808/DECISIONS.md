# DECISIONS — forks taken autonomously

Per the autonomy clause: every fork, the choice, one line of rationale.

| # | fork | choice | why |
|---|---|---|---|
| 1 | The brief names `log_migration_20260808/PATH_REMAP.json` for relocated captures | used `log_relocation_20260808/`, which is what exists | same batch, different name. No input path was missing, so no remap was needed at all. |
| 2 | `zephyr/VERSION` carries an empty `EXTRAVERSION` where the manifest pins `v3.7.99-ncs1` | treated the CHECKED-OUT TREE as authoritative and recorded the discrepancy | every `file:line` in `CONTEXT_AUDIT.md` was read from this tree, so the audit is self-consistent whatever the tag says. The tree is additionally patched by this project, so a tag match would prove little. |
| 3 | §1 law 1 says no measurement shares context with the measured; §5 says the detector runs on the system workqueue — but the syswq also runs `tx_complete_work`, which the TX_WORK channel measures | **followed the brief**: detector and capture on the system workqueue | the brief is explicit and the alternative (a dedicated thread) is what v43/v44 used, with the failure mode the brief is trying to avoid. Residual risk stated in `V45_DESIGN.md` §1 and mitigated: the TX_WORK channel and `wdt_feed_count` make syswq liveness an explicit corpse field, so a decoded corpse never has to assume it. |
| 4 | The v43/v44 monitor thread still exists and can also claim the reboot budget | kept it, sharing the one budget | deleting it would silently retire an independent authority for a different wedge class. Three authorities, one reset per power cycle; v45 is owner 3. |
| 5 | `att.c` is on the notify path and would be informative to mark | **did not mark it** | `bt_att_chan_create_pdu()` is reached from the notify worker AND the BT RX WQ. §3 forbids markers in a generic multi-context helper, and that rule is exactly why v44's channel was untrustworthy. The APP_NOTIFY channel is marked at the single call site in `main.c` instead. Contract-tested. |
| 6 | §8 asks for a **512**-entry trajectory ring | used **510** | 512 is not divisible by `BSF_STALL_RING_PAGE_ENTRIES` (5), and `stall_ring_policy.h` has enforced "capacity divides evenly into pages -- no partial last page" since the ring shipped. Rendering handles a short page correctly, so 512 would work; but 510 keeps a long-standing invariant for 0.1 s of span out of 25.5. RAM delta is 12 400 B rather than the brief's ≈12 480 B. |
| 7 | §6 asks for a diagnostic API "inside Zephyr host `buf.c` (patched -- in scope)" | implemented the pool/ownership snapshot in the **application** instead | every net_buf pool is a `STRUCT_SECTION_ITERABLE` carrying `name`, `buf_count`, `avail_count`, `free` and `__bufs` in the public header, so all seven pools are reachable by name from the app. Patching `buf.c` would have added a sixth SDK file to keep in step for zero extra information. The one thing the SDK genuinely must provide -- the law-4 allocation hook -- is a five-line `__weak` no-op in `net_buf/buf.c`. |
| 8 | §14 says "guard everything behind a dedicated Kconfig, default n" | added an application `Kconfig` (`BSF_V45_TRACE`, `BSF_V45_FAULT_INJECT`) **and** kept `__has_include` | an application Kconfig's symbols reach `autoconf.h`, which every compiled file sees including the patched SDK ones, so this is a real Kconfig and not a substitute. `__has_include` is belt-and-braces: either condition failing neutralises the instrumentation completely. |
| 9 | The jitter before `sys_reboot()` | a **delayed work item**, not `k_sleep()` | up to 4 s of `k_sleep` on the system workqueue would stall `telemetry_work_handler()`, which is the watchdog feed, on that same queue. `WATCHDOG_TIMEOUT_MS` is 30 000 so it would probably have survived — "probably" is not a reason to park the watchdog. |
| 10 | `ota_active` had no existing signal | enabled `CONFIG_MCUMGR_MGMT_NOTIFICATION_HOOKS` + `CONFIG_MCUMGR_GRP_IMG_STATUS_HOOKS` and registered a callback | the alternatives were `boot_confirm_policy.required` (only covers post-boot confirmation) or inferring it from `pkt_pool` occupancy (indirect and racy). A 4.1 s notify was measured during a DFU, so this arm is a real false-positive source, not a theoretical one. A 30 s keepalive means an abandoned upload cannot disarm the detector permanently. |
| 11 | `FINAL_BT_WEDGE_FORENSICS.md` §4 names the `free_tx` FIFO as a second unbounded wait reachable from `bt_gatt_notify()` | **corrected it**: in NCS v2.8.0 that allocation is `K_NO_WAIT` | `conn.c:548-554`. Exhaustion is handled as `tx_processor` back-pressure via `dont_have_tx_context()`. The rank-1 conclusion is unaffected — one unbounded wait suffices — but the §4 wait-object table must not advertise a wait that cannot occur. v45 exports the address anyway so "never matched" becomes a checked result. |
| 12 | §9's justification for writing flash before `bt_enable()` ("radio off, no MPSL sync needed") | **the premise is false**; kept the conclusion on a different argument | MPSL initialises at `PRE_KERNEL_1`, so `nrf_flash_sync_is_required()` is true everywhere the app can run. The replacement argument (capture never writes flash; the persist runs post-reboot on the `main` thread with no BLE role scheduled; the call is bounded and fails safe) is stronger and is in `CONTEXT_AUDIT.md` item 10. |
| 13 | §2 item 11 asks for free flash for an 8–16 KB corpse partition; there is **none** | shipped the full implementation with `BSF_CORPSE_FLASH_ENABLED=0`, plus a non-default `pm_static_v45_corpse.yml` and an overlap checker | the deployed map tiles the whole 1 MiB exactly. Every carve that does not move MCUboot's boundaries was rejected for cause. The one clean carve requires an SWD reflash of MCUboot on every board, which the OTA-only Stage C cannot do. Building it (`b306-imu-relay-v45-flash`) proves the code and the map are correct; enabling it is a separate campaign. |
| 14 | The forbidden thread name appeared once, inside a comment saying it does not exist | reworded the comment | the source contract greps for the literal string, and a check with an exception carved out for "but this one is fine" is a check people learn to route around. |
| 15 | Existing tests broke on the v45 changes (`test_bt_stage_contract.py` gate rename; `test_stall_ring_policy.c` hard-coded 200-entry geometry) | updated them, preserving each assertion's INTENT | the ring test's fill counts became `BSF_STALL_RING_CAPACITY`-relative and its page loops use the ring's actual page count, so the same properties are tested at any geometry. The contract test now accepts either patch-manager name while still requiring the build to refuse an unverified SDK. |
| 16 | `test_bsf_v45_decoder.py` found no DWARF for `bsf_v45_flash_header_t` | merged DWARF across **all** v45 builds and added compile-time `_Static_assert`s on every wire struct | a struct only gets a DWARF entry if something references it, and that one is behind `BSF_CORPSE_FLASH_ENABLED`. Skipping it silently would have made the layout check worth nothing on exactly the struct nobody exercises. |
| 17 | A detector unit test failed on "uptime wrap" | the **test** was wrong, not the detector | it left `connected_at_ms = 0`, i.e. a link up for 49.7 days, so the arm condition correctly read "connected 984 ms ago" the instant the clock wrapped. Fixed the fixture to a realistic 60 s and recorded the trap in the test comment. |
| 18 | §12 asks for fault-injection hooks "implement + unit-test now, do not run on hardware" | implemented behind `BSF_V45_FAULT_INJECT` (default 0), with a build-time `message(WARNING)` if it is ever set | the sync_evt leak deliberately induces the suspected failure. A fleet image that could do that on command is a hazard, so it is a compile-time decision, not a runtime one. |
| 19 | Where the `V45 *` opcodes should live | on the existing vendor command channel, reusing the 232-byte stall-characteristic envelope with `form = 0xC5` | this is what keeps the master frozen at dk-v36 **by construction**: it transports an opaque string and an opaque fixed-length read and parses neither. No hard gate failure to report. |
| 20 | `main.c:541 unused variable 'pool'` warning | left it | pre-existing, documented in the source at the point of definition, and unrelated to v45. Fixing unrelated warnings inside a diagnostic change is how a diff stops being reviewable. |

---

## Where I am not certain

1. **The detector's home.** #3 above. The brief is unambiguous and I followed it,
   but a syswq-blocking wedge is undetectable by a syswq-resident detector, and
   nothing in the four observed events rules that class out for the *future*.
   The corpse records syswq liveness so a reader can always tell — but if the
   fleet runs and nothing ever triggers, this is the first thing to re-examine.
2. **`true_min_avail` under concurrency.** The law-4 hook does a plain compare
   and store rather than an atomic RMW loop, because it runs on the allocating
   thread and must be near-free. A race can lose one update of a monotone
   minimum. Bounded and stated, but it means `true_min_avail` is a *lower bound
   on the observed minimum*, not provably the exact minimum.
3. **Rejoin timing after the jittered reboot.** The 20.7 s figure is inherited
   from earlier measurements; the added 0–4 s jitter should simply add to it,
   and the host script's 60 s grace covers both. Not measured, because no
   hardware was touched.
4. **Whether `hci_rx_pool` owner tracking will ever fire.** The forensics say no
   holder can exist in this configuration. The hooks cost nothing, so they ship,
   but a permanently empty field is the expected outcome, not a defect.
5. **Flash persistence has never executed.** The code compiles, links and passes
   its overlap and layout checks, but with the partition absent from the default
   map it has never run — not on hardware, not in emulation. Treat Stage B
   injection 1 as its first real test, and only on a board that has been
   SWD-reflashed with the overlay.
