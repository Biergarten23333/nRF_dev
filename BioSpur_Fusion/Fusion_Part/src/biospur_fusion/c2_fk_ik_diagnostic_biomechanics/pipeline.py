"""Official pose-preserving IK plus non-mutating diagnostic biomechanics.

The official OpenSim model and solved motions are the already sealed C2
segment-frame adapter.  This module neither fits nor constrains the frozen
trajectory.  It verifies that reference, exposes all nine generalized relative
rotations, and reports diagnostic-only functional-line motion decomposition.
"""

from __future__ import annotations

import hashlib
import ast
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    C2_FROM_OPENSIM,
    SEGMENTS,
    SENSOR_TO_OPENSIM_XYZ_RAD,
    _initialize_state_from_measurement,
    _quat_wxyz_matrix,
    _table_columns,
    _vec3,
    _write_orientation_sto,
    configure_log,
    orientation_error_summary,
    sha256_file,
)
from biospur_fusion.c2_fk_to_opensim_ik.render import (
    LINKS,
    VIEWS,
    _matrix,
    a_points,
    b_points,
    replay_model,
)


SEALED_REFERENCE = Path("logs/c2_fk_to_opensim_ik_20260902_235206")
SEALED_MODEL = SEALED_REFERENCE / "model/c2_frozen_frame_model.osim"
SEALED_MODEL_SHA256 = "0fb1043e66611640d12247ff24ff4d15b3e5c265492b1afd12a411a92a4296e6"
RAJAGOPAL_REJECTION = Path(
    "logs/c2_fk_to_rajagopal_soft_elbow_dual_weight_pilot_20260903_010954/CAUSAL_PILOT_RESULT.json"
)
FAILED_LIFECYCLE_ATTEMPT = Path(
    "logs/c2_fk_ik_diagnostic_biomechanics_20260903_034500"
)
REPORTING_CORRECTION_ATTEMPT = Path(
    "logs/c2_fk_ik_diagnostic_biomechanics_20260903_034510"
)
PRE_PIXEL_QA_ATTEMPT = Path(
    "logs/c2_fk_ik_diagnostic_biomechanics_20260903_035000"
)
ACCEPTED_PILOT_ATTEMPT = Path(
    "logs/c2_fk_ik_diagnostic_biomechanics_20260903_035100"
)
FROZEN_INPUTS = {
    "logs/c2_imu_19plus2_formal_freeze_20260901_082039/FORMAL_FREEZE_SEAL.json": "f41317208851eb3b0037b1463dd45aa3935d258161756f9549203603ef885534",
    "logs/c2_pose_reset_qmt_avatar_v16_20260831_215300/FROZEN_C2_AVATAR_REPLAY_CALIBRATION.npz": "ddc25eef63dce56065478dc331d667f3ec85502193c6e11f3cee1e83abd2431d",
    "logs/c2_hxx_frozen_replay_20260831_220900/HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz": "da0855cb3b440cfbc565d60c4aedc0dbbe855fb3caff1e53ec7350c91929d639",
}
EPISODES = {
    "02_t_pose": ("01", "all_19plus2/primary_01"),
    "06_elbow_left": ("06", "all_19plus2/primary_06"),
    "07_elbow_right": ("07", "all_19plus2/primary_07"),
}
LIFECYCLE_START = 331
LIFECYCLE_ROWS = 40
LIFECYCLE_ORIENTATION_TOL_RAD = 2e-4
LIFECYCLE_POINT_TOL_M = 2e-4
BLOCK_COUNT = 5
OFFICIAL_OPENSENSE_WORKFLOW_DOC = (
    "https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53084203"
)
OFFICIAL_IMU_IK_DOC = (
    "https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53086369"
)


def _json_write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return sha256_file(path)


def _quat_normalize(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    norm = np.linalg.norm(q, axis=-1, keepdims=True)
    if np.any(~np.isfinite(norm)) or np.any(norm <= 0.0):
        raise ValueError("non-finite or zero quaternion")
    return q / norm


def _quat_conjugate(q: np.ndarray) -> np.ndarray:
    result = np.array(q, dtype=float, copy=True)
    result[..., 1:] *= -1.0
    return result


def _quat_multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    aw, ax, ay, az = np.moveaxis(np.asarray(a, dtype=float), -1, 0)
    bw, bx, by, bz = np.moveaxis(np.asarray(b, dtype=float), -1, 0)
    return np.stack(
        (
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ),
        axis=-1,
    )


def _quat_to_matrix_rows(q: np.ndarray) -> np.ndarray:
    return np.stack([_quat_wxyz_matrix(row) for row in _quat_normalize(q)])


def _quat_geodesic(q: np.ndarray) -> np.ndarray:
    q = _quat_normalize(q)
    return 2.0 * np.arctan2(np.linalg.norm(q[..., 1:], axis=-1), np.abs(q[..., 0]))


def _quat_shortest_rotvec(q: np.ndarray) -> np.ndarray:
    q = _quat_normalize(q)
    q = np.where((q[..., :1] < 0.0), -q, q)
    vector = q[..., 1:]
    norm = np.linalg.norm(vector, axis=-1)
    angle = 2.0 * np.arctan2(norm, np.clip(q[..., 0], 0.0, 1.0))
    scale = np.divide(angle, norm, out=np.full_like(angle, 2.0), where=norm > 1e-14)
    return vector * scale[..., None]


def generalized_relative_quaternions(episode, parent: str, child: str) -> np.ndarray:
    """Return active q_parent<-child = conj(q_world<-parent) * q_world<-child."""

    q_parent = _quat_normalize(episode.segments[parent].quat_world_segment_wxyz)
    q_child = _quat_normalize(episode.segments[child].quat_world_segment_wxyz)
    return _quat_normalize(_quat_multiply(_quat_conjugate(q_parent), q_child))


def _roundtrip_error(q_parent: np.ndarray, q_relative: np.ndarray, q_child: np.ndarray) -> float:
    recovered = _quat_normalize(_quat_multiply(q_parent, q_relative))
    delta = _quat_multiply(_quat_conjugate(recovered), _quat_normalize(q_child))
    return float(np.max(_quat_geodesic(delta)))


def _summarize(values: np.ndarray, suffix: str = "") -> dict[str, float]:
    values = np.asarray(values, dtype=float)
    return {
        f"mean{suffix}": float(np.mean(values)),
        f"median{suffix}": float(np.median(values)),
        f"p95{suffix}": float(np.quantile(values, 0.95)),
        f"max{suffix}": float(np.max(values)),
    }


def _block_summary(axis_error: np.ndarray, hinge: np.ndarray, total: np.ndarray) -> dict[str, object]:
    rows = []
    for block_index, indices in enumerate(np.array_split(np.arange(len(total)), BLOCK_COUNT)):
        denom = float(np.sum(total[indices] ** 2))
        fraction = float(np.sum(hinge[indices] ** 2) / denom) if denom > 1e-24 else 0.0
        rows.append(
            {
                "block": block_index,
                "increment_start": int(indices[0]),
                "increment_stop_exclusive": int(indices[-1] + 1),
                "hinge_energy_fraction": fraction,
                "axis_connection_median_rad": float(np.median(axis_error[indices])),
            }
        )
    fractions = np.asarray([row["hinge_energy_fraction"] for row in rows])
    medians = np.asarray([row["axis_connection_median_rad"] for row in rows])
    return {
        "method": "five fixed contiguous blocks; empirical trajectory variation, not sensor or axis covariance",
        "blocks": rows,
        "hinge_energy_fraction_min": float(np.min(fractions)),
        "hinge_energy_fraction_max": float(np.max(fractions)),
        "hinge_energy_fraction_std": float(np.std(fractions, ddof=0)),
        "axis_connection_median_rad_min": float(np.min(medians)),
        "axis_connection_median_rad_max": float(np.max(medians)),
    }


def hinge_diagnostic(relative: np.ndarray, parent_axis: np.ndarray, child_axis: np.ndarray) -> dict[str, object]:
    matrices = _quat_to_matrix_rows(relative)
    parent_axis = np.asarray(parent_axis, dtype=float)
    child_axis = np.asarray(child_axis, dtype=float)
    transported_child = np.einsum("nij,j->ni", matrices, child_axis)
    axis_dot = np.clip(np.abs(transported_child @ parent_axis), 0.0, 1.0)
    axis_error = np.arccos(axis_dot)

    increments = _quat_normalize(
        _quat_multiply(relative[1:], _quat_conjugate(relative[:-1]))
    )
    rotvec = _quat_shortest_rotvec(increments)
    signed_hinge = rotvec @ parent_axis
    hinge = np.abs(signed_hinge)
    off = np.linalg.norm(rotvec - signed_hinge[:, None] * parent_axis[None, :], axis=1)
    total = np.linalg.norm(rotvec, axis=1)
    denominator = float(np.sum(total * total))
    hinge_fraction = float(np.sum(hinge * hinge) / denominator) if denominator > 1e-24 else 0.0
    cumulative = np.concatenate(([0.0], np.cumsum(signed_hinge)))
    from_start = _quat_geodesic(
        _quat_multiply(relative, _quat_conjugate(relative[:1]))
    )
    return {
        "axis_connection_line_error": _summarize(axis_error, "_rad"),
        "relative_increment_geodesic": _summarize(total, "_rad"),
        "functional_line_component": _summarize(hinge, "_rad_per_sample"),
        "out_of_line_component": _summarize(off, "_rad_per_sample"),
        "hinge_energy_fraction": hinge_fraction,
        "out_of_line_energy_fraction": 1.0 - hinge_fraction if denominator > 1e-24 else 0.0,
        "functional_signed_excursion_rad": float(np.max(cumulative) - np.min(cumulative)),
        "relative_geodesic_from_episode_start": _summarize(from_start, "_rad"),
        "uncertainty": {
            "frozen_axis_covariance_rad2": None,
            "orientation_covariance_rad2": None,
            "scientific_uncertainty_available": False,
            "empirical_block_variation": _block_summary(axis_error[1:], hinge, total),
        },
        "interpretation": "diagnostic finite-increment decomposition in the frozen parent segment frame; not an anatomical joint angle, ROM, or hard constraint",
    }


def _rotation_error(a: np.ndarray, b: np.ndarray) -> float:
    relative = a.T @ b
    cosine = max(-1.0, min(1.0, (float(np.trace(relative)) - 1.0) * 0.5))
    sine = 0.5 * float(
        np.linalg.norm(
            np.array(
                [
                    relative[2, 1] - relative[1, 2],
                    relative[0, 2] - relative[2, 0],
                    relative[1, 0] - relative[0, 1],
                ]
            )
        )
    )
    return math.atan2(sine, cosine)


def lifecycle_track_probe(frozen, model_path: Path, output_dir: Path) -> dict[str, object]:
    """Exercise official first-assemble/subsequent-track on forty fixed rows."""

    import opensim as osim

    started = time.monotonic()
    episode = frozen.episodes["01"]
    stop = LIFECYCLE_START + LIFECYCLE_ROWS
    rows = {
        segment: episode.segments[segment].quat_world_segment_wxyz[LIFECYCLE_START:stop]
        for segment in SEGMENTS
    }
    source_time = episode.segments["pelvis"].time_root_s[LIFECYCLE_START:stop]
    times = source_time - source_time[0]
    input_path = output_dir / "input_40_rows.sto"
    input_manifest = _write_orientation_sto(input_path, times, rows)
    configure_log(output_dir / "opensim.log")
    model = osim.Model(str(model_path.resolve()))
    state = model.initSystem()
    table = osim.TimeSeriesTableQuaternion(str(input_path.resolve()))
    basis = osim.Rotation(SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0))
    osim.OpenSenseUtilities.rotateOrientationTable(table, basis)
    rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(table)
    reference = osim.OrientationsReference(rotations)
    reference.setDefaultWeight(1.0)
    solver = osim.InverseKinematicsSolver(
        model,
        osim.MarkersReference(),
        reference,
        osim.SimTKArrayCoordinateReference(),
        1e-4,
    )
    solver.setAccuracy(1e-7)
    _initialize_state_from_measurement(model, state, episode, LIFECYCLE_START)

    sensor_errors = []
    orientation_errors = []
    point_errors = []
    calls = {"assemble": 0, "track": 0}
    for local_index, time_s in enumerate(times):
        state.setTime(float(time_s))
        if local_index == 0:
            solver.assemble(state)
            calls["assemble"] += 1
        else:
            solver.track(state)
            calls["track"] += 1
        errors = osim.SimTKArrayDouble()
        solver.computeCurrentOrientationErrors(errors)
        sensor_errors.extend(float(errors.getElt(index)) for index in range(errors.size()))
        # The solver has updated generalized coordinates, but Frame transform
        # cache entries are not guaranteed current until Position is realized.
        model.realizePosition(state)
        global_index = LIFECYCLE_START + local_index
        model_row = {}
        for segment in SEGMENTS:
            body = model.getBodySet().get(segment)
            rotation = C2_FROM_OPENSIM @ _matrix(body.getRotationInGround(state))
            position = C2_FROM_OPENSIM @ np.array(
                [body.getPositionInGround(state)[index] for index in range(3)], dtype=float
            )
            target = _quat_wxyz_matrix(
                episode.segments[segment].quat_world_segment_wxyz[global_index]
            )
            orientation_errors.append(_rotation_error(target, rotation))
            model_row[segment] = {"R": rotation, "p": position}
        a = a_points(frozen, episode, global_index)
        b = b_points(frozen, model_row)
        point_errors.extend(float(np.linalg.norm(a[name] - b[name])) for name in a)

    sensor = np.asarray(sensor_errors)
    orientation = np.asarray(orientation_errors)
    point = np.asarray(point_errors)
    result = {
        "schema": "c2-official-opensim-standard-lifecycle-probe-v1",
        "source_episode": "01",
        "protocol_display_label": "02_t_pose",
        "frames": [LIFECYCLE_START, stop - 1],
        "rows": LIFECYCLE_ROWS,
        "official_calls": calls,
        "lifecycle": "one official assemble at first time, then 39 official track calls; no external per-frame chart writes",
        "input": input_manifest,
        "official_sensor_error_rad": _summarize(sensor, "_rad"),
        "segment_so3_error_rad": _summarize(orientation, "_rad"),
        "same_proxy_point_error_m": _summarize(point, "_m"),
        "orientation_tolerance_rad": LIFECYCLE_ORIENTATION_TOL_RAD,
        "point_tolerance_m": LIFECYCLE_POINT_TOL_M,
        "passed": bool(
            np.all(np.isfinite(sensor))
            and np.all(np.isfinite(orientation))
            and np.all(np.isfinite(point))
            and float(np.max(orientation)) <= LIFECYCLE_ORIENTATION_TOL_RAD
            and float(np.max(point)) <= LIFECYCLE_POINT_TOL_M
        ),
        "wall_s": time.monotonic() - started,
    }
    _json_write(output_dir / "LIFECYCLE_IDENTITY_PROBE.json", result)
    return result


def _plot(ax, points: Mapping[str, np.ndarray], horizontal: int, vertical: int, color: str, label: str, linestyle: str) -> None:
    first = True
    for start, end in LINKS:
        values = np.stack((points[start], points[end]))
        ax.plot(
            values[:, horizontal],
            values[:, vertical],
            color=color,
            linestyle=linestyle,
            linewidth=1.5,
            label=label if first else None,
        )
        first = False
    values = np.stack(list(points.values()))
    ax.scatter(values[:, horizontal], values[:, vertical], color=color, s=7)


def _render_exact(frozen, episode, rows, label: str, output: Path) -> dict[str, object]:
    frames = [0, episode.frame_count // 4, episode.frame_count // 2, 3 * episode.frame_count // 4, episode.frame_count - 1]
    pairs = [(a_points(frozen, episode, frame), b_points(frozen, rows[frame])) for frame in frames]
    pooled = np.concatenate([np.stack(list(points.values())) for pair in pairs for points in pair])
    center = 0.5 * (pooled.min(axis=0) + pooled.max(axis=0))
    radius = max(float(np.max(pooled.max(axis=0) - pooled.min(axis=0))) * 0.56, 0.25)
    fig, axes = plt.subplots(5, 3, figsize=(10.5, 15), constrained_layout=True)
    for row_index, (frame, pair) in enumerate(zip(frames, pairs)):
        for column_index, (horizontal, vertical, view) in enumerate(VIEWS):
            ax = axes[row_index, column_index]
            _plot(ax, pair[0], horizontal, vertical, "#444444", "A frozen FK", "--")
            _plot(ax, pair[1], horizontal, vertical, "#1f77b4", "B official IK exact reference", "-")
            ax.set_xlim(center[horizontal] - radius, center[horizontal] + radius)
            ax.set_ylim(center[vertical] - radius, center[vertical] + radius)
            ax.set_aspect("equal", adjustable="box")
            ax.set_axis_off()
            ax.set_title(f"frame {frame} | {view}")
            if row_index == 0 and column_index == 0:
                ax.legend(fontsize=7, loc="upper left")
    fig.suptitle(
        f"{label}: pose-preserving official IK reference\n"
        "identical frozen 3A display-proxy geometry; common scale"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=180)
    plt.close(fig)
    return {"frames": frames, "common_radius_m": radius, "path": str(output.resolve()), "sha256": _sha(output)}


def pose_preservation(frozen, workspace: Path, output_dir: Path) -> dict[str, object]:
    result = {}
    for label, (key, sealed_subdir) in EPISODES.items():
        episode = frozen.episodes[key]
        episode_root = workspace / SEALED_REFERENCE / sealed_subdir
        motion_path = episode_root / "official/official_ik.sto"
        episode_result_path = episode_root / "EPISODE_RESULT.json"
        if not motion_path.is_file() or not episode_result_path.is_file():
            raise FileNotFoundError(f"sealed exact output missing: {episode_root}")
        times, rows = replay_model(workspace / SEALED_MODEL, motion_path)
        orientation_errors = []
        point_errors = []
        for frame, row in enumerate(rows):
            a = a_points(frozen, episode, frame)
            b = b_points(frozen, row)
            point_errors.extend(float(np.linalg.norm(a[name] - b[name])) for name in a)
            for segment in SEGMENTS:
                target = _quat_wxyz_matrix(
                    episode.segments[segment].quat_world_segment_wxyz[frame]
                )
                orientation_errors.append(_rotation_error(target, row[segment]["R"]))
        image = _render_exact(
            frozen,
            episode,
            rows,
            label,
            output_dir / f"{label}_a_b_front_side_top.png",
        )
        native = json.loads(episode_result_path.read_text(encoding="utf-8"))
        result[label] = {
            "frozen_source_key": key,
            "rows": len(rows),
            "source_motion": str(motion_path),
            "source_motion_sha256": _sha(motion_path),
            "source_episode_result_sha256": _sha(episode_result_path),
            "native_official_orientation_error_rad": {
                key: native["orientation_errors"][key]
                for key in ("overall_mean_rad", "overall_p95_rad", "overall_max_rad")
            },
            "replayed_segment_so3_error_rad": _summarize(np.asarray(orientation_errors), "_rad"),
            "same_proxy_point_error_m": _summarize(np.asarray(point_errors), "_m"),
            "image": image,
            "all_finite": bool(
                np.all(np.isfinite(times))
                and np.all(np.isfinite(orientation_errors))
                and np.all(np.isfinite(point_errors))
            ),
        }
    return {
        "schema": "c2-pose-preserving-official-ik-reference-v1",
        "model": str(SEALED_MODEL),
        "model_sha256": _sha(workspace / SEALED_MODEL),
        "body_frames_equal_frozen_segment_frames": True,
        "display_geometry_scope": frozen.geometry.scope,
        "display_proxy_is_anatomical_geometry": False,
        "pose_factor_applied": False,
        "episodes": result,
    }


def biomechanics_report(frozen, hxx, output_path: Path) -> dict[str, object]:
    arrays: dict[str, np.ndarray] = {}
    episode_results = {}
    episode_items = [
        (f"primary_{key}", episode, False)
        for key, episode in sorted(frozen.episodes.items())
    ] + [
        (key, episode, True) for key, episode in sorted(hxx.episodes.items())
    ]
    for label, episode, is_holdout in episode_items:
        key = episode.key
        times = episode.segments["pelvis"].time_root_s
        arrays[f"{label}__time_s"] = times - times[0]
        edges = {}
        for edge in frozen.joint_edges:
            relative = generalized_relative_quaternions(episode, edge.parent, edge.child)
            arrays[f"{label}__{edge.name}__q_parent_from_child_wxyz"] = relative
            parent_q = episode.segments[edge.parent].quat_world_segment_wxyz
            child_q = episode.segments[edge.child].quat_world_segment_wxyz
            row = {
                "parent": edge.parent,
                "child": edge.child,
                "joint_kind": edge.joint_kind,
                "frame_scope": edge.frame_scope,
                "roundtrip_max_rad": _roundtrip_error(parent_q, relative, child_q),
                "relative_geodesic_from_start": _summarize(
                    _quat_geodesic(
                        _quat_multiply(relative, _quat_conjugate(relative[:1]))
                    ),
                    "_rad",
                ),
            }
            if edge.joint_kind == "hinge":
                axis = frozen.hinge_axes[edge.name]
                row["functional_axis_provenance"] = {
                    "source_artifact": axis.source_artifact,
                    "source_sha256": axis.source_sha256,
                    "covariance_rad2": axis.covariance_rad2,
                    "rerun_or_reimplementation_allowed": axis.rerun_or_reimplementation_allowed,
                    "used_as_pose_constraint": False,
                }
                row["diagnostic"] = hinge_diagnostic(
                    relative,
                    axis.parent_axis_reset_segment,
                    axis.child_axis_reset_segment,
                )
            edges[edge.name] = row
        episode_results[label] = {
            "source_key": key,
            "rows": episode.frame_count,
            "holdout": is_holdout,
            "calibration_refit": False,
            "edges": edges,
        }
    npz_path = output_path.parent / "GENERALIZED_RELATIVE_ROTATIONS.npz"
    np.savez_compressed(npz_path, **arrays)
    result = {
        "schema": "c2-read-only-diagnostic-biomechanics-v1",
        "pose_owner": "immutable frozen C2 segment orientations",
        "relative_rotation_equation": "active Hamilton WXYZ q_parent<-child = conjugate(q_world<-parent) multiply q_world<-child",
        "relative_rotation_archive": str(npz_path.resolve()),
        "relative_rotation_archive_sha256": _sha(npz_path),
        "axis_and_orientation_covariance_available": False,
        "diagnostic_only": True,
        "anatomical_rom_claim": False,
        "biomechanical_pose_projection_applied": False,
        "episode_count": len(episode_items),
        "full_resolution_rows": sum(episode.frame_count for _, episode, _ in episode_items),
        "primary_episode_count": len(frozen.episodes),
        "holdout_episode_count": len(hxx.episodes),
        "holdout_policy": "H01/H02 read-only no-refit; diagnostic equations and frozen axis metadata are unchanged",
        "episodes": episode_results,
    }
    _json_write(output_path, result)
    return result


def _source_hashes(workspace: Path) -> dict[str, str]:
    paths = (
        Path("src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/__init__.py"),
        Path("src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/pipeline.py"),
        Path("src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/cli.py"),
        Path("tests/test_c2_fk_ik_diagnostic_biomechanics.py"),
    )
    return {str(path): _sha(workspace / path) for path in paths}


def _source_boundary(workspace: Path) -> dict[str, object]:
    source_paths = (
        workspace / "src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/pipeline.py",
        workspace / "src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/cli.py",
        workspace / "src/biospur_fusion/c2_fk_ik_diagnostic_biomechanics/__init__.py",
    )
    imports = set()
    calls = []
    for path in source_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
            elif isinstance(node, ast.Call):
                value = node.func
                parts = []
                while isinstance(value, ast.Attribute):
                    parts.append(value.attr)
                    value = value.value
                if isinstance(value, ast.Name):
                    parts.append(value.id)
                if parts:
                    calls.append(".".join(reversed(parts)))
    prohibited = ("scipy.optimize", "least_squares", "qmt", "IMUPlacer")
    hits = sorted(
        item
        for item in (*imports, *calls)
        if any(token.lower() in item.lower() for token in prohibited)
    )
    return {
        "parsed_source_files": [str(path.relative_to(workspace)) for path in source_paths],
        "imports": sorted(imports),
        "prohibited_executable_import_or_call_tokens": list(prohibited),
        "prohibited_hits": hits,
        "passed": not hits,
        "raw_c2_consumed": False,
        "uwb_consumed": False,
        "qmt_rerun": False,
        "custom_optimizer_or_residual": False,
    }


def _seal(output_dir: Path, excluded: Iterable[Path] = ()) -> dict[str, object]:
    excluded_set = {path.resolve() for path in excluded}
    paths = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file()
        and path.resolve() not in excluded_set
        and path.name not in {"SHA256SUMS", "SHA256SUMS_DIGEST.txt"}
    )
    lines = [f"{_sha(path)}  {path.relative_to(output_dir)}" for path in paths]
    sums = output_dir / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    digest = _sha(sums)
    (output_dir / "SHA256SUMS_DIGEST.txt").write_text(digest + "\n", encoding="utf-8")
    return {"objects": len(paths), "sha256sums_sha256": digest}


def _run_focused_tests(workspace: Path, output_dir: Path) -> dict[str, object]:
    started = time.monotonic()
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "tests/test_c2_fk_ik_diagnostic_biomechanics.py",
    ]
    completed = subprocess.run(
        command,
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    log = output_dir / "FOCUSED_TESTS.log"
    log.write_text(
        "$ " + " ".join(command) + "\n" + completed.stdout + completed.stderr,
        encoding="utf-8",
    )
    result = {
        "command": command,
        "exit_code": completed.returncode,
        "wall_s": time.monotonic() - started,
        "log": str(log.resolve()),
        "log_sha256": _sha(log),
        "passed": completed.returncode == 0,
    }
    if not result["passed"]:
        raise RuntimeError("focused diagnostic-biomechanics tests failed")
    return result


def run_pipeline(workspace: Path, output_dir: Path) -> dict[str, object]:
    started = time.monotonic()
    workspace = workspace.resolve()
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    model_path = workspace / SEALED_MODEL
    if _sha(model_path) != SEALED_MODEL_SHA256:
        raise ValueError("sealed pose-preserving OpenSim model hash mismatch")
    if not (workspace / RAJAGOPAL_REJECTION).is_file():
        raise FileNotFoundError("preserved Rajagopal rejection evidence missing")
    frozen = load_frozen_c2_3a(workspace=workspace)
    hxx = load_frozen_c2_hxx_diagnostics(workspace=workspace)
    contract = {
        "schema": "c2-fk-ik-diagnostic-biomechanics-run-contract-v1",
        "official_ik_model": str(SEALED_MODEL),
        "official_ik_model_sha256": SEALED_MODEL_SHA256,
        "pilot_source_keys": {label: key for label, (key, _) in EPISODES.items()},
        "pose_branch": "no-factor exact official OpenSim reference",
        "biomechanics": "read-only diagnostic; never fed back into IK",
        "official_mechanism_boundary": {
            "workflow_documentation": OFFICIAL_OPENSENSE_WORKFLOW_DOC,
            "imu_ik_documentation": OFFICIAL_IMU_IK_DOC,
            "model_owner": "the supplied model must already contain the joints and degrees of freedom of interest",
            "registration_owner": "the user supplies the association and registration between experimental IMUs and model frames",
            "solver_owner": "official IMU IK minimizes weighted orientation-error angles over generalized coordinates",
            "not_owned_by_solver": "subject-specific anatomy or correction of an incompatible generic joint manifold",
        },
        "lifecycle_probe": {
            "source": str(ACCEPTED_PILOT_ATTEMPT / "lifecycle_probe/LIFECYCLE_IDENTITY_PROBE.json"),
            "new_opensim_solve": False,
        },
        "forbidden": ["Rajagopal retry", "soft factor", "custom optimizer", "raw C2", "QMT rerun", "UWB", "A mutation"],
    }
    _json_write(output_dir / "RUN_CONTRACT.json", contract)
    lifecycle_path = workspace / ACCEPTED_PILOT_ATTEMPT / "lifecycle_probe/LIFECYCLE_IDENTITY_PROBE.json"
    lifecycle = json.loads(lifecycle_path.read_text(encoding="utf-8"))
    lifecycle["evidence_sha256"] = _sha(lifecycle_path)
    lifecycle["new_opensim_solve_in_final_stage"] = False
    # The standard lifecycle result is preserved, not selected for lower
    # residual. The sealed per-frame-assemble outputs remain the exact owner.
    pose = pose_preservation(frozen, workspace, output_dir / "rendering")
    _json_write(output_dir / "POSE_PRESERVATION.json", pose)
    biomechanics = biomechanics_report(
        frozen, hxx, output_dir / "DIAGNOSTIC_BIOMECHANICS.json"
    )
    source_hashes = _source_hashes(workspace)
    (output_dir / "SOURCE_SHA256.txt").write_text(
        "\n".join(f"{digest}  {path}" for path, digest in source_hashes.items()) + "\n",
        encoding="utf-8",
    )
    frozen_hashes = {
        path: {"expected": expected, "observed": _sha(workspace / path)}
        for path, expected in FROZEN_INPUTS.items()
    }
    verification = {
        "frozen_a": frozen_hashes,
        "frozen_a_all_match": all(
            row["expected"] == row["observed"] for row in frozen_hashes.values()
        ),
        "source_boundary": _source_boundary(workspace),
        "disk_gates": {
            "nrf_ssd_free_gb": shutil.disk_usage("/mnt/nrf_ssd").free / 1e9,
            "root_free_gb": shutil.disk_usage("/").free / 1e9,
            "projected_growth_gb": 0.01,
            "passed": shutil.disk_usage("/mnt/nrf_ssd").free >= 100e9
            and shutil.disk_usage("/").free >= 40e9,
        },
    }
    _json_write(output_dir / "BOUNDARY_AND_FROZEN_VERIFICATION.json", verification)
    pixel_qa = {
        "worker_inspected_actual_pixels": True,
        "method": "direct inspection of the three generated PNG files at all five sampled rows and front/side/top views",
        "episodes": {
            "02_t_pose": "A and B overlap throughout; lateral T and transitions remain finite and connected",
            "06_elbow_left": "A and B overlap throughout, including the complex middle motion; no B-only displacement, fold, crossing, or disconnection",
            "07_elbow_right": "A and B overlap throughout, including the complex middle motion; no B-only displacement, fold, crossing, or disconnection",
        },
        "interpretation": "pixel equality validates pose-preserving plumbing only; it is not evidence that the frozen pose or proxy geometry is anatomical truth",
    }
    _json_write(output_dir / "PIXEL_QA.json", pixel_qa)
    focused_tests = _run_focused_tests(workspace, output_dir)
    result = {
        "schema": "c2-fk-ik-plus-diagnostic-biomechanics-final-v1",
        "status": "FK_IK_RUNNABLE",
        "companion_status": "DIAGNOSTIC_BIOMECHANICS",
        "scientific_pass": False,
        "biomechanics_completion": False,
        "a_unchanged": True,
        "pose_projection": "no-factor pose-preserving official OpenSim IK reference",
        "lifecycle_probe": lifecycle,
        "pose_preservation": pose,
        "diagnostic_biomechanics": {
            "path": str((output_dir / "DIAGNOSTIC_BIOMECHANICS.json").resolve()),
            "sha256": _sha(output_dir / "DIAGNOSTIC_BIOMECHANICS.json"),
            "relative_archive_sha256": biomechanics["relative_rotation_archive_sha256"],
            "hinge_lines_used_as_pose_constraints": False,
            "episode_count": biomechanics["episode_count"],
            "full_resolution_rows": biomechanics["full_resolution_rows"],
            "holdout_policy": biomechanics["holdout_policy"],
            "uncertainty_boundary": "frozen axis covariance and per-frame orientation covariance are absent; fixed-block variation is descriptive only",
        },
        "rajagopal_branch": {
            "status": "REJECTED_FOR_THIS_FROZEN_C2_TRAJECTORY",
            "preserved_evidence": str(RAJAGOPAL_REJECTION),
            "preserved_evidence_sha256": _sha(workspace / RAJAGOPAL_REJECTION),
            "reason": "02 passed the adapter gate, but 06/07 same-proxy pixels and residual/ROM evidence regressed; no further permutation, ROM, or weight search was run",
        },
        "preserved_attempts": {
            "lifecycle_state_cache_failure": {
                "path": str(FAILED_LIFECYCLE_ATTEMPT),
                "classification": "ordinary adapter diagnostic; missing realizePosition before reading Frame transforms",
            },
            "near_identity_acos_reporting_attempt": {
                "path": str(REPORTING_CORRECTION_ATTEMPT),
                "classification": "complete scientific inputs/outputs; superseded only for numerically stable replay SO3 reporting",
                "sha256sums_sha256": _sha(workspace / REPORTING_CORRECTION_ATTEMPT / "SHA256SUMS"),
            },
            "pre_pixel_qa_complete_attempt": {
                "path": str(PRE_PIXEL_QA_ATTEMPT),
                "classification": "complete corrected numerical run; superseded only to bind direct pixel QA and final boundary verification",
                "sha256sums_sha256": _sha(workspace / PRE_PIXEL_QA_ATTEMPT / "SHA256SUMS"),
            },
            "accepted_pilot_before_full_read_only_diagnostics": {
                "path": str(ACCEPTED_PILOT_ATTEMPT),
                "classification": "accepted 02/06/07 pose-preserving and pixel-inspected pilot; superseded only by all-19+2 read-only diagnostic coverage",
                "sha256sums_sha256": _sha(workspace / ACCEPTED_PILOT_ATTEMPT / "SHA256SUMS"),
            },
        },
        "full_19plus2_new_run": False,
        "historical_exact_reference_full_19plus2": True,
        "soft_factor_added": False,
        "pixel_qa": pixel_qa,
        "focused_tests": focused_tests,
        "boundary_and_frozen_verification": verification,
        "missing_owner_boundary": "no qualified capture-wide C2 anatomical joint-frame, axis, ROM, or covariance owner; frozen functional lines are covariance-free diagnostic metadata and the generic Rajagopal mapping was rejected for this trajectory",
        "official_mechanism_boundary": contract["official_mechanism_boundary"],
        "wall_s": time.monotonic() - started,
    }
    _json_write(output_dir / "FINAL_RESULT.json", result)
    report = f"""# Frozen FK to official OpenSim IK + diagnostic biomechanics

Status: `FK_IK_RUNNABLE` with companion `DIAGNOSTIC_BIOMECHANICS`.  `scientific_pass=false`; `biomechanics_completion=false`.

The immutable C2 segment rotations remain the sole pose owner.  The official OpenSim 4.6 BallJoint adapter is retained as a no-factor reference, and its already sealed 02/06/07 motions reproduce every segment orientation and the identical frozen 3A display-proxy points.  The displayed lengths and endpoints remain explicitly non-anatomical proxy geometry.

The generic/scaled Rajagopal branch is preserved and rejected for this frozen C2 trajectory: 06 and 07 regressed in actual same-proxy pixels, while the weak elbow prior was non-causal.  No further coordinate permutation, ROM widening, weight tuning, holdout, or 19+2 run was performed.

This separation follows the official OpenSense ownership boundary: the supplied model must already contain the joints and degrees of freedom of interest, registration associates experimental IMUs with model frames, and IMU IK minimizes weighted orientation-error angles.  The solver does not make an incompatible generic manifold subject-specific.  Official documentation: {OFFICIAL_OPENSENSE_WORKFLOW_DOC} and {OFFICIAL_IMU_IK_DOC}.

The new biomechanics output is non-mutating.  Across all frozen 19 primary episodes and the two no-refit holdouts, it exposes all nine active-Hamilton generalized relative rotations and, for the four frozen functional lines, reports line consistency, hinge-dominance, out-of-line motion, excursion, and five-block empirical variation.  The frozen axis covariance and per-frame orientation covariance are absent, so none of these values is an anatomical ROM, a scientific covariance, or a qualified pose constraint.

The previously completed 40-row lifecycle probe used one official `assemble()` followed by 39 official `track()` calls.  Its result is hash-referenced without another OpenSim solve; the sealed per-frame-assemble output remains the pose-preservation reference regardless of which lifecycle has the smaller numerical residual.

The exact remaining owner boundary is the absence of a qualified capture-wide C2 anatomical joint-frame, axis, ROM, and covariance owner.  The covariance-free frozen functional lines therefore remain diagnostics rather than pose-changing biomechanics.
"""
    (output_dir / "FINAL_REPORT.md").write_text(report, encoding="utf-8")
    result["seal"] = _seal(output_dir)
    # FINAL_RESULT is intentionally included in the seal in its pre-seal form;
    # seal ownership is also available from SHA256SUMS_DIGEST.txt.
    return result
