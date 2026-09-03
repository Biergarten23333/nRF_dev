from __future__ import annotations

import json
from pathlib import Path

CONFIG_PATH = Path(__file__).with_name("frozen_config.json")
STAGE1_ROOT = Path("/tmp/biospur_pure_imu_baseline_c123_20260823T091031Z")
STAGE2_ROOT = Path("/tmp/biospur_pure_imu_interactive_stage2_20260823T105814Z")
STAGE3R1_ROOT = Path("/tmp/biospur_pure_imu_stage3r1_bumpless_edges_20260823T131630Z")
CAPTURES = ("1", "2", "3")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
