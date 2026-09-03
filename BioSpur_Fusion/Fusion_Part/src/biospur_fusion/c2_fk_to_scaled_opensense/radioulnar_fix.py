"""Bounded official-OpenSim radioulnar ownership correction.

The frozen C2 forearm orientation describes the tracked forearm frame.  The
Rajagopal model's ``pro_sup`` coordinate moves ``radius`` relative to ``ulna``.
This adapter therefore reparents the already calibrated forearm IMU frame to
``radius`` while preserving its placement-state transform, and unlocks the
existing official coordinate.  It does not recalibrate an IMU, fit an offset,
or implement IK mathematics.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_fk_to_opensim_ik.adapter import orientation_error_summary

from .pipeline import (
    BODY_BY_SEGMENT,
    C2_FROM_OPENSIM,
    SENSOR_TO_OPENSIM_XYZ_RAD,
    configure_opensim_log,
    replay_model,
    sha256_file,
)
from .render import a_points, render_episode


BASE_MODEL = Path(
    "logs/c2_fk_to_scaled_opensense_20260903_004220/model/"
    "calibrated_attempt_002.osim"
)
BASE_EPISODE_DIR = {
    "06": Path(
        "logs/c2_fk_to_scaled_opensense_20260903_004220/"
        "episodes_attempt_002_body_names/06_upper_dynamic"
    ),
    "07": Path(
        "logs/c2_fk_to_scaled_opensense_20260903_004220/"
        "all_19plus2/primary_07"
    ),
}
DISPLAY_LABEL = {"06": "06_elbow_left", "07": "07_elbow_right"}
FRAME_BY_SEGMENT_RADIUS = {
    segment: (
        "radius_l_imu"
        if segment == "forearm_left"
        else "radius_r_imu"
        if segment == "forearm_right"
        else f"{body}_imu"
    )
    for segment, body in BODY_BY_SEGMENT.items()
}
FRAME_BODY_BY_SEGMENT_RADIUS = {
    **dict(BODY_BY_SEGMENT),
    "forearm_left": "radius_l",
    "forearm_right": "radius_r",
}
PHASES_S = {"flexion": (0.0, 15.0), "pronation_supination": (15.0, 30.0)}
FIXTURE_TOLERANCE_RAD = 0.02


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(i, j) for j in range(3)] for i in range(3)], dtype=float
    )


def _vec3(values):
    import opensim as osim

    return osim.Vec3(*(float(value) for value in values))


def _rotation(matrix: np.ndarray):
    import opensim as osim

    return osim.Rotation(osim.Mat33(*(float(value) for value in matrix.ravel())))


def _rotation_angle(first: np.ndarray, second: np.ndarray) -> float:
    cosine = (float(np.trace(first.T @ second)) - 1.0) / 2.0
    return math.acos(float(np.clip(cosine, -1.0, 1.0)))


def _position(frame, state) -> np.ndarray:
    value = frame.getPositionInGround(state)
    return np.array([value[index] for index in range(3)], dtype=float)


def _frame_path(segment: str) -> str:
    return (
        f"/bodyset/{FRAME_BODY_BY_SEGMENT_RADIUS[segment]}/"
        f"{FRAME_BY_SEGMENT_RADIUS[segment]}"
    )


def configure_radius_model(
    workspace: Path,
    source_model: Path,
    output_model: Path,
    output_json: Path,
    pro_sup_reference: str = "official_default",
) -> dict[str, object]:
    """Reparent both calibrated forearm frames without changing placement pose."""

    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_json.parent / "configure_opensim.log")
    model = osim.Model(str(source_model.resolve()))
    source_coordinates = {}
    for side in ("l", "r"):
        coordinate = model.updCoordinateSet().get(f"pro_sup_{side}")
        source_coordinates[side] = {
            "range_rad": [
                float(coordinate.getRangeMin()),
                float(coordinate.getRangeMax()),
            ],
            "default_rad": float(coordinate.getDefaultValue()),
            "locked": bool(coordinate.getDefaultLocked()),
            "clamped": bool(coordinate.getDefaultClamped()),
        }
        if pro_sup_reference == "official_range_midpoint":
            coordinate.setDefaultValue(
                0.5 * (coordinate.getRangeMin() + coordinate.getRangeMax())
            )
        elif pro_sup_reference != "official_default":
            raise ValueError(f"unknown pro_sup reference policy: {pro_sup_reference}")
        coordinate.setDefaultLocked(False)
    state = model.initSystem()
    model.assemble(state)
    model.realizePosition(state)

    reports = {}
    placement_targets = {}
    for side in ("l", "r"):
        old_name = f"ulna_{side}_imu"
        old_path = f"/bodyset/ulna_{side}/{old_name}"
        old_frame = model.getComponent(old_path)
        radius = model.updBodySet().get(f"radius_{side}")
        old_rotation_ground = _matrix(old_frame.getRotationInGround(state))
        old_position_ground = _position(old_frame, state)
        radius_rotation_ground = _matrix(radius.getRotationInGround(state))
        radius_position_ground = _position(radius, state)
        radius_from_imu = radius_rotation_ground.T @ old_rotation_ground
        radius_to_imu = radius_rotation_ground.T @ (
            old_position_ground - radius_position_ground
        )
        placement_targets[side] = (old_rotation_ground, old_position_ground)

        old_frame.setName(f"{old_name}_inactive")
        new_frame = osim.PhysicalOffsetFrame()
        new_frame.setName(f"radius_{side}_imu")
        new_frame.setParentFrame(radius)
        new_frame.set_translation(_vec3(radius_to_imu))
        new_frame.set_orientation(
            _rotation(radius_from_imu).convertRotationToBodyFixedXYZ()
        )
        radius.addComponent(new_frame)

        coordinate = model.updCoordinateSet().get(f"pro_sup_{side}")
        before = source_coordinates[side]
        coordinate.setDefaultLocked(False)
        reports[side] = {
            "joint": f"radioulnar_{side}",
            "joint_type": model.getJointSet()
            .get(f"radioulnar_{side}")
            .getConcreteClassName(),
            "coordinate": f"pro_sup_{side}",
            "coordinate_before": before,
            "coordinate_after": {
                **before,
                "default_rad": float(coordinate.getDefaultValue()),
                "locked": False,
            },
            "old_frame_path": old_path,
            "inactive_frame_name": f"{old_name}_inactive",
            "new_frame_path": f"/bodyset/radius_{side}/radius_{side}_imu",
            "radius_from_imu_rotation": radius_from_imu.tolist(),
            "radius_to_imu_translation_m": radius_to_imu.tolist(),
            "translation_owner": (
                "deterministic placement-state rigid reparenting; preserves the "
                "old model-frame world point and is not a measured IMU position"
            ),
        }

    model.finalizeConnections()
    check_state = model.initSystem()
    model.assemble(check_state)
    model.realizePosition(check_state)
    for side in ("l", "r"):
        frame = model.getComponent(f"/bodyset/radius_{side}/radius_{side}_imu")
        target_rotation, target_position = placement_targets[side]
        reports[side]["placement_closure"] = {
            "orientation_error_rad": _rotation_angle(
                target_rotation, _matrix(frame.getRotationInGround(check_state))
            ),
            "position_error_m": float(
                np.linalg.norm(_position(frame, check_state) - target_position)
            ),
        }
        coordinate = model.getCoordinateSet().get(f"pro_sup_{side}")
        reports[side]["verified_coordinate"] = {
            "range_rad": [
                float(coordinate.getRangeMin()),
                float(coordinate.getRangeMax()),
            ],
            "default_rad": float(coordinate.getDefaultValue()),
            "locked": bool(coordinate.getDefaultLocked()),
            "clamped": bool(coordinate.getDefaultClamped()),
        }

    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model.resolve()))
    result = {
        "schema": "biospur-c2-radioulnar-model-ownership-v1",
        "source_model": str(source_model.resolve()),
        "source_model_sha256": sha256_file(source_model),
        "output_model": str(output_model.resolve()),
        "output_model_sha256": sha256_file(output_model),
        "opensim_version": osim.GetVersionAndDate(),
        "sides": reports,
        "imu_placer_rerun": False,
        "placement_changed": False,
        "weights_changed": False,
        "other_joint_frames_changed": False,
        "coordinate_ranges_changed": False,
        "pro_sup_reference_policy": pro_sup_reference,
        "axial_zero_scientifically_observed": False,
        "axial_zero_owner": (
            "official default" if pro_sup_reference == "official_default" else
            "diagnostic centre of unchanged official ROM; no anatomical-truth claim"
        ),
        "frozen_a_changed": False,
        "wall_s": time.monotonic() - started,
    }
    output_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def _write_orientation_table(
    output: Path, times: np.ndarray, quaternion_rows: list[list[str]]
) -> None:
    labels = [FRAME_BY_SEGMENT_RADIUS[segment] for segment in BODY_BY_SEGMENT]
    lines = [
        f"DataRate={1.0 / float(np.median(np.diff(times))):.17g}",
        "DataType=Quaternion",
        "version=3",
        "OpenSimVersion=4.6",
        "endheader",
        "time\t" + "\t".join(labels),
    ]
    lines.extend(
        f"{float(row_time):.17g}\t" + "\t".join(row)
        for row_time, row in zip(times, quaternion_rows, strict=True)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_official_ik(
    model_path: Path,
    orientation_path: Path,
    output_dir: Path,
    sensor_rotation: tuple[float, float, float],
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_dir / "opensim.log")
    table = osim.TimeSeriesTableQuaternion(str(orientation_path.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientations_file(str(orientation_path.resolve()))
    tool.set_sensor_to_opensim_rotations(_vec3(sensor_rotation))
    tool.set_time_range(0, float(times[0]))
    tool.set_time_range(1, float(times[-1]))
    tool.set_results_directory(str(output_dir.resolve()))
    tool.set_output_motion_file("official_ik.sto")
    tool.set_report_errors(True)
    weights = osim.OrientationWeightSet()
    for segment in BODY_BY_SEGMENT:
        weights.cloneAndAppend(
            osim.OrientationWeight(FRAME_BY_SEGMENT_RADIUS[segment], 1.0)
        )
    tool.set_orientation_weights(weights)
    tool.set_accuracy(1e-7)
    setup = output_dir / "imu_ik_setup.xml"
    tool.printToXML(str(setup.resolve()))
    if not tool.run(False):
        raise RuntimeError("official radioulnar IK returned false")
    motion = output_dir / "official_ik.sto"
    errors = output_dir / "official_ik.sto_orientationErrors.sto"
    if not motion.is_file() or not errors.is_file():
        raise RuntimeError("official radioulnar IK outputs are missing")
    return {
        "engine": "official OpenSim IMUInverseKinematicsTool",
        "model_sha256": sha256_file(model_path),
        "input_sha256": sha256_file(orientation_path),
        "motion_sha256": sha256_file(motion),
        "errors_sha256": sha256_file(errors),
        "equal_orientation_weights": 1.0,
        "accuracy": 1e-7,
        "orientation_errors": orientation_error_summary(errors),
        "wall_s": time.monotonic() - started,
    }


def _motion_columns(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    import opensim as osim

    table = osim.TimeSeriesTable(str(path.resolve()))
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    columns = {
        label: np.deg2rad(
            np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)
        )
        for label in table.getColumnLabels()
    }
    return times, columns


def run_mechanism_fixture(model_path: Path, output_dir: Path) -> dict[str, object]:
    """Official FK-to-IK closure for elbow and radioulnar coordinates."""

    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_opensim_log(output_dir / "generate_opensim.log")
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.updCoordinateSet()
    row_specs = []
    quaternion_rows = []
    for side in ("l", "r"):
        elbow_name = f"elbow_flex_{side}"
        pro_name = f"pro_sup_{side}"
        elbow = coordinates.get(elbow_name)
        pro = coordinates.get(pro_name)
        elbow_mid = 0.5 * (elbow.getRangeMin() + elbow.getRangeMax())
        pro_mid = 0.5 * (pro.getRangeMin() + pro.getRangeMax())
        deltas = (
            (-0.1, 0.0),
            (+0.1, 0.0),
            (0.0, -0.1),
            (0.0, +0.1),
            (-0.1, -0.1),
            (+0.1, +0.1),
        )
        for elbow_delta, pro_delta in deltas:
            state = model.initSystem()
            elbow.setValue(state, elbow_mid + elbow_delta, False)
            pro.setValue(state, pro_mid + pro_delta, False)
            model.assemble(state)
            model.realizePosition(state)
            row = {
                "side": side,
                "elbow_name": elbow_name,
                "pro_sup_name": pro_name,
                "elbow_mid_rad": float(elbow_mid),
                "pro_sup_mid_rad": float(pro_mid),
                "elbow_delta_rad": elbow_delta,
                "pro_sup_delta_rad": pro_delta,
                "assembled_elbow_rad": float(elbow.getValue(state)),
                "assembled_pro_sup_rad": float(pro.getValue(state)),
                "shoulder_truth_rad": {
                    name: float(coordinates.get(name).getValue(state))
                    for name in (
                        f"arm_flex_{side}",
                        f"arm_add_{side}",
                        f"arm_rot_{side}",
                    )
                },
            }
            ulna = model.getBodySet().get(f"ulna_{side}")
            radius = model.getBodySet().get(f"radius_{side}")
            relative = _matrix(ulna.getRotationInGround(state)).T @ _matrix(
                radius.getRotationInGround(state)
            )
            row["ulna_from_radius_rotation"] = relative.tolist()
            row["ulna_radius_geodesic_rad"] = _rotation_angle(
                np.eye(3), relative
            )
            quaternions = []
            for segment in BODY_BY_SEGMENT:
                frame = model.getComponent(_frame_path(segment))
                quaternion = frame.getRotationInGround(
                    state
                ).convertRotationToQuaternion()
                quaternions.append(
                    ",".join(f"{quaternion.get(index):.17g}" for index in range(4))
                )
            row_specs.append(row)
            quaternion_rows.append(quaternions)

    orientation_path = output_dir / "model_generated_elbow_pro_sup.sto"
    times = np.arange(len(row_specs), dtype=float) * 0.01
    _write_orientation_table(orientation_path, times, quaternion_rows)
    official = _run_official_ik(
        model_path, orientation_path, output_dir, (0.0, 0.0, 0.0)
    )
    _, columns = _motion_columns(output_dir / "official_ik.sto")
    for index, row in enumerate(row_specs):
        row["ik_elbow_rad"] = float(columns[row["elbow_name"]][index])
        row["ik_pro_sup_rad"] = float(columns[row["pro_sup_name"]][index])
        row["elbow_error_rad"] = abs(
            row["ik_elbow_rad"] - row["assembled_elbow_rad"]
        )
        row["pro_sup_error_rad"] = abs(
            row["ik_pro_sup_rad"] - row["assembled_pro_sup_rad"]
        )
        row["shoulder_compensation_rad"] = {
            name: abs(float(columns[name][index]) - truth)
            for name, truth in row["shoulder_truth_rad"].items()
        }

    max_coordinate_error = max(
        max(row["elbow_error_rad"], row["pro_sup_error_rad"])
        for row in row_specs
    )
    max_shoulder_compensation = max(
        max(row["shoulder_compensation_rad"].values()) for row in row_specs
    )
    # arm_add=+pi/2 is the official T-pose and an Euler-chart singularity, so
    # component-wise shoulder-coordinate differences are not a physical
    # compensation metric.  The tracked humerus frame's SO(3) error is the
    # observable physical owner and uses the same unchanged 0.02-rad bound.
    max_humerus_orientation_error = max(
        official["orientation_errors"]["sensors"][name]["max_rad"]
        for name in ("humerus_l_imu", "humerus_r_imu")
    )
    result = {
        "schema": "biospur-c2-radioulnar-mechanism-fixture-v1",
        "rows": len(row_specs),
        "official_ik_calls": 1,
        "coordinate_midpoint_plus_minus_rad": 0.1,
        "coordinate_ranges_unchanged": True,
        "motion_source_units": "degree; converted to radians exactly once",
        "orientation_error_source_units": "radian; no conversion",
        "fixture_tolerance_rad": FIXTURE_TOLERANCE_RAD,
        "max_coordinate_error_rad": max_coordinate_error,
        "max_shoulder_compensation_rad": max_shoulder_compensation,
        "shoulder_coordinate_delta_is_diagnostic_only": True,
        "shoulder_coordinate_chart_boundary": "arm_add_l/r=+pi/2 official T-pose",
        "max_physical_humerus_orientation_error_rad": max_humerus_orientation_error,
        "official_orientation_max_rad": official["orientation_errors"][
            "overall_max_rad"
        ],
        "passed": bool(
            max_coordinate_error <= FIXTURE_TOLERANCE_RAD
            and max_humerus_orientation_error <= FIXTURE_TOLERANCE_RAD
            and official["orientation_errors"]["overall_max_rad"]
            <= FIXTURE_TOLERANCE_RAD
        ),
        "rows_detail": row_specs,
        "official": official,
        "wall_s": time.monotonic() - started,
    }
    (output_dir / "MECHANISM_FIXTURE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def _write_frozen_episode(episode, output: Path) -> dict[str, object]:
    reference = episode.segments["pelvis"].time_root_s
    elapsed = reference - reference[0]
    rows = []
    for row_index in range(episode.frame_count):
        row = []
        for segment in BODY_BY_SEGMENT:
            quaternion = episode.segments[segment].quat_world_segment_wxyz[
                row_index
            ]
            row.append(",".join(f"{float(value):.17g}" for value in quaternion))
        rows.append(row)
    _write_orientation_table(output, elapsed, rows)
    return {
        "episode": episode.key,
        "rows": episode.frame_count,
        "labels": [FRAME_BY_SEGMENT_RADIUS[s] for s in BODY_BY_SEGMENT],
        "quaternion_values_changed": False,
        "time_origin_shift_only": True,
        "sha256": sha256_file(output),
    }


def run_episode(
    workspace: Path, model_path: Path, output_root: Path, key: str
) -> dict[str, object]:
    episode = load_frozen_c2_3a(workspace=workspace).episodes[key]
    output_dir = output_root / "episodes" / DISPLAY_LABEL[key]
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / "frozen_orientations.sto"
    input_manifest = _write_frozen_episode(episode, input_path)
    official = _run_official_ik(
        model_path, input_path, output_dir, SENSOR_TO_OPENSIM_XYZ_RAD
    )
    result = {
        "episode": key,
        "input": input_manifest,
        **official,
    }
    (output_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def _table_columns_raw(path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    import opensim as osim

    table = osim.TimeSeriesTable(str(path.resolve()))
    return np.asarray(table.getIndependentColumn(), dtype=float), {
        label: np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)
        for label in table.getColumnLabels()
    }


def _summary(values: np.ndarray) -> dict[str, float]:
    return {
        "mean_rad": float(np.mean(values)),
        "median_rad": float(np.median(values)),
        "p95_rad": float(np.quantile(values, 0.95)),
        "max_rad": float(np.max(values)),
    }


def _phase_metrics(
    errors_path: Path, motion_path: Path, side: str, model_path: Path
) -> dict[str, object]:
    import opensim as osim

    times, errors = _table_columns_raw(errors_path)
    motion_times, motion = _motion_columns(motion_path)
    if not np.array_equal(times, motion_times):
        raise ValueError("motion/error time rows differ")
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.getCoordinateSet()
    coordinate_names = (
        f"elbow_flex_{side}",
        f"pro_sup_{side}",
        f"arm_flex_{side}",
        f"arm_add_{side}",
        f"arm_rot_{side}",
    )
    result = {}
    for phase, (start, stop) in PHASES_S.items():
        mask = (times >= start) & (times < stop)
        sensor_names = (f"humerus_{side}_imu", f"radius_{side}_imu")
        phase_errors = np.concatenate([errors[name][mask] for name in sensor_names])
        coordinate_rows = {}
        for name in coordinate_names:
            coordinate = coordinates.get(name)
            values = motion[name][mask]
            lo = float(coordinate.getRangeMin())
            hi = float(coordinate.getRangeMax())
            coordinate_rows[name] = {
                "range_rad": [lo, hi],
                "min_rad": float(np.min(values)),
                "max_rad": float(np.max(values)),
                "near_1deg_lower_rows": int(
                    np.count_nonzero(values <= lo + math.radians(1.0))
                ),
                "near_1deg_upper_rows": int(
                    np.count_nonzero(values >= hi - math.radians(1.0))
                ),
            }
        result[phase] = {
            "interval_s": [start, stop],
            "rows": int(np.count_nonzero(mask)),
            "sensors": {name: _summary(errors[name][mask]) for name in sensor_names},
            "target_pair": _summary(phase_errors),
            "coordinates": coordinate_rows,
        }
    return result


def _baseline_phase_metrics(workspace: Path, key: str) -> dict[str, object]:
    side = "l" if key == "06" else "r"
    root = workspace / BASE_EPISODE_DIR[key]
    times, errors = _table_columns_raw(
        root / "official_ik.sto_orientationErrors.sto"
    )
    motion_times, motion = _motion_columns(root / "official_ik.sto")
    if not np.array_equal(times, motion_times):
        raise ValueError("baseline motion/error time rows differ")
    result = {}
    for phase, (start, stop) in PHASES_S.items():
        mask = (times >= start) & (times < stop)
        sensors = (f"humerus_{side}_imu", f"ulna_{side}_imu")
        target = np.concatenate([errors[name][mask] for name in sensors])
        result[phase] = {
            "interval_s": [start, stop],
            "rows": int(np.count_nonzero(mask)),
            "sensors": {
                "upper_arm": _summary(errors[sensors[0]][mask]),
                "forearm": _summary(errors[sensors[1]][mask]),
            },
            "target_pair": _summary(target),
            "coordinates": {
                f"elbow_flex_{side}": {
                    "min_rad": float(np.min(motion[f"elbow_flex_{side}"][mask])),
                    "max_rad": float(np.max(motion[f"elbow_flex_{side}"][mask])),
                },
                f"pro_sup_{side}": {
                    "min_rad": float(np.min(motion[f"pro_sup_{side}"][mask])),
                    "max_rad": float(np.max(motion[f"pro_sup_{side}"][mask])),
                    "locked": True,
                },
            },
        }
    return result


def analyze_episode(
    workspace: Path, model_path: Path, output_root: Path, key: str
) -> dict[str, object]:
    frozen = load_frozen_c2_3a(workspace=workspace)
    episode = frozen.episodes[key]
    output_dir = output_root / "episodes" / DISPLAY_LABEL[key]
    _, points, rom = replay_model(
        model_path,
        output_dir / "official_ik.sto",
        output_dir / "analysis_opensim.log",
    )
    render = render_episode(
        frozen,
        episode,
        key,
        DISPLAY_LABEL[key],
        points,
        output_root / "rendering" / f"{DISPLAY_LABEL[key]}_ab_front_side_top.png",
    )
    changes = []
    for row, b_points in enumerate(points):
        direct = a_points(episode, frozen.geometry, row)
        changes.extend(
            float(np.linalg.norm(direct[name] - b_points[name])) for name in direct
        )
    side = "l" if key == "06" else "r"
    result = {
        "episode": key,
        "new": _phase_metrics(
            output_dir / "official_ik.sto_orientationErrors.sto",
            output_dir / "official_ik.sto",
            side,
            model_path,
        ),
        "baseline": _baseline_phase_metrics(workspace, key),
        "rom": rom,
        "point_change_m": {
            "mean": float(np.mean(changes)),
            "p95": float(np.quantile(changes, 0.95)),
            "max": float(np.max(changes)),
        },
        "render": render,
    }
    (output_dir / "PHASE_ANALYSIS.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("configure", "fixture", "episode", "analyze")
    )
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--source-model", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key", choices=("06", "07"))
    parser.add_argument(
        "--pro-sup-reference",
        choices=("official_default", "official_range_midpoint"),
        default="official_default",
    )
    args = parser.parse_args()
    if args.command == "configure":
        result = configure_radius_model(
            args.workspace,
            args.source_model,
            args.model,
            args.output / "MODEL_OWNERSHIP.json",
            args.pro_sup_reference,
        )
    elif args.command == "fixture":
        result = run_mechanism_fixture(args.model, args.output / "fixture")
    elif args.command == "episode":
        result = run_episode(args.workspace, args.model, args.output, args.key)
    else:
        result = analyze_episode(args.workspace, args.model, args.output, args.key)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
