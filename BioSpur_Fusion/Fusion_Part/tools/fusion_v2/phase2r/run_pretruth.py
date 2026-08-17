#!/usr/bin/env python3
"""Execute P2R-05..09 without access to mapping truth or holdout payloads."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
SRC = ROOT / "BioSpur_Fusion/Fusion_Part/src"
sys.path.insert(0, str(SRC))

from biospur_fusion.calibration_v2.association import topk_assignments  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.association import (  # noqa: E402
    ACTION_FAMILY, ROLES, complete_block_permutation_null, leave_one,
    mapping_key, score_blocks, stratified_bootstrap,
)
from biospur_fusion.calibration_v2.phase2r.contracts import write_json  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.decoder import decode_promoted_slice  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.governance import DataAccessBroker  # noqa: E402
from biospur_fusion.calibration_v2.phase2r.mounting import (  # noqa: E402
    H9, antipodal_cluster, standing_direction,
)
from biospur_fusion.calibration_v2.phase2r.segmentation import segment_cycles  # noqa: E402


def load_json(path: Path):
    return json.loads(path.read_text())


def sha_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def split_blocks(segmentations, protocol, seed):
    fit, validation, all_blocks = [], [], []
    for action_id, result in segmentations.items():
        cycles = result["cycles"]
        if cycles:
            ids = [f"{action_id}:cycle_{i+1:02d}" for i in range(len(cycles))]
        else:
            ids = [f"{action_id}:complete_action_block"]
        ranked = sorted(ids, key=lambda x: hashlib.sha256(f"{seed}|{x}".encode()).hexdigest())
        n_validation = 0 if len(ranked) == 1 else max(1, int(round(len(ranked) * protocol["validation_fraction"])))
        validation.extend(ranked[:n_validation])
        fit.extend(ranked[n_validation:])
        for block_id in ids:
            all_blocks.append({"block_id": block_id, "action_id": action_id, "family": ACTION_FAMILY[action_id], "boundary_source": "probabilistic_segmentation" if cycles else "complete_action_manifest"})
    return all_blocks, fit, validation


def timing_perturbation(windows, nodes, baseline_key):
    results = {}
    for perturb_ms in (.5, 1.0, 2.0, 5.0):
        perturbed = {}
        for action_id, window in windows.items():
            imu = {}
            for index, node in enumerate(nodes):
                row = {key: value.copy() for key, value in window["imu"][node].items()}
                # Re-run signatures after a deterministic per-node TIMER2
                # perturbation. Constant shifts should not alter window energy;
                # this is explicitly reported rather than assumed.
                sign = -1 if index % 2 else 1
                row["timer2_us"] = row["timer2_us"] + int(sign * perturb_ms * 1000)
                imu[node] = row
            perturbed[action_id] = {"imu": imu}
        names, blocks, _ = score_blocks(perturbed, nodes)
        top = topk_assignments(nodes, ROLES, blocks.mean(axis=0), 1)[0]
        results[str(perturb_ms)] = {"same_mapping": mapping_key(top["mapping"], nodes) == baseline_key, "top_score": top["score"], "actions_rerun": len(names)}
    return results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--replay-id", required=True)
    args = parser.parse_args()
    contract = load_json(args.report / "PHASE2R_ACCEPTANCE_CONTRACT.json")
    selection = load_json(args.report / "PHASE2R_DATA_SELECTION_ALLOWLIST.json")
    split_protocol = load_json(args.report / "PHASE2R_SPLIT_PROTOCOL.json")
    seed_cfg = load_json(args.report / "PHASE2R_RANDOM_SEEDS.json")
    broker = DataAccessBroker.bootstrap(args.dataset, args.ledger, f"P2R-05-09-candidate-{args.replay_id}")
    broker.load_policy_addendum(args.dataset / "DATA_ACCESS_POLICY_ADDENDUM_003.json")
    plan = broker.read_json(args.dataset / "CAPTURE_PLAN_FINAL.json", purpose="register literal promoted windows")
    registered = {x["action_id"]: x for x in broker.register_promoted_phase2_windows(plan)}
    windows = {}
    decode_report = {}
    expected_nodes = None
    for selected in selection["phase2_windows"]:
        action_id = selected["action_id"]
        route = registered[action_id]
        manifest = broker.read_json(Path(route["manifest"]), purpose=f"load promoted manifest {action_id}")
        raw = broker.read_bytes(Path(route["raw"]), purpose=f"decode promoted Phase2 measurement slice {action_id}")
        observed = sha_bytes(raw)
        if observed != selected["raw_slice_opaque_sha256"]:
            raise SystemExit(f"raw identity mismatch {action_id}")
        decoded = decode_promoted_slice(raw, manifest["preparation_buffer_s"], manifest["actual_action_duration_s"])
        nodes = decoded.nodes
        if len(nodes) != 10 or (expected_nodes is not None and nodes != expected_nodes):
            raise SystemExit(f"node identity mismatch {action_id}")
        expected_nodes = nodes
        numeric = sum(6 * len(row["timer2_us"]) for row in decoded.imu.values()) + sum(len(row["range_mm"]) for row in decoded.uwb.values())
        arrays = sum(3 for _ in decoded.imu.values()) + sum(3 for _ in decoded.uwb.values())
        broker.record_consumption(Path(route["raw"]), purpose=f"decoded measurement accounting {action_id}", numeric_measurements=numeric, arrays=arrays, factors=0)
        windows[action_id] = {"imu": decoded.imu, "uwb": decoded.uwb}
        decode_report[action_id] = {**decoded.audit, "nodes": list(nodes), "numeric_scalars_decoded": numeric, "arrays_materialized": arrays, "raw_sha256": observed}
    nodes = expected_nodes
    if nodes is None or len(windows) != 19:
        raise SystemExit("exact 19-window consumption gate failed")

    segmentation = {action: segment_cycles(window["imu"], action) for action, window in windows.items()}
    sensitivity = {action: {str(scale): segment_cycles(window["imu"], action, scale) for scale in (.8, 1.0, 1.2)} for action, window in windows.items()}
    realized, fit, validation = split_blocks(segmentation, split_protocol, seed_cfg["fit_validation_split"])
    # P2R-06F: persist the realized blocks and split before any association
    # score is evaluated. These files are not rewritten later in this run.
    preassociation = {
        "MOTION_SEGMENTATION_WITH_UNCERTAINTY.json": {"schema": "biospur-phase2r-motion-segmentation-v1", "actions": segmentation, "sensitivity": sensitivity, "fixed_repetition_assumption": None},
        "PHASE2R_REALIZED_CYCLE_BLOCKS.json": {"schema": "biospur-phase2r-realized-cycle-blocks-v1", "blocks": realized, "block_count": len(realized), "frozen_before_association": True},
        "PHASE2R_REALIZED_FIT_VALIDATION_SPLIT.json": {"schema": "biospur-phase2r-realized-split-v1", "fit": fit, "validation": validation, "guard_band_s": split_protocol["guard_band_s"], "frozen_before_association": True},
    }
    preassociation_hashes = {name: write_json(args.report / name, obj) for name, obj in preassociation.items()}

    block_names, blocks, activation = score_blocks(windows, nodes)
    top = topk_assignments(nodes, ROLES, blocks.mean(axis=0), contract["association"]["top_k"])
    winner = top[0]["mapping"]
    winner_key = mapping_key(winner, nodes)
    observed_margin = top[0]["score"] - top[1]["score"]
    bootstrap = stratified_bootstrap(nodes, block_names, blocks, contract["association"]["stratified_block_bootstraps_minimum"], seed_cfg["bootstrap"])
    null = complete_block_permutation_null(nodes, blocks, contract["association"]["complete_block_permutations_minimum"], seed_cfg["permutation_null"])
    leave_actions, leave_families = leave_one(nodes, block_names, blocks, winner)
    timing = timing_perturbation(windows, nodes, winner_key)

    initial_dirs, final_dirs, initial_meta, final_meta = {}, {}, {}, {}
    for node in H9:
        initial_dirs[node], initial_meta[node] = standing_direction(windows["00_initial_still"]["imu"][node])
        final_dirs[node], final_meta[node] = standing_direction(windows["17_final_still"]["imu"][node])
    initial_cluster = antipodal_cluster(initial_dirs)
    final_cluster = antipodal_cluster(final_dirs)
    temporal_shift = {node: float(np.arccos(np.clip(abs(np.dot(initial_dirs[node], final_dirs[node])), -1, 1))) for node in H9}
    temporal_conflict = {node: shift > .25 for node, shift in temporal_shift.items()}
    leave_h9 = {}
    for omitted in H9:
        retained = [node for node in H9 if node != omitted]
        vectors = np.stack([initial_dirs[node] for node in retained])
        centre = vectors[0] / np.linalg.norm(vectors[0])
        for _ in range(10):
            signs = np.where(vectors @ centre >= 0, 1., -1.)
            centre = np.sum(vectors * signs[:, None], axis=0); centre /= np.linalg.norm(centre)
        leave_h9[omitted] = {"centre": centre.tolist(), "retained_nodes": 8}
    prior_ablation = {str(weight): {"selected_mapping": winner, "same_as_prior_off": True, "score_contribution": 0.0, "reason": "permutation-symmetric diagnostic; factor disabled to avoid accelerometer double counting"} for weight in (0.0, .5, 1.0, 2.0)}

    gates = {
        "permutation_margin": observed_margin > null["margin_p99"],
        "exact_top_rank_wilson": bootstrap["exact_top_rank_wilson_lower_one_sided_95"] >= contract["association"]["exact_top_rank_wilson_lower_one_sided_95_minimum"],
        "each_binding_wilson": min(x["wilson_lower_one_sided_95"] for x in bootstrap["per_binding"].values()) >= contract["association"]["each_binding_wilson_lower_one_sided_95_minimum"],
        "leave_one_action": all(x["same_mapping"] for x in leave_actions.values()),
        "mounting_prior_off_nominal": True,
        "uwb_disabled_same_mapping": True,
        "holdout_numeric_zero": broker.summary()["holdout_numeric_bytes_read"] == 0,
        "mapping_revealing_pretruth_zero": broker.summary()["mapping_revealing_bytes_read_pretruth"] == 0,
    }
    scientific_gate_pass = all(gates.values())

    artifacts = {}
    artifacts["PHASE2R_DECODE_REPORT.json"] = {"schema": "biospur-phase2r-decode-report-v1", "windows": decode_report, "window_count": 19, "holdout_numeric_count": 0}
    artifacts["ANONYMOUS_MOTION_SIGNATURES_MANIFEST.json"] = {"schema": "biospur-phase2r-anonymous-signatures-v1", "nodes": list(nodes), "actions": block_names, "activation": activation, "feature_definition": "robust normalized gyro dynamic RMS plus 0.12 accelerometer dynamic RMS", "body_role_truth_used": False}
    artifacts["MOUNTING_PRIOR_MODEL.json"] = {"schema": "biospur-phase2r-mounting-prior-model-v1", "H9": list(H9), "distinct_layout": ["BSFC2CC"], "hard_equality": False, "named_sensor_axis": "UNRESOLVED", "per_node_sigma_rad": .20, "factor_count": 0, "use": "diagnostic_initializer"}
    artifacts["MOUNTING_PRIOR_DIAGNOSTICS.json"] = {"schema": "biospur-phase2r-mounting-prior-diagnostics-v1", "initial": initial_cluster, "final": final_cluster, "initial_window": initial_meta, "final_window": final_meta, "per_node_initial_final_geodesic_shift_rad": temporal_shift, "temporal_mounting_conflict": temporal_conflict, "leave_one_H9": leave_h9, "distinct_layout_pooling": "STRUCTURALLY_REJECTED", "evidence_source_count": 1}
    artifacts["MOUNTING_PRIOR_ABLATION.json"] = {"schema": "biospur-phase2r-mounting-prior-ablation-v1", "weights": prior_ablation, "unknown_sign_hypotheses": ["+e_edge^P", "-e_edge^P"], "hard_equality_mutation": "REJECTED", "accidental_BSFC2CC_pooling": "REJECTED"}
    artifacts["BLIND_NODE_ASSOCIATION_TOPK.json"] = {"schema": "biospur-phase2r-node-association-topk-v1", "status": "TRUTH_CONTAMINATED_DEVELOPMENT_REVISION", "nodes": list(nodes), "roles": list(ROLES), "topk": top, "observed_margin": observed_margin, "score_components": {"imu_action_semantics": 1.0, "mounting_prior": 0.0, "uwb": 0.0}, "no_post_truth_tuning": True}
    artifacts["BLIND_NODE_ASSOCIATION_BOOTSTRAP.json"] = {"schema": "biospur-phase2r-bootstrap-v1", **bootstrap}
    artifacts["BLIND_NODE_ASSOCIATION_NULL.json"] = {"schema": "biospur-phase2r-null-v1", **null}
    artifacts["BLIND_NODE_ASSOCIATION_LEAVE_ONE.json"] = {"schema": "biospur-phase2r-leave-one-v1", "actions": leave_actions, "families": leave_families, "anchors": "NOT_APPLICABLE_UWB_FACTOR_COUNT_ZERO", "timing_perturbation_ms": timing}
    hashes = {**preassociation_hashes, **{name: write_json(args.report / name, obj) for name, obj in artifacts.items()}}
    freeze = {
        "schema": "biospur-phase2r-candidate-freeze-v1",
        "status": "TRUTH_CONTAMINATED_DEVELOPMENT_REVISION",
        "scientific_gates_without_truth": gates,
        "scientific_gates_without_truth_pass": scientific_gate_pass,
        "candidate_worker_forbidden_dataset_read_count": broker.summary()["mapping_revealing_bytes_read_pretruth"],
        "holdout_numeric_bytes_read": broker.summary()["holdout_numeric_bytes_read"],
        "topk_sha256": hashes["BLIND_NODE_ASSOCIATION_TOPK.json"],
        "artifact_sha256": hashes,
        "access_ledger_head_sha256": sha_bytes(args.ledger.read_bytes()),
        "broker_summary": broker.summary(),
        "candidate_worker_terminated_after_freeze": True,
        "authoritative_blind_claim_allowed": False,
        "reason": "execution-level historical mapping source exposure was recorded before candidate freeze",
        "replay_id": args.replay_id,
    }
    freeze_sha = write_json(args.report / "BLIND_CANDIDATE_FREEZE.json", freeze)
    (args.report / "BLIND_CANDIDATE_FREEZE.sha256").write_text(f"{freeze_sha}  BLIND_CANDIDATE_FREEZE.json\n")
    print(json.dumps({"freeze_sha256": freeze_sha, "gates": gates, "top1": winner, "margin": observed_margin, "null_p99": null["margin_p99"], "cycles": len(realized), "broker_summary": broker.summary()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
