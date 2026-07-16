#!/usr/bin/env python3
"""Write a build provenance sidecar for a generated build directory."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a build provenance sidecar.")
    parser.add_argument("--build-dir", required=True, help="Build directory path")
    parser.add_argument("--source", required=True, help="Source file or script that generated the build")
    parser.add_argument(
        "--command",
        default=None,
        help="Optional shell command or invocation summary used to create the build",
    )
    parser.add_argument(
        "--note",
        action="append",
        default=[],
        help="Optional extra note; may be repeated",
    )
    args = parser.parse_args()

    build_dir = Path(args.build_dir).resolve()
    build_dir.mkdir(parents=True, exist_ok=True)

    sidecar_path = REPO_ROOT / f"{build_dir.name}.source"
    payload = {
        "build_dir": build_dir.name,
        "build_dir_path": str(build_dir),
        "source": args.source,
        "command": args.command,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "notes": args.note,
    }
    sidecar_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
