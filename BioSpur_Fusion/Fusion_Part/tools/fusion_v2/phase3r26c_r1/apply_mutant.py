#!/usr/bin/env python3
"""Copy the production package to /tmp and apply one exact source mutation."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import shutil

from mutants import MUTANTS


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--mutant-root", type=Path, required=True)
    parser.add_argument("--mutant", choices=tuple(MUTANTS), required=True)
    args = parser.parse_args()
    root = args.mutant_root.resolve()
    if not str(root).startswith("/tmp/"):
        raise RuntimeError("mutant root must be below /tmp")
    if root.exists():
        raise RuntimeError(f"mutant root already exists: {root}")
    target_package = root / "biospur_fusion"
    shutil.copytree(args.package.resolve(), target_package)
    relative, old, new = MUTANTS[args.mutant]
    target = target_package / "heading_anchor_audit_v2" / relative
    before = target.read_text()
    if before.count(old) != 1:
        raise RuntimeError(f"expected exactly one mutation anchor, found {before.count(old)}")
    before_sha = digest(target)
    target.write_text(before.replace(old, new, 1))
    after = target.read_text()
    after_sha = digest(target)
    if after_sha == before_sha:
        raise RuntimeError("mutation did not change production source")
    print(json.dumps({
        "mutant": args.mutant,
        "mutant_root_path": str(root),
        "mutated_source": str(target),
        "source_sha_before": before_sha,
        "source_sha_after": after_sha,
        "source_diff": "".join(difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"baseline/{relative}",
            tofile=f"mutant/{args.mutant}/{relative}",
        )),
        "actual_production_source_mutation": True,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
