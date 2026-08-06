#!/usr/bin/env python3
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = (root / "src/main.c").read_text()
pm = (root / "pm_static.yml").read_text()

assert "BT_GATT_CHRC_READ" in src
read_body = src[src.index("static ssize_t stall_status_read(", src.index("static ssize_t stall_status_read(") + 1):]
read_body = read_body[:read_body.index("\n}")]
assert "bt_gatt_attr_read" in read_body
assert "publish_control_reply" not in read_body
assert "enqueue_ctl_record" not in read_body
assert "STALL_ARM_NOTIFY_OK 64u" in src
assert "BLE_SUPERVISION_TIMEOUT_MS 4000u" in src
assert "STALL_DETECT_MS 5000u" in src
assert "STALL_RECOVERY_RETRACT_MS 1500u" in src
assert "in_call_age_ms" in src and "k_uptime_get()" in src
assert "RETAINED_STALL_MAGIC 0x56333852u" in src
assert "STALL_MAX_RECOVERIES_PER_POWER 1u" in src
assert src.index("retained_stall.first_snapshot = sampled") < src.index(
    "k_work_reschedule(&stall_recovery_work")
assert "end_address: 0x100000" in pm
assert "settings_storage" not in pm and "storage" not in pm
print("v36 source contract: PASS")
