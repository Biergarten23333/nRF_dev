"""Thin official-OpenSim soft-articulation adapter.

The model is the already validated frozen-segment-frame OpenSim model.  Its
elbow BallJoints retain flexion, out-of-plane, and axial freedom, while the
official InverseKinematicsSolver receives one broad CoordinateReference for
each elbow's neutral-frame ``ry`` coordinate.  No optimizer, residual, or
Jacobian is implemented here.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    C2_FROM_OPENSIM,
    FRAME_BY_SEGMENT,
    SEGMENTS,
    SENSOR_TO_OPENSIM_XYZ_RAD,
    _initialize_state_from_measurement,
    _quat_wxyz_matrix,
    _rotation_wxyz,
    _table_columns,
    _write_scalar_sto,
    build_model,
    configure_log,
    orientation_error_summary,
    sha256_file,
    write_episode_sto,
)
from biospur_fusion.c2_fk_to_opensim_ik.render import episode_metrics, replay_model


# Largest clean-flexion parent-axis leave-one-block-out deviation.  It is used
# only as a broad uncertainty scale, never as a hard axis or acceptance limit.
OUT_OF_PLANE_SIGMA_RAD = 0.553178986187994
OUT_OF_PLANE_WEIGHT = 1.0 / OUT_OF_PLANE_SIGMA_RAD**2
SOFT_COORDINATES = ("elbow_left_ry", "elbow_right_ry")
ORIENTATION_WEIGHT = 1.0
CONSTRAINT_WEIGHT = 1e-4
SOLVER_ACCURACY = 1e-7


def build_candidate_model(frozen, output_path: Path) -> dict[str, object]:
    """Build and relabel the frozen-frame model without changing its manifold."""

    import opensim as osim

    base = build_model(frozen, output_path)
    configure_log(output_path.parent / "candidate_model_opensim.log")
    model = osim.Model(str(output_path.resolve()))
    model.setName("C2_frozen_frame_soft_elbow_candidate")
    model.finalizeConnections()
    model.initSystem()
    model.printToXML(str(output_path.resolve()))
    result = {
        "schema": "biospur.c2_fk_to_opensim_soft_elbow.model.v1",
        "model_sha256": sha256_file(output_path),
        "opensim_version": osim.GetVersionAndDate(),
        "base_model_manifest": base,
        "joint_centres_and_lengths_owner": "frozen C2 3A display-proxy geometry; explicitly non-anatomical",
        "shoulder_hip_trunk_joint_type": "OpenSim BallJoint, 3 rotational coordinates",
        "elbow_joint_type": "OpenSim BallJoint, 3 rotational coordinates",
        "elbow_neutral_chart": {
            "rx": "primary bend coordinate by deterministic frozen-frame convention; not an anatomical axis claim",
            "ry": "out-of-plane coordinate by deterministic frozen-frame convention; official soft CoordinateReference in IK",
            "rz": "axial coordinate by deterministic frozen-frame convention; unpenalized",
        },
        "coordinate_ranges_rad": [-math.pi, math.pi],
        "coordinate_ranges_are_chart_not_anatomical_rom": True,
        "functional_axis_used_as_hard_axis": False,
        "model_has_no_project_owned_constraint_or_optimizer": True,
    }
    (output_path.parent / "MODEL_OWNERSHIP.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _coordinate_references(osim):
    references = osim.SimTKArrayCoordinateReference()
    owners = []
    for name in SOFT_COORDINATES:
        function = osim.Constant(0.0)
        reference = osim.CoordinateReference(name, function)
        reference.setWeight(OUT_OF_PLANE_WEIGHT)
        references.push_back(reference)
        owners.append((function, reference))
    # The 4.6 Python bindings expose C++ reference arrays through SWIG.  Keep
    # the Python wrappers and their Function arguments alive with the solver;
    # passing temporaries can leave dangling wrapped references.
    return references, owners


def _run_official_solver(model_path: Path, orientations_path: Path, episode, output_dir: Path) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_log(output_dir / "opensim.log")
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    table = osim.TimeSeriesTableQuaternion(str(orientations_path.resolve()))
    basis = osim.Rotation(SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0))
    osim.OpenSenseUtilities.rotateOrientationTable(table, basis)
    rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(table)
    orientations = osim.OrientationsReference(rotations)
    orientations.setDefaultWeight(ORIENTATION_WEIGHT)
    marker_reference = osim.MarkersReference()
    coordinate_references, coordinate_reference_owners = _coordinate_references(osim)
    solver = osim.InverseKinematicsSolver(
        model,
        marker_reference,
        orientations,
        coordinate_references,
        CONSTRAINT_WEIGHT,
    )
    solver.setAccuracy(SOLVER_ACCURACY)

    times = np.asarray(rotations.getIndependentColumn(), dtype=float)
    coordinates = model.updCoordinateSet()
    coordinate_labels = [coordinates.get(i).getName() for i in range(coordinates.getSize())]
    sensor_labels = [
        solver.getOrientationSensorNameForIndex(i)
        for i in range(solver.getNumOrientationSensorsInUse())
    ]
    motion_rows: list[list[float]] = []
    error_rows: list[list[float]] = []
    for row_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        _initialize_state_from_measurement(model, state, episode, row_index)
        # Official assemble() is required after the external per-frame chart
        # initialization; track() retains a prior internal solution.
        solver.assemble(state)
        motion_rows.append(
            [math.degrees(float(coordinates.get(i).getValue(state))) for i in range(coordinates.getSize())]
        )
        errors = osim.SimTKArrayDouble()
        solver.computeCurrentOrientationErrors(errors)
        error_rows.append([float(errors.getElt(i)) for i in range(errors.size())])

    motion_path = output_dir / "official_ik.sto"
    error_path = output_dir / "official_ik.sto_orientationErrors.sto"
    _write_scalar_sto(
        motion_path,
        times,
        coordinate_labels,
        motion_rows,
        name="official_ik.sto",
        in_degrees=True,
    )
    _write_scalar_sto(
        error_path,
        times,
        sensor_labels,
        error_rows,
        name="OrientationErrors",
        in_degrees=False,
    )
    return {
        "engine": "OpenSim::InverseKinematicsSolver",
        "opensim_version": osim.GetVersionAndDate(),
        "official_coordinate_references": {
            name: {"value_rad": 0.0, "weight": OUT_OF_PLANE_WEIGHT}
            for name in SOFT_COORDINATES
        },
        "orientation_default_weight": ORIENTATION_WEIGHT,
        "constraint_weight": CONSTRAINT_WEIGHT,
        "accuracy": SOLVER_ACCURACY,
        "per_frame_lifecycle": "measurement chart then official assemble",
        "swig_reference_owner_count": len(coordinate_reference_owners),
        "wall_s": time.monotonic() - started,
        "motion": str(motion_path.resolve()),
        "motion_sha256": sha256_file(motion_path),
        "errors": str(error_path.resolve()),
        "errors_sha256": sha256_file(error_path),
        "log_sha256": sha256_file(output_dir / "opensim.log"),
    }


def _matrix_to_wxyz(matrix: np.ndarray) -> np.ndarray:
    import opensim as osim

    rotation = osim.Rotation(osim.Mat33(*(float(value) for value in matrix.reshape(-1))))
    return _rotation_wxyz(rotation)


def _fixture_episode(model_path: Path):
    """Generate signed interior rows through official model FK."""

    import opensim as osim

    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    coordinates = model.updCoordinateSet()
    by_name = {coordinates.get(i).getName(): coordinates.get(i) for i in range(coordinates.getSize())}
    times = np.arange(4, dtype=float) * 0.01
    requested_ry = np.array([0.18, -0.18, 0.10, -0.10], dtype=float)
    rows = {segment: [] for segment in SEGMENTS}
    for ry in requested_ry:
        for coordinate in by_name.values():
            coordinate.setValue(state, 0.0, False)
        by_name["elbow_left_rx"].setValue(state, 0.35, False)
        by_name["elbow_left_ry"].setValue(state, float(ry), False)
        by_name["elbow_left_rz"].setValue(state, 0.22, False)
        model.realizePosition(state)
        for segment in SEGMENTS:
            rotation_os = np.array(
                [
                    [model.getBodySet().get(segment).getRotationInGround(state).get(i, j) for j in range(3)]
                    for i in range(3)
                ],
                dtype=float,
            )
            rows[segment].append(_matrix_to_wxyz(C2_FROM_OPENSIM @ rotation_os))
    series = {
        segment: SimpleNamespace(
            time_root_s=times,
            quat_world_segment_wxyz=np.asarray(values),
            mask=np.ones(len(times), dtype=bool),
        )
        for segment, values in rows.items()
    }
    return (
        SimpleNamespace(
            key="model_generated_signed_soft_elbow",
            segments=series,
            frame_count=len(times),
            valid_frame_mask=np.ones(len(times), dtype=bool),
        ),
        requested_ry,
    )


def run_signed_fixture(model_path: Path, output_dir: Path) -> dict[str, object]:
    started = time.monotonic()
    episode, requested_ry = _fixture_episode(model_path)
    input_path = output_dir / "input" / "model_generated_orientations.sto"
    input_manifest = write_episode_sto(episode, input_path)
    official = _run_official_solver(model_path, input_path, episode, output_dir / "official")
    _, motion = _table_columns(Path(official["motion"]))
    observed = np.deg2rad(motion["elbow_left_ry"])
    orientation = orientation_error_summary(Path(official["errors"]))
    paired_symmetry = max(abs(observed[0] + observed[1]), abs(observed[2] + observed[3]))
    result = {
        "schema": "biospur.c2_fk_to_opensim_soft_elbow.fixture.v1",
        "passed": bool(
            orientation["all_finite"]
            and np.all(np.sign(observed) == np.sign(requested_ry))
            and np.all(np.abs(observed) < np.abs(requested_ry))
            and paired_symmetry <= 1e-4
        ),
        "requested_out_of_plane_rad": requested_ry.tolist(),
        "solved_out_of_plane_rad": observed.tolist(),
        "signed_response_preserved": bool(np.all(np.sign(observed) == np.sign(requested_ry))),
        "soft_reference_reduces_magnitude": bool(np.all(np.abs(observed) < np.abs(requested_ry))),
        "paired_symmetry_error_rad": float(paired_symmetry),
        "fixture_coordinates_rad": {"elbow_left_rx": 0.35, "elbow_left_rz": 0.22},
        "orientation_errors": orientation,
        "input": input_manifest,
        "official": official,
        "wall_s": time.monotonic() - started,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "FIXTURE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _rotation_distance(left: np.ndarray, right: np.ndarray) -> float:
    cosine = np.clip((np.trace(left.T @ right) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.arccos(cosine))


def _summarize(values: np.ndarray, suffix: str) -> dict[str, float]:
    return {
        f"mean_{suffix}": float(np.mean(values)),
        f"median_{suffix}": float(np.median(values)),
        f"p95_{suffix}": float(np.quantile(values, 0.95)),
        f"max_{suffix}": float(np.max(values)),
    }


def analyze_episode(frozen, episode, model_path: Path, motion_path: Path) -> dict[str, object]:
    times, rows = replay_model(model_path, motion_path)
    orientation_changes = {segment: [] for segment in SEGMENTS}
    continuity = {segment: [] for segment in SEGMENTS}
    for index, row in enumerate(rows):
        for segment in SEGMENTS:
            measured = _quat_wxyz_matrix(episode.segments[segment].quat_world_segment_wxyz[index])
            orientation_changes[segment].append(_rotation_distance(measured, row[segment]["R"]))
            if index:
                continuity[segment].append(_rotation_distance(rows[index - 1][segment]["R"], row[segment]["R"]))
    motion_times, columns_deg = _table_columns(motion_path)
    if not np.array_equal(times, motion_times):
        raise ValueError("motion/replay time mismatch")
    columns = {name: np.deg2rad(values) for name, values in columns_deg.items()}
    result = episode_metrics(frozen, episode, rows)
    result.update(
        {
            "orientation_change_by_segment": {
                segment: _summarize(np.asarray(values), "rad")
                for segment, values in orientation_changes.items()
            },
            "orientation_change_overall": _summarize(
                np.concatenate([np.asarray(values) for values in orientation_changes.values()]), "rad"
            ),
            "so3_continuity_by_segment": {
                segment: _summarize(np.asarray(values), "rad_per_frame")
                for segment, values in continuity.items()
            },
            "soft_coordinate_trajectories": {
                name: {
                    **_summarize(np.abs(columns[name]), "abs_rad"),
                    "min_rad": float(np.min(columns[name])),
                    "max_rad": float(np.max(columns[name])),
                    "near_chart_limit_rows": int(
                        np.count_nonzero(np.abs(columns[name]) >= math.pi - math.radians(1.0))
                    ),
                }
                for name in SOFT_COORDINATES
            },
            "motion_source_units": "degree",
            "motion_converted_to_rad_exactly_once": True,
            "rows": len(rows),
        }
    )
    return result


def run_episode(frozen, episode, model_path: Path, output_dir: Path) -> dict[str, object]:
    started = time.monotonic()
    input_path = output_dir / "input" / "frozen_orientations.sto"
    input_manifest = write_episode_sto(episode, input_path)
    official = _run_official_solver(model_path, input_path, episode, output_dir / "official")
    errors = orientation_error_summary(Path(official["errors"]))
    analysis = analyze_episode(frozen, episode, model_path, Path(official["motion"]))
    result = {
        "schema": "biospur.c2_fk_to_opensim_soft_elbow.episode.v1",
        "episode": episode.key,
        "finite": bool(errors["all_finite"] and analysis["finite"]),
        "input": input_manifest,
        "official": official,
        "orientation_errors": errors,
        "analysis": analysis,
        "wall_s": time.monotonic() - started,
    }
    (output_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
