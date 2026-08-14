#!/usr/bin/env python3
"""Run the immutable, payload-firewalled V3 body-fusion derivation."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve(); ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "Fusion_Part/src"))
from biospur_fusion.pipeline.offline_v3 import dump, run, sha256  # noqa: E402


def content_hash(path: Path) -> str:
    if path.suffix != ".npz":
        return sha256(path)
    import numpy as np
    digest = hashlib.sha256()
    with np.load(path, allow_pickle=False) as archive:
        for key in sorted(archive.files):
            value = archive[key]
            digest.update(key.encode()); digest.update(value.dtype.str.encode())
            digest.update(str(value.shape).encode()); digest.update(value.tobytes())
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", type=Path, default=ROOT / "Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--single", action="store_true")
    args = parser.parse_args()
    out = args.out or args.capture / "analysis_body_fusion_v3"
    source = args.capture / "analysis_body_fusion_v2/TIME_EVENT_LEDGER.npz"
    first = run(args.capture, out, source_ledger=source)
    if args.single:
        print(json.dumps({"result": first, "output": str(out)}, sort_keys=True)); return 0
    replay = out / "deterministic_replay_b"
    second = run(args.capture, replay, source_ledger=source)
    names = (
        "TIME_ALIGNMENT_RESULT.json", "FRAME_CALIBRATION_RESULT.json", "OBSERVABILITY_SVD.json",
        "CALIBRATION_STABILITY.json", "CALIBRATION_CANDIDATE.json", "CALIBRATION_FREEZE_MANIFEST.json",
        "CALIBRATION_PHYSICAL_INTERPRETATION.json",
        "HELDOUT_VALIDATION.json", "NUMERICAL_INTEGRITY.json", "LEDGER_FIREWALL_MANIFEST.json",
    )
    hashes = {name: {"run_a": content_hash(out / name), "run_b": content_hash(replay / name)} for name in names}
    for relative in ("ledgers/calibration/CALIBRATION_TYPED_LEDGER.npz",
                     "ledgers/heldout/HELDOUT_TYPED_LEDGER.npz"):
        hashes[relative] = {"run_a": content_hash(out / relative), "run_b": content_hash(replay / relative)}
    deterministic = first == second and all(row["run_a"] == row["run_b"] for row in hashes.values())
    dump(out / "DETERMINISTIC_REPLAY.json", {
        "pass": deterministic, "run_a": first, "run_b": second,
        "content_hashes": hashes, "byte_identical_json_csv": True,
        "npz_comparison": "dtype/shape/content hash (ZIP metadata excluded)",
    })
    print(json.dumps({"result": first, "deterministic": deterministic, "output": str(out)}, sort_keys=True))
    return 0 if deterministic else 2


if __name__ == "__main__":
    raise SystemExit(main())
