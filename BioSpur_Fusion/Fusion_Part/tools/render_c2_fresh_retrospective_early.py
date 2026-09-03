#!/usr/bin/env python3
"""Render four fresh-state, final-frame retrospective early C2 tri-views."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
from scipy.spatial.transform import Rotation


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
REPLAY = (
    SPRINT / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002"
    / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
)
REPLAY_AUDIT = (
    SPRINT / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002"
    / "REPLAY_AUDIT.json"
)
AMENDMENT = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
MAP_HELPER = WORKSPACE / "tools/render_c2_fresh_continuous_squat.py"
OUT = SPRINT / "C2_FRESH_FINAL_FROZEN_RETROSPECTIVE_EARLY_TRIVIEWS_008"


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode()
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def _write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _load_exact_map_helper() -> Any:
    spec = importlib.util.spec_from_file_location(
        "c2_fresh_exact_map_helper", MAP_HELPER,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load the bounded fresh exact-map helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh retrospective viewer requires canonical Fusion_Part")

    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.quaternion_contract import (
        qmt_wxyz_to_scipy_active,
    )
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        direct_orientation_avatar_fk,
        landmark_proxy_sensitivity_profiles,
    )

    map_helper = _load_exact_map_helper()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(AMENDMENT.read_text(encoding="utf-8"))["effective_settings"]
    if (
        manifest["fresh_verification"].get("pass") is not True
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("fresh retrospective viewer authority is inconsistent")
    actions = tuple(settings["execution_contract"]["chronological_actions"])
    requested = (
        (0, "00_initial_still"),
        (3, "04_shoulder_left"),
        (7, "08_hip_left"),
        (8, "09_hip_right"),
    )
    if any(actions[index] != action for index, action in requested):
        raise RuntimeError("registered early action chronology changed")
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = tuple(sorted({
        name for _, parent, child in EDGE_SPECS for name in (parent, child)
    }))
    if set(node_by_segment) != set(segments):
        raise RuntimeError("fresh retrospective hardware/segment identity is incomplete")
    nodes = tuple(sorted(node_by_segment.values()))
    final_physical_support = manifest["structure"]["physical_trajectory_support"]["18"]
    final_support = tuple(sorted(
        branch_id
        for branch_id, evidence in final_physical_support.items()
        if evidence.get("physically_legal") is True
    ))
    if len(final_support) != 4:
        raise RuntimeError("fresh retrospective viewer requires four final hard-supported branches")
    canonical_ids = tuple(
        row["branch_id"] for row in manifest["structure"]["frame_branches"]
    )
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    renderer = settings["scientific_renderer"]
    sample_quantiles = tuple(float(value) for value in renderer["sample_quantiles"])
    if sample_quantiles != (0.0, 0.5, 1.0):
        raise RuntimeError("registered renderer sample quantiles changed")
    quantile_by_action = {
        str(action): float(value)
        for action, value in renderer["partial_sensor_axis_proxy"]
        ["sample_quantile_by_action"].items()
    }
    if any(action not in quantile_by_action for _, action in requested):
        raise RuntimeError("registered per-action viewer quantile is incomplete")
    profile_linestyles = ("-", "--", ":", "-.", (0, (3, 1, 1, 1)))
    profile_alphas = (0.92, 0.58, 0.58, 0.36, 0.36)
    profile_labels = (
        "Observer A: L/R forearm 0.245 m",
        "Observer B low: L/R forearm 0.260 m",
        "Observer B high: L/R forearm 0.265 m",
        "Cross sensitivity: L-low/R-high",
        "Cross sensitivity: L-high/R-low",
    )
    colors = ("#111827", "#2563eb", "#dc2626", "#059669")
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    action_audits: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    OUT.mkdir(parents=True, exist_ok=False)

    with (
        np.load(NPZ, allow_pickle=False) as arrays,
        np.load(REPLAY, allow_pickle=False) as replay_arrays,
    ):
        weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        selection_branch_id = max(
            final_support,
            key=lambda value: (float(weights[canonical_ids.index(value)]), value),
        )
        for chronological_index, action in requested:
            selected_quantile = quantile_by_action[action]
            if selected_quantile not in sample_quantiles:
                raise RuntimeError(f"{action}: unregistered viewer quantile")
            orientation_bridge: dict[str, Any] = {}
            for node in nodes:
                fresh_time = np.asarray(
                    arrays[
                        f"orientation/{chronological_index:02d}/{node}/time_us"
                    ],
                    dtype=np.int64,
                )
                replay_time = np.asarray(
                    replay_arrays[
                        f"orientation/{chronological_index:02d}/{node}/time_us"
                    ],
                    dtype=np.int64,
                )
                fresh_quaternion = np.asarray(
                    arrays[
                        f"orientation/{chronological_index:02d}/{node}/"
                        "quat_world_sensor_wxyz"
                    ],
                    dtype=float,
                )
                replay_quaternion = np.asarray(
                    replay_arrays[
                        f"orientation/{chronological_index:02d}/{node}/"
                        "quat_world_sensor_wxyz"
                    ],
                    dtype=float,
                )
                if not np.array_equal(fresh_time, replay_time):
                    raise RuntimeError(f"{action}:{node}: time bridge differs")
                if not np.array_equal(fresh_quaternion, replay_quaternion):
                    raise RuntimeError(f"{action}:{node}: quaternion bridge differs")
                orientation_bridge[node] = {
                    "row_count": int(len(fresh_time)),
                    "time_us_sha256": _array_sha(fresh_time),
                    "quat_world_sensor_wxyz_sha256": _array_sha(fresh_quaternion),
                    "fresh_vs_replay_exact_equal": True,
                }
            pelvis_node = node_by_segment["pelvis"]
            pelvis_time_us = np.asarray(
                arrays[
                    f"orientation/{chronological_index:02d}/{pelvis_node}/time_us"
                ],
                dtype=np.int64,
            )
            grid_bridge: dict[str, Any] = {}
            for branch_id in final_support:
                fresh_grid = np.asarray(
                    arrays[
                        f"trajectories/{chronological_index:02d}/{branch_id}/"
                        "common_physical_time_s"
                    ],
                    dtype=float,
                )
                replay_grid = np.asarray(
                    replay_arrays[
                        f"trajectory/{chronological_index:02d}/{branch_id}/"
                        "common_physical_time_s"
                    ],
                    dtype=float,
                )
                if not np.array_equal(fresh_grid, replay_grid):
                    raise RuntimeError(f"{action}:{branch_id}: common grid differs")
                grid_bridge[branch_id] = {
                    "row_count": int(len(fresh_grid)),
                    "sha256": _array_sha(fresh_grid),
                    "fresh_vs_replay_exact_equal": True,
                }
            root_rows, segment_source_by_root, edge_map_audit = (
                map_helper._compose_exact_root_maps(
                    replay_arrays,
                    chronological_index=chronological_index,
                    selection_branch_id=selection_branch_id,
                    branch_ids=final_support,
                    pelvis_time_us=pelvis_time_us,
                    edge_specs=EDGE_SPECS,
                    require_contiguous_root_rows=False,
                )
            )
            discontinuities = np.flatnonzero(np.diff(root_rows) != 1) + 1
            exact_root_spans = [
                np.asarray(value, dtype=np.int64)
                for value in np.split(root_rows, discontinuities)
            ]
            if any(len(value) == 0 for value in exact_root_spans):
                raise RuntimeError(f"{action}: empty exact rooted span")
            selected_position = int(np.rint(
                selected_quantile * (len(root_rows) - 1)
            ))
            selected_root = int(root_rows[selected_position])
            selected_span_indices = [
                index for index, value in enumerate(exact_root_spans)
                if int(value[0]) <= selected_root <= int(value[-1])
                and selected_root in set(int(row) for row in value)
            ]
            if len(selected_span_indices) != 1:
                raise RuntimeError(
                    f"{action}: selected root row does not belong to exactly one "
                    "exact contiguous span"
                )
            selected_time_s = float(pelvis_time_us[selected_root]) * 1e-6

            avatar_results: list[
                tuple[str, float, list[tuple[Mapping[str, Any], Any]]]
            ] = []
            branch_rows: list[dict[str, Any]] = []
            for branch_id in final_support:
                selected_rotations: dict[str, np.ndarray] = {}
                segment_source_rows: dict[str, int] = {}
                segment_delta_rows: dict[str, float] = {}
                for segment in segments:
                    source_index = int(
                        segment_source_by_root[segment][selected_root]
                    )
                    quaternion = np.asarray(
                        arrays[
                            f"orientation/{chronological_index:02d}/"
                            f"{node_by_segment[segment]}/quat_world_sensor_wxyz"
                        ][source_index],
                        dtype=float,
                    )
                    world_from_sensor = qmt_wxyz_to_scipy_active(
                        quaternion
                    ).as_matrix()
                    segment_from_sensor = np.asarray(
                        arrays[
                            f"frames/{branch_id}/segment_from_sensor/{segment}"
                        ],
                        dtype=float,
                    )
                    if (
                        segment_from_sensor.shape != (3, 3)
                        or not np.allclose(
                            segment_from_sensor.T @ segment_from_sensor,
                            np.eye(3), atol=1e-8,
                        )
                        or np.linalg.det(segment_from_sensor) <= 0.0
                    ):
                        raise RuntimeError(f"{action}:{segment}: final frame is invalid")
                    delta = float(np.asarray(
                        arrays[
                            f"trajectories/{chronological_index:02d}/{branch_id}/"
                            f"segment_global_delta/{segment}"
                        ],
                        dtype=float,
                    )[selected_root])
                    yaw = Rotation.from_rotvec([0.0, 0.0, delta]).as_matrix()
                    selected_rotations[segment] = (
                        yaw @ world_from_sensor @ segment_from_sensor.T
                    )
                    segment_source_rows[segment] = source_index
                    segment_delta_rows[segment] = delta
                profile_results = [
                    (
                        profile,
                        direct_orientation_avatar_fk(
                            world_from_segment=selected_rotations,
                            profile=profile,
                            pelvis_gauge_position_m=np.zeros(3),
                        ),
                    )
                    for profile in profiles
                ]
                weight = float(weights[canonical_ids.index(branch_id)])
                avatar_results.append((branch_id, weight, profile_results))
                branch_rows.append({
                    "branch_id": branch_id,
                    "posterior_weight": weight,
                    "final_hard_supported": True,
                    "segment_source_rows": segment_source_rows,
                    "segment_global_delta_rad": segment_delta_rows,
                    "world_from_segment_sha256": {
                        segment: _array_sha(value)
                        for segment, value in selected_rotations.items()
                    },
                })

            figure, axes = plt.subplots(
                1, 3,
                figsize=tuple(float(value) for value in renderer["figure_size_inches"]),
                dpi=int(renderer["dpi"]),
            )
            for axis, (view_name, (horizontal_name, vertical_name)) in zip(
                axes, view_specs, strict=True,
            ):
                horizontal = coordinate[horizontal_name]
                vertical = coordinate[vertical_name]
                for branch_index, (_, _, profile_results) in enumerate(avatar_results):
                    for profile_index, (_, result) in enumerate(profile_results):
                        for line in result.line_segments_m.values():
                            axis.plot(
                                line[:, horizontal], line[:, vertical],
                                color=colors[branch_index],
                                linestyle=profile_linestyles[profile_index],
                                linewidth=float(renderer["line_width"]) * (
                                    1.0 if profile_index == 0 else 0.72
                                ),
                                alpha=profile_alphas[profile_index],
                                zorder=2 if profile_index == 0 else 1,
                            )
                    points = np.vstack(list(
                        profile_results[0][1].landmark_positions_m.values()
                    ))
                    axis.scatter(
                        points[:, horizontal], points[:, vertical],
                        s=8, color=colors[branch_index], alpha=0.72, zorder=4,
                    )
                axis.set_title(view_name)
                axis.set_xlabel(f"fresh replay-world/gauge {horizontal_name} (m)")
                axis.set_ylabel(f"fresh replay-world/gauge {vertical_name} (m)")
                axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
                axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
                axis.set_aspect("equal", adjustable="box")
                axis.grid(alpha=0.2)

            handles = [
                Line2D(
                    [0], [0], color=colors[index], linewidth=1.6,
                    label=(
                        f"{branch_id.removeprefix('HINGE_SIGN_').replace('_', ' ')}; "
                        f"w={weight:.6f}"
                    ),
                )
                for index, (branch_id, weight, _) in enumerate(avatar_results)
            ] + [
                Line2D(
                    [0], [0], color="#4b5563",
                    linestyle=profile_linestyles[index],
                    linewidth=1.3 if index == 0 else 1.0,
                    alpha=profile_alphas[index], label=label,
                )
                for index, label in enumerate(profile_labels)
            ]
            figure.legend(
                handles, [handle.get_label() for handle in handles],
                loc="lower center", bbox_to_anchor=(0.5, 0.08),
                ncol=3, fontsize=5.8, framealpha=0.92,
            )
            figure.suptitle(
                f"ATTEMPT003 RETROSPECTIVE FINAL-FROZEN VIEWER — {action}\n"
                "NOT CAUSAL PROGRESS / NOT POSE TRUTH / NOT SCIENCE PASS",
                color="#991b1b", fontsize=11.5, fontweight="bold",
            )
            figure.text(
                0.5, 0.018,
                f"registered same-action quantile={selected_quantile:.1f}; "
                f"exact rooted row={selected_root}; t={selected_time_s:.6f} s; "
                "fresh orientation/delta/final frame; no nearest/interpolation/IK/rebase/repair",
                ha="center", fontsize=8.0,
            )
            figure.tight_layout(rect=(0.01, 0.20, 0.99, 0.88))
            png = OUT / f"FRESH_RETROSPECTIVE_{chronological_index:02d}_{action}_TRIVIEW.png"
            figure.savefig(png)
            plt.close(figure)
            shape = tuple(int(value) for value in plt.imread(png).shape[:2])
            png.chmod(0o444)
            artifacts.append({
                "chronological_index": chronological_index,
                "action": action,
                "path": str(png.relative_to(WORKSPACE)),
                "sha256": _sha(png),
                "pixel_dimensions": [shape[1], shape[0]],
            })
            action_audits.append({
                "chronological_index": chronological_index,
                "action": action,
                "selection_rule": (
                    "REGISTERED_PARTIAL_SENSOR_AXIS_PROXY_SAMPLE_QUANTILE_BY_ACTION_ON_"
                    "SORTED_UNFILLED_UNION_OF_EXACT_NINE_EDGE_ROOTED_PELVIS_SPANS"
                ),
                "selection_setting_path": (
                    "scientific_renderer.partial_sensor_axis_proxy."
                    "sample_quantile_by_action"
                ),
                "selected_quantile": selected_quantile,
                "selected_common_position": selected_position,
                "selected_pelvis_source_row": selected_root,
                "selected_common_physical_time_s": selected_time_s,
                "common_exact_rooted_pelvis_rows": {
                    "count": int(len(root_rows)),
                    "first": int(root_rows[0]),
                    "last": int(root_rows[-1]),
                    "sha256": _array_sha(root_rows),
                    "contiguous_interval_count": int(
                        1 + np.count_nonzero(np.diff(root_rows) != 1)
                    ),
                    "gap_count": int(np.count_nonzero(np.diff(root_rows) != 1)),
                    "exact_contiguous_spans": [
                        {
                            "span_index": index,
                            "first": int(value[0]),
                            "last": int(value[-1]),
                            "count": int(len(value)),
                            "sha256": _array_sha(value),
                        }
                        for index, value in enumerate(exact_root_spans)
                    ],
                    "selected_span_index": selected_span_indices[0],
                    "selected_root_belongs_to_exactly_one_valid_span": True,
                    "selection_is_member_of_exact_owner_row_set": bool(
                        selected_root in set(int(value) for value in root_rows)
                    ),
                    "gap_rows_inserted_or_filled": 0,
                    "gaps_crossed_or_interpolated_for_selected_row": False,
                },
                "orientation_bridge_by_node": orientation_bridge,
                "trajectory_grid_bridge_by_branch": grid_bridge,
                "edge_exact_source_maps": edge_map_audit,
                "branch_rows": branch_rows,
            })

    audit = {
        "schema": "biospur-c2-fresh-final-frozen-retrospective-early-triview-v1",
        "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
        "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
        "immutable_source_map_bridge": {
            "replay_npz": {"path": str(REPLAY.relative_to(WORKSPACE)), "sha256": _sha(REPLAY)},
            "replay_audit": {"path": str(REPLAY_AUDIT.relative_to(WORKSPACE)), "sha256": _sha(REPLAY_AUDIT)},
            "only_old_fields_used": [
                "heading/<action>/<branch>/<edge>/<span>/common_physical_time_s",
                "heading/<action>/<branch>/<edge>/<span>/selected_parent_source_indices",
                "heading/<action>/<branch>/<edge>/<span>/selected_child_source_indices"
            ],
            "old_world_from_segment_or_delta_used": False,
        },
        "source": {"path": str(Path(__file__).resolve().relative_to(WORKSPACE)), "sha256": _sha(Path(__file__).resolve())},
        "exact_map_helper": {"path": str(MAP_HELPER.relative_to(WORKSPACE)), "sha256": _sha(MAP_HELPER)},
        "artifacts": artifacts,
        "actions": action_audits,
        "frame_source": "fresh ATTEMPT003 final frames/<branch>/segment_from_sensor",
        "frame_source_is_future_relative_to_early_actions": True,
        "product_role": "RETROSPECTIVE_FINAL_FROZEN_VIEWER_ONLY",
        "causal_progressive_metrics_modified_or_supported": False,
        "all_four_final_legal_branches_retained": True,
        "viewer_only_profiles": [
            {
                "profile_id": str(profile["profile_id"]),
                "forearm_left_m": float(profile["forearm_left_m"]),
                "forearm_right_m": float(profile["forearm_right_m"]),
                "weight": None,
            }
            for profile in profiles
        ],
        "forearm_midpoint_or_left_right_equality_hardened": False,
        "payload_read": False,
        "heldout_opened": False,
        "fit_qmt_progressive_or_frozen_state_modified": False,
        "nearest_row_interpolation_manual_pose_flip_ik_rebase_repair": False,
        "scientific_acceptance_pass": False,
    }
    audit_path = OUT / "FRESH_RETROSPECTIVE_EARLY_TRIVIEW_AUDIT.json"
    _write_new(audit_path, audit)
    print(json.dumps({
        "artifacts": artifacts,
        "audit": {"path": str(audit_path.relative_to(WORKSPACE)), "sha256": _sha(audit_path)},
        "scientific_acceptance_pass": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
