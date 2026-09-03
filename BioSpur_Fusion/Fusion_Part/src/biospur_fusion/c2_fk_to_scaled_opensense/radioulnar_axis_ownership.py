"""Read-only elbow-axis ownership audit in one OpenSim parent-body frame.

This module does not run IK or change a model.  It compares the sealed QMT /
Olsson axis, a fresh line fitted to frozen relative-orientation increments,
and the official Rajagopal elbow tangent after transporting all three into the
same humerus-body coordinates.
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

from .pipeline import configure_opensim_log, sha256_file
from .radioulnar_decomposition import PERTURBATION_RAD, _rotvec
from .radioulnar_fix import _matrix


FLEXION_WINDOW_S = (0.0, 15.0)
OFFICIAL_TANGENT_VALUES_RAD = (0.2, 1.309, 2.418)


def _angle_undirected(first: np.ndarray, second: np.ndarray) -> float:
    first = first / np.linalg.norm(first)
    second = second / np.linalg.norm(second)
    return math.acos(float(np.clip(abs(np.dot(first, second)), -1.0, 1.0)))


def _body_frame_transform(model, state, body_name: str, frame_name: str) -> np.ndarray:
    body = model.getBodySet().get(body_name)
    frame = model.getComponent(f"/bodyset/{body_name}/{frame_name}")
    return _matrix(body.getRotationInGround(state)).T @ _matrix(
        frame.getRotationInGround(state)
    )


def _body_relative(model, state, parent: str, child: str) -> np.ndarray:
    parent_body = model.getBodySet().get(parent)
    child_body = model.getBodySet().get(child)
    return _matrix(parent_body.getRotationInGround(state)).T @ _matrix(
        child_body.getRotationInGround(state)
    )


def _line_fit(
    relative: list[np.ndarray], relative_time_s: np.ndarray, window_s: tuple[float, float]
) -> dict[str, object]:
    start_s, stop_s = window_s
    rows = np.flatnonzero(
        (relative_time_s[:-1] >= start_s) & (relative_time_s[1:] <= stop_s)
    )
    increments = np.asarray(
        [_rotvec(relative[row + 1] @ relative[row].T) for row in rows]
    )
    _, singular_values, vh = np.linalg.svd(increments, full_matrices=False)
    axis = vh[0]
    projection = increments @ axis
    orthogonal = increments - np.outer(projection, axis)
    energy = np.square(singular_values)
    tolerance = float(
        max(increments.shape) * np.finfo(float).eps * singular_values[0]
    )
    block_count = 5
    block_rows = np.array_split(np.arange(len(rows)), block_count)
    leave_one_block_out_angles = []
    for block in block_rows:
        retained = np.delete(increments, block, axis=0)
        _, _, retained_vh = np.linalg.svd(retained, full_matrices=False)
        leave_one_block_out_angles.append(_angle_undirected(axis, retained_vh[0]))
    return {
        "window_s": [start_s, stop_s],
        "first_increment_row": int(rows[0]),
        "last_increment_row": int(rows[-1]),
        "increment_count": int(len(rows)),
        "increment_definition": "Log(R_parent_child[k+1] R_parent_child[k]^T)",
        "axis_parent_segment_undirected": axis.tolist(),
        "singular_values_rad": singular_values.tolist(),
        "numerical_rank": int(np.sum(singular_values > tolerance)),
        "rank_tolerance_rad": tolerance,
        "first_line_explained_energy_fraction": float(energy[0] / energy.sum()),
        "fit_weighting": "ordinary SVD; increments contribute by squared magnitude",
        "orthogonal_residual_mean_rad": float(
            np.mean(np.linalg.norm(orthogonal, axis=1))
        ),
        "orthogonal_residual_p95_rad": float(
            np.quantile(np.linalg.norm(orthogonal, axis=1), 0.95)
        ),
        "orthogonal_residual_max_rad": float(
            np.max(np.linalg.norm(orthogonal, axis=1))
        ),
        "signed_projection_min_rad": float(projection.min()),
        "signed_projection_max_rad": float(projection.max()),
        "leave_one_contiguous_block_out": {
            "block_count": block_count,
            "block_increment_counts": [int(len(block)) for block in block_rows],
            "axis_angle_rad": leave_one_block_out_angles,
            "maximum_axis_angle_rad": float(max(leave_one_block_out_angles)),
        },
    }


def _official_elbow_tangents(model, side: str) -> dict[str, object]:
    name = f"elbow_flex_{side}"
    coordinate = model.updCoordinateSet().get(name)
    lo = float(coordinate.getRangeMin())
    hi = float(coordinate.getRangeMax())
    rows = []
    for requested in OFFICIAL_TANGENT_VALUES_RAD:
        value = float(np.clip(requested, lo + 0.1, hi - 0.1))
        base = model.initSystem()
        coordinate = model.updCoordinateSet().get(name)
        coordinate.setValue(base, value, False)
        model.assemble(base)
        model.realizePosition(base)
        base_relative = _body_relative(
            model, base, f"humerus_{side}", f"radius_{side}"
        )

        changed = model.initSystem()
        coordinate = model.updCoordinateSet().get(name)
        coordinate.setValue(changed, value + PERTURBATION_RAD, False)
        model.assemble(changed)
        model.realizePosition(changed)
        changed_relative = _body_relative(
            model, changed, f"humerus_{side}", f"radius_{side}"
        )
        tangent = _rotvec(changed_relative @ base_relative.T) / PERTURBATION_RAD
        tangent /= np.linalg.norm(tangent)
        rows.append({"coordinate_value_rad": value, "tangent_parent_body": tangent.tolist()})
    reference = np.asarray(rows[0]["tangent_parent_body"])
    return {
        "joint": f"elbow_{side}",
        "joint_class": model.getJointSet()
        .get(f"elbow_{side}")
        .getConcreteClassName(),
        "coordinate": name,
        "range_rad": [lo, hi],
        "finite_difference_rad": PERTURBATION_RAD,
        "samples": rows,
        "maximum_sign_invariant_tangent_variation_rad": float(
            max(
                _angle_undirected(reference, np.asarray(row["tangent_parent_body"]))
                for row in rows
            )
        ),
        "representative_tangent_parent_body": reference.tolist(),
    }


def run(workspace: Path, model_path: Path, qmt_report: Path, output: Path) -> dict:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output.parent / "axis_ownership_opensim.log")
    frozen = load_frozen_c2_3a(workspace=workspace)
    raw_qmt = json.loads(qmt_report.read_text())
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    model.realizePosition(state)
    episodes = {}
    for episode_key, side, edge in (
        ("06", "l", "elbow_left"),
        ("07", "r", "elbow_right"),
    ):
        upper_name = "upper_arm_left" if side == "l" else "upper_arm_right"
        forearm_name = "forearm_left" if side == "l" else "forearm_right"
        episode = frozen.episodes[episode_key]
        upper = episode.segments[upper_name]
        forearm = episode.segments[forearm_name]
        relative = [
            _quat_wxyz_matrix(parent).T @ _quat_wxyz_matrix(child)
            for parent, child in zip(
                upper.quat_world_segment_wxyz,
                forearm.quat_world_segment_wxyz,
                strict=True,
            )
        ]
        relative_time_s = upper.time_root_s - upper.time_root_s[0]
        fresh = _line_fit(relative, relative_time_s, FLEXION_WINDOW_S)
        qmt_window = raw_qmt["qmt_olsson_hinge_axes"][edge][
            "registered_windows"
        ][0]
        matched_window = _line_fit(
            relative,
            relative_time_s,
            (
                float(qmt_window["registered_start_s"]),
                float(qmt_window["registered_stop_s"]),
            ),
        )

        parent_transform = _body_frame_transform(
            model, state, f"humerus_{side}", f"humerus_{side}_imu"
        )
        child_transform = _body_frame_transform(
            model, state, f"radius_{side}", f"radius_{side}_imu"
        )
        parent_child_body = _body_relative(
            model, state, f"humerus_{side}", f"radius_{side}"
        )
        qmt = frozen.hinge_axes[edge]
        qmt_parent_body = parent_transform @ qmt.parent_axis_reset_segment
        qmt_child_in_parent_body = (
            parent_child_body @ child_transform @ qmt.child_axis_reset_segment
        )
        fresh_parent_body = parent_transform @ np.asarray(
            fresh["axis_parent_segment_undirected"]
        )
        matched_parent_body = parent_transform @ np.asarray(
            matched_window["axis_parent_segment_undirected"]
        )
        official = _official_elbow_tangents(model, side)
        official_axis = np.asarray(official["representative_tangent_parent_body"])
        episodes[episode_key] = {
            "edge": edge,
            "parent_segment": upper_name,
            "child_segment": forearm_name,
            "common_frame": f"OpenSim humerus_{side} body",
            "transform": {
                "equation": "axis_body = R_body_from_calibrated_imu * axis_reset_segment",
                "parent_R_body_from_calibrated_imu": parent_transform.tolist(),
                "child_R_body_from_calibrated_imu": child_transform.tolist(),
                "parent_R_body_from_child_body_at_calibration": parent_child_body.tolist(),
                "parent_det": float(np.linalg.det(parent_transform)),
                "child_det": float(np.linalg.det(child_transform)),
                "parent_orthogonality_frobenius": float(
                    np.linalg.norm(parent_transform.T @ parent_transform - np.eye(3))
                ),
                "child_orthogonality_frobenius": float(
                    np.linalg.norm(child_transform.T @ child_transform - np.eye(3))
                ),
                "parent_axis_round_trip_error": float(
                    np.linalg.norm(
                        parent_transform.T @ qmt_parent_body
                        - qmt.parent_axis_reset_segment
                    )
                ),
                "child_axis_round_trip_error": float(
                    np.linalg.norm(
                        child_transform.T
                        @ parent_child_body.T
                        @ qmt_child_in_parent_body
                        - qmt.child_axis_reset_segment
                    )
                ),
            },
            "qmt_olsson_all_evidence": {
                "primitive": "qmt.jointAxisEstHingeOlsson_unmodified",
                "parent_axis_reset_segment": qmt.parent_axis_reset_segment.tolist(),
                "child_axis_reset_segment": qmt.child_axis_reset_segment.tolist(),
                "parent_axis_common_parent_body": qmt_parent_body.tolist(),
                "child_axis_common_parent_body": qmt_child_in_parent_body.tolist(),
                "parent_child_axis_angle_rad": _angle_undirected(
                    qmt_parent_body, qmt_child_in_parent_body
                ),
                "raw_aligned_row_count": raw_qmt["qmt_olsson_hinge_axes"][edge][
                    "raw_aligned_row_count"
                ],
                "registered_windows": raw_qmt["qmt_olsson_hinge_axes"][edge][
                    "registered_windows"
                ],
                "covariance_rad2": None,
            },
            "fresh_flexion_relative_rotation_line": {
                **fresh,
                "axis_common_parent_body": fresh_parent_body.tolist(),
            },
            "fresh_line_on_qmt_registered_window_diagnostic": {
                **matched_window,
                "axis_common_parent_body": matched_parent_body.tolist(),
                "warning": "includes the 15-20 s pronation/supination portion",
            },
            "official_rajagopal": official,
            "sign_invariant_angles_rad": {
                "qmt_parent_vs_fresh_flexion": _angle_undirected(
                    qmt_parent_body, fresh_parent_body
                ),
                "qmt_parent_vs_fresh_matched_window": _angle_undirected(
                    qmt_parent_body, matched_parent_body
                ),
                "qmt_parent_vs_rajagopal": _angle_undirected(
                    qmt_parent_body, official_axis
                ),
                "fresh_flexion_vs_rajagopal": _angle_undirected(
                    fresh_parent_body, official_axis
                ),
            },
        }
    result = {
        "schema": "biospur-c2-elbow-axis-common-parent-frame-audit-v1",
        "classification": "READ_ONLY_CAUSAL_EVIDENCE",
        "model_change": False,
        "official_ik_calls": 0,
        "official_fk_tangent_samples": 6,
        "model": str(model_path.resolve()),
        "model_sha256": sha256_file(model_path),
        "qmt_report": str(qmt_report.resolve()),
        "qmt_report_sha256": sha256_file(qmt_report),
        "episodes": episodes,
        "decision": {
            "shortest_arc_model_candidate_run": False,
            "reason": (
                "The QMT/Olsson and fresh flexion axes disagree on both sides; "
                "the right fresh line also lacks dominant one-dimensional excitation."
            ),
            "pixel_or_ik_result_used_for_axis_selection": False,
        },
        "wall_s": time.monotonic() - started,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--qmt-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.workspace, args.model, args.qmt_report, args.output),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
