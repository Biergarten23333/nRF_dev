"""Measurement-conditioned, non-clinical visualization centerline V1.

This module never consumes a capture payload.  It compiles immutable graphical
proxy geometry and bounded shared placement inputs.  Strict V4.1 scientific
joint-centre semantics remain outside this namespace.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable, Mapping


DISCLAIMER = (
    "Non-clinical visualization centerline. Axial segment twist and clinical "
    "joint centres/angles are not validated."
)
SCHEMA = "biospur-visualization-centerline-v1"
NODES = (
    "BSF3C79",
    "BSFC2CC",
    "BSF44AD",
    "BSF6C53",
    "BSF8BC4",
    "BSF1120",
    "BSF31CC",
    "BSFAA61",
    "BSFB165",
    "BSFEC35",
)
IDENTITY_MAP = {
    "BSF31CC": "CentralSensor",
    "BSFC2CC": "PelvisSensor",
    "BSFAA61": "ElbowProxy_L",
    "BSF1120": "ElbowProxy_R",
    "BSFB165": "WristProxy_L",
    "BSFEC35": "WristProxy_R",
    "BSF44AD": "KneeProxy_L",
    "BSF3C79": "KneeProxy_R",
    "BSF6C53": "AnkleProxy_L",
    "BSF8BC4": "AnkleProxy_R",
}
RENDERING_MEASUREMENTS = {
    "rendering_upper_arm_length_L": "acromion_to_lateral_epicondyle_L",
    "rendering_upper_arm_length_R": "acromion_to_lateral_epicondyle_R",
    "rendering_forearm_length_L": "lateral_epicondyle_to_wrist_styloid_midpoint_L",
    "rendering_forearm_length_R": "lateral_epicondyle_to_wrist_styloid_midpoint_R",
    "rendering_thigh_length_L": "greater_trochanter_to_lateral_knee_landmark_L",
    "rendering_thigh_length_R": "greater_trochanter_to_lateral_knee_landmark_R",
    "rendering_shank_length_L": "lateral_knee_landmark_to_malleolar_midpoint_L",
    "rendering_shank_length_R": "lateral_knee_landmark_to_malleolar_midpoint_R",
}
REQUIRED_BODY_MEASUREMENTS = set(RENDERING_MEASUREMENTS.values()) | {
    "biacromial_breadth",
    "ASIS_breadth",
    "C7_to_mid_PSIS",
    "pelvis_anterior_posterior_depth",
}
FOOT_MEASUREMENTS = {
    "foot_length_L",
    "foot_length_R",
    "floor_to_malleolar_midpoint_L",
    "floor_to_malleolar_midpoint_R",
    "rear_heel_stack_height_L",
    "rear_heel_stack_height_R",
    "forefoot_stack_height_L",
    "forefoot_stack_height_R",
}
HARRINGTON = {
    "derivation_name": "Harrington pelvis-only hip joint centre regression",
    "derivation_version": "Harrington_et_al_2007_equations_5_7",
    "doi": "10.1016/j.jbiomech.2006.02.003",
    "population_regression_derived": True,
    "published_LOOCV_RMSE_mm": {
        "anterior": 4.7,
        "outward": 5.5,
        "superior": 5.9,
    },
}
TOPOLOGY_EDGES = (
    ("C7Proxy", "PelvisProxy"),
    ("C7Proxy", "AcromionProxy_L"),
    ("AcromionProxy_L", "ElbowProxy_L"),
    ("ElbowProxy_L", "WristProxy_L"),
    ("C7Proxy", "AcromionProxy_R"),
    ("AcromionProxy_R", "ElbowProxy_R"),
    ("ElbowProxy_R", "WristProxy_R"),
    ("PelvisProxy", "MidASISProxy"),
    ("MidASISProxy", "HipRegression_L"),
    ("HipRegression_L", "KneeProxy_L"),
    ("KneeProxy_L", "AnkleProxy_L"),
    ("MidASISProxy", "HipRegression_R"),
    ("HipRegression_R", "KneeProxy_R"),
    ("KneeProxy_R", "AnkleProxy_R"),
)
FORBIDDEN_PATH_PARTS = {
    "logs",
    "continuous_raw",
    "calibration_ledger",
    "heldout",
    "held_out",
    "walk",
    "final_still",
}
SHARED_WEARING_EVIDENCE = {
    "MEASURED_CAPTURE_DAY",
    "PHOTO_DERIVED",
    "OPERATOR_RECOLLECTION",
}


class VisualizationInputError(ValueError):
    """Input would require guessing or crossing the capture firewall."""


def validate_gates(gates: Mapping[str, Any]) -> None:
    """Reject incomplete or weakened product contracts before any compilation."""
    if gates.get("schema") != "biospur-visualization-centerline-gates-v1":
        raise VisualizationInputError("unexpected visualization gate schema")
    geometry = gates.get("geometry_gates", {})
    calibration = gates.get("calibration_gates", {})
    rendering = gates.get("rendering", {})
    firewall = gates.get("firewall", {})
    if not geometry.get("require_fixed_left_right_identity", False):
        raise VisualizationInputError("fixed left/right identity is mandatory")
    if not geometry.get("require_connected_centerline", False):
        raise VisualizationInputError("connected centerline is mandatory")
    if not calibration.get("quotient_removes_axial_twist", False):
        raise VisualizationInputError("axial twist must be removed from the visualization quotient")
    if not calibration.get("placement_at_bound_requires_disclosure", False):
        raise VisualizationInputError("placement bound hits must require disclosure")
    if not calibration.get("mandatory_action_dependence_report_required", False):
        raise VisualizationInputError("mandatory-action dependence must be reported separately")
    if not calibration.get("optional_action_removal_pass_required", False):
        raise VisualizationInputError("optional-action removal stability is mandatory")
    if rendering.get("fixed_axes_required") is not True:
        raise VisualizationInputError("fixed rendering axes are mandatory")
    if rendering.get("visual_interpolation_analysis_use") != "FORBIDDEN":
        raise VisualizationInputError("render interpolation must remain excluded from analysis")
    if rendering.get("watermark_every_frame") is not True:
        raise VisualizationInputError("the non-clinical watermark is mandatory on every frame")
    if firewall.get("walk_open_count") != 1 or firewall.get("final_still") != "SEALED":
        raise VisualizationInputError("walk/final-still firewall contract was weakened")
    positive_paths = (
        (geometry, "maximum_bone_length_change_mm"),
        (geometry, "minimum_positive_rendering_length_mm"),
        (geometry, "maximum_direct_repeat_range_mm"),
        (calibration, "observability_relative_singular_value_threshold"),
        (calibration, "null_maximum_segment_axis_angular_change_rad"),
        (calibration, "null_maximum_graphical_node_displacement_mm"),
        (calibration, "null_maximum_antenna_displacement_mm"),
        (calibration, "multistart_maximum_relative_cost_difference"),
        (calibration, "repeatability_maximum_segment_axis_angular_change_rad"),
        (calibration, "multistart_maximum_graphical_node_displacement_mm"),
        (calibration, "multistart_maximum_antenna_displacement_mm"),
        (calibration, "interleaved_maximum_graphical_node_displacement_mm"),
        (calibration, "interleaved_maximum_placement_displacement_mm"),
        (calibration, "interleaved_maximum_antenna_displacement_mm"),
        (calibration, "optional_action_removal_maximum_segment_axis_angular_change_rad"),
        (calibration, "optional_action_removal_maximum_graphical_node_displacement_mm"),
        (calibration, "optional_action_removal_maximum_antenna_displacement_mm"),
        (calibration, "model_mismatch_maximum_normalized_residual_median"),
        (calibration, "model_mismatch_maximum_normalized_residual_p95"),
        (calibration, "placement_maximum_posterior_shift_sigma"),
        (calibration, "placement_minimum_bound_clearance_fraction"),
        (calibration, "placement_profile_maximum_graphical_node_displacement_mm"),
        (calibration, "placement_profile_maximum_segment_axis_angular_change_rad"),
        (calibration, "placement_profile_maximum_antenna_displacement_mm"),
    )
    for section, name in positive_paths:
        _finite_number(section.get(name), name, positive=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def assert_nonpayload_input(path: Path) -> None:
    resolved = path.resolve()
    collision = sorted({part.lower() for part in resolved.parts} & FORBIDDEN_PATH_PARTS)
    if collision:
        raise VisualizationInputError(f"capture/held-out path is forbidden: {collision}")
    if not resolved.is_file():
        raise VisualizationInputError(f"input is absent: {resolved}")


def read_csv(path: Path) -> list[dict[str, str]]:
    assert_nonpayload_input(path)
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(None in row for row in rows):
        raise VisualizationInputError(f"malformed CSV: {path}")
    return rows


def _finite_number(text: str, field: str, *, positive: bool = False) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise VisualizationInputError(f"{field} must be numeric") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        raise VisualizationInputError(f"{field} must be {'positive and ' if positive else ''}finite")
    return value


def _repeat_summary(row: Mapping[str, str], maximum_range_mm: float) -> dict[str, Any]:
    name = row["measurement_id"]
    repeats = [
        _finite_number(row[f"repeat_{index}_mm"].strip(), f"{name}.repeat_{index}", positive=True)
        for index in (1, 2, 3)
    ]
    spread = max(repeats) - min(repeats)
    if spread > maximum_range_mm:
        raise VisualizationInputError(
            f"{name} repeat range {spread:.6g} mm exceeds frozen {maximum_range_mm:.6g} mm gate"
        )
    resolution = _finite_number(
        row["instrument_resolution_mm"].strip(), f"{name}.instrument_resolution_mm", positive=True
    )
    for required in ("instrument", "actual_measurement_date", "operator"):
        if not row.get(required, "").strip():
            raise VisualizationInputError(f"{name}.{required} is required")
    sample_sd = statistics.stdev(repeats)
    standard_u = math.hypot(sample_sd / math.sqrt(3.0), resolution / math.sqrt(36.0))
    return {
        "raw_repeats_mm": repeats,
        "mean_mm": statistics.fmean(repeats),
        "repeat_range_mm": spread,
        "instrument": row.get("instrument", "").strip() or None,
        "instrument_resolution_mm": resolution,
        "measurement_standard_uncertainty_mm": standard_u,
        "provenance": "DIRECT_SURFACE_MEASUREMENT",
        "actual_measurement_date": row["actual_measurement_date"].strip(),
        "operator": row["operator"].strip(),
        "notes": row.get("notes", "").strip() or None,
    }


def _summarize_measurements(rows: Iterable[Mapping[str, str]], maximum_range_mm: float) -> dict[str, Any]:
    by_name = {row["measurement_id"]: row for row in rows}
    values: dict[str, Any] = {}
    missing: list[str] = []
    for name in sorted(REQUIRED_BODY_MEASUREMENTS):
        row = by_name.get(name)
        if row is None or not all(row.get(f"repeat_{index}_mm", "").strip() for index in (1, 2, 3)):
            missing.append(name)
            continue
        values[name] = _repeat_summary(row, maximum_range_mm)
    return {"values": values, "missing": missing, "complete": not missing}


def _derive_harrington(measurements: Mapping[str, Any]) -> dict[str, Any]:
    width = measurements["ASIS_breadth"]
    depth = measurements["pelvis_anterior_posterior_depth"]
    pw = width["mean_mm"]
    pd = depth["mean_mm"]
    u_pw = width["measurement_standard_uncertainty_mm"]
    u_pd = depth["measurement_standard_uncertainty_mm"]
    anterior = -0.24 * pd - 9.9
    outward = 0.33 * pw + 7.3
    superior = -0.30 * pw - 10.9
    measurement_u = [0.33 * u_pw, 0.24 * u_pd, 0.30 * u_pw]
    model_u = [
        HARRINGTON["published_LOOCV_RMSE_mm"]["outward"],
        HARRINGTON["published_LOOCV_RMSE_mm"]["anterior"],
        HARRINGTON["published_LOOCV_RMSE_mm"]["superior"],
    ]
    combined = [math.hypot(a, b) for a, b in zip(measurement_u, model_u)]
    return {
        **HARRINGTON,
        "origin": "MidASISProxy",
        "axes": "right/anterior/superior",
        "left_offset_mm": [-outward, anterior, superior],
        "right_offset_mm": [outward, anterior, superior],
        "measurement_standard_uncertainty_right_anterior_superior_mm": measurement_u,
        "model_RMSE_right_anterior_superior_mm": model_u,
        "combined_standard_uncertainty_right_anterior_superior_mm": combined,
        "label": "POPULATION_REGRESSION_DERIVED_GRAPHICAL_HIP_PROXY",
        "not_absolute_anatomical_ground_truth": True,
    }


def _add(a: list[float], b: list[float]) -> list[float]:
    return [a[index] + b[index] for index in range(3)]


def _distance(a: Iterable[float], b: Iterable[float]) -> float:
    return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))


def _connected(nodes: Iterable[str], edges: Iterable[tuple[str, str]]) -> bool:
    node_set = set(nodes)
    if not node_set:
        return False
    adjacency = {node: set() for node in node_set}
    for left, right in edges:
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)
    pending = [next(iter(node_set))]
    seen: set[str] = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        pending.extend(adjacency[current] - seen)
    return seen == node_set


def _compile_geometry(measurement_rows: list[dict[str, str]], gates: Mapping[str, Any]) -> dict[str, Any]:
    geometry_gates = gates["geometry_gates"]
    summary = _summarize_measurements(
        measurement_rows, float(geometry_gates["maximum_direct_repeat_range_mm"])
    )
    if not summary["complete"]:
        return {
            "status": "BLOCKED_OPERATOR_MEASUREMENTS_MISSING",
            "missing": summary["missing"],
            "SCIENTIFIC_CENTERLINE": "UNCHANGED_FROZEN_V4_1_BLOCKED",
            "VISUALIZATION_CENTERLINE": "BLOCKED_OPERATOR_MEASUREMENTS_MISSING",
            "Meskers_usage": "NOT_USED",
        }
    measured = summary["values"]
    lengths: dict[str, Any] = {}
    for output_name, measurement_name in RENDERING_MEASUREMENTS.items():
        source = measured[measurement_name]
        if source["mean_mm"] < float(geometry_gates["minimum_positive_rendering_length_mm"]):
            raise VisualizationInputError(
                f"{measurement_name} is below the frozen minimum rendering length"
            )
        lengths[output_name] = {
            "value_mm": source["mean_mm"],
            "standard_uncertainty_mm": source["measurement_standard_uncertainty_mm"],
            "raw_measurement_ref": measurement_name,
            "provenance": "DIRECT_PALPABLE_LANDMARK_SURFACE_CHORD",
            "immutable_during_replay": True,
            "internal_anatomical_joint_centre_length": False,
        }
    torso = measured["C7_to_mid_PSIS"]["mean_mm"]
    pelvis_depth = measured["pelvis_anterior_posterior_depth"]["mean_mm"]
    shoulder_width = measured["biacromial_breadth"]["mean_mm"]
    hips = _derive_harrington(measured)
    pelvis = [0.0, 0.0, 0.0]
    mid_asis = [0.0, pelvis_depth, 0.0]
    c7 = [0.0, 0.0, torso]
    points = {
        "PelvisProxy": pelvis,
        "MidASISProxy": mid_asis,
        "C7Proxy": c7,
        "AcromionProxy_L": [-shoulder_width / 2.0, 0.0, torso],
        "AcromionProxy_R": [shoulder_width / 2.0, 0.0, torso],
    }
    points["ElbowProxy_L"] = _add(
        points["AcromionProxy_L"], [-lengths["rendering_upper_arm_length_L"]["value_mm"], 0.0, 0.0]
    )
    points["ElbowProxy_R"] = _add(
        points["AcromionProxy_R"], [lengths["rendering_upper_arm_length_R"]["value_mm"], 0.0, 0.0]
    )
    points["WristProxy_L"] = _add(
        points["ElbowProxy_L"], [-lengths["rendering_forearm_length_L"]["value_mm"], 0.0, 0.0]
    )
    points["WristProxy_R"] = _add(
        points["ElbowProxy_R"], [lengths["rendering_forearm_length_R"]["value_mm"], 0.0, 0.0]
    )
    points["HipRegression_L"] = _add(mid_asis, hips["left_offset_mm"])
    points["HipRegression_R"] = _add(mid_asis, hips["right_offset_mm"])
    points["KneeProxy_L"] = _add(
        points["HipRegression_L"], [0.0, 0.0, -lengths["rendering_thigh_length_L"]["value_mm"]]
    )
    points["KneeProxy_R"] = _add(
        points["HipRegression_R"], [0.0, 0.0, -lengths["rendering_thigh_length_R"]["value_mm"]]
    )
    points["AnkleProxy_L"] = _add(
        points["KneeProxy_L"], [0.0, 0.0, -lengths["rendering_shank_length_L"]["value_mm"]]
    )
    points["AnkleProxy_R"] = _add(
        points["KneeProxy_R"], [0.0, 0.0, -lengths["rendering_shank_length_R"]["value_mm"]]
    )
    provenance = {
        "C7Proxy": "DIRECT_C7_TO_MID_PSIS_ENDPOINT_GRAPHICAL_PROXY",
        "PelvisProxy": "DIRECT_MID_PSIS_GRAPHICAL_ORIGIN",
        "MidASISProxy": "DIRECT_PELVIS_AP_DEPTH_GRAPHICAL_PROXY",
        "AcromionProxy_L": "DIRECT_BIACROMIAL_ENDPOINT_NOT_GLENOHUMERAL_CENTRE",
        "AcromionProxy_R": "DIRECT_BIACROMIAL_ENDPOINT_NOT_GLENOHUMERAL_CENTRE",
        "ElbowProxy_L": "LATERAL_HUMERAL_EPICONDYLE_GRAPHICAL_PROXY",
        "ElbowProxy_R": "LATERAL_HUMERAL_EPICONDYLE_GRAPHICAL_PROXY",
        "WristProxy_L": "RADIAL_ULNAR_STYLOID_MIDPOINT_GRAPHICAL_PROXY",
        "WristProxy_R": "RADIAL_ULNAR_STYLOID_MIDPOINT_GRAPHICAL_PROXY",
        "HipRegression_L": hips["label"],
        "HipRegression_R": hips["label"],
        "KneeProxy_L": "LATERAL_FEMORAL_EPICONDYLE_GRAPHICAL_PROXY",
        "KneeProxy_R": "LATERAL_FEMORAL_EPICONDYLE_GRAPHICAL_PROXY",
        "AnkleProxy_L": "MEDIAL_LATERAL_MALLEOLAR_MIDPOINT_GRAPHICAL_PROXY",
        "AnkleProxy_R": "MEDIAL_LATERAL_MALLEOLAR_MIDPOINT_GRAPHICAL_PROXY",
    }
    expected_nodes = set(provenance)
    connected = _connected(expected_nodes, TOPOLOGY_EDGES)
    if geometry_gates["require_connected_centerline"] and not connected:
        raise VisualizationInputError("compiled visualization centerline is disconnected")
    geometry_core = {
        "schema": "biospur-visualization-proxy-geometry-v1",
        "rendering_lengths": lengths,
        "T_pose_proxy_points_right_anterior_superior_mm": points,
        "graphical_node_provenance": provenance,
        "topology_edges": [list(edge) for edge in TOPOLOGY_EDGES],
        "identity_map": IDENTITY_MAP,
        "Harrington_hip_proxy": hips,
        "Meskers_usage": "NOT_USED_NO_3D_DIGITIZATION_REQUIRED_FOR_VISUALIZATION",
        "bone_lengths_immutable": True,
        "centerline_connected": connected,
        "clinical_joint_centres": "NOT_VALIDATED",
        "clinical_joint_angles": "NOT_VALIDATED",
        "axial_segment_twist": "UNAVAILABLE",
        "absolute_anatomical_accuracy": "NOT_CLAIMED",
        "disclaimer": DISCLAIMER,
    }
    geometry_core["geometry_sha256"] = canonical_hash(geometry_core)
    return {
        "status": "GEOMETRY_COMPILED",
        "SCIENTIFIC_CENTERLINE": "UNCHANGED_FROZEN_V4_1_BLOCKED",
        "VISUALIZATION_CENTERLINE": "GEOMETRY_COMPILED_PENDING_PLACEMENT",
        "direct_measurements": summary,
        "geometry": geometry_core,
    }


def _wearing_rows(rows: list[dict[str, str]], scope: str, node: str = "ALL") -> dict[str, dict[str, str]]:
    return {
        row["field"]: row
        for row in rows
        if row.get("scope") == scope and row.get("node") == node
    }


def _hardware_repeat(row: Mapping[str, str], name: str, *, non_negative: bool = False) -> dict[str, Any]:
    repeats = [
        _finite_number(row[f"repeat_{index}_mm"].strip(), f"{name}.repeat_{index}", positive=not non_negative)
        for index in (1, 2, 3)
    ]
    if non_negative and any(value < 0 for value in repeats):
        raise VisualizationInputError(f"{name} repeats must be non-negative")
    resolution = _finite_number(
        row.get("instrument_resolution_mm", "").strip(),
        f"{name}.instrument_resolution_mm",
        positive=True,
    )
    for required in ("instrument", "actual_measurement_date", "operator", "photo_references"):
        if not row.get(required, "").strip():
            raise VisualizationInputError(f"{name}.{required} is required")
    sample_sd = statistics.stdev(repeats)
    return {
        "raw_repeats_mm": repeats,
        "mean_mm": statistics.fmean(repeats),
        "repeat_range_mm": max(repeats) - min(repeats),
        "instrument": row["instrument"].strip(),
        "instrument_resolution_mm": resolution,
        "measurement_standard_uncertainty_mm": math.hypot(
            sample_sd / math.sqrt(3.0), resolution / math.sqrt(36.0)
        ),
        "actual_measurement_date": row["actual_measurement_date"].strip(),
        "operator": row["operator"].strip(),
        "photo_references": row["photo_references"].strip(),
        "provenance": "DIRECT_OBSERVABLE_HARDWARE_MEASUREMENT",
    }


def _cross(left: list[float], right: list[float]) -> list[float]:
    return [
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    ]


def _orientation_from_observable_faces(
    antenna_edge: str, x_edge_relation: str, top_face_direction: str
) -> tuple[list[list[float]], dict[str, Any]]:
    edge = antenna_edge.strip().upper()
    relation = x_edge_relation.strip().upper()
    top = top_face_direction.strip().upper()
    edge_map = {
        "B_POS_X": ("x", 1.0),
        "B_NEG_X": ("x", -1.0),
        "B_POS_Y": ("y", 1.0),
        "B_NEG_Y": ("y", -1.0),
    }
    if edge not in edge_map:
        raise VisualizationInputError("pcb_edge_toward_antenna_end has an invalid selection")
    if relation not in {"PARALLEL", "PERPENDICULAR"}:
        raise VisualizationInputError("pcb_x_edge_relation_to_enclosure_long_axis must be PARALLEL/PERPENDICULAR")
    axis, sign = edge_map[edge]
    expected_relation = "PARALLEL" if axis == "x" else "PERPENDICULAR"
    if relation != expected_relation:
        raise VisualizationInputError(
            f"observable edge selections disagree: {edge} implies B+x is {expected_relation} to E long axis"
        )
    if top not in {"AWAY_FROM_BODY", "TOWARD_BODY"}:
        raise VisualizationInputError("pcb_top_component_face_points has an invalid selection")
    column_z = [0.0, 0.0, 1.0 if top == "AWAY_FROM_BODY" else -1.0]
    antenna_direction = [sign, 0.0, 0.0]
    if axis == "x":
        column_x = antenna_direction
        column_y = _cross(column_z, column_x)
    else:
        column_y = antenna_direction
        column_x = _cross(column_y, column_z)
    rotation = [
        [column_x[row], column_y[row], column_z[row]]
        for row in range(3)
    ]
    return rotation, {
        "antenna_edge_observation": edge,
        "B_x_edge_relation_to_E_long_axis": relation,
        "PCB_top_component_face_observation": top,
        "derived_axis_mapping": {
            "B_plus_x_in_E": column_x,
            "B_plus_y_in_E": column_y,
            "B_plus_z_in_E": column_z,
        },
        "sign_and_handedness_rule": "selected B antenna edge maps to +E+x; B+z follows visible top face; remaining column is a right-handed cross product",
    }


def _transform_point(rotation: list[list[float]], translation: list[float], point: list[float]) -> list[float]:
    return [
        translation[row] + sum(rotation[row][column] * point[column] for column in range(3))
        for row in range(3)
    ]


def _compile_direct_hardware(
    hardware_rows: list[dict[str, str]], hardware_provenance: Mapping[str, Any]
) -> dict[str, Any]:
    rows = {row["measurement_id"]: row for row in hardware_rows}
    numeric_required = (
        "enclosure_outer_long_mm",
        "enclosure_outer_short_mm",
        "enclosure_outer_thickness_mm",
        "U4_to_E_ANTENNA_END_SHORT_FACE_mm",
        "U4_to_E_NONANTENNA_END_SHORT_FACE_mm",
        "U4_to_E_POS_Y_LONG_SIDE_mm",
        "U4_to_E_NEG_Y_LONG_SIDE_mm",
        "PCB_top_plane_to_body_facing_face_mm",
        "PCB_mechanical_play_long_mm",
        "PCB_mechanical_play_short_mm",
        "PCB_mechanical_play_thickness_mm",
        "strap_width_mm",
    )
    categorical_required = (
        "pcb_edge_toward_antenna_end",
        "pcb_x_edge_relation_to_enclosure_long_axis",
        "pcb_top_component_face_points",
        "identical_PCB_enclosure_registration",
    )
    missing = [
        name
        for name in numeric_required
        if name not in rows or not all(rows[name].get(f"repeat_{index}_mm", "").strip() for index in (1, 2, 3))
    ]
    missing += [
        name
        for name in categorical_required
        if name not in rows or rows[name].get("categorical_value", "").strip() in {"", "SELECT"}
    ]
    if missing:
        return {
            "status": "BLOCKED_DIRECT_HARDWARE_MEASUREMENTS_MISSING",
            "missing": sorted(set(missing)),
            "hardware_source_status": hardware_provenance.get("status"),
        }
    for name in categorical_required:
        for required in ("actual_measurement_date", "operator", "photo_references"):
            if not rows[name].get(required, "").strip():
                raise VisualizationInputError(f"{name}.{required} is required")
    if rows["identical_PCB_enclosure_registration"]["categorical_value"].strip().upper() != "YES":
        return {
            "status": "BLOCKED_ASSEMBLIES_NOT_CONFIRMED_IDENTICAL",
            "reason": "one shared PCB-to-enclosure transform cannot be reused",
        }
    non_negative = {
        "PCB_mechanical_play_long_mm",
        "PCB_mechanical_play_short_mm",
        "PCB_mechanical_play_thickness_mm",
    }
    numeric = {
        name: _hardware_repeat(rows[name], name, non_negative=name in non_negative)
        for name in numeric_required
    }
    long = numeric["enclosure_outer_long_mm"]
    short = numeric["enclosure_outer_short_mm"]
    thickness = numeric["enclosure_outer_thickness_mm"]
    d_ant = numeric["U4_to_E_ANTENNA_END_SHORT_FACE_mm"]
    d_non = numeric["U4_to_E_NONANTENNA_END_SHORT_FACE_mm"]
    d_pos_y = numeric["U4_to_E_POS_Y_LONG_SIDE_mm"]
    d_neg_y = numeric["U4_to_E_NEG_Y_LONG_SIDE_mm"]
    d_body = numeric["PCB_top_plane_to_body_facing_face_mm"]
    if d_ant["mean_mm"] > long["mean_mm"] or d_non["mean_mm"] > long["mean_mm"]:
        raise VisualizationInputError("U4-to-short-face distance exceeds enclosure long dimension")
    if d_pos_y["mean_mm"] > short["mean_mm"] or d_neg_y["mean_mm"] > short["mean_mm"]:
        raise VisualizationInputError("U4-to-long-side distance exceeds enclosure short dimension")
    if d_body["mean_mm"] > thickness["mean_mm"]:
        raise VisualizationInputError("PCB-top-to-body-face distance exceeds enclosure thickness")
    x_candidates = [long["mean_mm"] / 2.0 - d_ant["mean_mm"], d_non["mean_mm"] - long["mean_mm"] / 2.0]
    y_candidates = [short["mean_mm"] / 2.0 - d_pos_y["mean_mm"], d_neg_y["mean_mm"] - short["mean_mm"] / 2.0]
    registration = [statistics.fmean(x_candidates), statistics.fmean(y_candidates), -thickness["mean_mm"] / 2.0 + d_body["mean_mm"]]
    registration_u = [
        math.sqrt((long["measurement_standard_uncertainty_mm"] / 2.0) ** 2 + (d_ant["measurement_standard_uncertainty_mm"] ** 2 + d_non["measurement_standard_uncertainty_mm"] ** 2) / 4.0),
        math.sqrt((short["measurement_standard_uncertainty_mm"] / 2.0) ** 2 + (d_pos_y["measurement_standard_uncertainty_mm"] ** 2 + d_neg_y["measurement_standard_uncertainty_mm"] ** 2) / 4.0),
        math.hypot(thickness["measurement_standard_uncertainty_mm"] / 2.0, d_body["measurement_standard_uncertainty_mm"]),
    ]
    rotation_E_from_B, orientation_provenance = _orientation_from_observable_faces(
        rows["pcb_edge_toward_antenna_end"]["categorical_value"],
        rows["pcb_x_edge_relation_to_enclosure_long_axis"]["categorical_value"],
        rows["pcb_top_component_face_points"]["categorical_value"],
    )
    play = [
        numeric["PCB_mechanical_play_long_mm"]["mean_mm"],
        numeric["PCB_mechanical_play_short_mm"]["mean_mm"],
        numeric["PCB_mechanical_play_thickness_mm"]["mean_mm"],
    ]
    rotation_uncertainty_deg = max(
        0.1,
        math.degrees(math.atan2(max(play), max(1.0, min(long["mean_mm"], short["mean_mm"])))),
    )
    u4_box_mil = hardware_provenance.get("cad", {}).get("U4_footprint_envelope_mil")
    package = hardware_provenance.get("datasheet", {}).get("package_dimensions_mm")
    if not u4_box_mil or not package:
        return {"status": "BLOCKED_VERIFIED_U4_OR_PACKAGE_EVIDENCE_MISSING"}
    phase_bounds = [
        [float(u4_box_mil[0]) * 0.0254, float(u4_box_mil[1]) * 0.0254],
        [float(u4_box_mil[2]) * 0.0254, float(u4_box_mil[3]) * 0.0254],
        [0.0, float(package[2])],
    ]
    phase_corners_E = [
        _transform_point(rotation_E_from_B, registration, [x, y, z])
        for x in phase_bounds[0]
        for y in phase_bounds[1]
        for z in phase_bounds[2]
    ]
    lower = [min(point[index] for point in phase_corners_E) for index in range(3)]
    upper = [max(point[index] for point in phase_corners_E) for index in range(3)]
    maximum_phase_radius_mm = max(
        math.sqrt(x * x + y * y + z * z)
        for x in phase_bounds[0]
        for y in phase_bounds[1]
        for z in phase_bounds[2]
    )
    first_order_rotation_position_u_mm = maximum_phase_radius_mm * math.sqrt(
        3.0 * math.radians(rotation_uncertainty_deg) ** 2
    )
    uncertainty = [
        math.sqrt(
            registration_u[index] ** 2
            + ((upper[index] - lower[index]) / math.sqrt(12.0)) ** 2
            + (play[index] / math.sqrt(12.0)) ** 2
            + first_order_rotation_position_u_mm ** 2
        )
        for index in range(3)
    ]
    if any(value <= 0 for value in uncertainty):
        raise VisualizationInputError("shared hardware uncertainty must remain non-zero")
    return {
        "status": "DIRECT_HARDWARE_COMPILED",
        "shared_PCB_to_enclosure": {
            "status": "BOUNDED_SET_NOT_EXACT_POINT",
            "frame_convention": {
                "B": "board frame at U4 CAD reference; axes follow verified PCB CAD",
                "E": "enclosure frame at geometric centre; +x antenna-end; +z away from body-facing face",
                "rotation": "R_E_from_B deterministically derived from named physical faces/edges; operator supplied no Euler angles",
            },
            "t_E_from_B_origin_mean_mm": registration,
            "R_E_from_B": rotation_E_from_B,
            "orientation_derivation": orientation_provenance,
            "translation_derivation": {
                "x_candidates_mm": x_candidates,
                "y_candidates_mm": y_candidates,
                "z_equation": "-enclosure_outer_thickness/2 + PCB_top_plane_to_body_facing_face",
                "source_rows": [
                    "enclosure_outer_long_mm", "enclosure_outer_short_mm", "enclosure_outer_thickness_mm",
                    "U4_to_E_ANTENNA_END_SHORT_FACE_mm", "U4_to_E_NONANTENNA_END_SHORT_FACE_mm",
                    "U4_to_E_POS_Y_LONG_SIDE_mm", "U4_to_E_NEG_Y_LONG_SIDE_mm",
                    "PCB_top_plane_to_body_facing_face_mm",
                ],
                "axis_and_sign_choices": "+E+x toward antenna-end face; +E+y toward E_POS_Y_LONG_SIDE; +E+z away from body-facing face",
            },
            "face_distance_closure_audit_mm": {
                "short_faces_sum_minus_outer_long": d_ant["mean_mm"] + d_non["mean_mm"] - long["mean_mm"],
                "long_sides_sum_minus_outer_short": d_pos_y["mean_mm"] + d_neg_y["mean_mm"] - short["mean_mm"],
            },
            "rotation_standard_uncertainty_deg_each_axis": rotation_uncertainty_deg,
            "rotation_uncertainty_provenance": "derived from measured mechanical play with 0.1 degree non-zero engineering floor",
            "first_order_rotation_contribution_to_position_standard_uncertainty_mm": first_order_rotation_position_u_mm,
            "RF_phase_centre_prior_in_U4_board_frame_mm": {
                "bounds": phase_bounds,
                "label": "DWM1001C_PRINTED_ANTENNA_AREA_WITHIN_VERIFIED_U4_MODULE_ENVELOPE",
                "exact_point": False,
            },
            "enclosure_origin_to_RF_phase_centre_bounds_E_mm": {"lower": lower, "upper": upper},
            "combined_standard_uncertainty_mm": uncertainty,
            "assembly_play_E_long_short_thickness_mm": play,
            "reuse_across_nodes": list(NODES),
        },
        "direct_hardware_measurements": numeric,
        "labelled_photo_references": sorted({rows[name]["photo_references"].strip() for name in (*numeric_required, *categorical_required)}),
    }


def _compile_wearing_placement(wearing_rows: list[dict[str, str]]) -> dict[str, Any]:
    shared = _wearing_rows(wearing_rows, "SHARED")
    required = (
        "shared_capture_wearing_evidence_basis",
        "body_facing_enclosure_face",
        "enclosure_long_axis_rule",
        "antenna_end_direction_rule",
        "attachment_surface",
        "attachment_convention",
        "likely_slip",
    )
    missing = [
        name for name in required
        if name not in shared or shared[name].get("categorical_value", "").strip() in {"", "SELECT", "DESCRIBE"}
    ]
    evidence = shared.get("shared_capture_wearing_evidence_basis", {})
    wearing_status = evidence.get("evidence_status", "").strip()
    wearing_reference = evidence.get("evidence_reference", "").strip()
    if wearing_status in {"", "UNSET"}:
        missing.append("shared_capture_wearing_evidence_basis.evidence_status")
    if not wearing_reference:
        missing.append("shared_capture_wearing_evidence_basis.evidence_reference")
    default_rows = _wearing_rows(wearing_rows, "ENGINEERING_DEFAULT")
    expected_bounds = {
        "enclosure_center_to_graphical_landmark_x": (-50.0, 50.0, "mm"),
        "enclosure_center_to_graphical_landmark_y": (-50.0, 50.0, "mm"),
        "enclosure_center_to_graphical_landmark_z": (-50.0, 50.0, "mm"),
        "placement_rotation_about_x": (-30.0, 30.0, "deg"),
        "placement_rotation_about_y": (-30.0, 30.0, "deg"),
        "placement_rotation_about_z": (-30.0, 30.0, "deg"),
    }
    for name in expected_bounds:
        if name not in default_rows:
            missing.append(name)
    if missing:
        return {"status": "BLOCKED_SHARED_WEARING_CONVENTION_MISSING", "missing": sorted(set(missing))}
    if wearing_status not in SHARED_WEARING_EVIDENCE:
        raise VisualizationInputError("shared capture wearing evidence has an invalid status")
    if wearing_status == "MEASURED_CAPTURE_DAY" and "contemporary" not in wearing_reference.lower():
        raise VisualizationInputError("MEASURED_CAPTURE_DAY requires explicitly identified contemporary evidence")
    placement_bounds: dict[str, list[float]] = {}
    for name, (expected_low, expected_high, expected_units) in expected_bounds.items():
        row = default_rows[name]
        if row.get("prior_class", "").strip() != "DEFAULT_ENGINEERING_PRIOR":
            raise VisualizationInputError(f"{name} must remain labelled DEFAULT_ENGINEERING_PRIOR")
        if row.get("evidence_status", "").strip() == "MEASURED_CAPTURE_DAY":
            raise VisualizationInputError("default engineering bounds cannot be labelled MEASURED_CAPTURE_DAY")
        if row.get("evidence_status", "").strip() != "ENGINEERING_DEFAULT":
            raise VisualizationInputError(f"{name} evidence status must remain ENGINEERING_DEFAULT")
        low = _finite_number(row.get("lower_bound"), f"{name}.lower_bound")
        high = _finite_number(row.get("upper_bound"), f"{name}.upper_bound")
        if (low, high, row.get("units", "").strip()) != (expected_low, expected_high, expected_units):
            raise VisualizationInputError(f"{name} fixed default bounds must not be tightened or relabelled")
        placement_bounds[name] = [low, high]
    placement_priors: dict[str, Any] = {}
    override_audit: dict[str, Any] = {}
    for node in NODES:
        rows = _wearing_rows(wearing_rows, "NODE_OVERRIDE", node)
        marker = rows.get("override_present")
        override = marker is not None and marker.get("categorical_value", "").strip().upper() == "YES"
        if override:
            evidence_status = marker.get("evidence_status", "").strip()
            evidence_reference = marker.get("evidence_reference", "").strip()
            if evidence_status not in {"PHOTO_DERIVED", "OPERATOR_RECOLLECTION", "MEASURED_CAPTURE_DAY"}:
                raise VisualizationInputError(f"{node} override has invalid evidence status")
            if not evidence_reference:
                raise VisualizationInputError(f"{node} override lacks evidence reference/recollection")
            if evidence_status == "MEASURED_CAPTURE_DAY" and "contemporary" not in evidence_reference.lower():
                raise VisualizationInputError(
                    f"{node} MEASURED_CAPTURE_DAY override requires explicitly identified contemporary evidence"
                )
            description = marker.get("notes", "").strip()
            if not description or description.startswith("Optional categorical"):
                raise VisualizationInputError(f"{node} override lacks a categorical placement description")
            provenance = evidence_status
            reference = evidence_reference
        else:
            description = None
            provenance = "CALIBRATION_ESTIMATED_FROM_DEFAULT_ENGINEERING_PRIOR"
            reference = wearing_reference
        placement_priors[node] = {
            "bounds": dict(placement_bounds),
            "bounds_provenance": "DEFAULT_ENGINEERING_PRIOR_NOT_CAPTURE_DAY_MEASUREMENT",
            "provenance": provenance,
            "evidence_reference": reference,
            "categorical_override_description": description,
            "estimate_as_calibration_only_nuisance": True,
            "non_zero_uncertainty_required": True,
        }
        override_audit[node] = {"override": override, "provenance": provenance}
    return {
        "status": "SHARED_WEARING_PLACEMENT_COMPILED",
        "wearing_rules": {
            name: shared[name]["categorical_value"].strip()
            for name in required
        },
        "shared_evidence": {"capture_wearing": {"status": wearing_status, "reference": wearing_reference}},
        "default_engineering_bounds": placement_bounds,
        "placement_priors": placement_priors,
        "override_audit": override_audit,
    }


def _foot_status(shoe_rows: list[dict[str, str]] | None) -> dict[str, Any]:
    if not shoe_rows:
        return {
            "status": "BLOCKED_SHOE_GEOMETRY_INCOMPLETE",
            "missing": sorted(FOOT_MEASUREMENTS),
            "blocks_visualization_centerline": False,
        }
    by_name = {row["measurement_id"]: row for row in shoe_rows}
    missing = [
        name
        for name in sorted(FOOT_MEASUREMENTS)
        if name not in by_name or not all(by_name[name].get(f"repeat_{index}_mm", "").strip() for index in (1, 2, 3))
    ]
    return {
        "status": "PASS" if not missing else "BLOCKED_SHOE_GEOMETRY_INCOMPLETE",
        "missing": missing,
        "blocks_visualization_centerline": False,
    }


def compile_visualization_inputs(
    measurement_rows: list[dict[str, str]],
    hardware_measurement_rows: list[dict[str, str]],
    wearing_rows: list[dict[str, str]],
    hardware_provenance: Mapping[str, Any],
    gates: Mapping[str, Any],
    *,
    shoe_rows: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Compile only non-payload inputs; never open a calibration or held-out ledger."""
    validate_gates(gates)
    geometry = _compile_geometry(measurement_rows, gates)
    hardware = _compile_direct_hardware(hardware_measurement_rows, hardware_provenance)
    wearing = _compile_wearing_placement(wearing_rows)
    foot = _foot_status(shoe_rows)
    geometry_ready = geometry["status"] == "GEOMETRY_COMPILED"
    hardware_ready = hardware["status"] == "DIRECT_HARDWARE_COMPILED"
    wearing_ready = wearing["status"] == "SHARED_WEARING_PLACEMENT_COMPILED"
    result = {
        "schema": SCHEMA,
        "product_separation": {
            "SCIENTIFIC_CENTERLINE": "UNCHANGED_FROZEN_V4_1_BLOCKED",
            "VISUALIZATION_CENTERLINE": (
                "INPUTS_READY_CALIBRATION_LEDGER_STILL_SEALED"
                if geometry_ready and hardware_ready and wearing_ready
                else "BLOCKED_OPERATOR_INPUTS_MISSING"
            ),
        },
        "geometry": geometry,
        "shared_hardware": hardware,
        "shared_wearing_and_placement": wearing,
        "foot_rendering": foot,
        "gates_sha256": canonical_hash(gates),
        "capture_payload_opened": False,
        "calibration_ledger_opened": False,
        "walk_opened": False,
        "final_still_opened": False,
        "Meskers_usage": "NOT_USED_IN_VISUALIZATION_PROXY_PATH",
        "Harrington_usage": "POPULATION_REGRESSION_WITH_PUBLISHED_MODEL_ERROR",
        "disclaimer": DISCLAIMER,
    }
    result["input_manifest_sha256"] = canonical_hash(result)
    return result


def audit_replay_geometry(
    geometry: Mapping[str, Any],
    frames: Iterable[Mapping[str, Iterable[float]]],
    identity_map: Mapping[str, str],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail if a replay changes proxy lengths, identities or connectivity."""
    expected_identity = geometry["identity_map"]
    identity_ok = dict(identity_map) == expected_identity
    tolerance = float(gates["geometry_gates"]["maximum_bone_length_change_mm"])
    lengths = geometry["rendering_lengths"]
    expected_edges = {
        ("AcromionProxy_L", "ElbowProxy_L"): lengths["rendering_upper_arm_length_L"]["value_mm"],
        ("AcromionProxy_R", "ElbowProxy_R"): lengths["rendering_upper_arm_length_R"]["value_mm"],
        ("ElbowProxy_L", "WristProxy_L"): lengths["rendering_forearm_length_L"]["value_mm"],
        ("ElbowProxy_R", "WristProxy_R"): lengths["rendering_forearm_length_R"]["value_mm"],
        ("HipRegression_L", "KneeProxy_L"): lengths["rendering_thigh_length_L"]["value_mm"],
        ("HipRegression_R", "KneeProxy_R"): lengths["rendering_thigh_length_R"]["value_mm"],
        ("KneeProxy_L", "AnkleProxy_L"): lengths["rendering_shank_length_L"]["value_mm"],
        ("KneeProxy_R", "AnkleProxy_R"): lengths["rendering_shank_length_R"]["value_mm"],
    }
    maximum_change = 0.0
    disconnected_frames: list[int] = []
    frame_count = 0
    for frame_index, frame in enumerate(frames):
        frame_count += 1
        if not _connected(frame.keys(), TOPOLOGY_EDGES):
            disconnected_frames.append(frame_index)
            continue
        for edge, expected in expected_edges.items():
            if edge[0] not in frame or edge[1] not in frame:
                disconnected_frames.append(frame_index)
                break
            maximum_change = max(maximum_change, abs(_distance(frame[edge[0]], frame[edge[1]]) - expected))
    pass_gate = (
        identity_ok
        and not disconnected_frames
        and frame_count > 0
        and maximum_change <= tolerance
    )
    return {
        "pass": pass_gate,
        "identity_map_fixed": identity_ok,
        "frame_count": frame_count,
        "disconnected_frames": disconnected_frames,
        "maximum_bone_length_change_mm": maximum_change,
        "gate_mm": tolerance,
        "geometry_sha256": geometry["geometry_sha256"],
    }
