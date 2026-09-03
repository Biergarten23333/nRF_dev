"""Official OpenSim IK adapter for the immutable Capture-2 FK trajectory.

The OpenSim body frame for every body is, by construction, the corresponding
frozen C2 segment frame.  This module defines an articulated model and invokes
OpenSim's IMUInverseKinematicsTool; it contains no optimizer or calibration.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path
from typing import Mapping

import numpy as np


SEGMENTS = (
    "pelvis",
    "torso",
    "upper_arm_left",
    "forearm_left",
    "upper_arm_right",
    "forearm_right",
    "thigh_left",
    "shank_left",
    "thigh_right",
    "shank_right",
)
FRAME_BY_SEGMENT = {name: f"{name}_imu" for name in SEGMENTS}
PILOT_EPISODES = {
    "00_initial_still": "00",
    "02_t_pose": "01",
    "06_upper_dynamic": "06",
    "10_lower_dynamic": "10",
    "H01_boxing": "H01_boxing",
    "H02_golf": "H02_golf",
}

# C2 is Z-up. OpenSim is Y-up. The official tool applies this active global
# rotation once to every input orientation: R_OS<-F = Rx(-pi/2) R_C2<-F.
SENSOR_TO_OPENSIM_XYZ_RAD = (-math.pi / 2.0, 0.0, 0.0)
C2_FROM_OPENSIM = np.array(
    [[1.0, 0.0, 0.0], [0.0, 0.0, -1.0], [0.0, 1.0, 0.0]], dtype=float
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configure_log(path: Path) -> None:
    import opensim as osim

    path.parent.mkdir(parents=True, exist_ok=True)
    osim.Logger.removeFileSink()
    osim.Logger.addFileSink(str(path.resolve()))


def _vec3(values) -> object:
    import opensim as osim

    return osim.Vec3(*(float(value) for value in values))


def _configure_coordinate(coordinate, name: str) -> None:
    coordinate.setName(name)
    coordinate.setDefaultValue(0.0)
    coordinate.setRangeMin(-math.pi)
    coordinate.setRangeMax(math.pi)
    coordinate.setDefaultLocked(False)
    coordinate.setDefaultClamped(True)


def build_model(frozen, output_path: Path) -> dict[str, object]:
    """Build the one fixed articulated model in frozen segment coordinates."""

    import opensim as osim

    started = time.monotonic()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    configure_log(output_path.parent / "model_build_opensim.log")
    model = osim.Model()
    model.setName("C2_frozen_segment_frame_model")
    model.setGravity(osim.Vec3(0.0, -9.80665, 0.0))

    # Inertial quantities are non-physical computational placeholders and do
    # not enter kinematic IK. Geometry below is the immutable A geometry.
    bodies = {}
    for segment in SEGMENTS:
        body = osim.Body(
            segment,
            1.0,
            osim.Vec3(0.0, 0.0, 0.0),
            osim.Inertia(1.0, 1.0, 1.0, 0.0, 0.0, 0.0),
        )
        model.addBody(body)
        bodies[segment] = body

    zero = osim.Vec3(0.0, 0.0, 0.0)
    root = osim.BallJoint("ground_pelvis", model.getGround(), zero, zero, bodies["pelvis"], zero, zero)
    model.addJoint(root)
    for index, suffix in enumerate(("rx", "ry", "rz")):
        _configure_coordinate(root.updCoordinate(index), f"pelvis_{suffix}")

    geometry = frozen.geometry
    length = geometry.segment_length_m
    ball_specs = (
        ("pelvis_torso", "pelvis", "torso", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ("shoulder_left", "torso", "upper_arm_left", (-0.5 * geometry.shoulder_span_m, 0.0, geometry.torso_height_m), (0.0, 0.0, 0.0)),
        ("shoulder_right", "torso", "upper_arm_right", (0.5 * geometry.shoulder_span_m, 0.0, geometry.torso_height_m), (0.0, 0.0, 0.0)),
        ("hip_left", "pelvis", "thigh_left", (-0.5 * geometry.hip_span_m, 0.0, 0.0), (0.0, 0.0, 0.0)),
        ("hip_right", "pelvis", "thigh_right", (0.5 * geometry.hip_span_m, 0.0, 0.0), (0.0, 0.0, 0.0)),
    )
    for name, parent, child, parent_location, child_location in ball_specs:
        joint = osim.BallJoint(
            name,
            bodies[parent],
            _vec3(parent_location),
            zero,
            bodies[child],
            _vec3(child_location),
            zero,
        )
        model.addJoint(joint)
        for index, suffix in enumerate(("rx", "ry", "rz")):
            _configure_coordinate(joint.updCoordinate(index), f"{name}_{suffix}")

    distal_joint_specs = (
        ("elbow_left", "upper_arm_left", "forearm_left"),
        ("elbow_right", "upper_arm_right", "forearm_right"),
        ("knee_left", "thigh_left", "shank_left"),
        ("knee_right", "thigh_right", "shank_right"),
    )
    hinge_manifest = {}
    for name, parent, child in distal_joint_specs:
        axis = frozen.hinge_axes[name]
        joint = osim.BallJoint(
            name,
            bodies[parent],
            _vec3((0.0, 0.0, -length[parent])),
            zero,
            bodies[child],
            zero,
            zero,
        )
        model.addJoint(joint)
        for index, suffix in enumerate(("rx", "ry", "rz")):
            _configure_coordinate(joint.updCoordinate(index), f"{name}_{suffix}")
        hinge_manifest[name] = {
            "parent": parent,
            "child": child,
            "joint_type": "BallJoint",
            "reason": "frozen segment frames do not own a signed anatomical hinge frame; fixed-length connection is retained without completing one",
            "parent_axis_segment": axis.parent_axis_reset_segment.tolist(),
            "child_axis_segment": axis.child_axis_reset_segment.tolist(),
            "functional_axis_used_as_hard_joint_frame": False,
        }

    for segment in SEGMENTS:
        frame = osim.PhysicalOffsetFrame()
        frame.setName(FRAME_BY_SEGMENT[segment])
        frame.setParentFrame(bodies[segment])
        frame.set_translation(zero)
        frame.set_orientation(zero)
        bodies[segment].addComponent(frame)

    model.finalizeConnections()
    model.initSystem()
    model.printToXML(str(output_path.resolve()))
    result = {
        "model_sha256": sha256_file(output_path),
        "opensim_version": osim.GetVersionAndDate(),
        "bodies": list(SEGMENTS),
        "coordinates": model.getNumCoordinates(),
        "root_translation_m": [0.0, 0.0, 0.0],
        "body_frames_equal_frozen_segment_frames": True,
        "imu_offsets_identity": True,
        "geometry_scope": frozen.geometry.scope,
        "segment_lengths_m": dict(length),
        "torso_height_m": geometry.torso_height_m,
        "shoulder_span_m": geometry.shoulder_span_m,
        "hip_span_m": geometry.hip_span_m,
        "hinges": hinge_manifest,
        "ball_coordinate_ranges_rad": [-math.pi, math.pi],
        "distal_ball_coordinate_ranges_rad": [-math.pi, math.pi],
        "mass_inertia_used_by_ik": False,
        "wall_s": time.monotonic() - started,
    }
    (output_path.parent / "MODEL_MANIFEST.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _write_orientation_sto(
    path: Path,
    times: np.ndarray,
    rows_by_segment: Mapping[str, np.ndarray],
) -> dict[str, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = [FRAME_BY_SEGMENT[segment] for segment in SEGMENTS]
    lines = [
        f"DataRate={1.0 / float(np.median(np.diff(times))):.17g}",
        "DataType=Quaternion",
        "version=3",
        "OpenSimVersion=4.6",
        "endheader",
        "time\t" + "\t".join(labels),
    ]
    for row_index, time_s in enumerate(times):
        values = []
        for segment in SEGMENTS:
            quat = np.asarray(rows_by_segment[segment][row_index], dtype=float)
            values.append(",".join(f"{item:.17g}" for item in quat))
        lines.append(f"{float(time_s):.17g}\t" + "\t".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"rows": len(times), "labels": labels, "sha256": sha256_file(path)}


def write_episode_sto(episode, path: Path) -> dict[str, object]:
    reference = episode.segments["pelvis"].time_root_s
    if not np.all(episode.valid_frame_mask):
        raise ValueError(f"masked frozen rows in {episode.key}")
    rows = {}
    for segment in SEGMENTS:
        series = episode.segments[segment]
        if not np.array_equal(series.time_root_s, reference) or not np.all(series.mask):
            raise ValueError(f"frozen row mismatch in {episode.key}/{segment}")
        rows[segment] = series.quat_world_segment_wxyz
    result = _write_orientation_sto(path, reference - reference[0], rows)
    result.update(
        {
            "episode": episode.key,
            "source_time_s": [float(reference[0]), float(reference[-1])],
            "elapsed_time_s": [0.0, float(reference[-1] - reference[0])],
            "quaternion_values_changed": False,
        }
    )
    return result


def _run_tool(
    model_path: Path,
    orientations_path: Path,
    output_dir: Path,
    time_range: tuple[float, float],
    sensor_rotation: tuple[float, float, float],
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "opensim.log")
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientations_file(str(orientations_path.resolve()))
    tool.set_sensor_to_opensim_rotations(_vec3(sensor_rotation))
    tool.set_time_range(0, float(time_range[0]))
    tool.set_time_range(1, float(time_range[1]))
    tool.set_results_directory(str(output_dir.resolve()))
    tool.set_output_motion_file("official_ik.sto")
    tool.set_report_errors(True)
    weights = osim.OrientationWeightSet()
    for segment in SEGMENTS:
        weights.cloneAndAppend(osim.OrientationWeight(FRAME_BY_SEGMENT[segment], 1.0))
    tool.set_orientation_weights(weights)
    tool.set_accuracy(1e-7)
    tool.printToXML(str((output_dir / "setup.xml").resolve()))
    succeeded = bool(tool.run(False))
    motion = output_dir / "official_ik.sto"
    errors = output_dir / "official_ik.sto_orientationErrors.sto"
    if not succeeded or not motion.is_file() or not errors.is_file():
        raise RuntimeError("official IMUInverseKinematicsTool did not produce outputs")
    return {
        "engine": "OpenSim::IMUInverseKinematicsTool/InverseKinematicsSolver",
        "opensim_version": osim.GetVersionAndDate(),
        "succeeded": succeeded,
        "equal_orientation_weights": 1.0,
        "accuracy": 1e-7,
        "sensor_to_opensim_rotations_xyz_rad": list(sensor_rotation),
        "wall_s": time.monotonic() - started,
        "motion": str(motion.resolve()),
        "motion_sha256": sha256_file(motion),
        "errors": str(errors.resolve()),
        "errors_sha256": sha256_file(errors),
        "setup_sha256": sha256_file(output_dir / "setup.xml"),
        "log_sha256": sha256_file(output_dir / "opensim.log"),
    }


def _rotation_wxyz(rotation) -> np.ndarray:
    quat = rotation.convertRotationToQuaternion()
    return np.array([quat.get(index) for index in range(4)], dtype=float)


def _table_columns(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    import opensim as osim

    table = osim.TimeSeriesTable(str(path.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    return times, {
        label: np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)
        for label in table.getColumnLabels()
    }


def orientation_error_summary(path: Path) -> dict[str, object]:
    times, columns = _table_columns(path)
    header = path.read_text(encoding="utf-8").split("endheader", 1)[0]
    # Pinned OpenSim converts the coordinate report to degrees, but writes
    # computeCurrentOrientationErrors() directly. Error tables therefore have
    # no inDegrees=yes metadata and are radians.
    source_units = "degree" if "inDegrees=yes" in header else "radian"
    if source_units == "degree":
        columns = {name: np.deg2rad(values) for name, values in columns.items()}
    sensors = {}
    for name, values in columns.items():
        sensors[name] = {
            "mean_rad": float(np.mean(values)),
            "median_rad": float(np.median(values)),
            "p95_rad": float(np.quantile(values, 0.95)),
            "max_rad": float(np.max(values)),
        }
    all_values = np.concatenate(list(columns.values()))
    return {
        "rows": len(times),
        "source_units": source_units,
        "reported_units": "radian",
        "converted_exactly_once": source_units == "degree",
        "all_finite": bool(np.all(np.isfinite(all_values))),
        "overall_mean_rad": float(np.mean(all_values)),
        "overall_p95_rad": float(np.quantile(all_values, 0.95)),
        "overall_max_rad": float(np.max(all_values)),
        "sensors": sensors,
    }


def _quat_wxyz_matrix(quat: np.ndarray) -> np.ndarray:
    w, x, y, z = (float(value) for value in quat)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ],
        dtype=float,
    )


def _body_xyz(matrix: np.ndarray) -> tuple[float, float, float]:
    import opensim as osim

    rotation = osim.Rotation(osim.Mat33(*(float(v) for v in matrix.reshape(-1))))
    xyz = rotation.convertRotationToBodyFixedXYZ()
    return tuple(float(xyz[index]) for index in range(3))


def _initialize_state_from_measurement(
    model, state, episode, row_index: int
) -> dict[str, float]:
    """Choose the BallJoint chart representing one measured A pose.

    This initializes official IK only; it fits no model parameter and is the
    same deterministic map for every episode.
    """

    c2_to_osim = C2_FROM_OPENSIM.T
    matrices = {
        segment: _quat_wxyz_matrix(
            episode.segments[segment].quat_world_segment_wxyz[row_index]
        )
        for segment in SEGMENTS
    }
    relative = {
        "ground_pelvis": c2_to_osim @ matrices["pelvis"],
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
    values = {}
    coordinates = model.updCoordinateSet()
    by_name = {
        coordinates.get(index).getName(): coordinates.get(index)
        for index in range(coordinates.getSize())
    }
    for joint_name, matrix in relative.items():
        prefix = "pelvis" if joint_name == "ground_pelvis" else joint_name
        for suffix, value in zip(("rx", "ry", "rz"), _body_xyz(matrix)):
            name = f"{prefix}_{suffix}"
            by_name[name].setValue(state, value, False)
            values[name] = value
    model.realizePosition(state)
    return values


def _write_scalar_sto(
    path: Path,
    times: np.ndarray,
    labels: list[str],
    rows: list[list[float]],
    *,
    name: str,
    in_degrees: bool,
) -> None:
    lines = []
    if in_degrees:
        lines.append("inDegrees=yes")
    lines.extend(
        [
            f"name={name}",
            "DataType=double",
            "version=3",
            "OpenSimVersion=4.6-2026-06-22-85aaf64",
            "endheader",
            "time\t" + "\t".join(labels),
        ]
    )
    for time_s, values in zip(times, rows):
        lines.append(
            f"{float(time_s):.17g}\t"
            + "\t".join(f"{float(value):.17g}" for value in values)
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_direct_solver(
    model_path: Path,
    orientations_path: Path,
    episode,
    output_dir: Path,
) -> dict[str, object]:
    """Invoke official InverseKinematicsSolver with measurement chart start."""

    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "opensim.log")
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    quaternion_table = osim.TimeSeriesTableQuaternion(str(orientations_path.resolve()))
    basis_rotation = osim.Rotation(
        SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0)
    )
    osim.OpenSenseUtilities.rotateOrientationTable(quaternion_table, basis_rotation)
    rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(quaternion_table)
    orientation_reference = osim.OrientationsReference(rotations)
    orientation_reference.setDefaultWeight(1.0)
    marker_reference = osim.MarkersReference()
    coordinate_references = osim.SimTKArrayCoordinateReference()
    solver = osim.InverseKinematicsSolver(
        model, marker_reference, orientation_reference, coordinate_references, 1e-4
    )
    solver.setAccuracy(1e-7)
    initial = _initialize_state_from_measurement(model, state, episode, 0)
    times = np.asarray(rotations.getIndependentColumn(), dtype=float)
    state.setTime(float(times[0]))
    solver.assemble(state)
    coordinates = model.updCoordinateSet()
    coordinate_labels = [
        coordinates.get(index).getName() for index in range(coordinates.getSize())
    ]
    sensor_labels = [
        solver.getOrientationSensorNameForIndex(index)
        for index in range(solver.getNumOrientationSensorsInUse())
    ]
    motion_rows = []
    error_rows = []
    for row_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        _initialize_state_from_measurement(model, state, episode, row_index)
        # assemble() reinitializes the official assembler from the state just
        # written above.  track() retains the solver's preceding internal
        # solution and therefore does not honor a per-frame chart reset.
        solver.assemble(state)
        motion_rows.append(
            [
                math.degrees(float(coordinates.get(index).getValue(state)))
                for index in range(coordinates.getSize())
            ]
        )
        errors = osim.SimTKArrayDouble()
        solver.computeCurrentOrientationErrors(errors)
        error_rows.append(
            [float(errors.getElt(index)) for index in range(errors.size())]
        )
    motion = output_dir / "official_ik.sto"
    errors_path = output_dir / "official_ik.sto_orientationErrors.sto"
    _write_scalar_sto(
        motion,
        times,
        coordinate_labels,
        motion_rows,
        name="official_ik.sto",
        in_degrees=True,
    )
    _write_scalar_sto(
        errors_path,
        times,
        sensor_labels,
        error_rows,
        name="OrientationErrors",
        in_degrees=False,
    )
    return {
        "engine": "OpenSim::InverseKinematicsSolver with OrientationsReference",
        "opensim_version": osim.GetVersionAndDate(),
        "succeeded": True,
        "equal_orientation_weights": 1.0,
        "accuracy": 1e-7,
        "constraint_weight": 1e-4,
        "sensor_to_opensim_rotations_xyz_rad": list(SENSOR_TO_OPENSIM_XYZ_RAD),
        "initialization": "deterministic per-frame measurement BallJoint chart followed by official assemble; no fitted model parameter",
        "initial_coordinates_rad": initial,
        "wall_s": time.monotonic() - started,
        "motion": str(motion.resolve()),
        "motion_sha256": sha256_file(motion),
        "errors": str(errors_path.resolve()),
        "errors_sha256": sha256_file(errors_path),
        "log_sha256": sha256_file(output_dir / "opensim.log"),
    }


def run_identity(model_path: Path, output_dir: Path) -> dict[str, object]:
    """Generate a nontrivial pose from this model and recover it with official IK."""

    import opensim as osim

    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "identity_generation_opensim.log")
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    coordinates = model.updCoordinateSet()
    expected = {}
    for index in range(coordinates.getSize()):
        coordinate = coordinates.get(index)
        value = 0.08 * math.sin(index + 1.0)
        coordinate.setValue(state, value, False)
        expected[coordinate.getName()] = value
    model.realizePosition(state)
    rows = {
        segment: np.repeat(
            _rotation_wxyz(model.getBodySet().get(segment).getRotationInGround(state))[None, :],
            3,
            axis=0,
        )
        for segment in SEGMENTS
    }
    input_path = output_dir / "model_generated_orientations.sto"
    input_manifest = _write_orientation_sto(input_path, np.array([0.0, 0.01, 0.02]), rows)
    official = _run_tool(model_path, input_path, output_dir / "official", (0.0, 0.02), (0.0, 0.0, 0.0))
    error = orientation_error_summary(Path(official["errors"]))
    _, motion_columns = _table_columns(Path(official["motion"]))
    coordinate_error = {}
    for name, expected_value in expected.items():
        observed = float(np.deg2rad(motion_columns[name][-1]))
        coordinate_error[name] = abs(observed - expected_value)
    result = {
        "passed": bool(
            error["all_finite"]
            and error["overall_max_rad"] <= 2e-4
            and max(coordinate_error.values()) <= 2e-4
        ),
        "round_trip_tolerance_rad": 2e-4,
        "input": input_manifest,
        "official": official,
        "orientation_errors": error,
        "max_coordinate_error_rad": max(coordinate_error.values()),
        "coordinate_errors_rad": coordinate_error,
    }
    (output_dir / "IDENTITY_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if not result["passed"]:
        raise RuntimeError("official model identity round-trip failed")
    return result


def run_episode(episode, model_path: Path, output_dir: Path) -> dict[str, object]:
    input_path = output_dir / "input" / "frozen_orientations.sto"
    input_manifest = write_episode_sto(episode, input_path)
    official = _run_direct_solver(
        model_path, input_path, episode, output_dir / "official"
    )
    errors = orientation_error_summary(Path(official["errors"]))
    result = {
        "episode": episode.key,
        "input": input_manifest,
        "official": official,
        "orientation_errors": errors,
        "finite": errors["all_finite"],
    }
    (output_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
