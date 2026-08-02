#!/usr/bin/env python3
"""Bind a Fusion Master marker to exactly one application byte image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--marker", required=True)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "marker_registry.json",
    )
    args = parser.parse_args()

    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    if args.marker not in registry:
        raise SystemExit(f"MARKER_GUARD_FAIL unregistered={args.marker}")
    binary = args.binary.read_bytes()
    marker_hits = binary.count(args.marker.encode("ascii"))
    if marker_hits != 1:
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker_hits={marker_hits} expected=1"
        )
    digest = hashlib.sha256(binary).hexdigest()
    expected = registry[args.marker]["sha256"]
    if digest != expected:
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={args.marker} "
            f"registered={expected} actual={digest}"
        )
    print(f"MARKER_GUARD_PASS marker={args.marker} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
