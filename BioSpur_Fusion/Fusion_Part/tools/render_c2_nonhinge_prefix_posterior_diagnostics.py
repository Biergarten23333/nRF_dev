#!/usr/bin/env python3
"""Render five prefix-local, factorized nonhinge S1 diagnostics.

The official hinge-QMT checkpoint matrices are only a coordinate scaffold.
Every displayed nonhinge edge consumes its corrected prefix-local full-circle
distribution. No circular mean, argmax, Cartesian 360**5 pose, or final-prefix
heading posterior is rendered.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib

matplotlib.use("Agg", force=True)

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np
from scipy.spatial.transform import Rotation

import render_c2_fresh_distal_longitudinal_support as frozen_viewer


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SOURCE_REPLAY = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001"
SOURCE_NPZ = SOURCE_REPLAY / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
CORRECTED = SPRINT / "C2_NONHINGE_PREFIX_CORRECTED_REPLAY_001"
CORRECTED_NPZ = CORRECTED / "CORRECTED_PREFIX_NONHINGE_STATE.npz"
FRESH_MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
SETTINGS_PATH = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
OUT = SPRINT / "C2_NONHINGE_PREFIX_POSTERIOR_TRIVIEWS_002"

NONHINGE_EDGES = (
    "pelvis_torso",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
)
DOWNSTREAM = {
    "pelvis_torso": (
        "torso", "upper_arm_left", "forearm_left",
        "upper_arm_right", "forearm_right",
    ),
    "shoulder_left": ("upper_arm_left", "forearm_left"),
    "shoulder_right": ("upper_arm_right", "forearm_right"),
    "hip_left": ("thigh_left", "shank_left"),
    "hip_right": ("thigh_right", "shank_right"),
}
AFFECTED_LINES = {
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
CORE_LINES = (
    "spine", "shoulder_crossbar", "hip_crossbar",
    "upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right",
)
DISTAL_EDGE = {
    "forearm_left": "elbow_left",
    "forearm_right": "elbow_right",
    "shank_left": "knee_left",
    "shank_right": "knee_right",
}
DISTAL_LABEL = {
    "forearm_left": "wrist_left",
    "forearm_right": "wrist_right",
    "shank_left": "ankle_left",
    "shank_right": "ankle_right",
}
ACTION_SPECS = (
    (0, "00_initial_still"),
    (1, "02_t_pose"),
    (2, "03_pelvis_hula_circle"),
    (3, "04_shoulder_left"),
    (4, "05_shoulder_right"),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _quadrature(grid: np.ndarray, weights: np.ndarray) -> list[dict[str, float]]:
    """Collapse all 360 cells into 24 fixed, result-independent bins."""

    if len(grid) != 360 or weights.shape != grid.shape:
        raise ValueError("prefix nonhinge posterior must use the exact 360-cell S1 grid")
    rows = []
    for index in range(24):
        cells = np.arange(index * 15, (index + 1) * 15, dtype=int)
        rows.append({
            "candidate_grid_index": int(cells[len(cells) // 2]),
            "delta_rad": float(grid[cells[len(cells) // 2]]),
            "integrated_probability": float(np.sum(weights[cells])),
            "source_cell_start": int(cells[0]),
            "source_cell_stop_exclusive": int(cells[-1] + 1),
        })
    if not np.isclose(sum(row["integrated_probability"] for row in rows), 1.0):
        raise RuntimeError("full-S1 quadrature lost posterior mass")
    return rows


def _structural_topology(result: Any) -> bool:
    positions = result.segment_sensor_positions_m
    from biospur_fusion.v0.c2_progressive.scientific_fk import ROOTED_EDGES

    expected = {segment for edge in ROOTED_EDGES for segment in edge}
    if set(positions) != expected:
        return False
    return all(
        np.isfinite(positions[parent]).all()
        and np.isfinite(positions[child]).all()
        and float(np.linalg.norm(positions[child] - positions[parent])) > 1e-6
        for parent, child in ROOTED_EDGES
    )


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("prefix posterior renderer requires canonical Fusion_Part")
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_sensitivity_profiles,
    )

    settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))[
        "effective_settings"
    ]
    renderer = settings["scientific_renderer"]
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    manifest = json.loads(FRESH_MANIFEST.read_text(encoding="utf-8"))
    canonical_rows = {
        str(row["branch_id"]): row for row in manifest["structure"]["frame_branches"]
    }
    selected_branch_ids = tuple(
        json.loads((SOURCE_REPLAY / "REPLAY_00.json").read_text(encoding="utf-8"))[
            "branch_ids"
        ]
    )
    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    profile_styles = ("-", "--", ":", "-.", (0, (3, 1, 1, 1)))
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    source_hash_before = _sha(SOURCE_NPZ)
    corrected_hash_before = _sha(CORRECTED_NPZ)
    OUT.mkdir(parents=True, exist_ok=False)
    artifacts: list[dict[str, Any]] = []
    action_audits: list[dict[str, Any]] = []

    with np.load(SOURCE_NPZ, allow_pickle=False) as source, np.load(
        CORRECTED_NPZ, allow_pickle=False,
    ) as corrected:
        frozen_weights = np.asarray(source["frozen/branch_weights"], dtype=float)
        canonical_order = tuple(canonical_rows)
        branch_weight = {
            branch: float(frozen_weights[canonical_order.index(branch)])
            for branch in selected_branch_ids
        }
        for index, action in ACTION_SPECS:
            replay = json.loads(
                (SOURCE_REPLAY / f"REPLAY_{index:02d}.json").read_text(encoding="utf-8")
            )
            if replay["action"] != action:
                raise RuntimeError("prefix/action chronology differs from immutable replay")
            figure, axes = plt.subplots(
                4, 3, figsize=(22.5, 16.8), dpi=int(renderer["dpi"]), squeeze=False,
            )
            branch_audits = []
            for branch_row, branch in enumerate(sorted(
                selected_branch_ids, key=lambda value: (-branch_weight[value], value),
            )):
                frame_prefix = f"frames/{branch}"
                frames = {
                    segment: np.asarray(
                        source[f"{frame_prefix}/segment_from_sensor/{segment}"], dtype=float,
                    )
                    for segment in segments
                }
                connections = frozen_viewer._connections(source, frame_prefix)
                world_trajectory = {
                    segment: np.asarray(
                        source[
                            f"physical_trajectory/{index:02d}/{branch}/"
                            f"world_from_segment/{segment}"
                        ], dtype=float,
                    )
                    for segment in segments
                }
                time_s = np.asarray(
                    source[
                        f"physical_trajectory/{index:02d}/{branch}/common_physical_time_s"
                    ], dtype=float,
                )
                if set(len(value) for value in world_trajectory.values()) != {3} or len(time_s) != 3:
                    raise RuntimeError("registered 0/0.5/1 checkpoint support changed")
                posterior_by_edge = {}
                quadrature_by_edge = {}
                for edge in NONHINGE_EDGES:
                    prefix = f"nonhinge_heading/{index:02d}/{branch}/{edge}"
                    grid = np.asarray(corrected[f"{prefix}/delta_grid_rad"], dtype=float)
                    weights = np.asarray(corrected[f"{prefix}/posterior_weights"], dtype=float)
                    posterior_by_edge[edge] = weights
                    quadrature_by_edge[edge] = _quadrature(grid, weights)

                structural_count = 0
                total_candidate_count = 0
                selected_sample = 0 if index == 0 else 1
                base_selected = frozen_viewer._fk_result(
                    heading_world_from_sensor={
                        segment: world_trajectory[segment][selected_sample] @ frames[segment]
                        for segment in segments
                    },
                    sensor_from_segment={segment: frames[segment].T for segment in segments},
                    connections=connections,
                    profile=profiles[0],
                )
                base_lines = frozen_viewer._line_map(base_selected)
                for view_column, (view_name, (horizontal_name, vertical_name)) in enumerate(view_specs):
                    axis = axes[branch_row, view_column]
                    horizontal = coordinate[horizontal_name]
                    vertical = coordinate[vertical_name]
                    for profile_index, profile in enumerate(profiles):
                        result = frozen_viewer._fk_result(
                            heading_world_from_sensor={
                                segment: world_trajectory[segment][selected_sample] @ frames[segment]
                                for segment in segments
                            },
                            sensor_from_segment={segment: frames[segment].T for segment in segments},
                            connections=connections,
                            profile=profile,
                        )
                        lines = frozen_viewer._line_map(result)
                        for line_name in CORE_LINES:
                            line = lines[line_name]
                            axis.plot(
                                line[:, horizontal], line[:, vertical],
                                color="#374151", linewidth=0.55, alpha=0.32,
                                linestyle=profile_styles[profile_index], zorder=1,
                            )

                    for sample_index, sample_alpha in enumerate((0.16, 0.26, 0.38)):
                        base_world = {
                            segment: world_trajectory[segment][sample_index]
                            for segment in segments
                        }
                        for edge in NONHINGE_EDGES:
                            for candidate in quadrature_by_edge[edge]:
                                yaw = Rotation.from_rotvec(
                                    [0.0, 0.0, candidate["delta_rad"]]
                                ).as_matrix()
                                candidate_world = {
                                    segment: (
                                        yaw @ base_world[segment]
                                        if segment in DOWNSTREAM[edge]
                                        else base_world[segment]
                                    )
                                    for segment in segments
                                }
                                result = frozen_viewer._fk_result(
                                    heading_world_from_sensor={
                                        segment: candidate_world[segment] @ frames[segment]
                                        for segment in segments
                                    },
                                    sensor_from_segment={
                                        segment: frames[segment].T for segment in segments
                                    },
                                    connections=connections,
                                    profile=profiles[0],
                                )
                                total_candidate_count += int(view_column == 0)
                                legal = _structural_topology(result)
                                structural_count += int(view_column == 0 and legal)
                                if not legal:
                                    continue
                                lines = frozen_viewer._line_map(result)
                                alpha = sample_alpha * (
                                    0.45 + 0.55 * candidate["integrated_probability"] * 24.0
                                )
                                for line_name in AFFECTED_LINES[edge]:
                                    line = lines[line_name]
                                    axis.plot(
                                        line[:, horizontal], line[:, vertical],
                                        color=EDGE_COLORS[edge], linewidth=0.5,
                                        alpha=min(0.55, alpha), zorder=2,
                                    )

                    joints = np.vstack(list(base_selected.shared_joint_positions_m.values()))
                    sensors = np.vstack(list(base_selected.segment_sensor_positions_m.values()))
                    axis.scatter(
                        joints[:, horizontal], joints[:, vertical],
                        marker="o", s=9, color="#1d4ed8", alpha=0.72, zorder=4,
                    )
                    axis.scatter(
                        sensors[:, horizontal], sensors[:, vertical],
                        marker="s", s=9, color="#b91c1c", alpha=0.72, zorder=4,
                    )
                    axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
                    axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
                    axis.set_aspect("equal", adjustable="box")
                    axis.grid(alpha=0.16)
                    if branch_row == 0:
                        axis.set_title(view_name, fontsize=10)
                    axis.set_xlabel(f"replay-world {horizontal_name} (m)", fontsize=7)
                    axis.set_ylabel(f"replay-world {vertical_name} (m)", fontsize=7)
                    if view_column == 0:
                        short = branch.removeprefix("HINGE_SIGN_").replace("_", " ")
                        axis.text(
                            0.01, 0.98,
                            f"{short}\nframe-branch weight={branch_weight[branch]:.6f}",
                            transform=axis.transAxes, va="top", ha="left", fontsize=6.2,
                            bbox={"facecolor": "white", "alpha": 0.82, "edgecolor": "none"},
                        )
                branch_audits.append({
                    "branch_id": branch,
                    "frame_branch_weight": branch_weight[branch],
                    "checkpoint_times_s": time_s.tolist(),
                    "full_s1_posterior_sha256_by_edge": {
                        edge: hashlib.sha256(
                            np.ascontiguousarray(posterior_by_edge[edge]).view(np.uint8)
                        ).hexdigest()
                        for edge in NONHINGE_EDGES
                    },
                    "fixed_24_bin_quadrature_by_edge": quadrature_by_edge,
                    "structural_topology_candidate_count": structural_count,
                    "candidate_count_before_structural_gate": total_candidate_count,
                    "full_body_physical_legality_evaluated": False,
                })

            legend = [
                *[
                    Line2D([0], [0], color=EDGE_COLORS[edge], label=f"{edge}: 24-bin full-S1 mass")
                    for edge in NONHINGE_EDGES
                ],
                Line2D([0], [0], color="#374151", label="5 raw landmark-profile core outlines"),
                Line2D([0], [0], marker="o", color="#1d4ed8", linestyle="None", label="functional joint"),
                Line2D([0], [0], marker="s", color="#b91c1c", linestyle="None", label="surface sensor origin"),
            ]
            figure.legend(
                legend, [item.get_label() for item in legend], loc="lower center",
                bbox_to_anchor=(0.5, 0.065), ncol=4, fontsize=6.8, framealpha=0.95,
            )
            figure.suptitle(
                f"REPLAY_{index:02d} {action} — PREFIX-LOCAL NONHINGE FULL-S1 DIAGNOSTIC\n"
                "TRAINING-ONLY POST-FREEZE FRAME / PREFIX HEADING STATE / MULTIMODAL / NOT POSE TRUTH / NOT PASS",
                color="#991b1b", fontsize=13, fontweight="bold",
            )
            figure.text(
                0.5, 0.018,
                "All 360 cells integrated into fixed 24-bin edge quadratures; three registered 0/0.5/1 time checkpoints; "
                "no circular mean/argmax, no 360^5 pose, no final-heading backflow.\n"
                "Shared nuisance is conservative first-order push-forward, NOT joint marginal; full-body legality unknown; "
                "distal tuned mean endpoints withheld; no IK/rebase/repair/payload/heldout.",
                ha="center", fontsize=8.0,
            )
            figure.tight_layout(rect=(0.01, 0.125, 0.99, 0.91))
            png = OUT / f"REPLAY_{index:02d}_{action}_PREFIX_NONHINGE_S1_TRIVIEW.png"
            figure.savefig(png)
            plt.close(figure)
            shape = tuple(int(value) for value in plt.imread(png).shape[:2])
            png.chmod(0o444)
            artifacts.append({
                "chronological_index": index,
                "action": action,
                "path": str(png),
                "sha256": _sha(png),
                "pixel_dimensions": [shape[1], shape[0]],
            })
            action_audits.append({
                "chronological_index": index,
                "action": action,
                "branch_rows": branch_audits,
            })
            print(json.dumps({"rendered": str(png), "sha256": _sha(png)}), flush=True)

    if _sha(SOURCE_NPZ) != source_hash_before or _sha(CORRECTED_NPZ) != corrected_hash_before:
        raise RuntimeError("immutable replay input changed during derived rendering")
    audit = {
        "schema": "biospur-c2-five-prefix-posterior-aware-triview-diagnostic-v1",
        "source_replay_npz": {"path": str(SOURCE_NPZ), "sha256": source_hash_before},
        "corrected_prefix_state": {
            "path": str(CORRECTED_NPZ), "sha256": corrected_hash_before,
        },
        "corrected_prefix_audit": {
            "path": str(CORRECTED / "AUDIT.json"), "sha256": _sha(CORRECTED / "AUDIT.json"),
        },
        "actions": action_audits,
        "artifacts": artifacts,
        "exactly_five_artifacts": len(artifacts) == 5,
        "full_360_cell_posterior_mass_consumed": True,
        "fixed_result_independent_quadrature_bin_count_per_edge": 24,
        "circular_mean_or_argmax_used": False,
        "global_cartesian_nonhinge_pose_enumerated": False,
        "old_qmt_only_carried_zero_rendered_as_pose": False,
        "full_body_physical_legality_evaluated": False,
        "joint_nuisance_marginal_claimed": False,
        "payload_or_heldout_access": False,
        "fit_qmt_or_progressive_recomputed": False,
        "scientific_acceptance_pass": False,
        "tuned_human_pose_pass": False,
    }
    audit_path = OUT / "AUDIT.json"
    _write_json(audit_path, audit)
    print(json.dumps({
        "audit": str(audit_path), "audit_sha256": _sha(audit_path),
        "artifacts": artifacts,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
