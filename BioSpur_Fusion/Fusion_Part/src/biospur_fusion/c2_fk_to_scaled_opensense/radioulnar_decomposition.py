"""Fixed-sample local kinematic decomposition for rejected radioulnar pilots.

No model or IK output is changed.  At twelve preregistered time samples this
uses official OpenSim FK finite perturbations to express the measured relative
IMU increment in the instantaneous elbow, radioulnar, and shoulder-rotation
directions.  The small linear projections are report-only diagnostics, not an
estimator or candidate-selection objective.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_3b_official_opensense.adapter import COUPLED_COORDINATES
from biospur_fusion.c2_fk_to_opensim_ik.adapter import _quat_wxyz_matrix

from .pipeline import C2_FROM_OPENSIM, configure_opensim_log, sha256_file
from .radioulnar_fix import _matrix, _rotation


C2_TO_OPENSIM = C2_FROM_OPENSIM.T
SAMPLE_TIMES_S = (1.0, 7.5, 14.0, 16.0, 22.5, 29.0)
PERTURBATION_RAD = 1e-3


def _rotvec(matrix: np.ndarray) -> np.ndarray:
    value = _rotation(matrix).convertRotationToAngleAxis()
    return np.asarray([value.get(1), value.get(2), value.get(3)]) * value.get(0)


def _relative_frames(model, state, parent_path: str, child_path: str) -> np.ndarray:
    parent = model.getComponent(parent_path)
    child = model.getComponent(child_path)
    return _matrix(parent.getRotationInGround(state)).T @ _matrix(
        child.getRotationInGround(state)
    )


def _motion_columns(model, path: Path) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """Read an OpenSim motion table and convert rotational columns once.

    OpenSim writes this motion file with ``inDegrees=yes``.  Translational
    coordinates remain in metres; only coordinates whose official MotionType
    is Rotational are converted to radians here.
    """

    import opensim as osim

    table = osim.TimeSeriesTable(str(path.resolve()))
    if table.getTableMetaDataAsString("inDegrees").strip().lower() != "yes":
        raise RuntimeError("expected official motion table with inDegrees=yes")
    coordinates = model.updCoordinateSet()
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    columns = {}
    for label in table.getColumnLabels():
        values = np.asarray(table.getDependentColumn(label).to_numpy(), dtype=float)
        coordinate = coordinates.get(label)
        columns[label] = (
            np.deg2rad(values)
            if int(coordinate.getMotionType()) == 1
            else values
        )
    return times, columns


def _set_motion_state(model, columns, row: int):
    state = model.initSystem()
    coordinates = model.updCoordinateSet()
    for name, values in columns.items():
        coordinate = coordinates.get(name)
        if coordinate.getLocked(state) or name in COUPLED_COORDINATES:
            continue
        # _motion_columns() already owns the single degrees-to-radians
        # conversion.  A second conversion here invalidated attempt 001.
        coordinate.setValue(state, float(values[row]), False)
    model.assemble(state)
    model.realizePosition(state)
    return state


def _sensitivity(
    model,
    columns,
    row: int,
    parent_path: str,
    child_path: str,
    coordinate_name: str,
):
    base_state = _set_motion_state(model, columns, row)
    coordinate = model.updCoordinateSet().get(coordinate_name)
    base_value = float(coordinate.getValue(base_state))
    lo, hi = float(coordinate.getRangeMin()), float(coordinate.getRangeMax())
    base_relative = _relative_frames(model, base_state, parent_path, child_path)
    if base_value + PERTURBATION_RAD <= hi:
        changed_state = _set_motion_state(model, columns, row)
        coordinate.setValue(changed_state, base_value + PERTURBATION_RAD, False)
        model.realizePosition(changed_state)
        relative_vector = _rotvec(
            base_relative.T
            @ _relative_frames(model, changed_state, parent_path, child_path)
        ) / PERTURBATION_RAD
        scheme = "forward"
    elif base_value - PERTURBATION_RAD >= lo:
        changed_state = _set_motion_state(model, columns, row)
        coordinate.setValue(changed_state, base_value - PERTURBATION_RAD, False)
        model.realizePosition(changed_state)
        relative_vector = _rotvec(
            _relative_frames(model, changed_state, parent_path, child_path).T
            @ base_relative
        ) / PERTURBATION_RAD
        scheme = "backward"
    else:
        raise RuntimeError(f"no admissible finite difference for {coordinate_name}")
    return {
        "relative_frame_vector_per_rad": relative_vector,
        "scheme": scheme,
        "value_rad": base_value,
        "range_rad": [lo, hi],
        "distance_to_lower_rad": base_value - lo,
        "distance_to_upper_rad": hi - base_value,
}


def _frozen_relative(parent_rows: np.ndarray, child_rows: np.ndarray) -> list[np.ndarray]:
    return [
        (C2_TO_OPENSIM @ _quat_wxyz_matrix(parent)).T
        @ (C2_TO_OPENSIM @ _quat_wxyz_matrix(child))
        for parent, child in zip(parent_rows, child_rows, strict=True)
    ]


def _direct_frozen_line(
    relative: list[np.ndarray], times: np.ndarray, phase_stop_s: float = 15.0
) -> dict[str, object]:
    """Fit an undirected line directly to frozen right increments.

    This intentionally does not import the rejected functional mapping.  Both
    the data line and official coordinate sensitivities use the same
    parent-IMU to child-IMU relative-orientation right-increment chart.
    """

    use = np.flatnonzero(times[:-1] < phase_stop_s)
    increments = np.asarray(
        [_rotvec(relative[row].T @ relative[row + 1]) for row in use]
    )
    _, singular_values, vh = np.linalg.svd(increments, full_matrices=False)
    axis = vh[0]
    projections = increments @ axis
    residuals = increments - np.outer(projections, axis)
    energy = np.square(singular_values)
    tolerance = float(
        max(increments.shape) * np.finfo(float).eps * singular_values[0]
    )
    return {
        "owner": "direct frozen parent-to-child relative SO(3) right increments",
        "chart": "parent-IMU to child-IMU relative orientation; R_k^T R_(k+1)",
        "phase_rows": int(len(use)),
        "axis_undirected": axis.tolist(),
        "singular_values_rad": singular_values.tolist(),
        "numerical_rank": int(np.sum(singular_values > tolerance)),
        "rank_tolerance_rad": tolerance,
        "first_line_explained_energy_fraction": float(energy[0] / energy.sum()),
        "orthogonal_residual_p95_rad": float(
            np.quantile(np.linalg.norm(residuals, axis=1), 0.95)
        ),
        "signed_projection_min_rad": float(projections.min()),
        "signed_projection_max_rad": float(projections.max()),
        "absolute_axis_zero_observable": False,
    }


def _project(matrix: np.ndarray, measured: np.ndarray) -> dict[str, object]:
    column_norms = np.linalg.norm(matrix, axis=0)
    singular_values = np.linalg.svd(matrix, compute_uv=False)
    tolerance = float(max(matrix.shape) * np.finfo(float).eps * singular_values[0])
    rank = int(np.sum(singular_values > tolerance))
    full_rank = rank == matrix.shape[1]
    nondegenerate_columns = bool(np.all(column_norms > np.sqrt(np.finfo(float).eps)))
    report = {
        "column_norms_per_rad": column_norms.tolist(),
        "singular_values_per_rad": singular_values.tolist(),
        "rank": rank,
        "columns": int(matrix.shape[1]),
        "rank_tolerance_per_rad": tolerance,
        "full_column_rank": full_rank,
        "nondegenerate_columns": nondegenerate_columns,
        "condition": (
            float(singular_values[0] / singular_values[-1])
            if full_rank and singular_values[-1] > 0
            else None
        ),
    }
    if not full_rank or not nondegenerate_columns:
        return {
            **report,
            "coefficients_rad_per_step": None,
            "projection_rad": None,
            "residual_rad": None,
            "residual_norm_rad": None,
            "coefficient_status": "REFUSED_DEGENERATE",
        }
    coefficients, _, _, _ = np.linalg.lstsq(matrix, measured, rcond=None)
    projection = matrix @ coefficients
    residual = measured - projection
    return {
        **report,
        "coefficients_rad_per_step": coefficients.tolist(),
        "projection_rad": projection.tolist(),
        "residual_rad": residual.tolist(),
        "residual_norm_rad": float(np.linalg.norm(residual)),
        "coefficient_status": "REPORT_ONLY_FULL_RANK",
    }


def run(
    workspace: Path,
    model_path: Path,
    episode_root: Path,
    supersedes: Path,
    output: Path,
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output.parent / "decomposition_opensim.log")
    model = osim.Model(str(model_path.resolve()))
    frozen = load_frozen_c2_3a(workspace=workspace)
    episodes = {}
    for key, side, label in (
        ("06", "l", "06_elbow_left"),
        ("07", "r", "07_elbow_right"),
    ):
        motion_path = episode_root / "episodes" / label / "official_ik.sto"
        times, columns = _motion_columns(model, motion_path)
        episode = frozen.episodes[key]
        torso_rows = episode.segments["torso"].quat_world_segment_wxyz
        humerus_rows = episode.segments[
            "upper_arm_left" if side == "l" else "upper_arm_right"
        ].quat_world_segment_wxyz
        forearm_rows = episode.segments[
            "forearm_left" if side == "l" else "forearm_right"
        ].quat_world_segment_wxyz
        measured_shoulder_relative = _frozen_relative(torso_rows, humerus_rows)
        measured_distal_relative = _frozen_relative(humerus_rows, forearm_rows)
        direct_line = _direct_frozen_line(measured_distal_relative, times)
        samples = []
        for target_time in SAMPLE_TIMES_S:
            row = int(np.argmin(np.abs(times - target_time)))
            if row >= len(times) - 1:
                raise RuntimeError("sample has no forward measured increment")
            shoulder_measured = _rotvec(
                measured_shoulder_relative[row].T
                @ measured_shoulder_relative[row + 1]
            )
            distal_measured = _rotvec(
                measured_distal_relative[row].T
                @ measured_distal_relative[row + 1]
            )
            shoulder_paths = (
                "/bodyset/torso/torso_imu",
                f"/bodyset/humerus_{side}/humerus_{side}_imu",
            )
            distal_paths = (
                f"/bodyset/humerus_{side}/humerus_{side}_imu",
                f"/bodyset/radius_{side}/radius_{side}_imu",
            )
            shoulder_sensitivities = {}
            for coordinate_name in (
                f"arm_flex_{side}",
                f"arm_add_{side}",
                f"arm_rot_{side}",
            ):
                shoulder_sensitivities[coordinate_name] = _sensitivity(
                    model, columns, row, *shoulder_paths, coordinate_name
                )
            distal_sensitivities = {}
            for coordinate_name in (
                f"elbow_flex_{side}",
                f"pro_sup_{side}",
            ):
                distal_sensitivities[coordinate_name] = _sensitivity(
                    model, columns, row, *distal_paths, coordinate_name
                )
            shoulder_matrix = np.column_stack(
                [
                    shoulder_sensitivities[name]["relative_frame_vector_per_rad"]
                    for name in (
                        f"arm_flex_{side}",
                        f"arm_add_{side}",
                        f"arm_rot_{side}",
                    )
                ]
            )
            distal_matrix = np.column_stack(
                [
                    distal_sensitivities[name]["relative_frame_vector_per_rad"]
                    for name in (f"elbow_flex_{side}", f"pro_sup_{side}")
                ]
            )
            official_elbow = distal_matrix[:, 0] / np.linalg.norm(distal_matrix[:, 0])
            direct_axis = np.asarray(direct_line["axis_undirected"])

            def serialized(values):
                return {
                    name: {
                        **{
                            field: value
                            for field, value in entry.items()
                            if not isinstance(value, np.ndarray)
                        },
                        "relative_frame_vector_per_rad": entry[
                            "relative_frame_vector_per_rad"
                        ].tolist(),
                    }
                    for name, entry in values.items()
                }

            samples.append(
                {
                    "target_time_s": target_time,
                    "actual_time_s": float(times[row]),
                    "row": row,
                    "phase": "flexion" if target_time < 15.0 else "pronation_supination",
                    "shoulder_chain": {
                        "frames": list(shoulder_paths),
                        "measured_increment_rotvec_rad": shoulder_measured.tolist(),
                        "measured_increment_norm_rad": float(
                            np.linalg.norm(shoulder_measured)
                        ),
                        "sensitivities": serialized(shoulder_sensitivities),
                        "projection": _project(shoulder_matrix, shoulder_measured),
                    },
                    "distal_chain": {
                        "frames": list(distal_paths),
                        "measured_increment_rotvec_rad": distal_measured.tolist(),
                        "measured_increment_norm_rad": float(
                            np.linalg.norm(distal_measured)
                        ),
                        "sensitivities": serialized(distal_sensitivities),
                        "projection": _project(distal_matrix, distal_measured),
                        "direct_frozen_line_vs_official_elbow_abs_dot": float(
                            abs(np.dot(direct_axis, official_elbow))
                        ),
                    },
                    "actual_coordinate_delta_rad": {
                        name: float(values[row + 1] - values[row])
                        for name, values in columns.items()
                        if name
                        in {
                            f"arm_flex_{side}",
                            f"arm_add_{side}",
                            f"arm_rot_{side}",
                            f"elbow_flex_{side}",
                            f"pro_sup_{side}",
                        }
                    },
                }
            )
        episodes[key] = {
            "direct_frozen_first_phase_distal_line": direct_line,
            "samples": samples,
        }
    result = {
        "schema": "biospur-c2-radioulnar-instantaneous-decomposition-v2",
        "supersedes_noncanonical": {
            "path": str(supersedes.resolve()),
            "sha256": sha256_file(supersedes),
            "reasons": [
                "motion coordinates were converted degrees-to-radians twice before FK",
                "arm_rot was incorrectly projected into a humerus-to-radius chain invariant to shoulder rotation",
            ],
        },
        "official_ik_calls": 0,
        "official_fk_state_evaluations": 2 * len(SAMPLE_TIMES_S) * 5 * 2,
        "sample_times_s": list(SAMPLE_TIMES_S),
        "finite_difference_rad": PERTURBATION_RAD,
        "motion_units": {
            "official_header": "inDegrees=yes",
            "rotational_coordinates": "converted degrees to radians exactly once",
            "translational_coordinates": "metres; no angular conversion",
        },
        "custom_optimizer": False,
        "linear_projection_is_report_only": True,
        "chain_separation": {
            "shoulder": "torso_imu to humerus_imu; arm_flex/arm_add/arm_rot only",
            "distal": "humerus_imu to radius_imu; elbow_flex/pro_sup only",
            "shoulder_coordinates_are_forbidden_in_distal_projection": True,
        },
        "model": str(model_path.resolve()),
        "model_sha256": sha256_file(model_path),
        "episodes": episodes,
        "wall_s": time.monotonic() - started,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--episode-root", type=Path, required=True)
    parser.add_argument("--supersedes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run(
        args.workspace,
        args.model,
        args.episode_root,
        args.supersedes,
        args.output,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
