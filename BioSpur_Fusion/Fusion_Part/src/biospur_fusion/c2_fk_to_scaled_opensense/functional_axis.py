"""Bounded functional coordinate-frame correction for the scaled OpenSense model.

This module does not fit poses or implement IK.  It uses the immutable C2 FK
orientations from the eight declared functional actions to choose one of the
24 proper signed axis permutations for each affected *OpenSim joint frame*.
The chosen frame rotations are then consumed by the official OpenSim IK tool.
"""

from __future__ import annotations

import itertools
import json
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3b_official_opensense.adapter import BODY_BY_SEGMENT
from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix

from .pipeline import FRAME_BY_SEGMENT, configure_opensim_log, sha256_file


C2_TO_OPENSIM = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=float
)


@dataclass(frozen=True)
class FunctionalAction:
    episode: str
    joint: str
    coordinate: str
    parent_segment: str
    child_segment: str
    fit_duration_s: float
    approximate_repetitions: int
    first_motion_coordinate_sign: int
    action_owner: str


ACTIONS = (
    FunctionalAction("04", "acromial_l", "arm_add_l", "torso", "upper_arm_left", 30.0, 3, +1, "left arm raises from body side"),
    FunctionalAction("05", "acromial_r", "arm_add_r", "torso", "upper_arm_right", 30.0, 3, +1, "right arm raises from body side"),
    FunctionalAction("06", "elbow_l", "elbow_flex_l", "upper_arm_left", "forearm_left", 15.0, 3, +1, "first 15 s left elbow flexion/extension"),
    FunctionalAction("07", "elbow_r", "elbow_flex_r", "upper_arm_right", "forearm_right", 15.0, 3, +1, "first 15 s right elbow flexion/extension"),
    FunctionalAction("08", "hip_l", "hip_flexion_l", "pelvis", "thigh_left", 30.0, 3, +1, "left thigh raises forward"),
    FunctionalAction("09", "hip_r", "hip_flexion_r", "pelvis", "thigh_right", 30.0, 3, +1, "right thigh raises forward"),
    FunctionalAction("10", "walker_knee_l", "knee_angle_l", "thigh_left", "shank_left", 30.0, 3, -1, "left seated knee extends first; flexion coordinate therefore decreases"),
    FunctionalAction("11", "walker_knee_r", "knee_angle_r", "thigh_right", "shank_right", 30.0, 3, -1, "right seated knee extends first; flexion coordinate therefore decreases"),
)

HINGE_DOMINANT_ACTIONS = tuple(
    action
    for action in ACTIONS
    if action.joint in {"elbow_l", "elbow_r", "walker_knee_l", "walker_knee_r"}
)
MULTIDOF_UNRESOLVED_ACTIONS = tuple(
    action for action in ACTIONS if action not in HINGE_DOMINANT_ACTIONS
)


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(i, j) for j in range(3)] for i in range(3)], dtype=float
    )


def _rotation(matrix: np.ndarray):
    import opensim as osim

    return osim.Rotation(osim.Mat33(*(float(value) for value in matrix.ravel())))


def _rotation_vector(matrix: np.ndarray) -> np.ndarray:
    angle_axis = _rotation(matrix).convertRotationToAngleAxis()
    return float(angle_axis.get(0)) * np.array(
        [angle_axis.get(1), angle_axis.get(2), angle_axis.get(3)], dtype=float
    )


def _signed_permutations() -> tuple[np.ndarray, ...]:
    result = []
    for permutation in itertools.permutations(range(3)):
        for signs in itertools.product((-1.0, 1.0), repeat=3):
            candidate = np.zeros((3, 3), dtype=float)
            candidate[list(permutation), range(3)] = signs
            if round(float(np.linalg.det(candidate))) == 1:
                result.append(candidate)
    assert len(result) == 24
    return tuple(result)


def _mapping_name(matrix: np.ndarray) -> str:
    names = []
    for source_axis in range(3):
        target_axis = int(np.argmax(np.abs(matrix[:, source_axis])))
        sign = "+" if matrix[target_axis, source_axis] > 0.0 else "-"
        names.append(sign + "xyz"[target_axis])
    return " ".join(names)


def _relative_body_rotation(model, state, parent_segment: str, child_segment: str) -> np.ndarray:
    parent = model.getBodySet().get(BODY_BY_SEGMENT[parent_segment])
    child = model.getBodySet().get(BODY_BY_SEGMENT[child_segment])
    return _matrix(parent.getRotationInGround(state)).T @ _matrix(
        child.getRotationInGround(state)
    )


def _model_positive_axis(model, action: FunctionalAction) -> tuple[np.ndarray, np.ndarray]:
    state = model.initSystem()
    model.assemble(state)
    model.realizePosition(state)
    relative_zero = _relative_body_rotation(
        model, state, action.parent_segment, action.child_segment
    )
    parent = model.getBodySet().get(BODY_BY_SEGMENT[action.parent_segment])
    joint = model.getJointSet().get(action.joint)
    parent_body_to_joint = _matrix(parent.getRotationInGround(state)).T @ _matrix(
        joint.getParentFrame().getRotationInGround(state)
    )
    coordinate = model.updCoordinateSet().get(action.coordinate)
    coordinate.setValue(state, float(coordinate.getValue(state)) + 1e-4, False)
    model.assemble(state)
    model.realizePosition(state)
    tangent_parent = _rotation_vector(
        _relative_body_rotation(
            model, state, action.parent_segment, action.child_segment
        )
        @ relative_zero.T
    ) / 1e-4
    tangent_joint = parent_body_to_joint.T @ tangent_parent
    tangent_joint[np.abs(tangent_joint) < 1e-10] = 0.0
    tangent_joint /= np.linalg.norm(tangent_joint)
    return tangent_joint, parent_body_to_joint


def _measured_relative_increments(model, episode, action: FunctionalAction) -> tuple[np.ndarray, np.ndarray]:
    offsets = {}
    for segment in (action.parent_segment, action.child_segment):
        body_name = BODY_BY_SEGMENT[segment]
        frame = model.getComponent(f"/bodyset/{body_name}/{FRAME_BY_SEGMENT[segment]}")
        offsets[segment] = _matrix(frame.getOffsetTransform().R())

    def body_rotation(segment: str, row: int) -> np.ndarray:
        measured = _quat_wxyz_matrix(
            episode.segments[segment].quat_world_segment_wxyz[row]
        )
        return C2_TO_OPENSIM @ measured @ offsets[segment].T

    times = episode.segments[action.parent_segment].time_root_s
    times = times - times[0]
    relative = []
    for row in range(episode.frame_count):
        relative.append(
            body_rotation(action.parent_segment, row).T
            @ body_rotation(action.child_segment, row)
        )
    relative = np.asarray(relative)
    indices = np.flatnonzero(times[:-1] < action.fit_duration_s)
    increments = np.asarray(
        [_rotation_vector(relative[row + 1] @ relative[row].T) for row in indices]
    )
    return times[indices], increments


def _select_mapping(
    increments_parent: np.ndarray,
    increment_times: np.ndarray,
    action: FunctionalAction,
    official_axis_joint: np.ndarray,
    parent_body_to_joint: np.ndarray,
) -> tuple[np.ndarray, dict[str, object]]:
    # An uncentred scatter fit is the line-through-origin analogue of the
    # functional angular-velocity line. Every increment in the declared phase
    # contributes; no speed threshold or result-selected row is used.
    _, singular_values, right_vectors = np.linalg.svd(
        increments_parent, full_matrices=False
    )
    observed_parent = right_vectors[0]
    half_cycle_s = action.fit_duration_s / (2.0 * action.approximate_repetitions)
    first_motion = np.sum(
        increments_parent[increment_times < half_cycle_s], axis=0
    )
    if (
        float(np.dot(observed_parent, first_motion))
        * action.first_motion_coordinate_sign
        < 0.0
    ):
        observed_parent *= -1.0
    observed_joint = parent_body_to_joint.T @ observed_parent

    candidates = []
    for candidate in _signed_permutations():
        predicted_axis = candidate @ official_axis_joint
        score = float(np.dot(observed_joint, predicted_axis))
        rotation_angle = math.acos(
            float(np.clip((np.trace(candidate) - 1.0) / 2.0, -1.0, 1.0))
        )
        candidates.append(
            (score, rotation_angle, _mapping_name(candidate), candidate, predicted_axis)
        )
    # Score owns the mapped functional direction. The smallest SO(3) change
    # uniquely owns any equivalent unobserved secondary-axis completion.
    candidates.sort(key=lambda item: (-round(item[0], 12), item[1], item[2]))
    score, rotation_angle, name, selected, selected_axis = candidates[0]
    equivalent = [
        item
        for item in candidates
        if np.allclose(item[4], selected_axis, atol=1e-12, rtol=0.0)
    ]
    orthogonal = increments_parent - np.outer(
        increments_parent @ observed_parent, observed_parent
    )
    residual_norm = np.linalg.norm(orthogonal, axis=1)
    total_energy = float(np.sum(singular_values**2))
    report = {
        "episode": action.episode,
        "joint": action.joint,
        "coordinate": action.coordinate,
        "action_owner": action.action_owner,
        "fit_window_s": [0.0, action.fit_duration_s],
        "increment_rows": int(len(increments_parent)),
        "first_direction_window_s": [0.0, half_cycle_s],
        "first_motion_coordinate_sign": action.first_motion_coordinate_sign,
        "official_positive_axis_joint": official_axis_joint.tolist(),
        "observed_positive_axis_parent_body": observed_parent.tolist(),
        "observed_positive_axis_joint": observed_joint.tolist(),
        "increment_covariance_rad2": np.cov(
            increments_parent, rowvar=False, ddof=1
        ).tolist(),
        "singular_values_rad": singular_values.tolist(),
        "first_line_explained_energy_fraction": float(
            singular_values[0] ** 2 / total_energy
        ),
        "orthogonal_residual_median_rad": float(np.median(residual_norm)),
        "orthogonal_residual_p95_rad": float(np.quantile(residual_norm, 0.95)),
        "candidate_count": len(candidates),
        "selected_mapping": name,
        "selected_matrix": selected.tolist(),
        "selected_det": float(np.linalg.det(selected)),
        "selected_alignment": score,
        "selected_rotation_angle_rad": rotation_angle,
        "selected_axis_equivalence_class_size": len(equivalent),
        "equivalence_owner": "data identify Q*a only; smallest SO(3) angle then lexical mapping name is the preregistered canonical representative",
        "runner_up_distinct_alignment": next(
            item[0]
            for item in candidates
            if not np.allclose(item[4], selected_axis, atol=1e-12, rtol=0.0)
        ),
        "mapping_uses_B_or_holdout": False,
    }
    return selected, report


def configure_functional_model(
    workspace: Path, base_model_path: Path, output_model_path: Path, output_json: Path
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_json.parent / "configure_opensim.log")
    frozen = load_frozen_c2_3a(workspace=workspace)
    model = osim.Model(str(base_model_path.resolve()))
    default_state = model.initSystem()
    model.assemble(default_state)
    model.realizePosition(default_state)
    bodies_before = {
        body.getName(): _matrix(body.getRotationInGround(default_state))
        for body in (model.getBodySet().get(i) for i in range(model.getBodySet().getSize()))
    }

    reports = []
    mappings = {}
    for action in HINGE_DOMINANT_ACTIONS:
        official_axis_joint, parent_body_to_joint = _model_positive_axis(model, action)
        times, increments = _measured_relative_increments(
            model, frozen.episodes[action.episode], action
        )
        mapping, report = _select_mapping(
            increments, times, action, official_axis_joint, parent_body_to_joint
        )
        mappings[action.joint] = mapping
        reports.append(report)

    for action in HINGE_DOMINANT_ACTIONS:
        joint = model.updJointSet().get(action.joint)
        mapping = mappings[action.joint]
        for frame in (joint.getParentFrame(), joint.getChildFrame()):
            offset = osim.PhysicalOffsetFrame.safeDownCast(frame)
            if offset is None:
                raise TypeError(f"{action.joint} joint frame is not PhysicalOffsetFrame")
            old_rotation = _matrix(offset.getOffsetTransform().R())
            new_rotation = _rotation(old_rotation @ mapping)
            offset.set_orientation(new_rotation.convertRotationToBodyFixedXYZ())

    model.finalizeConnections()
    check_state = model.initSystem()
    model.assemble(check_state)
    model.realizePosition(check_state)
    neutral_errors = {}
    for index in range(model.getBodySet().getSize()):
        body = model.getBodySet().get(index)
        delta = bodies_before[body.getName()].T @ _matrix(
            body.getRotationInGround(check_state)
        )
        neutral_errors[body.getName()] = float(np.linalg.norm(_rotation_vector(delta)))
    output_model_path.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model_path.resolve()))
    result = {
        "schema": "biospur-c2-fk-opensense-functional-axis-v1",
        "base_model": str(base_model_path.resolve()),
        "base_model_sha256": sha256_file(base_model_path),
        "output_model": str(output_model_path.resolve()),
        "output_model_sha256": sha256_file(output_model_path),
        "selection_engine": "NumPy SVD plus exact 24-element det=+1 signed-permutation set; no optimizer and no IK result",
        "candidate_count_per_joint": 24,
        "analytic_candidate_comparisons": 24 * len(HINGE_DOMINANT_ACTIONS),
        "transform_target": "OpenSim Joint parent and child PhysicalOffsetFrame orientations",
        "transform_side": "R_body_joint_new = R_body_joint_old @ Q on both frames",
        "physical_effect": "R_joint(q) becomes Q R_joint(q) Q^T; neutral pose and joint centres unchanged; nonidentity Q changes the physical reachable manifold",
        "coordinate_names_values_ranges_changed": False,
        "imu_placement_frames_changed": False,
        "joint_types_spatial_functions_lengths_changed": False,
        "walker_knee_effect": "the same proper Q conjugates every model-owned knee rotation and rotates its translation coupling; this is generic-model diagnostic configuration, not IMU-position evidence",
        "multidof_identity_retained": [
            {
                "episode": action.episode,
                "joint": action.joint,
                "coordinate": action.coordinate,
                "outcome": "REJECT_FRAME_CHANGE_NO_SECOND_NAMED_NONCOLLINEAR_AXIS",
                "reason": "one named functional axis cannot own the twist of a 3-DOF joint frame",
            }
            for action in MULTIDOF_UNRESOLVED_ACTIONS
        ],
        "active_hamilton_global_basis_det": float(np.linalg.det(C2_TO_OPENSIM)),
        "mappings": reports,
        "neutral_body_orientation_error_rad": neutral_errors,
        "neutral_max_error_rad": max(neutral_errors.values()),
        "wall_s": time.monotonic() - started,
    }
    output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--base-model", type=Path, required=True)
    parser.add_argument("--output-model", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()
    result = configure_functional_model(
        args.workspace, args.base_model, args.output_model, args.output_json
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
