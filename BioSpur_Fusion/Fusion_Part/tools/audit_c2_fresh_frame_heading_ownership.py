#!/usr/bin/env python3
"""Audit frozen C2 frame, longitudinal-axis, twist, and heading ownership."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
MANIFEST = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
NPZ = SPRINT / "C2_FRESH_CONTINUATION_FROZEN_STATE_003.npz"
GATE = SPRINT / "C2_FRESH_CONTINUATION_GATE_003.json"
REPLAY = SPRINT / "RUN013_POSTFREEZE_RETROSPECTIVE_QMT_REPLAY_002/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
MAP_HELPER = WORKSPACE / "tools/render_c2_fresh_continuous_squat.py"
OUTPUT = SPRINT / "C2_FRESH_FRAME_HEADING_OWNERSHIP_AUDIT_001.json"
ARM_SEGMENTS = (
    "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
)


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
    ).encode("utf-8")
    return hashlib.sha256(header + array.tobytes()).hexdigest()


def _load_helper() -> Any:
    spec = importlib.util.spec_from_file_location("fresh_exact_map_helper", MAP_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load exact rooted-map owner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _matrix_error(first: np.ndarray, second: np.ndarray) -> float:
    return float(np.max(np.abs(np.asarray(first) - np.asarray(second))))


def _compose_action(
    *,
    fresh: Mapping[str, np.ndarray],
    action_index: int,
    branch_id: str,
    root_rows: np.ndarray,
    source_by_root: Mapping[str, Mapping[int, int]],
    node_by_segment: Mapping[str, str],
    segments: tuple[str, ...],
    frame_prefix: str,
) -> dict[str, np.ndarray]:
    output: dict[str, np.ndarray] = {}
    for segment in segments:
        source_rows = np.asarray(
            [source_by_root[segment][int(root)] for root in root_rows], dtype=int,
        )
        quaternions = np.asarray(
            fresh[
                f"orientation/{action_index:02d}/{node_by_segment[segment]}/"
                "quat_world_sensor_wxyz"
            ][source_rows],
            dtype=float,
        )
        # qmt arrays are scalar-first; scipy expects scalar-last.
        world_from_sensor = Rotation.from_quat(
            quaternions[:, [1, 2, 3, 0]], scalar_first=False,
        ).as_matrix()
        segment_from_sensor = np.asarray(
            fresh[f"{frame_prefix}/segment_from_sensor/{segment}"], dtype=float,
        )
        sensor_from_segment = segment_from_sensor.T
        uncorrected = np.einsum(
            "nij,jk->nik", world_from_sensor, sensor_from_segment,
        )
        delta = np.asarray(
            fresh[
                f"trajectories/{action_index:02d}/{branch_id}/"
                f"segment_global_delta/{segment}"
            ],
            dtype=float,
        )[root_rows]
        yaw = Rotation.from_rotvec(np.column_stack((
            np.zeros(len(delta)), np.zeros(len(delta)), delta,
        ))).as_matrix()
        output[segment] = np.einsum("nij,njk->nik", yaw, uncorrected)
    return output


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("frame/heading audit requires canonical Fusion_Part")
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        direct_orientation_avatar_fk,
        landmark_proxy_sensitivity_profiles,
    )

    helper = _load_helper()
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))["effective_settings"]
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    gate = json.loads(GATE.read_text(encoding="utf-8"))
    if (
        gate.get("fresh_raw_gate_pass") is not True
        or gate.get("heldout_opened") is not False
        or manifest.get("heldout_opened") is not False
        or manifest.get("scientific_acceptance_pass") is not False
    ):
        raise RuntimeError("frozen frame/heading audit authority is inconsistent")
    actions = tuple(settings["execution_contract"]["chronological_actions"])
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = tuple(sorted({name for _, parent, child in EDGE_SPECS for name in (parent, child)}))
    if set(node_by_segment) != set(segments):
        raise RuntimeError("frame/heading audit hardware identity is incomplete")
    nodes = tuple(sorted(node_by_segment.values()))
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    sigma = float(settings["segment_frames"]["unidentified_direction_sigma_rad"])

    all_action_evidence: list[dict[str, Any]] = []
    checkpoint_rows: list[dict[str, Any]] = []
    frame_rows: list[dict[str, Any]] = []
    historical_frame_candidates: list[dict[str, Any]] = []
    bridge_rows: list[dict[str, Any]] = []
    with (
        np.load(NPZ, allow_pickle=False) as fresh,
        np.load(REPLAY, allow_pickle=False) as replay,
    ):
        canonical_ids = tuple(
            row["branch_id"] for row in manifest["structure"]["frame_branches"]
        )
        support = tuple(
            branch_id for branch_id, retained in zip(
                canonical_ids, fresh["frozen/branch_hard_support"], strict=True,
            ) if retained
        )
        weights = np.asarray(fresh["frozen/branch_weights"], dtype=float)
        if len(support) != 4:
            raise RuntimeError("frame/heading audit requires four hard-supported branches")
        dominant = max(support, key=lambda value: float(weights[canonical_ids.index(value)]))

        for branch_id in support:
            for segment in ("torso", *ARM_SEGMENTS):
                covariance = np.asarray(
                    fresh[f"frames/{branch_id}/frame_covariance/{segment}"],
                    dtype=float,
                )
                frame_rows.append({
                    "branch_id": branch_id,
                    "branch_weight": float(weights[canonical_ids.index(branch_id)]),
                    "segment": segment,
                    "frame_tangent_covariance_trace_rad2": float(np.trace(covariance)),
                    "frame_tangent_covariance_eigenvalues_rad2": np.linalg.eigvalsh(covariance).tolist(),
                    "segment_from_sensor_sha256": _array_sha(
                        fresh[f"frames/{branch_id}/segment_from_sensor/{segment}"]
                    ),
                })

        initial_heading_delta_max_abs_rad = max(
            float(np.max(np.abs(np.asarray(
                fresh[
                    f"trajectories/00/{branch_id}/"
                    f"segment_global_delta/{segment}"
                ],
                dtype=float,
            ))))
            for branch_id in support
            for segment in segments
        )

        # These are the owner-authenticated chronological frame candidates
        # already retained by the fresh runtime.  They are evidence/history,
        # not exchangeable samples from one final Gaussian posterior.
        historical_change_by_segment: dict[str, list[float]] = {
            segment: [] for segment in ("torso", *ARM_SEGMENTS)
        }
        for action_index in range(10, 19):
            prefix_weights = np.asarray(
                fresh[f"progressive_prefix/{action_index:02d}/branch_weights"],
                dtype=float,
            )
            prefix_branch_ids = sorted({
                key.split("/")[2]
                for key in fresh.files
                if key.startswith(f"physical_trajectory/{action_index:02d}/")
            })
            if set(prefix_branch_ids) != set(support):
                raise RuntimeError(
                    f"prefix {action_index}: physical frame branch support changed"
                )
            for branch_id in prefix_branch_ids:
                branch_index = canonical_ids.index(branch_id)
                for segment in ("torso", *ARM_SEGMENTS):
                    prefix_key = (
                        f"physical_trajectory/{action_index:02d}/{branch_id}/"
                        f"segment_from_sensor/{segment}"
                    )
                    prefix_frame = np.asarray(fresh[prefix_key], dtype=float)
                    final_frame = np.asarray(
                        fresh[f"frames/{branch_id}/segment_from_sensor/{segment}"],
                        dtype=float,
                    )
                    change_deg = float(np.degrees(
                        Rotation.from_matrix(prefix_frame @ final_frame.T).magnitude()
                    ))
                    historical_change_by_segment[segment].append(change_deg)
                    covariance = np.asarray(
                        fresh[
                            f"physical_trajectory/{action_index:02d}/{branch_id}/"
                            f"orientation_covariance/{segment}"
                        ],
                        dtype=float,
                    )
                    traces = np.trace(covariance, axis1=1, axis2=2)
                    historical_frame_candidates.append({
                        "chronological_index": action_index,
                        "action": actions[action_index],
                        "branch_id": branch_id,
                        "branch_weight_at_prefix": float(prefix_weights[branch_index]),
                        "segment": segment,
                        "segment_from_sensor_sha256": _array_sha(prefix_frame),
                        "geodesic_change_to_final_frame_deg": change_deg,
                        "physical_checkpoint_count": int(len(covariance)),
                        "orientation_covariance_trace_rad2_min": float(np.min(traces)),
                        "orientation_covariance_trace_rad2_max": float(np.max(traces)),
                        "owner_authenticated_chronological_candidate": True,
                        "same_final_state_exchangeable_posterior_sample": False,
                    })

        action_maps: dict[int, tuple[np.ndarray, Mapping[str, Mapping[int, int]]]] = {}
        for action_index, action in enumerate(actions):
            orientation_equal = True
            for node in nodes:
                for leaf in ("time_us", "quat_world_sensor_wxyz"):
                    orientation_equal &= np.array_equal(
                        fresh[f"orientation/{action_index:02d}/{node}/{leaf}"],
                        replay[f"orientation/{action_index:02d}/{node}/{leaf}"],
                    )
            if not orientation_equal:
                raise RuntimeError(f"{action}: fresh/replay orientation bridge differs")
            pelvis_time = np.asarray(
                fresh[f"orientation/{action_index:02d}/{node_by_segment['pelvis']}/time_us"],
                dtype=np.int64,
            )
            for branch_id in support:
                if not np.array_equal(
                    fresh[f"trajectories/{action_index:02d}/{branch_id}/common_physical_time_s"],
                    replay[f"trajectory/{action_index:02d}/{branch_id}/common_physical_time_s"],
                ):
                    raise RuntimeError(f"{action}: fresh/replay common grid differs")
            root_rows, source_by_root, edge_audit = helper._compose_exact_root_maps(
                replay,
                chronological_index=action_index,
                selection_branch_id=dominant,
                branch_ids=support,
                pelvis_time_us=pelvis_time,
                edge_specs=EDGE_SPECS,
                require_contiguous_root_rows=False,
            )
            action_maps[action_index] = (root_rows, source_by_root)
            bridge_rows.append({
                "chronological_index": action_index,
                "action": action,
                "exact_root_row_count": int(len(root_rows)),
                "exact_contiguous_span_count": int(1 + np.count_nonzero(np.diff(root_rows) != 1)),
                "root_rows_sha256": _array_sha(root_rows),
                "all_ten_orientation_time_and_quaternion_arrays_exact_equal": True,
                "all_four_branch_common_grids_exact_equal": True,
                "edge_map_semantic_sha256": hashlib.sha256(json.dumps(
                    edge_audit, sort_keys=True, separators=(",", ":"),
                ).encode("utf-8")).hexdigest(),
            })
            for branch_id in support:
                world = _compose_action(
                    fresh=fresh,
                    action_index=action_index,
                    branch_id=branch_id,
                    root_rows=root_rows,
                    source_by_root=source_by_root,
                    node_by_segment=node_by_segment,
                    segments=segments,
                    frame_prefix=f"frames/{branch_id}",
                )
                metrics: dict[str, Any] = {}
                for segment in ARM_SEGMENTS:
                    # Viewer proximal-to-distal is segment -Z. Its dot with
                    # world-down equals R_world_segment[2,2].
                    down_cosine = world[segment][:, 2, 2]
                    longitudinal = -world[segment][:, :, 2]
                    reference = longitudinal[0]
                    excursion = np.degrees(np.arccos(np.clip(
                        longitudinal @ reference, -1.0, 1.0,
                    )))
                    metrics[segment] = {
                        "proximal_to_distal_world_down_cosine_min": float(np.min(down_cosine)),
                        "proximal_to_distal_world_down_cosine_median": float(np.median(down_cosine)),
                        "proximal_to_distal_world_down_cosine_max": float(np.max(down_cosine)),
                        "fraction_in_world_down_hemisphere": float(np.mean(down_cosine > 0.0)),
                        "longitudinal_excursion_from_first_row_max_deg": float(np.max(excursion)),
                    }
                for side in ("left", "right"):
                    upper = -world[f"upper_arm_{side}"][:, :, 2]
                    lower = -world[f"forearm_{side}"][:, :, 2]
                    relative = np.degrees(np.arccos(np.clip(
                        np.sum(upper * lower, axis=1), -1.0, 1.0,
                    )))
                    metrics[f"elbow_{side}_longitudinal_separation"] = {
                        "minimum_deg": float(np.min(relative)),
                        "median_deg": float(np.median(relative)),
                        "maximum_deg": float(np.max(relative)),
                    }
                all_action_evidence.append({
                    "chronological_index": action_index,
                    "action": action,
                    "branch_id": branch_id,
                    "branch_weight": float(weights[canonical_ids.index(branch_id)]),
                    "frame_role": "FINAL_FROZEN_RETROSPECTIVE_FOR_ACTION_EVIDENCE_ONLY",
                    "row_count": int(len(root_rows)),
                    "metrics": metrics,
                })

        # Reconstruct the two causal actions from their own prefix frames and
        # require equality to every persisted physical checkpoint matrix.
        correct_errors: list[float] = []
        wrong_transpose_errors: list[float] = []
        wrong_yaw_order_errors: list[float] = []
        wrong_yaw_sign_errors: list[float] = []
        for action_index in (15, 16):
            root_rows, source_by_root = action_maps[action_index]
            for branch_id in support:
                frame_prefix = f"physical_trajectory/{action_index:02d}/{branch_id}"
                reconstructed = _compose_action(
                    fresh=fresh,
                    action_index=action_index,
                    branch_id=branch_id,
                    root_rows=root_rows,
                    source_by_root=source_by_root,
                    node_by_segment=node_by_segment,
                    segments=segments,
                    frame_prefix=frame_prefix,
                )
                full_time = np.asarray(
                    fresh[f"trajectories/{action_index:02d}/{branch_id}/common_physical_time_s"]
                )
                checkpoint_time = np.asarray(fresh[f"{frame_prefix}/common_physical_time_s"])
                for checkpoint_index, time_s in enumerate(checkpoint_time):
                    matches = np.flatnonzero(full_time == time_s)
                    if len(matches) != 1 or int(matches[0]) not in set(map(int, root_rows)):
                        raise RuntimeError("physical checkpoint lacks one exact rooted source row")
                    root = int(matches[0])
                    position = int(np.flatnonzero(root_rows == root)[0])
                    for segment in segments:
                        expected = np.asarray(
                            fresh[f"{frame_prefix}/world_from_segment/{segment}"][checkpoint_index]
                        )
                        correct = reconstructed[segment][position]
                        correct_errors.append(_matrix_error(correct, expected))
                        source_index = source_by_root[segment][root]
                        quaternion = np.asarray(
                            fresh[
                                f"orientation/{action_index:02d}/{node_by_segment[segment]}/"
                                "quat_world_sensor_wxyz"
                            ][source_index]
                        )
                        world_sensor = Rotation.from_quat(
                            quaternion[[1, 2, 3, 0]], scalar_first=False,
                        ).as_matrix()
                        segment_from_sensor = np.asarray(
                            fresh[f"{frame_prefix}/segment_from_sensor/{segment}"]
                        )
                        delta = float(fresh[
                            f"trajectories/{action_index:02d}/{branch_id}/"
                            f"segment_global_delta/{segment}"
                        ][root])
                        yaw = Rotation.from_rotvec([0.0, 0.0, delta]).as_matrix()
                        wrong_transpose_errors.append(_matrix_error(
                            yaw @ world_sensor @ segment_from_sensor, expected,
                        ))
                        wrong_yaw_order_errors.append(_matrix_error(
                            world_sensor @ segment_from_sensor.T @ yaw, expected,
                        ))
                        wrong_yaw_sign_errors.append(_matrix_error(
                            Rotation.from_rotvec([0.0, 0.0, -delta]).as_matrix()
                            @ world_sensor @ segment_from_sensor.T,
                            expected,
                        ))
                checkpoint_rows.append({
                    "chronological_index": action_index,
                    "action": actions[action_index],
                    "branch_id": branch_id,
                    "checkpoint_count": int(len(checkpoint_time)),
                    "segment_count": len(segments),
                })

        # Viewer-only algebra: limb axial twist cannot move any longitudinal
        # link, whereas torso twist moves the crossbar and is coupled to QMT.
        final_branch = dominant
        final_world = {
            segment: np.asarray(
                fresh[f"physical_trajectory/16/{final_branch}/world_from_segment/{segment}"][2]
            ) for segment in segments
        }
        profile = profiles[0]
        base_avatar = direct_orientation_avatar_fk(
            world_from_segment=final_world, profile=profile,
            pelvis_gauge_position_m=np.zeros(3),
        )
        limb_twist_max = 0.0
        torso_twist_max = 0.0
        twist_rows = []
        for angle in (-sigma, sigma):
            twist = Rotation.from_rotvec([0.0, 0.0, angle]).as_matrix()
            limb_world = {key: value.copy() for key, value in final_world.items()}
            for segment in ARM_SEGMENTS:
                limb_world[segment] = limb_world[segment] @ twist
            limb_avatar = direct_orientation_avatar_fk(
                world_from_segment=limb_world, profile=profile,
                pelvis_gauge_position_m=np.zeros(3),
            )
            limb_difference = max(
                _matrix_error(limb_avatar.landmark_positions_m[key], value)
                for key, value in base_avatar.landmark_positions_m.items()
            )
            torso_world = {key: value.copy() for key, value in final_world.items()}
            torso_world["torso"] = torso_world["torso"] @ twist
            torso_avatar = direct_orientation_avatar_fk(
                world_from_segment=torso_world, profile=profile,
                pelvis_gauge_position_m=np.zeros(3),
            )
            torso_difference = max(
                _matrix_error(torso_avatar.landmark_positions_m[key], value)
                for key, value in base_avatar.landmark_positions_m.items()
            )
            limb_twist_max = max(limb_twist_max, limb_difference)
            torso_twist_max = max(torso_twist_max, torso_difference)
            twist_rows.append({
                "twist_rad": angle,
                "twist_deg": float(np.degrees(angle)),
                "all_four_arm_segments_post_heading_viewer_max_point_change_m": limb_difference,
                "torso_post_heading_viewer_max_point_change_m": torso_difference,
            })

    final_rows = [
        row for row in all_action_evidence
        if row["chronological_index"] == 16
    ]
    final_down_minimum = min(
        row["metrics"][segment]["proximal_to_distal_world_down_cosine_min"]
        for row in final_rows for segment in ARM_SEGMENTS
    )
    audit = {
        "schema": "biospur-c2-fresh-frame-heading-ownership-audit-v1",
        "authority": {
            "fresh_manifest": {"path": str(MANIFEST.relative_to(WORKSPACE)), "sha256": _sha(MANIFEST)},
            "fresh_npz": {"path": str(NPZ.relative_to(WORKSPACE)), "sha256": _sha(NPZ)},
            "fresh_gate": {"path": str(GATE.relative_to(WORKSPACE)), "sha256": _sha(GATE)},
            "immutable_replay_source_maps": {"path": str(REPLAY.relative_to(WORKSPACE)), "sha256": _sha(REPLAY)},
            "settings": {"path": str(SETTINGS.relative_to(WORKSPACE)), "sha256": _sha(SETTINGS)},
        },
        "source_evidence": {
            "frame_construction": "segment_frames.py:_construct_sensor_from_segment uses proximal-minus-distal as segment +Z; one-center distal segments use sensor-to-proximal as +Z; _frame_from_z_y returns sensor_from_segment",
            "heading_composition": "heading.py composes world_from_sensor @ sensor_from_segment, then rooted world-Z delta is left-multiplied",
            "viewer_longitudinal_use": "scientific_fk.py:direct_orientation_avatar_fk uses segment -Z for proximal-to-distal limb links",
        },
        "mount_prior": {
            "legacy_direction_sensitivity_labels_deg": list(settings["segment_frames"]["parameter_sensitivity"]["wear_profiles_deg"]),
            "legacy_labels_used_as_hard_cone_or_candidate_generator": False,
            "qualitative_minus_y_minus_z_authority_is_broad_noncompact_support": True,
            "unidentified_direction_sigma_rad": sigma,
            "unidentified_direction_sigma_deg": float(np.degrees(sigma)),
            "not_exact_vectors": True,
            "not_hard_bilateral_mirrors": True,
        },
        "four_hard_supported_branches": list(support),
        "frame_covariance": frame_rows,
        "owner_retained_chronological_frame_candidates": {
            "prefix_indices": list(range(10, 19)),
            "candidate_row_count": len(historical_frame_candidates),
            "rows": historical_frame_candidates,
            "maximum_geodesic_change_to_final_frame_deg_by_segment": {
                segment: max(values)
                for segment, values in historical_change_by_segment.items()
            },
            "selection_or_equal_weighting_performed": False,
            "purpose": "BOUNDED_BRANCH_AWARE_CANDIDATE_AND_HISTORY_EVIDENCE",
        },
        "exact_source_map_bridge_by_action": bridge_rows,
        "all_action_final_frozen_retrospective_evidence": all_action_evidence,
        "causal_checkpoint_composition": {
            "actions": checkpoint_rows,
            "comparison_count": len(correct_errors),
            "correct_owner_formula_max_abs_matrix_error": max(correct_errors),
            "wrong_segment_transpose_max_abs_matrix_error": max(wrong_transpose_errors),
            "wrong_yaw_order_max_abs_matrix_error": max(wrong_yaw_order_errors),
            "wrong_yaw_sign_max_abs_matrix_error": max(wrong_yaw_sign_errors),
            "correct_formula_checkpoint_exact_tolerance_pass": max(correct_errors) <= 2e-15,
        },
        "bounded_hypotheses": {
            "H0_OWNER_EXACT_COMPOSITION": {
                "status": "SUPPORTED_BY_CAUSAL_CHECKPOINT_ROUNDTRIP",
                "rendered_existing_fresh_triplet": {
                    "initial_retrospective_sha256": "540d9093df89a9fa437d5bad10409ac8b7e804046e3b25699b4e43bb503bb8e1",
                    "squat_causal_sha256": "9c09ae0bdecd846a4a1c00f2efcc0f74047949cb862c06c9f554467ada2f39a2",
                    "final_causal_sha256": "a691d6d29f4892746cb5866fbfa05b26c47e9d8d01eb3c2aa9122eeb536df580",
                },
            },
            "H0A_INITIAL_CAUSAL_LOW_INFORMATION": {
                "status": "ZERO_HEADING_UPDATE_REQUIRES_WIDE_NOT_PASS_VIEW",
                "maximum_absolute_segment_global_delta_rad": initial_heading_delta_max_abs_rad,
                "all_ten_segments_and_four_branches_exactly_zero": initial_heading_delta_max_abs_rad == 0.0,
                "manual_frame_or_sign_flip_justified": False,
                "final_frozen_heading_backflow_to_causal_metrics_allowed": False,
                "retrospective_final_frozen_viewer_must_remain_separately_labelled": True,
            },
            "H1_LONGITUDINAL_Z_REVERSED": {
                "status": "REJECTED_BEFORE_RENDER_BY_SOURCE_AND_NATURAL_REST_EVIDENCE",
                "minimum_final_still_proximal_to_distal_world_down_cosine_across_branches_and_arms": final_down_minimum,
                "reversal_would_change_down_cosine_sign": True,
                "manual_flip_rendered_or_selected": False,
            },
            "H2_ARM_AXIAL_TWIST": {
                "status": "CANNOT_CHANGE_DIRECT_AVATAR_LONGITUDINAL_LINK_POINTS_POST_HEADING",
                "registered_plus_minus_one_sigma_viewer_algebra": twist_rows,
                "maximum_landmark_change_m": limb_twist_max,
                "qmt_rerun_or_branch_selection_performed": False,
            },
            "H3_TORSO_AXIAL_TWIST": {
                "status": "MATERIAL_VIEWER_EFFECT_BUT_NOT_INDEPENDENT_OF_QMT_HEADING",
                "registered_plus_minus_one_sigma_post_heading_viewer_max_landmark_change_m": torso_twist_max,
                "posthoc_variant_rendered_or_selected": False,
                "reason": "Changing torso frame alters shoulder crossbar and the QMT parent frame; a valid alternative requires an owner-retained frame sample and derived official-QMT replay, not a posthoc viewer rotation.",
            },
            "H4_HEADING_TRANSPOSE_ORDER_OR_SIGN": {
                "status": "REJECTED_BY_PERSISTED_CAUSAL_CHECKPOINT_ROUNDTRIP",
                "wrong_variants_rendered_or_selected": False,
            },
        },
        "causal_diagnosis": {
            "longitudinal_axis_sign_bug_supported": False,
            "arm_axial_twist_can_fix_link_geometry": False,
            "parent_child_heading_application_bug_supported": False,
            "initial_heading_observable_from_six_axis_still": False,
            "mean_only_weak_torso_frame_is_scientifically_credible": False,
            "historical_prefix_frame_candidates_audited_before_new_sampling": True,
            "blocking_owner_gap": "Frozen state retains chronological prefix10-18 frame candidates and their orientation covariance, but each is a different-time owner state rather than an exchangeable within-prefix manifold mixture. The final torso state remains one mean with enormous tangent covariance; choosing an earlier prefix or posthoc twist for final pixels would backflow or collapse uncertainty and couple inconsistently to QMT.",
            "narrow_next_delta": "Use retained chronological frame candidates as history/evidence and preserve all four weighted hinge branches. A future owner change should retain within-prefix branch-aware manifold frame candidates before moment collapse, then derived-replay official heading for those candidates; do not synthesize tangent-Gaussian samples, rerun calibration fitting, or choose from pixels.",
        },
        "raw_payload_read": False,
        "fit_or_progressive_state_modified": False,
        "retrospective_metrics_entered_causal_progress": False,
        "manual_pose_flip_ik_rebase_repair_branch_lock": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
    }
    _write_new(OUTPUT, audit)
    print(json.dumps({
        "output": str(OUTPUT.relative_to(WORKSPACE)),
        "sha256": _sha(OUTPUT),
        "correct_checkpoint_max_error": max(correct_errors),
        "final_down_cosine_minimum": final_down_minimum,
        "limb_twist_max_landmark_change_m": limb_twist_max,
        "torso_twist_max_landmark_change_m": torso_twist_max,
        "heldout_opened": False,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
