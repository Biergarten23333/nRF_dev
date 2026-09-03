#!/usr/bin/env python3
"""Run the integrated real-data BioSpur Fusion V0 baseline."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.v0.pipeline import run_v0


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    parser.add_argument("--config", type=Path, default=root / "config/biospur_fusion_v0/config.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_v0(root, args.config, args.output)
    print(json.dumps({"pass": result["pass"], "classification": result["classification"], "output": str(args.output.resolve())}, sort_keys=True))
    return 0 if result["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
