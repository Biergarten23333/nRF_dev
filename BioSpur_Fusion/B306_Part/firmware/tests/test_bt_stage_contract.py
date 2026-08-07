#!/usr/bin/env python3
"""Source contract for the v43 BT RX stage instrumentation.

WHY EACH ASSERTION IS HERE
--------------------------
These are not style checks. Each one pins a property that something already got
wrong, or that a later tidy-up would silently invalidate.

The expensive one is the terminal-stage list. A stage that can be the LAST mark
on a healthy path but is not listed as quiescent makes the monitor fire on an
idle board. That is not hypothetical: DEFERRED_RESCHEDULE_AFTER was missing, the
monitor fired on a perfectly healthy canary during the DK restore window,
rebooted an image that had not yet earned its MCUboot confirmation, and MCUboot
reverted it to v41. Two OTA attempts were spent before it was diagnosed. Adding
a stage to the enum without deciding whether it can be terminal reintroduces it.
"""
import hashlib
import re
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
hdr = (root / "firmware/src/bsf_bt_stage.h").read_text()
main_c = (root / "firmware/src/main.c").read_text()
cmake = (root / "firmware/CMakeLists.txt").read_text()
patch = (root / "firmware/patches/ncs-v2.8.0-bt-conn-stage-trace.patch").read_text()
script = (root / "firmware/patches/host_patch.sh").read_text()

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)


# --- 1. the terminal-stage list ------------------------------------------
stages = dict(re.findall(r"BSF_BT_STAGE_([A-Z_]+)\s*=\s*(\d+)", hdr))
check(len(stages) >= 21, f"expected >=21 stages (v44 appended 7), found {len(stages)}")

quiescent = set(re.findall(r"\(s\) == BSF_BT_STAGE_([A-Z_]+)",
                           hdr[hdr.index("#define BSF_BT_STAGE_IS_QUIESCENT"):
                               hdr.index("/* Event codes")]))
# Every stage whose name marks the END of an operation must be terminal, and
# every stage that is mid-operation must not be. EXIT/AFTER names that are
# immediately followed by another mark on the healthy path are the exceptions,
# and they are listed explicitly so adding one is a deliberate act.
# v44. The terminal set is now PROVABLE rather than enumerated:
# rx_work_handler() is the single entry point for everything the BT RX WQ does,
# and v44 brackets it, so quiescent <=> not inside rx_work_handler().
#
# WHAT FAILED IN v43 WAS NOT THIS TEST'S STRUCTURE. The "every stage must be
# classified" rule below worked and did force a decision. What failed was THE
# DECISION, made by NAME instead of by which call sites can leave the stage as
# the last mark. TX_NOTIFY_EXIT *sounds* terminal, and it genuinely is for
# bt_conn_set_state() and for hci_num_completed_packets() -- but NOT for
# bt_conn_recv(), which calls tx_notify at its head and then descends into an
# unmarked ATT stack. A real wedge on BSF6C53 sat there for ten minutes and was
# never captured.
#
# Classify by call site. Never by name.
must_be_quiescent = {"IDLE", "RX_WORK_EXIT"}
must_not_be = {"CONN_RECV_ENTER", "TX_NOTIFY_ENTER", "TX_NOTIFY_BEFORE_SUBMIT",
               "TX_NOTIFY_BEFORE_FLUSH", "RESET_RX_BEFORE",
               "DEFERRED_RESCHEDULE_BEFORE",
               # v44: every one of these is now followed by RX_WORK_EXIT
               "TX_NOTIFY_EXIT", "CONN_RECV_EXIT", "DEFERRED_RESCHEDULE_AFTER",
               "RX_WORK_ENTER", "ACL_RECV_ENTER", "ACL_RECV_EXIT",
               "ATT_ALLOC_RESPONSE", "ATT_ALLOC_FOREVER", "ATT_ALLOC_DONE"}
check(must_be_quiescent <= quiescent,
      f"terminal stages missing from the quiescent list: "
      f"{sorted(must_be_quiescent - quiescent)} -- the monitor will fire on an "
      f"idle board and reboot an unconfirmed image")
check(not (must_not_be & quiescent),
      f"mid-operation stages wrongly marked quiescent: "
      f"{sorted(must_not_be & quiescent)} -- the monitor can no longer see a "
      f"wedge in them")
# Any NEW stage must be classified one way or the other, deliberately.
classified = must_be_quiescent | must_not_be | {
    "TX_NOTIFY_AFTER_SUBMIT", "TX_NOTIFY_AFTER_FLUSH", "RESET_RX_AFTER",
    "TX_NOTIFY_DIRECT", "_COUNT"}
unclassified = set(stages) - classified
check(not unclassified,
      f"new stage(s) {sorted(unclassified)} added without deciding whether they "
      f"can be the last mark on a healthy path -- classify them in this test")

# --- 2. the hot path may only write RAM ----------------------------------
marker = hdr[hdr.index("static inline void bsf_bt_stage_mark"):
             hdr.index("#define BSF_BT_STAGE(s)")]
for banned in ("LOG_", "printk", "k_mutex", "k_sem", "k_work", "k_sleep",
               "malloc", "net_buf_alloc", "flash", "k_spin"):
    check(banned not in marker,
          f"bsf_bt_stage_mark() must only write RAM, found `{banned}` -- the "
          f"instrumented build must behave like v41")
check("bsf_bt_stage_seq++" in marker and
      marker.index("bsf_bt_stage_id =") < marker.index("bsf_bt_stage_seq++"),
      "the sequence counter must be published LAST, after the stage, or a "
      "reader that sees a new seq can still read the previous stage")

# --- 3. the monitor is on neither of the two suspect queues --------------
mon = main_c[main_c.index("static void bsf_bt_monitor("):
             main_c.index("K_THREAD_DEFINE(bt_monitor_thread_id")]
check("K_THREAD_DEFINE(bt_monitor_thread_id" in main_c,
      "the monitor must be its own thread, not a work item -- a work item on "
      "either suspect queue shares fate with what it is monitoring")
check("k_work" not in mon,
      "the monitor must not submit or wait on work items")
check("notify_ok" not in mon and "data_subscribed" not in mon,
      "the monitor must NOT trigger on notifications stopping -- that readmits "
      "the producer, RF, scheduling, the central and the application, all of "
      "which are already excluded and every one a false-positive source")

# --- 4. ONE reboot budget, shared, with stated precedence ----------------
check(main_c.count("bsf_reboot_budget_take(") >= 3,
      "both reset authorities must draw from the shared budget")
check("BSF_REBOOT_OWNER_BTRX" in main_c and "BSF_REBOOT_OWNER_RING" in main_c,
      "each authority must identify itself in the budget, or a corpse cannot "
      "say which one spent it")
check("reset_now && !bsf_reboot_budget_take" in main_c,
      "the ring ISR must yield to the shared budget, not reboot unilaterally")

# --- 5. capture happens BEFORE recovery ----------------------------------
check(mon.index("bsf_capture_corpse(") < mon.index("bsf_reboot_budget_take("),
      "the corpse must be captured before the budget is claimed -- losing the "
      "reset must not also lose the evidence")

# --- 6. validity is never assumed ----------------------------------------
val = main_c[main_c.index("static bool bsf_corpse_validate"):
             main_c.index("static void bsf_corpse_invalidate")]
for need in ("magic", "schema", "length", "crc32"):
    check(need in val, f"corpse validation must check {need} -- .noinit does "
                       f"not survive power-on, so cold-boot garbage can look "
                       f"entirely plausible")
cap = main_c[main_c.index("static void bsf_capture_corpse"):
             main_c.index("static bool bsf_corpse_validate")]
check(cap.rindex("c->valid = BSF_CORPSE_MAGIC") > cap.index("c->crc32 ="),
      "the valid flag must be written after the CRC, or a reset mid-capture "
      "leaves a record that passes validation with half its fields unset")
check("CORPSE ACK" in main_c and "cleared=1" in main_c,
      "only a positive ACK may clear the valid marker")

# --- 7. no runtime flash was added for diagnostics -----------------------
for banned in ("nvs_", "settings_save", "flash_area_write", "flash_write"):
    check(banned not in cap,
          f"the corpse path must not touch flash (`{banned}`): flash x system "
          f"workqueue x bt_conn_tx_notify is still in the suspicion tree")

# --- 8. the frozen Kconfig set (brief section 2) -------------------------
prj = (root / "firmware/prj.conf").read_text()
for banned in ("CONFIG_BT_CONN_TX_NOTIFY_WQ=y", "CONFIG_BT_HCI_ACL_FLOW_CONTROL=y"):
    check(banned not in prj,
          f"{banned} is Nordic's workaround for the very dependency under "
          f"investigation; enabling it would hide the fault before it is recorded")
check("CONFIG_BT_MAX_CONN=1" in prj, "BT_MAX_CONN must stay 1")
check("CONFIG_BT_CONN_FRAG_COUNT" not in prj, "BT_CONN_FRAG_COUNT stays at its default of 1")

# --- 9. the host patch is a repository artifact and the build gates on it -
check("host_patch.sh verify" in cmake and "FATAL_ERROR" in cmake,
      "the build must refuse to configure unless the host patch verifies")
check("zephyr_include_directories" in cmake,
      "conn.c is compiled into a Zephyr library and resolves the header "
      "through zephyr_interface, not the app's private include path")
check(patch.count("__has_include(<bsf_bt_stage.h>)") >= 3,
      "every patched file must self-neutralise for other projects built "
      "against this SHARED SDK -- v44 touches conn.c, hci_core.c and att.c")
sha = hashlib.sha256((root / "firmware/patches/"
                      "ncs-v2.8.0-bt-conn-stage-trace.patch").read_bytes()).hexdigest()
check(f"PATCH_SHA={sha}" in script,
      f"host_patch.sh PATCH_SHA is stale: patch now hashes {sha}")

# --- 10. marker moved with the change ------------------------------------
m = re.search(r'set\(BSF_FW_MARKER "b306-imu-relay-v(\d+)"\)', cmake)
check(m is not None and int(m.group(1)) >= 43,
      "the v43 changes must be carried by the current marker")

if fails:
    print("BT stage contract: FAIL")
    for f in fails:
        print(f"  - {f}")
    raise SystemExit(1)
print("BT stage contract: PASS")
