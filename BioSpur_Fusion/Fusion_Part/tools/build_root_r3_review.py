#!/usr/bin/env python3
"""Build the standalone Root-R3 HTML viewer and accelerated review MP4."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.root_r3.viewer import build_review_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    viewer, video = build_review_artifacts(args.output)
    print(json.dumps({"viewer": str(viewer), "video": str(video)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
