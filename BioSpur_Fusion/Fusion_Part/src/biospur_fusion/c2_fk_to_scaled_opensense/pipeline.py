"""Scale, place, and run the official Rajagopal OpenSense model.

The only fitted quantities are the official IMUPlacer's capture-wide constant
frame rotations from the fixed central interval of frozen episode 01.  No raw
IMU data, custom IK objective, or action-dependent parameter enters here.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3b_official_opensense.adapter import (
    BODY_BY_SEGMENT,
    COUPLED_COORDINATES,
    ENABLED_COORDINATES,
    configure_opensim_log,
)
from biospur_fusion.c2_fk_to_opensim_ik.adapter import orientation_error_summary


SENSOR_TO_OPENSIM_XYZ_RAD = (-math.pi / 2.0, 0.0, 0.0)
FRAME_BY_SEGMENT = {
    segment: f"{body}_imu" for segment, body in BODY_BY_SEGMENT.items()
}
C2_FROM_OPENSIM = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]], dtype=float
)
CALIBRATION_SLICE = slice(175, 526)  # fixed central 351/701 rows, before B.
PILOT_EPISODES = {
    "00_initial_still": "00",
    "02_t_pose": "01",
    "06_upper_dynamic": "06",
    "10_lower_dynamic": "10",
    "H01_boxing": "H01_boxing",
    "H02_golf": "H02_golf",
}
TARGET_LENGTH_M = {
    "humerus_l": 0.3175,
    "humerus_r": 0.3175,
    "ulna_l": 0.255,
    "radius_l": 0.255,
    "ulna_r": 0.255,
    "radius_r": 0.255,
    "femur_l": 0.48,
    "femur_r": 0.48,
    "tibia_l": 0.43,
    "tibia_r": 0.43,
}
LENGTH_JOINTS = {
    "humerus_l": ("acromial_l", "elbow_l"),
    "humerus_r": ("acromial_r", "elbow_r"),
    "ulna_l": ("elbow_l", "radius_hand_l"),
    "radius_l": ("elbow_l", "radius_hand_l"),
    "ulna_r": ("elbow_r", "radius_hand_r"),
    "radius_r": ("elbow_r", "radius_hand_r"),
    "femur_l": ("hip_l", "walker_knee_l"),
    "femur_r": ("hip_r", "walker_knee_r"),
    "tibia_l": ("walker_knee_l", "ankle_l"),
    "tibia_r": ("walker_knee_r", "ankle_r"),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _vec3(values):
    import opensim as osim

    return osim.Vec3(*(float(value) for value in values))


def _position(frame, state) -> np.ndarray:
    value = frame.getPositionInGround(state)
    return np.array([value[index] for index in range(3)], dtype=float)


def _joint_position(model, state, name: str) -> np.ndarray:
    return _position(model.getJointSet().get(name).getParentFrame(), state)


def configure_scaled_model(source_model: Path, output_model: Path) -> dict[str, object]:
    """Use official Model.scale with only the eight frozen limb lengths."""

    import opensim as osim

    configure_opensim_log(output_model.parent / "model_configure_opensim.log")
    model = osim.Model(str(source_model.resolve()))
    state = model.initSystem()
    model.assemble(state)
    model.realizePosition(state)
    original_lengths: dict[str, float] = {}
    factors: dict[str, float] = {}
    scales = osim.ScaleSet()
    for body_name, target in TARGET_LENGTH_M.items():
        first, second = LENGTH_JOINTS[body_name]
        length = float(np.linalg.norm(_joint_position(model, state, first) - _joint_position(model, state, second)))
        original_lengths[body_name] = length
        factor = target / length
        factors[body_name] = factor
        scale = osim.Scale()
        scale.setSegmentName(body_name)
        scale.setScaleFactors(osim.Vec3(factor, factor, factor))
        scales.cloneAndAppend(scale)
    if not model.scale(state, scales, False):
        raise RuntimeError("official Model.scale returned false")

    coordinates = model.updCoordinateSet()
    coordinate_policy = {}
    for index in range(coordinates.getSize()):
        coordinate = coordinates.get(index)
        name = coordinate.getName()
        if name in COUPLED_COORDINATES:
            coordinate_policy[name] = "official coupled constraint unchanged"
        elif name in ENABLED_COORDINATES:
            coordinate.setDefaultLocked(False)
            coordinate_policy[name] = "orientation-observed, official ROM retained"
        elif name in {"pelvis_tx", "pelvis_ty", "pelvis_tz"}:
            coordinate.setDefaultValue(0.0)
            coordinate.setDefaultLocked(True)
            coordinate_policy[name] = "frozen-A zero root translation gauge"
        else:
            if name in {"pro_sup_l", "pro_sup_r"}:
                coordinate.setDefaultValue(0.0)
            coordinate.setDefaultLocked(True)
            coordinate_policy[name] = "unobserved, official default locked"

    for segment, body_name in BODY_BY_SEGMENT.items():
        body = model.updBodySet().get(body_name)
        frame = osim.PhysicalOffsetFrame()
        frame.setName(FRAME_BY_SEGMENT[segment])
        frame.setParentFrame(body)
        frame.set_translation(osim.Vec3(0.0, 0.0, 0.0))
        frame.set_orientation(osim.Vec3(0.0, 0.0, 0.0))
        body.addComponent(frame)
    model.finalizeConnections()
    model.initSystem()
    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model.resolve()))
    return {
        "source_model": str(source_model.resolve()),
        "source_model_sha256": sha256_file(source_model),
        "configured_model_sha256": sha256_file(output_model),
        "model_joint_centres": "official generic model-derived; not subject anatomical truth",
        "scale_api": "OpenSim::Model.scale",
        "scale_isotropic": True,
        "measured_surface_proxy_lengths_m": TARGET_LENGTH_M,
        "unscaled_model_lengths_m": original_lengths,
        "body_scale_factors": factors,
        "unmeasured_pelvis_torso_and_other_dimensions": "official model defaults retained",
        "imu_frame_translation_m": [0.0, 0.0, 0.0],
        "coordinate_policy": coordinate_policy,
    }


def _robust_wxyz(rows: np.ndarray) -> np.ndarray:
    """Sign-invariant component median over the preregistered stable interval."""

    values = np.asarray(rows, dtype=float).copy()
    reference = values[0]
    values[np.einsum("ij,j->i", values, reference) < 0.0] *= -1.0
    result = np.median(values, axis=0)
    return result / np.linalg.norm(result)


def write_calibration_table(episode, output: Path) -> dict[str, object]:
    rows = {}
    dispersions = {}
    for segment in BODY_BY_SEGMENT:
        source = episode.segments[segment].quat_world_segment_wxyz[CALIBRATION_SLICE]
        mean = _robust_wxyz(source)
        rows[segment] = mean
        dots = np.clip(np.abs(source @ mean), 0.0, 1.0)
        angles = 2.0 * np.arccos(dots)
        dispersions[segment] = {
            "median_rad": float(np.median(angles)),
            "p95_rad": float(np.quantile(angles, 0.95)),
            "max_rad": float(np.max(angles)),
        }
    labels = [FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT]
    line = "0\t" + "\t".join(
        ",".join(f"{value:.17g}" for value in rows[segment]) for segment in BODY_BY_SEGMENT
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "DataRate=200\nDataType=Quaternion\nversion=3\nOpenSimVersion=4.6\nendheader\n"
        + "time\t" + "\t".join(labels) + "\n" + line + "\n",
        encoding="utf-8",
    )
    return {
        "frozen_episode": "01",
        "selection_frozen_before_B": True,
        "selection": "central rows [175,526), 351 rows; no result-selected row",
        "aggregation": "sign-aligned componentwise median normalized once",
        "rows_used": 351,
        "dispersion": dispersions,
        "table_sha256": sha256_file(output),
    }


def run_imu_placer(model_path: Path, calibration_sto: Path, output_model: Path) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_model.parent / "imu_placer_opensim.log")
    tool = osim.IMUPlacer()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientation_file_for_calibration(str(calibration_sto.resolve()))
    tool.set_sensor_to_opensim_rotations(_vec3(SENSOR_TO_OPENSIM_XYZ_RAD))
    tool.set_base_imu_label("")
    tool.set_base_heading_axis("")
    tool.set_output_model_file(str(output_model.resolve()))
    setup = output_model.parent / "imu_placer_setup.xml"
    tool.printToXML(str(setup.resolve()))
    calibrated = tool.run(False)
    if not output_model.is_file():
        raise RuntimeError("official IMUPlacer did not write calibrated model")
    return {
        "engine": "official OpenSim::IMUPlacer",
        "opensim_version": osim.GetVersionAndDate(),
        "returned_model": calibrated is not None,
        "sensor_to_opensim_rotations_xyz_rad": list(SENSOR_TO_OPENSIM_XYZ_RAD),
        "base_heading_correction": "disabled; frozen C2 world yaw gauge retained",
        "output_model_sha256": sha256_file(output_model),
        "setup_sha256": sha256_file(setup),
        "wall_s": time.monotonic() - started,
    }


def run_ik(model_path: Path, episode, output_dir: Path) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    input_sto = output_dir / "frozen_orientations.sto"
    reference = episode.segments["pelvis"].time_root_s
    elapsed = reference - reference[0]
    labels = [FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT]
    lines = [f"DataRate={1.0 / float(np.median(np.diff(elapsed))):.17g}", "DataType=Quaternion", "version=3", "OpenSimVersion=4.6", "endheader", "time\t" + "\t".join(labels)]
    for row, time_s in enumerate(elapsed):
        values = []
        for segment in BODY_BY_SEGMENT:
            quat = episode.segments[segment].quat_world_segment_wxyz[row]
            values.append(",".join(f"{value:.17g}" for value in quat))
        lines.append(f"{float(time_s):.17g}\t" + "\t".join(values))
    input_sto.write_text("\n".join(lines) + "\n", encoding="utf-8")
    input_manifest = {"episode": episode.key, "rows": len(elapsed), "labels": labels, "first_elapsed_time_s": 0.0, "last_elapsed_time_s": float(elapsed[-1]), "quaternion_values_changed": False, "sha256": sha256_file(input_sto)}
    configure_opensim_log(output_dir / "opensim.log")
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientations_file(str(input_sto.resolve()))
    tool.set_sensor_to_opensim_rotations(_vec3(SENSOR_TO_OPENSIM_XYZ_RAD))
    tool.set_time_range(0, 0.0)
    tool.set_time_range(1, float(input_manifest["last_elapsed_time_s"]))
    tool.set_results_directory(str(output_dir.resolve()))
    tool.set_output_motion_file("official_ik.sto")
    tool.set_report_errors(True)
    weights = osim.OrientationWeightSet()
    for segment in BODY_BY_SEGMENT:
        weights.cloneAndAppend(osim.OrientationWeight(FRAME_BY_SEGMENT[segment], 1.0))
    tool.set_orientation_weights(weights)
    # Reuse the already validated plumbing adapter's official solver accuracy.
    tool.set_accuracy(1e-7)
    setup = output_dir / "imu_ik_setup.xml"
    tool.printToXML(str(setup.resolve()))
    if not tool.run(False):
        raise RuntimeError("official IMUInverseKinematicsTool returned false")
    motion = output_dir / "official_ik.sto"
    errors = output_dir / "official_ik.sto_orientationErrors.sto"
    if not motion.is_file() or not errors.is_file():
        raise RuntimeError("official IK outputs are missing")
    return {
        "episode": episode.key,
        "input": input_manifest,
        "engine": "official OpenSim::IMUInverseKinematicsTool/InverseKinematicsSolver",
        "accuracy": 1e-7,
        "equal_orientation_weights": 1.0,
        "motion": str(motion.resolve()),
        "motion_sha256": sha256_file(motion),
        "errors": str(errors.resolve()),
        "errors_sha256": sha256_file(errors),
        "orientation_errors": orientation_error_summary(errors),
        "wall_s": time.monotonic() - started,
    }


def run_identity(model_path: Path, output_dir: Path) -> dict[str, object]:
    """Generate three rows from the calibrated model and recover them officially."""

    import opensim as osim

    output_dir.mkdir(parents=True, exist_ok=True)
    configure_opensim_log(output_dir / "identity_generate.log")
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    coordinates = model.updCoordinateSet()
    expected = {}
    for index in range(coordinates.getSize()):
        coordinate = coordinates.get(index)
        if coordinate.getLocked(state) or coordinate.getName() in COUPLED_COORDINATES:
            continue
        lo, hi = coordinate.getRangeMin(), coordinate.getRangeMax()
        value = max(lo + 0.2 * (hi - lo), min(hi - 0.2 * (hi - lo), 0.08 * math.sin(index + 1.0)))
        coordinate.setValue(state, value, False)
        expected[coordinate.getName()] = value
    # Ensure model-generated truth satisfies all official coupled constraints.
    model.assemble(state)
    model.realizePosition(state)
    labels = [FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT]
    quats = []
    for segment in BODY_BY_SEGMENT:
        frame = model.getComponent(f"/bodyset/{BODY_BY_SEGMENT[segment]}/{FRAME_BY_SEGMENT[segment]}")
        quat = frame.getRotationInGround(state).convertRotationToQuaternion()
        quats.append(",".join(f"{quat.get(i):.17g}" for i in range(4)))
    input_sto = output_dir / "model_generated.sto"
    input_sto.write_text(
        "DataRate=100\nDataType=Quaternion\nversion=3\nOpenSimVersion=4.6\nendheader\n"
        + "time\t" + "\t".join(labels) + "\n"
        + "\n".join(f"{t:.2f}\t" + "\t".join(quats) for t in (0.0, 0.01, 0.02)) + "\n",
        encoding="utf-8",
    )
    # Identity input is already in OpenSim coordinates, hence zero basis rotation.
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientations_file(str(input_sto.resolve()))
    tool.set_sensor_to_opensim_rotations(osim.Vec3(0.0, 0.0, 0.0))
    tool.set_time_range(0, 0.0); tool.set_time_range(1, 0.02)
    tool.set_results_directory(str(output_dir.resolve()))
    tool.set_output_motion_file("identity_ik.sto")
    tool.set_report_errors(True)
    tool.set_accuracy(1e-7)
    if not tool.run(False):
        raise RuntimeError("identity official IK failed")
    errors = orientation_error_summary(output_dir / "identity_ik.sto_orientationErrors.sto")
    passed = bool(errors["all_finite"] and errors["overall_max_rad"] <= 2e-4)
    result = {"passed": passed, "orientation_tolerance_rad": 2e-4, "orientation_errors": errors}
    (output_dir / "IDENTITY_RESULT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if not passed:
        raise RuntimeError("identity orientation round trip exceeded tolerance")
    return result


def replay_model(model_path: Path, motion_path: Path, log_path: Path) -> tuple[np.ndarray, list[dict[str, np.ndarray]], dict[str, object]]:
    import opensim as osim

    configure_opensim_log(log_path)
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    table = osim.TimeSeriesTable(str(motion_path.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    labels = list(table.getColumnLabels())
    columns = {label: np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float) for label in labels}
    coordinates = model.updCoordinateSet()
    by_name = {coordinates.get(i).getName(): coordinates.get(i) for i in range(coordinates.getSize())}
    joint_names = {
        "shoulder_left": "acromial_l", "shoulder_right": "acromial_r",
        "hip_left": "hip_l", "hip_right": "hip_r", "elbow_left": "elbow_l",
        "elbow_right": "elbow_r", "wrist_left": "radius_hand_l", "wrist_right": "radius_hand_r",
        "knee_left": "walker_knee_l", "knee_right": "walker_knee_r",
        "ankle_left": "ankle_l", "ankle_right": "ankle_r",
    }
    rows = []
    violations = {name: 0 for name in labels}
    near_limits = {name: 0 for name in labels}
    for row in range(len(times)):
        state.setTime(float(times[row]))
        for name in labels:
            coordinate = by_name[name]
            if coordinate.getLocked(state):
                continue
            value = math.radians(float(columns[name][row])) if int(coordinate.getMotionType()) == 1 or name in COUPLED_COORDINATES else float(columns[name][row])
            coordinate.setValue(state, value, False)
            lo, hi = coordinate.getRangeMin(), coordinate.getRangeMax()
            # Only clamped coordinates own an enforceable ROM boundary. Root
            # gauge coordinates retain informational ranges but are unclamped.
            if coordinate.getClamped(state):
                violations[name] += int(value < lo - 1e-8 or value > hi + 1e-8)
                near_limits[name] += int(value <= lo + math.radians(1.0) or value >= hi - math.radians(1.0))
        model.realizePosition(state)
        pelvis = _position(model.getBodySet().get("pelvis"), state)
        points_os = {"pelvis_center": pelvis}
        for point_name, joint_name in joint_names.items():
            points_os[point_name] = _joint_position(model, state, joint_name)
        points_os["shoulder_mid"] = 0.5 * (points_os["shoulder_left"] + points_os["shoulder_right"])
        points = {name: C2_FROM_OPENSIM @ (value - pelvis) for name, value in points_os.items()}
        rows.append(points)
    rom = {"violation_count": violations, "within_1deg_of_limit_count": near_limits, "rows": len(times)}
    return times, rows, rom
