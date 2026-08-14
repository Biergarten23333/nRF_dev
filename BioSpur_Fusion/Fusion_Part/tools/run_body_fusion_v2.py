#!/usr/bin/env python3
"""Run the capture-bound offline V2 derivation twice; no hardware imports."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part" / "src"))
from biospur_fusion.pipeline.offline_v2 import dump, run_derivation, sha256  # noqa: E402


def npz_content_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    import numpy as np
    with np.load(path, allow_pickle=False) as archive:
        for key in sorted(archive.files):
            value = archive[key]
            digest.update(key.encode()); digest.update(value.dtype.str.encode())
            digest.update(str(value.shape).encode()); digest.update(value.tobytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--rebuild-ledger", action="store_true")
    args = parser.parse_args()
    out = args.out or args.capture / "analysis_body_fusion_v2"
    existing_ledger = out / "TIME_EVENT_LEDGER.npz"
    first = run_derivation(
        args.capture, out,
        ledger_path=existing_ledger if existing_ledger.exists() and not args.rebuild_ledger else None,
    )
    replay = out / "deterministic_replay_b"
    second = run_derivation(args.capture, replay, ledger_path=out / "TIME_EVENT_LEDGER.npz")
    compared = [
        "TIME_ALIGNMENT_RESULT.json", "CLOCK_MODELS.csv", "CLOCK_RESIDUALS.csv",
        "UWB_FRONTEND_AUDIT.csv", "IMU_FRONTEND_AUDIT.json",
        "FRAME_BINDING_RESULT.json", "BODY_MODEL_MANIFEST.json", "CALIBRATION_FREEZE_MANIFEST.json",
        "HELDOUT_VALIDATION.json", "NUMERICAL_INTEGRITY.json",
    ]
    hashes = {name: {"run_a": sha256(out / name), "run_b": sha256(replay / name)} for name in compared}
    hashes["Q1_ATTITUDE_TIMELINES.npz"] = {
        "run_a": npz_content_sha256(out / "Q1_ATTITUDE_TIMELINES.npz"),
        "run_b": npz_content_sha256(replay / "Q1_ATTITUDE_TIMELINES.npz"),
    }
    deterministic = all(value["run_a"] == value["run_b"] for value in hashes.values()) and first == second
    dump(out / "DETERMINISTIC_REPLAY.json", {"pass": deterministic, "run_a": first, "run_b": second,
                                               "artifact_hashes": hashes, "shared_ledger_sha256": sha256(out / "TIME_EVENT_LEDGER.npz")})
    files = sorted(path for path in out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (out / "SHA256SUMS").write_text("".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8")
    print(json.dumps({"result": first, "deterministic_replay": deterministic, "output": str(out)}, sort_keys=True))
    return 0 if deterministic else 2


if __name__ == "__main__":
    raise SystemExit(main())
