"""Report-only radioulnar sign/zero and corrected-control comparison.

This module makes no IK call and fits no parameter.  It compares already
generated official outputs, uses official Rajagopal FK for signed coordinate
sensitivity, and projects incremental measured relative rotations onto that
precomputed physical axis.  The integrated twist therefore has an arbitrary
zero, which is reported rather than guessed.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix

from .pipeline import C2_FROM_OPENSIM, configure_opensim_log, sha256_file
from .radioulnar_fix import _matrix, _rotation, _table_columns_raw, _motion_columns


C2_TO_OPENSIM = C2_FROM_OPENSIM.T
PHASES = {"flexion": (0.0, 15.0), "pronation_supination": (15.0, 30.0)}


def _summary(values: np.ndarray) -> dict[str, float]:
    return {
        "mean_rad": float(np.mean(values)),
        "median_rad": float(np.median(values)),
        "p95_rad": float(np.quantile(values, 0.95)),
        "max_rad": float(np.max(values)),
    }


def _rotvec(matrix: np.ndarray) -> np.ndarray:
    angle_axis = _rotation(matrix).convertRotationToAngleAxis()
    return np.asarray(
        [angle_axis.get(1), angle_axis.get(2), angle_axis.get(3)], dtype=float
    ) * float(angle_axis.get(0))


def _frame_offset(model, state, body_name: str, frame_name: str) -> np.ndarray:
    body = model.getBodySet().get(body_name)
    frame = model.getComponent(f"/bodyset/{body_name}/{frame_name}")
    return _matrix(body.getRotationInGround(state)).T @ _matrix(
        frame.getRotationInGround(state)
    )


def _model_signed_sensitivity(model_path: Path, log_path: Path) -> dict[str, object]:
    import opensim as osim

    configure_opensim_log(log_path)
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.updCoordinateSet()
    result = {}
    for side in ("l", "r"):
        elbow = coordinates.get(f"elbow_flex_{side}")
        pro = coordinates.get(f"pro_sup_{side}")
        q0 = 0.5 * (pro.getRangeMin() + pro.getRangeMax())
        relative = {}
        frame_response = {}
        for delta in (-0.1, 0.0, 0.1):
            state = model.initSystem()
            elbow.setValue(state, math.pi / 2.0, False)
            pro.setValue(state, q0 + delta, False)
            model.assemble(state)
            model.realizePosition(state)
            humerus = model.getBodySet().get(f"humerus_{side}")
            radius = model.getBodySet().get(f"radius_{side}")
            h_frame = model.getComponent(
                f"/bodyset/humerus_{side}/humerus_{side}_imu"
            )
            r_frame = model.getComponent(
                f"/bodyset/radius_{side}/radius_{side}_imu"
            )
            relative[delta] = _matrix(humerus.getRotationInGround(state)).T @ _matrix(
                radius.getRotationInGround(state)
            )
            frame_response[delta] = _matrix(
                h_frame.getRotationInGround(state)
            ).T @ _matrix(r_frame.getRotationInGround(state))
        body_change = _rotvec(relative[-0.1].T @ relative[0.1])
        frame_change = _rotvec(frame_response[-0.1].T @ frame_response[0.1])
        result[side] = {
            "joint": f"radioulnar_{side}",
            "coordinate": f"pro_sup_{side}",
            "range_rad": [float(pro.getRangeMin()), float(pro.getRangeMax())],
            "q0_rad": float(q0),
            "fixture_rad": [float(q0 - 0.1), float(q0), float(q0 + 0.1)],
            "elbow_fixture_rad": math.pi / 2.0,
            "positive_body_relative_rotvec_for_plus_0p2_rad": body_change.tolist(),
            "positive_imu_frame_relative_rotvec_for_plus_0p2_rad": frame_change.tolist(),
            "positive_imu_frame_axis": (
                frame_change / np.linalg.norm(frame_change)
            ).tolist(),
            "response_angle_rad": float(np.linalg.norm(frame_change)),
        }
    return result


def _measured_relative_twist(
    model_path: Path, episode, side: str, axis: np.ndarray
) -> dict[str, object]:
    h_rows = episode.segments[
        "upper_arm_left" if side == "l" else "upper_arm_right"
    ].quat_world_segment_wxyz
    r_rows = episode.segments[
        "forearm_left" if side == "l" else "forearm_right"
    ].quat_world_segment_wxyz
    relative = []
    for h_quat, r_quat in zip(h_rows, r_rows, strict=True):
        h_imu = C2_TO_OPENSIM @ _quat_wxyz_matrix(h_quat)
        r_imu = C2_TO_OPENSIM @ _quat_wxyz_matrix(r_quat)
        # The official sensitivity axis below is expressed in the moving
        # radius IMU frame, so the measured increment must remain in the same
        # sensor-relative chart.  Converting to body frames here would mix
        # coordinate systems and spuriously project elbow motion onto pro_sup.
        relative.append(h_imu.T @ r_imu)
    relative = np.asarray(relative)
    times = episode.segments["pelvis"].time_root_s
    elapsed = times - times[0]
    result = {}
    for name, (start, stop) in PHASES.items():
        indices = np.flatnonzero((elapsed >= start) & (elapsed < stop))
        axial_steps = []
        nonaxial_steps = []
        cumulative = [0.0]
        for previous, current in zip(indices[:-1], indices[1:], strict=True):
            vector = _rotvec(relative[previous].T @ relative[current])
            axial = float(np.dot(vector, axis))
            axial_steps.append(axial)
            nonaxial_steps.append(float(np.linalg.norm(vector - axial * axis)))
            cumulative.append(cumulative[-1] + axial)
        cumulative_array = np.asarray(cumulative)
        result[name] = {
            "interval_s": [start, stop],
            "rows": int(len(indices)),
            "signed_increment_rad": {
                "min": float(np.min(axial_steps)),
                "max": float(np.max(axial_steps)),
                "median": float(np.median(axial_steps)),
            },
            "integrated_relative_twist_rad_arbitrary_zero": {
                "min": float(np.min(cumulative_array)),
                "max": float(np.max(cumulative_array)),
                "peak_to_peak": float(np.ptp(cumulative_array)),
                "end": float(cumulative_array[-1]),
            },
            "nonaxial_increment_rad": {
                "median": float(np.median(nonaxial_steps)),
                "p95": float(np.quantile(nonaxial_steps, 0.95)),
                "max": float(np.max(nonaxial_steps)),
            },
        }
    return {
        "equation": (
            "R_HI=R_GI_h^T R_GI_f; "
            "dtheta=AngleAxis(R_HI[k-1]^T R_HI[k]) dot a_prosup_IMU"
        ),
        "absolute_axial_zero_observable": False,
        "reason": "02 T-pose orientation placement absorbs any constant radioulnar axial coordinate into R_BI",
        "phases": result,
    }


def _phase_output(
    errors_path: Path,
    motion_path: Path,
    side: str,
    forearm_label: str,
    model_path: Path,
) -> dict[str, object]:
    import opensim as osim

    times, errors = _table_columns_raw(errors_path)
    motion_times, motion = _motion_columns(motion_path)
    if not np.array_equal(times, motion_times):
        raise RuntimeError("motion/error timelines differ")
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.getCoordinateSet()
    result = {}
    for name, (start, stop) in PHASES.items():
        mask = (times >= start) & (times < stop)
        sensors = [f"humerus_{side}_imu", forearm_label]
        all_target = np.concatenate([errors[sensor][mask] for sensor in sensors])
        coord_result = {}
        for coordinate_name in (f"elbow_flex_{side}", f"pro_sup_{side}"):
            coordinate = coordinates.get(coordinate_name)
            values = motion[coordinate_name][mask]
            lo, hi = float(coordinate.getRangeMin()), float(coordinate.getRangeMax())
            coord_result[coordinate_name] = {
                "min_rad": float(np.min(values)),
                "max_rad": float(np.max(values)),
                "near_1deg_lower_rows": int(
                    np.count_nonzero(values <= lo + math.radians(1.0))
                ),
                "near_1deg_upper_rows": int(
                    np.count_nonzero(values >= hi - math.radians(1.0))
                ),
                "range_rad": [lo, hi],
            }
        result[name] = {
            "target_pair": _summary(all_target),
            "sensors": {sensor: _summary(errors[sensor][mask]) for sensor in sensors},
            "all_sensors": {
                sensor: _summary(values[mask]) for sensor, values in errors.items()
            },
            "coordinates": coord_result,
        }
    return result


def run(
    workspace: Path,
    control_model: Path,
    radius_model: Path,
    control_root: Path,
    radius_root: Path,
    output: Path,
) -> dict[str, object]:
    started = time.monotonic()
    frozen = load_frozen_c2_3a(workspace=workspace)
    sensitivity = _model_signed_sensitivity(
        radius_model, output.parent / "signed_sensitivity_opensim.log"
    )
    episodes = {}
    for key, side in (("06", "l"), ("07", "r")):
        control_dir = control_root / f"{key}_no_radius_control"
        radius_dir = radius_root / "episodes" / (
            "06_elbow_left" if key == "06" else "07_elbow_right"
        )
        control = _phase_output(
            control_dir / "official_ik.sto_orientationErrors.sto",
            control_dir / "official_ik.sto",
            side,
            f"ulna_{side}_imu",
            control_model,
        )
        radius = _phase_output(
            radius_dir / "official_ik.sto_orientationErrors.sto",
            radius_dir / "official_ik.sto",
            side,
            f"radius_{side}_imu",
            radius_model,
        )
        deltas = {}
        for phase in PHASES:
            deltas[phase] = {
                metric: float(radius[phase]["target_pair"][metric])
                - float(control[phase]["target_pair"][metric])
                for metric in ("mean_rad", "p95_rad", "max_rad")
            }
        axis = np.asarray(sensitivity[side]["positive_imu_frame_axis"])
        episodes[key] = {
            "control": control,
            "radius": radius,
            "radius_minus_control_target_pair": deltas,
            "measured_relative_twist": _measured_relative_twist(
                radius_model, frozen.episodes[key], side, axis
            ),
        }
    result = {
        "schema": "biospur-c2-radioulnar-causal-v2",
        "supersedes_analysis_attempt": (
            "RADIOULNAR_CAUSAL.json mixed body-relative increments with an "
            "IMU-frame sensitivity axis and is noncanonical"
        ),
        "official_ik_calls": 0,
        "custom_optimizer_or_fit": False,
        "control_model": str(control_model.resolve()),
        "control_model_sha256": sha256_file(control_model),
        "radius_model": str(radius_model.resolve()),
        "radius_model_sha256": sha256_file(radius_model),
        "old_comparison_classification": "DIAGNOSTIC_OLD_CONFOUNDED_BASELINE",
        "model_signed_sensitivity": sensitivity,
        "episodes": episodes,
        "wall_s": time.monotonic() - started,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--control-model", type=Path, required=True)
    parser.add_argument("--radius-model", type=Path, required=True)
    parser.add_argument("--control-root", type=Path, required=True)
    parser.add_argument("--radius-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.workspace,
        args.control_model,
        args.radius_model,
        args.control_root,
        args.radius_root,
        args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
