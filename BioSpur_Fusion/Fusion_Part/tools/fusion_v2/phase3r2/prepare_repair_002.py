#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat


ROOT = Path(__file__).resolve().parents[5]
RUN = ROOT / "BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r2/phase3r2_20260818T084835Z"
ORIGINAL = RUN / "SCIENTIFIC_CLOSURE_MANIFEST.json"


def sha(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, path)


def main() -> int:
    amendment = RUN / "QUALIFICATION_AMENDMENT_002.json"
    write_json(amendment, {
        "schema": "biospur-phase3r2-qualification-amendment-v1",
        "amendment_id": "QUALIFICATION_AMENDMENT_002",
        "classification": "FORWARD_SCIENTIFIC_QUALIFICATION_REPAIR",
        "supersedes_observability_interpretation_in_commit": "6e0090b397cd420a3a2b58530ecca51fb8d6953e",
        "problem": "convention-fixed rank was reported without a separate gauge-free null-mode matrix",
        "repair": "project one declared common global-yaw gauge before the fixed tolerance sweep and report convention-fixed separately",
        "real_data_verdict_changed": False,
        "h_numeric_decode_before_repair": 0,
        "requires_new_forward_candidate": True
    })
    original = json.loads(ORIGINAL.read_text())
    paths = [row["path"] for row in original["files"]]
    paths.append(str(amendment.relative_to(ROOT)))
    rows = []
    closure = hashlib.sha256()
    for relative in sorted(paths):
        path = ROOT / relative; digest = sha(path)
        rows.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
        closure.update(relative.encode() + b"\0" + digest.encode() + b"\0")
    manifest = RUN / "SCIENTIFIC_CLOSURE_MANIFEST_002.json"
    write_json(manifest, {
        "schema": "biospur-phase3r2-scientific-closure-v1", "revision": 2,
        "status": "LIMITED_CANDIDATE_GAUGE_FREE_OBSERVABILITY_REPAIR",
        "files": rows, "scientific_closure_sha256": closure.hexdigest(),
        "supersedes": "SCIENTIFIC_CLOSURE_MANIFEST.json",
        "real_calibration_bundle": "NOT_CREATED_TIME_GATE"
    })
    stage = RUN / "STAGING_ALLOWLIST_CANDIDATE_002.txt"
    paths = sorted({
        "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/imu_pose_v2/observability.py",
        "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r2/test_fk_solver_observability.py",
        "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r2/prepare_repair_002.py",
        str(amendment.relative_to(ROOT)), str(manifest.relative_to(ROOT)),
        str(stage.relative_to(ROOT)),
        str((RUN / "WIP_CLOSURE_CANDIDATE_002.json").relative_to(ROOT)),
    })
    stage.write_text("".join(path + "\n" for path in paths))
    wip_rows = []
    for relative in paths:
        if relative.endswith("WIP_CLOSURE_CANDIDATE_002.json"): continue
        path = ROOT / relative
        wip_rows.append({"path": relative, "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
                         "size": path.stat().st_size, "sha256": sha(path)})
    write_json(RUN / "WIP_CLOSURE_CANDIDATE_002.json", {
        "schema": "biospur-phase3r2-wip-closure-v1", "revision": 2,
        "files": wip_rows,
        "wip_closure_sha256": hashlib.sha256(json.dumps(wip_rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "self_excluded_to_avoid_self_reference": True
    })
    print(json.dumps({"scientific_closure_sha256": closure.hexdigest(), "staging_paths": len(paths)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
