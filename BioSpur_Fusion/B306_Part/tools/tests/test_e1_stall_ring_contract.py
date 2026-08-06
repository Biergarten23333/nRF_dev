#!/usr/bin/env python3
"""Source contract for the trajectory ring (b306-imu-relay-v42, E1 + E2 + H1).

Guards the properties that are easy to break silently in a later round and that
the native policy test cannot see, because they live in main.c, in the build
files, or in the relationship between them.
"""
from pathlib import Path

root = Path(__file__).resolve().parents[2]
fw = (root / "firmware/src/main.c").read_text()
ring = (root / "firmware/src/stall_ring_policy.h").read_text()
hdr = (root / "include/biospur_fusion_ble.h").read_text()
cmake = (root / "firmware/CMakeLists.txt").read_text()
version = (root / "firmware/VERSION").read_text()

import re
# Comment-stripped view: the forbidden-word checks below are about what the
# code can *do*, and the comments legitimately discuss recovery and reboots.
ring_code = re.sub(r"/\*.*?\*/", "", ring, flags=re.S)

# --- the sampling context must not be a thread
assert "K_TIMER_DEFINE(stall_ring_timer, stall_ring_sample, NULL);" in fw, \
    "the ring must sample from a k_timer expiry, not a workqueue or thread"
assert "k_timer_start(&stall_ring_timer" in fw
sampler_reset_region = fw[fw.index("static void stall_ring_sample("):
                          fw.index("K_TIMER_DEFINE(stall_ring_timer")]
# The blocking-call scan stops before the reset block: sys_reboot() is the one
# thing the ISR is now allowed to do, and it neither blocks nor schedules.
sampler = sampler_reset_region[:sampler_reset_region.index("if (reset_now) {")]
# nothing in the sampler may block, allocate, log or feed the watchdog
for forbidden in ("K_FOREVER", "k_sleep", "k_msleep", "k_sem_take", "k_mutex_lock",
                  "k_malloc", "malloc(", "net_buf_alloc", "LOG_", "printk",
                  "watchdog_feed", "k_work_submit", "k_queue"):
    assert forbidden not in sampler, \
        f"the ISR sampler must not use {forbidden}"
assert "k_spin_lock(&stall_ring_lock)" in sampler and \
       "k_spin_unlock(&stall_ring_lock" in sampler, \
    "the ring write must be spinlock-brief"

# the watchdog must still have exactly one feed site, and it must not be ours
assert fw.count("watchdog_feed_once") == 2, \
    "watchdog_feed_once must be defined once and called from exactly one site"
# Two wdt_feed sites: the one-shot feed inside watchdog_start(), and the
# wrapper. Only the wrapper is periodic, which is what D1's argument rests on.
assert fw.count("wdt_feed(") == 2, "no new periodic watchdog feed may appear"
assert "int watchdog_ret = watchdog_feed_once();" in fw, \
    "the single feed site is still the head of telemetry_work_handler"

# --- pool sampling must not steal the kind-8 low-water window
assert "static uint8_t sample_pool_available(uint8_t *out)" in fw
avail = fw[fw.index("static uint8_t sample_pool_available("):
           fw.index("static void stall_ring_sample(")]
assert "pool_low_water" not in avail, \
    "the 20 Hz sampler must not touch the 1 Hz low-water window"
assert "atomic_set(&pool_low_water[count]" in fw, \
    "the v39 windowed low_water fix must still be present"

# --- retained across the reset, and never re-armed implicitly
assert '__attribute__((section(".noinit"))) static struct bsf_stall_ring stall_ring;' in fw
assert "stall_ring_boot_result = (uint8_t)bsf_stall_ring_boot(&stall_ring);" in fw
assert "ring->boot_id++;" in ring
boot = ring[ring.index("bsf_stall_ring_boot(struct bsf_stall_ring *ring)"):
            ring.index("/* First freeze wins")]
assert "bsf_stall_ring_clear" not in boot, \
    "a surviving ring must not be re-armed at boot; that would erase the evidence"

# --- freeze is latched before recovery is armed
alarm = fw[fw.index("if (decision.fire &&"):fw.index("if (watchdog_ret != 0)")]
assert "stall_ring_latch(BSF_RING_FREEZE_ALARM" in alarm
assert alarm.index("stall_ring_latch(BSF_RING_FREEZE_ALARM") < \
       alarm.index("atomic_set(&stall_recovery_pending, 1)"), \
    "latch the trajectory before the reboot that would otherwise overwrite it"

# --- E2/F1: the ring shares the v37 disconnect retraction
retract = fw[fw.index("static void disconnected("):fw.index("static void connected(")]
assert "bsf_stall_ring_retract_disconnect(" in retract, \
    "E2/F1: the ring must share the v37 disconnect retraction"
assert retract.index("bsf_stall_ring_retract_disconnect(") > \
       retract.index("bsf_stall_detector_retract_disconnect("), \
    "the ring retraction consumes the detector's verdict, so it must run after it"
assert "alarm_retracted" in retract
# it must run on EVERY disconnect, not only inside the alarm-retracted branch:
# the no_exit backstop has no alarm to be retracted with.
_body = retract[retract.index("bsf_stall_ring_retract_disconnect("):]
assert "if (alarm_retracted) {" not in _body

# E2/F1: the backstop freezes a RAM buffer and can do nothing else.
freeze = ring_code[ring_code.index("bsf_stall_ring_freeze(struct bsf_stall_ring *ring,"):
                   ring_code.index("bsf_stall_ring_push(struct bsf_stall_ring *ring,")]
for forbidden in ("recovery", "reboot", "reset", "disconnect"):
    assert forbidden not in freeze, \
        f"the freeze path must not be able to {forbidden} anything"
push = ring_code[ring_code.index("bsf_stall_ring_push(struct bsf_stall_ring *ring,"):
                 ring_code.index("bsf_stall_ring_take_reset(")]
assert "bsf_stall_ring_freeze(ring, BSF_RING_FREEZE_NO_EXIT" in push

# --- H1: DELIBERATELY OVERTURNED, not eroded.
#
# E2 asserted here that `sys_reboot` and `stall_recovery` appear NOWHERE in the
# ring policy. That assertion was correct when it was written: it guarded
# against v36's spurious reboots, and a ring backstop had no business rebooting
# anything.
#
# N6 changed the facts. The stall on BSF44AD showed the ring is unreachable on a
# live stalled board by EITHER path -- `RING PAGE=n` + read needs the GATT read,
# dead from onset + 0.0 s, and `RING STATUS` answers over the control-reply
# plane, which is the stalled publisher. The retrieval window exists only after
# a reboot, and N6 showed no path to one: the detector did not fire, RECONNECT
# removed the board permanently, and a brownout takes .noinit with it.
#
# So the backstop may now reset -- and ONLY on these terms, which is what the
# assertions below pin.
assert "stall_recovery" not in ring_code, \
    "the ring policy still may not touch the detector's recovery path"
assert ring_code.count("reset_pending = 1u") == 1, \
    "exactly one place may ever owe a reset"

# 1. the freeze strictly precedes the request, in the same statement
assert "bsf_stall_ring_freeze(ring, BSF_RING_FREEZE_NO_EXIT" in push and \
       push.index("bsf_stall_ring_freeze(ring, BSF_RING_FREEZE_NO_EXIT") < \
       push.index("reset_pending = 1u"), \
    "the reset may only be owed AFTER the freeze that preserves the evidence"

# 2. bounded to one per power cycle, and the bound is retained across the reset
assert "#define BSF_STALL_RING_MAX_ISR_RESETS 1u" in ring
take = ring_code[ring_code.index("bsf_stall_ring_take_reset("):]
assert "isr_resets >= BSF_STALL_RING_MAX_ISR_RESETS" in take
assert "ring->frozen == 0u" in take, \
    "take_reset must refuse to reset a ring that holds no evidence"
clear = ring_code[ring_code.index("bsf_stall_ring_clear(struct bsf_stall_ring *ring)"):
                  ring_code.index("bsf_stall_ring_consistent(")]
assert "isr_resets" not in clear, \
    "RING CLEAR must not refund the reset budget, or a flap becomes a loop"

# 3. only the ISR sampler may act on it, and only via sys_reboot
assert fw.count("bsf_stall_ring_take_reset(") == 1, \
    "exactly one caller may claim a reset"
assert "reset_now = bsf_stall_ring_take_reset(&stall_ring);" in sampler_reset_region, \
    "the claim belongs in the k_timer ISR, the context that survives the stall"

# --- the dwell, arming and existing recovery bound are untouched by H1
assert "#define STALL_DETECT_MS 5000u" in fw
assert "#define STALL_ARM_NOTIFY_OK 64u" in fw
assert "#define STALL_MAX_RECOVERIES_PER_POWER 1u" in fw
assert "#define BSF_STALL_RING_NO_EXIT_SAMPLES 120u" in ring

# E2/F2: a retained ring must validate its geometry and every index invariant.
assert "static inline bool bsf_stall_ring_consistent(" in ring
for invariant in ("ring->count > BSF_STALL_RING_CAPACITY",
                  "ring->head >= BSF_STALL_RING_CAPACITY",
                  "ring->freeze_index > ring->count",
                  "ring->frozen > 1u", "ring->primed > 1u",
                  "ring->freeze_reason > BSF_RING_FREEZE_MANUAL"):
    assert invariant in ring, invariant
for stamp in ("ring->capacity != BSF_STALL_RING_CAPACITY",
              "ring->entry_size != sizeof(bsf_stall_ring_entry_t)",
              "ring->period_ms != BSF_STALL_RING_PERIOD_MS"):
    assert stamp in ring, stamp
assert "BSF_RING_BOOT_GEOMETRY" in ring and "BSF_RING_BOOT_INVALID" in ring
# every rejection path must wipe, so nothing can render
boot = ring[ring.index("bsf_stall_ring_boot(struct bsf_stall_ring *ring)"):
            ring.index("/* First freeze wins")]
assert "memset(ring, 0, sizeof(*ring));" in boot, \
    "a rejected ring must be wiped, not left renderable"
assert "bsf_stall_ring_boot_name(stall_ring_boot_result)" in fw, \
    "the boot verdict must be reported in the banner and in RING STATUS"
assert fw.count("bsf_stall_ring_boot_name(stall_ring_boot_result)") == 2

# --- retrieval: never an ATT transaction that can run 30 s, never wedges
assert '"RING PAGE=' in fw and '"RING PAGE OFF"' in fw and '"RING CLEAR"' in fw \
    and '"RING FREEZE"' in fw and '"RING STATUS"' in fw
view = ring[ring.index("static inline bool bsf_stall_ring_view_page("):]
assert "now_ms - view->selected_uptime_ms" in view, \
    "the selection must expire by comparison at read time, not by a scheduled work item"
for forbidden in ("k_work", "k_timer", "k_delayed", "k_sem", "k_mutex",
                  "malloc", "printf"):
    assert forbidden not in ring_code, \
        f"the ring policy must schedule and allocate nothing ({forbidden})"
render = ring[ring.index("static inline int bsf_stall_ring_render_page("):
              ring.index("static inline void bsf_stall_ring_view_select(")]
for mutation in ("ring->head", "ring->count =", "ring->writes_total",
                 "ring->frozen ="):
    assert mutation not in render, \
        "rendering a page must not advance any state -- that is what makes it idempotent"

# --- both wire forms are the same length, and the old one is untouched
assert "#define BSF_STALL_RING_VERSION 4u" in hdr
# v3 must stay decodable: a v41 board (BSF44AD) may still be carrying a frozen
# ring that only becomes readable if it ever reboots and rejoins.
assert "#define BSF_STALL_RING_VERSION_V41 3u" in hdr
assert "RING_VERSION_V41" in (root / "tools/stall_ring_decode.py").read_text() or \
       "3:" in (root / "tools/stall_ring_decode.py").read_text(), \
    "the host decoder must still handle v3 pages"
assert "#define BSF_STALL_STATUS_VERSION 2u" in hdr
assert "_Static_assert(sizeof(bsf_stall_ring_page_t) == 232u," in hdr
assert "_Static_assert(sizeof(bsf_stall_ring_page_t) == sizeof(bsf_stall_status_t)," in hdr
assert "_Static_assert(sizeof(bsf_ble_pool_usage_t) == 140u," in hdr, \
    "kind-8 payload size must not drift"
assert "_Static_assert(sizeof(bsf_ble_telemetry_t) == 243u," in hdr
assert "_Static_assert(sizeof(bsf_ble_queue_counters_t) == 58u," in hdr

# --- geometry is derived from the detector dwell, not picked at random
assert "#define BSF_STALL_RING_CAPACITY 200u" in ring
assert "#define BSF_STALL_RING_PERIOD_MS 50u" in ring
assert "#define STALL_DETECT_MS 5000u" in fw, \
    "the ring span was sized against this dwell; changing it must fail here"
assert "#define STALL_RECOVERY_RETRACT_MS 1500u" in fw
assert "#define BSF_STALL_RING_NO_EXIT_SAMPLES 120u" in ring

# --- carried forward from the current canonical image
assert "#define STALL_ARM_NOTIFY_OK 64u" in fw
assert "RETAINED_STALL_MAGIC 0x56333852u" in fw
assert "retained_stall.first_snapshot" in fw

# --- markers advanced together
assert 'set(BSF_FW_MARKER "b306-imu-relay-v42")' in cmake
assert "VERSION_PATCHLEVEL = 42" in version, \
    "v39 shipped with patchlevel 38; the image header and the marker are realigned here"
port = (root / "tools/confirm_b306_v42.py").read_text()
assert 'B306_MARKER = "b306-imu-relay-v42"' in port
assert 'MASTER_MARKER = "dk-fusion-imu-relay-v33"' in port

print("E1 stall-ring contract: PASS")
