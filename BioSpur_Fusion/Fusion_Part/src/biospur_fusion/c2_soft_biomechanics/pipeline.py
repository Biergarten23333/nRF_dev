"""Official-OpenSim soft coordinate projection for frozen C2 segment poses.

The project owns orchestration, a coordinate-chart reparameterization, and
capture-wide statistics. OpenSim owns every IK and soft CoordinateReference
goal. No project residual, Jacobian, optimizer, anatomical ROM, or joint centre
is introduced here.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Mapping

import numpy as np

from biospur_fusion.c2_fk_ik_diagnostic_biomechanics.pipeline import (
    _quat_conjugate,
    _quat_geodesic,
    _quat_multiply,
    _quat_to_matrix_rows,
    generalized_relative_quaternions,
)
from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    C2_FROM_OPENSIM,
    FRAME_BY_SEGMENT,
    SEGMENTS,
    SENSOR_TO_OPENSIM_XYZ_RAD,
    _body_xyz,
    _quat_wxyz_matrix,
    _vec3,
    _write_orientation_sto,
    _write_scalar_sto,
    configure_log,
    orientation_error_summary,
    sha256_file,
)


BASELINE_MODEL = Path("logs/c2_fk_to_opensim_ik_20260902_235206/model/c2_frozen_frame_model.osim")
BASELINE_MODEL_SHA256 = "0fb1043e66611640d12247ff24ff4d15b3e5c265492b1afd12a411a92a4296e6"
DISTAL_JOINTS = ("elbow_left", "elbow_right", "knee_left", "knee_right")
DISTAL_SEGMENTS = {
    "elbow_left": ("upper_arm_left", "forearm_left"),
    "elbow_right": ("upper_arm_right", "forearm_right"),
    "knee_left": ("thigh_left", "shank_left"),
    "knee_right": ("thigh_right", "shank_right"),
}
PROFILES = ("weak", "central", "strong")
BLOCKS_PER_EPISODE = 5
ELIGIBILITY_CUT_RAD = math.pi / 2.0
NUMERICAL_ROTATION_TOL = math.sqrt(np.finfo(float).eps)
MIN_RESULTANT_MAGNITUDE = math.sqrt(np.finfo(float).eps)
MIN_GIMBAL_COSINE = math.sqrt(np.finfo(float).eps)
SOLVER_ACCURACY = float(np.finfo(float).eps ** (2.0 / 3.0))


def _json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _proper_shortest_x_to_axis(axis: np.ndarray) -> np.ndarray:
    """Return the unique minimum-angle SO(3) map from +x to `axis`."""

    source = np.array([1.0, 0.0, 0.0])
    target = np.array(axis, dtype=float, copy=True)
    target /= np.linalg.norm(target)
    cosine = float(source @ target)
    if cosine <= -1.0 + 64.0 * np.finfo(float).eps:
        raise ValueError("functional line representative is antipodal to +x")
    cross = np.cross(source, target)
    skew = np.array(
        [[0.0, -cross[2], cross[1]], [cross[2], 0.0, -cross[0]], [-cross[1], cross[0], 0.0]]
    )
    result = np.eye(3) + skew + skew @ skew / (1.0 + cosine)
    if not np.allclose(result.T @ result, np.eye(3), atol=1e-12) or not math.isclose(
        float(np.linalg.det(result)), 1.0, abs_tol=1e-12
    ):
        raise ValueError("shortest-arc frame is not proper SO(3)")
    if not np.allclose(result @ source, target, atol=1e-12):
        raise ValueError("shortest-arc frame does not map +x to axis")
    return result


def _wrap_pi(values: np.ndarray) -> np.ndarray:
    return np.arctan2(np.sin(values), np.cos(values))


def _circular_mean(values: np.ndarray) -> float:
    return math.atan2(float(np.mean(np.sin(values))), float(np.mean(np.cos(values))))


def _circular_resultant(values: np.ndarray) -> float:
    return float(math.hypot(float(np.mean(np.cos(values))), float(np.mean(np.sin(values)))))


def _xyz_matrix(values: np.ndarray) -> np.ndarray:
    x, y, z = (float(value) for value in values)
    cx, sx = math.cos(x), math.sin(x)
    cy, sy = math.cos(y), math.sin(y)
    cz, sz = math.cos(z), math.sin(z)
    rx = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]])
    ry = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]])
    rz = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]])
    return rx @ ry @ rz


def _matrix_angle(a: np.ndarray, b: np.ndarray) -> float:
    relative = a.T @ b
    cosine = float(np.clip((np.trace(relative) - 1.0) * 0.5, -1.0, 1.0))
    sine = 0.5 * float(
        np.linalg.norm(
            [
                relative[2, 1] - relative[1, 2],
                relative[0, 2] - relative[2, 0],
                relative[1, 0] - relative[0, 1],
            ]
        )
    )
    return math.atan2(sine, cosine)


def _fixed_blocks(values: np.ndarray) -> list[np.ndarray]:
    return [values[index] for index in np.array_split(np.arange(len(values)), BLOCKS_PER_EPISODE)]


def _quantiles(values: list[float]) -> dict[str, float]:
    array = np.asarray(values, dtype=float)
    if array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError("empty or non-finite uncertainty population")
    return {
        "p10": float(np.quantile(array, 0.10)),
        "p50": float(np.quantile(array, 0.50)),
        "p90": float(np.quantile(array, 0.90)),
    }


def _orientation_roughness_blocks(frozen) -> list[float]:
    scales: list[float] = []
    variance_factor = math.sqrt(6.0)
    for episode in frozen.episodes.values():
        for segment in SEGMENTS:
            q = episode.segments[segment].quat_world_segment_wxyz
            increments = _quat_multiply(_quat_conjugate(q[:-1]), q[1:])
            second = _quat_multiply(_quat_conjugate(increments[:-1]), increments[1:])
            roughness = _quat_geodesic(second) / variance_factor
            for block in _fixed_blocks(roughness):
                scales.append(float(np.sqrt(np.mean(block * block))))
    return scales


def estimate_capture_wide_ownership(frozen) -> dict[str, object]:
    """Estimate diagnostic information scales using all 19 primary episodes."""

    orientation = _quantiles(_orientation_roughness_blocks(frozen))
    edge_by_name = {edge.name: edge for edge in frozen.joint_edges}
    joints: dict[str, object] = {}
    for name in DISTAL_JOINTS:
        edge = edge_by_name[name]
        axis = frozen.hinge_axes[name]
        parent_axis = np.asarray(axis.parent_axis_reset_segment, dtype=float)
        child_axis = np.asarray(axis.child_axis_reset_segment, dtype=float)
        proper = _proper_shortest_x_to_axis(parent_axis)
        chart_rows: list[np.ndarray] = []
        axis_errors: list[np.ndarray] = []
        per_episode_chart: list[np.ndarray] = []
        per_episode_axis: list[np.ndarray] = []
        reconstruction_errors: list[np.ndarray] = []
        chart_reconstruction_errors: list[np.ndarray] = []
        gimbal_cosines: list[np.ndarray] = []
        for episode in frozen.episodes.values():
            relative = generalized_relative_quaternions(episode, edge.parent, edge.child)
            matrices = _quat_to_matrix_rows(relative)
            chart = np.asarray([_body_xyz(proper.T @ row @ proper) for row in matrices])
            chart_matrices = np.asarray([_xyz_matrix(row) for row in chart])
            chart_error = np.asarray(
                [_matrix_angle(expected, recovered) for expected, recovered in zip(proper.T @ matrices @ proper, chart_matrices)]
            )
            reconstruction_error = np.asarray(
                [_matrix_angle(expected, proper @ recovered @ proper.T) for expected, recovered in zip(matrices, chart_matrices)]
            )
            transported = np.einsum("nij,j->ni", matrices, child_axis)
            discrepancy = np.arccos(np.clip(np.abs(transported @ parent_axis), 0.0, 1.0))
            chart_rows.append(chart)
            axis_errors.append(discrepancy)
            per_episode_chart.append(chart)
            per_episode_axis.append(discrepancy)
            reconstruction_errors.append(reconstruction_error)
            chart_reconstruction_errors.append(chart_error)
            gimbal_cosines.append(np.abs(np.cos(chart[:, 1])))
        all_chart = np.concatenate(chart_rows)
        all_axis = np.concatenate(axis_errors)
        all_reconstruction = np.concatenate(reconstruction_errors)
        all_chart_reconstruction = np.concatenate(chart_reconstruction_errors)
        all_gimbal_cosines = np.concatenate(gimbal_cosines)
        axis_p95 = float(np.quantile(all_axis, 0.95))
        resultant_by_suffix = {
            "ry": _circular_resultant(all_chart[:, 1]),
            "rz": _circular_resultant(all_chart[:, 2]),
        }
        eligible = bool(
            np.all(np.isfinite(all_chart))
            and axis_p95 < ELIGIBILITY_CUT_RAD
            and float(np.max(all_reconstruction)) <= NUMERICAL_ROTATION_TOL
            and float(np.max(all_chart_reconstruction)) <= NUMERICAL_ROTATION_TOL
            and float(np.min(all_gimbal_cosines)) > MIN_GIMBAL_COSINE
            and min(resultant_by_suffix.values()) > MIN_RESULTANT_MAGNITUDE
        )
        coordinates: dict[str, object] = {}
        for coordinate_index, suffix in ((1, "ry"), (2, "rz")):
            target = _circular_mean(all_chart[:, coordinate_index])
            coordinate_scales: list[float] = []
            axis_scales: list[float] = []
            for chart, discrepancy in zip(per_episode_chart, per_episode_axis):
                residual = _wrap_pi(chart[:, coordinate_index] - target)
                for block in _fixed_blocks(residual):
                    coordinate_scales.append(float(np.sqrt(np.mean(block * block))))
                for block in _fixed_blocks(discrepancy):
                    axis_scales.append(float(np.sqrt(np.mean(block * block))))
            coordinate_q = _quantiles(coordinate_scales)
            axis_q = _quantiles(axis_scales)
            weights = {
                "weak": (orientation["p10"] / math.hypot(coordinate_q["p90"], axis_q["p90"])) ** 2,
                "central": (orientation["p50"] / math.hypot(coordinate_q["p50"], axis_q["p50"])) ** 2,
                "strong": (orientation["p90"] / math.hypot(coordinate_q["p10"], axis_q["p10"])) ** 2,
            }
            coordinates[suffix] = {
                "target_circular_mean_rad": target,
                "circular_resultant_magnitude": resultant_by_suffix[suffix],
                "coordinate_block_rms_rad": coordinate_q,
                "axis_inconsistency_block_rms_rad": axis_q,
                "weights": weights if eligible else {profile: 0.0 for profile in PROFILES},
            }
        joints[name] = {
            "eligible": eligible,
            "eligibility_rule": "capture-wide p95 undirected parent-child functional-line discrepancy < pi/2",
            "axis_connection_p95_rad": axis_p95,
            "chart_roundtrip_max_rad": float(np.max(all_chart_reconstruction)),
            "q_equals_p_x_pt_roundtrip_max_rad": float(np.max(all_reconstruction)),
            "minimum_abs_cos_body_fixed_y": float(np.min(all_gimbal_cosines)),
            "numerical_rotation_tolerance_rad": NUMERICAL_ROTATION_TOL,
            "minimum_resultant_magnitude": MIN_RESULTANT_MAGNITUDE,
            "minimum_gimbal_cosine": MIN_GIMBAL_COSINE,
            "ineligibility_reasons": [
                reason
                for condition, reason in (
                    (axis_p95 >= ELIGIBILITY_CUT_RAD, "axis_connection_p95_not_below_pi_over_2"),
                    (float(np.max(all_reconstruction)) > NUMERICAL_ROTATION_TOL, "q_equals_p_x_pt_roundtrip"),
                    (float(np.max(all_chart_reconstruction)) > NUMERICAL_ROTATION_TOL, "xyz_chart_roundtrip"),
                    (float(np.min(all_gimbal_cosines)) <= MIN_GIMBAL_COSINE, "body_fixed_xyz_gimbal_cut"),
                    (min(resultant_by_suffix.values()) <= MIN_RESULTANT_MAGNITUDE, "circular_mean_degenerate"),
                )
                if condition
            ],
            "parent_axis_segment": parent_axis.tolist(),
            "child_axis_segment": child_axis.tolist(),
            "proper_chart_rotation_parent_and_child": proper.tolist(),
            "proper_chart_det": float(np.linalg.det(proper)),
            "source_artifact": axis.source_artifact,
            "source_sha256": axis.source_sha256,
            "frozen_axis_covariance_rad2": axis.covariance_rad2,
            "coordinates": coordinates,
        }
    return {
        "schema": "c2-capture-wide-soft-coordinate-ownership-v1",
        "primary_episode_count": len(frozen.episodes),
        "primary_rows": sum(episode.frame_count for episode in frozen.episodes.values()),
        "blocks_per_episode": BLOCKS_PER_EPISODE,
        "action_labels_used_in_statistics": False,
        "orientation_roughness_block_rms_rad": orientation,
        "orientation_roughness_interpretation": "capture-wide empirical second-increment roughness divided by sqrt(6); engineering scale, not measurement covariance",
        "profiles": {
            "weak": "orientation p10 / combined coordinate+axis p90",
            "central": "orientation p50 / combined coordinate+axis p50",
            "strong": "orientation p90 / combined coordinate+axis p10",
        },
        "central_profile_preregistered": True,
        "scientific_covariance_claim": False,
        "joints": joints,
    }


def _rotation_xyz(matrix: np.ndarray):
    import opensim as osim

    rotation = osim.Rotation(osim.Mat33(*(float(value) for value in matrix.reshape(-1))))
    xyz = rotation.convertRotationToBodyFixedXYZ()
    return osim.Vec3(*(float(xyz[index]) for index in range(3)))


def _matrix_to_quat_wxyz(matrix: np.ndarray) -> np.ndarray:
    """Stable active rotation-matrix to Hamilton WXYZ conversion."""

    matrix = np.asarray(matrix, dtype=float)
    candidates = np.array(
        [
            1.0 + matrix[0, 0] + matrix[1, 1] + matrix[2, 2],
            1.0 + matrix[0, 0] - matrix[1, 1] - matrix[2, 2],
            1.0 - matrix[0, 0] + matrix[1, 1] - matrix[2, 2],
            1.0 - matrix[0, 0] - matrix[1, 1] + matrix[2, 2],
        ]
    )
    index = int(np.argmax(candidates))
    value = math.sqrt(max(float(candidates[index]), 0.0)) * 0.5
    if index == 0:
        quat = np.array(
            [value, (matrix[2, 1] - matrix[1, 2]) / (4.0 * value), (matrix[0, 2] - matrix[2, 0]) / (4.0 * value), (matrix[1, 0] - matrix[0, 1]) / (4.0 * value)]
        )
    elif index == 1:
        quat = np.array(
            [(matrix[2, 1] - matrix[1, 2]) / (4.0 * value), value, (matrix[0, 1] + matrix[1, 0]) / (4.0 * value), (matrix[0, 2] + matrix[2, 0]) / (4.0 * value)]
        )
    elif index == 2:
        quat = np.array(
            [(matrix[0, 2] - matrix[2, 0]) / (4.0 * value), (matrix[0, 1] + matrix[1, 0]) / (4.0 * value), value, (matrix[1, 2] + matrix[2, 1]) / (4.0 * value)]
        )
    else:
        quat = np.array(
            [(matrix[1, 0] - matrix[0, 1]) / (4.0 * value), (matrix[0, 2] + matrix[2, 0]) / (4.0 * value), (matrix[1, 2] + matrix[2, 1]) / (4.0 * value), value]
        )
    quat /= np.linalg.norm(quat)
    return quat if quat[0] >= 0.0 else -quat


def _coordinate_reference_array(osim, ownership: Mapping[str, object], profile: str):
    references = osim.SimTKArrayCoordinateReference()
    owners = []
    manifest = []
    if profile == "none":
        return references, owners, manifest
    for name in DISTAL_JOINTS:
        record = ownership["joints"][name]
        if not record["eligible"]:
            continue
        for suffix in ("ry", "rz"):
            coordinate = record["coordinates"][suffix]
            function = osim.Constant(float(coordinate["target_circular_mean_rad"]))
            reference = osim.CoordinateReference(f"{name}_{suffix}", function)
            reference.setWeight(float(coordinate["weights"][profile]))
            references.push_back(reference)
            owners.append((function, reference))
            manifest.append(
                {
                    "coordinate": f"{name}_{suffix}",
                    "target_rad": coordinate["target_circular_mean_rad"],
                    "weight": coordinate["weights"][profile],
                }
            )
    return references, owners, manifest


def run_mechanism_fixture(
    model_path: Path,
    ownership: Mapping[str, object],
    output_dir: Path,
) -> dict[str, object]:
    """Official model-generated no-factor/three-profile response fixture."""

    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "opensim.log")
    generator = osim.Model(str(model_path.resolve()))
    generator_state = generator.initSystem()
    coordinate_set = generator.updCoordinateSet()
    coordinate_names = [coordinate_set.get(i).getName() for i in range(coordinate_set.getSize())]
    base = {name: 0.0 for name in coordinate_names}
    for joint in DISTAL_JOINTS:
        if not ownership["joints"][joint]["eligible"]:
            continue
        for suffix in ("ry", "rz"):
            base[f"{joint}_{suffix}"] = float(
                ownership["joints"][joint]["coordinates"][suffix]["target_circular_mean_rad"]
            )
    fixtures = []
    for joint in DISTAL_JOINTS:
        record = ownership["joints"][joint]
        if not record["eligible"]:
            continue
        amplitudes = {
            "ry": float(record["coordinates"]["ry"]["coordinate_block_rms_rad"]["p50"]),
            "rz": float(record["coordinates"]["rz"]["coordinate_block_rms_rad"]["p50"]),
        }
        fixtures.extend(
            [
                {"joint": joint, "kind": "pure_x", "changes": {f"{joint}_rx": 1.0}},
                {"joint": joint, "kind": "pure_y", "changes": {f"{joint}_ry": amplitudes["ry"]}},
                {"joint": joint, "kind": "pure_z", "changes": {f"{joint}_rz": amplitudes["rz"]}},
                {
                    "joint": joint,
                    "kind": "mixed",
                    "changes": {
                        f"{joint}_rx": 1.0,
                        f"{joint}_ry": amplitudes["ry"],
                        f"{joint}_rz": amplitudes["rz"],
                    },
                },
            ]
        )
    truth_rows = []
    rows_by_segment = {segment: [] for segment in SEGMENTS}
    for fixture in fixtures:
        truth = dict(base)
        for name, delta in fixture["changes"].items():
            truth[name] += delta
        for name, value in truth.items():
            coordinate_set.get(name).setValue(generator_state, float(value), False)
        generator.realizePosition(generator_state)
        truth_rows.append(truth)
        for segment in SEGMENTS:
            rotation = generator.getBodySet().get(segment).getRotationInGround(generator_state)
            matrix_os = np.asarray([[rotation.get(i, k) for k in range(3)] for i in range(3)])
            rows_by_segment[segment].append(_matrix_to_quat_wxyz(C2_FROM_OPENSIM @ matrix_os))
    rows_by_segment = {name: np.asarray(values) for name, values in rows_by_segment.items()}
    times = np.arange(len(fixtures), dtype=float)
    orientation_path = output_dir / "model_generated_orientations.sto"
    input_manifest = _write_orientation_sto(orientation_path, times, rows_by_segment)

    solutions = {}
    profile_manifests = {}
    for profile in ("none", *PROFILES):
        model = osim.Model(str(model_path.resolve()))
        state = model.initSystem()
        table = osim.TimeSeriesTableQuaternion(str(orientation_path.resolve()))
        basis = osim.Rotation(SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0))
        osim.OpenSenseUtilities.rotateOrientationTable(table, basis)
        rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(table)
        orientation_reference = osim.OrientationsReference(rotations)
        orientation_reference.setDefaultWeight(1.0)
        references, owners, reference_manifest = _coordinate_reference_array(osim, ownership, profile)
        solver = osim.InverseKinematicsSolver(
            model, osim.MarkersReference(), orientation_reference, references, 1e-4
        )
        solver.setAccuracy(SOLVER_ACCURACY)
        coordinates = model.updCoordinateSet()
        solved_rows = []
        error_rows = []
        for row_index, truth in enumerate(truth_rows):
            state.setTime(float(times[row_index]))
            for name, value in truth.items():
                coordinates.get(name).setValue(state, float(value), False)
            model.realizePosition(state)
            solver.assemble(state)
            solved_rows.append({name: float(coordinates.get(name).getValue(state)) for name in coordinate_names})
            errors = osim.SimTKArrayDouble()
            solver.computeCurrentOrientationErrors(errors)
            error_rows.append([float(errors.getElt(i)) for i in range(errors.size())])
        solutions[profile] = solved_rows
        objective_rows = []
        for solved, errors in zip(solved_rows, error_rows):
            orientation_goal = float(np.mean(np.square(errors)) / 2.0)
            coordinate_terms = []
            coordinate_gradient = []
            for reference in reference_manifest:
                delta = float(_wrap_pi(solved[reference["coordinate"]] - reference["target_rad"]))
                coordinate_terms.append(0.5 * reference["weight"] * delta * delta)
                coordinate_gradient.append(reference["weight"] * delta)
            objective_rows.append(
                {
                    "orientation_goal": orientation_goal,
                    "coordinate_goal": float(sum(coordinate_terms)),
                    "total_goal": orientation_goal + float(sum(coordinate_terms)),
                    "coordinate_goal_gradient_norm": float(np.linalg.norm(coordinate_gradient)),
                }
            )
        profile_manifests[profile] = {
            "coordinate_references": reference_manifest,
            "objective_equations": {
                "orientation_goal": "sum_i(angle_i_rad^2)/(2*N), N=10 because all orientation weights are one",
                "coordinate_goal": "sum_k(weight_k*(q_k-target_k)^2/2); CoordinateReference weight is linear and is not squared again",
                "total_goal": "orientation_goal + coordinate_goal"
            },
            "objective_rows": objective_rows,
            "objective_summary": {
                "orientation_goal_mean": float(np.mean([row["orientation_goal"] for row in objective_rows])),
                "coordinate_goal_mean": float(np.mean([row["coordinate_goal"] for row in objective_rows])),
                "total_goal_mean": float(np.mean([row["total_goal"] for row in objective_rows])),
                "coordinate_goal_gradient_norm_max": float(np.max([row["coordinate_goal_gradient_norm"] for row in objective_rows])),
            },
            "orientation_error_rad": {
                "mean": float(np.mean(error_rows)),
                "max": float(np.max(error_rows)),
            },
        }

    solver_tolerance = math.sqrt(SOLVER_ACCURACY)
    machine_nonzero = 128.0 * np.finfo(float).eps
    no_factor_max_error = 0.0
    x_free_max_error = 0.0
    non_target_crosstalk_max = 0.0
    profile_x_free_max = {profile: 0.0 for profile in PROFILES}
    profile_non_target_crosstalk_max = {profile: 0.0 for profile in PROFILES}
    response_rows = []
    monotonic = True
    nonhard = True
    nonzero = True
    for index, fixture in enumerate(fixtures):
        truth = truth_rows[index]
        no_factor_max_error = max(
            no_factor_max_error,
            max(abs(float(_wrap_pi(solutions["none"][index][name] - value))) for name, value in truth.items()),
        )
        joint = fixture["joint"]
        x_name = f"{joint}_rx"
        for profile in PROFILES:
            x_free_max_error = max(
                x_free_max_error,
                abs(float(_wrap_pi(solutions[profile][index][x_name] - solutions["none"][index][x_name]))),
            )
            profile_x_free_max[profile] = max(
                profile_x_free_max[profile],
                abs(float(_wrap_pi(solutions[profile][index][x_name] - solutions["none"][index][x_name]))),
            )
            for name in coordinate_names:
                if name.startswith(joint + "_"):
                    continue
                non_target_crosstalk_max = max(
                    non_target_crosstalk_max,
                    abs(float(_wrap_pi(solutions[profile][index][name] - solutions["none"][index][name]))),
                )
                profile_non_target_crosstalk_max[profile] = max(
                    profile_non_target_crosstalk_max[profile],
                    abs(float(_wrap_pi(solutions[profile][index][name] - solutions["none"][index][name]))),
                )
        for suffix in ("ry", "rz"):
            name = f"{joint}_{suffix}"
            if name not in fixture["changes"]:
                continue
            target = float(ownership["joints"][joint]["coordinates"][suffix]["target_circular_mean_rad"])
            residuals = {
                profile: abs(float(_wrap_pi(solutions[profile][index][name] - target)))
                for profile in ("none", *PROFILES)
            }
            responses = {profile: residuals["none"] - residuals[profile] for profile in PROFILES}
            monotonic = bool(
                monotonic
                and residuals["weak"] + solver_tolerance >= residuals["central"]
                and residuals["central"] + solver_tolerance >= residuals["strong"]
            )
            nonhard = bool(nonhard and residuals["strong"] > solver_tolerance)
            nonzero = bool(nonzero and all(response > machine_nonzero for response in responses.values()))
            response_rows.append(
                {
                    "joint": joint,
                    "fixture": fixture["kind"],
                    "coordinate": name,
                    "input_residual_rad": residuals["none"],
                    "residual_rad": {profile: residuals[profile] for profile in PROFILES},
                    "response_rad": responses,
                }
            )
    roughness_by_profile = {
        "weak": float(ownership["orientation_roughness_block_rms_rad"]["p10"]),
        "central": float(ownership["orientation_roughness_block_rms_rad"]["p50"]),
        "strong": float(ownership["orientation_roughness_block_rms_rad"]["p90"]),
    }
    profile_crosstalk_pass = {
        profile: bool(
            profile_x_free_max[profile] <= roughness_by_profile[profile]
            and profile_non_target_crosstalk_max[profile] <= roughness_by_profile[profile]
        )
        for profile in PROFILES
    }
    passed = bool(
        no_factor_max_error <= solver_tolerance
        and all(profile_crosstalk_pass.values())
        and monotonic
        and nonhard
        and nonzero
        and all(np.isfinite(row["orientation_error_rad"]["max"]) for row in profile_manifests.values())
    )
    result = {
        "schema": "c2-soft-biomechanics-official-model-generated-fixture-v1",
        "rows": len(fixtures),
        "official_solver_configurations": 4,
        "official_assemble_calls": 4 * len(fixtures),
        "profiles": ["none", *PROFILES],
        "weight_semantics": "pinned OpenSim/Simbody uses CoordinateReference weight linearly on QValue goal=(q-target)^2/2; OrientationSensors internally normalizes sum(weight_i*angle_i^2) by 2*sum(weight_i)",
        "common_weight_rescaling_applied": False,
        "input": input_manifest,
        "fixture_definitions": fixtures,
        "profile_manifests": profile_manifests,
        "no_factor_max_coordinate_error_rad": no_factor_max_error,
        "x_free_max_profile_change_rad": x_free_max_error,
        "non_target_coordinate_crosstalk_max_rad": non_target_crosstalk_max,
        "profile_x_free_max_change_rad": profile_x_free_max,
        "profile_non_target_coordinate_crosstalk_max_rad": profile_non_target_crosstalk_max,
        "profile_registered_roughness_bound_rad": roughness_by_profile,
        "profile_crosstalk_pass": profile_crosstalk_pass,
        "official_solver_accuracy": SOLVER_ACCURACY,
        "solver_derived_tolerance_rad": solver_tolerance,
        "soft_response_rows": response_rows,
        "soft_response_monotonic_with_weight": monotonic,
        "strong_profile_remains_nonhard": nonhard,
        "all_profiles_nonzero_response": nonzero,
        "passed": passed,
        "wall_s": time.monotonic() - started,
    }
    _json_write(output_dir / "MECHANISM_FIXTURE.json", result)
    return result


def prepare_candidate_model(workspace: Path, ownership: Mapping[str, object], output_path: Path) -> dict[str, object]:
    """Apply chart-only SO(3) conjugations to the sealed BallJoint model."""

    import opensim as osim

    source = workspace / BASELINE_MODEL
    if sha256_file(source) != BASELINE_MODEL_SHA256:
        raise ValueError("sealed baseline model hash mismatch")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    configure_log(output_path.parent / "model_prepare_opensim.log")
    baseline = osim.Model(str(source.resolve()))
    baseline_state = baseline.initSystem()
    baseline.realizePosition(baseline_state)
    model = osim.Model(str(source.resolve()))
    changed = []
    for name, record in ownership["joints"].items():
        if not record["eligible"]:
            continue
        proper = np.asarray(record["proper_chart_rotation_parent_and_child"], dtype=float)
        joint = model.updJointSet().get(name)
        for frame_index in (0, 1):
            joint.upd_frames(frame_index).set_orientation(_rotation_xyz(proper))
        changed.append(name)
    model.finalizeConnections()
    state = model.initSystem()
    model.realizePosition(state)
    fixture = {}
    unit_angle_rad = 1.0
    for name in DISTAL_JOINTS:
        record = ownership["joints"][name]
        if not record["eligible"]:
            fixture[name] = {"eligible": False, "passed": True, "reason": "unchanged BallJoint fallback"}
            continue
        proper = np.asarray(record["proper_chart_rotation_parent_and_child"], dtype=float)
        parent, child = DISTAL_SEGMENTS[name]
        base_parent = baseline.getBodySet().get(parent)
        base_child = baseline.getBodySet().get(child)
        candidate_parent = model.getBodySet().get(parent)
        candidate_child = model.getBodySet().get(child)
        baseline_relative_zero = np.asarray(
            [[base_parent.getTransformInGround(baseline_state).R().get(i, k) for k in range(3)] for i in range(3)]
        ).T @ np.asarray(
            [[base_child.getTransformInGround(baseline_state).R().get(i, k) for k in range(3)] for i in range(3)]
        )
        candidate_relative_zero = np.asarray(
            [[candidate_parent.getTransformInGround(state).R().get(i, k) for k in range(3)] for i in range(3)]
        ).T @ np.asarray(
            [[candidate_child.getTransformInGround(state).R().get(i, k) for k in range(3)] for i in range(3)]
        )
        zero_error = _matrix_angle(baseline_relative_zero, candidate_relative_zero)
        coordinate = model.updCoordinateSet().get(f"{name}_rx")
        coordinate.setValue(state, unit_angle_rad, False)
        model.realizePosition(state)
        observed = np.asarray(
            [[candidate_parent.getTransformInGround(state).R().get(i, k) for k in range(3)] for i in range(3)]
        ).T @ np.asarray(
            [[candidate_child.getTransformInGround(state).R().get(i, k) for k in range(3)] for i in range(3)]
        )
        expected = proper @ _xyz_matrix(np.array([unit_angle_rad, 0.0, 0.0])) @ proper.T
        conjugation_error = _matrix_angle(expected, observed)
        parent_frame = model.getJointSet().get(name).get_frames(0)
        child_frame = model.getJointSet().get(name).get_frames(1)
        parent_point = np.asarray([parent_frame.getPositionInGround(state)[i] for i in range(3)])
        child_point = np.asarray([child_frame.getPositionInGround(state)[i] for i in range(3)])
        connection_error = float(np.linalg.norm(parent_point - child_point))
        coordinate.setValue(state, 0.0, False)
        model.realizePosition(state)
        passed = bool(
            zero_error <= NUMERICAL_ROTATION_TOL
            and conjugation_error <= NUMERICAL_ROTATION_TOL
            and connection_error <= NUMERICAL_ROTATION_TOL
        )
        fixture[name] = {
            "eligible": True,
            "unit_angle_rad": unit_angle_rad,
            "neutral_relative_rotation_error_rad": zero_error,
            "official_fk_conjugation_error_rad": conjugation_error,
            "official_joint_connection_error_m": connection_error,
            "segment_lengths_changed": False,
            "passed": passed,
        }
        if not passed:
            raise ValueError(f"official FK conjugation fixture failed for {name}")
    model.printToXML(str(output_path.resolve()))
    return {
        "schema": "c2-soft-chart-model-manifest-v1",
        "source_model": str(source),
        "source_model_sha256": BASELINE_MODEL_SHA256,
        "candidate_model": str(output_path.resolve()),
        "candidate_model_sha256": sha256_file(output_path),
        "changed_joint_charts": changed,
        "joint_type_after": {name: model.getJointSet().get(name).getConcreteClassName() for name in DISTAL_JOINTS},
        "reachable_set_changed": False,
        "neutral_pose_changed": False,
        "joint_centres_or_lengths_changed": False,
        "official_fk_fixture": fixture,
        "official_fk_fixture_passed": all(row["passed"] for row in fixture.values()),
        "opensim_version": osim.GetVersionAndDate(),
    }


def _relative_matrices(episode) -> dict[str, np.ndarray]:
    matrices = {
        segment: _quat_wxyz_matrix(episode.segments[segment].quat_world_segment_wxyz[0])
        for segment in SEGMENTS
    }
    return {
        "ground_pelvis": C2_FROM_OPENSIM.T @ matrices["pelvis"],
        "pelvis_torso": matrices["pelvis"].T @ matrices["torso"],
        "shoulder_left": matrices["torso"].T @ matrices["upper_arm_left"],
        "elbow_left": matrices["upper_arm_left"].T @ matrices["forearm_left"],
        "shoulder_right": matrices["torso"].T @ matrices["upper_arm_right"],
        "elbow_right": matrices["upper_arm_right"].T @ matrices["forearm_right"],
        "hip_left": matrices["pelvis"].T @ matrices["thigh_left"],
        "knee_left": matrices["thigh_left"].T @ matrices["shank_left"],
        "hip_right": matrices["pelvis"].T @ matrices["thigh_right"],
        "knee_right": matrices["thigh_right"].T @ matrices["shank_right"],
    }


def _initialize_chart(model, state, episode, row_index: int, ownership: Mapping[str, object]) -> None:
    matrices = {
        segment: _quat_wxyz_matrix(episode.segments[segment].quat_world_segment_wxyz[row_index])
        for segment in SEGMENTS
    }
    relative = {
        "ground_pelvis": C2_FROM_OPENSIM.T @ matrices["pelvis"],
        "pelvis_torso": matrices["pelvis"].T @ matrices["torso"],
        "shoulder_left": matrices["torso"].T @ matrices["upper_arm_left"],
        "elbow_left": matrices["upper_arm_left"].T @ matrices["forearm_left"],
        "shoulder_right": matrices["torso"].T @ matrices["upper_arm_right"],
        "elbow_right": matrices["upper_arm_right"].T @ matrices["forearm_right"],
        "hip_left": matrices["pelvis"].T @ matrices["thigh_left"],
        "knee_left": matrices["thigh_left"].T @ matrices["shank_left"],
        "hip_right": matrices["pelvis"].T @ matrices["thigh_right"],
        "knee_right": matrices["thigh_right"].T @ matrices["shank_right"],
    }
    coordinates = model.updCoordinateSet()
    by_name = {coordinates.get(i).getName(): coordinates.get(i) for i in range(coordinates.getSize())}
    for joint_name, matrix in relative.items():
        if joint_name in ownership["joints"] and ownership["joints"][joint_name]["eligible"]:
            proper = np.asarray(
                ownership["joints"][joint_name]["proper_chart_rotation_parent_and_child"], dtype=float
            )
            matrix = proper.T @ matrix @ proper
        prefix = "pelvis" if joint_name == "ground_pelvis" else joint_name
        for suffix, value in zip(("rx", "ry", "rz"), _body_xyz(matrix)):
            by_name[f"{prefix}_{suffix}"].setValue(state, float(value), False)
    model.realizePosition(state)


def run_episode(
    model_path: Path,
    episode,
    ownership: Mapping[str, object],
    profile: str,
    output_dir: Path,
) -> dict[str, object]:
    """Run one frozen episode through official OpenSim soft-coordinate IK."""

    import opensim as osim

    if profile not in PROFILES:
        raise ValueError(f"unknown profile: {profile}")
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "opensim.log")
    input_path = output_dir / "orientations.sto"
    reference_time = episode.segments["pelvis"].time_root_s
    input_manifest = _write_orientation_sto(
        input_path,
        reference_time - reference_time[0],
        {segment: episode.segments[segment].quat_world_segment_wxyz for segment in SEGMENTS},
    )
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    quaternion_table = osim.TimeSeriesTableQuaternion(str(input_path.resolve()))
    basis = osim.Rotation(SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0))
    osim.OpenSenseUtilities.rotateOrientationTable(quaternion_table, basis)
    rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(quaternion_table)
    orientation_reference = osim.OrientationsReference(rotations)
    orientation_reference.setDefaultWeight(1.0)
    coordinate_references, coordinate_reference_owners, reference_manifest = _coordinate_reference_array(
        osim, ownership, profile
    )
    solver = osim.InverseKinematicsSolver(
        model,
        osim.MarkersReference(),
        orientation_reference,
        coordinate_references,
        1e-4,
    )
    solver.setAccuracy(SOLVER_ACCURACY)
    times = np.asarray(rotations.getIndependentColumn(), dtype=float)
    coordinate_set = model.updCoordinateSet()
    labels = [coordinate_set.get(i).getName() for i in range(coordinate_set.getSize())]
    motion_rows: list[list[float]] = []
    error_rows: list[list[float]] = []
    sensor_labels: list[str] = []
    for row_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        _initialize_chart(model, state, episode, row_index, ownership)
        solver.assemble(state)
        if not sensor_labels:
            # The official solver constructs orientation assembly conditions
            # during its first assemble; pre-assemble index queries are an
            # invalid OpenSim 4.6 Python lifecycle operation.
            sensor_labels = [
                solver.getOrientationSensorNameForIndex(i)
                for i in range(solver.getNumOrientationSensorsInUse())
            ]
        motion_rows.append(
            [math.degrees(float(coordinate_set.get(i).getValue(state))) for i in range(coordinate_set.getSize())]
        )
        errors = osim.SimTKArrayDouble()
        solver.computeCurrentOrientationErrors(errors)
        error_rows.append([float(errors.getElt(i)) for i in range(errors.size())])
    motion_path = output_dir / "soft_ik.sto"
    errors_path = output_dir / "soft_ik.sto_orientationErrors.sto"
    _write_scalar_sto(motion_path, times, labels, motion_rows, name="soft_ik.sto", in_degrees=True)
    _write_scalar_sto(
        errors_path,
        times,
        sensor_labels,
        error_rows,
        name="OrientationErrors",
        in_degrees=False,
    )
    result = {
        "schema": "c2-official-opensim-soft-coordinate-episode-v1",
        "episode": episode.key,
        "profile": profile,
        "rows": len(times),
        "engine": "official OpenSim::InverseKinematicsSolver",
        "lifecycle": "deterministic measurement-chart initialization followed by official assemble at every row",
        "state_dimensions": coordinate_set.getSize(),
        "orientation_observations": len(sensor_labels),
        "coordinate_references": reference_manifest,
        "official_solver_accuracy": SOLVER_ACCURACY,
        "orientation_errors": orientation_error_summary(errors_path),
        "input": input_manifest,
        "motion": str(motion_path.resolve()),
        "motion_sha256": sha256_file(motion_path),
        "errors": str(errors_path.resolve()),
        "errors_sha256": sha256_file(errors_path),
        "wall_s": time.monotonic() - started,
        "uwb_consumed": False,
        "custom_optimizer_or_residual": False,
    }
    _json_write(output_dir / "EPISODE_RESULT.json", result)
    return result


def source_sha256(paths: list[Path]) -> dict[str, str]:
    return {str(path): sha256_file(path) for path in paths}
