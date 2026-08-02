#!/usr/bin/env python3
"""Reject accidental reuse of a deployed B306 firmware marker."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


MARKER_RE = re.compile(rb"(?<![A-Za-z0-9._-])(b306-[A-Za-z0-9._-]+)\x00")


def marker_from_elf(path: Path) -> str:
    matches = {
        match.group(1).decode("ascii")
        for match in MARKER_RE.finditer(path.read_bytes())
    }
    if len(matches) != 1:
        raise SystemExit(
            f"MARKER_GUARD_FAIL expected one embedded b306 marker, found "
            f"{sorted(matches)} in {path}"
        )
    return matches.pop()


def check(elf: Path, artifact: Path, manifest_path: Path) -> tuple[str, str]:
    manifest = json.loads(manifest_path.read_text())
    marker = marker_from_elf(elf)
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    entry = manifest["markers"].get(marker)
    if entry is None:
        return marker, digest
    if entry.get("retired", False):
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={marker} status=retired sha256={digest}"
        )
    if digest not in entry.get("signed_sha256", []):
        raise SystemExit(
            f"MARKER_GUARD_FAIL marker={marker} deployed=1 "
            f"sha256={digest} expected={entry.get('signed_sha256', [])}"
        )
    return marker, digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", required=True, type=Path)
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    marker, digest = check(args.elf, args.artifact, args.manifest)
    print(f"MARKER_GUARD_PASS marker={marker} sha256={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

