#!/usr/bin/env python3
"""Run the offline Root-R5A identifiability diagnostic."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))
from biospur_fusion.root_r5a.pipeline import run  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True, type=Path); args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
