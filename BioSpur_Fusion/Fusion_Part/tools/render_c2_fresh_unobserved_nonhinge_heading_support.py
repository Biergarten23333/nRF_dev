#!/usr/bin/env python3
"""Render factorized full-circle support for unobserved nonhinge headings."""

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

import render_c2_fresh_distal_longitudinal_support as frozen_viewer


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
REPLAY = (
    SPRINT / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002"
    / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
)
R006_AUDIT = (
    SPRINT / "C2_FRESH_CAUSAL_CONTINUOUS_SQUAT_MULTIBRANCH_TRIVIEW_006"
    / "FRESH_CAUSAL_CONTINUOUS_SQUAT_TRIVIEW_AUDIT.json"
)
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
AUTHORITY = RUN / "USER_UNOBSERVED_NONHINGE_HEADING_SUPPORT_AMENDMENT_005.json"
OUT = SPRINT / "C2_FRESH_UNOBSERVED_NONHINGE_HEADING_SUPPORT_TRIVIEWS_002"

DOWNSTREAM_SEGMENTS = {
    "pelvis_torso": ("torso", "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right"),
    "shoulder_left": ("upper_arm_left", "forearm_left"),
    "shoulder_right": ("upper_arm_right", "forearm_right"),
    "hip_left": ("thigh_left", "shank_left"),
    "hip_right": ("thigh_right", "shank_right"),
}
AFFECTED_CORE_LINES = {
    "pelvis_torso": ("spine", "shoulder_crossbar", "upper_arm_left", "upper_arm_right"),
    "shoulder_left": ("upper_arm_left",),
    "shoulder_right": ("upper_arm_right",),
    "hip_left": ("thigh_left",),
    "hip_right": ("thigh_right",),
}
EDGE_COLORS = {
    "pelvis_torso": "#7c3aed",
    "shoulder_left": "#2563eb",
    "shoulder_right": "#0891b2",
    "hip_left": "#dc2626",
    "hip_right": "#f97316",
}
CORE_LINES = {
    "spine", "shoulder_crossbar", "hip_crossbar",
    "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _selected_world_from_segment(
    arrays: Mapping[str, np.ndarray],
    replay: Mapping[str, np.ndarray],
    *,
    chronological_index: int,
    branch_id: str,
    branch_ids: tuple[str, ...],
    selected_root_from_squat_audit: int,
    node_by_segment: Mapping[str, str],
    segments: tuple[str, ...],
    edge_specs: tuple[tuple[str, str, str], ...],
) -> tuple[dict[str, np.ndarray], int, int, float, Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.quaternion_contract import (
        qmt_wxyz_to_scipy_active,
    )

    frame_prefix = frozen_viewer._frame_prefix(chronological_index, branch_id)
    old_frames = {
        segment: np.asarray(
            arrays[f"{frame_prefix}/segment_from_sensor/{segment}"], dtype=float,
        )
        for segment in segments
    }
    if chronological_index in (0, 15):
        pelvis_time = np.asarray(
            arrays[
                f"orientation/{chronological_index:02d}/{node_by_segment['pelvis']}/time_us"
            ], dtype=np.int64,
        )
        root_rows, source_by_root, map_audit = frozen_viewer._load_map_helper()._compose_exact_root_maps(
            replay,
            chronological_index=chronological_index,
            selection_branch_id=branch_id,
            branch_ids=branch_ids,
            pelvis_time_us=pelvis_time,
            edge_specs=edge_specs,
            require_contiguous_root_rows=(chronological_index == 15),
        )
        selected_root = (
            int(root_rows[0]) if chronological_index == 0
            else int(selected_root_from_squat_audit)
        )
        if selected_root not in set(int(value) for value in root_rows):
            raise RuntimeError("registered selected row left exact rooted support")
        trajectory_row = selected_root
        world: dict[str, np.ndarray] = {}
        for segment in segments:
            source_index = int(source_by_root[segment][selected_root])
            quaternion = np.asarray(
                arrays[
                    f"orientation/{chronological_index:02d}/{node_by_segment[segment]}/"
                    "quat_world_sensor_wxyz"
                ][source_index], dtype=float,
            )
            world_from_sensor = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
            delta = float(np.asarray(
                arrays[
                    f"trajectories/{chronological_index:02d}/{branch_id}/"
                    f"segment_global_delta/{segment}"
                ], dtype=float,
            )[trajectory_row])
            yaw = Rotation.from_rotvec([0.0, 0.0, delta]).as_matrix()
            world[segment] = yaw @ world_from_sensor @ old_frames[segment].T
        return (
            world, selected_root, trajectory_row,
            float(pelvis_time[selected_root]) * 1e-6, map_audit,
        )

    common_time = np.asarray(
        arrays[f"{frame_prefix}/common_physical_time_s"], dtype=float,
    )
    sample_index = len(common_time) - 1
    selected_time = float(common_time[sample_index])
    trajectory_time = np.asarray(
        arrays[f"trajectories/{chronological_index:02d}/{branch_id}/common_physical_time_s"],
        dtype=float,
    )
    matches = np.flatnonzero(trajectory_time == selected_time)
    if len(matches) != 1:
        raise RuntimeError("final checkpoint time does not map exactly to one trajectory row")
    return (
        {
            segment: np.asarray(
                arrays[f"{frame_prefix}/world_from_segment/{segment}"][sample_index],
                dtype=float,
            )
            for segment in segments
        },
        sample_index,
        int(matches[0]),
        selected_time,
        {"source": "EXACT_CAUSAL_PREFIX16_CHECKPOINT_TIME_MATCH", "match_count": 1},
    )


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("nonhinge support viewer requires canonical Fusion_Part")

    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.heading import (
        NONHINGE_EDGES,
        _factorized_unobserved_nonhinge_heading_support,
    )
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_sensitivity_profiles,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))["effective_settings"]
    authority = json.loads(AUTHORITY.read_text(encoding="utf-8"))
    r006 = json.loads(R006_AUDIT.read_text(encoding="utf-8"))
    if (
        manifest["fresh_verification"].get("pass") is not True
        or manifest.get("heldout_opened") is not False
        or authority["scientific_acceptance_pass"] is not False
    ):
        raise RuntimeError("unobserved nonhinge viewer authority is inconsistent")
    profile = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])[0]
    renderer = settings["scientific_renderer"]
    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    branch_rows = {
        str(row["branch_id"]): row for row in manifest["structure"]["frame_branches"]
    }
    npz_before = _sha(NPZ)
    replay_before = _sha(REPLAY)
    OUT.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []
    action_audit: list[dict[str, Any]] = []
    actions = (
        (0, "00_initial_still", "RETROSPECTIVE_FINAL_FROZEN"),
        (15, "16_squat", "CAUSAL_PREFIX15_DERIVED"),
        (16, "17_final_still", "CAUSAL_PREFIX16_DERIVED"),
    )
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}

    with np.load(NPZ, allow_pickle=False) as arrays, np.load(REPLAY, allow_pickle=False) as replay:
        frozen_weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        branch_order = tuple(branch_rows)
        weights = {
            branch_id: float(frozen_weights[branch_order.index(branch_id)])
            for branch_id in branch_order
        }
        for chronological_index, action, role in actions:
            support_index = 18 if chronological_index == 0 else chronological_index
            legal = manifest["structure"]["physical_trajectory_support"][str(support_index)]
            branch_ids = tuple(sorted(
                branch_id for branch_id, row in legal.items()
                if row.get("physically_legal") is True
            ))
            if len(branch_ids) != 4:
                raise RuntimeError(f"{action}: exact four retained branches changed")
            figure, axes = plt.subplots(
                4, 3, figsize=(22.5, 16.8), dpi=int(renderer["dpi"]), squeeze=False,
            )
            per_branch: list[dict[str, Any]] = []
            selected_time_reference: float | None = None
            for branch_row_index, branch_id in enumerate(
                sorted(branch_ids, key=lambda value: (-weights[value], value))
            ):
                world, selected_row, trajectory_row, selected_time, map_audit = (
                    _selected_world_from_segment(
                        arrays, replay,
                        chronological_index=chronological_index,
                        branch_id=branch_id,
                        branch_ids=branch_ids,
                        selected_root_from_squat_audit=int(r006["selected_pelvis_source_row"]),
                        node_by_segment=node_by_segment,
                        segments=segments,
                        edge_specs=EDGE_SPECS,
                    )
                )
                if selected_time_reference is None:
                    selected_time_reference = selected_time
                elif not np.isclose(selected_time_reference, selected_time, atol=0.0, rtol=0.0):
                    raise RuntimeError("branch rows selected different physical times")
                frame_prefix = frozen_viewer._frame_prefix(chronological_index, branch_id)
                frames = {
                    segment: np.asarray(
                        arrays[f"{frame_prefix}/segment_from_sensor/{segment}"], dtype=float,
                    )
                    for segment in segments
                }
                connections = frozen_viewer._connections(arrays, frame_prefix)
                base = frozen_viewer._fk_result(
                    heading_world_from_sensor={
                        segment: world[segment] @ frames[segment]
                        for segment in segments
                    },
                    sensor_from_segment={segment: frames[segment].T for segment in segments},
                    connections=connections,
                    profile=profile,
                )
                edge_state: dict[tuple[str, str], dict[str, float | int]] = {}
                direct_span_common_grid_rows_by_edge: dict[str, int] = {}
                for edge in NONHINGE_EDGES:
                    edge_delta = np.asarray(
                        arrays[
                            f"trajectories/{chronological_index:02d}/{branch_id}/edge_delta/{edge}"
                        ], dtype=float,
                    )
                    edge_variance = np.asarray(
                        arrays[
                            f"trajectories/{chronological_index:02d}/{branch_id}/edge_variance/{edge}"
                        ], dtype=float,
                    )
                    edge_observed = np.asarray(
                        arrays[
                            f"trajectories/{chronological_index:02d}/{branch_id}/edge_observed/{edge}"
                        ], dtype=bool,
                    )
                    frozen_state = np.asarray(
                        arrays[f"frozen/heading_edge_state/{branch_id}:{edge}"],
                        dtype=float,
                    )
                    if frozen_state.shape != (4,):
                        raise RuntimeError("frozen heading edge state layout changed")
                    effective_observation_count = int(np.rint(frozen_state[3]))
                    direct_span_common_grid_rows_by_edge[edge] = int(
                        np.count_nonzero(edge_observed)
                    )
                    edge_state[(branch_id, edge)] = {
                        "delta_rad": float(edge_delta[trajectory_row]),
                        "variance_rad2": float(edge_variance[trajectory_row]),
                        "span_count": 1,
                        "observation_count": effective_observation_count,
                    }
                support = _factorized_unobserved_nonhinge_heading_support(
                    edge_state, branch_id=branch_id,
                )
                if set(support) != set(NONHINGE_EDGES):
                    raise RuntimeError("stored action unexpectedly contains a nonhinge heading observation")
                candidate_lines: dict[str, list[dict[str, np.ndarray]]] = {}
                for edge, candidates in support.items():
                    candidate_lines[edge] = []
                    for candidate in candidates:
                        yaw = Rotation.from_rotvec([
                            0.0, 0.0,
                            float(candidate["offset_from_carried_coordinate_rad"]),
                        ]).as_matrix()
                        candidate_world = {
                            segment: (
                                yaw @ value
                                if segment in DOWNSTREAM_SEGMENTS[edge]
                                else value.copy()
                            )
                            for segment, value in world.items()
                        }
                        result = frozen_viewer._fk_result(
                            heading_world_from_sensor={
                                segment: candidate_world[segment] @ frames[segment]
                                for segment in segments
                            },
                            sensor_from_segment={segment: frames[segment].T for segment in segments},
                            connections=connections,
                            profile=profile,
                        )
                        candidate_lines[edge].append(frozen_viewer._line_map(result))
                base_lines = frozen_viewer._line_map(base)
                for view_column, (view_name, (horizontal_name, vertical_name)) in enumerate(view_specs):
                    axis = axes[branch_row_index, view_column]
                    horizontal = coordinate[horizontal_name]
                    vertical = coordinate[vertical_name]
                    for line_name in CORE_LINES:
                        line = base_lines[line_name]
                        axis.plot(
                            line[:, horizontal], line[:, vertical],
                            color="#111827", linewidth=1.0, alpha=0.58,
                            linestyle="--", zorder=2,
                        )
                    for edge, candidates in support.items():
                        for candidate_index, candidate in enumerate(candidates):
                            for line_name in AFFECTED_CORE_LINES[edge]:
                                line = candidate_lines[edge][candidate_index][line_name]
                                axis.plot(
                                    line[:, horizontal], line[:, vertical],
                                    color=EDGE_COLORS[edge], linewidth=0.55,
                                    alpha=0.18 + 0.42 * float(
                                        candidate["normalized_factorized_weight"]
                                    ) * len(candidates),
                                    zorder=3,
                                )
                    joints = np.vstack(list(base.shared_joint_positions_m.values()))
                    sensors = np.vstack([
                        value for name, value in base.segment_sensor_positions_m.items()
                        if name not in ("forearm_left", "forearm_right", "shank_left", "shank_right")
                    ])
                    axis.scatter(
                        joints[:, horizontal], joints[:, vertical],
                        marker="o", s=10, color="#1d4ed8", zorder=5,
                    )
                    axis.scatter(
                        sensors[:, horizontal], sensors[:, vertical],
                        marker="s", s=10, color="#b91c1c", zorder=5,
                    )
                    axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
                    axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
                    axis.set_aspect("equal", adjustable="box")
                    axis.grid(alpha=0.18)
                    if branch_row_index == 0:
                        axis.set_title(view_name, fontsize=10)
                    axis.set_xlabel(f"replay-world/gauge {horizontal_name} (m)", fontsize=7)
                    axis.set_ylabel(f"replay-world/gauge {vertical_name} (m)", fontsize=7)
                    if view_column == 0:
                        short = branch_id.removeprefix("HINGE_SIGN_").replace("_", " ")
                        axis.text(
                            0.01, 0.98, f"{short}\nw={weights[branch_id]:.6f}",
                            transform=axis.transAxes, va="top", ha="left", fontsize=6.4,
                            bbox={"facecolor": "white", "alpha": 0.78, "edgecolor": "none"},
                        )
                per_branch.append({
                    "branch_id": branch_id,
                    "posterior_weight": weights[branch_id],
                    "selected_source_or_checkpoint_row": selected_row,
                    "selected_trajectory_row": trajectory_row,
                    "selected_time_s": selected_time,
                    "exact_map_audit": map_audit,
                    "factorized_unobserved_nonhinge_support": support,
                    "direct_span_common_grid_rows_by_edge": (
                        direct_span_common_grid_rows_by_edge
                    ),
                    "direct_span_rows_are_effective_information": False,
                })
            legend = [
                Line2D([0], [0], color="#111827", linestyle="--", label="carried-coordinate core (not a pose estimate)"),
                *[
                    Line2D([0], [0], color=EDGE_COLORS[edge], label=f"{edge} 8-point heading S1")
                    for edge in NONHINGE_EDGES
                ],
                Line2D([0], [0], marker="o", color="#1d4ed8", linestyle="None", label="functional joint"),
                Line2D([0], [0], marker="s", color="#b91c1c", linestyle="None", label="surface sensor origin"),
            ]
            figure.legend(
                legend, [handle.get_label() for handle in legend], loc="lower center",
                bbox_to_anchor=(0.5, 0.058), ncol=4, fontsize=7.2, framealpha=0.94,
            )
            role_title = (
                "RETROSPECTIVE FINAL-FROZEN VIEWER / NOT CAUSAL PROGRESS"
                if chronological_index == 0 else
                f"{role.replace('_', ' ')} / DERIVED OWNER SUPPORT"
            )
            figure.suptitle(
                f"ATTEMPT003 {action} — UNOBSERVED NONHINGE HEADING FACTORIZED S1 SUPPORT\n"
                f"{role_title} / FULL-BODY LEGALITY UNKNOWN / NOT POSE TRUTH / NOT SCIENCE PASS",
                color="#991b1b", fontsize=13, fontweight="bold",
            )
            figure.text(
                0.5, 0.018,
                f"t={selected_time_reference:.6f} s; five nonhinge edges each retain 8 uniform full-circle candidates; "
                "no 8^5 Cartesian pose, no carried-zero lock; observed elbow/knee QMT remains separate\n"
                "distal wrist/ankle tuned lines disabled; Observer-A profile used only to isolate heading support; "
                "no pixel selection, imported ROM, IK/rebase/repair, payload or heldout",
                ha="center", fontsize=8.2,
            )
            figure.tight_layout(rect=(0.01, 0.12, 0.99, 0.92))
            png = OUT / f"FRESH_{chronological_index:02d}_{action}_NONHINGE_S1_SUPPORT_TRIVIEW.png"
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
            action_audit.append({
                "chronological_index": chronological_index,
                "action": action,
                "role": role,
                "selected_time_s": selected_time_reference,
                "branch_rows": per_branch,
            })

    if _sha(NPZ) != npz_before or _sha(REPLAY) != replay_before:
        raise RuntimeError("immutable frozen arrays changed during derived rendering")
    audit = {
        "schema": "biospur-c2-fresh-unobserved-nonhinge-heading-support-render-v1",
        "authority": {"path": str(AUTHORITY.relative_to(WORKSPACE)), "sha256": _sha(AUTHORITY)},
        "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
        "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": npz_before},
        "replay_exact_source_maps": {"path": str(REPLAY.relative_to(WORKSPACE)), "sha256": replay_before},
        "source": {"path": str(Path(__file__).resolve().relative_to(WORKSPACE)), "sha256": _sha(Path(__file__).resolve())},
        "owner_source_hashes": {
            path: _sha(WORKSPACE / path)
            for path in (
                "src/biospur_fusion/v0/c2_progressive/heading.py",
                "src/biospur_fusion/v0/c2_progressive/pipeline_runtime.py",
                "tests/v0/test_c2_p2_prefit_owners.py",
            )
        },
        "actions": action_audit,
        "artifacts": artifacts,
        "all_four_hinge_sign_branches_preserved": True,
        "five_unobserved_nonhinge_edges": list(NONHINGE_EDGES),
        "eight_uniform_positive_candidates_per_edge": True,
        "factorized_not_global_8_power_5_pose_grid": True,
        "carried_zero_heading_presented_as_evidence": False,
        "observed_hinge_qmt_erased_or_recomputed": False,
        "distal_tuned_endpoint_lines_rendered": False,
        "fit_qmt_or_19_prefix_rerun": False,
        "payload_or_heldout_access": False,
        "causal_progressive_metrics_modified": False,
        "scientific_acceptance_pass": False,
        "tuned_human_pose_pass": False,
    }
    audit_path = OUT / "AUDIT.json"
    _write_new(audit_path, audit)
    print(json.dumps({
        "audit": str(audit_path),
        "audit_sha256": _sha(audit_path),
        "artifacts": artifacts,
        "scientific_acceptance_pass": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
