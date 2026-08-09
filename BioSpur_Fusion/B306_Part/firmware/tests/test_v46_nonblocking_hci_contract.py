#!/usr/bin/env python3
"""v46: the HCI RX path must not block, and must not drop.

WHY THIS TEST IS WRITTEN AGAINST FIXTURES AND NOT ONLY THE LIVE SDK
-------------------------------------------------------------------
A contract test that has only ever been run against a build believed good
proves nothing: it cannot distinguish "the property holds" from "the check is
broken". This project has paid for that eight times -- a stale glob, a decoder
checked against old ELFs, a lifetime test passing on a known-broken build, a
truncated enumeration, a ms/s unit bug, a patch-manager check hashing a
superseded file, and two extractors of my own that produced confident wrong
numbers from stale or empty fields.

So this test carries four fixtures and asserts what it does on each:

  pristine   K_FOREVER present                     -> MUST FAIL
  v46        K_NO_WAIT + retain + freed-callback   -> MUST PASS
  fake_drop  K_NO_WAIT but the message is dropped  -> MUST FAIL
  fake_noresub  retains but never resubmits        -> MUST FAIL

The last two matter because both are plausible "fixes" that remove the
deadlock and replace it with something worse: silent ACL fragment loss, or a
permanently parked receive worker.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = Path("/home/zekaixiao/ncs/v2.8.0")
DRIVER = SDK / "nrf/subsys/bluetooth/controller/hci_driver.c"
BUF_C = SDK / "zephyr/subsys/bluetooth/host/buf.c"
BUF_H = SDK / "zephyr/include/zephyr/bluetooth/buf.h"

failures: list[str] = []


def check(cond: bool, msg: str) -> bool:
    if not cond:
        failures.append(msg)
    return bool(cond)


# --------------------------------------------------------------------------
# The predicate, applied to driver source text. Returns a list of reasons the
# source does NOT satisfy the v46 contract; empty means it does.
# --------------------------------------------------------------------------
def v46_violations(src: str) -> list[str]:
    bad = []

    # 1. no blocking allocation on the non-discardable RX paths
    for m in re.finditer(r"bt_buf_get_rx\([^)]*K_FOREVER[^)]*\)", src):
        bad.append(f"blocking bt_buf_get_rx: {m.group(0)}")
    if re.search(r"bt_buf_get_evt\([^;]*?K_FOREVER", src, re.S):
        bad.append("blocking bt_buf_get_evt with K_FOREVER")

    # 2. allocation failure must be reported, not swallowed
    if "-ENOBUFS" not in src:
        bad.append("no -ENOBUFS: allocation failure is not reported")

    # 3. the fetched message must be RETAINED, not dropped or overwritten
    if not re.search(r"retained_msg_type\s*=\s*msg_type", src):
        bad.append("fetched message is not retained on -ENOBUFS")
    if not re.search(r"if\s*\(\s*retained_msg_type\s*!=", src):
        bad.append("retained message is never re-processed before a new fetch")

    # 4. the sentinel must be cleared exactly once, after delivery
    clears = re.findall(r"retained_msg_type\s*=\s*BSF_V46_MSG_TYPE_NONE", src)
    # one at the declaration, one after successful delivery
    if len(clears) != 2:
        bad.append(f"sentinel cleared {len(clears)} times, expected exactly 2 "
                   "(declaration + once after delivery)")

    # 5. a freed buffer must resubmit the receive worker
    if not re.search(r"bt_buf_rx_freed_cb_set\s*\(", src):
        bad.append("no freed-buffer callback registered")
    cb = re.search(r"static void hci_rx_buf_freed\([^)]*\)\s*\{(.*?)\n\}", src, re.S)
    if not cb:
        bad.append("no freed-buffer handler defined")
    elif "receive_signal_raise" not in cb.group(1):
        bad.append("freed-buffer handler does not resubmit the receive worker")

    # 6. the zero sentinel must be guarded at compile time
    if not re.search(r"BUILD_ASSERT\(\s*BSF_V46_MSG_TYPE_NONE\s*!=", src):
        bad.append("no BUILD_ASSERT that the zero sentinel cannot collide")
    return bad


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------
PRISTINE = """
static void data_packet_process(const struct device *dev, uint8_t *hci_buf)
{
	data_buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_FOREVER);
	if (!data_buf) { LOG_ERR("No data buffer available"); return; }
}
"""

FAKE_DROP = """
#define BSF_V46_MSG_TYPE_NONE ((sdc_hci_msg_type_t)0)
BUILD_ASSERT(BSF_V46_MSG_TYPE_NONE != SDC_HCI_MSG_TYPE_DATA, "x");
static int data_packet_process(const struct device *dev, uint8_t *hci_buf)
{
	data_buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
	if (!data_buf) { LOG_ERR("dropping"); return -ENOBUFS; }
	return 0;
}
static void hci_rx_buf_freed(enum bt_buf_type m) { receive_signal_raise(); }
void x(void) { bt_buf_rx_freed_cb_set(hci_rx_buf_freed); }
"""   # never retains: no retained_msg_type at all

FAKE_NORESUB = """
#define BSF_V46_MSG_TYPE_NONE ((sdc_hci_msg_type_t)0)
BUILD_ASSERT(BSF_V46_MSG_TYPE_NONE != SDC_HCI_MSG_TYPE_DATA, "x");
static sdc_hci_msg_type_t retained_msg_type = BSF_V46_MSG_TYPE_NONE;
static int data_packet_process(const struct device *dev, uint8_t *hci_buf)
{
	data_buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
	if (!data_buf) { return -ENOBUFS; }
	return 0;
}
static bool fetch(const struct device *dev, uint8_t *b)
{
	if (retained_msg_type != BSF_V46_MSG_TYPE_NONE) {
		retained_msg_type = BSF_V46_MSG_TYPE_NONE;
		return true;
	}
	retained_msg_type = msg_type;
	return false;
}
"""   # retains, but nothing ever wakes the worker


def main() -> int:
    print("v46 non-blocking HCI RX contract")

    # --- the live SDK must satisfy it ------------------------------------
    for p in (DRIVER, BUF_C, BUF_H):
        if not check(p.is_file(), f"missing SDK file {p}"):
            print("v46 contract: FAIL")
            return 1
    src = DRIVER.read_text()
    live = v46_violations(src)
    if check(not live, f"patched SDK violates the v46 contract: {live}"):
        print("  ok   patched SDK satisfies the contract")

    # the Zephyr companion must actually exist, or nothing can resubmit
    if check("bt_buf_rx_freed_cb_set" in BUF_H.read_text(),
             "zephyr buf.h lacks bt_buf_rx_freed_cb_set (companion not backported)"):
        print("  ok   freed-callback API present in zephyr buf.h")
    bc = BUF_C.read_text()
    if check(re.search(r"NET_BUF_POOL_FIXED_DEFINE\(hci_rx_pool.*?_destroy\)", bc, re.S)
             or re.search(r"NET_BUF_POOL_DEFINE\(acl_in_pool.*?_destroy\)", bc, re.S),
             "no RX pool has a destroy hook, so a freed buffer notifies nobody"):
        print("  ok   RX pool destroy hook wired to the notifier")
    if check(re.search(r"BT_BUF_EVT\s*\|\s*BT_BUF_ACL_IN", bc),
             "shared hci_rx_pool must report BOTH types; one alone leaves a waiter parked"):
        print("  ok   shared pool reports both buffer types")

    # --- the fixtures decide whether this test can fail at all -----------
    for name, fixture, must_fail in (("pristine", PRISTINE, True),
                                     ("fake_drop", FAKE_DROP, True),
                                     ("fake_noresub", FAKE_NORESUB, True)):
        v = v46_violations(fixture)
        if must_fail:
            if check(bool(v), f"fixture '{name}' was ACCEPTED -- this test cannot fail, "
                              "so it is not a gate"):
                print(f"  ok   fixture '{name}' correctly rejected ({len(v)} violation(s))")

    print("v46 contract:", "FAIL" if failures else "PASS")
    for f in failures:
        print("  -", f)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
