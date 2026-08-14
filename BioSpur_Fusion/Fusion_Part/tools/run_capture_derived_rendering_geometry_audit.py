#!/usr/bin/env python3
"""Run the sealed, calibration-only rendering-geometry feasibility audit."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.visualization.capture_derived_audit_v1 import run_audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-ledger", type=Path, required=True)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--gates", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run_audit(args.calibration_ledger, args.layout, args.gates, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
