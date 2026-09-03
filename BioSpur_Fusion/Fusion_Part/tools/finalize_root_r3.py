#!/usr/bin/env python3
"""Finalize Root-R3 reporting and before/after integrity evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.root_r3.finalize import finalize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = finalize(args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
