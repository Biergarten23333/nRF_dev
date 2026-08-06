#!/usr/bin/env python3
"""F1 — the Fusion Master must never block a thread on host output.

WHY THIS TEST EXISTS

A peripheral's `bt_gatt_notify()` blocks only when its TX credits stop coming
back, and credits come back from the LOCAL controller's HCI
Number-Of-Completed-Packets event, which fires when the PEER'S CONTROLLER
acknowledges at the link layer (zephyr/subsys/bluetooth/host/hci_core.c:555,
`k_sem_give(bt_conn_get_pkts(conn))`). The peer's *host* is not in that path.

So the only way the Master can back-pressure ten peripherals at once is to stop
consuming HCI RX itself, which requires one of its own threads -- above all the
BT RX thread that runs the notification callbacks -- to block. If that ever
happened, every connected B306 would stop completing notifies simultaneously,
the 5000 ms dwell would fire on all of them, and because the link is healthy no
disconnect would arrive to trigger the 1500 ms retraction: **ten boards would
reboot at once for a reason that has nothing to do with them.**

Today that cannot happen, because every stage of the Master's output path drops
instead of waiting. This test pins each of those stages. Every assertion here is
load-bearing: turning any one of them into a blocking call re-creates the
fleet-wide reboot exposure, and it would look like a tidy-up.

See B306_Part/logs/dwell_cdc_exposure_20260806/DWELL_CDC_EXPOSURE.md.
"""
import re
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = (root / "src/main.c").read_text()
prj = (root / "prj.conf").read_text()

BLOCKING = ("K_FOREVER", "k_sleep(", "k_msleep(", "k_mutex_lock(", "k_sem_take(",
            "k_malloc", "uart_poll_out", "k_queue_get", "k_fifo_get",
            "k_condvar_wait", "k_thread_join", "k_pipe_get")


def body_of(pattern):
    """Return the full text of the first function whose signature matches."""
    m = re.search(pattern, src, re.M)
    assert m is not None, f"function not found: {pattern}"
    i = src.index("{", m.start())
    depth, j = 0, i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1


# --- stage 1: the BT RX thread. The notification callbacks must never wait.
callbacks = sorted(set(re.findall(r"\.notify\s*=\s*(\w+)", src)))
assert callbacks == ["data_notification", "telemetry_notification"], callbacks
for name in callbacks:
    body = body_of(r"^static uint8_t " + name + r"\(")
    found = [k for k in BLOCKING if k in body]
    assert not found, f"{name} runs on the BT RX thread and must not block: {found}"
    puts = re.findall(r"k_msgq_put\([^;]*?\)", body)
    assert puts, f"{name} must hand off to the logger queue"
    for put in puts:
        assert "K_NO_WAIT" in put, \
            f"{name} must DROP when the logger queue is full, never wait: {put}"
assert "++peer->logger_dropped" in src or "peer->logger_dropped++" in src, \
    "a dropped record must be counted, or the drop is invisible"

# --- stage 2: the CDC ring. Bounded, drops, counts, never waits.
cdc = body_of(r"^static void cdc_queue_record\(")
found = [k for k in BLOCKING if k in cdc]
assert not found, f"cdc_queue_record must not block: {found}"
assert "ring_buf_space_get(&cdc_tx_ring)" in cdc, \
    "the CDC path must check for space rather than wait for it"
assert "cdc_dropped_bytes" in cdc and "cdc_dropped_records" in cdc, \
    "a full CDC ring must drop and count, not stall the caller"
assert "irq_lock()" in cdc and "irq_unlock(" in cdc, \
    "the ring update is IRQ-brief, which is what makes it safe to call anywhere"

# --- stage 3: the console. RTT must skip rather than spin when unattended.
assert "CONFIG_SEGGER_RTT_MODE_NO_BLOCK_SKIP=y" in prj, \
    "a blocking RTT backend would stall whatever thread called printk"
assert "CONFIG_UART_CONSOLE=n" in prj, \
    "a UART console would reintroduce a blocking poll_out on the print path"
assert "CONFIG_RTT_CONSOLE=y" in prj

# --- and every printk on the Master goes through the non-blocking wrapper
assert "#define printk(...) fusion_printf(__VA_ARGS__)" in src
fp = body_of(r"^static void fusion_printf\(")
found = [k for k in BLOCKING if k in fp]
assert not found, f"fusion_printf must not block: {found}"

# --- the logger thread is preemptible, so it can never starve the BT RX thread
m = re.search(r"K_THREAD_DEFINE\(fusion_logger,\s*\d+,\s*fusion_log_thread,"
              r"\s*NULL,\s*NULL,\s*NULL,\s*(-?\d+)", src)
assert m is not None, "the logger thread definition moved"
assert int(m.group(1)) >= 0, \
    "the logger must be preemptible; a cooperative logger could starve BT RX"

# --- the drop counters must stay on the wire, so a regression is observable
for field in ("logger_drop", "cdc_drop_bytes", "cdc_drop_records"):
    assert field in src, f"{field} must remain reportable to the host"

print("Fusion Master host-output non-blocking contract: PASS")
sys.exit(0)
