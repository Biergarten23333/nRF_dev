#!/usr/bin/env python3
"""Render two multibranch tri-views from fresh attempt 003 causal arrays."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.lines import Line2D


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
AMENDMENT = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
OUT = SPRINT / "C2_FRESH_CAUSAL_FINAL_SQUAT_MULTIBRANCH_TRIVIEWS_003"


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


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("fresh causal viewer requires canonical Fusion_Part")
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        direct_orientation_avatar_fk,
        landmark_proxy_sensitivity_profiles,
    )

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    settings = json.loads(AMENDMENT.read_text(encoding="utf-8"))["effective_settings"]
    verification = manifest["fresh_verification"]
    if (
        verification.get("pass") is not True
        or verification.get("fresh_execution_role") != "FRESH_RAW_RECOMPUTATION"
        or verification.get("primary_reader_session_id")
        == verification.get("fresh_reader_session_id")
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("fresh causal tri-view authority is inconsistent")
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    if tuple(str(row["profile_id"]) for row in profiles) != (
        "RAW_OBSERVER_A_BILATERAL_ROWS",
        "RAW_OBSERVER_B_INTERVAL_LOW_BILATERAL_ROWS",
        "RAW_OBSERVER_B_INTERVAL_HIGH_BILATERAL_ROWS",
        "RAW_CROSS_SIDE_SENSITIVITY_LEFT_LOW_RIGHT_HIGH",
        "RAW_CROSS_SIDE_SENSITIVITY_LEFT_HIGH_RIGHT_LOW",
    ):
        raise RuntimeError("registered landmark-proxy profile closure changed")
    renderer = settings["scientific_renderer"]
    frame_rows = manifest["structure"]["frame_branches"]
    canonical_ids = tuple(row["branch_id"] for row in frame_rows)
    segments = (
        "pelvis", "torso", "upper_arm_left", "forearm_left",
        "upper_arm_right", "forearm_right", "thigh_left", "shank_left",
        "thigh_right", "shank_right",
    )
    actions = ((15, "16_squat"), (16, "17_final_still"))
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    colors = ("#111827", "#2563eb", "#dc2626", "#059669")
    profile_linestyles = ("-", "--", ":", "-.", (0, (3, 1, 1, 1)))
    profile_alphas = (0.92, 0.58, 0.58, 0.36, 0.36)
    profile_labels = (
        "Observer A: L/R forearm 0.245 m",
        "Observer B low: L/R forearm 0.260 m",
        "Observer B high: L/R forearm 0.265 m",
        "Cross sensitivity: L-low/R-high",
        "Cross sensitivity: L-high/R-low",
    )
    action_rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    OUT.mkdir(parents=True, exist_ok=False)
    with np.load(NPZ, allow_pickle=False) as arrays:
        weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
        hard_support = np.asarray(arrays["frozen/branch_hard_support"], dtype=bool)
        for chronological_index, action in actions:
            support = manifest["structure"]["physical_trajectory_support"][
                str(chronological_index)
            ]
            branch_ids = tuple(sorted(support))
            if len(branch_ids) != 4 or not all(
                support[value]["physically_legal"] for value in branch_ids
            ):
                raise RuntimeError(f"{action}: fresh hard-supported branch closure changed")
            results: list[tuple[str, float, list[tuple[Mapping[str, Any], Any]]]] = []
            branch_rows: list[dict[str, Any]] = []
            time_reference: np.ndarray | None = None
            selected_index: int | None = None
            for branch_id in branch_ids:
                canonical_index = canonical_ids.index(branch_id)
                if not hard_support[canonical_index]:
                    raise RuntimeError(f"{action}: branch is not in frozen hard support")
                prefix = f"physical_trajectory/{chronological_index}/{branch_id}"
                common_time = np.asarray(
                    arrays[f"{prefix}/common_physical_time_s"], dtype=float,
                )
                if time_reference is None:
                    time_reference = common_time.copy()
                    selected_index = int(common_time.size // 2)
                elif not np.array_equal(common_time, time_reference):
                    raise RuntimeError(f"{action}: branch time grids differ")
                assert selected_index is not None
                rotations = {
                    segment: np.asarray(
                        arrays[f"{prefix}/world_from_segment/{segment}"][selected_index],
                        dtype=float,
                    )
                    for segment in segments
                }
                profile_results = [
                    (
                        profile,
                        direct_orientation_avatar_fk(
                            world_from_segment=rotations,
                            profile=profile,
                            pelvis_gauge_position_m=np.zeros(3),
                        ),
                    )
                    for profile in profiles
                ]
                weight = float(weights[canonical_index])
                results.append((branch_id, weight, profile_results))
                branch_rows.append({
                    "branch_id": branch_id,
                    "posterior_weight": weight,
                    "hard_supported": True,
                    "full_physical_gate_legal": True,
                    "viewer_only_profile_ids": [
                        str(profile["profile_id"]) for profile in profiles
                    ],
                    "world_from_segment_array_sha256_by_segment": {
                        segment: _array_sha(
                            arrays[f"{prefix}/world_from_segment/{segment}"]
                        ) for segment in segments
                    },
                })
            assert time_reference is not None and selected_index is not None
            selected_time = float(time_reference[selected_index])

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
                for branch_index, (branch_id, weight, profile_results) in enumerate(results):
                    for profile_index, (_, result) in enumerate(profile_results):
                        for line in result.line_segments_m.values():
                            axis.plot(
                                line[:, horizontal], line[:, vertical],
                                color=colors[branch_index],
                                linestyle=profile_linestyles[profile_index],
                                linewidth=(
                                    float(renderer["line_width"])
                                    * (1.0 if profile_index == 0 else 0.72)
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
                for index, (branch_id, weight, _) in enumerate(results)
            ] + [
                Line2D(
                    [0], [0], color="#4b5563",
                    linestyle=profile_linestyles[index],
                    linewidth=1.3 if index == 0 else 1.0,
                    alpha=profile_alphas[index], label=label,
                )
                for index, label in enumerate(profile_labels)
            ]
            labels = [handle.get_label() for handle in handles]
            figure.legend(
                handles, labels, loc="lower center", bbox_to_anchor=(0.5, 0.08),
                ncol=3, fontsize=5.8, framealpha=0.92,
            )
            figure.suptitle(
                f"ATTEMPT003 FRESH CAUSAL {action} — ALL FOUR HARD-SUPPORTED BRANCHES\n"
                "training-only owner arrays; fixed camera/geometry; NOT POSE/SCIENCE PASS",
                color="#991b1b", fontsize=11.5, fontweight="bold",
            )
            figure.text(
                0.5, 0.018,
                f"prefix={chronological_index}; sample=floor(n/2)={selected_index}; "
                f"t={selected_time:.6f} s; five raw viewer-only profiles; "
                "Observer A 245 mm + Observer B [260,265] mm remain separate; "
                "no weights/midpoint/equality/branch lock/IK/rebase/repair",
                ha="center", fontsize=8.0,
            )
            figure.tight_layout(rect=(0.01, 0.20, 0.99, 0.88))
            png = OUT / f"FRESH_CAUSAL_{chronological_index:02d}_{action}_MULTIBRANCH_TRIVIEW.png"
            figure.savefig(png)
            plt.close(figure)
            image_shape = tuple(int(value) for value in plt.imread(png).shape[:2])
            png.chmod(0o444)
            artifacts.append({
                "action": action,
                "chronological_index": chronological_index,
                "path": str(png.relative_to(WORKSPACE)),
                "sha256": _sha(png),
                "pixel_dimensions": [image_shape[1], image_shape[0]],
            })
            action_rows.append({
                "action": action,
                "chronological_index": chronological_index,
                "sample_rule": "FLOOR_PHYSICAL_TRAJECTORY_CHECKPOINT_COUNT_DIVIDED_BY_TWO",
                "sample_index": selected_index,
                "common_physical_time_s": selected_time,
                "common_physical_time_array_sha256": _array_sha(time_reference),
                "viewer_only_profiles": [
                    {
                        "profile_id": str(profile["profile_id"]),
                        "forearm_left_m": float(profile["forearm_left_m"]),
                        "forearm_right_m": float(profile["forearm_right_m"]),
                        "nonprobabilistic_weight": None,
                    }
                    for profile in profiles
                ],
                "branch_rows": branch_rows,
            })
    final_prefix = json.loads(
        (SPRINT / "C2_FRESH_FRESH_PREFIX_18_ATTEMPT_003.json").read_text(
            encoding="utf-8"
        )
    )
    expected_physical_validity = (
        0.4 * (10.0 / 70.0) + 0.3 * 1.0 + 0.3 * (4.0 / 16.0)
    )
    if not np.isclose(
        float(final_prefix["physical_validity"]), expected_physical_validity,
        atol=1e-15, rtol=0.0,
    ):
        raise RuntimeError("fresh final physical-validity component formula changed")
    audit = {
        "schema": "biospur-c2-fresh-causal-final-squat-multibranch-triview-v1",
        "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
        "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
        "source": {
            "path": "tools/render_c2_fresh_final_squat_multibranch.py",
            "sha256": _sha(Path(__file__).resolve()),
        },
        "artifacts": artifacts,
        "actions": action_rows,
        "renderer_input_path": "physical_trajectory/<prefix>/<branch>/world_from_segment/<segment>",
        "renderer_recomputed_or_transposed_another_orientation_source": False,
        "landmark_proxy_profile_contract": {
            "owner": "VIEWER_ONLY_NON_ANATOMICAL_SCALE_CONTEXT",
            "profile_count": len(profiles),
            "observer_a_forearm_rows_m": {"left": 0.245, "right": 0.245},
            "observer_b_forearm_interval_rows_m": {
                "left": [0.260, 0.265], "right": [0.260, 0.265],
            },
            "midpoint_or_side_equality_hardened": False,
            "profile_weights_assigned": False,
            "entered_fit_qmt_or_segment_frames": False,
        },
        "final_prefix18_physical_validity_breakdown": {
            "registered_weights": {
                "current_episode_geometry_information_fraction": 0.4,
                "official_heading_effective_support_fraction": 0.3,
                "trajectory_legal_branch_fraction": 0.3,
            },
            "components": {
                "current_episode_observed_rank_over_state_dimension": 10.0 / 70.0,
                "official_heading_effective_support_fraction": 1.0,
                "hard_supported_legal_branch_fraction": 4.0 / 16.0,
            },
            "value": expected_physical_validity,
            "is_direct_pose_validity_or_residual_score": False,
            "all_four_prefix18_physical_trajectory_entries_individually_legal": True,
        },
        "causal_branch_decision": "RETAIN_ALL_FOUR_HARD_SUPPORTED_BRANCHES_AND_RENDER_MULTIMODAL_OVERLAY",
        "dominant_branch_used_as_hard_lock": False,
        "pixel_evidence_used_for_branch_selection": False,
        "payload_read": False,
        "heldout_opened": False,
        "fit_qmt_progressive_or_frozen_state_modified": False,
        "manual_pose_flip_ik_rebase_repair": False,
        "scientific_acceptance_pass": False,
    }
    audit_path = OUT / "FRESH_CAUSAL_FINAL_SQUAT_MULTIBRANCH_TRIVIEW_AUDIT.json"
    _write_new(audit_path, audit)
    print(json.dumps({
        "artifacts": artifacts,
        "audit": {"path": str(audit_path.relative_to(WORKSPACE)), "sha256": _sha(audit_path)},
        "scientific_acceptance_pass": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
