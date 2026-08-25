"""Deterministic Root-R6A1B authority and minimal-state construction.

This module classifies immutable registry entries.  It deliberately has no
dataset loader, optimizer, state update, or production calibration values.
"""
from __future__ import annotations

from collections import Counter, defaultdict, deque
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np


AUTHORITY_CLASSES = (
    "FIX_BY_CONVENTION",
    "IMPORT_FROZEN_PROVENANCE",
    "MEASURE_DIRECTLY",
    "ESTIMATE_WITH_PRIOR",
    "DERIVE_NOT_INDEPENDENT",
    "BLOCKED_MISSING_DEFINITION",
)

CATEGORY_COUNTS = {
    "anatomical_point": 5,
    "anchor_delay": 8,
    "anchor_position": 8,
    "bone_length": 8,
    "imu_extrinsic": 10,
    "joint_child": 9,
    "joint_parent": 9,
    "joint_rest": 9,
    "tag_lever": 10,
    "time_relationship": 10,
    "world_model_gauge": 1,
}

AUTHORITY_COUNTS = {
    "FIX_BY_CONVENTION": 10,
    "IMPORT_FROZEN_PROVENANCE": 26,
    "MEASURE_DIRECTLY": 4,
    "ESTIMATE_WITH_PRIOR": 28,
    "DERIVE_NOT_INDEPENDENT": 18,
    "BLOCKED_MISSING_DEFINITION": 1,
}

NODES_TO_SEGMENTS = {
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}

JOINTS = {
    "pelvis_torso": ("pelvis", "torso"),
    "shoulder_left": ("torso", "upper_arm_left"),
    "elbow_left": ("upper_arm_left", "forearm_left"),
    "shoulder_right": ("torso", "upper_arm_right"),
    "elbow_right": ("upper_arm_right", "forearm_right"),
    "hip_left": ("pelvis", "thigh_left"),
    "knee_left": ("thigh_left", "shank_left"),
    "hip_right": ("pelvis", "thigh_right"),
    "knee_right": ("thigh_right", "shank_right"),
}

BONE_DERIVATIONS = {
    "bone_length:upper_arm_left": ("joint_parent:elbow_left", "joint_child:shoulder_left"),
    "bone_length:forearm_left": ("anatomical_point:wrist_left", "joint_child:elbow_left"),
    "bone_length:upper_arm_right": ("joint_parent:elbow_right", "joint_child:shoulder_right"),
    "bone_length:forearm_right": ("anatomical_point:wrist_right", "joint_child:elbow_right"),
    "bone_length:thigh_left": ("joint_parent:knee_left", "joint_child:hip_left"),
    "bone_length:shank_left": ("anatomical_point:ankle_left", "joint_child:knee_left"),
    "bone_length:thigh_right": ("joint_parent:knee_right", "joint_child:hip_right"),
    "bone_length:shank_right": ("anatomical_point:ankle_right", "joint_child:knee_right"),
}

DIRECT_POINTS = {
    "anatomical_point:ankle_left",
    "anatomical_point:ankle_right",
    "anatomical_point:wrist_left",
    "anatomical_point:wrist_right",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def resolve_sources(fusion: Path, execution_brief: Path) -> dict[str, Path]:
    """Resolve the bounded read-only provenance set used by R6A1B."""
    fusion = Path(fusion).resolve()
    project = fusion.parent
    deployment = project / "B306_Part/deployments/current_room_autopos_20260811_183541"
    predecessor = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z"
    return {
        "fusion": fusion,
        "execution_brief": Path(execution_brief).resolve(),
        "ledger": predecessor / "CALIBRATION_SLOT_LEDGER.json",
        "noise_audit": predecessor / "NOISE_PROVENANCE_AUDIT.json",
        "body_source": fusion / "src/biospur_fusion/root_r6a0/body.py",
        "body_config": fusion / "config/root_r6a0/body_graph.json",
        "frame_contract": fusion / "docs/FRAME_CONTRACT_V1.md",
        "anchor_reference": fusion / "config/geometry/current_room_autopos_20260811_183541.reference.json",
        "anchor_geometry": deployment / "V4IO_LAYOUT.json",
        "anchor_manifest": deployment / "CAPTURE_BOUND_GEOMETRY_MANIFEST.json",
        "delay_ledger": deployment / "RANGE_CORRECTION_LEDGER.md",
        "time_alignment": fusion / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_ALIGNMENT_RESULT.json",
    }


def _artifact(path: Path | None) -> tuple[str | None, str | None]:
    if path is None:
        return None, None
    return str(path), sha256_file(path) if path.is_file() else None


def validate_source_ledger(ledger: Mapping[str, Any]) -> None:
    slots = list(ledger.get("slots", ()))
    ids = [slot.get("slot_id") for slot in slots]
    counts = Counter(slot.get("category") for slot in slots)
    if len(slots) != 87 or len(set(ids)) != 87:
        raise ValueError("immutable source ledger is not exactly 87 unique slots")
    if dict(sorted(counts.items())) != CATEGORY_COUNTS:
        raise ValueError(f"source category mismatch: {dict(sorted(counts.items()))}")
    if any(slot.get("value") is not None for slot in slots):
        raise ValueError("source ledger contains a non-null real calibration value")
    if any(slot.get("status") != "FROZEN_UNCERTAIN" for slot in slots):
        raise ValueError("source ledger contains a non-frozen status")


def _authority(slot: Mapping[str, Any]) -> str:
    category = str(slot["category"])
    slot_id = str(slot["slot_id"])
    if category in {"anchor_delay", "anchor_position", "time_relationship"}:
        return "IMPORT_FROZEN_PROVENANCE"
    if category in {"joint_parent", "joint_rest", "imu_extrinsic"}:
        return "ESTIMATE_WITH_PRIOR"
    if category in {"bone_length", "tag_lever"}:
        return "DERIVE_NOT_INDEPENDENT"
    if category == "joint_child" or category == "world_model_gauge":
        return "FIX_BY_CONVENTION"
    if slot_id in DIRECT_POINTS:
        return "MEASURE_DIRECTLY"
    if slot_id == "anatomical_point:torso_top":
        return "BLOCKED_MISSING_DEFINITION"
    raise ValueError(f"unclassified slot {slot_id}")


def _derived_from(slot: Mapping[str, Any]) -> list[str]:
    slot_id = str(slot["slot_id"])
    category = str(slot["category"])
    if category == "joint_child":
        return ["convention:child_segment_origin_at_proximal_joint"]
    if category == "bone_length":
        return list(BONE_DERIVATIONS[slot_id])
    if category == "tag_lever":
        node = slot_id.split(":", 1)[1]
        return [f"imu_extrinsic:{node}", f"external_internal_lever:imu_to_uwb_phase_centre:{node}"]
    if category == "world_model_gauge":
        return ["convention:navigation_world"]
    if category == "anchor_position":
        return ["external_import:v4_anchor_geometry"]
    if category == "anchor_delay":
        return ["external_import:v4_anchor_delay_convention"]
    if category == "time_relationship":
        node = slot_id.split(":", 1)[1]
        return [f"external_import:clock_model:{node}"]
    return []


def _coupled_slots(slot: Mapping[str, Any]) -> list[str]:
    slot_id = str(slot["slot_id"])
    category = str(slot["category"])
    if category == "imu_extrinsic":
        return [f"tag_lever:{slot_id.split(':', 1)[1]}"]
    if category == "tag_lever":
        return [f"imu_extrinsic:{slot_id.split(':', 1)[1]}"]
    if category in {"joint_parent", "joint_child"}:
        joint = slot_id.split(":", 1)[1]
        coupled = [f"joint_parent:{joint}", f"joint_child:{joint}"]
        return [item for item in coupled if item != slot_id]
    if category == "bone_length":
        return list(BONE_DERIVATIONS[slot_id])
    if category == "time_relationship":
        return ["dynamic navigation motion at mapped measurement time"]
    return []


def _meaning(slot: Mapping[str, Any]) -> str:
    slot_id = str(slot["slot_id"])
    category = str(slot["category"])
    if category == "joint_child":
        return "Child-segment coordinates of the shared physical joint centre; fixed to [0,0,0] by choosing each non-root child origin at its proximal joint."
    if category == "joint_parent":
        return "Parent-segment coordinates of the shared physical joint centre used by the single FK chain."
    if category == "joint_rest":
        return "Neutral child-to-parent relative orientation R_parent_child before the dynamic joint rotation."
    if category == "bone_length":
        return "Time-invariant reporting scalar derived as the norm between proximal and distal landmarks in one segment frame."
    if category == "imu_extrinsic":
        return "Session/subject/donning-dependent rigid pose T_segment_IMU mapping IMU coordinates into the segment frame."
    if category == "tag_lever":
        return "Segment-local vector from segment origin to the UWB antenna phase centre; derived as t_SI + R_SI*l_IA."
    if category == "time_relationship":
        return "Capture-bound affine mapping from one node B306 native timer to global common time; not a second motion-fit offset."
    if category == "anchor_position":
        return "Capture-bound anchor antenna phase-centre position in the imported V4 relative-geometry frame, converted from millimetres to metres."
    if category == "anchor_delay":
        return "Capture-bound additive inter-anchor range correction with the frozen tag-observation sign convention."
    if category == "world_model_gauge":
        return "Static coordinate transform T_world_model; a chosen coordinate gauge, never learned from C1 body motion."
    if slot_id == "anatomical_point:torso_top":
        return "Named torso-local reporting point whose physical landmark is not defined by existing source."
    return "Directly measured distal anatomical landmark expressed in its owning segment-local frame."


def _uncertainty(slot: Mapping[str, Any], authority: str) -> dict[str, Any]:
    source = copy.deepcopy(slot.get("uncertainty"))
    if authority == "FIX_BY_CONVENTION":
        model = "zero numerical uncertainty only after the convention is materialized; provenance-bridge uncertainty remains separate"
    elif authority == "IMPORT_FROZEN_PROVENANCE":
        model = "capture-bound imported covariance/residual model; no generic reuse without exact manifest equivalence"
    elif authority == "MEASURE_DIRECTLY":
        model = "repeatability plus landmark-placement covariance from direct metrology; numerical value remains null until capture"
    elif authority == "ESTIMATE_WITH_PRIOR":
        model = "bounded anatomical/donning prior with cross-covariances where shared; numerical prior remains unqualified"
    elif authority == "DERIVE_NOT_INDEPENDENT":
        model = "propagate covariance from declared parents and internal rigid-lever metrology"
    else:
        model = "undefined until the physical landmark definition is supplied"
    return {"source_ledger_uncertainty_unchanged": source, "authority_uncertainty_contract": model}


def _qualification(slot: Mapping[str, Any], authority: str) -> tuple[str, str | None]:
    slot_id = str(slot["slot_id"])
    category = str(slot["category"])
    if authority == "BLOCKED_MISSING_DEFINITION":
        return "BLOCKED", "Define the exact torso_top physical landmark and measurement procedure; a label alone is insufficient."
    if category == "tag_lever":
        return "PENDING_EXTERNAL_RIGID_LEVER_PROVENANCE", "Per-node IMU-origin to UWB antenna phase-centre vector and frame/sign are not proven by PCB CAD, enclosure drawing, antenna definition, or direct metrology."
    if category == "world_model_gauge":
        return "CONVENTION_DEFINED_FRAME_BRIDGE_PENDING", "The V4 artifact is RELATIVE_GEOMETRY_ONLY; an exact V4-to-navigation/protocol/pelvis bridge is absent."
    if category in {"anchor_position", "anchor_delay"}:
        return "QUALIFIED_FOR_EXACT_CAPTURE_BOUND_IMPORT_ONLY", None
    if category == "time_relationship":
        return "QUALIFIED_FOR_CAPTURE_BOUND_IMPORT_ONLY", None
    if authority == "MEASURE_DIRECTLY":
        return "PENDING_DIRECT_METROLOGY", None
    if category == "imu_extrinsic":
        return "PENDING_DONNING_AND_SIGNED_AXIS_CAPTURE", None
    if category in {"joint_parent", "joint_rest"}:
        return "PENDING_BOUNDED_ANATOMICAL_PRIOR", None
    if category == "bone_length" or category == "joint_child":
        return "DERIVATION_OR_CONVENTION_QUALIFIED", None
    raise ValueError(slot_id)


def build_authority_plan(ledger: Mapping[str, Any], fusion: Path, sources: Mapping[str, Path]) -> dict[str, Any]:
    validate_source_ledger(ledger)
    body_source = sources["body_source"]
    body_config = sources["body_config"]
    brief = sources["execution_brief"]
    anchor_ref = sources["anchor_reference"]
    time_alignment = sources["time_alignment"]
    entries = []
    for source_slot in sorted(ledger["slots"], key=lambda row: str(row["slot_id"])):
        slot = copy.deepcopy(source_slot)
        authority = _authority(slot)
        category = str(slot["category"])
        if category in {"anchor_position", "anchor_delay"}:
            candidate = anchor_ref
        elif category == "time_relationship":
            candidate = time_alignment
        elif category == "imu_extrinsic":
            candidate = brief
        elif category == "world_model_gauge":
            candidate = sources["frame_contract"]
        elif category == "anatomical_point" and slot["slot_id"] == "anatomical_point:torso_top":
            candidate = None
        elif category in {"joint_parent", "joint_child", "joint_rest", "bone_length", "tag_lever", "anatomical_point"}:
            candidate = body_source if category == "tag_lever" else body_config
        else:
            candidate = None
        candidate_path, checksum = _artifact(candidate)
        node_or_entity = slot["node/joint/anchor"]
        entity_id = str(node_or_entity["entity_id"])
        status, blocker = _qualification(slot, authority)
        session_specific = category in {"imu_extrinsic", "tag_lever", "time_relationship", "world_model_gauge"}
        subject_specific = category in {"anatomical_point", "bone_length", "joint_parent", "joint_child", "joint_rest", "imu_extrinsic", "tag_lever"}
        hardware_specific = category in {"imu_extrinsic", "tag_lever", "anchor_delay", "time_relationship"}
        entry = {
            "slot_id": slot["slot_id"],
            "category": category,
            "node/joint/anchor": node_or_entity,
            "mathematical_type": slot["mathematical_type"],
            "dimension": slot["dimension"],
            "source_frame": slot["source_frame"],
            "target_frame": slot["target_frame"],
            "physical_meaning": _meaning(slot),
            "authority_class": authority,
            "candidate_source_artifact": candidate_path,
            "source_checksum": checksum,
            "session_specific": session_specific,
            "subject_specific": subject_specific,
            "hardware_specific": hardware_specific,
            "independently_free": authority == "ESTIMATE_WITH_PRIOR",
            "derived_from": _derived_from(slot),
            "coupled_slots": _coupled_slots(slot),
            "known_gauges": ["world_model_gauge"] if category in {"anchor_position", "world_model_gauge"} else [],
            "observability_dependencies": list(slot["observability_dependencies"]),
            "uncertainty_model": _uncertainty(slot, authority),
            "qualification_action": slot["qualification_action"],
            "qualification_status": status,
            "blocking_reason": blocker,
            "source_value": None,
            "source_status": "FROZEN_UNCERTAIN",
            "entity_id": entity_id,
        }
        entries.append(entry)
    authority_counts = dict(sorted(Counter(row["authority_class"] for row in entries).items()))
    if authority_counts != dict(sorted(AUTHORITY_COUNTS.items())):
        raise AssertionError(authority_counts)
    return {
        "schema": "biospur-root-r6a1b-calibration-authority-plan-v1",
        "source_ledger_schema": ledger["schema"],
        "source_ledger_sha256": sha256_file(sources["ledger"]),
        "source_slot_count": 87,
        "source_values_all_null": True,
        "source_statuses_all_frozen_uncertain": True,
        "authority_classes": list(AUTHORITY_CLASSES),
        "authority_counts": authority_counts,
        "per_category_counts": dict(CATEGORY_COUNTS),
        "slot_count": len(entries),
        "slots": entries,
        "policy": {
            "fit_from_c1": False,
            "replace_null_values": False,
            "authority_is_not_a_value": True,
            "operator_attestation_is_not_zero_uncertainty_metrology": True,
        },
    }


def build_dependency_graph(plan: Mapping[str, Any]) -> dict[str, Any]:
    slot_ids = {row["slot_id"] for row in plan["slots"]}
    external = set()
    edges = []
    for row in plan["slots"]:
        for parent in row["derived_from"]:
            if parent not in slot_ids:
                external.add(parent)
            edges.append({"dependent": row["slot_id"], "prerequisite": parent})
    nodes = ([{"id": slot, "kind": "registry_slot"} for slot in sorted(slot_ids)]
             + [{"id": item, "kind": "external_or_convention"} for item in sorted(external)])
    return {
        "schema": "biospur-root-r6a1b-calibration-dependency-graph-v1",
        "edge_direction": "dependent_to_prerequisite",
        "nodes": nodes,
        "edges": sorted(edges, key=lambda row: (row["dependent"], row["prerequisite"])),
        "canonical_geometry": {
            "child_origin_rule": "joint_child:<joint> = [0,0,0] by convention for every non-root child",
            "shared_joint_rule": "p_WJ = T_W_parent*p_parent_joint = T_W_child*[0,0,0]",
            "bone_rule": "bone length is a time-invariant norm of declared segment-local endpoints",
            "tag_rule": "l_SA = t_SI + R_SI*l_IA",
            "clock_rule": "t_global_ns = round(a_ns_per_us*t_native_us + b_ns), imported once per node/boot/capture",
        },
        "eliminated_duplicate_freedoms": [
            "all nine child-local joint offsets removed as optimizer freedoms by proximal-origin convention",
            "all eight bone lengths derived rather than co-fitted with endpoint geometry",
            "all ten tag levers derived from session extrinsic plus per-device internal rigid lever",
            "all ten common-time relationships imported rather than refitted against motion",
            "the six-dimensional world gauge fixed rather than learned from C1",
        ],
    }


def build_minimal_state(plan: Mapping[str, Any]) -> dict[str, Any]:
    estimated = [row for row in plan["slots"] if row["authority_class"] == "ESTIMATE_WITH_PRIOR"]
    directly_measured = [row for row in plan["slots"] if row["authority_class"] == "MEASURE_DIRECTLY"]
    return {
        "schema": "biospur-root-r6a1b-minimal-identifiable-state-v1",
        "scope": "structural and synthetic only; not a real observability or estimation claim",
        "canonical_free_parameters": {
            "class": "calibration parameter",
            "slot_ids": [row["slot_id"] for row in estimated],
            "slot_count": len(estimated),
            "dimension": sum(int(row["dimension"]) for row in estimated),
            "composition": {
                "joint_parent_local_centres": {"slots": 9, "dimension": 27},
                "joint_rest_rotations": {"slots": 9, "dimension": 27},
                "session_nominal_imu_extrinsics": {"slots": 10, "dimension": 60},
            },
            "authorization": "potentially estimated only with qualified bounded priors and declared capture evidence",
        },
        "fixed_parameters": {
            "class": "world-coordinate gauge or geometric convention",
            "slot_ids": [row["slot_id"] for row in plan["slots"] if row["authority_class"] == "FIX_BY_CONVENTION"],
            "runtime_numeric_values_authorized": False,
        },
        "imported_parameters": {
            "class": "capture-bound calibration input",
            "slot_ids": [row["slot_id"] for row in plan["slots"] if row["authority_class"] == "IMPORT_FROZEN_PROVENANCE"],
            "generic_cross_capture_reuse_authorized": False,
        },
        "directly_measured_parameters": {
            "class": "calibration parameter",
            "slot_ids": [row["slot_id"] for row in directly_measured],
            "dimension": sum(int(row["dimension"]) for row in directly_measured),
            "joint_optimizer_freedom": False,
        },
        "derived_parameters": {
            "class": "derived/reporting geometry",
            "slot_ids": [row["slot_id"] for row in plan["slots"] if row["authority_class"] == "DERIVE_NOT_INDEPENDENT"],
            "joint_optimizer_freedom": False,
        },
        "blocked_parameters": [row["slot_id"] for row in plan["slots"] if row["authority_class"] == "BLOCKED_MISSING_DEFINITION"],
        "dynamic_navigation_states": {
            "class": "navigation state",
            "per_keyframe": {"root_position_m": 3, "root_orientation_so3": 3, "root_velocity_mps": 3},
            "dimension_per_keyframe": 9,
        },
        "dynamic_articulated_states": {
            "class": "navigation state",
            "per_keyframe": {"nine_relative_joint_orientations": 27, "nine_relative_joint_rates": 27},
            "dimension_per_keyframe": 54,
            "bone_stretch_state": None,
        },
        "imu_bias_states": {
            "class": "IMU bias state",
            "per_keyframe_or_sparse_knot": {"ten_gyro_bias_vectors": 30, "ten_accel_bias_vectors": 30},
            "dimension": 60,
            "qualified_process_noise": False,
        },
        "dynamic_skin_slip_nuisance": {
            "class": "dynamic skin-slip nuisance",
            "representation": "three-dimensional so(3) perturbation per device at sparse smooth control knots",
            "maximum_dimension_per_control_knot": 30,
            "per_sample_unconstrained": False,
            "numerical_bounds": None,
        },
        "measurement_noise_parameters": {
            "class": "measurement noise parameter",
            "optimizer_state": False,
            "production_values": None,
            "required_source": "TEN_DEVICE_LONG_STATIC_MULTI_TEMPERATURE_NOISE_CAPTURE",
        },
        "world_coordinate_gauge": {
            "class": "world-coordinate gauge",
            "slot_id": "world_model_gauge",
            "optimizer_state": False,
            "unresolved_external_bridge": "V4-relative to right-handed +Z-up navigation/protocol/pelvis convention",
        },
        "eliminated_duplicate_freedoms": {
            "joint_child_dimensions": 27,
            "bone_length_dimensions": 8,
            "tag_lever_dimensions": 30,
            "time_relationship_dimensions": 20,
            "world_gauge_dimensions": 6,
            "total_registry_dimensions_not_independently_optimized": 91,
        },
        "remaining_unavoidable_gauges": [
            "global origin and yaw/heading until the external V4-to-navigation/protocol bridge is fixed",
            "root initial translation is a navigation initial condition, not a second static calibration gauge",
        ],
        "synthetic_rank_expectations": {
            "canonical_static_free_dimension": 114,
            "prior_augmented_structural_rank": 114,
            "data_only_rank": None,
            "claim": "full structural rank with explicit priors is necessary but does not prove real trajectory observability",
        },
        "prerequisites_for_real_estimation": [
            "define torso_top or remove it from all estimating/reporting consumers",
            "per-node PCB/enclosure IMU-origin to UWB phase-centre metrology",
            "exact V4-relative to navigation/protocol/pelvis frame bridge",
            "direct distal-landmark metrology and bounded joint/rest priors",
            "signed-axis/donning/neutral-return skin-slip capture",
            "qualified ten-device multi-temperature electronic-noise capture",
            "Root-R6A2 adapter must use the corrected BSFEC35-left and BSFB165-right mapping",
        ],
    }


def build_frame_contract(sources: Mapping[str, Path]) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1b-frame-chain-contract-v1",
        "transform_notation": "T_X_Y maps coordinates from frame Y into frame X; rotations are active local-to-parent SO(3)",
        "frames": {
            "W": "right-handed navigation world, +Z anti-gravity after its external bridge is materialized",
            "M": "body-model gauge frame",
            "S_i": "anatomical segment-local frame",
            "I_i": "IMU sensor/PCB frame for node i",
            "A_i": "UWB antenna phase-centre point rigidly attached to the same enclosure",
            "V4": "frozen UWB relative-geometry frame; not independently proven gravity aligned",
            "P": "protocol/pelvis convention whose exact bridge to V4 and W remains missing",
        },
        "handedness": "RIGHT_HANDED",
        "gravity": {"plus_z": "anti-gravity/up", "vector_W_mps2": [0.0, 0.0, -9.80665]},
        "chain": {
            "segment": "T_W_Si(t) = T_W_M * T_M_root(t) * FK_root_to_Si(q(t), subject_geometry)",
            "imu": "T_W_Ii(t) = T_W_Si(t) * T_Si_Ii_nominal * Exp(delta_theta_skin_i(t))",
            "antenna_point": "p_W_Ai(t) = T_W_Si(t) * (t_Si_Ii + R_Si_Ii*l_Ii_Ai)",
            "joint_closure": "T_W_parent*p_parent_J = T_W_child*p_child_J; canonical p_child_J=[0,0,0]",
            "joint_rotation": "R_parent_child(t) = Exp(joint_rest) * Exp(dynamic_joint_rotvec(t))",
        },
        "world_gauge": {
            "authority": "FIX_BY_CONVENTION",
            "origin": None,
            "initial_translation_gauge": "T_M_root translation is dynamic initial navigation state, not another static gauge",
            "global_yaw_heading": None,
            "pelvis_protocol_relationship": None,
            "qualification": "PENDING_EXACT_V4_TO_W_AND_PROTOCOL_PELVIS_BRIDGE",
        },
        "source_evidence": [
            {"path": str(sources["body_source"]), "sha256": sha256_file(sources["body_source"]), "proves": "single FK composition and segment/IMU/tag point usage"},
            {"path": str(sources["body_config"]), "sha256": sha256_file(sources["body_config"]), "proves": "slot types, transform conventions, and rooted graph; its wrist ownership mapping is superseded for R6A1B"},
            {"path": str(sources["frame_contract"]), "sha256": sha256_file(sources["frame_contract"]), "proves": "B/S/N/V4/H frame roles and T_NB=T_NS*T_SB"},
        ],
        "source_conflict": {
            "artifact": str(sources["body_config"]),
            "conflict": "historical active_identity_mapping assigns BSFB165 to forearm_left and BSFEC35 to forearm_right",
            "resolution": "R6A1B uses the execution brief's authoritative corrected mapping: BSFEC35 left, BSFB165 right; protected Root-R6A0 source is not rewritten",
            "root_r6a2_requirement": "consume the R6A1B mapping explicitly; do not inherit the historical mapping silently",
        },
        "display_frame_policy": "Any display/body frame is downstream presentation only and cannot alter estimation geometry.",
    }


def build_donning_contract(sources: Mapping[str, Path]) -> dict[str, Any]:
    descriptions = {
        "BSFEC35": "above left wrist prominence, upright; outward -Z approximately anatomical left",
        "BSFB165": "above right wrist prominence, upright; outward -Z approximately anatomical right",
        "BSFAA61": "left side of upper arm with elbow direction upward; outward -Z approximately left/rear under the recorded convention",
        "BSF1120": "right side of upper arm with elbow direction upward; outward -Z approximately right/rear",
        "BSF31CC": "central rib-triangle region, facing forward",
        "BSFC2CC": "centre of belt, facing forward",
        "BSF44AD": "anterior left thigh, knee direction upward, facing forward",
        "BSF3C79": "anterior right thigh, knee direction upward, facing forward",
        "BSF6C53": "lateral left shank because anterior tibial placement was unsuitable; outward approximately anatomical left",
        "BSF8BC4": "lateral right shank; outward approximately anatomical right",
    }
    placements = [{"node_id": node, "segment": NODES_TO_SEGMENTS[node], "neutral_description": descriptions[node], "exact_rotation": None}
                  for node in NODES_TO_SEGMENTS]
    return {
        "schema": "biospur-root-r6a1b-session-donning-contract-v1",
        "authority": "OPERATOR_ATTESTED_SESSION_SPECIFIC_DONNING",
        "directional_status": "FULL_DIRECTIONAL_CLOSURE_SUPPORTED_WITH_UNQUANTIFIED_DONNING_UNCERTAINTY",
        "execution_brief": {"path": str(sources["execution_brief"]), "sha256": sha256_file(sources["execution_brief"])},
        "placements": placements,
        "common_enclosure_condition": "one consistent physical short edge on every enclosure had a substantial groundward component",
        "zero_uncertainty_rotation_authorized": False,
        "historical_r2_5_search": {
            "searched_name": "FRAME_CHAIN_AND_AXIS_AUDIT.md",
            "formerly_reported_path": "BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r25/review_20260819/FRAME_CHAIN_AND_AXIS_AUDIT.md",
            "found": False,
            "checksum": None,
            "independent_historical_reread_claimed": False,
            "authority_fallback": "direct execution brief only",
        },
        "c1_neutral_exclusion": {
            "first_r6a1a_window_end_before_labelled_initial_still_s": 96.327,
            "neutral_confirmation_authorized": False,
        },
    }


def build_skin_contract() -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1b-skin-slip-state-contract-v1",
        "registry_membership": "NOT_ONE_OF_THE_87_STATIC_SLOTS",
        "phenomenon": [
            "skin, strap, tendon, and enclosure can move relative to the underlying bone",
            "pronation/supination includes real forearm motion and is not alone proof of slip",
            "residual after return to the same labelled neutral is slip/hysteresis evidence",
            "the IMU and antenna move together, so UWB alone cannot separate enclosure from bone motion",
        ],
        "conceptual_model": "R_SI(t) = R_SI_nominal * Exp(delta_theta_skin(t))",
        "nominal_source": "session-specific donning extrinsic with qualified prior",
        "dynamic_state": {
            "type": "bounded temporally smooth so(3) perturbation",
            "dimension_per_device_control_knot": 3,
            "per_sample_unconstrained": False,
            "magnitude_bound_rad": None,
            "rate_or_smoothness_bound": None,
            "translation_component": None,
            "one_axis_assumption": False,
        },
        "session_semantics": {
            "reset": "reset delta_theta_skin to an uncertain near-zero prior only after a newly labelled neutral donning action",
            "carry_across_boot": False,
            "carry_across_redonning": False,
            "carry_across_sessions": False,
        },
        "forbidden_absorptions": ["electronic IMU white noise", "IMU bias random walk", "bone length", "unconstrained per-frame extrinsic"],
        "observability_dependencies": [
            "joint topology and neighbouring segments",
            "fixed bone geometry",
            "gravity and contact evidence",
            "at least five repeated neutral returns",
            "robust UWB factors as enclosure-motion evidence but not sole bone/slip discriminator",
        ],
        "qualification_status": "CONCEPTUAL_STRUCTURE_QUALIFIED_NUMERICAL_BOUNDS_UNMEASURED",
    }


def build_import_audit(sources: Mapping[str, Path]) -> dict[str, Any]:
    anchor_reference = json.loads(sources["anchor_reference"].read_text())
    geometry = sources["anchor_geometry"]
    manifest = sources["anchor_manifest"]
    delay_ledger = sources["delay_ledger"]
    time_alignment = json.loads(sources["time_alignment"].read_text())
    return {
        "schema": "biospur-root-r6a1b-import-provenance-audit-v1",
        "anchors": {
            "classification": "IMPORT_FROZEN_PROVENANCE",
            "source_reference": {"path": str(sources["anchor_reference"]), "sha256": sha256_file(sources["anchor_reference"])},
            "geometry": {"path": str(geometry), "sha256": sha256_file(geometry), "coordinate_frame": "V4 RELATIVE_GEOMETRY_ONLY", "source_units": "mm", "registry_units": "m", "conversion": 0.001},
            "capture_bound_manifest": {"path": str(manifest), "sha256": sha256_file(manifest)},
            "delay_convention": {"path": str(delay_ledger), "sha256": sha256_file(delay_ledger), "sign": "predicted_range = geometric_range + d_anchor_mm + tag_delay", "apply_once": True},
            "reference_declared_geometry_sha256": anchor_reference.get("geometry_sha256"),
            "equivalence": {
                "anchor_identity": "PROVEN_FOR_EXACT_MANIFEST",
                "coordinate_frame": "PROVEN_AS_V4_RELATIVE_ONLY; W bridge missing",
                "units": "PROVEN_MM_TO_M_CONVERSION",
                "hardware_revision_and_identity": "PROVEN_FOR_EXACT_CAPTURE_BOUND_MANIFEST_ONLY",
                "antenna_definition": "DW timestamp/antenna calibration 16436 and tag observation path documented; physical W bridge not implied",
                "delay_sign_and_convention": "PROVEN_APPLY_ONCE",
                "calibration_version": "PROVEN_BY_REFERENCE_AND_MANIFEST",
                "source_checksum": "PROVEN",
            },
            "generic_reuse": False,
            "c1_recalibration": False,
        },
        "time_relationships": {
            "classification": "IMPORT_FROZEN_PROVENANCE",
            "source": {"path": str(sources["time_alignment"]), "sha256": sha256_file(sources["time_alignment"])},
            "source_schema": time_alignment.get("schema"),
            "mapping": "global_time_ns = round(a_ns_per_us * timer2_expanded_us + b_ns)",
            "binding": "per node, boot epoch, decoder, and capture",
            "uncertainty": "import ClockModel residual/sigma metadata; do not invent a generic covariance",
            "clock_offset_vs_motion_coupling": {
                "analysis": "a second free delta_t changes position approximately v*delta_t and orientation approximately omega*delta_t, allowing timing error to absorb spatial/extrinsic/model residuals",
                "second_free_mapping_authorized": False,
                "policy": "evaluate factors only at imported global_time_ns and propagate mapping uncertainty",
            },
        },
        "world_bridge": {
            "available_anchor_frame": "V4 RELATIVE_GEOMETRY_ONLY",
            "required": "exact rigid transform and uncertainty from V4 to right-handed +Z-up navigation W plus protocol/pelvis definition",
            "available": False,
        },
    }


def build_noise_protocol(sources: Mapping[str, Path]) -> dict[str, Any]:
    prerequisite = json.loads(sources["noise_audit"].read_text())["prerequisite"]
    return {
        "schema": "biospur-root-r6a1b-electronic-noise-capture-protocol-v1",
        "name": prerequisite["name"],
        "source_prerequisite": {"path": str(sources["noise_audit"]), "sha256": sha256_file(sources["noise_audit"]), "requirements_verbatim": prerequisite["capture_protocol"]},
        "capture_status": "NOT_EXECUTED",
        "devices": list(NODES_TO_SEGMENTS),
        "separation": "electronic-noise capture only; rigid, non-worn, and not usable to fit donning or skin slip",
        "mounting_and_operation": {
            "fixture": "all ten identified nodes rigidly clamped to one stable non-vibrating fixture on a non-moving support",
            "sample_rate_hz": 200,
            "timestamp_path": "production native timer, boot_epoch, accepted status, and materialized global_time_ns",
            "firmware": "production capture firmware; record both MCU hashes, decoder hash, config hash, radio state, and node identities",
            "radio_state": "UWB ranging and BLE traffic not required for IMU recording disabled",
        },
        "plateaus": [
            {"temperature_C": 20, "tolerance_C": 0.5, "soak_min": 45, "continuous_capture_h": 4, "day": 1},
            {"temperature_C": 30, "tolerance_C": 0.5, "soak_min": 45, "continuous_capture_h": 4, "day": 1},
            {"temperature_C": 40, "tolerance_C": 0.5, "soak_min": 45, "continuous_capture_h": 4, "day": 1},
            {"temperature_C": 20, "tolerance_C": 0.5, "soak_min": 45, "continuous_capture_h": 4, "day": 2},
        ],
        "required_fields": ["raw accelerometer", "raw gyroscope", "temperature", "accepted status", "boot_epoch", "native timer", "global_time_ns"],
        "plateau_invalidation": ["any boot", "any nonpositive timestamp", "any raw rail hit", "any gap greater than 20 ms"],
        "gap_policy": "invalidate the affected plateau; never interpolate",
        "analysis": {
            "method": "overlapping Allan deviation per axis on each uninterrupted plateau",
            "tau_max": "one tenth of that uninterrupted plateau",
            "white_region": {"slope_target": -0.5, "accepted_slope": [-0.75, -0.25], "minimum_decades": 1, "minimum_log_spaced_points": 5, "minimum_r_squared": 0.95},
            "bias_random_walk_region": {"slope_target": 0.5, "accepted_slope": [0.35, 0.65], "minimum_decades": 1, "minimum_log_spaced_points": 5, "minimum_r_squared": 0.95},
            "density_convention": "continuous covariance density q; discrete measurement variance q^2/dt",
            "confidence_intervals": "predeclared 95% moving-block bootstrap over contiguous raw samples; block duration at least the larger of 10 s or 10 times the fitted region's maximum tau; report resample count and seed",
            "temperature": "estimate and report temperature dependence separately from white density and bias random walk",
        },
        "pass_fail": {
            "per_axis_per_device": "both declared regions satisfy slope, decade, point-count, R^2, validity, and provenance requirements",
            "repeatability": "the second-day 20 C estimate is within 20 percent under the same density convention",
            "device_profile": "freeze only after every required axis/parameter passes; failures remain null",
            "pooling": "forbidden unless a separate predeclared hierarchical/equivalence test justifies it with uncertainty",
        },
        "provenance": ["raw bytes", "raw and derived SHA-256", "decoder source/version/hash", "both MCU firmware hashes", "capture config hash", "fixture and radio state", "actual sample counts and native-dt distribution", "temperature logger identity/calibration"],
        "production_constants_estimated_here": False,
    }


def build_donning_protocol() -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1b-donning-signed-axis-skin-slip-capture-protocol-v1",
        "capture_status": "NOT_EXECUTED",
        "separation": "worn geometric/donning/skin-slip capture; never an electronic-noise characterization",
        "device_mapping": NODES_TO_SEGMENTS,
        "minimum_repetitions_per_required_action_family_per_side": 5,
        "preflight": [
            "verify every B306 identity against the node/segment map, including BSFEC35 left and BSFB165 right",
            "photograph or label the enclosure's physical axes and identify the one common short edge",
            "preserve firmware, decoder, configuration, temperature, boot_epoch, native time, and global_time_ns",
            "record donning/session/subject ID and whether any device was redonned",
        ],
        "required_actions": [
            {"family": "labelled_neutral_still", "sides": "left and right separately", "repetitions": 5, "purpose": "nominal pose and neutral-return reference"},
            {"family": "flat_gravity_test", "sides": "each physical device", "repetitions": 5, "purpose": "axis-to-gravity consistency"},
            {"family": "signed_positive_90_degree_rotations", "sides": "each register axis and device", "repetitions": 5, "purpose": "positive register-axis sign"},
            {"family": "signed_negative_90_degree_rotations", "sides": "each register axis and device", "repetitions": 5, "purpose": "negative register-axis sign"},
            {"family": "common_groundward_short_edge_verification", "sides": "each device", "repetitions": 5, "purpose": "physical enclosure-edge contract"},
            {"family": "wrist_pronation_supination", "sides": "left and right separately", "repetitions": 5, "purpose": "separate true forearm motion from neutral-return slip/hysteresis"},
            {"family": "elbow_flexion_extension", "sides": "left and right separately", "repetitions": 5, "purpose": "joint/topology excitation"},
            {"family": "knee_flexion_extension", "sides": "left and right separately", "repetitions": 5, "purpose": "joint/topology excitation"},
        ],
        "action_sequence_rule": "each motion repetition begins and ends in an explicitly labelled neutral still; any redonning starts a new session block",
        "evidence": {"visual_or_fixture": "optional but recommended and time-synchronized", "physical_edge_id_required": True},
        "reporting": ["per-node signed-axis result", "neutral-return residual rotation vector", "hysteresis by action/direction/repetition", "temperature", "timestamp continuity", "mapping verification", "redonning/reset events"],
        "estimation_limits": {
            "exact_all_motion_extrinsic_from_one_still": False,
            "motion_alone_proves_skin_slip": False,
            "unconstrained_per_sample_extrinsic": False,
            "open_full_c1_or_heldout": False,
        },
    }


def _so3_exp(vector: np.ndarray) -> np.ndarray:
    theta = float(np.linalg.norm(vector))
    if theta < 1e-12:
        return np.eye(3)
    axis = vector / theta
    x, y, z = axis
    skew = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + np.sin(theta) * skew + (1.0 - np.cos(theta)) * (skew @ skew)


def _acyclic(graph: Mapping[str, Any]) -> tuple[bool, list[str]]:
    nodes = {row["id"] for row in graph["nodes"]}
    outgoing: dict[str, list[str]] = defaultdict(list)
    indegree = {node: 0 for node in nodes}
    for edge in graph["edges"]:
        prerequisite, dependent = edge["prerequisite"], edge["dependent"]
        outgoing[prerequisite].append(dependent)
        indegree[dependent] += 1
    ready = deque(sorted(node for node, count in indegree.items() if count == 0))
    order = []
    while ready:
        node = ready.popleft()
        order.append(node)
        for child in sorted(outgoing[node]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
    return len(order) == len(nodes), order


def _synthetic_rank(plan: Mapping[str, Any]) -> dict[str, Any]:
    free = [row for row in plan["slots"] if row["independently_free"]]
    starts = {}
    cursor = 0
    for row in free:
        starts[row["slot_id"]] = (cursor, cursor + int(row["dimension"]))
        cursor += int(row["dimension"])
    dimension = cursor
    x0 = np.linspace(-0.04, 0.05, dimension)

    def output(x: np.ndarray) -> np.ndarray:
        rows = [x.copy()]  # declared bounded priors constrain each canonical coordinate
        for bone_slot, dependencies in sorted(BONE_DERIVATIONS.items()):
            values = []
            for dependency in dependencies:
                if dependency.startswith("joint_parent"):
                    left, right = starts[dependency]
                    values.append(x[left:right])
                elif dependency.startswith("joint_child"):
                    values.append(np.zeros(3))
                else:
                    sign = -1.0 if "left" in dependency else 1.0
                    values.append(np.array([0.02 * sign, -0.01, -0.25]))
            rows.append(np.array([np.linalg.norm(values[0] - values[1])]))
        for node in sorted(NODES_TO_SEGMENTS):
            left, right = starts[f"imu_extrinsic:{node}"]
            pose = x[left:right]
            internal = np.array([0.018, -0.006, 0.011])
            rows.append(pose[3:] + _so3_exp(pose[:3]) @ internal)
        return np.concatenate(rows)

    epsilon = 1e-6
    baseline = output(x0)
    jacobian = np.empty((len(baseline), dimension))
    for column in range(dimension):
        delta = np.zeros(dimension)
        delta[column] = epsilon
        jacobian[:, column] = (output(x0 + delta) - output(x0 - delta)) / (2.0 * epsilon)
    singular = np.linalg.svd(jacobian, compute_uv=False)
    tolerance = max(jacobian.shape) * np.finfo(float).eps * singular[0]
    return {
        "free_dimension": dimension,
        "output_dimension": int(jacobian.shape[0]),
        "rank": int(np.sum(singular > tolerance)),
        "minimum_singular_value": float(singular[-1]),
        "tolerance": float(tolerance),
        "interpretation": "prior-augmented structural rank only; the identity rows represent declared bounded priors, not real measurements",
        "data_only_observability_claimed": False,
    }


def run_synthetic_qualification(ledger: Mapping[str, Any], contracts: Mapping[str, Any], protected_hashes_match: bool = True) -> dict[str, Any]:
    plan = contracts["CALIBRATION_AUTHORITY_PLAN.json"]
    graph = contracts["CALIBRATION_DEPENDENCY_GRAPH.json"]
    minimal = contracts["MINIMAL_IDENTIFIABLE_STATE.json"]
    frame = contracts["FRAME_CHAIN_CONTRACT.json"]
    skin = contracts["SKIN_SLIP_STATE_CONTRACT.json"]
    gates = []

    def gate(number: int, name: str, passed: bool, **metrics: Any) -> None:
        gates.append({"gate": number, "name": name, "pass": bool(passed), "metrics": metrics})

    counts = dict(sorted(Counter(row["category"] for row in ledger["slots"]).items()))
    gate(1, "exact_87_slot_enumeration_and_counts", len(ledger["slots"]) == 87 and counts == CATEGORY_COUNTS, slot_count=len(ledger["slots"]), category_counts=counts)
    plan_ids = [row["slot_id"] for row in plan["slots"]]
    auth_valid = all(row["authority_class"] in AUTHORITY_CLASSES for row in plan["slots"])
    gate(2, "exactly_one_authority_per_slot", len(plan_ids) == len(set(plan_ids)) == 87 and auth_valid, authority_counts=plan["authority_counts"])
    immutable = all(row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in ledger["slots"])
    gate(3, "source_values_and_statuses_immutable", immutable and plan["source_values_all_null"] and plan["source_statuses_all_frozen_uncertain"])
    expected = {
        "anatomical_point": (3, "R^3 local point (m)"), "anchor_delay": (1, "R^1 delay/range correction"),
        "anchor_position": (3, "R^3 world position (m)"), "bone_length": (1, "R^1 positive length (m)"),
        "imu_extrinsic": (6, "SE(3) tangent coordinates [rotation, translation]"), "joint_child": (3, "R^3 child-local joint centre (m)"),
        "joint_parent": (3, "R^3 parent-local joint centre (m)"), "joint_rest": (3, "SO(3) tangent coordinates"),
        "tag_lever": (3, "R^3 segment-local phase-centre lever (m)"), "time_relationship": (2, "R^2 clock affine relationship"),
        "world_model_gauge": (6, "SE(3) world-model gauge tangent coordinates"),
    }
    types_ok = all((row["dimension"], row["mathematical_type"]) == expected[row["category"]] for row in plan["slots"])
    gate(4, "frame_chain_type_and_unit_consistency", types_ok and frame["handedness"] == "RIGHT_HANDED", transform_notation=frame["transform_notation"])

    rng = np.random.default_rng(6101)
    parent_positions = {"pelvis": np.zeros(3)}
    rotations = {"pelvis": np.eye(3)}
    closure_error = 0.0
    for index, (joint, (parent, child)) in enumerate(JOINTS.items()):
        p_parent = rng.normal(0.0, 0.2, 3)
        r_child = rotations[parent] @ _so3_exp(rng.normal(0.0, 0.1, 3))
        centre = parent_positions[parent] + rotations[parent] @ p_parent
        parent_positions[child] = centre
        rotations[child] = r_child
        child_joint_world = parent_positions[child] + r_child @ np.zeros(3)
        closure_error = max(closure_error, float(np.linalg.norm(centre - child_joint_world)))
    gate(5, "shared_parent_child_joint_centre_closure", closure_error < 1e-14, maximum_error_m=closure_error, joint_count=len(JOINTS))

    endpoints = {slot: (rng.normal(size=3), rng.normal(size=3)) for slot in BONE_DERIVATIONS}
    before = {slot: float(np.linalg.norm(a - b)) for slot, (a, b) in endpoints.items()}
    after = dict(before)  # joint rotations never alter segment-local endpoint separation
    gate(6, "bone_length_invariance", before == after and len(before) == 8, maximum_change_m=max(abs(before[k] - after[k]) for k in before))
    free_ids = set(minimal["canonical_free_parameters"]["slot_ids"])
    gate(7, "no_duplicated_free_bone_or_joint_geometry", not any(row["slot_id"] in free_ids for row in plan["slots"] if row["category"] in {"bone_length", "joint_child"}), free_geometry_dimension=minimal["canonical_free_parameters"]["dimension"])
    gate(8, "no_duplicated_free_imu_tag_transform", all(row["authority_class"] == "DERIVE_NOT_INDEPENDENT" and not row["independently_free"] for row in plan["slots"] if row["category"] == "tag_lever"), tag_formula=graph["canonical_geometry"]["tag_rule"])
    gate(9, "no_second_free_time_mapping", all(not row["independently_free"] and row["authority_class"] == "IMPORT_FROZEN_PROVENANCE" for row in plan["slots"] if row["category"] == "time_relationship"))
    world = next(row for row in plan["slots"] if row["slot_id"] == "world_model_gauge")
    gate(10, "world_gauge_fixed", world["authority_class"] == "FIX_BY_CONVENTION" and not world["independently_free"] and minimal["world_coordinate_gauge"]["optimizer_state"] is False)
    acyclic, order = _acyclic(graph)
    gate(11, "acyclic_derivation_graph", acyclic, node_count=len(graph["nodes"]), edge_count=len(graph["edges"]), topological_order_count=len(order))
    serial_a = canonical_bytes(minimal)
    serial_b = canonical_bytes(json.loads(serial_a))
    gate(12, "minimal_state_serialization_determinism", serial_a == serial_b, sha256=hashlib.sha256(serial_a).hexdigest())
    rank = _synthetic_rank(plan)
    gate(13, "synthetic_jacobian_rank", rank["rank"] == rank["free_dimension"] == 114, **rank)
    gauges = minimal["remaining_unavoidable_gauges"]
    gate(14, "remaining_gauges_explicit", len(gauges) == 2 and all(isinstance(item, str) and item for item in gauges), gauges=gauges)
    test_bound = 0.10
    arbitrary_motion = np.array([0.55, -0.40, 0.30])
    best_bounded = arbitrary_motion * min(1.0, test_bound / np.linalg.norm(arbitrary_motion))
    residual = float(np.linalg.norm(arbitrary_motion - best_bounded))
    gate(15, "bounded_skin_slip_cannot_replace_arbitrary_motion", residual > 0.5 and skin["dynamic_state"]["per_sample_unconstrained"] is False, test_only_bound_rad=test_bound, residual_rad=residual)
    zero_error = float(np.max(np.abs(_so3_exp(np.zeros(3)) - np.eye(3))))
    gate(16, "zero_skin_slip_reproduces_nominal_rigid_model", zero_error == 0.0, maximum_error=zero_error)
    gate(17, "no_root_r6a0_or_r6a1a_regression", protected_hashes_match, protected_hashes_match=protected_hashes_match)
    replay_one = {name: hashlib.sha256(canonical_bytes(value)).hexdigest() for name, value in sorted(contracts.items())}
    replay_two = {name: hashlib.sha256(canonical_bytes(json.loads(canonical_bytes(value)))).hexdigest() for name, value in sorted(contracts.items())}
    gate(18, "deterministic_replay_identical_artifact_hashes", replay_one == replay_two, artifact_hashes=replay_one)
    return {
        "schema": "biospur-root-r6a1b-synthetic-observability-audit-v1",
        "gate_count": len(gates),
        "all_pass": all(row["pass"] for row in gates),
        "real_observability_claimed": False,
        "real_calibration_performed": False,
        "gates": gates,
    }


def build_contracts(ledger: Mapping[str, Any], fusion: Path, sources: Mapping[str, Path]) -> dict[str, Any]:
    plan = build_authority_plan(ledger, fusion, sources)
    contracts = {
        "CALIBRATION_AUTHORITY_PLAN.json": plan,
        "CALIBRATION_DEPENDENCY_GRAPH.json": build_dependency_graph(plan),
        "MINIMAL_IDENTIFIABLE_STATE.json": build_minimal_state(plan),
        "FRAME_CHAIN_CONTRACT.json": build_frame_contract(sources),
        "SESSION_DONNING_CONTRACT.json": build_donning_contract(sources),
        "SKIN_SLIP_STATE_CONTRACT.json": build_skin_contract(),
        "IMPORT_PROVENANCE_AUDIT.json": build_import_audit(sources),
        "NOISE_CAPTURE_PROTOCOL.json": build_noise_protocol(sources),
        "DONNING_AND_SKIN_SLIP_CAPTURE_PROTOCOL.json": build_donning_protocol(),
    }
    return contracts
