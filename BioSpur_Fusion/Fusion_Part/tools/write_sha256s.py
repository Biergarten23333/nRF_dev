#!/usr/bin/env python3
"""Write a deterministic recursive SHA256SUMS for an analysis directory."""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            value.update(block)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("directory", type=Path)
    root = parser.parse_args().directory.resolve(); output = root / "SHA256SUMS"
    files = sorted(path for path in root.rglob("*") if path.is_file() and path != output)
    output.write_text("".join(f"{digest(path)}  {path.relative_to(root)}\n" for path in files), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
