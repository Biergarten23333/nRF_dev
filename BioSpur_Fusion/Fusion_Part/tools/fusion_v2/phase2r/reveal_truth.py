#!/usr/bin/env python3
"""One-way P2R-10/11 sealed mapping reveal and frozen-candidate validation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT / "BioSpur_Fusion/Fusion_Part/src"))

from biospur_fusion.calibration_v2.phase2r.contracts import write_json  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.governance import DataAccessBroker  # noqa: E402


COMMITMENT = "8f744eee31ff505b58ee24e88c75f22c6f75dccce7ad4719a2476a42d72a0524"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_ledger(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    mapping_pretruth = [row for row in rows if row.get("stage") == "PHASE2R_PRETRUTH" and row.get("allowed") and row.get("access_class") == "SEALED_MAPPING_TRUTH"]
    holdout_reads = [row for row in rows if row.get("allowed") and "/holdout/" in (row.get("resolved_realpath") or "") and row.get("payload_bytes_read", 0) > 0]
    return {"path": str(path.resolve()), "sha256": sha(path), "rows": len(rows), "mapping_pretruth_allowed_reads": len(mapping_pretruth), "holdout_payload_reads": len(holdout_reads)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--candidate-ledger", type=Path, action="append", required=True)
    args = parser.parse_args()
    freeze_path = args.report / "BLIND_CANDIDATE_FREEZE.json"
    freeze_sha_line = (args.report / "BLIND_CANDIDATE_FREEZE.sha256").read_text().strip().split()
    if len(freeze_sha_line) != 2 or freeze_sha_line[0] != sha(freeze_path):
        raise SystemExit("candidate freeze SHA mismatch")
    freeze = json.loads(freeze_path.read_text())
    if not freeze.get("candidate_worker_terminated_after_freeze"):
        raise SystemExit("candidate worker termination not attested")
    ledger_audits = [audit_ledger(path) for path in args.candidate_ledger]
    if any(x["mapping_pretruth_allowed_reads"] or x["holdout_payload_reads"] for x in ledger_audits):
        raise SystemExit("pretruth ledger gate failed")
    if freeze.get("holdout_numeric_bytes_read") != 0 or freeze.get("candidate_worker_forbidden_dataset_read_count") != 0:
        raise SystemExit("candidate freeze access counters failed")

    broker = DataAccessBroker.bootstrap(args.dataset, args.ledger, "P2R-10-sealed-truth-reveal")
    truth_path = args.dataset / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json"
    broker.enable_sealed_mapping_reveal(truth_path, COMMITMENT, freeze_validated=True)
    truth_payload = broker.read_bytes(truth_path, purpose="single sealed mapping reveal for frozen-candidate validation")
    truth = json.loads(truth_payload)
    observed = hashlib.sha256(truth_payload).hexdigest()
    if observed != COMMITMENT:
        raise SystemExit("sealed truth commitment mismatch")
    truth_mapping = {row["hardware_id"]: row["body_segment"] for row in truth["rows"]}
    topk = json.loads((args.report / "BLIND_NODE_ASSOCIATION_TOPK.json").read_text())
    candidates = topk["topk"]
    truth_rank = next((index + 1 for index, row in enumerate(candidates) if row["mapping"] == truth_mapping), None)
    top1 = candidates[0]["mapping"]
    exact = sum(top1.get(node) == role for node, role in truth_mapping.items())
    gates = freeze["scientific_gates_without_truth"]
    automatic_pass = exact == 10 and all(gates.values()) and freeze["status"] != "TRUTH_CONTAMINATED_DEVELOPMENT_REVISION"
    release = {
        "schema": "biospur-phase2r-sealed-truth-release-record-v1", "transaction": "ONE_WAY_AFTER_CANDIDATE_FREEZE",
        "candidate_freeze_sha256": freeze_sha_line[0], "candidate_worker_terminated": True,
        "candidate_ledgers": ledger_audits, "pretruth_mapping_opens": 0, "holdout_numeric_opens": 0,
        "truth_path": "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json", "truth_sha256": observed,
        "truth_reveal_ledger_sha256": sha(args.ledger), "historical_typo_resolution": "BSFC22C -> BSFC2CC operator-confirmed",
    }
    verification = {
        "schema": "biospur-phase2r-mapping-commitment-verification-v1", "expected_sha256": COMMITMENT,
        "observed_sha256": observed, "match": observed == COMMITMENT, "node_count": len(truth_mapping),
        "unique_hardware_ids": len(set(truth_mapping)), "unique_roles": len(set(truth_mapping.values())),
    }
    validation = {
        "schema": "biospur-phase2r-blind-association-validation-v1",
        "candidate_status": freeze["status"], "automatic_association_status": "PASS" if automatic_pass else "FAILED_OR_CONDITIONAL",
        "top1_exact_matches": exact, "top1_total": 10, "truth_topk_rank": truth_rank,
        "truth_in_topk": truth_rank is not None, "pre_frozen_gates": gates,
        "all_pre_frozen_gates_pass": all(gates.values()), "no_post_truth_tuning": True,
        "post_truth_candidate_reordering": False, "authoritative_node_association_freeze_generated": False,
    }
    result = {
        "schema": "biospur-phase2r-blind-association-validated-result-v1",
        "substage_result": "FAIL_PHASE2A_BLIND_NODE_ASSOCIATION",
        "reason": ["pre-frozen statistical gates failed", "execution-level historical mapping source exposure contaminated pristine blindness"],
        "top1_exact_matches": exact, "truth_topk_rank": truth_rank, "candidate_freeze_sha256": freeze_sha_line[0],
        "mapping_commitment_verified": True, "no_post_truth_tuning": True,
    }
    binding = {
        "schema": "biospur-phase2r-operator-ground-truth-mapping-binding-v1",
        "binding_authority": "OPERATOR_RECORDED_POST_CAPTURE", "automatic_association_status": "FAILED_OR_CONDITIONAL",
        "mapping_commitment_sha256": COMMITMENT, "mapping": truth_mapping,
        "use": "operator-bound conditional calibration only; does not convert automatic result to PASS",
    }
    for name, obj in (
        ("SEALED_TRUTH_RELEASE_RECORD.json", release),
        ("MAPPING_COMMITMENT_VERIFICATION.json", verification),
        ("BLIND_NODE_ASSOCIATION_VALIDATION.json", validation),
        ("BLIND_NODE_ASSOCIATION_VALIDATED_RESULT.json", result),
        ("OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json", binding),
    ):
        write_json(args.report / name, obj)
    print(json.dumps({"commitment_verified": True, "top1_exact_matches": exact, "truth_topk_rank": truth_rank, "automatic_pass": automatic_pass, "holdout_numeric_opens": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
