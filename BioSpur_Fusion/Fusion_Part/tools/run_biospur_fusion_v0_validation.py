#!/usr/bin/env python3
"""Lock and execute one predeclared BioSpur Fusion V0 validation action."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from biospur_fusion.v0.contracts import sha256_file
from biospur_fusion.v0.validation import (
    create_candidate_lock_manifest, run_locked_action_validation, write_checksums,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    root = Path(__file__).resolve().parents[1]
    sub = parser.add_subparsers(dest="command", required=True)
    lock = sub.add_parser("lock")
    lock.add_argument("--profile", type=Path, required=True)
    lock.add_argument("--destination", type=Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--predeclaration", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    checksums = sub.add_parser("checksums")
    checksums.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "lock":
        manifest = create_candidate_lock_manifest(root, args.profile, args.destination)
        print(json.dumps({
            "manifest": str(args.destination.resolve()),
            "candidate_manifest_sha256": sha256_file(args.destination),
            "profile_sha256": manifest["profile_sha256"],
            "covered_files": len(manifest["files"]),
        }, sort_keys=True))
        return 0
    if args.command == "run":
        result = run_locked_action_validation(root, args.predeclaration, args.output)
        print(json.dumps({
            "output": str(args.output.resolve()),
            "automatic_execution_integrity_pass": result["automatic_execution_integrity_pass"],
            "classification": result["classification"],
        }, sort_keys=True))
        return 0 if result["automatic_execution_integrity_pass"] else 2
    manifest = write_checksums(args.output)
    print(json.dumps({"output": str(args.output.resolve()), "files": len(manifest)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
