#!/usr/bin/env python3
"""Build the exact pre-raw runtime identity for the continuous C2 run."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from run_c2_continuous_root_ab_full import (
    RAW,
    RAW_CONTAINER_SHA256_DECLARED,
    RUNTIME_MANIFEST_SCHEMA,
    runtime_required_files,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def build(output: Path) -> None:
    rows = []
    for role, path in sorted(runtime_required_files().items()):
        resolved = path.resolve()
        if not resolved.is_file():
            raise FileNotFoundError(resolved)
        rows.append({"role": role, "path": str(resolved), "sha256": _sha256(resolved)})
    raw_stat = RAW.stat()
    document = {
        "schema": RUNTIME_MANIFEST_SCHEMA,
        "raw_container": {
            "path": str(RAW.resolve()),
            "size_bytes": RAW.stat().st_size,
            "declared_sha256": RAW_CONTAINER_SHA256_DECLARED,
            "hash_recomputed_by_this_gate": False,
            "stat_identity": {
                "device": raw_stat.st_dev,
                "inode": raw_stat.st_ino,
                "size_bytes": raw_stat.st_size,
                "mtime_ns": raw_stat.st_mtime_ns,
            },
        },
        "files": rows,
    }
    output.parent.mkdir(parents=True, exist_ok=False)
    output.write_text(json.dumps(document, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.output)
