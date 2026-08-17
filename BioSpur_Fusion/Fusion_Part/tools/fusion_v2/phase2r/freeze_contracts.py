#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "BioSpur_Fusion/Fusion_Part/src"))

from biospur_fusion.calibration_v2.phase2r.contracts import (  # noqa: E402
    acceptance_contract, p3_scope, seeds, source_bundle_hash, split_protocol, write_json,
)
from biospur_fusion.calibration_v2.phase2r.governance import DataAccessBroker  # noqa: E402


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args()
    args.report.mkdir(parents=True, exist_ok=True)
    broker = DataAccessBroker.bootstrap(args.dataset, args.ledger, "P2R-04-contract-freeze")
    policy_hash = broker.hash_allowed(args.dataset / "DATA_ACCESS_POLICY.json", purpose="freeze base policy identity")["sha256"]
    addendum = broker.load_policy_addendum(args.dataset / "DATA_ACCESS_POLICY_ADDENDUM_003.json")
    addendum_hash = broker.hash_allowed(args.dataset / "DATA_ACCESS_POLICY_ADDENDUM_003.json", purpose="freeze addendum identity")["sha256"]
    prior_entries = []
    for entry in addendum["new_exact_allowlist"]:
        actual = broker.hash_allowed(Path(entry["realpath"]), purpose="freeze exact non-role prior identity")
        if actual["sha256"] != entry["sha256"]:
            raise SystemExit("operator prior identity mismatch")
        prior_entries.append(actual)
    plan = broker.read_json(args.dataset / "CAPTURE_PLAN_FINAL.json", purpose="freeze literal action routing")
    windows = broker.register_promoted_phase2_windows(plan)
    manifest_rows = []
    for row in windows:
        manifest = broker.read_json(Path(row["manifest"]), purpose=f"freeze promoted manifest {row['action_id']}")
        manifest_identity = broker.hash_allowed(Path(row["manifest"]), purpose=f"bind promoted manifest {row['action_id']}")
        if manifest.get("status") != "ACCEPTED" or manifest.get("data_role") != "PHASE2_CALIBRATION":
            raise SystemExit(f"non-accepted promoted manifest: {row['action_id']}")
        manifest_rows.append({
            **row,
            "manifest_sha256": manifest_identity["sha256"],
            "raw_slice_opaque_sha256": manifest["continuous_range"]["slice_sha256"],
            "actual_action_duration_s": manifest["actual_action_duration_s"],
            "preparation_buffer_s": manifest["preparation_buffer_s"],
            "post_action_buffer_s": manifest["post_action_buffer_s"],
            "promoted_attempt_id": manifest["attempt_id"],
        })
    holdouts = [{"action_id": a["action_id"], "relative_dir": a["relative_dir"], "numeric_access": "SEALED_ZERO"} for a in plan["actions"] if a["data_role"] == "SEALED_PHASE3_REGRESSION"]
    if len(holdouts) != 3:
        raise SystemExit("expected three sealed holdouts")
    source_paths = sorted((ROOT / "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/calibration_v2/phase2r").glob("*.py"))
    source_paths += sorted((ROOT / "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase2r").glob("*.py"))
    code_hash = source_bundle_hash(source_paths)
    pose = ROOT / "BioSpur_Fusion/Fusion_Part/config/fusion_v2/imu_frontend/POSE_USABILITY_CONTRACT.md"
    documents = {
        "PHASE2R_INPUT_MANIFEST.json": {
            "schema": "biospur-phase2r-input-manifest-v1", "dataset_root": str(args.dataset.resolve()),
            "base_policy_sha256": policy_hash, "addendum_sha256": addendum_hash,
            "checksum_manifest_sha256": broker.hash_allowed(args.dataset / "checksums/SHA256SUMS.txt", purpose="freeze chained checksum manifest")["sha256"],
            "code_bundle_sha256": code_hash, "promoted_phase2_windows": manifest_rows,
            "sealed_holdouts": holdouts, "forbidden_historical_input_count": 0,
        },
        "PHASE2R_DATA_SELECTION_ALLOWLIST.json": {
            "schema": "biospur-phase2r-data-selection-allowlist-v1", "phase2_windows": manifest_rows,
            "phase2_window_count": len(manifest_rows), "neutral_sway_numeric_count": 0,
            "holdout_numeric_count": 0, "recursive_glob_forbidden": True,
        },
        "PHASE2R_DATA_ACCESS_CONTRACT.json": {
            "schema": "biospur-phase2r-data-access-contract-v1", "base_policy_sha256": policy_hash,
            "addendum_sha256": addendum_hash,
            "broker": "exact realpath, pre-open denial, JSONL ledger", "forbidden_read_target": 0,
            "holdout_numeric_decode_target": 0, "mapping_revealing_pretruth_read_target": 0,
        },
        "PHASE2R_ACCEPTANCE_CONTRACT.json": acceptance_contract(code_hash),
        "PHASE2R_SPLIT_PROTOCOL.json": split_protocol(),
        "PHASE2R_RANDOM_SEEDS.json": seeds(),
        "PHASE2R_OPERATOR_PRIOR_BINDING.json": {
            "schema": "biospur-phase2r-operator-prior-binding-v1", "base_policy_sha256": policy_hash,
            "addendum_sha256": addendum_hash,
            "exact_entries": prior_entries, "body_role_content": False,
            "H9_evidence_source_count": 1, "DISTINCT_LAYOUT_structurally_excluded": ["BSFC2CC"],
            "directed_edge_identity": "DIRECTED_EDGE_ID_UNRESOLVED", "edge_to_imu_axis": "PCB_EDGE_TO_IMU_AXIS_UNRESOLVED",
        },
        "P3_PROVISIONAL_OUTPUT_SCOPE_AND_SENSITIVITY_PROTOCOL.json": p3_scope(pose.resolve(), sha(pose)),
    }
    hashes = {name: write_json(args.report / name, obj) for name, obj in documents.items()}
    freeze = {
        "schema": "biospur-phase2r-contract-freeze-v1", "status": "FROZEN_BEFORE_REAL_NUMERIC_ACCESS",
        "code_bundle_sha256": code_hash, "artifact_sha256": hashes, "broker_summary": broker.summary(),
        "contamination_status": "TRUTH_CONTAMINATED_DEVELOPMENT_REVISION",
        "contamination_reason": "execution-level historical mapping constant appeared in repository source-search output before candidate freeze",
    }
    write_json(args.report / "PHASE2R_CONTRACT_FREEZE.json", freeze)
    print(json.dumps(freeze, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
