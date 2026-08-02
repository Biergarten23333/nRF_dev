#!/usr/bin/env python3
"""Static scope and sequencing checks for relay8.2."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SS = (ROOT / "src/ss_twr_init.c").read_text(encoding="utf-8")
TRACKER = (ROOT / "include/tag_relay8_2.h").read_text(encoding="utf-8")
FRAME = (ROOT / "include/biospur_link.h").read_bytes()
BASE_FRAME = (
    ROOT.parents[1] / "relay8_1-workspace/src/include/biospur_link.h"
).read_bytes()

assert FRAME == BASE_FRAME, "96-byte UART frame changed"
assert "dwt_setdelayedtrxtime((uint32_t)(window_start >> 8));" in SS
assert "dwt_rxenable(DWT_START_RX_DELAYED)" in SS
assert "rx_arm_failures++" in SS

arm = SS.index("(void)ss_twr_init_beacon_arm_next_window();")
diagnostics = SS.index("if (cir_diag_pending)", arm)
publish = SS.index("ss_twr_init_alt_publish_rx_diag", diagnostics)
assert arm < diagnostics < publish, (
    "hardware RX must arm before diagnostic reads and formatting"
)

assignments = re.findall(r"ss_twr_init_sweep_count\s*=\s*0U", SS)
assert not assignments, "runtime/config source still resets public sweep"
assert "BSS initialization is the reboot-time reset" in SS

for token in (
    "struct tag_relay8_2_clock_tracker",
    "TAG_RELAY8_2_RATE_GAIN_SHIFT",
    "TAG_RELAY8_2_PHASE_OUTLIER_US",
    "TAG_RELAY8_2_WINDOW_MAX_EARLY_US",
):
    assert token in TRACKER

print("relay8.2 static contract: PASS")
