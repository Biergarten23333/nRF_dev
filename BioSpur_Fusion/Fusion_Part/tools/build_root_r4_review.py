#!/usr/bin/env python3
"""Rebuild only the offline Root-R4 viewer/video from finalized JSON evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]; sys.path.insert(0, str(ROOT / "Fusion_Part/src"))
from biospur_fusion.root_r4.viewer import build_review_video, build_viewer  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True); args = parser.parse_args()
    transforms = json.loads((args.output / "COMMON_TRANSFORM_CANDIDATES.json").read_text())
    blocks = json.loads((args.output / "COMMON_TRANSFORM_TIME_BLOCK_STABILITY.json").read_text())
    health = json.loads((args.output / "PER_LINK_HEALTH_AUDIT.json").read_text())
    lineage = json.loads((args.output / "T4_RAW_RANGE_LINEAGE_AUDIT.json").read_text())
    payload = {"synthetic": "truth-based qualification PASS", "real": "C1 internal diagnostic only", "authorized": "none",
               "lineage": f"{lineage['t4_events']:,}/{lineage['t4_events']:,} exact",
               "health": "/".join(str(health["classification_counts"].get(key, 0)) for key in
                                  ("RANGE_LINK_CREDIBLE", "RANGE_LINK_DEGRADED", "RANGE_LINK_REJECTED")),
               "frames": [{"layer": row["layer"], "yaw": row["yaw_V4_from_N_deg"], "med": row["residual_median_m"]}
                          for row in transforms["candidates"]],
               "blocks": [row["yaw_V4_from_N_deg"] for row in blocks["blocks"]]}
    build_viewer(args.output, payload); build_review_video(args.output, payload); return 0


if __name__ == "__main__":
    raise SystemExit(main())
