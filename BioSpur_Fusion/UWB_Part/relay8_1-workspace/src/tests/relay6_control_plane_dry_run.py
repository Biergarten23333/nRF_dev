#!/usr/bin/env python3
"""Offline round-trip bound and parser dry run for relay6 commands."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
FUSION = ROOT.parents[2]
TAG_SOURCE = ROOT / "apps/tag/src/uwb_tag_ble.c"
B306_HEADER = FUSION / "B306_Part/include/biospur_fusion_ble.h"
WIRE_HEADER = ROOT / "include/biospur_link.h"

CONTROL_LINE_MAX = 200
UART_PAYLOAD_MAX = 191

raw_cfg = (
    "CFG TAG=15 SLOT=254 COUNT=255 MASK=0xFFFF PERIOD=65535 "
    "ACTIVE=65535 ACTIVE_US=65535 EPOCH=4294967295 "
    "SUPERFRAME_BASE=4294967295 GEN=255 BEACON_SYNC=1 "
    "DW_ANCHOR=1 RUN=1 PMODE=3"
)
host_cfg = "BSF3C79 TAG RAW " + raw_cfg
cfg_ok = (
    "CFG_OK TAG=15 SLOT=254/255 MASK=0xFFFF PERIOD=65535 "
    "ACTIVE=65535 ACTIVE_US=65535 GEN=255 BEACON_SYNC=1 "
    "DW_ANCHOR=1 LIVE=1 RUN=1 STATE=RUNNING"
)
beacon_command = "BEACON_STATUS"
host_beacon_command = "BSF3C79 TAG RAW " + beacon_command
beacon_reply = (
    "BEACON sync=1 lock=1 rx=4294967295 promoted=1 "
    "mismatch=4294967295 miss=4294967295 gen=255 "
    "counter=4294967295 rebase=4294967295 dw=1 "
    "dwmiss=4294967295"
)

for name, value, bound in (
    ("raw CFG UART payload", raw_cfg, UART_PAYLOAD_MAX),
    ("wrapped CFG control line", host_cfg, CONTROL_LINE_MAX),
    ("CFG_OK reverse reply", cfg_ok, UART_PAYLOAD_MAX),
    ("BEACON_STATUS UART command", beacon_command, UART_PAYLOAD_MAX),
    ("wrapped BEACON_STATUS control line", host_beacon_command, CONTROL_LINE_MAX),
    ("BEACON_STATUS reverse reply", beacon_reply, UART_PAYLOAD_MAX),
):
    assert len(value.encode("ascii")) <= bound, (
        name, len(value.encode("ascii")), bound
    )
    print(f"{name}: {len(value.encode('ascii'))}/{bound} PASS")

cfg_pattern = re.compile(
    r"^CFG TAG=(?P<tag>\d+) SLOT=(?P<slot>\d+) COUNT=(?P<count>\d+) "
    r"MASK=(?P<mask>0x[0-9A-F]+) PERIOD=(?P<period>\d+) "
    r"ACTIVE=(?P<active>\d+) ACTIVE_US=(?P<active_us>\d+) "
    r"EPOCH=(?P<epoch>\d+) SUPERFRAME_BASE=(?P<base>\d+) "
    r"GEN=(?P<gen>\d+) BEACON_SYNC=(?P<sync>[01]) "
    r"DW_ANCHOR=(?P<dw>[01]) RUN=(?P<run>[01]) PMODE=(?P<pmode>\d+)$"
)
beacon_pattern = re.compile(
    r"^BEACON sync=[01] lock=[01] rx=\d+ promoted=[01] "
    r"mismatch=\d+ miss=\d+ gen=\d+ counter=\d+ rebase=\d+ "
    r"dw=[01] dwmiss=\d+$"
)
cfg_ok_pattern = re.compile(
    r"^CFG_OK TAG=\d+ SLOT=\d+/\d+ MASK=0x[0-9A-F]+ PERIOD=\d+ "
    r"ACTIVE=\d+ ACTIVE_US=\d+ GEN=\d+ BEACON_SYNC=[01] "
    r"DW_ANCHOR=[01] LIVE=[01] RUN=[01] STATE=(?:RUNNING|ARMED)$"
)
assert cfg_pattern.fullmatch(raw_cfg)
assert cfg_ok_pattern.fullmatch(cfg_ok)
assert beacon_pattern.fullmatch(beacon_reply)

tag_source = TAG_SOURCE.read_text(encoding="utf-8")
wire_header = WIRE_HEADER.read_text(encoding="utf-8")
b306_header = B306_HEADER.read_text(encoding="utf-8")
for token in (
    '"DW_ANCHOR="',
    '"BEACON_STATUS"',
    "TAG_RELAY6_BEACON_STATUS_FORMAT",
):
    assert token in tag_source
settings_start = tag_source.index("struct uwb_tag_ble_settings_record")
settings_end = tag_source.index("};", settings_start)
assert "dw_anchor" not in tag_source[settings_start:settings_end]
store_start = tag_source.index("int uwb_tag_ble_runtime_config_store")
store_end = tag_source.index(
    "static void uwb_tag_ble_apply_mode_defaults", store_start
)
assert "record.dw_anchor" not in tag_source[store_start:store_end]
assert re.search(r"#define\s+BSL_RELAY_PAYLOAD_MAX\s+191[uU]", wire_header)
assert re.search(r"#define\s+BSF_CONTROL_LINE_MAX\s+200[uU]", b306_header)
print("target parser/source and declared reverse bounds: PASS")
