#!/usr/bin/env python3
"""Bind or execute the exact C1/C2/C3 protocol-window R6A2B rerun."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

FUSION = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FUSION / "src"))

from biospur_fusion.root_r6a2b.protocol_windows import (  # noqa: E402
    bind_protocol_windows,
    run_protocol_windows,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bind-only", action="store_true")
    group.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    args.result_dir.mkdir(parents=True, exist_ok=True)
    path = bind_protocol_windows(FUSION, args.result_dir) if args.bind_only else run_protocol_windows(FUSION, args.result_dir)
    print(path)


if __name__ == "__main__":
    main()
