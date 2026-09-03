#!/usr/bin/env python3
"""Run conservative qmt-off V0 development comparisons."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.v0.conservative_runner import (
    run_capture1_development, run_raw_development,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    sub = parser.add_subparsers(dest="command", required=True)
    capture1 = sub.add_parser("capture1")
    capture1.add_argument("--output", type=Path, required=True)
    action = sub.add_parser("development-action")
    action.add_argument("--predeclaration", type=Path, required=True)
    action.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture1":
        result = run_capture1_development(root, args.output)
    else:
        result = run_raw_development(root, args.predeclaration, args.output)
    print(json.dumps({
        "output": str(args.output.resolve()),
        "selected_v0_mode": result["selected_v0_mode"],
        "automatic_integrity_pass": result["automatic_integrity_pass"],
    }, sort_keys=True))
    return 0 if result["automatic_integrity_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
