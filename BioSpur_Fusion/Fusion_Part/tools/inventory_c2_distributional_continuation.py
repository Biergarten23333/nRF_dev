#!/usr/bin/env python3
"""Freeze the distributional-only C2 continuation boundary.

This tool is intentionally read-only with respect to all scientific state.  It
binds existing training-only artifacts and current owner/test sources into one
compact inventory, then records the two missing authorities that are required
before a single fixed-geometry endpoint avatar can be rendered honestly.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
OUT = SPRINT / "C2_DISTRIBUTIONAL_CONTINUATION_INVENTORY_001"

FRESH_MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
FRESH_NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
FRESH_GATE = SPRINT / "C2_FRESH_CONTINUATION_GATE_003.json"
FRESH_FOCUSED_GATE = SPRINT / "C2_FRESH_CONTINUATION_FOCUSED_TEST_GATE_002.json"
CURVE_DATA = (
    SPRINT
    / "C2_FRESH_CAUSAL_PROGRESSIVE_CURVES_002"
    / "FRESH_CAUSAL_19_PREFIX_CURVE_DATA.json"
)
CURVE_AUDIT = (
    SPRINT
    / "C2_FRESH_CAUSAL_PROGRESSIVE_CURVES_002"
    / "FRESH_CAUSAL_19_PREFIX_PROGRESS_PREQUENTIAL_AUDIT.json"
)
NONHINGE_AUDIT = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002" / "AUDIT.json"
NONHINGE_NPZ = (
    SPRINT
    / "C2_NONHINGE_JOINT_RAO_REPLAY_002"
    / "CORRECTED_PREFIX_NONHINGE_STATE.npz"
)
DISTAL_AUDIT = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_002" / "AUDIT.json"
DISTAL_NPZ = (
    SPRINT
    / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_002"
    / "COMPLETE_S1_DISTAL_LONGITUDINAL_STATE.npz"
)
HEADING_COORDINATE_AUDIT = (
    SPRINT / "C2_HEADING_FRAME_COORDINATE_MIGRATION_BOUNDARY_AUDIT_001" / "AUDIT.json"
)
SPINE_CLOSURE_AUDIT = (
    SPRINT / "C2_FIXED_LANDMARK_PROXY_SPINE_OWNER_CLOSURE_001" / "AUDIT.json"
)

SOURCE_PATHS = {
    "inventory_owner": WORKSPACE / "tools/inventory_c2_distributional_continuation.py",
    "causal_progressive_owner": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/progressive.py",
    "nonhinge_heading_owner": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/nonhinge_heading.py",
    "persistent_qmt_heading_owner": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/heading.py",
    "segment_frame_branch_owner": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/segment_frames.py",
    "complete_s1_distal_owner": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/distal_longitudinal.py",
    "scientific_fk_and_viewer_guard": WORKSPACE / "src/biospur_fusion/v0/c2_progressive/scientific_fk.py",
}
TEST_PATHS = {
    "complete_s1_distal_tests": WORKSPACE / "tests/v0/test_c2_distal_longitudinal.py",
    "nonhinge_joint_nuisance_tests": WORKSPACE / "tests/v0/test_c2_nonhinge_heading.py",
    "frame_and_heading_boundary_tests": WORKSPACE / "tests/v0/test_c2_p2_prefit_owners.py",
}

PROGRESSIVE_LEAVES = {
    "branch_weights",
    "data_information",
    "data_information_nonzero_eigenvalues",
    "measurement_statistical_covariance",
    "posterior_covariance",
    "posterior_mean",
    "prequential_and_information_scalars",
    "prequential_prior_branch_weights",
    "prequential_prior_covariance",
    "prequential_prior_mean",
    "shared_systematic_covariance",
    "statistical_accumulator_covariance",
    "statistical_accumulator_mean",
    "temporal_migration_covariance",
}
NONHINGE_LEAVES = {
    "action_log_likelihood",
    "delta_grid_rad",
    "heading_information",
    "independent_covariance_logdet",
    "nuisance_normal_j_t_w_j",
    "nuisance_score_j_t_w_r",
    "posterior_weights",
    "residual_quadratic_r_t_w_r",
    "shared_nuisance_covariance",
    "shared_nuisance_reference_mean",
    "shared_nuisance_score",
}
DISTAL_LEAVES = {
    "candidate_sensor_from_segment",
    "delta_grid_rad",
    "full_r3_child_sensor_to_joint_m",
    "full_r3_parent_sensor_to_joint_m",
    "posterior_weights",
    "prior_weights",
    "signed_child_hinge_axis_sensor",
    "signed_parent_hinge_axis_sensor",
}


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    digest = hashlib.sha256()
    digest.update(array.dtype.str.encode("ascii"))
    digest.update(json.dumps(array.shape, separators=(",", ":")).encode("ascii"))
    digest.update(array.view(np.uint8))
    return digest.hexdigest()


def _group_sha256(items: Iterable[tuple[str, np.ndarray]]) -> str:
    digest = hashlib.sha256()
    for key, value in sorted(items):
        digest.update(key.encode("utf-8"))
        digest.update(_array_sha256(value).encode("ascii"))
    return digest.hexdigest()


def _binding(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    return {
        "path": str(path),
        "sha256": _sha256(path),
        "size_bytes": path.stat().st_size,
    }


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise TypeError(f"{path}: expected a JSON object")
    return value


def _write_new(path: Path, text: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o444)


def _progressive_inventory(
    manifest: Mapping[str, Any], arrays: Mapping[str, np.ndarray]
) -> list[Mapping[str, Any]]:
    rows = manifest["structure"]["progressive_prefixes"]
    if len(rows) != 19:
        raise ValueError("fresh causal prefix count is not 19")
    inventory = []
    for expected_index, row in enumerate(rows):
        if int(row["chronological_index"]) != expected_index:
            raise ValueError("causal prefix chronology is not contiguous")
        prefix = f"progressive_prefix/{expected_index:02d}/"
        keys = sorted(key for key in arrays if key.startswith(prefix))
        leaves = {key.removeprefix(prefix) for key in keys}
        if leaves != PROGRESSIVE_LEAVES:
            raise ValueError(
                f"{prefix}: leaves differ; missing={sorted(PROGRESSIVE_LEAVES - leaves)}, "
                f"extra={sorted(leaves - PROGRESSIVE_LEAVES)}"
            )
        values = [np.asarray(arrays[key]) for key in keys]
        if not all(np.all(np.isfinite(value)) for value in values):
            raise ValueError(f"{prefix}: nonfinite causal state")
        eigenvalues = np.asarray(arrays[prefix + "data_information_nonzero_eigenvalues"])
        if len(eigenvalues) != int(row["data_information_rank"]):
            raise ValueError(f"{prefix}: rank metadata differs from array support")
        branch_weights = np.asarray(arrays[prefix + "branch_weights"], dtype=float)
        if len(branch_weights) != len(row["branch_ids"]):
            raise ValueError(f"{prefix}: branch support length differs")
        inventory.append(
            {
                "chronological_index": expected_index,
                "action": row["action"],
                "data_information_rank": int(row["data_information_rank"]),
                "prequential_observed_rank": int(row["prequential_observed_rank"]),
                "prequential_status": row["prequential_status"],
                "branch_count": len(branch_weights),
                "maximum_branch_weight": float(np.max(branch_weights)),
                "prequential_prior_covariance_trace": float(
                    np.trace(arrays[prefix + "prequential_prior_covariance"])
                ),
                "posterior_covariance_trace": float(
                    np.trace(arrays[prefix + "posterior_covariance"])
                ),
                "bound_array_count": len(keys),
                "bound_arrays_sha256": _group_sha256(
                    (key, arrays[key]) for key in keys
                ),
            }
        )
    if inventory[0]["data_information_rank"] != 0:
        raise ValueError("initial still is no longer zero-information")
    return inventory


def _frame_covariance_inventory(arrays: Mapping[str, np.ndarray]) -> Mapping[str, Any]:
    keys = sorted(
        key
        for key in arrays
        if key.startswith("frames/")
        and (
            "/frame_covariance/" in key
            or key.endswith("/joint_frame_covariance")
            or "/paired_hinge_frame_covariance/" in key
        )
    )
    if not keys:
        raise ValueError("no frozen frame covariance arrays")
    if not all(np.all(np.isfinite(arrays[key])) for key in keys):
        raise ValueError("nonfinite frozen frame covariance")
    branch_ids = {key.split("/", 2)[1] for key in keys}
    return {
        "terminal_frame_branch_count": len(branch_ids),
        "frame_covariance_array_count": sum("/frame_covariance/" in key for key in keys),
        "joint_frame_covariance_array_count": sum(
            key.endswith("/joint_frame_covariance") for key in keys
        ),
        "paired_hinge_frame_covariance_array_count": sum(
            "/paired_hinge_frame_covariance/" in key for key in keys
        ),
        "all_covariance_arrays_finite": True,
        "covariance_arrays_sha256": _group_sha256((key, arrays[key]) for key in keys),
        "qualification_boundary": (
            "PRESERVED_HISTORICAL_FRAME_COVARIANCE; DISTAL FRAME MEANS DO NOT "
            "QUALIFY WRIST_OR_ANKLE POINT POSE"
        ),
    }


def _nonhinge_inventory(audit: Mapping[str, Any], npz_path: Path) -> Mapping[str, Any]:
    with np.load(npz_path, allow_pickle=False) as arrays:
        keys = list(arrays.files)
        prefixes = {key.split("/", 2)[1] for key in keys if key.startswith("nonhinge_heading/")}
        leaves = {key.rsplit("/", 1)[-1] for key in keys}
    if prefixes != {f"{index:02d}" for index in range(19)}:
        raise ValueError("nonhinge posterior does not bind all 19 prefixes")
    if leaves != NONHINGE_LEAVES:
        raise ValueError(
            f"nonhinge leaves differ; missing={sorted(NONHINGE_LEAVES - leaves)}, "
            f"extra={sorted(leaves - NONHINGE_LEAVES)}"
        )
    final_reports = audit["action_rows"][-1]["nonhinge_reports"]
    final_by_edge: dict[str, dict[str, list[float]]] = {}
    for entry in final_reports:
        report = entry["report"]
        edge = str(entry["edge"])
        row = final_by_edge.setdefault(edge, {"resultants": [], "information": []})
        row["resultants"].append(float(report["circular_resultant_magnitude"]))
        row["information"].append(float(report["information_gain_from_uniform_nats"]))
    edge_summary = {}
    for edge, values in sorted(final_by_edge.items()):
        resultants = np.asarray(values["resultants"])
        information = np.asarray(values["information"])
        edge_summary[edge] = {
            "retained_branch_report_count": len(resultants),
            "resultant_min": float(np.min(resultants)),
            "resultant_max": float(np.max(resultants)),
            "information_gain_nats_min": float(np.min(information)),
            "information_gain_nats_max": float(np.max(information)),
            "identical_across_retained_hinge_branches": bool(
                np.all(resultants == resultants[0])
                and np.all(information == information[0])
            ),
        }
    return {
        "prefix_count": len(prefixes),
        "array_count": len(keys),
        "persisted_leaf_schema": sorted(leaves),
        "final_edge_distribution_summary": edge_summary,
        "shared_nuisance_treatment": audit["shared_nuisance_treatment"],
        "covariance_owner": audit["covariance_owner"],
        "hard_argmax_or_threshold_relaxation_used": audit[
            "hard_argmax_or_threshold_relaxation_used"
        ],
        "qualified_as_pose_posterior": audit["owner_output_qualified_as_pose_posterior"],
        "legal_distributional_use": (
            "FULL_S1_PREFIX POSTERIOR, SUFFICIENT STATISTICS, AND COVARIANCE MAY "
            "BE PROPAGATED WITHOUT HARD LOCK"
        ),
        "forbidden_collapse": (
            "NO CIRCULAR MEAN, MAP, ARGMAX, ZERO-HEADING, OR PIXEL-SELECTED POSE "
            "FOR WEAK OR MULTIMODAL EDGES"
        ),
    }


def _distal_inventory(audit: Mapping[str, Any], npz_path: Path) -> Mapping[str, Any]:
    with np.load(npz_path, allow_pickle=False) as arrays:
        keys = list(arrays.files)
        leaves = {key.rsplit("/", 1)[-1] for key in keys}
        groups = sorted({key.rsplit("/", 1)[0] for key in keys})
        if leaves != DISTAL_LEAVES:
            raise ValueError(
                f"distal leaves differ; missing={sorted(DISTAL_LEAVES - leaves)}, "
                f"extra={sorted(leaves - DISTAL_LEAVES)}"
            )
        unchanged = all(
            np.array_equal(
                arrays[group + "/prior_weights"], arrays[group + "/posterior_weights"]
            )
            for group in groups
        )
        cell_counts = {
            len(np.asarray(arrays[group + "/delta_grid_rad"])) for group in groups
        }
    conclusions = audit["conclusions"]
    return {
        "owner_posterior_count": len(groups),
        "retained_hinge_branch_count": int(audit["retained_hinge_branch_count"]),
        "distal_segment_count": int(audit["distal_segment_count"]),
        "candidate_cell_counts": sorted(cell_counts),
        "array_count": len(keys),
        "persisted_leaf_schema": sorted(leaves),
        "prior_and_posterior_weights_byte_identical_for_all_owners": unchanged,
        "candidate_dependent_motion_information_gain_nats": conclusions[
            "candidate_dependent_motion_information_gain_nats"
        ],
        "complete_motion_identifies_distal_longitudinal_direction": conclusions[
            "complete_motion_identifies_distal_longitudinal_direction"
        ],
        "distal_endpoint_pose_ready": conclusions["distal_endpoint_pose_ready"],
        "legal_distributional_use": (
            "KEEP COMPLETE_S1 CANDIDATE FRAMES, BROAD REGISTERED PRIOR, FULL_R3 "
            "LEVERS, AND COVARIANCE FACTORIZED PER DISTAL SEGMENT"
        ),
        "forbidden_collapse": (
            "NO MEAN, MAP, MEDOID, LEGACY EIGHT-POINT SELECTION, OR SINGLE "
            "WRIST/ANKLE ENDPOINT"
        ),
    }


def _authority_requests() -> list[Mapping[str, Any]]:
    return [
        {
            "authority_id": "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING_R3",
            "why_required": (
                "The existing full-R3 functional center means are uncertain sensor-to-joint "
                "evidence and collapse the display core; the registered 0.280 m value is only "
                "a surface sensor-to-sensor scalar and cannot own a spine vector."
            ),
            "minimum_fields": [
                "named viewer-only/non-anatomical mapping or profile identifier",
                "explicit source and destination node names (at least hip-mid proxy and shoulder-mid proxy)",
                "3D vector or distribution in a named right-handed coordinate frame, with metres as units",
                "frame transform or landmark-to-viewer mapping needed to express that vector in the rendered frame",
                "nonzero covariance or bounded sensitivity alternatives, including correlations where known",
                "observer/acquisition provenance and side/profile identity; alternatives remain separate",
                "explicit prohibition on use in fit, QMT, functional-center estimation, branch likelihood, or scientific acceptance",
            ],
            "acceptable_measurement_forms": [
                "existing or new external 3D landmark digitization/optical tracking that records pelvis/hip-mid and shoulder-mid proxy landmarks in one calibrated frame",
                "calibrated multi-view photogrammetry, structured-light scan, or other 3D surface capture with sensor origins and the declared graphical landmarks registered together",
                "an explicit user-authorized viewer-only 3D skeleton profile with named nodes, coordinate convention, provenance, and uncertainty/sensitivity alternatives",
            ],
            "not_acceptable": [
                "0.280 m pelvis-surface-IMU to thorax-surface-IMU distance promoted to torso or spine +Z",
                "biacromial or bitrochanteric breadth promoted to internal joint truth",
                "functional center mean substituted as fixed display crossbar/spine",
                "camera, pixel, pose-label, or action-label tuning",
            ],
        },
        {
            "authority_id": "SIDE_SPECIFIC_DISTAL_LONGITUDINAL_OR_MOUNT_DIRECTION_EVIDENCE_R3",
            "why_required": (
                "For each forearm and shank, one proximal joint lever plus the hinge axis leaves "
                "a complete S1 coordinate gauge. All 19 training actions add exactly zero "
                "candidate-dependent information under the owned motion model."
            ),
            "segments": ["forearm_left", "forearm_right", "shank_left", "shank_right"],
            "minimum_fields": [
                "side-specific segment identifier and immutable evidence identifier",
                "proximal-to-distal longitudinal direction or sensor-to-segment rotation distribution in a named sensor/segment coordinate convention",
                "a signed 3D direction/rotation support with covariance or explicit multimodal alternatives",
                "measurement units, handedness, axis definitions, timestamp/static-trial association where applicable, and acquisition provenance",
                "independence from the sole sensor-to-elbow/knee lever and from action/pose labels",
                "no silent left/right equality, midpoint, sign choice, or exact surface-normal claim",
            ],
            "acceptable_measurement_forms": [
                "as-built 3D mount survey, calibrated jig, CAD-to-device registration, or 3D scan that measures the IMU package orientation relative to a declared forearm/shank landmark frame, with placement uncertainty",
                "external 3D digitization or optical-marker observation of elbow-to-wrist and knee-to-ankle directions together with the sensor frame in the same static calibration frame",
                "a second independent non-collinear landmark, lever, marker, or sensor observation that breaks the rotation-about-hinge gauge, with uncertainty",
                "already-recorded synchronized external marker/landmark evidence that supplies the same side-specific 3D relation without a raw C2 fit rerun",
            ],
            "not_acceptable": [
                "normalizing the sole sensor-to-elbow or sensor-to-knee lever as the bone axis",
                "hinge axis plus motion evidence alone under the proven gauge-invariant model",
                "legacy fixed eight-point support, a posterior mean/MAP/medoid, manual flip, ROM prior, pose label, or pixel choice",
            ],
        },
    ]


def main() -> None:
    if OUT.exists():
        raise FileExistsError(f"append-only output already exists: {OUT}")

    manifest = _load_json(FRESH_MANIFEST)
    fresh_gate = _load_json(FRESH_GATE)
    curve_audit = _load_json(CURVE_AUDIT)
    nonhinge_audit = _load_json(NONHINGE_AUDIT)
    distal_audit = _load_json(DISTAL_AUDIT)
    heading_coordinate_audit = _load_json(HEADING_COORDINATE_AUDIT)
    spine_closure_audit = _load_json(SPINE_CLOSURE_AUDIT)

    if manifest["heldout_opened"] or fresh_gate["heldout_opened"]:
        raise ValueError("heldout was opened in a bound fresh artifact")
    if curve_audit["retrospective_qmt_or_viewer_arrays_consumed"]:
        raise ValueError("causal curve audit consumed retrospective arrays")
    if nonhinge_audit["heldout_opened"] or distal_audit["execution"]["heldout_opened"]:
        raise ValueError("heldout was opened in a derived distributional artifact")
    if not heading_coordinate_audit["owner_boundary"][
        "affected_processed_edge_frame_coordinate_change_is_rejected"
    ]:
        raise ValueError("heading frame-coordinate update guard is not active")
    if spine_closure_audit["fixed_viewer_spine_owner_closure"][
        "render_authorized_by_this_closure"
    ]:
        raise ValueError("graphical-spine closure unexpectedly authorizes rendering")

    with np.load(FRESH_NPZ, allow_pickle=False) as fresh_arrays:
        progressive = _progressive_inventory(manifest, fresh_arrays)
        frame_covariance = _frame_covariance_inventory(fresh_arrays)

    artifacts = {
        "fresh_frozen_manifest": _binding(FRESH_MANIFEST),
        "fresh_frozen_npz": _binding(FRESH_NPZ),
        "fresh_training_only_equivalence_gate": _binding(FRESH_GATE),
        "fresh_focused_gate": _binding(FRESH_FOCUSED_GATE),
        "fresh_causal_curve_data": _binding(CURVE_DATA),
        "fresh_causal_curve_audit": _binding(CURVE_AUDIT),
        "nonhinge_joint_rao_audit": _binding(NONHINGE_AUDIT),
        "nonhinge_joint_rao_npz": _binding(NONHINGE_NPZ),
        "distal_complete_s1_audit": _binding(DISTAL_AUDIT),
        "distal_complete_s1_npz": _binding(DISTAL_NPZ),
        "heading_frame_coordinate_audit": _binding(HEADING_COORDINATE_AUDIT),
        "graphical_spine_closure_audit": _binding(SPINE_CLOSURE_AUDIT),
    }
    sources = {name: _binding(path) for name, path in SOURCE_PATHS.items()}
    tests = {name: _binding(path) for name, path in TEST_PATHS.items()}

    owners = [
        {
            "owner": "ProgressiveCalibrationState",
            "source_binding": "causal_progressive_owner",
            "legal_now": "preserve/re-evaluate chronological pre-ingest priors, information, covariance, and branch weights over 19 prefixes",
            "distributional_output": "70D posterior/prior covariance, information matrix/rank, systematic/statistical/temporal covariance, 16 branch weights",
            "forbidden": "retrospective QMT/viewer arrays entering causal metrics; causal point-pose claim",
        },
        {
            "owner": "PersistentNonhingeHeadingLikelihoodOwner",
            "source_binding": "nonhinge_heading_owner",
            "legal_now": "propagate full 360-cell time-varying circular posteriors with one capture-wide conditional Gaussian nuisance state",
            "distributional_output": "per-prefix S1 weights plus A, b, q, logdet, nuisance covariance/reference/score, and heading information",
            "forbidden": "mean/MAP/argmax/zero-heading point pose for broad or multimodal edges",
        },
        {
            "owner": "CompleteS1DistalLongitudinalOwner",
            "source_binding": "complete_s1_distal_owner",
            "legal_now": "retain factorized 360-cell distal coordinate support, full-R3 levers, hinge axes, prior weights, and uncertainty",
            "distributional_output": "four side-specific distal S1 distributions per retained hinge branch; motion likelihood proven constant",
            "forbidden": "single forearm/shank longitudinal axis or wrist/ankle endpoint",
        },
        {
            "owner": "PersistentHeadingOwner",
            "source_binding": "persistent_qmt_heading_owner",
            "legal_now": "preserve official QMT filtered traces/covariance on one unchanged frame-coordinate history and explicit local no-updates",
            "distributional_output": "edge-local filtered coordinate/variance traces; rooted uncertainty when all consumed coordinate owners are coherent",
            "forbidden": "silent frame-coordinate update after edge state; mixing fixed-prefix15 debug and earlier progressive QMT histories",
        },
        {
            "owner": "SegmentFrameBranchOwner",
            "source_binding": "segment_frame_branch_owner",
            "legal_now": "retain full-R3 centers/covariance, hinge sign branches, paired-frame covariance, and hinge-QMT readiness separately from endpoint readiness",
            "distributional_output": "frame branches and covariance; unresolved distal support only as broad/legacy diagnostic envelope",
            "forbidden": "sole center lever as distal longitudinal axis; legacy eight-point support as active posterior or pose selector",
        },
        {
            "owner": "ScientificForwardKinematicsOwner / viewer firewall",
            "source_binding": "scientific_fk_and_viewer_guard",
            "legal_now": "keep sensor origins, full-R3 joint evidence, and viewer geometry ownership separate; reject missing graphical-spine authority",
            "distributional_output": "no new point avatar until both requested authorities exist",
            "forbidden": "sensor origin as joint; 0.280 m surface scalar as spine/torso-Z; fan/proxy as delivery",
        },
    ]

    audit = {
        "schema": "biospur-c2-distributional-continuation-inventory-v1",
        "created_local": _now(),
        "authority_sources": {
            "direct_user_objective_thread": "01a03f71-e481-7e21-84f0-3c6cbeb58291",
            "relay_and_independent_monitor_thread": "01a04d0f-58f1-7240-b72f-3bf5b44a2156",
        },
        "execution": {
            "raw_payload_reread": False,
            "fit_progressive_qmt_tree_or_likelihood_replay_rerun": False,
            "heldout_opened": False,
            "new_pixels_generated": False,
            "scientific_state_mutated": False,
            "seal_or_activation_created": False,
            "scientific_acceptance_pass": False,
            "tuned_pose_pass": False,
        },
        "artifact_bindings": artifacts,
        "source_bindings": sources,
        "test_source_bindings": tests,
        "focused_verification_command": (
            "PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:tools .venv-v0/bin/python -B "
            "-m pytest -p no:cacheprovider -q tests/v0/test_c2_nonhinge_heading.py "
            "tests/v0/test_c2_distal_longitudinal.py "
            "tests/v0/test_c2_p2_prefit_owners.py::"
            "test_full_frame_owner_preserves_hinge_qmt_but_not_unresolved_distal_pose "
            "tests/v0/test_c2_p2_prefit_owners.py::"
            "test_heading_owner_rejects_silent_frame_coordinate_change_after_edge_state"
        ),
        "causal_progressive_state": {
            "prefix_count": len(progressive),
            "initial_still_zero_data_information": progressive[0]["data_information_rank"] == 0,
            "retrospective_qmt_or_viewer_arrays_consumed": False,
            "fresh_equivalence_gate_pass": bool(fresh_gate["fresh_raw_gate_pass"]),
            "rows": progressive,
        },
        "heading_and_frame_covariance_state": frame_covariance,
        "nonhinge_heading_distribution": _nonhinge_inventory(nonhinge_audit, NONHINGE_NPZ),
        "distal_longitudinal_distribution": _distal_inventory(distal_audit, DISTAL_NPZ),
        "heading_frame_coordinate_boundary": heading_coordinate_audit["owner_boundary"],
        "graphical_spine_boundary": spine_closure_audit["fixed_viewer_spine_owner_closure"],
        "owner_capability_inventory": owners,
        "attention_required": True,
        "attention_request_scope": (
            "ONLY THE TWO NEW AUTHORITIES BELOW; NO THRESHOLD, ALGORITHM, BRANCH, "
            "POSE, OR PIXEL DECISION IS REQUESTED"
        ),
        "authority_requests": _authority_requests(),
        "resume_contract_after_authority": {
            "starting_state": str(FRESH_NPZ),
            "preserve_existing_causal_metrics": True,
            "preserve_full_nonhinge_and_distal_distributions": True,
            "first_action": "bind new authority inputs append-only and validate coordinate/provenance contracts",
            "then": "continue derived coherent-frame distributional QMT/tree/FK without raw fit reset",
        },
    }

    authority = audit["authority_requests"]
    markdown = f"""# C2 distributional continuation inventory

Created: `{audit['created_local']}`

This checkpoint preserves the completed training-only state without producing a point pose. Held-out access remains closed; no raw payload, fit, progressive state, QMT/tree replay, or viewer was run.

## What remains runnable without collapsing an unobservable gauge

| Owner | Legal distributional operation | Point collapse that remains forbidden |
|---|---|---|
| `ProgressiveCalibrationState` | Preserve/re-evaluate all 19 causal pre-ingest priors, 70D information/covariance, and branch weights. | Retrospective viewer/QMT input in causal metrics; causal point-pose claim. |
| `PersistentNonhingeHeadingLikelihoodOwner` | Carry each edge's complete 360-cell time-varying circular posterior and Rao shared-nuisance sufficient statistics. | Mean/MAP/argmax/zero heading for weak or multimodal edges. |
| `CompleteS1DistalLongitudinalOwner` | Carry factorized complete-S1 support, full-R3 levers, hinge axes, covariance, and broad priors. | A single forearm/shank axis or wrist/ankle endpoint. |
| `PersistentHeadingOwner` | Preserve official filtered QMT traces only within one unchanged segment-frame coordinate history. | Silent frame-coordinate replacement or mixed fixed-prefix/progressive histories. |
| `SegmentFrameBranchOwner` | Preserve full-R3 center covariance, hinge branches, frame covariance, and hinge-QMT readiness separately from endpoint readiness. | Sole-lever distal axis; legacy eight-point quadrature as posterior/selector. |
| FK/viewer firewall | Keep sensor origins, uncertain functional joints, and viewer geometry as separate owners. | 0.280 m surface scalar as spine/torso-Z; sensor origin as joint; fan/proxy as delivery. |

Bound state: 19 causal prefixes; initial still rank 0; {frame_covariance['terminal_frame_branch_count']} terminal frame branches with {frame_covariance['frame_covariance_array_count'] + frame_covariance['joint_frame_covariance_array_count'] + frame_covariance['paired_hinge_frame_covariance_array_count']} covariance arrays; 19-prefix nonhinge full-S1 state; {audit['distal_longitudinal_distribution']['owner_posterior_count']} distal complete-S1 owners. Scientific and tuned-pose PASS remain false.

## Attention request: exactly two new authorities

### 1. `{authority[0]['authority_id']}`

Acceptable evidence is one of: (a) 3D landmark digitization/optical tracking in a common calibrated frame; (b) calibrated multi-view/structured-light 3D surface capture registering sensor origins and declared graphical nodes; or (c) an explicit user-authorized viewer-only 3D skeleton profile. It must name the hip-mid and shoulder-mid proxy nodes, coordinate frame/handedness, metre-valued vector or distribution, mapping transform, provenance, and nonzero covariance or separate sensitivity alternatives. It remains viewer-only and non-anatomical.

The 0.280 m pelvis-surface-sensor to thorax-surface-sensor scalar, surface breadths, and functional-center means are not acceptable substitutes for a 3D graphical spine.

### 2. `{authority[1]['authority_id']}`

For each of left/right forearm and shank, acceptable evidence is one of: (a) an as-built 3D mount survey/jig/CAD/scan registration with placement uncertainty; (b) 3D digitization or optical-marker evidence tying elbow-to-wrist or knee-to-ankle direction to the sensor frame; (c) a second independent non-collinear landmark/lever/marker/sensor observation; or (d) equivalent already-recorded synchronized external evidence. It must preserve side-specific signed 3D support, coordinate convention, provenance, and covariance/multimodal alternatives.

The sole proximal joint lever, hinge axis plus the proved gauge-invariant motion residual, legacy eight-point support, manual sign, ROM/action label, and pixels are not acceptable evidence.

## Immediate resume point

When both authorities are supplied, bind them append-only to `{FRESH_NPZ}`, validate coordinates and uncertainty, then continue the coherent-frame distributional QMT/tree/FK path. Do not reset or rerun the raw 19-prefix fit.
"""

    OUT.mkdir(parents=True, exist_ok=False)
    audit_path = OUT / "AUDIT.json"
    inventory_path = OUT / "OWNER_INVENTORY_AND_ATTENTION_REQUEST.md"
    _write_new(audit_path, json.dumps(audit, indent=2, sort_keys=True, allow_nan=False) + "\n")
    _write_new(inventory_path, markdown)
    sums = "".join(
        f"{_sha256(path)}  {path.name}\n" for path in (audit_path, inventory_path)
    )
    sums_path = OUT / "SHA256SUMS.txt"
    _write_new(sums_path, sums)
    OUT.chmod(0o555)
    print(
        json.dumps(
            {
                "execution_complete": True,
                "scientific_acceptance_pass": False,
                "tuned_pose_pass": False,
                "heldout_opened": False,
                "output_directory": str(OUT),
                "audit": _binding(audit_path),
                "inventory_and_attention_request": _binding(inventory_path),
                "sha256sums": _binding(sums_path),
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
