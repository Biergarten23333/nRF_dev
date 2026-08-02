#!/usr/bin/env python3
"""Bind each Master_Tag carrier marker to one CPUAPP merged-HEX byte stream."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MARKER_RE = re.compile(
    rb"(?<![A-Za-z0-9._-])"
    rb"(master-tag-carrier-v2-fix[0-9]+-[A-Za-z0-9._-]+)"
    rb"\x00"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--registry", required=True, type=Path)
    parser.add_argument("--record-new", action="store_true")
    args = parser.parse_args()

    markers = {
        match.group(1).decode("ascii")
        for match in MARKER_RE.finditer(args.elf.read_bytes())
    }
    if len(markers) != 1:
        raise SystemExit(
            "CARRIER_MARKER_GUARD_FAIL expected exactly one carrier marker, "
            f"found {sorted(markers)}"
        )

    marker = markers.pop()
    digest = hashlib.sha256(args.artifact.read_bytes()).hexdigest()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    recorded = registry["markers"].get(marker)

    if recorded is not None and recorded != digest:
        raise SystemExit(
            f"CARRIER_MARKER_GUARD_FAIL marker={marker} "
            f"recorded={recorded} built={digest}"
        )
    if recorded is None:
        if not args.record_new:
            raise SystemExit(
                f"CARRIER_MARKER_GUARD_FAIL marker={marker} is new; "
                "use --record-new once after the double-build matches"
            )
        registry["markers"][marker] = digest
        args.registry.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"CARRIER_MARKER_GUARD_PASS marker={marker} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
