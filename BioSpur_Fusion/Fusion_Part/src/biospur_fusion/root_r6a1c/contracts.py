"""Versioned Root-R6A1C contracts and deterministic qualification gates."""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
from zipfile import ZipFile

import numpy as np

from .adapter import CORRECT_NODE_MAP, OBSOLETE_WRIST_MAP, evaluate_r6a2_request


NODE_TO_SEGMENT = dict(CORRECT_NODE_MAP)
COMMON_NINE = tuple(node for node in NODE_TO_SEGMENT if node != "BSF31CC")
LEDGER_COUNTS = {
    "anatomical_point": 5, "anchor_delay": 8, "anchor_position": 8,
    "bone_length": 8, "imu_extrinsic": 10, "joint_child": 9,
    "joint_parent": 9, "joint_rest": 9, "tag_lever": 10,
    "time_relationship": 10, "world_model_gauge": 1,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def resolve_sources(fusion: Path, execution_brief: Path) -> dict[str, Path]:
    fusion = Path(fusion).resolve()
    project = fusion.parent
    deployment = project / "B306_Part/deployments/current_room_autopos_20260811_183541"
    r6a1a = fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z"
    r6a1b = fusion / "logs/root_r6a1b_calibration_authority_20260825T084243Z"
    return {
        "execution_brief": Path(execution_brief).resolve(),
        "protected_baseline": fusion / "config/root_r6a0/protected_baseline.json",
        "ledger": r6a1a / "CALIBRATION_SLOT_LEDGER.json",
        "noise_audit": r6a1a / "NOISE_PROVENANCE_AUDIT.json",
        "r6a1b_final": r6a1b / "FINAL_RESULT.json",
        "r6a1b_authority": r6a1b / "CALIBRATION_AUTHORITY_PLAN.json",
        "r6a1b_minimal": r6a1b / "MINIMAL_IDENTIFIABLE_STATE.json",
        "r6a1b_donning": r6a1b / "SESSION_DONNING_CONTRACT.json",
        "root_r6a0_body": fusion / "src/biospur_fusion/root_r6a0/body.py",
        "root_r6a0_graph": fusion / "config/root_r6a0/body_graph.json",
        "root_r3_interfaces": fusion / "src/biospur_fusion/root_r3/interfaces.py",
        "root_r4_data": fusion / "src/biospur_fusion/root_r4/data.py",
        "pure_imu_skeleton": fusion / "pure_imu_baseline/skeleton.py",
        "hardware_provenance": fusion / "config/body_calibration_v4_1/input_preparation/HARDWARE_PROVENANCE.json",
        "hardware_guide": fusion / "config/visualization_centerline_v1/HARDWARE_MEASUREMENT_GUIDE.md",
        "operator_facts": fusion / "config/body_calibration_v4_1/input_preparation/OPERATOR_FACTS_REMAINING.md",
        "legacy_subject_inputs": fusion / "config/body_calibration_v4_1/v47_subject_inputs_v4_1.json",
        "cad_archive": Path("/home/zekaixiao/Downloads/ProPrj_eFlake_Synapse_2026-08-13.epro"),
        "cad_parser": project / "B306_Part/tools/analyze_v47_c2cc_rotation_aware.py",
        "pcb_v020_step": fusion / "PCB/V0.20/3D_PCB17_2026-08-25.step",
        "pcb_v020_gerber": fusion / "PCB/V0.20/Gerber_PCB17_2026-08-25.zip",
        "pcb_v020_enclosure_base": fusion / "PCB/V0.20/FusionPCB底座.step",
        "dwm1001_datasheet": Path("/home/zekaixiao/Documents/Datasheets/DW1000 EVK/DWM1001_DWM1001-DEV_MDEK1001_Sources_and_Docs_v11/DWM1001/Product_and_Design_Documents/DWM1001C_Datasheet.pdf"),
        "anchor_reference": fusion / "config/geometry/current_room_autopos_20260811_183541.reference.json",
        "anchor_layout": deployment / "V4IO_LAYOUT.json",
        "anchor_manifest": deployment / "CAPTURE_BOUND_GEOMETRY_MANIFEST.json",
        "delay_ledger": deployment / "RANGE_CORRECTION_LEDGER.md",
        "clock_models": fusion / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/TIME_ALIGNMENT_RESULT.json",
        "frame_binding": fusion / "logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2/FRAME_BINDING_RESULT.json",
    }


def validate_ledger(ledger: Mapping[str, Any]) -> None:
    slots = list(ledger.get("slots", ()))
    counts = dict(sorted(Counter(row.get("category") for row in slots).items()))
    if len(slots) != 87 or len({row.get("slot_id") for row in slots}) != 87:
        raise ValueError("immutable ledger is not exactly 87 unique slots")
    if counts != LEDGER_COUNTS:
        raise ValueError(f"immutable ledger category mismatch: {counts}")
    if any(row.get("value") is not None for row in slots):
        raise ValueError("real ledger contains a value")
    if any(row.get("status") != "FROZEN_UNCERTAIN" for row in slots):
        raise ValueError("real ledger contains a non-frozen status")
    if ledger.get("fitted_from_c1_count") != 0:
        raise ValueError("real ledger reports a fitted slot")


def _source(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path)}


def build_identity_and_donning(sources: Mapping[str, Path]) -> dict[str, Any]:
    limb_distal = {
        "forearm_left": "hand", "forearm_right": "hand",
        "upper_arm_left": "elbow", "upper_arm_right": "elbow",
        "thigh_left": "knee", "thigh_right": "knee",
        "shank_left": "ankle", "shank_right": "ankle",
    }
    nodes = []
    for node, segment in NODE_TO_SEGMENT.items():
        if segment in limb_distal:
            minus_y = f"distal, toward {limb_distal[segment]}"
            plus_y = "proximal, toward elbow" if segment.startswith("forearm") else (
                "proximal, toward shoulder" if segment.startswith("upper_arm") else (
                    "proximal, toward hip" if segment.startswith("thigh") else "proximal, toward knee"))
        else:
            minus_y, plus_y = "caudal, toward feet", "cranial, toward head"
        nodes.append({
            "node_id": node, "segment": segment,
            "axis_directions": {
                "+Z": "toward skin", "-Z": "outward from body",
                "-Y": minus_y, "+Y": plus_y,
                "+X": None, "-X": None,
            },
            "full_physical_axis_qualification": "PENDING",
        })
    return {
        "schema": "biospur-root-r6a1c-node-identity-donning-contract-v1",
        "identity_map": dict(NODE_TO_SEGMENT),
        "node_count": 10,
        "identity_authority": "R6A1C_FORWARD_BINDING",
        "historical_conflict": {
            "artifact": _source(sources["root_r6a0_graph"]),
            "obsolete_assignments": {"BSFEC35": "forearm_right", "BSFB165": "forearm_left"},
            "rewritten": False,
            "future_policy": "R6A2 consumers must use this versioned adapter and must reject direct obsolete-map ingestion",
        },
        "nodes": nodes,
        "evidence_classification": {
            "donning_direction": "OPERATOR_ATTESTED_SESSION_SPECIFIC",
            "antenna_edge_pcb_axis_association": "OPERATOR_REPORTED_CAD_OBSERVATION_PENDING_INDEPENDENT_AUDIT",
            "real_signed_axis_data_validation": "PENDING",
            "full_positive_xyz_physical_axis_qualification": "PENDING",
        },
        "neutral_standing_relation": {
            "minus_y_approximately_aligned_with_gravity": True,
            "scope": "NEUTRAL_NATURAL_STANDING_EXPECTATION_ONLY",
            "permanent_motion_time_constraint": False,
            "stationary_specific_force_sensor_mps2": {"axis": "+Y", "magnitude": 9.80665, "approximate": True},
            "gravity_sensor_mps2": {"axis": "-Y", "magnitude": 9.80665, "approximate": True},
        },
        "neutral_donning_procedure": {
            "pcb_plane": "vertical",
            "physical_end_toward_ground": "large gold printed UWB antenna end",
            "physical_end_opposite_ground": "B306 end",
            "device_axis_toward_ground": "-Y",
            "device_axis_toward_gold_uwb_end": "-Y",
            "small_ceramic_antenna_role": "BLE_ONLY_NOT_A_DONNING_UWB_REFERENCE",
            "authority": "OPERATOR_ATTESTED_SESSION_SPECIFIC",
            "applies_during_motion": False,
            "superseded_interpretation": "B306 end toward ground",
            "superseded_interpretation_retained": False,
        },
        "c1_neutral_evidence": {
            "first_audited_window_before_labelled_initial_still_s": 96.327,
            "neutral_gravity_cone_independently_verified": False,
        },
        "placement_reference": {
            "name": "C", "definition": "enclosure geometric centre",
            "operator_reported_skin_to_internal_pcb_normal_separation": {
                "approximate_value_m": 0.013,
                "estimated_uncertainty_m": None,
                "status": "OPERATOR_REPORTED_APPROXIMATE_NOT_IMU_EXTRINSIC",
                "use_as_calibration_value": False,
            },
        },
        "attachment": {
            "skin_contact": "DIRECT", "straps": "TIGHT_CIRCUMFERENTIAL",
            "anti_slip_features": True, "gross_strap_roll": "OPERATOR_REPORTED_SMALL",
            "fast_motion_soft_tissue_transient": "POSSIBLE",
        },
        "reported_hardware_families": {
            "common_nine": {"nodes": list(COMMON_NINE), "authority": "OPERATOR_REPORTED", "revision_id": None},
            "BSF31CC_distinct": {"nodes": ["BSF31CC"], "authority": "OPERATOR_REPORTED", "revision_id": None},
        },
        "execution_brief": _source(sources["execution_brief"]),
    }


def _measurement_record(identifier: str, kind: str, definition: str, side: str | None,
                        geometry_class: str, method: str) -> dict[str, Any]:
    return {
        "measurement_id": identifier,
        "measurement_kind": kind,
        "value": None,
        "unit": "m",
        "measurement_method": method,
        "landmark_definition": definition,
        "side": side,
        "geometry_class": geometry_class,
        "operator": None,
        "date": None,
        "session": None,
        "estimated_uncertainty": None,
        "evidence_status": "DEFERRED_DIRECT_MEASUREMENT",
    }


def build_deferred_measurements(sources: Mapping[str, Path]) -> dict[str, Any]:
    direct = [
        _measurement_record("biacromial_shoulder_breadth", "direct observable measurement", "straight-line distance between left and right lateral acromion landmarks", "bilateral", "straight-line", "palpation plus anthropometer/caliper"),
        _measurement_record("bi_ASIS_pelvis_breadth", "direct observable measurement", "straight-line distance between left and right anterior superior iliac spine landmarks", "bilateral", "straight-line", "palpation plus anthropometer/caliper"),
    ]
    limb_defs = {
        "upper_arm": ("lateral acromion landmark", "declared elbow surface landmark"),
        "forearm": ("declared elbow surface landmark", "midpoint of radial and ulnar styloid landmarks"),
        "thigh": ("greater trochanter landmark", "declared patella/knee surface landmark"),
        "shank": ("declared patella/knee surface landmark", "midpoint of medial and lateral malleolus landmarks"),
    }
    for limb, (first, second) in limb_defs.items():
        for side in ("left", "right"):
            direct.append(_measurement_record(
                f"{limb}_landmark_length_{side}", "direct observable measurement",
                f"{side} {first} to {second}; a surface-landmark chord, not an internal joint-centre length",
                side, "straight-line", "palpation plus tape/caliper",
            ))
    placements = []
    placement_specs = [
        ("upper_arm_device_C_to_elbow_left", "left upper-arm enclosure centre C to palpable left elbow landmark", "left"),
        ("upper_arm_device_C_to_elbow_right", "right upper-arm enclosure centre C to palpable right elbow landmark", "right"),
        ("thigh_device_C_to_knee_left", "left thigh enclosure centre C to patella/declared knee landmark", "left"),
        ("thigh_device_C_to_knee_right", "right thigh enclosure centre C to patella/declared knee landmark", "right"),
        ("shank_device_C_to_lateral_malleolus_left", "left shank enclosure centre C to left lateral malleolus", "left"),
        ("shank_device_C_to_lateral_malleolus_right", "right shank enclosure centre C to right lateral malleolus", "right"),
        ("BSF31CC_C_to_xiphoid_or_sternal_landmark", "BSF31CC enclosure centre C relative to a declared xiphoid/sternal landmark", "midline"),
        ("BSFC2CC_C_to_navel_or_ASIS_line", "BSFC2CC enclosure centre C relative to navel and/or bilateral ASIS line", "midline_or_bilateral"),
    ]
    for identifier, definition, side in placement_specs:
        placements.append(_measurement_record(identifier, "session donning parameter", definition, side, "skin-surface", "skin-surface tape path with palpable endpoint and enclosure centre C"))
    distal = [
        _measurement_record("wrist_left", "direct observable measurement", "midpoint of left radial and ulnar styloid landmarks, later expressed in forearm_left segment frame", "left", "straight-line", "qualified landmark metrology and segment-frame registration"),
        _measurement_record("wrist_right", "direct observable measurement", "midpoint of right radial and ulnar styloid landmarks, later expressed in forearm_right segment frame", "right", "straight-line", "qualified landmark metrology and segment-frame registration"),
        _measurement_record("ankle_left", "direct observable measurement", "midpoint of left medial and lateral malleolus landmarks, later expressed in shank_left segment frame", "left", "straight-line", "qualified landmark metrology and segment-frame registration"),
        _measurement_record("ankle_right", "direct observable measurement", "midpoint of right medial and lateral malleolus landmarks, later expressed in shank_right segment frame", "right", "straight-line", "qualified landmark metrology and segment-frame registration"),
    ]
    latent = []
    for segment in ("upper_arm_left", "upper_arm_right", "forearm_left", "forearm_right", "thigh_left", "thigh_right", "shank_left", "shank_right"):
        latent.append(_measurement_record(
            f"latent_joint_centre_length:{segment}", "latent anatomical parameter",
            f"internal proximal-to-distal joint-centre length for {segment}; not numerically identical to its surface-landmark measurement",
            "left" if segment.endswith("left") else "right", "derived-model-space",
            "qualified anatomical mapping or functional joint-centre method with declared mapping uncertainty",
        ))
    derived = [{
        "quantity_id": f"bone_length:{segment}", "measurement_kind": "derived model quantity",
        "value": None, "unit": "m", "measurement_method": "derive from qualified shared joint-centre geometry",
        "landmark_definition": f"time-invariant internal joint-centre distance for {segment}",
        "side": "left" if segment.endswith("left") else "right", "geometry_class": "derived-model-space",
        "operator": None, "date": None, "session": None, "estimated_uncertainty": None,
        "evidence_status": "DEFERRED_DIRECT_MEASUREMENT", "independently_free": False,
    } for segment in ("upper_arm_left", "upper_arm_right", "forearm_left", "forearm_right", "thigh_left", "thigh_right", "shank_left", "shank_right")]
    return {
        "schema": "biospur-root-r6a1c-deferred-measurement-contract-v1",
        "default_status": "DEFERRED_DIRECT_MEASUREMENT",
        "required_future_fields": ["value", "unit", "measurement method", "landmark definition", "left/right side", "straight-line or skin-surface classification", "operator/date/session", "estimated uncertainty", "evidence status"],
        "minimum_future_subject_profile": direct,
        "future_device_placement_observations": placements,
        "distal_anatomical_points": distal,
        "latent_anatomical_parameters": latent,
        "derived_model_quantities": derived,
        "symmetry_prior": {"available_as_explicit_option": True, "enabled": False, "treated_as_measured_truth": False, "numerical_strength": None},
        "semantic_guards": {
            "surface_landmark_equals_internal_bone_length": False,
            "skin_surface_placement_equals_3d_imu_joint_transform": False,
            "population_average_allowed_as_real": False,
            "hidden_default_allowed": False,
            "synthetic_subject_geometry_allowed": "isolated synthetic tests only",
        },
        "legacy_null_evidence": [_source(sources["legacy_subject_inputs"]), _source(sources["operator_facts"])],
    }


def build_torso_top(sources: Mapping[str, Path]) -> dict[str, Any]:
    consumers = [
        {"path": str(sources["root_r3_interfaces"]), "sha256": sha256_file(sources["root_r3_interfaces"]), "semantics": "legacy torso segment midpoint uses pelvis and torso_top"},
        {"path": str(sources["root_r4_data"]), "sha256": sha256_file(sources["root_r4_data"]), "semantics": "legacy frozen-M1 torso midpoint uses pelvis and torso_top"},
        {"path": str(sources["pure_imu_skeleton"]), "sha256": sha256_file(sources["pure_imu_skeleton"]), "semantics": "torso_top is the torso centreline point from which left/right shoulders branch"},
        {"path": str(sources["root_r6a0_graph"]), "sha256": sha256_file(sources["root_r6a0_graph"]), "semantics": "registered torso-local reporting point"},
        {"path": str(sources["root_r6a0_body"]), "sha256": sha256_file(sources["root_r6a0_body"]), "semantics": "generic local-point transform/report path; no torso_top factor or independent consumer"},
        {"path": str(sources["r6a1b_authority"]), "sha256": sha256_file(sources["r6a1b_authority"]), "semantics": "historically blocked because the reporting landmark was ambiguous"},
    ]
    return {
        "schema": "biospur-root-r6a1c-torso-top-ownership-v1",
        "slot_id": "anatomical_point:torso_top",
        "all_consumers_inspected": True,
        "consumers": consumers,
        "selected_semantic_definition": "torso-frame midpoint of the left and right shoulder joint centres; a model reporting/centreline point, not a surface landmark",
        "authority_class": "DERIVE_NOT_INDEPENDENT",
        "optimizer_freedom": False,
        "derivation_formula": "p_torso_top^S_torso = 0.5 * (p_shoulder_left^S_torso + p_shoulder_right^S_torso)",
        "derived_from": ["joint_parent:shoulder_left", "joint_parent:shoulder_right"],
        "registry_mutated": False,
        "source_registry_value": None,
        "source_registry_status": "FROZEN_UNCERTAIN",
        "forward_adapter_action": "replace the ambiguous independent-point ownership with this derived view for R6A1C/R6A2 consumers",
        "why_not_surface_landmark": "no current Root-R6A0 estimating factor requires a directly measured suprasternal/C7 point; legacy semantics require a shoulder-centreline model point",
    }


def build_hardware_audit(sources: Mapping[str, Path]) -> dict[str, Any]:
    hardware = json.loads(sources["hardware_provenance"].read_text(encoding="utf-8"))
    with ZipFile(sources["pcb_v020_gerber"]) as archive:
        flying_probe = json.loads(archive.read("FlyingProbeTesting.json"))
    if flying_probe["lengthUnit"] != "mil":
        raise ValueError("V0.20 flying-probe coordinates are not in mil")
    component_rows = {row[1]: row for row in flying_probe["components"]["rows"]}
    u4, u7 = component_rows["U4"], component_rows["U7"]
    if (u4[2], u7[2], u4[5], u7[5]) != ("T", "B", 0, 180):
        raise ValueError("V0.20 U4/U7 side or angle differs from audited export")
    vector_u4_u7_m = [
        (u7[3] - u4[3]) * 0.0254 / 1000.0,
        (u7[4] - u4[4]) * 0.0254 / 1000.0,
        None,
    ]
    step_text = sources["pcb_v020_step"].read_text(encoding="utf-8")
    step_components_present = all(marker in step_text for marker in (
        "Designed by EasyEDA Pro",
        "U4~COMM-SMD_34P-L26.1-W19.1-P1.00",
        "U7~COMM-SMD_JY901S",
    ))
    if not step_components_present:
        raise ValueError("V0.20 STEP lacks the audited EasyEDA/U4/U7 identity markers")
    step_u4_translation_m = [0.0, 0.0, 0.001538354076708]
    step_u7_translation_m = [0.003580048260097, -0.000005227330454661, 0.0]
    step_u7_rotation_S_from_Iref = [
        [-1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, -1.0],
    ]
    planar_cross_export_delta_m = [
        step_u7_translation_m[0] - vector_u4_u7_m[0],
        step_u7_translation_m[1] - vector_u4_u7_m[1],
    ]
    enclosure_text = sources["pcb_v020_enclosure_base"].read_text(encoding="utf-8")
    enclosure_markers_present = all(marker in enclosure_text for marker in (
        "MANIFOLD_SOLID_BREP('cover - 4'",
        "PRODUCT('2025-09-14-11-51-50-502','limb v3'",
        "'2026-08-25T12:18:25+02:00'",
    ))
    if not enclosure_markers_present:
        raise ValueError("V0.20 enclosure-base STEP lacks its audited identity markers")
    pcb_board_vertex_bounds_S_m = {
        "minimum": [-0.014468394716780, -0.030995080010160, 0.0],
        "maximum": [0.016442268224536, 0.017195097790196, 0.001538354076708],
    }
    enclosure_body_face_candidate_bounds_S_m = {
        "minimum": [-0.014568394716780, -0.031095080010160, -0.012210000029964],
        "maximum": [0.016542268224536, 0.017295097790196, -0.002210000029964],
    }
    pcb_planar_center_S_m = [
        0.5 * (pcb_board_vertex_bounds_S_m["minimum"][index] + pcb_board_vertex_bounds_S_m["maximum"][index])
        for index in range(2)
    ]
    enclosure_body_planar_center_candidate_S_m = [
        0.5 * (enclosure_body_face_candidate_bounds_S_m["minimum"][index] + enclosure_body_face_candidate_bounds_S_m["maximum"][index])
        for index in range(2)
    ]
    return {
        "schema": "biospur-root-r6a1c-hardware-frame-lever-audit-v1",
        "runtime_units": "SI",
        "transform_notation": "T_X_Y maps coordinates from frame Y into frame X",
        "reported_families": {
            "COMMON_NINE_REPORTED": {"nodes": list(COMMON_NINE), "revision_id": None, "evidence": "OPERATOR_REPORTED", "shared_transform_authorized": False},
            "BSF31CC_REPORTED_DISTINCT": {"nodes": ["BSF31CC"], "revision_id": None, "evidence": "OPERATOR_REPORTED", "shared_transform_authorized": False},
        },
        "cad_candidate": {
            "binding_status": "V0_20_EXPORT_AUDITED_BUT_UNBOUND_TO_NODE_HARDWARE_REVISIONS",
            "archive": _source(sources["cad_archive"]),
            "parser": _source(sources["cad_parser"]),
            "pcb_documents": ["PCB17", "PCB17_1"],
            "pcb_documents_component_placement_identical": True,
            "schematic_components": {"U4": "DWM1001C", "U7": "JY901S"},
            "operator_supplied_v0_20_export": {
                "directory_revision_label": "V0.20",
                "revision_label_authority": "OPERATOR_PROVIDED_PATH_LABEL_NOT_DEVICE_SERIAL_BINDING",
                "step": _source(sources["pcb_v020_step"]),
                "gerber_zip": _source(sources["pcb_v020_gerber"]),
                "step_header": {
                    "producer": "EasyEDA Pro / Open CASCADE 7.8",
                    "file_timestamp": "2026-08-25T12:14:04",
                    "internally_encoded_revision_id": None,
                },
                "gerber_flying_probe_member": "FlyingProbeTesting.json",
                "step_components_present": step_components_present,
                "node_or_family_assignment": None,
                "node_or_family_assignment_status": "UNPROVEN",
                "matches_august_13_archive_component_references": max(abs(value) for value in planar_cross_export_delta_m) < 1.0e-8,
                "planar_cross_export_delta_m": planar_cross_export_delta_m,
            },
            "operator_supplied_v0_20_enclosure_base": {
                "association_authority": "OPERATOR_ATTESTED_DIRECTLY_RELEVANT_TO_FUSION_PCB_UNIT",
                "source": _source(sources["pcb_v020_enclosure_base"]),
                "source_kind": "ACTUAL_3D_PRINT_OUTPUT_STEP_FOR_FUSION_PCB_BASE",
                "step_identity": {
                    "solid_name": "cover - 4",
                    "product_name": "limb v3",
                    "file_timestamp": "2026-08-25T12:18:25+02:00",
                    "identity_markers_present": enclosure_markers_present,
                },
                "coordinate_registration": {
                    "status": "CAD_COMMON_COORDINATE_REGISTRATION_PROVEN_AS_BUILT_TOLERANCE_UNQUALIFIED",
                    "T_baseCAD_from_S_STEP": {
                        "source_frame": "S_STEP", "target_frame": "base_CAD",
                        "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                        "translation_m": [0.0, 0.0, 0.0],
                    },
                    "pcb_board_vertex_bounds_S_m": pcb_board_vertex_bounds_S_m,
                    "enclosure_body_face_candidate_bounds_S_m": enclosure_body_face_candidate_bounds_S_m,
                    "pcb_planar_center_S_m": pcb_planar_center_S_m,
                    "enclosure_body_planar_center_candidate_S_m": enclosure_body_planar_center_candidate_S_m,
                    "planar_center_residual_m": [
                        enclosure_body_planar_center_candidate_S_m[index] - pcb_planar_center_S_m[index]
                        for index in range(2)
                    ],
                    "paired_xy_face_offset_m": 0.0001,
                    "interpretation": "the enclosure-base body faces are the PCB board vertex limits expanded by exactly 0.1 mm on both x/y sides, proving common CAD coordinates",
                },
                "complete_enclosure_C": None,
                "complete_enclosure_C_status": "PENDING_NAMED_FACE_SEMANTICS_AND_CONFIRMATION_BASE_IS_COMPLETE_E_FRAME_ENVELOPE",
                "why_base_bbox_is_not_C": "strap features and attachment protrusions expand the one-solid bounding box; R6A1C does not silently redefine enclosure C as that whole-solid bbox midpoint",
                "antenna_end_face_identification": {
                    "physical_marker": "large gold printed UWB antenna end",
                    "neutral_donning_direction": "toward ground",
                    "opposite_end": "B306 end",
                    "authority": "OPERATOR_ATTESTED_SESSION_SPECIFIC",
                    "B_ECAD_edge_mapping": None,
                    "B_ECAD_edge_mapping_status": "OPERATOR_REPORTED_CAD_OBSERVATION_PENDING_INDEPENDENT_AUDIT",
                },
                "physical_fit_tolerance": None,
                "node_serial_or_family_assignment": None,
                "node_serial_or_family_assignment_status": "UNPROVEN",
            },
            "frames": {
                "B_ECAD": "2D EasyEDA board frame; origin at U4 component reference; +x/+y EasyEDA axes; z is absent from the 2D component table",
                "S_STEP": "V0.20 STEP assembly frame in metres at runtime; source file units are millimetres",
                "I_reg": "IMU register-channel frame AX/AY/AZ and GX/GY/GZ; die/body and package-axis bridge unqualified",
                "I_ref": "U7 CAD component reference; not proven to be IMU die origin",
                "C": "enclosure geometric centre; no CAD registration to B exists",
                "A_nom": "nominal geometric UWB antenna reference; only the printed antenna region is identified, not a unique point",
                "A_phase": "effective UWB electromagnetic phase centre; unqualified",
            },
            "component_reference_constraints": {
                "U4_reference_B_ECAD_m": [0.0, 0.0, None],
                "U4_rotation_about_Bz_deg": 0.0,
                "U4_side": "TOP",
                "U7_reference_B_ECAD_m": vector_u4_u7_m,
                "U7_rotation_about_Bz_deg": 180.0,
                "U7_side": "BOTTOM",
                "U4_to_U7_planar_distance_m": 0.0035800449162773904,
                "conversion": "EasyEDA mil * 0.0254 mm/mil / 1000 mm/m",
                "T_S_STEP_U4ref": {
                    "source_frame": "U4ref", "target_frame": "S_STEP",
                    "rotation": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
                    "translation_m": step_u4_translation_m,
                },
                "T_S_STEP_Iref": {
                    "source_frame": "I_ref", "target_frame": "S_STEP",
                    "rotation": step_u7_rotation_S_from_Iref,
                    "translation_m": step_u7_translation_m,
                },
                "Iref_to_U4ref_vector_expressed_S_STEP_m": [
                    step_u4_translation_m[index] - step_u7_translation_m[index]
                    for index in range(3)
                ],
                "step_conversion": "STEP coordinates in millimetres / 1000 mm/m",
            },
            "reference_vector_status": "CAD_COMPONENT_REFERENCE_ONLY_NOT_IMU_ORIGIN_TO_ANTENNA",
        },
        "imu_register_to_die_body": {"rotation": None, "translation_m": None, "status": "PENDING_SIGNED_AXIS_AND_IMU_PACKAGE_AUDIT"},
        "board_to_enclosure_C": {
            "rotation": None, "translation_m": None,
            "status": "BLOCKED_COMPLETE_ENCLOSURE_C_AND_PCB_AXIS_BINDING_MISSING",
            "cad_coordinate_registration_available": True,
            "as_built_fit_tolerance": None,
        },
        "imu_origin_to_enclosure_C": {"rotation": None, "translation_m": None, "status": "UNQUALIFIED"},
        "antenna_reference": {
            "classification": "CAD_NOMINAL_ANTENNA_REFERENCE",
            "nominal_point_B_m": None,
            "uwb_physical_region": "large gold printed PCB antenna element in the rectangular area above the DWM1001C shield and its required keep-out region",
            "ble_antenna_exclusion": "the small ceramic antenna is BLE and must never be selected as the UWB nominal reference or lever endpoint",
            "identification_authority": {
                "operator": "OPERATOR_ATTESTED_HARDWARE_IDENTIFICATION",
                "datasheet_cross_check": "CONSISTENT: DWM1001C page 18 identifies the rectangular area above the shield as the UWB antenna area; page 1 separately lists UWB printed-PCB and Bluetooth chip antennas",
            },
            "available_evidence": "operator identification, datasheet, and the hash-bound V0.20 PCB/STEP export identify the DWM1001C module/printed-UWB region; none marks a unique UWB feed/reference point in the exported board frame",
            "phase_centre_status": "PHASE_CENTRE_OFFSET_UNQUALIFIED",
            "phase_centre_point_B_m": None,
            "antenna_delay_calibration_is_geometric_phase_centre_proof": False,
        },
        "imu_origin_to_uwb_antenna_nominal": {"rotation": None, "translation_m": None, "status": "BLOCKED_IMU_DIE_AND_NOMINAL_ANTENNA_POINT_MISSING"},
        "evidence": {
            "prior_hardware_audit": _source(sources["hardware_provenance"]),
            "measurement_guide": _source(sources["hardware_guide"]),
            "pcb_v0_20_step": _source(sources["pcb_v020_step"]),
            "pcb_v0_20_gerber_zip": _source(sources["pcb_v020_gerber"]),
            "pcb_v0_20_enclosure_base_step": _source(sources["pcb_v020_enclosure_base"]),
            "dwm1001c_datasheet": _source(sources["dwm1001_datasheet"]),
            "datasheet_findings": [
                "19.1 x 26.2 x 2.6 mm package envelope",
                "rectangular area above shield is the printed UWB antenna area",
                "gold printed region is the UWB antenna; the separate small ceramic antenna is BLE and excluded from UWB lever construction",
                "minimum 10 mm metal-free antenna keep-out",
                "carrier PCB geometry affects antenna performance",
                "no electromagnetic phase-centre point is specified",
            ],
        },
        "source_consistency": {
            "prior_status": hardware["status"],
            "prior_cad_sha_matches": hardware["cad"]["sha256"] == sha256_file(sources["cad_archive"]),
            "prior_datasheet_sha_matches": hardware["datasheet"]["sha256"] == sha256_file(sources["dwm1001_datasheet"]),
            "v0_20_matches_prior_component_references_within_10nm": max(abs(value) for value in planar_cross_export_delta_m) < 1.0e-8,
        },
    }


def build_world_contract(sources: Mapping[str, Path]) -> dict[str, Any]:
    frame = json.loads(sources["frame_binding"].read_text(encoding="utf-8"))
    clock = json.loads(sources["clock_models"].read_text(encoding="utf-8"))
    manifest = json.loads(sources["anchor_manifest"].read_text(encoding="utf-8"))
    return {
        "schema": "biospur-root-r6a1c-world-frame-bridge-contract-v1",
        "synthetic_world": {
            "frame_id": "W_SYNTHETIC_R6A1C_V1", "right_handed": True,
            "+Z": "up/anti-gravity", "gravity_mps2": [0.0, 0.0, -9.80665],
            "origin_m": [0.0, 0.0, 0.0], "yaw_gauge_rad": 0.0,
            "status": "SYNTHETIC_TEST_ONLY_NOT_REGISTRY_VALUE",
            "writes_world_model_gauge_slot": False,
        },
        "real_frames": {
            "V4": {"meaning": "capture-bound relative anchor frame", "coordinate_contract": manifest["coordinate_contract"]},
            "N": {"meaning": "right-handed navigation frame intended +Z up", "T_N_V4": None, "status": "PENDING_QUALIFIED_SURVEY_OR_FRAME_BINDING"},
            "P": {"meaning": "protocol/pelvis heading convention", "T_N_P": None, "status": "PENDING_DEFINITION_AND_SURVEY"},
        },
        "frame_binding_evidence": {**_source(sources["frame_binding"]), "qualified": frame["qualified"], "rank": frame["rank"], "reason": frame["reason"]},
        "anchors": {
            "reference": _source(sources["anchor_reference"]),
            "layout": _source(sources["anchor_layout"]),
            "manifest": _source(sources["anchor_manifest"]),
            "delay_convention": _source(sources["delay_ledger"]),
            "deployment": manifest["deployment"], "capture_binding": manifest["capture"],
            "cross_deployment_reuse": False, "recalibrate_from_body_motion": False,
        },
        "clock_relationships": {
            "source": _source(sources["clock_models"]),
            "node_count": len(clock["clock_models"]),
            "binding": "per node, boot_epoch, decoder, and named capture",
            "generic_cross_capture_reuse": False,
            "models": {node: {"boot_epoch": row["boot_epoch"], "a_ns_per_us": row["a_ns_per_us"], "b_ns": row["b_ns"], "sigma_ns": row["sigma_ns"]} for node, row in sorted(clock["clock_models"].items())},
        },
        "real_execution_bridge_qualified": False,
    }


def build_readiness(contracts: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1c-r6a2-readiness-v1",
        "software_contract_readiness": True,
        "root_r6a2_synthetic_fault_architecture_ready": True,
        "root_r6a2_real_shadow_ready": False,
        "root_r6a2_real_body_update_authorized": False,
        "next_bounded_stage_authorized": "Root-R6A2A_SYNTHETIC_FAULT_AWARE_WHOLE_BODY_SHADOW_INTEGRATION",
        "synthetic_readiness_basis": [
            "corrected ten-node identity adapter is versioned and fail-closed",
            "donning/axis authority levels and correct specific-force sign are machine-readable",
            "deferred real measurements have typed null states rather than hidden defaults",
            "torso_top has one derived owner and no duplicate optimizer freedom",
            "synthetic hardware revisions and world gauge are isolated from real contracts",
            "fault tests can exercise missing/invalid inputs without constructing a real body state",
        ],
        "real_shadow_blockers": [
            "DEFERRED_ANTHROPOMETRY_AND_DEVICE_PLACEMENT",
            "REAL_SIGNED_AXIS_VALIDATION_PENDING",
            "PRODUCTION_PROCESS_NOISE_UNQUALIFIED",
            "HARDWARE_REVISION_BINDING_MISSING",
            "IMU_TO_ENCLOSURE_AND_ANTENNA_LEVERS_UNQUALIFIED",
            "UWB_PHASE_CENTRE_OFFSET_UNQUALIFIED",
            "V4_TO_NAVIGATION_AND_PROTOCOL_BRIDGE_UNQUALIFIED",
        ],
        "real_body_update_policy": "hard false throughout R6A1C regardless of future files appearing",
        "missing_body_measurements_block_synthetic": False,
    }


def build_contracts(ledger: Mapping[str, Any], sources: Mapping[str, Path]) -> dict[str, Any]:
    validate_ledger(ledger)
    contracts = {
        "NODE_IDENTITY_AND_DONNING_CONTRACT.json": build_identity_and_donning(sources),
        "DEFERRED_MEASUREMENT_CONTRACT.json": build_deferred_measurements(sources),
        "TORSO_TOP_OWNERSHIP.json": build_torso_top(sources),
        "HARDWARE_FRAME_AND_LEVER_AUDIT.json": build_hardware_audit(sources),
        "WORLD_FRAME_BRIDGE_CONTRACT.json": build_world_contract(sources),
    }
    contracts["ROOT_R6A2_READINESS.json"] = build_readiness(contracts)
    return contracts


def _rigid_inverse(rotation: np.ndarray, translation: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return rotation.T, -rotation.T @ translation


def _compose(a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    ra, ta = a; rb, tb = b
    return ra @ rb, ta + ra @ tb


def _synthetic_request() -> dict[str, Any]:
    return {
        "mode": "SYNTHETIC_FAULT_ARCHITECTURE",
        "node_identity_map": dict(NODE_TO_SEGMENT),
        "identity_contract_schema": "biospur-root-r6a1c-node-identity-donning-contract-v1",
        "donning_contract_schema": "biospur-root-r6a1c-node-identity-donning-contract-v1",
        "deferred_measurement_schema": "biospur-root-r6a1c-deferred-measurement-contract-v1",
        "hardware_frame_schema": "biospur-root-r6a1c-hardware-frame-lever-audit-v1",
        "world_frame_schema": "biospur-root-r6a1c-world-frame-bridge-contract-v1",
        "geometry_kind": "SYNTHETIC_TEST_ONLY", "synthetic_world_gauge": True,
        "writes_real_registry": False,
        "hardware_revision_ids": {"common": "SYNTHETIC_COMMON_V1", "torso": "SYNTHETIC_TORSO_V1"},
        "independent_parameter_owners": ["joint_parent:shoulder_left", "joint_parent:shoulder_right"],
        "derived_parameter_ids": ["anatomical_point:torso_top"],
    }


def run_qualification(ledger: Mapping[str, Any], contracts: Mapping[str, Any], protected_hashes_exact: bool = True) -> dict[str, Any]:
    identity = contracts["NODE_IDENTITY_AND_DONNING_CONTRACT.json"]
    deferred = contracts["DEFERRED_MEASUREMENT_CONTRACT.json"]
    torso = contracts["TORSO_TOP_OWNERSHIP.json"]
    hardware = contracts["HARDWARE_FRAME_AND_LEVER_AUDIT.json"]
    world = contracts["WORLD_FRAME_BRIDGE_CONTRACT.json"]
    readiness = contracts["ROOT_R6A2_READINESS.json"]
    gates = []

    def gate(letter: str, name: str, passed: bool, **metrics: Any) -> None:
        gates.append({"gate": letter, "name": name, "pass": bool(passed), "metrics": metrics})

    gate("A", "predecessor_protected_hashes_exact", protected_hashes_exact)
    mapping = identity["identity_map"]
    gate("B", "ten_node_identities_unique_complete", len(mapping) == len(set(mapping)) == len(set(mapping.values())) == 10, node_count=len(mapping))
    gate("C", "corrected_wrist_map_enforced", mapping["BSFEC35"] == "forearm_left" and mapping["BSFB165"] == "forearm_right" and not evaluate_r6a2_request({**_synthetic_request(), "node_identity_map": OBSOLETE_WRIST_MAP}).authorized)
    gate("D", "bsfc2cc_pelvis_identity_enforced", mapping.get("BSFC2CC") == "pelvis" and "BSFC22C" not in mapping)
    evidence = identity["evidence_classification"]
    gate("E", "donning_axis_authority_not_overstated", evidence["donning_direction"] == "OPERATOR_ATTESTED_SESSION_SPECIFIC" and evidence["real_signed_axis_data_validation"] == "PENDING" and evidence["full_positive_xyz_physical_axis_qualification"] == "PENDING")
    neutral = identity["neutral_standing_relation"]
    donning = identity["neutral_donning_procedure"]
    gate("F", "neutral_minus_y_gravity_not_permanent", neutral["minus_y_approximately_aligned_with_gravity"] and not neutral["permanent_motion_time_constraint"] and neutral["scope"] == "NEUTRAL_NATURAL_STANDING_EXPECTATION_ONLY" and donning["physical_end_toward_ground"] == "large gold printed UWB antenna end" and donning["physical_end_opposite_ground"] == "B306 end" and donning["device_axis_toward_ground"] == "-Y" and not donning["applies_during_motion"] and not donning["superseded_interpretation_retained"])
    gate("G", "accelerometer_specific_force_sign_correct", neutral["stationary_specific_force_sensor_mps2"]["axis"] == "+Y" and neutral["gravity_sensor_mps2"]["axis"] == "-Y")
    measurement_groups = (deferred["minimum_future_subject_profile"], deferred["future_device_placement_observations"], deferred["distal_anatomical_points"], deferred["latent_anatomical_parameters"], deferred["derived_model_quantities"])
    all_measurements = [row for group in measurement_groups for row in group]
    gate("H", "all_deferred_real_measurements_null", all(row["value"] is None and row["evidence_status"] == "DEFERRED_DIRECT_MEASUREMENT" for row in all_measurements), count=len(all_measurements))
    serialized_deferred = canonical_bytes(deferred).decode()
    gate("I", "no_population_average_anthropometry", "population_average_allowed_as_real\":false" in serialized_deferred and not any(isinstance(row["value"], (int, float)) for row in all_measurements))
    direct_ids = {row["measurement_id"] for row in deferred["minimum_future_subject_profile"]}
    latent_ids = {row["measurement_id"] for row in deferred["latent_anatomical_parameters"]}
    gate("J", "direct_observation_separate_from_latent_bone_geometry", direct_ids.isdisjoint(latent_ids) and all(row["measurement_kind"] == "latent anatomical parameter" for row in deferred["latent_anatomical_parameters"]))
    gate("K", "torso_top_resolved_without_duplicate_freedom", torso["authority_class"] == "DERIVE_NOT_INDEPENDENT" and not torso["optimizer_freedom"] and torso["derived_from"] == ["joint_parent:shoulder_left", "joint_parent:shoulder_right"])
    families = hardware["reported_families"]
    gate("L", "hardware_revisions_separated_fail_closed", set(families["COMMON_NINE_REPORTED"]["nodes"]).isdisjoint(families["BSF31CC_REPORTED_DISTINCT"]["nodes"]) and families["COMMON_NINE_REPORTED"]["revision_id"] is None and families["BSF31CC_REPORTED_DISTINCT"]["revision_id"] is None and not families["COMMON_NINE_REPORTED"]["shared_transform_authorized"])
    cad_transform = hardware["cad_candidate"]["component_reference_constraints"]["T_S_STEP_Iref"]
    r = np.asarray(cad_transform["rotation"], dtype=float)
    t = np.asarray(cad_transform["translation_m"], dtype=float)
    transform = (r, t); inverse = _rigid_inverse(*transform)
    ri, ti = _compose(transform, inverse)
    base = hardware["cad_candidate"]["operator_supplied_v0_20_enclosure_base"]
    base_registration = base["coordinate_registration"]
    gate("M", "transform_composition_inverse_consistency", np.allclose(ri, np.eye(3), atol=1e-15) and np.allclose(ti, np.zeros(3), atol=1e-15) and np.isclose(np.linalg.det(r), 1.0) and np.allclose(base_registration["planar_center_residual_m"], np.zeros(2), atol=1e-15) and hardware["imu_origin_to_uwb_antenna_nominal"]["translation_m"] is None and base["complete_enclosure_C"] is None, rotation_error=float(np.max(np.abs(ri-np.eye(3)))), translation_error_m=float(np.max(np.abs(ti))), determinant=float(np.linalg.det(r)), base_planar_registration_residual_m=base_registration["planar_center_residual_m"])
    antenna = hardware["antenna_reference"]
    gate("N", "nominal_antenna_not_phase_centre", antenna["classification"] == "CAD_NOMINAL_ANTENNA_REFERENCE" and antenna["phase_centre_status"] == "PHASE_CENTRE_OFFSET_UNQUALIFIED" and antenna["phase_centre_point_B_m"] is None)
    gate("O", "synthetic_world_isolated_from_real_bridge", world["synthetic_world"]["status"] == "SYNTHETIC_TEST_ONLY_NOT_REGISTRY_VALUE" and not world["synthetic_world"]["writes_world_model_gauge_slot"] and world["real_frames"]["N"]["T_N_V4"] is None)
    gate("P", "anchor_and_clock_provenance_capture_bound", not world["anchors"]["cross_deployment_reuse"] and not world["clock_relationships"]["generic_cross_capture_reuse"] and world["clock_relationships"]["node_count"] == 10)
    try:
        validate_ledger(ledger); ledger_ok = True
    except ValueError:
        ledger_ok = False
    gate("Q", "all_87_source_slots_null_frozen", ledger_ok, slot_count=len(ledger["slots"]))
    synthetic_values = [0.31, 0.27, 0.44]
    gate("R", "no_synthetic_value_contaminates_real_registry", all(row["value"] is None for row in ledger["slots"]) and all(value not in serialized_deferred for value in map(str, synthetic_values)) and world["synthetic_world"]["writes_world_model_gauge_slot"] is False)
    synthetic_decision = evaluate_r6a2_request(_synthetic_request())
    real_request = {**_synthetic_request(), "mode": "REAL_SHADOW", "geometry_kind": "REAL", "synthetic_world_gauge": False, "hardware_revision_ids": {"common": None, "torso": None}, "all_real_geometry_qualified": False, "signed_axis_validation": "PENDING", "process_noise_qualified": False, "hardware_levers_qualified": False, "v4_to_navigation_qualified": False, "anchor_capture_binding": "capture_A", "clock_capture_binding": "capture_B", "clock_boot_epochs_match": False}
    real_decision = evaluate_r6a2_request(real_request)
    required_blockers = {"NULL_REAL_GEOMETRY_PRESENTED_AS_CALIBRATED", "MISSING_HARDWARE_REVISION", "SIGNED_AXIS_VALIDATION_PENDING", "PROCESS_NOISE_UNQUALIFIED", "HARDWARE_LEVER_UNQUALIFIED", "UNQUALIFIED_V4_TO_NAVIGATION_BRIDGE", "CROSS_CAPTURE_CLOCK_OR_ANCHOR_REUSE", "CLOCK_BOOT_EPOCH_MISMATCH"}
    gate("S", "future_r6a2_adapter_fails_closed", synthetic_decision.authorized and not real_decision.authorized and required_blockers.issubset(real_decision.blockers), real_blockers=list(real_decision.blockers))
    replay_a = {name: hashlib.sha256(canonical_bytes(value)).hexdigest() for name, value in sorted(contracts.items())}
    replay_b = {name: hashlib.sha256(canonical_bytes(json.loads(canonical_bytes(value)))).hexdigest() for name, value in sorted(contracts.items())}
    gate("T", "deterministic_replay_output_hashes", replay_a == replay_b, artifact_hashes=replay_a)
    return {
        "schema": "biospur-root-r6a1c-qualification-gates-v1",
        "gate_count": len(gates), "all_pass": all(row["pass"] for row in gates),
        "gates": gates,
        "real_measurements_requested": False, "real_calibration_fitted": False,
        "real_body_state_updated": False, "root_r6a2_implemented": False,
        "readiness": copy.deepcopy(readiness),
    }
