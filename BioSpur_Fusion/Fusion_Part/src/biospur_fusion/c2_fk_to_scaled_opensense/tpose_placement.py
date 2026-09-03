"""Correct the official IMUPlacer calibration-pose ownership for C2.

OpenSim 4.6 IMUPlacer has no ``model_pose`` property: its pinned source uses
the model's default pose.  This adapter therefore writes one pre-placement
model whose default coordinates encode the registered T-pose protocol, runs
the official IMUPlacer once, and verifies the resulting placement and pixels.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix

from .pipeline import (
    BODY_BY_SEGMENT,
    C2_FROM_OPENSIM,
    FRAME_BY_SEGMENT,
    configure_opensim_log,
    replay_model,
    run_ik,
    run_imu_placer,
    sha256_file,
)
from .render import LINKS, VIEWS, _pose, a_points, render_episode


C2_TO_OPENSIM = C2_FROM_OPENSIM.T
TPOSE_DEFAULTS_RAD = {
    "pelvis_tilt": 0.0,
    "pelvis_list": 0.0,
    "pelvis_rotation": 0.0,
    "arm_flex_l": 0.0,
    "arm_flex_r": 0.0,
    "arm_add_l": math.pi / 2.0,
    "arm_add_r": math.pi / 2.0,
    "arm_rot_l": 0.0,
    "arm_rot_r": 0.0,
    "elbow_flex_l": 0.0,
    "elbow_flex_r": 0.0,
    "pro_sup_l": 0.0,
    "pro_sup_r": 0.0,
}
ROOT_GAUGE_FRAME_ORIENTATION_XYZ_RAD = (0.0, -math.pi / 2.0, 0.0)


def _frozen_tpose_lateral_axis_opensim(episode) -> np.ndarray:
    """Return the robust left-to-right arm direction from frozen orientations.

    Frozen C2 segment frames own the local -Z longitudinal direction.  This
    uses all preregistered placement rows and never consumes an IK result or a
    display-proxy joint centre.
    """

    per_row = []
    distal = np.array([0.0, 0.0, -1.0], dtype=float)
    for row in range(175, 526):
        left = _quat_wxyz_matrix(
            episode.segments["upper_arm_left"].quat_world_segment_wxyz[row]
        ) @ distal
        right = _quat_wxyz_matrix(
            episode.segments["upper_arm_right"].quat_world_segment_wxyz[row]
        ) @ distal
        lateral = C2_TO_OPENSIM @ (right - left)
        lateral[1] = 0.0
        norm = float(np.linalg.norm(lateral))
        if norm <= 1e-12:
            raise RuntimeError("frozen T-pose has degenerate bilateral arm direction")
        per_row.append(lateral / norm)
    axis = np.median(np.asarray(per_row), axis=0)
    axis /= np.linalg.norm(axis)
    return axis


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(i, j) for j in range(3)] for i in range(3)], dtype=float
    )


def _position(frame, state) -> np.ndarray:
    value = frame.getPositionInGround(state)
    return np.array([value[index] for index in range(3)], dtype=float)


def _rotation_angle(first: np.ndarray, second: np.ndarray) -> float:
    cosine = (float(np.trace(first.T @ second)) - 1.0) / 2.0
    return math.acos(float(np.clip(cosine, -1.0, 1.0)))


def _joint_position(model, state, name: str) -> np.ndarray:
    return _position(model.getJointSet().get(name).getParentFrame(), state)


def _model_points(model, state) -> dict[str, np.ndarray]:
    pelvis = _position(model.getBodySet().get("pelvis"), state)
    joint_names = {
        "shoulder_left": "acromial_l",
        "shoulder_right": "acromial_r",
        "elbow_left": "elbow_l",
        "elbow_right": "elbow_r",
        "wrist_left": "radius_hand_l",
        "wrist_right": "radius_hand_r",
        "hip_left": "hip_l",
        "hip_right": "hip_r",
        "knee_left": "walker_knee_l",
        "knee_right": "walker_knee_r",
        "ankle_left": "ankle_l",
        "ankle_right": "ankle_r",
    }
    points_os = {name: _joint_position(model, state, joint) for name, joint in joint_names.items()}
    points_os["pelvis_center"] = pelvis
    points_os["shoulder_mid"] = 0.5 * (
        points_os["shoulder_left"] + points_os["shoulder_right"]
    )
    return {
        name: C2_FROM_OPENSIM @ (value - pelvis) for name, value in points_os.items()
    }


def configure_tpose_model(
    source_model: Path, output_model: Path, output_root: Path, workspace: Path
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_root / "tpose_model_opensim.log")
    model = osim.Model(str(source_model.resolve()))
    ground_joint = model.updJointSet().get("ground_pelvis")
    ground_offset = osim.PhysicalOffsetFrame.safeDownCast(ground_joint.upd_frames(0))
    if ground_offset is None or ground_offset.getName() != "ground_offset":
        raise RuntimeError("official ground_pelvis parent offset frame is unavailable")
    ground_offset.set_orientation(osim.Vec3(*ROOT_GAUGE_FRAME_ORIENTATION_XYZ_RAD))
    coordinates = model.updCoordinateSet()
    defaults_before = {
        name: float(coordinates.get(name).getDefaultValue())
        for name in TPOSE_DEFAULTS_RAD
    }
    defaults_after = {}
    for name, value in TPOSE_DEFAULTS_RAD.items():
        coordinate = coordinates.get(name)
        if value < coordinate.getRangeMin() - 1e-12 or value > coordinate.getRangeMax() + 1e-12:
            raise ValueError(f"T-pose default outside official range: {name}={value}")
        coordinate.setDefaultValue(value)
        defaults_after[name] = value
    model.finalizeConnections()
    state = model.initSystem()
    model.assemble(state)
    model.realizePosition(state)

    arm_directions = {}
    for side in ("left", "right"):
        shoulder = _joint_position(model, state, f"acromial_{side[0]}")
        elbow = _joint_position(model, state, f"elbow_{side[0]}")
        direction_os = elbow - shoulder
        direction_os /= np.linalg.norm(direction_os)
        arm_directions[side] = {
            "opensim": direction_os.tolist(),
            "c2_basis": (C2_FROM_OPENSIM @ direction_os).tolist(),
        }
    left_os = np.asarray(arm_directions["left"]["opensim"])
    right_os = np.asarray(arm_directions["right"]["opensim"])
    if (
        abs(left_os[1]) >= 0.1
        or abs(right_os[1]) >= 0.1
        or float(np.dot(left_os, right_os)) >= -0.98
    ):
        raise RuntimeError("official model T-pose is not horizontal and bilateral")

    pelvis_rotation = model.getCoordinateSet().get("pelvis_rotation")
    pelvis_rotation_fixture = {
        "range_rad": [
            float(pelvis_rotation.getRangeMin()),
            float(pelvis_rotation.getRangeMax()),
        ],
        "default_rad": float(pelvis_rotation.getDefaultValue()),
        "clamped": bool(pelvis_rotation.getDefaultClamped()),
        "signed_fk": {},
    }
    for value in (-0.1, 0.1):
        fixture_state = model.initSystem()
        pelvis_rotation.setValue(fixture_state, value, False)
        model.realizePosition(fixture_state)
        lateral = (
            _joint_position(model, fixture_state, "elbow_r")
            - _joint_position(model, fixture_state, "acromial_r")
            - _joint_position(model, fixture_state, "elbow_l")
            + _joint_position(model, fixture_state, "acromial_l")
        )
        lateral /= np.linalg.norm(lateral)
        pelvis_rotation_fixture["signed_fk"][f"{value:+.1f}"] = lateral.tolist()

    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model.resolve()))

    frozen = load_frozen_c2_3a(workspace=workspace)
    episode = frozen.episodes["01"]
    frozen_lateral_os = _frozen_tpose_lateral_axis_opensim(episode)
    left_to_right_os = right_os - left_os
    left_to_right_os[1] = 0.0
    left_to_right_os /= np.linalg.norm(left_to_right_os)
    lateral_error_rad = math.acos(
        float(np.clip(np.dot(left_to_right_os, frozen_lateral_os), -1.0, 1.0))
    )
    if float(np.dot(left_to_right_os, frozen_lateral_os)) <= 0.99:
        raise RuntimeError("official T-pose root heading does not match frozen gauge")
    row = 350
    direct = a_points(episode, frozen.geometry, row)
    predicted = _model_points(model, state)
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 4.2), constrained_layout=True)
    all_points = np.concatenate(
        [np.stack(list(direct.values())), np.stack(list(predicted.values()))]
    )
    for column, (x_axis, y_axis, name) in enumerate(VIEWS):
        axis = axes[column]
        _pose(axis, direct, x_axis, y_axis, "#444444", "--", "A frozen FK row 350")
        _pose(axis, predicted, x_axis, y_axis, "#1f77b4", "-", "Official model T-pose")
        values = all_points[:, [x_axis, y_axis]]
        low, high = values.min(0), values.max(0)
        center = (low + high) / 2.0
        radius = max(float((high - low).max()) * 0.58, 0.25)
        axis.set_xlim(center[0] - radius, center[0] + radius)
        axis.set_ylim(center[1] - radius, center[1] + radius)
        axis.set_aspect("equal")
        axis.set_axis_off()
        axis.set_title(name)
        if column == 0:
            axis.legend(fontsize=7)
    fig.suptitle("Frozen 02 row 350 vs official model calibration T-pose")
    preview = output_root / "tpose_model_front_side_top.png"
    fig.savefig(preview, dpi=170)
    plt.close(fig)

    result = {
        "schema": "biospur-c2-official-tpose-model-v1",
        "source_model": str(source_model.resolve()),
        "source_model_sha256": sha256_file(source_model),
        "output_model": str(output_model.resolve()),
        "output_model_sha256": sha256_file(output_model),
        "official_imuplacer_model_pose_api": False,
        "official_source_semantics": "IMUPlacer uses model initSystem default pose as calibration pose",
        "coordinate_defaults_before_rad": defaults_before,
        "coordinate_defaults_after_rad": defaults_after,
        "protocol_owner": "official Rajagopal shoulder abduction coordinates at +pi/2 bilaterally encode the registered 02 straight-arm horizontal T-pose; no dynamic angle target",
        "sign_owner": "official forward-model generated lateral directions, checked before IMUPlacer",
        "root_heading_owner": (
            "capture-wide proper rotation of official ground_pelvis/ground_offset; "
            "registered C2 T-pose lateral gauge; pelvis_rotation remains zero"
        ),
        "root_gauge_component": "/jointset/ground_pelvis/ground_offset",
        "root_gauge_orientation_body_fixed_xyz_rad": list(
            ROOT_GAUGE_FRAME_ORIENTATION_XYZ_RAD
        ),
        "root_gauge_changes_internal_reachable_set": False,
        "pelvis_rotation_bidirectional_fixture": pelvis_rotation_fixture,
        "frozen_lateral_axis_opensim": frozen_lateral_os.tolist(),
        "model_lateral_axis_opensim": left_to_right_os.tolist(),
        "lateral_axis_error_rad": lateral_error_rad,
        "arm_directions_c2": arm_directions,
        "preview_png": str(preview.resolve()),
        "preview_row": row,
        "wall_s": time.monotonic() - started,
    }
    (output_root / "TPOSE_MODEL_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def _calibration_quaternions(path: Path) -> tuple[list[str], list[np.ndarray]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines.index("endheader")
    labels = lines[header + 1].split("\t")[1:]
    values = lines[header + 2].split("\t")[1:]
    return labels, [
        np.asarray([float(item) for item in value.split(",")], dtype=float)
        for value in values
    ]


def placement_closure(
    pre_model: Path, calibrated_model: Path, calibration_sto: Path, output_root: Path
) -> dict[str, object]:
    import opensim as osim

    labels, quaternions = _calibration_quaternions(calibration_sto)
    targets = {
        label: C2_TO_OPENSIM @ _quat_wxyz_matrix(quaternion)
        for label, quaternion in zip(labels, quaternions, strict=True)
    }
    result = {}
    for tag, model_path in (("before", pre_model), ("after", calibrated_model)):
        configure_opensim_log(output_root / f"placement_closure_{tag}_opensim.log")
        model = osim.Model(str(model_path.resolve()))
        state = model.initSystem()
        model.realizePosition(state)
        result[tag] = {}
        for label in labels:
            body_name = label.removesuffix("_imu")
            frame = model.getComponent(f"/bodyset/{body_name}/{label}")
            result[tag][label] = _rotation_angle(
                targets[label], _matrix(frame.getRotationInGround(state))
            )
    return {
        "calibration_sto": str(calibration_sto.resolve()),
        "calibration_sto_sha256": sha256_file(calibration_sto),
        "target_equation": "R_OS<-I = Rx(-pi/2) R_C2<-I",
        "before_error_rad": result["before"],
        "after_error_rad": result["after"],
        "after_max_rad": max(result["after"].values()),
    }


def run_placement(
    pre_model: Path, calibration_sto: Path, calibrated_model: Path, output_root: Path
) -> dict[str, object]:
    started = time.monotonic()
    placer = run_imu_placer(pre_model, calibration_sto, calibrated_model)
    closure = placement_closure(
        pre_model, calibrated_model, calibration_sto, output_root
    )
    result = {
        "schema": "biospur-c2-official-tpose-placement-v1",
        "placer": placer,
        "closure": closure,
        "placement_interval": "frozen episode 01 rows [175,526), robust 351-row mean",
        "imu_placer_runs": 1,
        "wall_s": time.monotonic() - started,
    }
    (output_root / "TPOSE_PLACEMENT_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def run_and_render_02(
    workspace: Path, model_path: Path, output_root: Path
) -> dict[str, object]:
    frozen = load_frozen_c2_3a(workspace=workspace)
    episode = frozen.episodes["01"]
    episode_dir = output_root / "02_t_pose"
    ik = run_ik(model_path, episode, episode_dir)
    (episode_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(ik, indent=2, sort_keys=True) + "\n"
    )
    _, points, rom = replay_model(
        model_path,
        episode_dir / "official_ik.sto",
        episode_dir / "analysis_opensim.log",
    )
    render = render_episode(
        frozen,
        episode,
        "01",
        "02_t_pose_corrected_placement",
        points,
        output_root / "02_t_pose_ab_front_side_top.png",
    )
    central = slice(175, 526)
    errors = ik["orientation_errors"]["sensors"]
    # Native per-row central metrics are computed directly from the raw table.
    import opensim as osim

    table = osim.TimeSeriesTable(
        str((episode_dir / "official_ik.sto_orientationErrors.sto").resolve())
    )
    central_errors = {}
    for label in table.getColumnLabels():
        values = np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)[
            central
        ]
        central_errors[label] = {
            "mean_rad": float(np.mean(values)),
            "p95_rad": float(np.quantile(values, 0.95)),
            "max_rad": float(np.max(values)),
        }
    result = {
        "schema": "biospur-c2-official-tpose-02-check-v1",
        "ik": ik,
        "central_rows": [175, 526],
        "central_orientation_errors_rad": central_errors,
        "all_sensor_summary_reference": errors,
        "rom": rom,
        "render": render,
        "wall_scope": "one official 02 IK plus replay/render",
    }
    (output_root / "TPOSE_02_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("model", "placement", "episode02"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--source-model", type=Path)
    parser.add_argument("--pre-model", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--calibration-sto", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "model":
        result = configure_tpose_model(
            args.source_model, args.model, args.output, args.workspace
        )
    elif args.command == "placement":
        result = run_placement(
            args.pre_model, args.calibration_sto, args.model, args.output
        )
    else:
        result = run_and_render_02(args.workspace, args.model, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
