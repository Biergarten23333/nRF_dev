#!/usr/bin/env python3
"""Finalize byte-identical dual-run evidence without runtime metadata."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MUTABLE = {"NUMERICAL_INTEGRITY.json", "REPORT.md", "SHA256SUMS"}


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            h.update(block)
    return h.hexdigest()


def file_names(root: Path) -> set[str]:
    return {path.name for path in root.iterdir() if path.is_file()}


def write_sums(root: Path) -> None:
    names = sorted(path.name for path in root.iterdir()
                   if path.is_file() and path.name != "SHA256SUMS")
    (root / "SHA256SUMS").write_text(
        "".join(f"{digest(root/name)}  {name}\n" for name in names), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    args = parser.parse_args()
    if file_names(args.first) != file_names(args.second):
        raise SystemExit("output file sets differ")
    compared = sorted(file_names(args.first) - MUTABLE)
    mismatch = [name for name in compared
                if digest(args.first/name) != digest(args.second/name)]
    if mismatch:
        raise SystemExit("non-deterministic outputs: " + ", ".join(mismatch))
    for root in (args.first, args.second):
        integrity_path = root / "NUMERICAL_INTEGRITY.json"
        integrity = json.loads(integrity_path.read_text(encoding="utf-8"))
        integrity["deterministic_replay_equality"] = True
        integrity["deterministic_compared_file_count"] = len(compared)
        integrity["deterministic_comparison_scope"] = compared
        integrity_path.write_text(json.dumps(integrity, indent=2, sort_keys=True,
                                             allow_nan=False) + "\n", encoding="utf-8")
        report_path = root / "REPORT.md"
        report = report_path.read_text(encoding="utf-8")
        report += ("\n## Reproducibility\n\nTwo independent full derivation/replay runs "
                   "produced byte-identical core JSON, CSV, Markdown and SVG outputs. "
                   "Runtime metadata is not embedded.\n")
        report_path.write_text(report, encoding="utf-8")
        write_sums(root)
    final_mismatch = [name for name in sorted(file_names(args.first))
                      if digest(args.first/name) != digest(args.second/name)]
    if final_mismatch:
        raise SystemExit("finalized output mismatch: " + ", ".join(final_mismatch))
    print(f"DETERMINISTIC_PASS files={len(file_names(args.first))}")


if __name__ == "__main__":
    main()
