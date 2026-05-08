#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from gui_prepare_layout_candidate import build_candidate, _load_json


PUSH_SCRIPT = Path(
    "SS-TWR/alt-SS-TWR/broadcast/scripts/push_apos_layout_verified.py"
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare a GUI candidate layout and push it to Tags with verified APOS forwarding.")
    ap.add_argument("--port", required=True)
    ap.add_argument("--layout-input", required=True, help="Solve JSON or layout JSON")
    ap.add_argument("--targets", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--result-key")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--reset-master", action="store_true")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    layout_input = Path(args.layout_input)
    payload = _load_json(layout_input)
    candidate = build_candidate(payload, layout_input, args.result_key)
    candidate_path = out_dir / "layout_candidate.json"
    candidate_path.write_text(json.dumps(candidate, indent=2) + "\n", encoding="utf-8")

    cmd = [
        sys.executable,
        str(PUSH_SCRIPT),
        "--port",
        args.port,
        "--baud",
        str(args.baud),
        "--targets",
        args.targets,
        "--layout-file",
        str(candidate_path),
        "--out-dir",
        str(out_dir),
    ]
    if args.reset_master:
        cmd.append("--reset-master")

    result = subprocess.run(cmd, check=False)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
