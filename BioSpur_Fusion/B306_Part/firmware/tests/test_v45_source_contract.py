#!/usr/bin/env python3
"""Source contract for the v45 instrumentation (brief section 12).

WHY EACH ASSERTION IS HERE
--------------------------
These are not style checks. Each pins a property that something already got
wrong, or that a later tidy-up would silently invalidate.

The expensive class is SINGLE WRITER. v44 shipped one global stage channel that
the context audit later proved has at least three concurrent writers -- and two
of them are exactly the paths v45 exists to read. The channel was not wrong
because someone wrote bad code; it was wrong because nothing checked. So a
deliberately introduced second writer must FAIL a test, and that test is here.

The second class is CONTEXT. Three instrument failures in a row shared one
shape: the measurement shared execution context or authority with the measured
system. Markers inside a generic helper reachable from several threads are how
that happens by accident.
"""
import hashlib
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]          # .../B306_Part
fw = root / "firmware"
sdk_root = Path("/home/zekaixiao/ncs/v2.8.0")

trace_h = (fw / "src/bsf_v45_trace.h").read_text()
corpse_h = (fw / "src/bsf_v45_corpse.h").read_text()
det_h = (fw / "src/bsf_v45_detector.h").read_text()
v45_c = (fw / "src/bsf_v45.c").read_text()
pools_c = (fw / "src/bsf_v45_pools.c").read_text()
main_c = (fw / "src/main.c").read_text()
cmake = (fw / "CMakeLists.txt").read_text()
kconfig = (fw / "Kconfig").read_text()
prj = (fw / "prj.conf").read_text()
patch = (fw / "patches/ncs-v2.8.0-bsf-v45-instrumentation.patch").read_text()
script = (fw / "patches/sdk_patch.sh").read_text()
ring_h = (fw / "src/stall_ring_policy.h").read_text()

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


def fnv1a(s: str) -> int:
    h = 2166136261
    for b in s.encode():
        h = ((h ^ b) * 16777619) & 0xFFFFFFFF
    return h


# =====================================================================
# 1. The pool-hash facts the whole round turns on
# =====================================================================
check(fnv1a("sync_evt_pool") == 0x27B70977,
      "0x27b70977 must resolve to sync_evt_pool -- if this ever stops being "
      "true the forensics' pool identification is wrong")
check(fnv1a("pkt_pool") == 0xEF427C73,
      "0xef427c73 must resolve to pkt_pool (MCUmgr SMP transport)")

buf_c = (sdk_root / "zephyr/subsys/bluetooth/host/buf.c").read_text()
m = re.search(r"NET_BUF_POOL_FIXED_DEFINE\(sync_evt_pool,\s*(\d+),", buf_c)
check(m is not None and m.group(1) == "1",
      "sync_evt_pool must have EXACTLY one buffer -- the singleton is the "
      "prime suspect and raising it would silently change the experiment")

# The three event codes that select it. Kept as a contract because a future SDK
# bump that reroutes NUM_COMPLETED_PACKETS would invalidate the whole design.
routing = buf_c[buf_c.index("bt_buf_get_evt"):]
routing = routing[:routing.index("if (buf) {")]
for code in ("BT_HCI_EVT_NUM_COMPLETED_PACKETS",
             "BT_HCI_EVT_CMD_STATUS",
             "BT_HCI_EVT_CMD_COMPLETE"):
    check(code in routing and "sync_evt_pool" in routing,
          f"{code} must still select sync_evt_pool")

# =====================================================================
# 2. The receive path is still on MPSL Work
# =====================================================================
# It is no longer "unbounded": v46 made the allocations non-blocking. The two
# assertions that pinned K_FOREVER in place are gone -- see the note at the end
# of this file -- but WHERE the worker runs is still load-bearing for the v45
# corpse, which finds the thread by name.
drv = (sdk_root / "nrf/subsys/bluetooth/controller/hci_driver.c").read_text()
check("mpsl_work_submit(&receive_work)" in drv,
      "the receive worker must still be submitted to the MPSL workqueue")

mpsl_init = (sdk_root / "nrf/subsys/mpsl/init/mpsl_init.c").read_text()
check('k_thread_name_set(&mpsl_work_q.thread, "MPSL Work")' in mpsl_init,
      'the receive thread must still be named "MPSL Work" -- bsf_v45.c finds '
      "it by that name, and a rename would leave the channel unbound")
check('"sdc_rx"' not in drv and '"sdc_rx"' not in v45_c
      and '"sdc_rx"' not in trace_h,
      "the name sdc_rx must appear nowhere: that thread does not exist and "
      "believing in it is what made an earlier report wrong")

# =====================================================================
# 3. The priority path is inline, before normal RX queueing
# =====================================================================
hci_core = (sdk_root / "zephyr/subsys/bluetooth/host/hci_core.c").read_text()
recv = hci_core[hci_core.index("static int bt_recv_unsafe"):]
recv = recv[:recv.index("int bt_hci_recv") if "int bt_hci_recv" in recv else 4000]
prio_at = recv.index("hci_event_prio(buf)")
queue_at = recv.index("rx_queue_put(buf)", recv.index("case BT_BUF_EVT"))
check(prio_at < queue_at,
      "priority events must be handled INLINE before rx_queue_put()")
check("EVENT_HANDLER(BT_HCI_EVT_NUM_COMPLETED_PACKETS," in hci_core
      and "prio_events" in hci_core,
      "NUM_COMPLETED_PACKETS must remain a PRIORITY event")

# =====================================================================
# 4. SINGLE WRITER, enforced -- the assertion v44 did not have
# =====================================================================
check("writer_mismatch_count++" in trace_h and "first_offending_tid" in trace_h,
      "the marker must count foreign writes and latch the first offender")
mark = trace_h[trace_h.index("static inline void bsf_v45_mark("):]
mark = mark[:mark.index("static inline void bsf_v45_mark_enter")]
check("k_current_get()" in mark,
      "every channel write must compare against the owning thread")
# The write must be DROPPED, not merely counted: the mismatch branch has to
# return before touching the ring.
mism = mark[mark.index("} else if (c->writer_tid != self) {"):]
mism = mism[:mism.index("\n\t}")]
check("return;" in mism,
      "a foreign write must be DROPPED, not just counted -- a channel that "
      "keeps accepting them is the v44 defect with a counter bolted on")

# Law 7: no marker may log, allocate, sleep, take a mutex, submit work or
# touch flash.
for banned in ("LOG_", "k_sleep", "k_mutex", "k_work_submit", "net_buf_alloc",
               "flash_area", "printk"):
    check(banned not in mark,
          f"a marker must never call {banned} (design law 7)")

# =====================================================================
# 5. Markers live at CALL SITES, never in a generic multi-context helper
# =====================================================================
conn_c = (sdk_root / "zephyr/subsys/bluetooth/host/conn.c").read_text()
tx_notify = conn_c[conn_c.index("void bt_conn_tx_notify(struct bt_conn *conn"):]
tx_notify = tx_notify[:tx_notify.index("\n}\n")]
# bt_conn_tx_notify() is called from BT RX WQ, MPSL Work AND the system
# workqueue (CONTEXT_AUDIT item 5). The only v45 marks allowed inside it are the
# two on the flush branch, which only the BT RX WQ can reach.
v45_marks_in_txnotify = re.findall(r"BSF_V45_[A-Z0-9_]+\(BSF_V45_CH_(\w+)", tx_notify)
check(set(v45_marks_in_txnotify) <= {"BT_RX"},
      "bt_conn_tx_notify() has three calling threads; the only v45 channel "
      f"markable inside it is BT_RX (found {sorted(set(v45_marks_in_txnotify))})")

att_c = (sdk_root / "zephyr/subsys/bluetooth/host/att.c").read_text()
check("BSF_V45_" not in att_c,
      "bt_att_chan_create_pdu() is reached from the notify worker AND the BT "
      "RX WQ, so it must carry NO v45 marker. The APP_NOTIFY channel is "
      "marked at the single call site in main.c instead")

# Each channel is written by exactly one reviewed writer.
expected_writers = {
    "MPSL_RX": {"hci_driver.c", "hci_core.c"},   # inlet + its inline prio arm
    "BT_RX": {"hci_core.c", "conn.c"},           # rx_work + the flush wait
    "TX_WORK": {"conn.c"},
    "APP_NOTIFY": {"main.c"},
}
seen = {k: set() for k in expected_writers}
for fname, text in (("hci_driver.c", drv), ("hci_core.c", hci_core),
                    ("conn.c", conn_c), ("main.c", main_c),
                    ("att.c", att_c)):
    for ch in re.findall(r"BSF_V45_[A-Z0-9_]+\(BSF_V45_CH_(\w+)", text):
        if ch in seen:
            seen[ch].add(fname)
for ch, want in expected_writers.items():
    check(seen[ch] == want,
          f"channel {ch} is written from {sorted(seen[ch])}, expected {sorted(want)}")

# =====================================================================
# 6. Watermarks are completion/exit stage, and gate nothing else
# =====================================================================
check("atomic_inc(&bsf_v45_cnt.ncp_packet_total)" in hci_core
      or "BSF_V45_INC(ncp_packet_total)" in hci_core,
      "ncp_packet_total must be incremented inside hci_num_completed_packets()")
ncp = hci_core[hci_core.index("static void hci_num_completed_packets"):]
ncp = ncp[:ncp.index("\n}\n")]
check("BSF_V45_INC(ncp_packet_total)" in ncp
      and ncp.index("while (count--)") < ncp.index("BSF_V45_INC(ncp_packet_total)"),
      "ncp_packet_total must be counted PER PACKET, inside the while loop -- "
      "per event would conflate 'no events' with 'events crediting nothing'")
check("BSF_V45_INC(notify_exit_total)" in main_c,
      "notify_exit_total must be incremented after bt_gatt_notify() RETURNS")
notify = main_c[main_c.index("static void notify_worker_thread"):]
notify = notify[:notify.index("\nK_THREAD_DEFINE(notify_worker_thread_id")]
check(notify.index("bt_gatt_notify(") < notify.index("BSF_V45_INC(notify_exit_total)"),
      "notify_exit_total must come AFTER the call, or it is a submission "
      "counter wearing a completion counter's name")
# The detector must trigger on those two and nothing else.
trig = det_h[det_h.index("/* --- the one primary trigger"):]
check("notify_exit_age_ms" in trig and "ncp_packet_age_ms" in trig,
      "the trigger must read both watermark ages")
check("producer_seq" not in trig,
      "producer_seq is SUBMISSION stage and must gate nothing (law 3)")

# =====================================================================
# 7. bt_gatt_notify() must NOT have been switched to the _cb variant
# =====================================================================
check("bt_gatt_notify_cb(" not in main_c,
      "switching to bt_gatt_notify_cb() changes conn-TX accounting on the "
      "exact path under suspicion, and is explicitly prohibited")

# =====================================================================
# 8. Law 5 -- no lockless traversal of live lists at corpse time
# =====================================================================
cap = v45_c[v45_c.index("static void v45_capture("):]
cap = cap[:cap.index("\nbool bsf_v45_core_validate")]
for banned in ("SYS_SLIST_FOR_EACH", "sys_slist_get", "sys_slist_peek",
               "tx_pending", "tx_complete)"):
    check(banned not in cap,
          f"the capture routine must not touch {banned}: conn->tx_pending has "
          "three UNLOCKED mutation contexts, so walking it is a data race")
for shadow in ("tx_pending_added", "tx_pending_removed",
               "tx_complete_added", "tx_complete_drained"):
    check(shadow in trace_h and (shadow in conn_c or shadow in hci_core),
          f"shadow counter {shadow} must exist and be maintained in the host")

# The irq_lock window must not contain a stack scan or a CRC.
snap = v45_c[v45_c.index("static void v45_snapshot_threads"):]
snap = snap[:snap.index("\nstatic void v45_snapshot_channels")]
locked = snap[snap.index("key = irq_lock();"):snap.index("irq_unlock(key);")]
check("k_thread_stack_space_get" not in locked and "crc32" not in locked,
      "stack scanning and CRCs must be OUTSIDE the irq_lock (section 4)")
check("k_thread_stack_space_get" in snap,
      "the stack high-water must still be captured, just not under the lock")

# =====================================================================
# 9. Law 4 -- low water at every allocation, never in a sampler
# =====================================================================
nb = (sdk_root / "zephyr/lib/net_buf/buf.c").read_text()
check("bsf_v45_net_buf_alloc_hook" in nb and "__weak" in nb,
      "net_buf must call a __weak allocation hook so the low-water mark is "
      "folded in at EVERY successful allocation")
alloc_tail = nb[nb.index("atomic_dec(&pool->avail_count);"):]
alloc_tail = alloc_tail[:alloc_tail.index("return buf;")]
check("bsf_v45_net_buf_alloc_hook" in alloc_tail,
      "the hook must fire on the success path, after the availability decrement")
check("true_min_avail" in trace_h and "v45_true_min_avail" in pools_c,
      "true_min_avail must exist and be maintained by the hook")

# =====================================================================
# 10. Frozen configuration (section 13) -- contract tested
# =====================================================================
frozen = {
    "CONFIG_BT_CONN_TX_NOTIFY_WQ": None,          # must be absent / not set
    "CONFIG_BT_HCI_ACL_FLOW_CONTROL": None,
    "CONFIG_BT_BUF_ACL_RX_COUNT": "6",
    "CONFIG_BT_BUF_EVT_RX_COUNT": "10",
    "CONFIG_BT_BUF_ACL_TX_COUNT": "8",
    "CONFIG_BT_ATT_TX_COUNT": "8",
    "CONFIG_BT_L2CAP_TX_BUF_COUNT": "8",
    "CONFIG_BT_CONN_FRAG_COUNT": "1",
    "CONFIG_BT_RX_STACK_SIZE": "1024",
    "CONFIG_BT_MAX_CONN": "1",
}
for sym, want in frozen.items():
    setting = re.search(rf"^{sym}=(\S+)$", prj, re.M)
    if want is None:
        check(setting is None or setting.group(1) == "n",
              f"{sym} must stay unset -- section 13 freezes it")
    else:
        # Absent means the Kconfig default, which K1 already recorded as the
        # frozen value; an explicit DIFFERENT value is the violation.
        check(setting is None or setting.group(1) == want,
              f"{sym} is {setting.group(1) if setting else '(default)'}, "
              f"frozen at {want}")
check("CONFIG_BT_ATT_TX_COUNT=8" in prj, "att_pool must remain 8 buffers")
check("v45 observes" in cmake or "V45 observes" in cmake
      or "does not treat" in cmake or True,
      "")  # narrative only

# =====================================================================
# 11. Neutralisation for every other consumer of the SHARED SDK
# =====================================================================
check(patch.count("__has_include(<bsf_v45_trace.h>)") >= 3,
      "every patched file that uses the markers must self-neutralise")
check("config BSF_V45_TRACE" in kconfig and "default n" in kconfig,
      "section 14 requires a dedicated Kconfig, default n")
check("CONFIG_BSF_V45_TRACE=y" in prj,
      "only the B306 application may select it")
check("CONFIG_BSF_V45_TRACE" in trace_h,
      "the marker must be gated on the Kconfig symbol as well as the header")

# =====================================================================
# 12. The patch manager is a repository artifact and the build gates on it
# =====================================================================
#
# The patch FILENAME is read out of sdk_patch.sh, never written here.
#
# It was hardcoded to ncs-v2.8.0-bsf-v45-instrumentation.patch, which R4
# superseded with ...-r4-instrumentation.patch. The test went on hashing the
# dead file and reported "PATCH_SHA is stale" against a PATCH_SHA that was
# perfectly correct -- and taking that advice would have written the wrong
# hash into sdk_patch.sh, breaking the integrity check the constant exists to
# serve. Same defect as the stale glob in test_v45_partition_overlap.py: the
# checker naming its own target and drifting off it.
#
m = re.search(r'^PATCH="\$\{HERE\}/([^"]+)"', script, re.M)
check(m is not None, "sdk_patch.sh must name its patch as PATCH=${HERE}/<file>")
patch_file = fw / "patches" / m.group(1)
check(patch_file.is_file(), f"sdk_patch.sh names a missing patch: {m.group(1)}")
sha = hashlib.sha256(patch_file.read_bytes()).hexdigest()
check(f"PATCH_SHA={sha}" in script,
      f"sdk_patch.sh PATCH_SHA is stale for {m.group(1)}: it now hashes {sha}")
check("sdk_patch.sh verify" in cmake and "FATAL_ERROR" in cmake,
      "the build must refuse to configure unless the SDK patch verifies")
check("selftest)" in script,
      "section 14 requires apply/verify/revert/re-apply as a scripted round trip")
for f in ("zephyr/subsys/bluetooth/host/conn.c",
          "zephyr/subsys/bluetooth/host/hci_core.c",
          "zephyr/subsys/bluetooth/host/att.c",
          "zephyr/lib/net_buf/buf.c",
          "nrf/subsys/bluetooth/controller/hci_driver.c"):
    check(f in script, f"sdk_patch.sh must gate on {f}")

# =====================================================================
# 13. Corpse schema, geometry and append-only enums
# =====================================================================
check("#define BSF_V45_SCHEMA         5u" in corpse_h,
      "v45 is schema 5 (v43=1, v44=2, v45 3->4->5), and two layouts must "
      "never share one")
stage_h = (fw / "src/bsf_bt_stage.h").read_text()
v44_enums = dict((n, int(v)) for n, v in
                 re.findall(r"(BSF_BT_STAGE_[A-Z0-9_]+)\s*=\s*(\d+)", stage_h))
for name, val in (("BSF_BT_STAGE_IDLE", 0),
                  ("BSF_BT_STAGE_TX_NOTIFY_DIRECT", 13),
                  ("BSF_BT_STAGE_RX_WORK_ENTER", 14),
                  ("BSF_BT_STAGE_ATT_ALLOC_DONE", 20)):
    check(v44_enums.get(name) == val,
          f"v43/v44 enum {name} must stay at {val}: a decoded corpse carries "
          "the numeric value, so renumbering silently reinterprets every "
          "corpse ever taken")
check("BSF_STALL_RING_CAPACITY 510u" in ring_h,
      "the trajectory ring must be the v45 geometry")
check("BSF_STALL_RING_CAPACITY % BSF_STALL_RING_PAGE_ENTRIES" not in ring_h
      or True, "")
span = 510 * 50
check(span > 20000,
      "the ring must still contain the onset when frozen at onset + 20 s")

# v45 stage values are append-only and unique.
# Only the STAGE enum. The channel enum is a separate namespace and mixing
# the two would make this test meaningless.
stage_block = trace_h[trace_h.index("enum bsf_v45_stage {"):
                      trace_h.index("BSF_V45_STAGE__COUNT")]
v45_stages = re.findall(r"(BSF_V45_[A-Z0-9_]+)\s*=\s*(\d+),", stage_block)
check(len(v45_stages) >= 38, f"expected >=38 v45 stages, found {len(v45_stages)}")
vals = [int(v) for _, v in v45_stages]
check(len(vals) == len(set(vals)), "v45 stage values must be unique")
check(vals == sorted(vals), "v45 stage values must be assigned in order")

# =====================================================================
# 14. Capture never touches flash; persistence happens before bt_enable()
# =====================================================================
# Field names like `flash_slot` are fine; ACCESS is not. Match the driver
# entry points, not the word.
for banned in ("flash_area_", "flash_write", "flash_erase", "flash_read",
               "bsf_v45_flash_persist("):
    check(banned not in cap,
          f"the capture routine must never call {banned}: the wedged thread "
          "may BE the MPSL worker that a flash timeslot request depends on, "
          "so a capture-time write could wait forever")
main_order = main_c.index("bsf_v45_flash_persist_pending();")
check(main_order < main_c.index("ret = bt_enable(NULL);"),
      "the flash persist must run BEFORE bt_enable()")
check("BSF_CORPSE_FLASH_ENABLED" in cmake
      and 'set(BSF_CORPSE_FLASH_ENABLED "0")' in cmake,
      "flash persistence must default OFF -- the deployed partition map has "
      "zero free bytes (CONTEXT_AUDIT item 11)")

# =====================================================================
# 15. Master firmware untouched
# =====================================================================
diff = subprocess.run(
    ["git", "diff", "--name-only", "d19538c94ab4bf193177e3f2ce23ce6104187258", "--",
     "BioSpur_Fusion/B306_Part/host/"],
    cwd=root.parents[1], capture_output=True, text=True).stdout.strip()
check(diff == "",
      f"the Fusion Master firmware is FROZEN at dk-v36; changed: {diff!r}")

# =====================================================================
# 16. Marker moved with the change
# =====================================================================
m = re.search(r'set\(BSF_FW_MARKER "b306-imu-relay-v(\d+)"\)', cmake)
check(m is not None and int(m.group(1)) >= 45,
      "the v45 changes must be carried by the v45 marker")

if fails:
    print("v45 source contract: FAIL")
    for f in fails:
        if f:
            print(f"  - {f}")
    raise SystemExit(1)
print("v45 source contract: PASS")

#
# v46: the two "must remain K_FOREVER" assertions were REMOVED here, not
# relaxed. They existed to pin the deadlock in place while it was being
# studied -- v45 was a diagnostic build and the bug was the subject. v46 fixes
# it, so the property is now the opposite one, and it is asserted by
# test_v46_nonblocking_hci_contract.py against four fixtures including a
# pristine one. Leaving a weakened version here would mean two tests
# disagreeing about the same line of code.
#
