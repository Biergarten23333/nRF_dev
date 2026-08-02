#!/usr/bin/env python3
"""Reject reuse of a version marker with different firmware image bytes.

Pass the reproducible application ``zephyr.bin`` as ``--artifact``.  Do not
pass ``zephyr.signed.bin``: this tree uses RSA-PSS, whose required random salt
makes the signature envelope different on every signing run even when the
authenticated firmware bytes and MCUboot image hash are identical.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MARKER_RE = re.compile(
    rb"(?<![A-Za-z0-9._-])"
    rb"(tag-fusion-link-(?:v2-)?relay(?:[4-8]|8\.[12]))"
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
            "MARKER_GUARD_FAIL expected exactly one relay4 through relay8.2 marker, "
            f"found {sorted(markers)}"
        )

    marker = markers.pop()
    digest = hashlib.sha256(args.artifact.read_bytes()).hexdigest()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    recorded = registry["markers"].get(marker)

    if recorded is not None and recorded != digest:
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={marker} "
            f"recorded={recorded} built={digest}"
        )
    if recorded is None:
        if not args.record_new:
            raise SystemExit(
                f"MARKER_GUARD_FAIL marker={marker} is new; "
                "use --record-new once after reproducibility passes"
            )
        registry["markers"][marker] = digest
        args.registry.write_text(
            json.dumps(registry, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(f"MARKER_GUARD_PASS marker={marker} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
