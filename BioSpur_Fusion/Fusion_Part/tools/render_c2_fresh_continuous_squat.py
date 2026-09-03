#!/usr/bin/env python3
"""Render fresh causal squat from the full persisted official-QMT trajectory."""
from __future__ import annotations

import hashlib
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
OUT = SPRINT / "C2_FRESH_CAUSAL_CONTINUOUS_SQUAT_MULTIBRANCH_TRIVIEW_006"


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


def _compose_exact_root_maps(
    replay_arrays: Mapping[str, np.ndarray],
    *,
    chronological_index: int,
    selection_branch_id: str,
    branch_ids: tuple[str, ...],
    pelvis_time_us: np.ndarray,
    edge_specs: tuple[tuple[str, str, str], ...],
    require_contiguous_root_rows: bool = True,
) -> tuple[np.ndarray, dict[str, dict[int, int]], dict[str, Any]]:
    root_by_time_us = {
        int(value): index for index, value in enumerate(pelvis_time_us)
    }
    if len(root_by_time_us) != len(pelvis_time_us):
        raise RuntimeError("fresh pelvis timer contains duplicate rows")
    segment_source_by_root: dict[str, dict[int, int]] = {
        "pelvis": {index: index for index in range(len(pelvis_time_us))},
    }
    common_root_rows = set(range(len(pelvis_time_us)))
    edge_audit: dict[str, Any] = {}
    for edge, parent, child in edge_specs:
        span_prefix = (
            f"heading/{chronological_index:02d}/{selection_branch_id}/{edge}/"
        )
        span_bases = sorted({
            name.rsplit("/", 1)[0]
            for name in replay_arrays
            if name.startswith(span_prefix)
            and name.endswith("/common_physical_time_s")
        })
        if not span_bases:
            raise RuntimeError(f"{edge}: immutable replay has no exact QMT source maps")
        child_map: dict[int, int] = {}
        parent_map = segment_source_by_root[parent]
        parent_all: list[np.ndarray] = []
        child_all: list[np.ndarray] = []
        root_all: list[np.ndarray] = []
        for span_number, base in enumerate(span_bases):
            common_time_s = np.asarray(
                replay_arrays[f"{base}/common_physical_time_s"], dtype=float,
            )
            parent_rows = np.asarray(
                replay_arrays[f"{base}/selected_parent_source_indices"],
                dtype=np.int64,
            )
            child_rows = np.asarray(
                replay_arrays[f"{base}/selected_child_source_indices"],
                dtype=np.int64,
            )
            if (
                parent_rows.shape != common_time_s.shape
                or child_rows.shape != common_time_s.shape
                or np.any(np.diff(common_time_s) <= 0.0)
                or np.any(np.diff(parent_rows) != 1)
                or np.any(np.diff(child_rows) != 1)
            ):
                raise RuntimeError(f"{edge}: immutable replay source span is invalid")
            common_time_us = np.rint(common_time_s * 1e6).astype(np.int64)
            if np.max(np.abs(
                common_time_s - common_time_us.astype(float) * 1e-6
            )) > 2e-12:
                raise RuntimeError(f"{edge}: source span is not on integer timer rows")
            root_rows = np.asarray([
                root_by_time_us.get(int(value), -1) for value in common_time_us
            ], dtype=np.int64)
            if np.any(root_rows < 0) or np.any(np.diff(root_rows) != 1):
                raise RuntimeError(f"{edge}: source span is not an exact pelvis-row map")
            for other_branch_id in branch_ids:
                other_prefix = (
                    f"heading/{chronological_index:02d}/{other_branch_id}/{edge}/"
                )
                other_bases = sorted({
                    name.rsplit("/", 1)[0]
                    for name in replay_arrays
                    if name.startswith(other_prefix)
                    and name.endswith("/common_physical_time_s")
                })
                if len(other_bases) != len(span_bases):
                    raise RuntimeError(f"{edge}: branch source-span closure differs")
                other_base = other_bases[span_number]
                for field, expected in (
                    ("common_physical_time_s", common_time_s),
                    ("selected_parent_source_indices", parent_rows),
                    ("selected_child_source_indices", child_rows),
                ):
                    if not np.array_equal(
                        np.asarray(replay_arrays[f"{other_base}/{field}"]), expected,
                    ):
                        raise RuntimeError(f"{edge}: branch source maps differ")
            for root, parent_source, child_source in zip(
                root_rows, parent_rows, child_rows, strict=True,
            ):
                if parent_map.get(int(root)) != int(parent_source):
                    raise RuntimeError(
                        f"{edge}: rooted parent index differs from source map"
                    )
                previous = child_map.setdefault(int(root), int(child_source))
                if previous != int(child_source):
                    raise RuntimeError(f"{edge}: overlapping source spans disagree")
            parent_all.append(parent_rows)
            child_all.append(child_rows)
            root_all.append(root_rows)
        if not child_map:
            raise RuntimeError(f"{edge}: exact rooted source-map composition is empty")
        segment_source_by_root[child] = child_map
        common_root_rows.intersection_update(child_map)
        parent_concat = np.concatenate(parent_all)
        child_concat = np.concatenate(child_all)
        root_concat = np.concatenate(root_all)
        edge_audit[edge] = {
            "official_qmt_span_count": len(span_bases),
            "selected_parent_source_indices_sha256": _array_sha(parent_concat),
            "selected_child_source_indices_sha256": _array_sha(child_concat),
            "pelvis_source_indices_sha256": _array_sha(root_concat),
            "selected_row_count": int(len(parent_concat)),
            "composed_root_row_count": int(len(child_map)),
            "all_four_branch_source_maps_exact_equal": True,
            "nearest_row_or_interpolation_used": False,
            "gap_or_span_boundary_crossed": False,
        }
    root_rows = np.asarray(sorted(common_root_rows), dtype=np.int64)
    if len(root_rows) < 3 or (
        require_contiguous_root_rows and np.any(np.diff(root_rows) != 1)
    ):
        raise RuntimeError("fresh continuous squat lacks a contiguous exact nine-edge root map")
    return root_rows, segment_source_by_root, edge_audit


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh continuous viewer requires canonical Fusion_Part")

    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.quaternion_contract import (
        qmt_wxyz_to_scipy_active,
    )
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        direct_orientation_avatar_fk,
        landmark_proxy_sensitivity_profiles,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(AMENDMENT.read_text(encoding="utf-8"))["effective_settings"]
    if (
        manifest["fresh_verification"].get("pass") is not True
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("fresh continuous viewer authority is inconsistent")
    actions = tuple(settings["execution_contract"]["chronological_actions"])
    chronological_index = 15
    action = actions[chronological_index]
    if action != "16_squat":
        raise RuntimeError("registered squat chronology changed")
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    if set(node_by_segment) != set(segments):
        raise RuntimeError("fresh continuous viewer hardware/segment identity is incomplete")
    nodes = tuple(sorted(node_by_segment.values()))
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    renderer = settings["scientific_renderer"]
    support = manifest["structure"]["physical_trajectory_support"][str(chronological_index)]
    branch_ids = tuple(sorted(support))
    if len(branch_ids) != 4 or not all(support[value]["physically_legal"] for value in branch_ids):
        raise RuntimeError("fresh squat requires exactly four individually legal branches")
    canonical_ids = tuple(
        row["branch_id"] for row in manifest["structure"]["frame_branches"]
    )

    OUT.mkdir(parents=True, exist_ok=False)
    with (
        np.load(NPZ, allow_pickle=False) as arrays,
        np.load(REPLAY, allow_pickle=False) as replay_arrays,
    ):
        orientation_bridge_rows: dict[str, Any] = {}
        for node in nodes:
            fresh_time = np.asarray(
                arrays[f"orientation/{chronological_index:02d}/{node}/time_us"],
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
                raise RuntimeError(f"{node}: replay/fresh time bridge differs")
            if not np.array_equal(fresh_quaternion, replay_quaternion):
                raise RuntimeError(f"{node}: replay/fresh quaternion bridge differs")
            orientation_bridge_rows[node] = {
                "time_us_sha256": _array_sha(fresh_time),
                "quat_world_sensor_wxyz_sha256": _array_sha(fresh_quaternion),
                "row_count": int(len(fresh_time)),
                "fresh_vs_replay_exact_equal": True,
            }
        pelvis_node = node_by_segment["pelvis"]
        pelvis_time_us = np.asarray(
            arrays[f"orientation/{chronological_index:02d}/{pelvis_node}/time_us"],
            dtype=np.int64,
        )
        weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        selection_branch_id = max(
            branch_ids,
            key=lambda value: (float(weights[canonical_ids.index(value)]), value),
        )
        trajectory_grid_bridge: dict[str, Any] = {}
        for branch_id in branch_ids:
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
                raise RuntimeError(f"{branch_id}: replay/fresh common grid differs")
            trajectory_grid_bridge[branch_id] = {
                "common_physical_time_s_sha256": _array_sha(fresh_grid),
                "row_count": int(len(fresh_grid)),
                "fresh_vs_replay_exact_equal": True,
            }
        root_rows, segment_source_by_root, edge_map_audit = _compose_exact_root_maps(
            replay_arrays,
            chronological_index=chronological_index,
            selection_branch_id=selection_branch_id,
            branch_ids=branch_ids,
            pelvis_time_us=pelvis_time_us,
            edge_specs=EDGE_SPECS,
        )
        common_time_s = pelvis_time_us[root_rows].astype(float) * 1e-6

        def world_from_segment(branch_id: str, segment: str) -> np.ndarray:
            source_indices = np.asarray([
                segment_source_by_root[segment][int(root)] for root in root_rows
            ], dtype=np.int64)
            quaternion = np.asarray(
                arrays[
                    f"orientation/{chronological_index:02d}/"
                    f"{node_by_segment[segment]}/quat_world_sensor_wxyz"
                ][source_indices],
                dtype=float,
            )
            world_from_sensor = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
            segment_from_sensor = np.asarray(
                arrays[
                    f"physical_trajectory/{chronological_index}/{branch_id}/"
                    f"segment_from_sensor/{segment}"
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
                raise RuntimeError(f"{segment}: frozen frame is not proper SO(3)")
            raw_world_from_segment = np.einsum(
                "nij,jk->nik", world_from_sensor, segment_from_sensor.T,
            )
            trajectory_prefix = f"trajectories/{chronological_index:02d}/{branch_id}"
            trajectory_time_s = np.asarray(
                arrays[f"{trajectory_prefix}/common_physical_time_s"], dtype=float,
            )
            if (
                trajectory_time_s.shape != pelvis_time_us.shape
                or np.max(np.abs(
                    trajectory_time_s - pelvis_time_us.astype(float) * 1e-6
                )) > 2e-12
            ):
                raise RuntimeError("fresh rooted trajectory differs from exact pelvis grid")
            delta = np.asarray(
                arrays[f"{trajectory_prefix}/segment_global_delta/{segment}"],
                dtype=float,
            )[root_rows]
            yaw = Rotation.from_rotvec(np.column_stack((
                np.zeros(len(delta)), np.zeros(len(delta)), delta,
            ))).as_matrix()
            return np.einsum("nij,njk->nik", yaw, raw_world_from_segment)

        rotations_by_branch = {
            branch_id: {
                segment: world_from_segment(branch_id, segment)
                for segment in segments
            }
            for branch_id in branch_ids
        }

        # Like-for-like regression against every already persisted physical
        # checkpoint prevents a transpose, clock-map, or heading-state adapter drift.
        comparison_count = 0
        maximum_checkpoint_rotation_error = 0.0
        checkpoint_rotation_error_by_segment = {
            segment: 0.0 for segment in segments
        }
        for branch_id in branch_ids:
            checkpoint_prefix = f"physical_trajectory/{chronological_index}/{branch_id}"
            checkpoint_time = np.asarray(
                arrays[f"{checkpoint_prefix}/common_physical_time_s"], dtype=float,
            )
            for checkpoint_index, value in enumerate(checkpoint_time):
                matches = np.flatnonzero(np.abs(common_time_s - value) <= 2e-12)
                if len(matches) != 1:
                    raise RuntimeError("fresh physical checkpoint is absent from exact full grid")
                position = int(matches[0])
                for segment in segments:
                    expected = np.asarray(
                        arrays[f"{checkpoint_prefix}/world_from_segment/{segment}"][
                            checkpoint_index
                        ],
                        dtype=float,
                    )
                    error = float(np.max(np.abs(
                        rotations_by_branch[branch_id][segment][position] - expected
                    )))
                    maximum_checkpoint_rotation_error = max(
                        maximum_checkpoint_rotation_error, error,
                    )
                    checkpoint_rotation_error_by_segment[segment] = max(
                        checkpoint_rotation_error_by_segment[segment], error,
                    )
                    comparison_count += 1
        if maximum_checkpoint_rotation_error > 2e-12:
            raise RuntimeError(
                "fresh causal continuous adapter differs from prefix15 owner checkpoints"
            )

        proximal_to_distal = np.asarray([0.0, 0.0, -1.0], dtype=float)

        def knee_flexion_deg(proximal: np.ndarray, distal: np.ndarray) -> np.ndarray:
            proximal_axis = np.einsum("nij,j->ni", proximal, proximal_to_distal)
            distal_axis = np.einsum("nij,j->ni", distal, proximal_to_distal)
            cosine = np.sum(proximal_axis * distal_axis, axis=1)
            return np.rad2deg(np.arccos(np.clip(cosine, -1.0, 1.0)))

        selection_rotations = rotations_by_branch[selection_branch_id]
        left_flexion_deg = knee_flexion_deg(
            selection_rotations["thigh_left"], selection_rotations["shank_left"],
        )
        right_flexion_deg = knee_flexion_deg(
            selection_rotations["thigh_right"], selection_rotations["shank_right"],
        )
        bilateral_score_deg = np.minimum(left_flexion_deg, right_flexion_deg)
        selected_position = int(np.argmax(bilateral_score_deg))
        selected_root = int(root_rows[selected_position])
        selected_time_s = float(common_time_s[selected_position])

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
        avatar_results: list[tuple[str, float, list[tuple[Mapping[str, Any], Any]]]] = []
        branch_rows: list[dict[str, Any]] = []
        for branch_id in branch_ids:
            selected_rotations = {
                segment: value[selected_position]
                for segment, value in rotations_by_branch[branch_id].items()
            }
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
                "physically_legal": True,
                "world_from_segment_selected_sha256_by_segment": {
                    segment: _array_sha(selected_rotations[segment])
                    for segment in segments
                },
            })

        view_specs = (
            ("FRONT", tuple(renderer["front_axes"])),
            ("SIDE", tuple(renderer["side_axes"])),
            ("TOP", tuple(renderer["top_axes"])),
        )
        coordinate = {"x": 0, "y": 1, "z": 2}
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
                points = np.vstack(
                    list(profile_results[0][1].landmark_positions_m.values())
                )
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
                [0], [0], color="#4b5563", linestyle=profile_linestyles[index],
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
            "ATTEMPT003 FRESH CAUSAL PREFIX15 FRAME — 16_squat FULL QMT ROW\n"
            "EXACT OWNER MATRICES / NOT POSE OR SCIENCE PASS",
            color="#991b1b", fontsize=11.5, fontweight="bold",
        )
        figure.text(
            0.5, 0.018,
            f"ARGMAX min(L,R knee flexion), earliest tie; t={selected_time_s:.6f} s; "
            f"L={left_flexion_deg[selected_position]:.2f}°, "
            f"R={right_flexion_deg[selected_position]:.2f}°; "
            "exact rooted maps; no nearest/interpolation/IK/rebase/repair/profile weights",
            ha="center", fontsize=8.0,
        )
        figure.tight_layout(rect=(0.01, 0.20, 0.99, 0.88))
        png = OUT / "FRESH_CAUSAL_15_16_squat_FULL_QMT_BILATERAL_FLEXION_TRIVIEW.png"
        figure.savefig(png)
        plt.close(figure)
        image_shape = tuple(int(value) for value in plt.imread(png).shape[:2])
        png.chmod(0o444)

        audit = {
            "schema": "biospur-c2-fresh-causal-continuous-squat-viewer-selection-v2",
            "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
            "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
            "immutable_source_map_bridge": {
                "replay_npz": {
                    "path": str(REPLAY.relative_to(WORKSPACE)),
                    "sha256": _sha(REPLAY),
                },
                "replay_audit": {
                    "path": str(REPLAY_AUDIT.relative_to(WORKSPACE)),
                    "sha256": _sha(REPLAY_AUDIT),
                },
                "fresh_vs_replay_orientation_action15_by_node": orientation_bridge_rows,
                "fresh_vs_replay_full_common_grid_by_branch": trajectory_grid_bridge,
                "only_reused_fields": [
                    "heading/15/<branch>/<edge>/<span>/common_physical_time_s",
                    "heading/15/<branch>/<edge>/<span>/selected_parent_source_indices",
                    "heading/15/<branch>/<edge>/<span>/selected_child_source_indices"
                ],
                "old_world_from_segment_reused": False,
                "old_physical_checkpoint_reused": False,
                "old_segment_global_delta_reused": False,
                "fresh_prefix15_frame_and_fresh_segment_global_delta_required": True,
                "qmt_pair_clock_or_fit_rerun": False,
                "bridge_rejected_on_any_time_quaternion_or_grid_difference": True
            },
            "source": {"path": str(Path(__file__).resolve().relative_to(WORKSPACE)), "sha256": _sha(Path(__file__).resolve())},
            "artifact": {
                "path": str(png.relative_to(WORKSPACE)),
                "sha256": _sha(png),
                "pixel_dimensions": [image_shape[1], image_shape[0]],
            },
            "chronological_index": chronological_index,
            "action": action,
            "selection_rule": (
                "ARGMAX_MIN_LEFT_RIGHT_KNEE_FLEXION_DEG_ON_EXACT_NINE_EDGE_"
                "OFFICIAL_QMT_ROOTED_PELVIS_ROWS;EARLIEST_TIE"
            ),
            "selection_branch_id": selection_branch_id,
            "selection_branch_used_only_for_nonvisual_timestamp_metric": True,
            "all_four_legal_branches_rendered_at_same_timestamp": True,
            "selected_common_position": selected_position,
            "selected_pelvis_source_row": selected_root,
            "selected_common_physical_time_s": selected_time_s,
            "selected_action_fraction_on_full_pelvis_grid": (
                selected_root / float(len(pelvis_time_us) - 1)
            ),
            "selected_left_knee_flexion_deg": float(left_flexion_deg[selected_position]),
            "selected_right_knee_flexion_deg": float(right_flexion_deg[selected_position]),
            "bilateral_minimum_flexion_p90_deg": float(np.quantile(bilateral_score_deg, 0.9)),
            "left_knee_maximum_flexion_deg": float(np.max(left_flexion_deg)),
            "right_knee_maximum_flexion_deg": float(np.max(right_flexion_deg)),
            "common_exact_rooted_pelvis_rows": {
                "count": int(len(root_rows)),
                "sha256": _array_sha(root_rows),
                "first": int(root_rows[0]),
                "last": int(root_rows[-1]),
            },
            "edge_exact_source_maps": edge_map_audit,
            "full_grid_formula": (
                "world_from_segment = Rz(official rooted segment_global_delta) @ "
                "world_from_sensor(quaternion) @ segment_from_sensor.T"
            ),
            "limb_longitudinal_axis_semantics": (
                "SEGMENT_MINUS_Z_IS_PROXIMAL_TO_DISTAL;NO_MANUAL_SIGN_FLIP"
            ),
            "checkpoint_equivalence": {
                "comparison_count": comparison_count,
                "maximum_absolute_rotation_matrix_error": maximum_checkpoint_rotation_error,
                "maximum_absolute_rotation_matrix_error_by_segment": checkpoint_rotation_error_by_segment,
                "frame_source": "physical_trajectory/15/<branch>/segment_from_sensor/<segment>",
                "all_segments_pass": maximum_checkpoint_rotation_error <= 2e-12,
                "future_prefix_frame_backflow_used": False,
            },
            "branch_rows": branch_rows,
            "viewer_only_anthropometric_profiles": [
                {
                    "profile_id": str(profile["profile_id"]),
                    "forearm_left_m": float(profile["forearm_left_m"]),
                    "forearm_right_m": float(profile["forearm_right_m"]),
                    "weight": None,
                }
                for profile in profiles
            ],
            "forearm_dual_observer_midpoint_or_equality_hardened": False,
            "nearest_row_or_interpolation_used": False,
            "postfreeze_final_frame_viewer": False,
            "causal_prefix15_owner_arrays_used": True,
            "payload_read": False,
            "heldout_opened": False,
            "fit_qmt_progressive_or_frozen_state_modified": False,
            "manual_pose_flip_ik_rebase_retarget_or_repair": False,
            "scientific_acceptance_pass": False,
        }
        audit_path = OUT / "FRESH_CAUSAL_CONTINUOUS_SQUAT_TRIVIEW_AUDIT.json"
        _write_new(audit_path, audit)
        print(json.dumps({
            "artifact": audit["artifact"],
            "audit": {"path": str(audit_path.relative_to(WORKSPACE)), "sha256": _sha(audit_path)},
            "selected_left_knee_flexion_deg": audit["selected_left_knee_flexion_deg"],
            "selected_right_knee_flexion_deg": audit["selected_right_knee_flexion_deg"],
            "common_exact_rooted_pelvis_row_count": len(root_rows),
            "checkpoint_equivalence": audit["checkpoint_equivalence"],
            "scientific_acceptance_pass": False,
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
