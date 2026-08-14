#!/usr/bin/env python3
"""Run V4.1 twice without mutating V3/V4 or opening held-out pre-freeze."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))

from biospur_fusion.pipeline.offline_v4_1 import dump, run, sha  # noqa: E402


def _artifact_hashes(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): sha(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and "deterministic_replay_b" not in path.relative_to(root).parts
        and path.name not in {"DETERMINISTIC_REPLAY.json", "SHA256SUMS"}
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture",
        type=Path,
        default=ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    output = args.out or args.capture / "analysis_body_fusion_v4_1"
    first = run(args.capture, output, ROOT)
    replay = output / "deterministic_replay_b"
    second = run(args.capture, replay, ROOT)
    first_hashes = _artifact_hashes(output)
    second_hashes = _artifact_hashes(replay)
    deterministic = first == second and first_hashes == second_hashes
    dump(output / "DETERMINISTIC_REPLAY.json", {
        "pass": deterministic,
        "run_a": first,
        "run_b": second,
        "run_a_artifact_hashes": first_hashes,
        "run_b_artifact_hashes": second_hashes,
    })
    manifest_rows = []
    for path in sorted(output.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS":
            manifest_rows.append(f"{sha(path)}  {path.relative_to(output)}")
    (output / "SHA256SUMS").write_text("\n".join(manifest_rows) + "\n", encoding="utf-8")
    print(json.dumps({
        "result": first,
        "deterministic": deterministic,
        "output": str(output),
        "sha256sum_entries": len(manifest_rows),
    }, sort_keys=True))
    return 0 if deterministic else 2


if __name__ == "__main__":
    raise SystemExit(main())
