#!/usr/bin/env python3
"""Static relay8 scope, priority, compatibility, and frame-contract gates."""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]

uart = (ROOT / "src/biospur_uart_link.c").read_text()
tag = (ROOT / "apps/tag/src/uwb_tag_ble.c").read_text()
beacon = (ROOT / "include/tag_beacon_sync.h").read_text()
frame = (ROOT / "include/biospur_link.h").read_text()
ss_twr = (ROOT / "src/ss_twr_init.c").read_text()

worker = int(re.search(r"#define BSL_RELAY_WORKER_PRIORITY (\d+)", uart).group(1))
main_conf = (ROOT / "apps/tag/prj.conf").read_text()
main = int(re.search(r"CONFIG_MAIN_THREAD_PRIORITY=(\d+)", main_conf).group(1))
assert worker < main, (worker, main)

assert "tag_relay8_apply_idle_beacon_policy(params);" in tag
assert "*window_n_out = TAG_BEACON_WINDOW_N_DEFAULT;" in beacon
assert "period_us * (uint32_t)window_n" not in beacon

relay7_frame = (ROOT.parents[1] / "relay7-workspace/src/include/biospur_link.h").read_text()
body_pattern = re.compile(
    r"typedef struct __attribute__\(\(packed\)\) \{\n"
    r"\t/\* --- sweep identity.*?\n\} bsl_uwb_t;",
    re.DOTALL,
)
assert body_pattern.search(frame).group(0) == body_pattern.search(relay7_frame).group(0)
assert "#define BSL_FLAG_SUPERFRAME_MASK" in frame
assert "#define BSL_FLAG_SUPERFRAME_VALID" in frame

finish = ss_twr.index("static void ss_twr_init_alt_finish_sweep(void)")
finish_end = ss_twr.index("static bool ss_twr_init_alt_wait_tx_done", finish)
finish_body = ss_twr[finish:finish_end]
assert finish_body.index("ss_twr_init_note_sweep_done();") < finish_body.index(
    "ss_twr_init_beacon_service_post_sweep_if_urgent();"
) < finish_body.index("ss_twr_init_publish_ranges_if_ready();")
assert "body.flags, &ss_twr_init_sweep_epoch" in ss_twr
assert "&ss_twr_init_sweep_epoch, &ss_twr_init_beacon.epoch" in ss_twr

print(f"relay8 static contract: PASS worker={worker} main={main} frame_chars={len(frame)}")
