#!/usr/bin/env python3
"""Round-trip bound and parser dry run for the relay8.2 status extension."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FUSION = ROOT.parents[2]
TAG_SOURCE = ROOT / "apps/tag/src/uwb_tag_ble.c"
B306_HEADER = FUSION / "B306_Part/include/biospur_fusion_ble.h"
WIRE_HEADER = ROOT / "include/biospur_link.h"

CONTROL_LINE_MAX = 200
UART_PAYLOAD_MAX = 191

command = "BEACON_STATUS"
host_command = "BSF3C79 TAG RAW " + command
reply = (
    "BEACON sync=1 lock=1 rx=4294967295 promoted=1 "
    "mismatch=4294967295 miss=4294967295 gen=255 "
    "counter=4294967295 rebase=4294967295 dw=1 "
    "dwmiss=4294967295 win=10 rxarm=4294967295"
)

for name, value, bound in (
    ("BEACON_STATUS UART command", command, UART_PAYLOAD_MAX),
    ("wrapped BEACON_STATUS control line", host_command, CONTROL_LINE_MAX),
    ("BEACON_STATUS worst-case reverse reply", reply, UART_PAYLOAD_MAX),
):
    length = len(value.encode("ascii"))
    assert length <= bound, (name, length, bound)
    print(f"{name}: {length}/{bound} PASS")

pattern = re.compile(
    r"^BEACON sync=[01] lock=[01] rx=\d+ promoted=[01] "
    r"mismatch=\d+ miss=\d+ gen=\d+ counter=\d+ rebase=\d+ "
    r"dw=[01] dwmiss=\d+ win=(?:10|[1-9]) rxarm=\d+$"
)
assert pattern.fullmatch(reply)

tag_source = TAG_SOURCE.read_text(encoding="utf-8")
wire_header = WIRE_HEADER.read_text(encoding="utf-8")
b306_header = B306_HEADER.read_text(encoding="utf-8")
assert "beacon_rx_arm_failures" in tag_source
assert "TAG_RELAY7_BEACON_STATUS_FORMAT" in tag_source
assert re.search(r"#define\s+BSL_RELAY_PAYLOAD_MAX\s+191[uU]", wire_header)
assert re.search(r"#define\s+BSF_CONTROL_LINE_MAX\s+200[uU]", b306_header)
print("relay8.2 reply parser/source and reverse bounds: PASS")
