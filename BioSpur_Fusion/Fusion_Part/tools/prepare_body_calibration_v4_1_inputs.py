#!/usr/bin/env python3
"""Prepare V4.1 measurement inputs without opening any capture payload.

This is deliberately an input compiler, not a calibration runner.  It reads
only the operator CSV forms, the placement questionnaire, and explicitly
supplied hardware-design evidence.  It never edits the frozen V4.1 input or
historical output directories.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import statistics
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "biospur-body-input-preparation-v4.1.0"
SESSION = "v47_ten_node_body_calibration_20260814_093601"
EXPECTED_FORM_NAMES = {
    "v47_subject_measurement_form.csv",
    "v47_shoe_measurement_form.csv",
    "v47_capture_placement_questionnaire.md",
}
FORBIDDEN_INPUT_PARTS = {
    "logs",
    "continuous_raw",
    "calibration_ledger",
    "walk",
    "final_still",
    "held_out",
    "heldout",
}

HARRINGTON = {
    "name": "Harrington pelvis-only hip joint centre regression",
    "version": "Harrington_et_al_2007_equations_5_7",
    "doi": "10.1016/j.jbiomech.2006.02.003",
    "reference": "https://pubmed.ncbi.nlm.nih.gov/16584737/",
    "equations_mm": {
        "anterior": "-0.24 * pelvis_depth - 9.9",
        "outward_magnitude": "0.33 * ASIS_breadth + 7.3",
        "superior": "-0.30 * ASIS_breadth - 10.9",
    },
    # Directional LOOCV residuals reported for the single-variable equations.
    # They are kept distinct from repeat/instrument uncertainty in the output.
    "published_LOOCV_RMSE_mm": {
        "anterior": 4.7,
        "outward": 5.5,
        "superior": 5.9,
    },
    "joint_centre_convention": (
        "Origin is mid-ASIS; body axes are right/anterior/superior. "
        "Both centres are posterior and inferior; the right centre has "
        "positive right coordinate and the left centre negative right coordinate."
    ),
}

MESKERS = {
    "name": "Meskers five-scapular-landmark glenohumeral rotation-centre regression",
    "version": "Meskers_et_al_1998_Table_2",
    "doi": "10.1016/S0021-9290(97)00101-2",
    "reference": "https://pubmed.ncbi.nlm.nih.gov/9596544/",
    "required_landmarks": ["AC", "AA", "TS", "AI", "PC"],
    "validation_RMSE_local_xyz_mm": [2.32, 2.68, 3.04],
    "equations_mm": {
        "x": "18.9743 + 0.2434 PC_x + 0.2341 AI_x + 0.1590 |AI-AA| + 0.0558 PC_y",
        "y": "-3.8791 - 0.3940 |AC-AA| + 0.1732 PC_y + 0.1205 AI_x - 0.1002 |AC-PC|",
        "z": "9.2629 + 1.0255 PC_z - 0.2403 PC_y + 0.1720 |TS-PC|",
    },
    "joint_centre_convention": (
        "Meskers local scapula frame, origin AA. Input digitizer frame is "
        "right/anterior/superior. Left landmarks are reflected into a right-side "
        "anatomical convention before regression and reflected back afterwards."
    ),
}

CAD_EXPECTED_SHA256 = "d70946843e9857b14c6b91a3ea4ab1f873be97aa61a1e5e77bf74f4b64ec8140"
CAD_LINEAGE_COMMIT = "c45dee7c662878d1c0e61f4d0682b5aa89301ac5"
CAD_LINEAGE_PATH = "BioSpur_Fusion/B306_Part/tools/analyze_v47_c2cc_rotation_aware.py"
DWM_DATASHEET_EXPECTED_SHA256 = "3e8efcac15663ce84704de7735fea410b94ccbf86d32541a224e6759f13845ac"
DWM_PACKAGE_MM = [19.1, 26.2, 2.6]


class InputError(ValueError):
    """An operator input cannot be interpreted without guessing."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def assert_safe_operator_input(path: Path) -> None:
    resolved = path.resolve()
    if path.name not in EXPECTED_FORM_NAMES:
        raise InputError(f"unexpected operator input filename: {path.name}")
    lowered = {part.lower() for part in resolved.parts}
    collision = sorted(lowered & FORBIDDEN_INPUT_PARTS)
    if collision:
        raise InputError(f"capture/held-out path is forbidden: {collision}")
    if not resolved.is_file():
        raise InputError(f"operator input is absent: {resolved}")


def assert_safe_output(path: Path) -> None:
    resolved = path.resolve()
    lowered = {part.lower() for part in resolved.parts}
    collision = sorted(lowered & FORBIDDEN_INPUT_PARTS)
    if collision or any(part.startswith("analysis_body_fusion_v") for part in lowered):
        raise InputError(f"historical/capture output path is forbidden: {collision or resolved}")
    if resolved.exists():
        if not resolved.is_dir() or any(resolved.iterdir()):
            raise InputError(f"output directory must be new or empty: {resolved}")


def _number(text: str, field: str, *, positive: bool) -> float:
    try:
        value = float(text)
    except (TypeError, ValueError) as exc:
        raise InputError(f"{field} must be numeric") from exc
    if not math.isfinite(value) or (positive and value <= 0):
        qualifier = "positive and finite" if positive else "finite"
        raise InputError(f"{field} must be {qualifier}")
    return value


def repeated_summary(
    row: dict[str, str], *, unit: str = "mm", allow_signed: bool = False
) -> dict[str, Any]:
    measurement_id = row["measurement_id"]
    repeats = [
        _number(row[f"repeat_{index}_mm"].strip(), f"{measurement_id}.repeat_{index}", positive=not allow_signed)
        for index in (1, 2, 3)
    ]
    resolution = _number(
        row["instrument_resolution_mm"].strip(),
        f"{measurement_id}.instrument_resolution_mm",
        positive=True,
    )
    sample_sd = statistics.stdev(repeats)
    repeat_sem = sample_sd / math.sqrt(3.0)
    quantization_u = resolution / math.sqrt(12.0 * 3.0)
    standard_u = math.hypot(repeat_sem, quantization_u)
    return {
        "status": "MEASURED",
        "raw_repeats": repeats,
        "unit": unit,
        "mean": statistics.fmean(repeats),
        "sample_standard_deviation": sample_sd,
        "repeat_standard_uncertainty_of_mean": repeat_sem,
        "instrument_resolution": resolution,
        "quantization_standard_uncertainty_of_mean": quantization_u,
        "combined_measurement_standard_uncertainty": standard_u,
        "uncertainty_rule": "sqrt((sample_sd/sqrt(3))^2 + (resolution/sqrt(12*3))^2)",
        "instrument": row.get("instrument", "").strip() or None,
        "source_reference": row.get("source_reference", "").strip() or None,
        "notes": row.get("notes", "").strip() or None,
    }


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows or any(None in row for row in rows):
        raise InputError(f"malformed CSV: {path}")
    ids = [row.get("measurement_id", "") for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise InputError(f"measurement_id must be present and unique: {path}")
    return rows


def summarize_subject(rows: list[dict[str, str]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    missing: list[str] = []
    for row in rows:
        measurement_id = row["measurement_id"]
        mode = row["measurement_mode"]
        if mode not in {"DIRECT_DISTANCE", "DIRECT_VERTICAL_DISTANCE", "DIRECT_MASS"}:
            continue
        if not all(row[f"repeat_{index}_mm"].strip() for index in (1, 2, 3)):
            missing.append(measurement_id)
            continue
        unit = "kg" if mode == "DIRECT_MASS" else "mm"
        values[measurement_id] = repeated_summary(row, unit=unit)
        values[measurement_id]["provenance_class"] = row["provenance_class"]
        values[measurement_id]["landmark_a"] = row["landmark_a"]
        values[measurement_id]["landmark_b"] = row["landmark_b_or_axis"]
        values[measurement_id]["excluded_from_derivation"] = row["provenance_class"] == "SANITY_CHECK_ONLY"
    required = {
        "acromion_to_lateral_epicondyle_L",
        "acromion_to_lateral_epicondyle_R",
        "lateral_epicondyle_to_wrist_styloid_midpoint_L",
        "lateral_epicondyle_to_wrist_styloid_midpoint_R",
        "greater_trochanter_to_lateral_knee_landmark_L",
        "greater_trochanter_to_lateral_knee_landmark_R",
        "lateral_knee_landmark_to_malleolar_midpoint_L",
        "lateral_knee_landmark_to_malleolar_midpoint_R",
        "biacromial_breadth",
        "ASIS_breadth",
        "C7_to_mid_PSIS",
        "pelvis_anterior_posterior_depth",
    }
    return {
        "schema": "biospur-direct-measurement-summary-v1",
        "values": values,
        "missing_required": sorted(required - values.keys()),
        "missing_optional_or_specialist": sorted(set(missing) - required),
        "all_required_complete": required <= values.keys(),
    }


def derive_hips(direct: dict[str, Any]) -> dict[str, Any]:
    values = direct["values"]
    refs = ["ASIS_breadth", "pelvis_anterior_posterior_depth"]
    if any(ref not in values for ref in refs):
        return {
            "status": "BLOCKED_MISSING_DIRECT_MEASUREMENTS",
            "missing": [ref for ref in refs if ref not in values],
            "derivation": HARRINGTON,
        }
    width = values["ASIS_breadth"]
    depth = values["pelvis_anterior_posterior_depth"]
    pw, pd = width["mean"], depth["mean"]
    u_pw = width["combined_measurement_standard_uncertainty"]
    u_pd = depth["combined_measurement_standard_uncertainty"]
    anterior = -0.24 * pd - 9.9
    outward = 0.33 * pw + 7.3
    superior = -0.30 * pw - 10.9
    measurement_u = {
        "right": 0.33 * u_pw,
        "anterior": 0.24 * u_pd,
        "superior": 0.30 * u_pw,
    }
    rmse = HARRINGTON["published_LOOCV_RMSE_mm"]
    combined_u = {
        axis: math.hypot(measurement_u[axis], rmse["outward" if axis == "right" else axis])
        for axis in ("right", "anterior", "superior")
    }
    left = [-outward, anterior, superior]
    right = [outward, anterior, superior]
    return {
        "status": "DERIVED",
        "derivation": HARRINGTON,
        "raw_measurement_refs": refs,
        "left_HJC_from_mid_ASIS_right_anterior_superior_mm": left,
        "right_HJC_from_mid_ASIS_right_anterior_superior_mm": right,
        "measurement_only_standard_uncertainty_mm": measurement_u,
        "model_LOOCV_RMSE_mm": rmse,
        "combined_standard_uncertainty_mm": combined_u,
        "combined_uncertainty_assumption": "independent zero-mean measurement and published model residuals",
        "derived_scalars": {
            "hip_joint_centre_width": {
                "value_mm": 2.0 * outward,
                "standard_uncertainty_mm": 2.0 * combined_u["right"],
                "definition": "distance between symmetric left/right Harrington centres",
            },
            "hip_joint_centre_vertical_offset": {
                "value_mm": superior,
                "standard_uncertainty_mm": combined_u["superior"],
                "definition": "signed superior coordinate of bilateral HJC line from mid-ASIS",
            },
        },
    }


Vector = tuple[float, float, float]


def vadd(a: Vector, b: Vector) -> Vector:
    return tuple(a[index] + b[index] for index in range(3))  # type: ignore[return-value]


def vsub(a: Vector, b: Vector) -> Vector:
    return tuple(a[index] - b[index] for index in range(3))  # type: ignore[return-value]


def vscale(a: Vector, scale: float) -> Vector:
    return tuple(value * scale for value in a)  # type: ignore[return-value]


def dot(a: Vector, b: Vector) -> float:
    return sum(a[index] * b[index] for index in range(3))


def cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def norm(a: Vector) -> float:
    return math.sqrt(dot(a, a))


def unit(a: Vector) -> Vector:
    length = norm(a)
    if length < 1e-9:
        raise InputError("degenerate scapular landmark geometry")
    return vscale(a, 1.0 / length)


def _scapula_frame(points: dict[str, Vector]) -> tuple[Vector, Vector, Vector]:
    x_axis = unit(vsub(points["AA"], points["TS"]))
    z_axis = unit(cross(vsub(points["AI"], points["AA"]), x_axis))
    # Input Y is anterior. Meskers z is selected posterior.
    if z_axis[1] > 0:
        z_axis = vscale(z_axis, -1.0)
    y_axis = unit(cross(z_axis, x_axis))
    if y_axis[2] <= 0:
        raise InputError("scapular frame is inconsistent with right/anterior/superior input axes")
    return x_axis, y_axis, z_axis


def _to_local(point: Vector, origin: Vector, axes: tuple[Vector, Vector, Vector]) -> Vector:
    delta = vsub(point, origin)
    return tuple(dot(delta, axis) for axis in axes)  # type: ignore[return-value]


def meskers_one_pass(points_global: dict[str, Vector], side: str) -> dict[str, Any]:
    if side not in {"L", "R"}:
        raise InputError(f"invalid side: {side}")
    mirror = -1.0 if side == "L" else 1.0
    points = {
        name: (mirror * point[0], point[1], point[2])
        for name, point in points_global.items()
    }
    axes = _scapula_frame(points)
    origin = points["AA"]
    local = {name: _to_local(point, origin, axes) for name, point in points.items()}
    distance = lambda a, b: norm(vsub(points[a], points[b]))
    pc, ai = local["PC"], local["AI"]
    gh_local = (
        18.9743 + 0.2434 * pc[0] + 0.2341 * ai[0] + 0.1590 * distance("AI", "AA") + 0.0558 * pc[1],
        -3.8791 - 0.3940 * distance("AC", "AA") + 0.1732 * pc[1] + 0.1205 * ai[0] - 0.1002 * distance("AC", "PC"),
        9.2629 + 1.0255 * pc[2] - 0.2403 * pc[1] + 0.1720 * distance("TS", "PC"),
    )
    gh_mirrored = origin
    for component, axis in zip(gh_local, axes):
        gh_mirrored = vadd(gh_mirrored, vscale(axis, component))
    gh_global = (mirror * gh_mirrored[0], gh_mirrored[1], gh_mirrored[2])
    return {
        "local_GH_xyz_mm": list(gh_local),
        "digitizer_global_GH_right_anterior_superior_mm": list(gh_global),
        "local_frame_axes_in_mirrored_digitizer_frame": {
            "x": list(axes[0]),
            "y": list(axes[1]),
            "z": list(axes[2]),
        },
    }


def _scapular_rows(rows: list[dict[str, str]], side: str) -> dict[str, dict[str, str]]:
    prefix = f"scapula_{side}_"
    return {row["measurement_id"][len(prefix):]: row for row in rows if row["measurement_id"].startswith(prefix)}


def derive_shoulders(rows: list[dict[str, str]]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "derivation": MESKERS,
        "digitizer_frame": "right/anterior/superior, millimetres, common across both sides and each pass",
        "sides": {},
    }
    global_means: dict[str, Vector] = {}
    for side in ("L", "R"):
        indexed = _scapular_rows(rows, side)
        required = [f"{landmark}_{axis}" for landmark in MESKERS["required_landmarks"] for axis in "xyz"]
        missing = [key for key in required if key not in indexed or not all(indexed[key][f"repeat_{i}_mm"].strip() for i in (1, 2, 3))]
        if missing:
            result["sides"][side] = {
                "status": "BLOCKED_MISSING_3D_SCAPULAR_LANDMARKS",
                "missing": missing,
            }
            continue
        passes: list[dict[str, Any]] = []
        for repeat_index in (1, 2, 3):
            points: dict[str, Vector] = {}
            for landmark in MESKERS["required_landmarks"]:
                points[landmark] = tuple(
                    _number(
                        indexed[f"{landmark}_{axis}"][f"repeat_{repeat_index}_mm"],
                        f"scapula_{side}_{landmark}_{axis}.repeat_{repeat_index}",
                        positive=False,
                    )
                    for axis in "xyz"
                )  # type: ignore[assignment]
            passes.append(meskers_one_pass(points, side))
        local_values = [entry["local_GH_xyz_mm"] for entry in passes]
        global_values = [entry["digitizer_global_GH_right_anterior_superior_mm"] for entry in passes]
        local_mean = [statistics.fmean(value[axis] for value in local_values) for axis in range(3)]
        global_mean = tuple(statistics.fmean(value[axis] for value in global_values) for axis in range(3))
        repeat_sem = [statistics.stdev(value[axis] for value in local_values) / math.sqrt(3.0) for axis in range(3)]
        resolutions = {
            key: _number(indexed[key]["instrument_resolution_mm"], f"scapula_{side}_{key}.resolution", positive=True)
            for key in required
        }
        mean_points: dict[str, Vector] = {
            landmark: tuple(
                statistics.fmean(
                    _number(
                        indexed[f"{landmark}_{axis}"][f"repeat_{repeat_index}_mm"],
                        f"scapula_{side}_{landmark}_{axis}.repeat_{repeat_index}",
                        positive=False,
                    )
                    for repeat_index in (1, 2, 3)
                )
                for axis in "xyz"
            )  # type: ignore[assignment]
            for landmark in MESKERS["required_landmarks"]
        }
        # Propagate each coordinate's quantisation uncertainty through the full
        # frame construction and nonlinear distance terms by a central Jacobian.
        quantization_variance = [0.0, 0.0, 0.0]
        for landmark in MESKERS["required_landmarks"]:
            for axis_index, axis in enumerate("xyz"):
                key = f"{landmark}_{axis}"
                epsilon = max(resolutions[key] * 0.01, 1e-4)
                plus = dict(mean_points)
                minus = dict(mean_points)
                plus_vector = list(mean_points[landmark])
                minus_vector = list(mean_points[landmark])
                plus_vector[axis_index] += epsilon
                minus_vector[axis_index] -= epsilon
                plus[landmark] = tuple(plus_vector)  # type: ignore[assignment]
                minus[landmark] = tuple(minus_vector)  # type: ignore[assignment]
                output_plus = meskers_one_pass(plus, side)["local_GH_xyz_mm"]
                output_minus = meskers_one_pass(minus, side)["local_GH_xyz_mm"]
                input_u = resolutions[key] / math.sqrt(12.0 * 3.0)
                for output_axis in range(3):
                    derivative = (output_plus[output_axis] - output_minus[output_axis]) / (2.0 * epsilon)
                    quantization_variance[output_axis] += (derivative * input_u) ** 2
        quantization_propagated_u = [math.sqrt(value) for value in quantization_variance]
        measurement_u = [math.hypot(repeat_sem[index], quantization_propagated_u[index]) for index in range(3)]
        model_rmse = MESKERS["validation_RMSE_local_xyz_mm"]
        combined_u = [math.hypot(measurement_u[index], model_rmse[index]) for index in range(3)]
        global_means[side] = global_mean  # type: ignore[assignment]
        result["sides"][side] = {
            "status": "DERIVED",
            "raw_measurement_refs": [f"scapula_{side}_{key}" for key in required],
            "per_pass": passes,
            "mean_local_GH_xyz_mm": local_mean,
            "mean_digitizer_global_GH_right_anterior_superior_mm": list(global_mean),
            "repeat_standard_uncertainty_of_mean_local_xyz_mm": repeat_sem,
            "instrument_quantization_propagated_standard_uncertainty_local_xyz_mm": quantization_propagated_u,
            "instrument_uncertainty_propagation": "central finite-difference Jacobian through scapular frame and Meskers equations",
            "measurement_standard_uncertainty_local_xyz_mm": measurement_u,
            "model_validation_RMSE_local_xyz_mm": model_rmse,
            "combined_standard_uncertainty_local_xyz_mm": combined_u,
            "combined_uncertainty_assumption": "independent zero-mean measurement and published model residuals",
        }
    if set(global_means) == {"L", "R"}:
        width = norm(vsub(global_means["R"], global_means["L"]))
        left_u = result["sides"]["L"]["combined_standard_uncertainty_local_xyz_mm"]
        right_u = result["sides"]["R"]["combined_standard_uncertainty_local_xyz_mm"]
        width_u_upper = math.hypot(max(left_u), max(right_u))
        result["derived_scalars"] = {
            "shoulder_joint_centre_width": {
                "status": "DERIVED",
                "value_mm": width,
                "standard_uncertainty_mm": width_u_upper,
                "definition": "distance between left/right Meskers GH centres in the common digitizer frame",
                "uncertainty_note": "conservative projection upper bound from independent bilateral local covariance maxima",
            }
        }
    else:
        result["derived_scalars"] = {
            "shoulder_joint_centre_width": {
                "status": "BLOCKED_MISSING_BILATERAL_3D_SCAPULAR_LANDMARKS",
                "value_mm": None,
            }
        }
    result["status"] = "DERIVED" if all(value["status"] == "DERIVED" for value in result["sides"].values()) else "BLOCKED"
    return result


def derive_b_schema(direct: dict[str, Any], hips: dict[str, Any], shoulders: dict[str, Any]) -> dict[str, Any]:
    blocked_surface_chord = {
        "status": "BLOCKED_SURFACE_CHORD_IS_NOT_INTERNAL_JOINT_CENTRE_LENGTH",
        "reason": (
            "The requested direct tape distance terminates at a palpable surface landmark. "
            "No population-average or fabricated medial/lateral joint-centre offset is applied."
        ),
    }
    output = {
        name: dict(blocked_surface_chord)
        for name in (
            "upper_arm_joint_centre_length_L",
            "upper_arm_joint_centre_length_R",
            "forearm_joint_centre_length_L",
            "forearm_joint_centre_length_R",
            "thigh_joint_centre_length_L",
            "thigh_joint_centre_length_R",
            "shank_joint_centre_length_L",
            "shank_joint_centre_length_R",
        )
    }
    if hips.get("status") == "DERIVED":
        output.update(hips["derived_scalars"])
        for value in hips["derived_scalars"].values():
            value.update(
                status="DERIVED",
                units="mm",
                derivation_name=HARRINGTON["name"],
                derivation_version=HARRINGTON["version"],
                equations_reference=HARRINGTON["reference"],
                raw_measurement_refs=hips["raw_measurement_refs"],
            )
    else:
        output["hip_joint_centre_width"] = {"status": "BLOCKED", "reason": hips}
        output["hip_joint_centre_vertical_offset"] = {"status": "BLOCKED", "reason": hips}
    shoulder_scalar = shoulders["derived_scalars"]["shoulder_joint_centre_width"]
    output["shoulder_joint_centre_width"] = dict(shoulder_scalar)
    if shoulder_scalar["status"] == "DERIVED":
        output["shoulder_joint_centre_width"].update(
            units="mm",
            derivation_name=MESKERS["name"],
            derivation_version=MESKERS["version"],
            equations_reference=MESKERS["reference"],
            raw_measurement_refs=[
                f"scapula_{side}_{landmark}_{axis}"
                for side in ("L", "R")
                for landmark in MESKERS["required_landmarks"]
                for axis in "xyz"
            ],
        )
    torso = direct["values"].get("C7_to_mid_PSIS")
    output["C7_to_pelvis_reference"] = (
        {
            "status": "DERIVED",
            "value_mm": torso["mean"],
            "standard_uncertainty_mm": torso["combined_measurement_standard_uncertainty"],
            "units": "mm",
            "derivation_name": "C7-to-mid-PSIS direct centerline chord",
            "derivation_version": "biospur_landmark_chord_v1",
            "raw_measurement_refs": ["C7_to_mid_PSIS"],
            "definition": "C7 surface landmark to bilateral PSIS midpoint; not an internal lumbosacral joint centre",
        }
        if torso
        else {"status": "BLOCKED_MISSING_DIRECT_MEASUREMENT", "raw_measurement_refs": ["C7_to_mid_PSIS"]}
    )
    return {
        "schema": "biospur-B-derived-joint-centre-preparation-v1",
        "joint_centre_positions": {"hips": hips, "shoulders": shoulders},
        "v4_1_scalar_candidates": output,
        "all_frozen_schema_scalars_ready": all(value.get("status") == "DERIVED" for value in output.values()),
    }


def summarize_shoes(rows: list[dict[str, str]]) -> dict[str, Any]:
    direct: dict[str, Any] = {}
    missing: list[str] = []
    row_by_id = {row["measurement_id"]: row for row in rows}
    for row in rows:
        if row["measurement_mode"] == "DERIVED_REAR_MINUS_FOREFOOT":
            continue
        if not all(row[f"repeat_{index}_mm"].strip() for index in (1, 2, 3)):
            missing.append(row["measurement_id"])
            continue
        value = repeated_summary(row)
        value.update(
            shoe_identity=row.get("shoe_identity", "").strip() or None,
            shoe_condition=row.get("shoe_condition", "").strip() or None,
            photo_references=row.get("photo_references", "").strip() or None,
        )
        direct[row["measurement_id"]] = value
    derived: dict[str, Any] = {}
    for side in ("L", "R"):
        rear_id = f"rear_heel_stack_height_{side}"
        fore_id = f"forefoot_stack_height_{side}"
        output_id = f"heel_minus_forefoot_elevation_{side}"
        if rear_id not in direct or fore_id not in direct:
            derived[output_id] = {
                "status": "BLOCKED_MISSING_STACK_MEASUREMENT",
                "missing": [name for name in (rear_id, fore_id) if name not in direct],
            }
            continue
        differences = [
            direct[rear_id]["raw_repeats"][index] - direct[fore_id]["raw_repeats"][index]
            for index in range(3)
        ]
        sample_sd = statistics.stdev(differences)
        resolution_u = math.hypot(
            direct[rear_id]["instrument_resolution"] / math.sqrt(12.0 * 3.0),
            direct[fore_id]["instrument_resolution"] / math.sqrt(12.0 * 3.0),
        )
        derived[output_id] = {
            "status": "DERIVED",
            "raw_paired_differences_mm": differences,
            "value_mm": statistics.fmean(differences),
            "standard_uncertainty_mm": math.hypot(sample_sd / math.sqrt(3.0), resolution_u),
            "equation": f"{rear_id} - {fore_id}, paired by repeat",
            "raw_measurement_refs": [rear_id, fore_id],
        }
    required = {
        "foot_length_L",
        "foot_length_R",
        "floor_to_malleolar_midpoint_L",
        "floor_to_malleolar_midpoint_R",
        "rear_heel_stack_height_L",
        "rear_heel_stack_height_R",
        "forefoot_stack_height_L",
        "forefoot_stack_height_R",
    }
    identities = sorted({value["shoe_identity"] for value in direct.values() if value["shoe_identity"]})
    photos = sorted({value["photo_references"] for value in direct.values() if value["photo_references"]})
    return {
        "schema": "biospur-shoe-input-preparation-v1",
        "direct": direct,
        "derived": derived,
        "shoe_identities": identities,
        "photo_references": photos,
        "missing_required": sorted(required - direct.keys()),
        "rendering_ready": required <= direct.keys() and all(value["status"] == "DERIVED" for value in derived.values()),
        "blocks_centerline_calibration": False,
    }


def placement_inventory(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    nodes = ["BSF31CC", "BSFC2CC", "BSFAA61", "BSF1120", "BSFB165", "BSFEC35", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4"]
    statuses: dict[str, str] = {}
    evidence_validation: dict[str, Any] = {}

    def answer(body: str, label: str) -> str:
        line = next((item for item in body.splitlines() if item.startswith(label)), "")
        return line.split(":", 1)[1].strip() if ":" in line else ""

    for node in nodes:
        section = text.split(f"## {node}", 1)
        if len(section) != 2:
            statuses[node] = "MISSING_SECTION"
            continue
        body = section[1].split("\n## ", 1)[0]
        status_line = next((line for line in body.splitlines() if line.startswith("- Evidence status:")), "")
        value = status_line.split(":", 1)[1].strip() if ":" in status_line else ""
        statuses[node] = value or "MISSING"
        photo = answer(body, "- Capture-day photo/video reference and visible scale:")
        recollection = answer(body, "- Operator recollection when no image exists:")
        long_axis = answer(body, "- Enclosure long-axis direction:")
        antenna = answer(body, "- Antenna-facing direction:")
        slip = answer(body, "- Likely translation slip interval and direction:")
        if statuses[node] in {"MEASURED_CAPTURE_DAY", "PHOTO_DERIVED"}:
            valid = bool(photo)
            reason = None if valid else "contemporary measurement/photo reference with visible scale is blank"
        elif statuses[node] == "CALIBRATION_ESTIMATED":
            valid = all((recollection, long_axis, antenna, slip))
            reason = None if valid else "bounded recollection, long axis, antenna direction, and slip interval are all required"
        elif statuses[node] == "MISSING":
            valid = True
            reason = None
        else:
            valid = False
            reason = "status is outside the allowed provenance vocabulary"
        evidence_validation[node] = {"valid": valid, "reason": reason}
    allowed = {"MEASURED_CAPTURE_DAY", "PHOTO_DERIVED", "CALIBRATION_ESTIMATED", "MISSING"}
    invalid = {
        node: {"status": value, "reason": evidence_validation[node]["reason"]}
        for node, value in statuses.items()
        if value not in allowed or not evidence_validation[node]["valid"]
    }
    return {
        "schema": "biospur-capture-placement-questionnaire-inventory-v1",
        "questionnaire_sha256": sha256(path),
        "node_statuses": statuses,
        "evidence_validation": evidence_validation,
        "invalid_statuses": invalid,
        "all_nodes_have_allowed_status": not invalid and all(value != "MISSING" for value in statuses.values()),
        "provenance_rule": (
            "MEASURED_CAPTURE_DAY requires contemporary measurement evidence; photos map to PHOTO_DERIVED; "
            "recollection without photos maps only to bounded CALIBRATION_ESTIMATED or MISSING."
        ),
    }


def _component_envelope(lines: Iterable[str]) -> list[float]:
    points: list[tuple[float, float]] = []
    for line in lines:
        row = json.loads(line)
        if row[0] != "POLY" or row[4] != 48:
            continue
        shape = row[6]
        for index in range(len(shape) - 1):
            if isinstance(shape[index], (int, float)) and isinstance(shape[index + 1], (int, float)):
                points.append((float(shape[index]), float(shape[index + 1])))
    if not points:
        raise InputError("component envelope missing from CAD")
    return [
        min(point[0] for point in points),
        max(point[0] for point in points),
        min(point[1] for point in points),
        max(point[1] for point in points),
    ]


def audit_shared_hardware(cad_path: Path | None, datasheet_path: Path | None) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema": "biospur-shared-pcb-phase-centre-enclosure-preparation-v1",
        "reuse_policy": "one transform may be reused only after questionnaire confirms mechanically identical assemblies",
        "frames": {
            "B_board": "origin at U4 CAD reference point on PCB top plane; +x/+y follow EasyEDA PCB axes; +z leaves PCB top",
            "A_antenna": "origin at effective DWM1001C UWB RF phase centre; axes parallel B unless evidence proves otherwise",
            "E_enclosure": "origin at enclosure geometric centre; +x toward antenna end along enclosure long axis; +z away from body-facing face",
        },
        "source_lineage": {
            "repository_commit": CAD_LINEAGE_COMMIT,
            "repository_path": CAD_LINEAGE_PATH,
            "note": "commit/path contain the prior fail-closed parser and authoritative external CAD hash; no CAD file is tracked in Git",
        },
    }
    if cad_path is None or not cad_path.is_file():
        base.update(status="BLOCKED_CAD_SOURCE_MISSING", pcb_phase_centre_to_enclosure=None)
        return base
    cad_hash = sha256(cad_path)
    if cad_hash != CAD_EXPECTED_SHA256:
        raise InputError(f"CAD SHA mismatch: {cad_hash}")
    with zipfile.ZipFile(cad_path) as archive:
        project = json.loads(archive.read("project.json"))
        pcb_ids = [key for key, value in project["pcbs"].items() if value in {"PCB17", "PCB17_1"}]
        if len(pcb_ids) != 2:
            raise InputError("expected PCB17 and PCB17_1")
        pcb_records: dict[str, Any] = {}
        for pcb_id in pcb_ids:
            lines = archive.read(f"PCB/{pcb_id}.epcb").decode().splitlines()
            unit_name = json.loads(lines[2])[3]
            records = [json.loads(line) for line in lines]
            components = {
                row[7].get("Unique ID"): row
                for row in records
                if row[0] == "COMPONENT" and isinstance(row[7], dict)
            }
            outline = next((row for row in records if row[0] == "POLY" and row[4] == 11 and row[6][0] == "R"), None)
            pcb_records[project["pcbs"][pcb_id]] = {
                "units": unit_name,
                "U4_DWM1001C_reference_origin_mil": components["UNIQUEU4"][4:6],
                "U4_rotation_deg": components["UNIQUEU4"][6],
                "U7_IMU_reference_origin_mil": components["UNIQUEU7"][4:6],
                "U7_rotation_deg": components["UNIQUEU7"][6],
                "outline_rectangle_record": outline[6] if outline else None,
            }
        u4_box = _component_envelope(
            archive.read("FOOTPRINT/ff5d591eeabc469985521741b9516086.efoo").decode().splitlines()
        )
        u7_box = _component_envelope(
            archive.read("FOOTPRINT/97c2bf0a57fa4fe1a93685d356de3b56.efoo").decode().splitlines()
        )
    datasheet: dict[str, Any] = {"status": "MISSING"}
    if datasheet_path is not None and datasheet_path.is_file():
        datasheet_hash = sha256(datasheet_path)
        if datasheet_hash != DWM_DATASHEET_EXPECTED_SHA256:
            raise InputError(f"DWM1001C datasheet SHA mismatch: {datasheet_hash}")
        datasheet = {
            "status": "VERIFIED",
            "absolute_path": str(datasheet_path.resolve()),
            "sha256": datasheet_hash,
            "revision": "DWM1001C Datasheet v1.7",
            "official_product_url": "https://www.qorvo.com/products/p/DWM1001C",
            "package_dimensions_mm": DWM_PACKAGE_MM,
            "relevant_statements": [
                "integrated printed UWB antenna",
                "rectangular area above shield is antenna area",
                "custom carrier geometry can change antenna performance",
            ],
        }
    # A uniform whole-package envelope is a deliberately conservative non-zero
    # RF-origin prior. It is not promoted to a usable transform because neither
    # the phase centre nor enclosure registration is in the evidence.
    envelope_sigma = [dimension / math.sqrt(12.0) for dimension in DWM_PACKAGE_MM]
    base.update(
        status="BLOCKED_SHARED_TRANSFORM_INCOMPLETE",
        cad={
            "absolute_path": str(cad_path.resolve()),
            "sha256": cad_hash,
            "git_tracking": "EXTERNAL_FILE_HASH_BOUND_BY_REPOSITORY_AUDIT",
            "pcb_documents": pcb_records,
            "U4_footprint_envelope_mil": u4_box,
            "U7_footprint_envelope_mil": u7_box,
            "antenna_phase_centre": "NOT_MARKED_IN_CAD",
            "enclosure_geometry": "NOT_PRESENT_IN_REPOSITORY_OR_CAD_ARCHIVE",
            "out_of_plane_registration": "UNKNOWN",
        },
        datasheet=datasheet,
        rf_phase_centre_prior={
            "status": "CONSERVATIVE_NONZERO_BOUND_ONLY",
            "value_B_mm": None,
            "one_sigma_uniform_whole_package_envelope_mm": envelope_sigma,
            "interpretation": "non-zero uncertainty bound only; not a phase-centre estimate",
        },
        assembly_tolerance_prior={
            "status": "BLOCKED_ENCLOSURE_REGISTRATION_MISSING",
            "value_B_to_E_mm": None,
            "one_sigma_mm": None,
            "requirement": "must be non-zero after shared enclosure registration/fit is measured",
        },
        pcb_phase_centre_to_enclosure=None,
        missing_shared_facts=[
            "confirm all ten assemblies use the same PCB-to-enclosure registration and antenna-end orientation",
            "shared enclosure outer and inner geometry or a versioned CAD/source drawing",
            "PCB U4 reference-to-enclosure-frame registration and mechanical play in three axes",
            "evidence-backed effective RF phase-centre convention within the DWM1001C antenna area",
            "non-zero assembly-tolerance distribution from enclosure fit/attachment evidence",
        ],
    )
    return base


def operator_facts_remaining(
    direct: dict[str, Any], shoes: dict[str, Any], shoulders: dict[str, Any], placement: dict[str, Any], hardware: dict[str, Any]
) -> list[str]:
    facts: list[str] = []
    for name in direct["missing_required"]:
        facts.append(f"three repeated direct readings plus instrument/resolution: {name}")
    for side, value in shoulders["sides"].items():
        if value["status"] != "DERIVED":
            facts.append(f"three common-frame 3D digitization passes for Meskers scapular landmarks on side {side}")
    for name in shoes["missing_required"]:
        facts.append(f"three repeated shoe/foot readings plus instrument/resolution: {name}")
    if not shoes["shoe_identities"]:
        facts.append("capture-shoe identity (brand/model/size/identifying features)")
    if not shoes["photo_references"]:
        facts.append("capture-shoe photo references, or an explicit statement that none exist")
    for node, status in placement["node_statuses"].items():
        if status == "MISSING":
            facts.append(f"placement evidence status and bounded recollection/photo reference for {node}")
        elif node in placement["invalid_statuses"]:
            facts.append(f"placement evidence for {node}: {placement['invalid_statuses'][node]['reason']}")
    facts.extend(hardware.get("missing_shared_facts", []))
    facts.extend(
        [
            "for true upper-arm/forearm/thigh/shank internal joint-centre lengths: evidence locating the medial/lateral joint centres; the supplied surface chords alone are not relabelled",
            "whether surviving straps are original capture straps; if yes, three strap-width readings and provenance",
        ]
    )
    return sorted(set(facts))


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    for path in (args.subject_csv, args.shoe_csv, args.placement_questionnaire):
        assert_safe_operator_input(path)
    assert_safe_output(args.out)
    subject_rows = read_csv(args.subject_csv)
    shoe_rows = read_csv(args.shoe_csv)
    direct = summarize_subject(subject_rows)
    hips = derive_hips(direct)
    shoulders = derive_shoulders(subject_rows)
    derived_b = derive_b_schema(direct, hips, shoulders)
    shoes = summarize_shoes(shoe_rows)
    placement = placement_inventory(args.placement_questionnaire)
    hardware = audit_shared_hardware(args.cad_source, args.dwm_datasheet)
    remaining = operator_facts_remaining(direct, shoes, shoulders, placement, hardware)
    args.out.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "A_DIRECT_SURFACE_MEASUREMENT.json": direct,
        "B_DERIVED_JOINT_CENTER.json": derived_b,
        "D_RENDERING_ONLY.json": shoes,
        "CAPTURE_PLACEMENT_INVENTORY.json": placement,
        "SHARED_HARDWARE_GEOMETRY.json": hardware,
    }
    for name, value in artifacts.items():
        canonical_json(args.out / name, value)
    report = {
        "schema": SCHEMA,
        "session": SESSION,
        "scope": "input acquisition and deterministic input preparation only",
        "calibration_opened": False,
        "held_out_opened": False,
        "raw_payload_opened": False,
        "frozen_v4_1_input_modified": False,
        "operator_facts_remaining": remaining,
        "readiness": {
            "direct_measurements": direct["all_required_complete"],
            "hip_joint_centres": hips["status"] == "DERIVED",
            "shoulder_joint_centres": shoulders["status"] == "DERIVED",
            "all_frozen_B_scalars": derived_b["all_frozen_schema_scalars_ready"],
            "shoe_rendering": shoes["rendering_ready"],
            "capture_placement": placement["all_nodes_have_allowed_status"],
            "shared_phase_centre_to_enclosure": hardware["pcb_phase_centre_to_enclosure"] is not None,
        },
        "verdict": "INPUT_PREPARATION_READY" if not remaining else "INPUT_PREPARATION_WAITING_FOR_OPERATOR_FACTS",
    }
    canonical_json(args.out / "INPUT_PREPARATION_REPORT.json", report)
    manifest_paths = sorted(path for path in args.out.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (args.out / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in manifest_paths), encoding="utf-8"
    )
    return report


def parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[2]
    config = root / "Fusion_Part/config/body_calibration_v4_1"
    argument_parser = argparse.ArgumentParser(description=__doc__)
    argument_parser.add_argument("--subject-csv", type=Path, default=config / "v47_subject_measurement_form.csv")
    argument_parser.add_argument("--shoe-csv", type=Path, default=config / "v47_shoe_measurement_form.csv")
    argument_parser.add_argument(
        "--placement-questionnaire", type=Path, default=config / "v47_capture_placement_questionnaire.md"
    )
    argument_parser.add_argument(
        "--cad-source", type=Path, default=Path("/home/zekaixiao/Downloads/ProPrj_eFlake_Synapse_2026-08-13.epro")
    )
    argument_parser.add_argument(
        "--dwm-datasheet",
        type=Path,
        default=Path(
            "/home/zekaixiao/Documents/Datasheets/DW1000 EVK/"
            "DWM1001_DWM1001-DEV_MDEK1001_Sources_and_Docs_v11/"
            "DWM1001/Product_and_Design_Documents/DWM1001C_Datasheet.pdf"
        ),
    )
    argument_parser.add_argument("--out", type=Path, required=True)
    return argument_parser


def main() -> int:
    args = parser().parse_args()
    try:
        report = prepare(args)
    except InputError as exc:
        print(json.dumps({"verdict": "INPUT_PREPARATION_INVALID", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
