"""Bounded clean-phase rerun of the official QMT/Olsson elbow-axis primitive.

Only the verified IMU orientation frontend is read.  The production QMT call
is unchanged, but the registered elbow window is corrected from the mixed
5--20 s interval to the protocol-owned 0--15 s flexion interval.  This module
does not change an OpenSim model or run IK.
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
import json
import math
import time
from pathlib import Path

import numpy as np
import qmt

from biospur_fusion.c2_coupled_progressive.contracts import SEGMENT_TO_NODE
from biospur_fusion.c2_coupled_progressive.estimator import (
    aligned_spans_for_episode,
    load_real_episodes,
)
from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import (
    estimate_pose_reset_calibration,
)

from .pipeline import configure_opensim_log, sha256_file
from .radioulnar_axis_ownership import _angle_undirected
from .radioulnar_fix import _matrix


CLEAN_WINDOW_S = (0.0, 15.0)
LEAVE_BLOCK_COUNT = 5


def _unit(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float).reshape(3)
    return value / np.linalg.norm(value)


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


def _fit(parent_acc, child_acc, parent_gyro, child_gyro):
    started = time.monotonic()
    with contextlib.redirect_stdout(io.StringIO()):
        parent_sensor, child_sensor = qmt.jointAxisEstHingeOlsson(
            parent_acc,
            child_acc,
            parent_gyro,
            child_gyro,
            {"useSampleSelection": False},
            debug=False,
            plot=False,
        )
    return (
        _unit(np.asarray(parent_sensor)),
        _unit(np.asarray(child_sensor)),
        time.monotonic() - started,
    )


def _spectrum(values: np.ndarray) -> dict[str, object]:
    centered = values - np.mean(values, axis=0, keepdims=True)
    singular = np.linalg.svd(centered, compute_uv=False)
    return {
        "centered_singular_values": singular.tolist(),
        "condition_first_to_third": float(singular[0] / singular[-1]),
        "rms": float(np.sqrt(np.mean(np.square(values)))),
    }


def _jackknife_covariance(full_axis: np.ndarray, axes: list[np.ndarray]) -> dict:
    aligned = np.asarray(
        [axis if np.dot(axis, full_axis) >= 0.0 else -axis for axis in axes]
    )
    differences = aligned - np.mean(aligned, axis=0, keepdims=True)
    count = len(aligned)
    covariance = (count - 1.0) / count * differences.T @ differences
    angles = [_angle_undirected(full_axis, axis) for axis in aligned]
    return {
        "method": "five contiguous equal-row leave-one-block-out jackknife",
        "axis_samples_sign_aligned_to_full_fit": aligned.tolist(),
        "axis_covariance": covariance.tolist(),
        "axis_covariance_trace": float(np.trace(covariance)),
        "axis_angle_to_full_rad": angles,
        "maximum_axis_angle_to_full_rad": float(max(angles)),
    }


def run(
    workspace: Path,
    model_path: Path,
    common_frame_audit_path: Path,
    output: Path,
) -> dict:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output.parent / "clean_qmt_opensim.log")
    episodes = load_real_episodes()
    calibration = estimate_pose_reset_calibration(episodes)
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    model.realizePosition(state)
    common = json.loads(common_frame_audit_path.read_text())
    result_episodes = {}
    total_calls = 0
    for episode_index, key, side, edge in (
        (5, "06", "l", "elbow_left"),
        (6, "07", "r", "elbow_right"),
    ):
        spans = [
            span
            for span in aligned_spans_for_episode(episodes[episode_index])
            if span.edge == edge
        ]
        if not spans:
            raise RuntimeError(f"no aligned span for {edge}")
        episode_start_s = min(float(span.time_root_s[0]) for span in spans)
        selected = []
        for span in spans:
            relative_s = span.time_root_s - episode_start_s
            mask = (relative_s >= CLEAN_WINDOW_S[0]) & (relative_s <= CLEAN_WINDOW_S[1])
            if np.any(mask):
                selected.append((span, mask, relative_s[mask]))
        if not selected:
            raise RuntimeError(f"no clean-phase rows for {edge}")
        parent_acc = np.concatenate([span.parent_acc_mps2[mask] for span, mask, _ in selected])
        child_acc = np.concatenate([span.child_acc_mps2[mask] for span, mask, _ in selected])
        parent_gyro = np.concatenate([span.parent_gyro_rads[mask] for span, mask, _ in selected])
        child_gyro = np.concatenate([span.child_gyro_rads[mask] for span, mask, _ in selected])
        selected_time = np.concatenate([relative for _, _, relative in selected])
        full_parent_sensor, full_child_sensor, full_wall = _fit(
            parent_acc, child_acc, parent_gyro, child_gyro
        )
        total_calls += 1

        blocks = np.array_split(np.arange(len(parent_acc)), LEAVE_BLOCK_COUNT)
        jack_parent_sensor = []
        jack_child_sensor = []
        jack_walls = []
        for block in blocks:
            keep = np.ones(len(parent_acc), dtype=bool)
            keep[block] = False
            parent_axis, child_axis, call_wall = _fit(
                parent_acc[keep], child_acc[keep], parent_gyro[keep], child_gyro[keep]
            )
            jack_parent_sensor.append(parent_axis)
            jack_child_sensor.append(child_axis)
            jack_walls.append(call_wall)
            total_calls += 1

        parent_segment = "upper_arm_left" if side == "l" else "upper_arm_right"
        child_segment = "forearm_left" if side == "l" else "forearm_right"
        parent_reset = _unit(
            calibration.initial_world_sensor[parent_segment] @ full_parent_sensor
        )
        child_reset = _unit(
            calibration.initial_world_sensor[child_segment] @ full_child_sensor
        )
        parent_transform = _body_frame_transform(
            model, state, f"humerus_{side}", f"humerus_{side}_imu"
        )
        child_transform = _body_frame_transform(
            model, state, f"radius_{side}", f"radius_{side}_imu"
        )
        parent_child = _body_relative(
            model, state, f"humerus_{side}", f"radius_{side}"
        )
        parent_body = _unit(parent_transform @ parent_reset)
        child_in_parent_body = _unit(parent_child @ child_transform @ child_reset)

        jack_parent_body = [
            _unit(
                parent_transform
                @ calibration.initial_world_sensor[parent_segment]
                @ axis
            )
            for axis in jack_parent_sensor
        ]
        jack_child_in_parent = [
            _unit(
                parent_child
                @ child_transform
                @ calibration.initial_world_sensor[child_segment]
                @ axis
            )
            for axis in jack_child_sensor
        ]
        fresh = np.asarray(
            common["episodes"][key]["fresh_flexion_relative_rotation_line"]
            ["axis_common_parent_body"]
        )
        rajagopal = np.asarray(
            common["episodes"][key]["official_rajagopal"]
            ["representative_tangent_parent_body"]
        )
        result_episodes[key] = {
            "edge": edge,
            "protocol_episode_index": episode_index,
            "window_s": list(CLEAN_WINDOW_S),
            "aligned_span_count": len(selected),
            "raw_aligned_row_count": int(len(parent_acc)),
            "first_relative_time_s": float(selected_time[0]),
            "last_relative_time_s": float(selected_time[-1]),
            "qmt_call": {
                "function": "qmt.jointAxisEstHingeOlsson",
                "settings": {"useSampleSelection": False},
                "debug": False,
                "plot": False,
                "full_fit_wall_s": full_wall,
                "leave_block_call_walls_s": jack_walls,
            },
            "excitation": {
                "parent_gyro_rads": _spectrum(parent_gyro),
                "child_gyro_rads": _spectrum(child_gyro),
                "parent_acc_mps2": _spectrum(parent_acc),
                "child_acc_mps2": _spectrum(child_acc),
            },
            "axes": {
                "parent_axis_sensor": full_parent_sensor.tolist(),
                "child_axis_sensor": full_child_sensor.tolist(),
                "parent_axis_reset_segment": parent_reset.tolist(),
                "child_axis_reset_segment": child_reset.tolist(),
                "parent_axis_common_parent_body": parent_body.tolist(),
                "child_axis_common_parent_body": child_in_parent_body.tolist(),
            },
            "uncertainty": {
                "parent": _jackknife_covariance(parent_body, jack_parent_body),
                "child_in_parent": _jackknife_covariance(
                    child_in_parent_body, jack_child_in_parent
                ),
                "qmt_full_fit_covariance": None,
            },
            "sign_invariant_angles_rad": {
                "parent_vs_child": _angle_undirected(
                    parent_body, child_in_parent_body
                ),
                "parent_vs_fresh_relative_rotation_line": _angle_undirected(
                    parent_body, fresh
                ),
                "parent_vs_rajagopal": _angle_undirected(parent_body, rajagopal),
                "fresh_relative_rotation_line_vs_rajagopal": _angle_undirected(
                    fresh, rajagopal
                ),
            },
            "transform_round_trip": {
                "parent_error": float(
                    np.linalg.norm(parent_transform.T @ parent_body - parent_reset)
                ),
                "child_error": float(
                    np.linalg.norm(
                        child_transform.T
                        @ parent_child.T
                        @ child_in_parent_body
                        - child_reset
                    )
                ),
                "parent_det": float(np.linalg.det(parent_transform)),
                "child_det": float(np.linalg.det(child_transform)),
            },
        }

    qmt_source = Path(inspect.getsourcefile(qmt.jointAxisEstHingeOlsson)).resolve()
    result = {
        "schema": "biospur-c2-clean-flexion-qmt-olsson-audit-v1",
        "classification": "RESULT_INDEPENDENT_READ_ONLY_CAUSAL_RERUN",
        "model_change": False,
        "official_ik_calls": 0,
        "qmt_public_calls": total_calls,
        "qmt_version": getattr(qmt, "__version__", "unknown"),
        "qmt_source": str(qmt_source),
        "qmt_source_sha256": sha256_file(qmt_source),
        "frontend": {
            "source": "VerifiedFrontendArchive orientation/* whitelist",
            "allowed_values": "continuous 6-axis and orientation frontend; no UWB",
            "raw_payload_opened": False,
        },
        "model": str(model_path.resolve()),
        "model_sha256": sha256_file(model_path),
        "common_frame_audit": str(common_frame_audit_path.resolve()),
        "common_frame_audit_sha256": sha256_file(common_frame_audit_path),
        "episodes": result_episodes,
        "decision_rule": (
            "No model mutation unless clean QMT parent/child and the independent "
            "fresh relative-rotation line agree in the common parent-body frame."
        ),
        "wall_s": time.monotonic() - started,
    }
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--common-frame-audit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            run(args.workspace, args.model, args.common_frame_audit, args.output),
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
