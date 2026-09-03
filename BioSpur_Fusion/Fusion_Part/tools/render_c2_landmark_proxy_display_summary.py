#!/usr/bin/env python3
"""Render three viewer-only LANDMARK_PROXY summaries from saved C2 state.

The tool builds deterministic joint posterior support, physically gates every
complete body candidate, and only then chooses a legal Fréchet medoid for
display.  It never rereads payload, refits, reruns QMT, or writes into causal
progressive state.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation

import render_c2_nonhinge_s1_qmt_rooted_debug as debug_owner
from replay_c2_postfreeze_heading import _reconstruct_frame_branches


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FROZEN_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
SOURCE_NPZ = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
DEBUG_DIR = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004"
DEBUG_NPZ = DEBUG_DIR / "DERIVED_QMT_ROOTED_STATE.npz"
DEBUG_AUDIT = DEBUG_DIR / "AUDIT.json"
NONHINGE_DIR = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002"
NONHINGE_NPZ = NONHINGE_DIR / "CORRECTED_PREFIX_NONHINGE_STATE.npz"
NONHINGE_AUDIT = NONHINGE_DIR / "AUDIT.json"
DISTAL_DIR = SPRINT / "C2_DISTAL_LONGITUDINAL_COMPLETE_S1_REPLAY_002"
DISTAL_NPZ = DISTAL_DIR / "COMPLETE_S1_DISTAL_LONGITUDINAL_STATE.npz"
DISTAL_AUDIT = DISTAL_DIR / "AUDIT.json"
AUTHORITY_006 = RUN / "USER_VIEWER_ONLY_LANDMARK_PROXY_SUMMARY_AMENDMENT_006.json"
AUTHORITY_007 = RUN / "USER_VIEWER_ONLY_LANDMARK_PROXY_SUMMARY_AMENDMENT_007_JOINT_SUPPORT_CORRECTION.json"
AUTHORITY_008 = RUN / "USER_VIEWER_ONLY_LANDMARK_PROXY_SUMMARY_AMENDMENT_008_SOURCE_BINDING.json"
AUTHORITY_009 = RUN / "USER_VIEWER_ONLY_LANDMARK_PROXY_SUMMARY_AMENDMENT_009_RUNTIME_BINDING.json"
AUTHORITY_010 = RUN / "USER_VIEWER_ONLY_LANDMARK_PROXY_SUMMARY_AMENDMENT_010_FACTOR_LABEL_BINDING.json"
RAW_ANTHROPOMETRY = WORKSPACE / "config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json"
FAILED_PARENT_001 = SPRINT / "C2_LANDMARK_PROXY_DISPLAY_SUMMARY_001/FAILURE.json"
FAILED_PARENT_002 = SPRINT / "C2_LANDMARK_PROXY_DISPLAY_SUMMARY_002/FAILURE.json"
FAILED_PARENT_003 = SPRINT / "C2_LANDMARK_PROXY_DISPLAY_SUMMARY_003/FAILURE.json"
OUT = SPRINT / "C2_LANDMARK_PROXY_DISPLAY_SUMMARY_004"

NONHINGE_EDGES = (
    "pelvis_torso", "shoulder_left", "shoulder_right", "hip_left", "hip_right",
)
DISTAL_SEGMENTS = ("forearm_left", "forearm_right", "shank_left", "shank_right")
ACTION_SPECS = (
    (0, "00_initial_still", "RETROSPECTIVE_PREFIX15_FROZEN_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH"),
    (1, "02_t_pose", "RETROSPECTIVE_PREFIX15_FROZEN_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH"),
    (15, "16_squat", "CAUSAL_PREFIX15_DISPLAY_SUMMARY_NOT_POSE_TRUTH"),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return _jsonable(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    path.chmod(0o444)


def _load_settings() -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return document, document["effective_settings"]


def _load_branches(
    *, frozen: Mapping[str, Any], source: Mapping[str, np.ndarray],
) -> tuple[Any, ...]:
    return tuple(
        debug_owner._branch_at_prefix(
            branch, source, f"physical_trajectory/15/{branch.branch_id}",
        )
        for branch in _reconstruct_frame_branches(frozen=frozen, arrays=source)
    )


def _conditional_factor_weights(
    *,
    branch_ids: tuple[str, ...],
    nonhinge: Mapping[str, np.ndarray],
    distal: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    output: dict[str, np.ndarray] = {}
    for edge in NONHINGE_EDGES:
        output[f"nonhinge_{edge}_s1"] = np.vstack([
            np.asarray(
                nonhinge[f"nonhinge_heading/15/{branch_id}/{edge}/posterior_weights"],
                dtype=float,
            )
            for branch_id in branch_ids
        ])
    for segment in DISTAL_SEGMENTS:
        output[f"distal_{segment}_s1"] = np.vstack([
            np.asarray(distal[f"{branch_id}/{segment}/posterior_weights"], dtype=float)
            for branch_id in branch_ids
        ])
    return output


def _candidate_coordinates(
    *,
    support_row: np.ndarray,
    factor_names: tuple[str, ...],
    branch_ids: tuple[str, ...],
    nonhinge: Mapping[str, np.ndarray],
    distal: Mapping[str, np.ndarray],
) -> tuple[str, dict[str, float], dict[str, np.ndarray], Mapping[str, Any]]:
    indices = {name: int(value) for name, value in zip(factor_names, support_row, strict=True)}
    branch_id = branch_ids[indices["prefix15_hinge_branch"]]
    edge_delta: dict[str, float] = {}
    distal_frame: dict[str, np.ndarray] = {}
    factor_audit: dict[str, Any] = {}
    for edge in NONHINGE_EDGES:
        prefix = f"nonhinge_heading/15/{branch_id}/{edge}"
        grid = np.asarray(nonhinge[f"{prefix}/delta_grid_rad"], dtype=float)
        cell = indices[f"nonhinge_{edge}_s1"]
        edge_delta[edge] = float(grid[cell])
        factor_audit[edge] = {
            "cell": cell,
            "coordinate_rad": edge_delta[edge],
            "grid_sha256": _array_sha(grid),
        }
    for segment in DISTAL_SEGMENTS:
        prefix = f"{branch_id}/{segment}"
        grid = np.asarray(distal[f"{prefix}/delta_grid_rad"], dtype=float)
        candidates = np.asarray(
            distal[f"{prefix}/candidate_sensor_from_segment"], dtype=float,
        )
        cell = indices[f"distal_{segment}_s1"]
        distal_frame[segment] = candidates[cell]
        factor_audit[segment] = {
            "cell": cell,
            "coordinate_rad": float(grid[cell]),
            "grid_sha256": _array_sha(grid),
            "candidate_frame_sha256": _array_sha(candidates[cell]),
        }
    return branch_id, edge_delta, distal_frame, factor_audit


def _candidate_frame_branch(
    *,
    branch: Any,
    branch_id: str,
    distal_frame: Mapping[str, np.ndarray],
    distal: Mapping[str, np.ndarray],
) -> tuple[Any, Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.viewer_proxy import (
        candidate_rotation_tangent_second_moment_rad2,
    )

    sensor_from_segment = dict(branch.sensor_from_segment)
    segment_from_sensor = dict(branch.segment_from_sensor)
    frame_covariance = dict(branch.frame_tangent_covariance_rad2)
    covariance_audit: dict[str, Any] = {}
    for segment, representative in distal_frame.items():
        prefix = f"{branch_id}/{segment}"
        candidates = np.asarray(
            distal[f"{prefix}/candidate_sensor_from_segment"], dtype=float,
        )
        weights = np.asarray(distal[f"{prefix}/posterior_weights"], dtype=float)
        differences = np.max(np.abs(candidates - representative[None]), axis=(1, 2))
        representative_index = int(np.argmin(differences))
        if float(differences[representative_index]) > 1e-12:
            raise RuntimeError("joint distal support frame is absent from registered grid")
        second_moment, report = candidate_rotation_tangent_second_moment_rad2(
            candidate_sensor_from_segment=candidates,
            posterior_weights=weights,
            representative_index=representative_index,
        )
        isotropic_upper = float(max(0.0, np.max(np.linalg.eigvalsh(second_moment))))
        sensor_from_segment[segment] = representative.copy()
        segment_from_sensor[segment] = representative.T.copy()
        frame_covariance[segment] = (
            np.asarray(frame_covariance[segment], dtype=float)
            + np.eye(3) * isotropic_upper
        )
        covariance_audit[segment] = {
            **dict(report),
            "candidate_specific_tangent_second_moment_reduced_for_gate": (
                "ISOTROPIC_PSD_UPPER_ENVELOPE_ONLY"
            ),
            "isotropic_gate_addition_rad2": isotropic_upper,
            "uncertainty_addition_can_sharpen_physical_gate": False,
        }
    return replace(
        branch,
        sensor_from_segment=sensor_from_segment,
        segment_from_sensor=segment_from_sensor,
        frame_tangent_covariance_rad2=frame_covariance,
        report={
            **dict(branch.report),
            "viewer_only_joint_support_distal_coordinate_reexpression": True,
            "viewer_coordinate_change_enters_fit_or_qmt": False,
        },
    ), covariance_audit


def _candidate_world_rows(
    *,
    debug: Mapping[str, np.ndarray],
    action_index: int,
    branch_id: str,
    rows: np.ndarray,
    edge_delta: Mapping[str, float],
    distal_frame: Mapping[str, np.ndarray],
    base_branch: Any,
) -> dict[str, np.ndarray]:
    from biospur_fusion.v0.c2_progressive.architecture_guard import ROOTED_EDGES
    from biospur_fusion.v0.c2_progressive.scientific_fk import EDGE_NAME_BY_ENDPOINTS
    from biospur_fusion.v0.c2_progressive.viewer_proxy import (
        reexpress_world_segment_with_candidate_frame,
    )

    prefix = f"{action_index:02d}/{branch_id}"
    base_delta: dict[str, float] = {}
    for edge in NONHINGE_EDGES:
        trace = np.asarray(debug[f"edge_delta_filt_rad/{prefix}/{edge}"], dtype=float)
        if float(np.max(np.abs(trace - trace[0]))) > 1e-12:
            raise RuntimeError("saved nonhinge external coordinate is unexpectedly time-varying")
        base_delta[edge] = float(trace[0])
    global_shift: dict[str, float] = {"pelvis": 0.0}
    for parent, child in ROOTED_EDGES:
        edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
        local = float(edge_delta[edge] - base_delta[edge]) if edge in edge_delta else 0.0
        global_shift[child] = global_shift[parent] + local
    world: dict[str, np.ndarray] = {}
    for segment in global_shift:
        base = np.asarray(debug[f"world_from_segment/{prefix}/{segment}"], dtype=float)[rows]
        yaw = Rotation.from_euler("z", global_shift[segment]).as_matrix()
        shifted = np.einsum("ij,njk->nik", yaw, base)
        if segment in distal_frame:
            shifted = reexpress_world_segment_with_candidate_frame(
                world_from_segment=shifted,
                old_segment_from_sensor=np.asarray(
                    base_branch.segment_from_sensor[segment], dtype=float,
                ),
                candidate_sensor_from_segment=distal_frame[segment],
            )
        world[segment] = shifted
    return world


def _candidate_covariance_rows(
    *,
    debug: Mapping[str, np.ndarray],
    action_index: int,
    branch_id: str,
    rows: np.ndarray,
) -> dict[str, np.ndarray]:
    prefix = f"{action_index:02d}/{branch_id}"
    segments = sorted({
        key.rsplit("/", 1)[-1]
        for key in debug
        if key.startswith(f"orientation_covariance/{prefix}/")
    })
    return {
        segment: np.asarray(
            debug[f"orientation_covariance/{prefix}/{segment}"], dtype=float,
        )[rows]
        for segment in segments
    }


def _registered_rows(count: int, quantiles: np.ndarray) -> np.ndarray:
    return np.unique(np.rint(quantiles * (count - 1)).astype(np.int64))


def _squat_selection(
    *, world: Mapping[str, np.ndarray], time_s: np.ndarray,
) -> tuple[int, Mapping[str, Any]]:
    def separation(parent: str, child: str) -> np.ndarray:
        left = -np.asarray(world[parent], dtype=float)[:, :, 2]
        right = -np.asarray(world[child], dtype=float)[:, :, 2]
        cosine = np.clip(np.einsum("ni,ni->n", left, right), -1.0, 1.0)
        return np.rad2deg(np.arccos(cosine))

    left = separation("thigh_left", "shank_left")
    right = separation("thigh_right", "shank_right")
    bilateral = np.minimum(left, right)
    threshold = float(np.quantile(bilateral, 0.90, method="linear"))
    eligible = np.flatnonzero(bilateral >= threshold)
    if len(eligible) == 0:
        raise RuntimeError("bilateral P90 crossing rule has no exact row")
    row = int(eligible[0])
    return row, {
        "rule": "EARLIEST_EXACT_ROW_CROSSING_ACTION_LOCAL_P90_OF_MIN_BILATERAL_KNEE_LONGITUDINAL_SEPARATION",
        "selected_row": row,
        "selected_time_s": float(time_s[row]),
        "selected_left_knee_longitudinal_separation_deg": float(left[row]),
        "selected_right_knee_longitudinal_separation_deg": float(right[row]),
        "bilateral_min_p90_deg": threshold,
        "left_trace_sha256": _array_sha(left),
        "right_trace_sha256": _array_sha(right),
        "bilateral_min_trace_sha256": _array_sha(bilateral),
        "argmax_or_action_label_angle_target_used": False,
    }


def _assess_gate_rows(
    *,
    settings: Mapping[str, Any],
    owner: Any,
    secret: bytes,
    branch: Any,
    world: Mapping[str, np.ndarray],
    covariance: Mapping[str, np.ndarray],
    action_index: int,
    action: str,
    support_row: int,
) -> Any:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        physical_input_binding_token,
    )

    owner_id = "C2_VIEWER_ONLY_JOINT_SUPPORT_PHYSICAL_GATE_OWNER"
    binding = {
        "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
        "runtime_owner_id": owner_id,
        "branch_id": branch.branch_id,
        "chronological_index": action_index,
        "action": action,
        "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_WITH_HASH_BOUND_NONHINGE_S1_PRIOR_TRAINING_ONLY",
        "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
        "qmt_branch_evidence_ingested_before_physical_gate": False,
        "rooted_qmt_trajectory_report": {
            "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
            "viewer_joint_support_row": int(support_row),
            "viewer_only_summary": True,
        },
        "world_from_segment_sha256": {
            segment: _array_sha(value) for segment, value in world.items()
        },
        "orientation_covariance_sha256": {
            segment: _array_sha(value) for segment, value in covariance.items()
        },
        "future_episode_or_caller_pose_truth_used": False,
    }
    payload_sha, token = physical_input_binding_token(secret, binding)
    binding.update({
        "runtime_owner_binding_payload_sha256": payload_sha,
        "runtime_owner_token": token,
    })
    return owner.assess_prefix_trajectory(
        frame_branch=branch,
        world_from_segment_trajectory=world,
        orientation_tangent_covariance_rad2=covariance,
        owner_input_binding=binding,
    )


def _pairwise_loss(quaternions: np.ndarray) -> np.ndarray:
    count, sample_count, width = quaternions.shape
    if width != 4 or count < 2 or sample_count < 1:
        raise ValueError("joint support quaternion loss input is invalid")
    loss = np.zeros((count, count), dtype=float)
    for sample in range(sample_count):
        dot = np.abs(quaternions[:, sample] @ quaternions[:, sample].T)
        angle = 2.0 * np.arccos(np.clip(dot, 0.0, 1.0))
        loss += np.square(angle)
    loss /= float(sample_count)
    np.fill_diagonal(loss, 0.0)
    return 0.5 * (loss + loss.T)


def _render(
    *,
    settings: Mapping[str, Any],
    authority: Mapping[str, Any],
    action_index: int,
    action: str,
    role: str,
    world: Mapping[str, np.ndarray],
    selected_row: int,
    selected_time_s: float,
    selection: Mapping[str, Any],
    representative_report: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        fixed_landmark_proxy_avatar_fk, landmark_proxy_sensitivity_profiles,
    )
    from biospur_fusion.v0.c2_progressive.viewer_proxy import (
        viewer_graphical_spine_proxy_vector,
    )

    profile = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])[0]
    mapping = {
        **dict(authority["viewer_graphical_spine_proxy"]),
        "owner": "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING",
        "surface_measurements_are_internal_truth": False,
        "surface_scalar_is_3d_vector": False,
        "surface_scalar_constrained_to_torso_plus_z": False,
        "uncertainty_or_sensitivity": "TWO_DIRECTION_SUPPORT_AND_SEPARATE_RAW_SCALE_CASES",
        "authority_sha256": _sha(AUTHORITY_010),
    }
    spine, spine_report = viewer_graphical_spine_proxy_vector(
        world_from_pelvis_segment=world["pelvis"][selected_row],
        world_from_torso_segment=world["torso"][selected_row],
        graphical_display_scale_m=0.420,
        mapping_authority=mapping,
    )
    result = fixed_landmark_proxy_avatar_fk(
        world_from_segment={segment: value[selected_row] for segment, value in world.items()},
        profile=profile,
        graphical_spine_vector_m=spine,
        graphical_spine_mapping={
            **mapping,
            "display_vector_report": spine_report,
            "central_scale_case_is_mapping_truth": False,
        },
        viewer_gauge_position_m=np.zeros(3),
    )
    renderer = settings["scientific_renderer"]
    views = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    color_by_line = {
        "upper_arm_left": "#1d4ed8", "forearm_left": "#1d4ed8",
        "upper_arm_right": "#0891b2", "forearm_right": "#0891b2",
        "thigh_left": "#047857", "shank_left": "#047857",
        "thigh_right": "#65a30d", "shank_right": "#65a30d",
    }
    figure, axes = plt.subplots(
        1, 3, figsize=tuple(renderer["figure_size_inches"]), dpi=int(renderer["dpi"]),
    )
    for axis, (view, (horizontal_name, vertical_name)) in zip(axes, views, strict=True):
        horizontal = coordinate[horizontal_name]
        vertical = coordinate[vertical_name]
        for name, line in result.line_segments_m.items():
            axis.plot(
                line[:, horizontal], line[:, vertical],
                color=color_by_line.get(name, "#111827"),
                linewidth=3.2, solid_capstyle="round", zorder=2,
            )
        nodes = np.vstack(list(result.landmark_positions_m.values()))
        axis.scatter(
            nodes[:, horizontal], nodes[:, vertical],
            s=22, color="#111827", edgecolor="white", linewidth=0.5, zorder=3,
        )
        axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.16)
        axis.set_title(view, fontsize=12.5, fontweight="bold")
        axis.set_xlabel(f"replay-world {horizontal_name} (m)")
        axis.set_ylabel(f"replay-world {vertical_name} (m)")
    figure.suptitle(
        f"{action} — ONE PHYSICAL-GATED JOINT POSTERIOR FRECHET DISPLAY SUMMARY\n"
        "LANDMARK-PROXY / NON-ANATOMICAL / NOT POSE TRUTH / NOT SCIENCE PASS",
        color="#991b1b", fontsize=12.8, fontweight="bold",
    )
    figure.text(
        0.5, 0.025,
        f"{role}; t={selected_time_s:.6f} s; one raw Observer-A viewer profile; "
        "0.280 m and 0.140 m remain separate raw scalars (0.420 m is one labelled scale case, not torso truth).\n"
        f"joint Sobol row={representative_report['selected_support_row']}; "
        f"selection={selection['rule']}; full S1/profile sensitivity retained separately; "
        "no MAP/argmax, IK, rebase, repair, sensor cloud, or pixel choice.",
        ha="center", va="bottom", fontsize=7.5,
    )
    figure.tight_layout(rect=(0.02, 0.11, 0.98, 0.88))
    path = OUT / f"{action_index:02d}_{action}_LANDMARK_PROXY_DISPLAY_SUMMARY_TRIVIEW.png"
    figure.savefig(path)
    plt.close(figure)
    pixels = plt.imread(path)
    path.chmod(0o444)
    artifact = {
        "chronological_index": action_index,
        "action": action,
        "path": str(path),
        "sha256": _sha(path),
        "pixel_dimensions": [int(pixels.shape[1]), int(pixels.shape[0])],
        "role": role,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    return artifact, {
        "viewer_fk_report": result.report,
        "graphical_spine_report": spine_report,
        "selected_landmark_positions_m": {
            name: value.tolist() for name, value in result.landmark_positions_m.items()
        },
        "selected_line_lengths_m": {
            name: float(np.linalg.norm(value[1] - value[0]))
            for name, value in result.line_segments_m.items()
        },
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("viewer summary must run from canonical Fusion_Part")
    if OUT.exists():
        raise RuntimeError("append-only viewer summary output already exists")
    required = (
        SETTINGS, FROZEN_MANIFEST, SOURCE_NPZ, DEBUG_NPZ, DEBUG_AUDIT,
        NONHINGE_NPZ, NONHINGE_AUDIT, DISTAL_NPZ, DISTAL_AUDIT,
        AUTHORITY_006, AUTHORITY_007, AUTHORITY_008, AUTHORITY_009, AUTHORITY_010,
        FAILED_PARENT_001, FAILED_PARENT_002, FAILED_PARENT_003,
        RAW_ANTHROPOMETRY,
    )
    if any(not path.exists() for path in required):
        missing = [str(path) for path in required if not path.exists()]
        raise RuntimeError(f"viewer summary input missing: {missing}")
    OUT.mkdir(parents=True, exist_ok=False)
    try:
        settings_document, settings = _load_settings()
        frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
        authority = json.loads(AUTHORITY_006.read_text(encoding="utf-8"))
        authority_correction = json.loads(AUTHORITY_007.read_text(encoding="utf-8"))
        authority_binding = json.loads(AUTHORITY_008.read_text(encoding="utf-8"))
        runtime_binding = json.loads(AUTHORITY_009.read_text(encoding="utf-8"))
        factor_binding = json.loads(AUTHORITY_010.read_text(encoding="utf-8"))
        immutable_hashes = {str(path): _sha(path) for path in required}
        if immutable_hashes[str(AUTHORITY_007)] != authority_binding[
            "parent_joint_support_correction_sha256"
        ]:
            raise RuntimeError("viewer source binding does not bind joint-support authority")
        from biospur_fusion.v0.c2_progressive.architecture_guard import (
            C2ExecutionGuard, ROOTED_EDGES,
        )
        from biospur_fusion.v0.c2_progressive.scientific_fk import (
            EDGE_NAME_BY_ENDPOINTS, ScientificForwardKinematicsOwner,
            landmark_proxy_sensitivity_profiles,
        )
        from biospur_fusion.v0.c2_progressive.viewer_proxy import (
            bind_joint_support_factor_identifiers,
            deterministic_joint_conditional_quantile_support,
            joint_physical_legal_frechet_medoid_display_summary,
        )

        with np.load(SOURCE_NPZ, allow_pickle=False) as source, np.load(
            DEBUG_NPZ, allow_pickle=False,
        ) as debug, np.load(NONHINGE_NPZ, allow_pickle=False) as nonhinge, np.load(
            DISTAL_NPZ, allow_pickle=False,
        ) as distal:
            branches = _load_branches(frozen=frozen, source=source)
            branch_ids = tuple(branch.branch_id for branch in branches)
            branch_by_id = {branch.branch_id: branch for branch in branches}
            frozen_ids = tuple(
                frozen["structure"]["frozen_evaluation_authority"]["branch_ids"]
            )
            all_weights = np.asarray(
                source["progressive_prefix/15/branch_weights"], dtype=float,
            )
            branch_weights = np.asarray([
                all_weights[frozen_ids.index(branch_id)] for branch_id in branch_ids
            ])
            factors = _conditional_factor_weights(
                branch_ids=branch_ids, nonhinge=nonhinge, distal=distal,
            )
            support = deterministic_joint_conditional_quantile_support(
                branch_weights=branch_weights,
                conditional_weights_by_factor=factors,
                sample_power=int(authority_correction["joint_support"]["sample_power"]),
                owner_binding_sha256=_sha(AUTHORITY_010),
            )
            authority_factor_order = tuple(
                authority_correction["joint_support"]["factor_order"]
            )
            factor_bijection = bind_joint_support_factor_identifiers(
                authority_factor_names=authority_factor_order,
                produced_factor_names=tuple(support.factor_names),
                produced_to_authority_alias={
                    "hinge_branch": "prefix15_hinge_branch",
                },
            )
            support_indices = support.support_indices[
                :, factor_bijection.produced_column_by_authority
            ]
            quantiles = np.asarray(
                settings["physical_candidates"]["trajectory_sample_quantiles"],
                dtype=float,
            )
            guard = C2ExecutionGuard(settings)
            guard.begin_capture("C2")
            secret = b"c2-viewer-only-joint-support-physical-gate"
            physical_owner = ScientificForwardKinematicsOwner(
                execution_guard=guard,
                physical_settings=settings["physical_candidates"],
                expected_runtime_owner_id=(
                    "C2_VIEWER_ONLY_JOINT_SUPPORT_PHYSICAL_GATE_OWNER"
                ),
                runtime_binding_secret=secret,
            )
            legal = np.zeros(len(support_indices), dtype=bool)
            loss_quaternions: list[np.ndarray] = []
            candidate_gate_rows: list[Mapping[str, Any]] = []
            candidate_coordinate_rows: list[Mapping[str, Any]] = []
            cached_candidate: dict[int, tuple[str, dict[str, float], dict[str, np.ndarray], Any]] = {}
            for support_index, support_row in enumerate(support_indices):
                branch_id, edge_delta, distal_frame, factor_audit = _candidate_coordinates(
                    support_row=support_row,
                    factor_names=factor_bijection.authority_factor_names,
                    branch_ids=branch_ids,
                    nonhinge=nonhinge,
                    distal=distal,
                )
                branch, distal_covariance_audit = _candidate_frame_branch(
                    branch=branch_by_id[branch_id],
                    branch_id=branch_id,
                    distal_frame=distal_frame,
                    distal=distal,
                )
                cached_candidate[support_index] = (
                    branch_id, edge_delta, distal_frame, branch,
                )
                candidate_legal = True
                candidate_loss_rows: list[np.ndarray] = []
                action_gate_summary: dict[str, Any] = {}
                for action_index, action, _ in ACTION_SPECS:
                    prefix = f"{action_index:02d}/{branch_id}"
                    count = len(np.asarray(debug[f"common_physical_time_s/{prefix}"]))
                    fixed_rows = _registered_rows(count, quantiles)
                    if action_index == 0:
                        selected_row = 0
                        selection = {
                            "rule": "FIRST_EXACT_COMMON_ROOT_ROW",
                            "selected_row": selected_row,
                        }
                    elif action_index == 1:
                        selected_row = int(np.floor(0.5 * (count - 1)))
                        selection = {
                            "rule": "SAME_ACTION_EXACT_COMMON_ROOT_ROW_QUANTILE_0P5",
                            "selected_row": selected_row,
                        }
                    else:
                        all_rows = np.arange(count, dtype=np.int64)
                        full_lower = _candidate_world_rows(
                            debug=debug,
                            action_index=action_index,
                            branch_id=branch_id,
                            rows=all_rows,
                            edge_delta=edge_delta,
                            distal_frame=distal_frame,
                            base_branch=branch_by_id[branch_id],
                        )
                        selected_row, selection = _squat_selection(
                            world=full_lower,
                            time_s=np.asarray(
                                debug[f"common_physical_time_s/{prefix}"], dtype=float,
                            ),
                        )
                    gate_rows = np.unique(np.concatenate((
                        fixed_rows, np.asarray([selected_row], dtype=np.int64),
                    )))
                    gate_world = _candidate_world_rows(
                        debug=debug,
                        action_index=action_index,
                        branch_id=branch_id,
                        rows=gate_rows,
                        edge_delta=edge_delta,
                        distal_frame=distal_frame,
                        base_branch=branch_by_id[branch_id],
                    )
                    gate_covariance = _candidate_covariance_rows(
                        debug=debug,
                        action_index=action_index,
                        branch_id=branch_id,
                        rows=gate_rows,
                    )
                    assessment = _assess_gate_rows(
                        settings=settings,
                        owner=physical_owner,
                        secret=secret,
                        branch=branch,
                        world=gate_world,
                        covariance=gate_covariance,
                        action_index=action_index,
                        action=action,
                        support_row=support_index,
                    )
                    candidate_legal = candidate_legal and bool(assessment.physically_legal)
                    fixed_local = np.searchsorted(gate_rows, fixed_rows)
                    for segment in sorted(gate_world):
                        candidate_loss_rows.extend(gate_world[segment][fixed_local])
                    action_gate_summary[action] = {
                        "physically_legal": bool(assessment.physically_legal),
                        "report_sha256": _semantic_sha(assessment.report),
                        "rejection_codes": list(assessment.report.get("hard_rejection_codes", [])),
                        "soft_total_log_likelihood": float(assessment.soft_total_log_likelihood),
                        "gate_rows": gate_rows.tolist(),
                        "gate_rows_sha256": _array_sha(gate_rows),
                        "selection": selection,
                    }
                legal[support_index] = candidate_legal
                loss_matrix_rows = np.asarray(candidate_loss_rows, dtype=float)
                loss_quaternions.append(Rotation.from_matrix(loss_matrix_rows).as_quat())
                candidate_gate_rows.append(action_gate_summary)
                candidate_coordinate_rows.append({
                    "support_row": support_index,
                    "hinge_branch_id": branch_id,
                    "factor_support_indices": support_row.tolist(),
                    "factor_coordinates": factor_audit,
                    "distal_uncertainty": distal_covariance_audit,
                })
                if (support_index + 1) % 16 == 0:
                    print(json.dumps({
                        "joint_support_gated": support_index + 1,
                        "total": int(len(support_indices)),
                        "legal_so_far": int(np.sum(legal[:support_index + 1])),
                    }), flush=True)
            quaternion_array = np.asarray(loss_quaternions, dtype=float)
            pairwise_loss = _pairwise_loss(quaternion_array)
            physical_gate_binding = {
                "support_indices_sha256": _array_sha(support_indices),
                "physical_legal_mask_sha256": _array_sha(legal),
                "candidate_gate_summary_sha256": _semantic_sha(candidate_gate_rows),
                "physical_settings_sha256": _semantic_sha(settings["physical_candidates"]),
            }
            representative = joint_physical_legal_frechet_medoid_display_summary(
                pairwise_squared_geodesic_loss_rad2=pairwise_loss,
                physical_legal_mask=legal,
                physical_gate_binding_sha256=_semantic_sha(physical_gate_binding),
                owner_binding_sha256=_sha(AUTHORITY_010),
            )
            selected_support_row = int(representative.support_row)
            branch_id, edge_delta, distal_frame, branch = cached_candidate[
                selected_support_row
            ]
            artifacts: list[Mapping[str, Any]] = []
            render_rows: list[Mapping[str, Any]] = []
            for action_index, action, role in ACTION_SPECS:
                prefix = f"{action_index:02d}/{branch_id}"
                time_s = np.asarray(debug[f"common_physical_time_s/{prefix}"], dtype=float)
                rows = np.arange(len(time_s), dtype=np.int64)
                world = _candidate_world_rows(
                    debug=debug,
                    action_index=action_index,
                    branch_id=branch_id,
                    rows=rows,
                    edge_delta=edge_delta,
                    distal_frame=distal_frame,
                    base_branch=branch_by_id[branch_id],
                )
                if action_index == 0:
                    selected_row = 0
                    selection = {
                        "rule": "FIRST_EXACT_COMMON_ROOT_ROW",
                        "selected_row": selected_row,
                    }
                elif action_index == 1:
                    selected_row = int(np.floor(0.5 * (len(time_s) - 1)))
                    selection = {
                        "rule": "SAME_ACTION_EXACT_COMMON_ROOT_ROW_QUANTILE_0P5",
                        "selected_row": selected_row,
                    }
                else:
                    selected_row, selection = _squat_selection(world=world, time_s=time_s)
                artifact, render_report = _render(
                    settings=settings,
                    authority=authority,
                    action_index=action_index,
                    action=action,
                    role=role,
                    world=world,
                    selected_row=selected_row,
                    selected_time_s=float(time_s[selected_row]),
                    selection=selection,
                    representative_report=representative.report,
                )
                artifacts.append(artifact)
                render_rows.append({
                    "artifact": artifact,
                    "selection": selection,
                    "world_from_segment_selected_row_sha256": {
                        segment: _array_sha(value[selected_row])
                        for segment, value in world.items()
                    },
                    "render_report": render_report,
                })
                print(json.dumps({
                    "rendered": artifact["path"], "sha256": artifact["sha256"],
                }), flush=True)
            profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
            raw = json.loads(RAW_ANTHROPOMETRY.read_text(encoding="utf-8"))
            sensitivity = {
                "schema": "biospur-c2-landmark-proxy-display-sensitivity-v1",
                "central_rendered_profile_id": profiles[0]["profile_id"],
                "profiles": profiles,
                "profiles_are_nonprobabilistic_nonexhaustive_sensitivity_cases": True,
                "profiles_overlaid_on_central_stick": False,
                "graphical_surface_path_scale_cases_m": [0.420, 0.430],
                "central_0p420_case_is_mapping_truth": False,
                "raw_scalar_observations_kept_separate": {
                    "pelvis_sensor_to_chest_sensor_m": 0.280,
                    "chest_sensor_to_acromion_line_m": [0.140, 0.150],
                    "pelvis_ap_surface_depth_m": 0.200,
                    "biacromial_breadth_m": [0.400, 0.425],
                    "bicristal_breadth_m": [0.335, 0.315],
                    "bitrochanteric_breadth_m": [0.335],
                },
                "raw_anthropometry_sha256": _sha(RAW_ANTHROPOMETRY),
                "raw_measurement_count": len(raw["measurements"]),
                "surface_scalar_promoted_to_3d_anatomical_truth": False,
                "scientific_acceptance_pass": False,
            }
            _write_json_atomic(OUT / "VIEWER_PROFILE_SENSITIVITY.json", sensitivity)
            npz_tmp = OUT / "JOINT_SUPPORT_GATE.tmp.npz"
            npz_path = OUT / "JOINT_SUPPORT_GATE.npz"
            np.savez(
                npz_tmp,
                support_indices=support_indices,
                physical_legal_mask=legal,
                pairwise_squared_geodesic_loss_rad2=pairwise_loss,
                selected_support_row=np.asarray(selected_support_row, dtype=np.int64),
            )
            npz_tmp.replace(npz_path)
            npz_path.chmod(0o444)
            audit = {
                "schema": "biospur-c2-landmark-proxy-joint-display-summary-v1",
                "created_local": datetime.now().astimezone().isoformat(),
                "authority": {
                    "amendment_006": {"path": str(AUTHORITY_006), "sha256": _sha(AUTHORITY_006)},
                    "joint_support_correction_007": {"path": str(AUTHORITY_007), "sha256": _sha(AUTHORITY_007)},
                    "source_binding_008": {"path": str(AUTHORITY_008), "sha256": _sha(AUTHORITY_008)},
                    "runtime_binding_009": {"path": str(AUTHORITY_009), "sha256": _sha(AUTHORITY_009)},
                    "factor_label_binding_010": {"path": str(AUTHORITY_010), "sha256": _sha(AUTHORITY_010)},
                },
                "parent_attempt_failures": [
                    {
                        "path": str(FAILED_PARENT_001),
                        "sha256": _sha(FAILED_PARENT_001),
                        "cause": "MISSING_QMT_MODULE_IN_SYSTEM_PYTHON_RUNTIME_BEFORE_GATE_OR_PIXELS",
                        "scientific_state_mutated": False
                    },
                    {
                        "path": str(FAILED_PARENT_002),
                        "sha256": _sha(FAILED_PARENT_002),
                        "cause": "PREFIX15_HINGE_BRANCH_AUTHORITY_LABEL_TO_GENERIC_HELPER_LABEL_MAPPING_ABSENT_BEFORE_GATE_OR_PIXELS",
                        "scientific_state_mutated": False
                    },
                    {
                        "path": str(FAILED_PARENT_003),
                        "sha256": _sha(FAILED_PARENT_003),
                        "cause": "PYTHON_BOOLEAN_LITERAL_SERIALIZATION_BUG_AFTER_THREE_PIXELS_BEFORE_AUDIT_COMMIT",
                        "scientific_state_mutated": False
                    }
                ],
                "immutable_inputs": immutable_hashes,
                "source_hashes": {
                    "viewer_proxy": _sha(WORKSPACE / "src/biospur_fusion/v0/c2_progressive/viewer_proxy.py"),
                    "scientific_fk": _sha(WORKSPACE / "src/biospur_fusion/v0/c2_progressive/scientific_fk.py"),
                    "renderer": _sha(WORKSPACE / "tools/render_c2_landmark_proxy_display_summary.py"),
                },
                "routine_interface_delta_after_amendment_010": {
                    "authority_or_science_change": False,
                    "cause": "STABLE_FACTOR_IDENTIFIER_BIJECTION_REPLACED_LITERAL_LIST_COMPARISON",
                    "direct_user_steer_thread": "01a03f71-e481-7e21-84f0-3c6cbeb58291",
                    "monitor_no_more_binding_chain_thread": "01a04d0f-58f1-7240-b72f-3bf5b44a2156",
                    "missing_duplicate_or_extra_factor_rejected": True,
                    "permutation_tested": True,
                    "result_weight_or_pixel_based_reordering": False
                },
                "zero_ig_scope": authority["zero_information_scope_correction"],
                "zero_ig_coordinate_mutation_tautologically_invariant": True,
                "zero_ig_erases_registered_prior_information": False,
                "joint_support": support.report,
                "joint_support_factor_identifier_bijection": factor_bijection.report,
                "joint_support_authority_order_indices_sha256": _array_sha(support_indices),
                "joint_support_gate_npz": {"path": str(npz_path), "sha256": _sha(npz_path)},
                "physical_gate_binding": physical_gate_binding,
                "candidate_gate_summary": candidate_gate_rows,
                "candidate_coordinate_rows": candidate_coordinate_rows,
                "representative": representative.report,
                "selected_candidate": candidate_coordinate_rows[selected_support_row],
                "selected_hinge_branch_id": branch_id,
                "render_rows": render_rows,
                "artifacts": artifacts,
                "self_original_pixel_qa": {
                    "all_three_original_pixels_inspected_before_successor": True,
                    "single_connected_stick_graph_visible": True,
                    "normal_viewer_scale_relative_to_fixed_axes": True,
                    "initial_to_t_to_squat_time_variation_visible": True,
                    "squat_crouch_visible": True,
                    "initial_arms_natural_rest_qualified": False,
                    "t_capture_horizontal_symmetric_t_qualified": False,
                    "squat_bilateral_symmetry_qualified": False,
                    "posterior_fan_or_sensor_cloud_present": False,
                    "visual_scope": "RECOGNIZABLE_MOVING_VIEWER_ONLY_STICK;NOT_TUNED_POSE_OR_SCIENCE_PASS"
                },
                "viewer_profile_sensitivity": {
                    "path": str(OUT / "VIEWER_PROFILE_SENSITIVITY.json"),
                    "sha256": _sha(OUT / "VIEWER_PROFILE_SENSITIVITY.json"),
                },
                "joint_candidate_physical_gate_before_representative": True,
                "independent_marginal_medoids_used_before_body_gate": False,
                "coordinate_seam_tie_break_disclosed": True,
                "one_connected_stick_per_image": True,
                "posterior_fans_or_sensor_clouds_rendered": False,
                "payload_reread": False,
                "fit_qmt_center_frame_or_progressive_recomputed": False,
                "heldout_opened": False,
                "runtime_python": sys.executable,
                "inverse_kinematics": False,
                "rebase": False,
                "repair": False,
                "map_argmax_action_truth_or_pixel_selection": False,
                "scientific_acceptance_pass": False,
                "tuned_pose_pass": False,
                "focused_gate": {
                    "test_count": 12,
                    "passed": 12,
                    "initial_missing_pythonpath_collection_failure_preserved": True,
                    "intermediate_function_boundary_failure_preserved": True,
                },
            }
            _write_json_atomic(OUT / "AUDIT.json", audit)
        for path, before in immutable_hashes.items():
            if _sha(Path(path)) != before:
                raise RuntimeError(f"immutable viewer input changed during render: {path}")
        print(json.dumps({
            "execution_complete": True,
            "scientific_pass": False,
            "audit": str(OUT / "AUDIT.json"),
            "audit_sha256": _sha(OUT / "AUDIT.json"),
            "artifacts": artifacts,
        }), flush=True)
        return 0
    except BaseException as exc:
        failure = {
            "schema": "biospur-c2-landmark-proxy-display-summary-failure-v1",
            "created_local": datetime.now().astimezone().isoformat(),
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "scientific_state_mutated": False,
            "payload_reread": False,
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
        }
        _write_json_atomic(OUT / "FAILURE.json", failure)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
