#!/usr/bin/env python3
"""Bind replay-002 S1 priors into persistent QMT and render three candidates."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import matplotlib

matplotlib.use("Agg", force=True)
import matplotlib.pyplot as plt
import numpy as np
from scipy.spatial.transform import Rotation

import render_c2_fresh_continuous_squat as exact_maps
import render_c2_fresh_distal_longitudinal_support as viewer_helpers
from replay_c2_postfreeze_heading import _reconstruct_frame_branches


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FROZEN_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
SOURCE_DIR = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001"
SOURCE_NPZ = SOURCE_DIR / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
POSTERIOR_DIR = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002"
POSTERIOR_NPZ = POSTERIOR_DIR / "CORRECTED_PREFIX_NONHINGE_STATE.npz"
POSTERIOR_AUDIT = POSTERIOR_DIR / "AUDIT.json"
OUT = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004"
GENERATE_PIXELS = False

NONHINGE_EDGES = (
    "pelvis_torso", "shoulder_left", "shoulder_right", "hip_left", "hip_right",
)
ACTION_SPECS = (
    (0, "00_initial_still", "RETROSPECTIVE_PREFIX15_FROZEN_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH"),
    (1, "02_t_pose", "RETROSPECTIVE_PREFIX15_FROZEN_VIEWER_ONLY_NOT_CAUSAL_METRIC_OR_POSE_TRUTH"),
    (15, "16_squat", "CAUSAL_PREFIX15_TRAINING_ONLY_DIAGNOSTIC_NOT_POSE_TRUTH"),
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


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    if isinstance(value, np.ndarray):
        return _json_compatible(value.tolist())
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(
            _json_compatible(value), handle,
            indent=2, sort_keys=True, allow_nan=False,
        )
        handle.write("\n")
    path.chmod(0o444)


def _persist_derived_state(
    *,
    world_by_action_branch: Mapping[int, Mapping[str, Mapping[str, np.ndarray]]],
    cov_by_action_branch: Mapping[int, Mapping[str, Mapping[str, np.ndarray]]],
    trajectories: Mapping[int, Mapping[str, Any]],
    root_rows_by_action_branch: Mapping[int, Mapping[str, np.ndarray]],
) -> Mapping[str, Any]:
    arrays: dict[str, np.ndarray] = {}
    alignment_audit: dict[str, Any] = {}
    for action_index, branch_rows in world_by_action_branch.items():
        for branch_id, segment_rows in branch_rows.items():
            root_rows = np.asarray(
                root_rows_by_action_branch[action_index][branch_id], dtype=np.int64,
            )
            full_time = np.asarray(
                trajectories[action_index][branch_id].common_physical_time_s,
                dtype=float,
            )
            if (
                root_rows.ndim != 1
                or len(root_rows) == 0
                or np.any(root_rows < 0)
                or np.any(root_rows >= len(full_time))
                or np.any(np.diff(root_rows) <= 0)
            ):
                raise RuntimeError("derived state exact rooted source rows are invalid")
            aligned_time = full_time[root_rows]
            for segment, value in segment_rows.items():
                if (
                    len(value) != len(root_rows)
                    or len(cov_by_action_branch[action_index][branch_id][segment])
                    != len(root_rows)
                ):
                    raise RuntimeError(
                        "derived world/covariance rows differ from exact rooted time rows"
                    )
                arrays[
                    f"world_from_segment/{action_index:02d}/{branch_id}/{segment}"
                ] = np.asarray(value, dtype=float)
                arrays[
                    f"orientation_covariance/{action_index:02d}/{branch_id}/{segment}"
                ] = np.asarray(
                    cov_by_action_branch[action_index][branch_id][segment],
                    dtype=float,
                )
            arrays[
                f"common_physical_time_s/{action_index:02d}/{branch_id}"
            ] = aligned_time
            arrays[
                f"exact_root_source_rows/{action_index:02d}/{branch_id}"
            ] = root_rows
            trajectory = trajectories[action_index][branch_id]
            for edge, value in trajectory.edge_delta_filt_rad.items():
                arrays[
                    f"edge_delta_filt_rad/{action_index:02d}/{branch_id}/{edge}"
                ] = np.asarray(value, dtype=float)[root_rows]
                arrays[
                    f"edge_variance_rad2/{action_index:02d}/{branch_id}/{edge}"
                ] = np.asarray(
                    trajectory.edge_variance_rad2[edge], dtype=float,
                )[root_rows]
            alignment_audit[f"{action_index:02d}/{branch_id}"] = {
                "row_count": int(len(root_rows)),
                "exact_root_source_rows_sha256": _array_sha(root_rows),
                "aligned_common_physical_time_s_sha256": _array_sha(aligned_time),
                "world_row_count_by_segment": {
                    segment: int(len(value))
                    for segment, value in segment_rows.items()
                },
                "covariance_row_count_by_segment": {
                    segment: int(len(
                        cov_by_action_branch[action_index][branch_id][segment]
                    ))
                    for segment in segment_rows
                },
                "all_row_counts_equal": True,
            }
    temporary = OUT / "DERIVED_QMT_ROOTED_STATE.tmp.npz"
    target = OUT / "DERIVED_QMT_ROOTED_STATE.npz"
    np.savez(temporary, **arrays)
    temporary.replace(target)
    target.chmod(0o444)
    return {
        "path": str(target),
        "sha256": _sha(target),
        "array_count": len(arrays),
        "exact_root_rows_and_aligned_time_persisted": True,
        "world_covariance_time_row_count_equality_enforced": True,
        "alignment_by_action_branch": alignment_audit,
        "role": "DERIVED_REUSABLE_QMT_ROOTED_WORLD_AND_COVARIANCE_STATE",
        "scientific_acceptance_pass": False,
    }


def _connection_map(arrays: Mapping[str, np.ndarray], prefix: str):
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.segment_frames import EdgeConnectionVectors

    return {
        edge: EdgeConnectionVectors(
            edge=edge,
            parent=parent,
            child=child,
            parent_sensor_to_joint_m=np.asarray(
                arrays[f"{prefix}/connection/{edge}/parent"], dtype=float,
            ),
            child_sensor_to_joint_m=np.asarray(
                arrays[f"{prefix}/connection/{edge}/child"], dtype=float,
            ),
            covariance_m2=np.asarray(
                arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
            ),
        )
        for edge, parent, child in EDGE_SPECS
    }


def _branch_at_prefix(base: Any, arrays: Mapping[str, np.ndarray], prefix: str) -> Any:
    segment_from_sensor = {
        segment: np.asarray(
            arrays[f"{prefix}/segment_from_sensor/{segment}"], dtype=float,
        )
        for segment in base.segment_from_sensor
    }
    return replace(
        base,
        segment_from_sensor=segment_from_sensor,
        sensor_from_segment={
            segment: value.T.copy() for segment, value in segment_from_sensor.items()
        },
        connection_vectors_by_edge=_connection_map(arrays, prefix),
        report={
            **dict(base.report),
            "derived_heading_frame_prefix": prefix,
            "source_frame_or_connection_mutated": False,
        },
    )


def _run_persistent_owner(
    *,
    settings: Mapping[str, Any],
    frozen: Mapping[str, Any],
    source: Mapping[str, np.ndarray],
    posterior: Mapping[str, np.ndarray],
    source_npz_sha256: str,
    posterior_audit_sha256: str,
):
    from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.heading import (
        PersistentHeadingOwner, _array_sha256,
    )

    frozen_branches = _reconstruct_frame_branches(frozen=frozen, arrays=source)
    branches = [
        _branch_at_prefix(
            branch, source, f"physical_trajectory/15/{branch.branch_id}",
        )
        for branch in frozen_branches
    ]
    branch_ids = tuple(branch.branch_id for branch in branches)
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    chronology = tuple(settings["execution_contract"]["chronological_actions"])
    source_audit_sha = posterior_audit_sha256
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    owner = PersistentHeadingOwner(
        settings["heading"], branches, execution_guard=guard,
        first_chronological_index=0,
    )
    retained: dict[int, dict[str, Any]] = {}
    action_audits = []
    fixed_branch_map = {branch.branch_id: branch for branch in branches}
    for action_index, action in enumerate(chronology[:16]):
        frame_role = (
            "CAUSAL_PREFIX15_FRAME_AND_CONNECTION"
            if action_index == 15
            else "RETROSPECTIVE_PREFIX15_FROZEN_FRAME_AND_CONNECTION"
        )

        prior_rows = []
        for branch_id in branch_ids:
            for edge in NONHINGE_EDGES:
                prefix = f"nonhinge_heading/{action_index:02d}/{branch_id}/{edge}"
                grid = np.asarray(posterior[f"{prefix}/delta_grid_rad"], dtype=float)
                weights = np.asarray(posterior[f"{prefix}/posterior_weights"], dtype=float)
                binding = {
                    "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
                    "branch_id": branch_id,
                    "edge": edge,
                    "chronological_index": action_index,
                    "action": action,
                    "delta_grid_rad_sha256": _array_sha256(grid),
                    "posterior_weights_sha256": _array_sha256(weights),
                    "source_audit_sha256": source_audit_sha,
                }
                prior_rows.append(owner.bind_external_nonhinge_circular_prior(
                    branch_id=branch_id,
                    edge=edge,
                    chronological_index=action_index,
                    action=action,
                    delta_grid_rad=grid,
                    posterior_weights=weights,
                    owner_input_binding=binding,
                ))

        base_time = np.asarray(
            source[f"orientation/{action_index:02d}/{node_by_segment['pelvis']}/time_us"],
            dtype=np.int64,
        ).astype(float) * 1e-6
        span_count = 0
        for branch_id in branch_ids:
            for edge, parent, child in EDGE_SPECS:
                span_prefix = f"heading/{action_index:02d}/{branch_id}/{edge}/"
                bases = sorted({
                    key.rsplit("/", 1)[0]
                    for key in source
                    if key.startswith(span_prefix) and key.endswith("/common_physical_time_s")
                })
                if not bases:
                    owner.record_action_no_update(
                        branch_id=branch_id,
                        edge=edge,
                        chronological_index=action_index,
                        action=action,
                        base_common_physical_time_s=base_time,
                        cause="IMMUTABLE_REPLAY_CONTAINS_NO_GAP_SAFE_SPAN",
                    )
                    continue
                parent_node = node_by_segment[parent]
                child_node = node_by_segment[child]
                for span_base in bases:
                    parent_rows = np.asarray(
                        source[f"{span_base}/selected_parent_source_indices"], dtype=np.int64,
                    )
                    child_rows = np.asarray(
                        source[f"{span_base}/selected_child_source_indices"], dtype=np.int64,
                    )
                    common_time = np.asarray(
                        source[f"{span_base}/common_physical_time_s"], dtype=float,
                    )
                    parent_gyro = np.asarray(
                        source[f"orientation/{action_index:02d}/{parent_node}/gyro_rads"],
                        dtype=float,
                    )[parent_rows]
                    child_gyro = np.asarray(
                        source[f"orientation/{action_index:02d}/{child_node}/gyro_rads"],
                        dtype=float,
                    )[child_rows]
                    parent_quat = np.asarray(
                        source[
                            f"orientation/{action_index:02d}/{parent_node}/quat_world_sensor_wxyz"
                        ], dtype=float,
                    )[parent_rows]
                    child_quat = np.asarray(
                        source[
                            f"orientation/{action_index:02d}/{child_node}/quat_world_sensor_wxyz"
                        ], dtype=float,
                    )[child_rows]
                    binding = {
                        "schema": "biospur-c2-runtime-owned-heading-span-input-v1",
                        "runtime_owner_token": _semantic_sha({
                            "role": "DERIVED_REPLAY002_EXTERNAL_S1_TO_OFFICIAL_QMT",
                            "span_base": span_base,
                            "source_npz_sha256": source_npz_sha256,
                        }),
                        "edge": edge,
                        "chronological_index": action_index,
                        "action": action,
                        "parent_gyro_sha256": _array_sha256(parent_gyro),
                        "child_gyro_sha256": _array_sha256(child_gyro),
                        "parent_quaternion_wxyz_sha256": _array_sha256(parent_quat),
                        "child_quaternion_wxyz_sha256": _array_sha256(child_quat),
                        "common_physical_time_s_sha256": _array_sha256(common_time),
                        "selected_source_row_indices_sha256": _array_sha256(parent_rows),
                    }
                    owner.process_span(
                        branch_id=branch_id,
                        edge=edge,
                        chronological_index=action_index,
                        action=action,
                        parent_gyro_sensor=parent_gyro,
                        child_gyro_sensor=child_gyro,
                        parent_quaternion_world_sensor_wxyz=parent_quat,
                        child_quaternion_world_sensor_wxyz=child_quat,
                        common_physical_time_s=common_time,
                        selected_source_row_indices=parent_rows,
                        owner_input_binding=binding,
                        reset_requested=False,
                        profile_stitch_requested=False,
                    )
                    span_count += 1
        trajectories = {
            branch_id: owner.assemble_action_rooted_trajectory(
                branch_id,
                chronological_index=action_index,
                action=action,
                base_common_physical_time_s=base_time,
            )
            for branch_id in branch_ids
        }
        if action_index in {0, 1, 15}:
            retained[action_index] = trajectories
        action_audits.append({
            "chronological_index": action_index,
            "action": action,
            "frame_role": frame_role,
            "external_prior_bind_count": len(prior_rows),
            "official_qmt_span_count_cumulative": span_count,
            "nonhinge_resultant_range": [
                float(min(row["posterior_resultant"] for row in prior_rows)),
                float(max(row["posterior_resultant"] for row in prior_rows)),
            ],
            "external_cumulative_posterior_multiplied_as_new_evidence": False,
        })
        print(json.dumps({
            "action_index": action_index,
            "action": action,
            "persistent_qmt_complete": True,
        }), flush=True)

    for action_index in (0, 1):
        retained[action_index] = {
            branch_id: owner.derive_retrospective_rooted_trajectory(
                retained[action_index][branch_id],
                branch_id=branch_id,
                external_prior_chronological_index=15,
            )
            for branch_id in branch_ids
        }
    return owner, retained, fixed_branch_map, branches, action_audits


def _full_world_trajectory(
    *,
    settings: Mapping[str, Any],
    source: Mapping[str, np.ndarray],
    trajectory: Any,
    branch: Any,
    branch_ids: tuple[str, ...],
    action_index: int,
):
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS
    from biospur_fusion.v0.c2_progressive.quaternion_contract import (
        qmt_wxyz_to_scipy_active,
    )

    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    pelvis_time_us = np.asarray(
        source[f"orientation/{action_index:02d}/{node_by_segment['pelvis']}/time_us"],
        dtype=np.int64,
    )
    root_rows, maps, map_audit = exact_maps._compose_exact_root_maps(
        source,
        chronological_index=action_index,
        selection_branch_id=branch.branch_id,
        branch_ids=branch_ids,
        pelvis_time_us=pelvis_time_us,
        edge_specs=EDGE_SPECS,
        require_contiguous_root_rows=False,
    )
    trajectory_time_us = np.rint(
        np.asarray(trajectory.common_physical_time_s, dtype=float) * 1e6
    ).astype(np.int64)
    if not np.array_equal(trajectory_time_us, pelvis_time_us):
        raise RuntimeError("persistent trajectory time differs from immutable pelvis grid")
    world: dict[str, np.ndarray] = {}
    covariance: dict[str, np.ndarray] = {}
    for segment, node in node_by_segment.items():
        source_rows = np.asarray([maps[segment][int(row)] for row in root_rows], dtype=np.int64)
        world_from_sensor = qmt_wxyz_to_scipy_active(np.asarray(
            source[f"orientation/{action_index:02d}/{node}/quat_world_sensor_wxyz"],
            dtype=float,
        )[source_rows]).as_matrix()
        raw = np.einsum(
            "nij,jk->nik", world_from_sensor,
            np.asarray(branch.sensor_from_segment[segment], dtype=float),
        )
        delta = np.asarray(trajectory.segment_global_delta_rad[segment], dtype=float)[root_rows]
        yaw = Rotation.from_rotvec(np.column_stack((
            np.zeros(len(delta)), np.zeros(len(delta)), delta,
        ))).as_matrix()
        world[segment] = np.einsum("nij,njk->nik", yaw, raw)
        gap = np.asarray(
            source[f"orientation/{action_index:02d}/{node}/gap_only_covariance_rad2"],
            dtype=float,
        )[source_rows]
        frame_cov = np.asarray(branch.frame_tangent_covariance_rad2[segment], dtype=float)
        covariance[segment] = gap + frame_cov[None]
        covariance[segment][:, 2, 2] += np.asarray(
            trajectory.segment_global_variance_rad2[segment], dtype=float,
        )[root_rows]
    return root_rows, maps, world, covariance, map_audit


def _select_rows(action_index: int, world: Mapping[str, np.ndarray]) -> tuple[int, Mapping[str, Any]]:
    count = len(next(iter(world.values())))
    if action_index == 0:
        return 0, {"rule": "REGISTERED_INITIAL_QUANTILE_0", "row": 0}
    row = int(np.floor(0.5 * (count - 1)))
    return row, {
        "rule": "REGISTERED_RESULT_INDEPENDENT_SAME_ACTION_QUANTILE_0P5",
        "row": row,
        "result_or_action_label_pose_metric_used": False,
    }


def _physical_gate(
    *,
    settings: Mapping[str, Any],
    branch: Any,
    world: Mapping[str, np.ndarray],
    covariance: Mapping[str, np.ndarray],
    selected_row: int,
    action_index: int,
    action: str,
    trajectory_report: Mapping[str, Any],
):
    from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        ScientificForwardKinematicsOwner, physical_input_binding_token,
    )

    count = len(next(iter(world.values())))
    quantile_rows = np.unique(np.rint(
        np.asarray(settings["physical_candidates"]["trajectory_sample_quantiles"])
        * (count - 1)
    ).astype(np.int64))
    gate_rows = np.unique(np.concatenate((quantile_rows, np.asarray([selected_row]))))
    gate_world = {segment: np.asarray(value)[gate_rows] for segment, value in world.items()}
    gate_cov = {segment: np.asarray(value)[gate_rows] for segment, value in covariance.items()}
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    secret = b"c2-replay002-s1-qmt-physical-owner"
    owner_id = "C2_REPLAY002_S1_QMT_ROOTED_DERIVED_OWNER"
    owner = ScientificForwardKinematicsOwner(
        execution_guard=guard,
        physical_settings=settings["physical_candidates"],
        expected_runtime_owner_id=owner_id,
        runtime_binding_secret=secret,
    )
    binding = {
        "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
        "runtime_owner_id": owner_id,
        "branch_id": branch.branch_id,
        "chronological_index": action_index,
        "action": action,
        "source": "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_WITH_HASH_BOUND_NONHINGE_S1_PRIOR_TRAINING_ONLY",
        "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
        "qmt_branch_evidence_ingested_before_physical_gate": False,
        "rooted_qmt_trajectory_report": dict(trajectory_report),
        "world_from_segment_sha256": {
            segment: _array_sha(value) for segment, value in gate_world.items()
        },
        "orientation_covariance_sha256": {
            segment: _array_sha(value) for segment, value in gate_cov.items()
        },
        "future_episode_or_caller_pose_truth_used": False,
    }
    payload_sha, token = physical_input_binding_token(secret, binding)
    binding.update({
        "runtime_owner_binding_payload_sha256": payload_sha,
        "runtime_owner_token": token,
    })
    assessment = owner.assess_prefix_trajectory(
        frame_branch=branch,
        world_from_segment_trajectory=gate_world,
        orientation_tangent_covariance_rad2=gate_cov,
        owner_input_binding=binding,
    )
    return assessment, gate_rows


def _branch_geodesic_loss(
    candidate: str,
    other: str,
    world_by_action_branch: Mapping[int, Mapping[str, Mapping[str, np.ndarray]]],
) -> float:
    values = []
    for _, branch_rows in world_by_action_branch.items():
        left = branch_rows[candidate]
        right = branch_rows[other]
        for segment in sorted(left):
            relative = np.einsum(
                "nij,njk->nik", np.transpose(left[segment], (0, 2, 1)), right[segment],
            )
            values.extend(np.linalg.norm(Rotation.from_matrix(relative).as_rotvec(), axis=1) ** 2)
    return float(np.mean(values))


def _render(
    *,
    settings: Mapping[str, Any],
    action_index: int,
    action: str,
    role: str,
    branch: Any,
    world: Mapping[str, np.ndarray],
    selected_row: int,
    selection_audit: Mapping[str, Any],
    posterior_summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_fk_points, landmark_proxy_sensitivity_profiles,
    )

    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    profile = profiles[0]
    result = landmark_proxy_fk_points(
        root_sensor_position_m=np.zeros(3),
        world_from_segment={segment: value[selected_row] for segment, value in world.items()},
        segment_from_sensor=branch.segment_from_sensor,
        connection_vectors_by_edge=branch.connection_vectors_by_edge,
        profile=profile,
    )
    lines = viewer_helpers._line_map(result)
    renderer = settings["scientific_renderer"]
    views = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    figure, axes = plt.subplots(1, 3, figsize=(22.5, 8.4), dpi=int(renderer["dpi"]))
    for axis, (view, (horizontal_name, vertical_name)) in zip(axes, views, strict=True):
        horizontal = coordinate[horizontal_name]
        vertical = coordinate[vertical_name]
        for name, line in lines.items():
            axis.plot(
                line[:, horizontal], line[:, vertical],
                color="#111827", linewidth=2.8, solid_capstyle="round", zorder=2,
            )
        joints = np.vstack(list(result.shared_joint_positions_m.values()))
        distal = np.vstack(list(result.distal_landmark_positions_m.values()))
        axis.scatter(joints[:, horizontal], joints[:, vertical], s=30, color="#2563eb", zorder=3)
        axis.scatter(distal[:, horizontal], distal[:, vertical], s=35, marker="x", color="#dc2626", zorder=3)
        axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
        axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.18)
        axis.set_title(view, fontsize=13, fontweight="bold")
        axis.set_xlabel(f"replay-world {horizontal_name} (m)")
        axis.set_ylabel(f"replay-world {vertical_name} (m)")
    figure.suptitle(
        f"{action} — SINGLE HINGE-MEDOID / JOINT-S1 MOMENT DIAGNOSTIC\n"
        f"{role} / FULL S1 NOT PHYSICALLY QUALIFIED / NOT SCIENCE PASS",
        color="#991b1b", fontsize=14, fontweight="bold",
    )
    figure.text(
        0.5, 0.025,
        "Official persistent QMT deltaFilt + hash-bound replay-002 full-S1 nonhinge moment boundary; "
        "fixed RAW Observer-A scale profile; no MAP/argmax heading, IK, rebase, repair, action-pose truth, or pixel selection.\n"
        f"branch={branch.branch_id}; row rule={selection_audit['rule']}; "
        f"nonhinge R range={posterior_summary['minimum_resultant']:.4f}–{posterior_summary['maximum_resultant']:.4f}; representative physical check only; full S1 retained in AUDIT.json.",
        ha="center", va="bottom", fontsize=8.2,
    )
    figure.tight_layout(rect=(0.02, 0.10, 0.98, 0.90))
    path = OUT / f"{action_index:02d}_{action}_S1_MOMENT_DIAGNOSTIC_TRIVIEW.png"
    figure.savefig(path)
    plt.close(figure)
    pixels = plt.imread(path)
    path.chmod(0o444)
    return {
        "chronological_index": action_index,
        "action": action,
        "path": str(path),
        "sha256": _sha(path),
        "pixel_dimensions": [int(pixels.shape[1]), int(pixels.shape[0])],
        "role": role,
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("S1-to-QMT derived viewer requires canonical Fusion_Part")
    if OUT.exists():
        raise RuntimeError("append-only S1-to-QMT output already exists")
    OUT.mkdir(parents=True, exist_ok=False)
    settings_document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings = settings_document["effective_settings"]
    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    source_hash_before = _sha(SOURCE_NPZ)
    posterior_hash_before = _sha(POSTERIOR_NPZ)
    posterior_audit_hash = _sha(POSTERIOR_AUDIT)
    with np.load(SOURCE_NPZ, allow_pickle=False) as source, np.load(
        POSTERIOR_NPZ, allow_pickle=False,
    ) as posterior:
        owner, trajectories, final_branch_map, frozen_branches, action_audits = (
            _run_persistent_owner(
                settings=settings, frozen=frozen, source=source, posterior=posterior,
                source_npz_sha256=source_hash_before,
                posterior_audit_sha256=posterior_audit_hash,
            )
        )
        branch_ids = tuple(branch.branch_id for branch in frozen_branches)
        base_by_id = {branch.branch_id: branch for branch in frozen_branches}
        world_by_action_branch: dict[int, dict[str, Mapping[str, np.ndarray]]] = {}
        cov_by_action_branch: dict[int, dict[str, Mapping[str, np.ndarray]]] = {}
        branch_by_action: dict[int, dict[str, Any]] = {}
        selection_by_action_branch: dict[int, dict[str, tuple[int, Mapping[str, Any]]]] = {}
        gate_by_action_branch: dict[int, dict[str, Any]] = {}
        root_rows_by_action_branch: dict[int, dict[str, np.ndarray]] = {}
        map_audits: dict[int, dict[str, Any]] = {}
        for action_index, action, _ in ACTION_SPECS:
            world_by_action_branch[action_index] = {}
            cov_by_action_branch[action_index] = {}
            branch_by_action[action_index] = {}
            selection_by_action_branch[action_index] = {}
            gate_by_action_branch[action_index] = {}
            root_rows_by_action_branch[action_index] = {}
            for branch_id in branch_ids:
                branch = base_by_id[branch_id]
                root_rows, _, world, covariance, map_audit = _full_world_trajectory(
                    settings=settings,
                    source=source,
                    trajectory=trajectories[action_index][branch_id],
                    branch=branch,
                    branch_ids=branch_ids,
                    action_index=action_index,
                )
                selected_local, selected_audit = _select_rows(action_index, world)
                assessment, gate_rows = _physical_gate(
                    settings=settings,
                    branch=branch,
                    world=world,
                    covariance=covariance,
                    selected_row=selected_local,
                    action_index=action_index,
                    action=action,
                    trajectory_report=trajectories[action_index][branch_id].report,
                )
                world_by_action_branch[action_index][branch_id] = world
                cov_by_action_branch[action_index][branch_id] = covariance
                branch_by_action[action_index][branch_id] = branch
                selection_by_action_branch[action_index][branch_id] = (
                    selected_local, {
                        **dict(selected_audit),
                        "root_source_row": int(root_rows[selected_local]),
                        "physical_gate_rows": gate_rows.tolist(),
                    },
                )
                gate_by_action_branch[action_index][branch_id] = assessment
                root_rows_by_action_branch[action_index][branch_id] = root_rows.copy()
                map_audits[action_index] = map_audit

        legal = [
            branch_id for branch_id in branch_ids
            if all(
                gate_by_action_branch[index][branch_id].physically_legal
                for index, _, _ in ACTION_SPECS
            )
        ]
        if not legal:
            raise RuntimeError("no branch is physical-legal across initial/T/squat")
        frozen_ids = tuple(
            frozen["structure"]["frozen_evaluation_authority"]["branch_ids"]
        )
        prefix15_weights = np.asarray(
            source["progressive_prefix/15/branch_weights"], dtype=float,
        )
        prefix15_prequential_weights = np.asarray(
            source["progressive_prefix/15/prequential_prior_branch_weights"],
            dtype=float,
        )
        if (
            prefix15_weights.shape != (len(frozen_ids),)
            or prefix15_prequential_weights.shape != (len(frozen_ids),)
        ):
            raise RuntimeError("prefix-15 branch weight arrays do not bind all sealed branches")
        legal_weights = np.asarray([
            prefix15_weights[frozen_ids.index(branch_id)] for branch_id in legal
        ], dtype=float)
        if float(np.sum(legal_weights)) <= 0.0:
            raise RuntimeError("prefix-15 physically checked hinge support has zero posterior mass")
        legal_weights /= np.sum(legal_weights)
        expected_loss = {
            candidate: float(sum(
                weight * _branch_geodesic_loss(
                    candidate, other, world_by_action_branch,
                )
                for other, weight in zip(legal, legal_weights, strict=True)
            ))
            for candidate in legal
        }
        selected_branch_id = min(legal, key=lambda value: (expected_loss[value], value))
        derived_state = _persist_derived_state(
            world_by_action_branch=world_by_action_branch,
            cov_by_action_branch=cov_by_action_branch,
            trajectories=trajectories,
            root_rows_by_action_branch=root_rows_by_action_branch,
        )
        artifacts = []
        render_audits = []
        owner_records = owner.audit()["external_nonhinge_circular_prior_records"]
        if GENERATE_PIXELS:
            for action_index, action, role in ACTION_SPECS:
                posterior_index = 15
                records = [
                    row for row in owner_records
                    if row["branch_id"] == selected_branch_id
                    and int(row["chronological_index"]) == posterior_index
                ]
                posterior_summary = {
                    "source_prefix": posterior_index,
                    "minimum_resultant": float(min(row["posterior_resultant"] for row in records)),
                    "maximum_resultant": float(max(row["posterior_resultant"] for row in records)),
                    "bindings": records,
                }
                selected_row, selected_audit = selection_by_action_branch[action_index][selected_branch_id]
                artifact = _render(
                    settings=settings,
                    action_index=action_index,
                    action=action,
                    role=role,
                    branch=branch_by_action[action_index][selected_branch_id],
                    world=world_by_action_branch[action_index][selected_branch_id],
                    selected_row=selected_row,
                    selection_audit=selected_audit,
                    posterior_summary=posterior_summary,
                )
                artifacts.append(artifact)
                render_audits.append({
                    "artifact": artifact,
                    "selection": selected_audit,
                    "posterior": posterior_summary,
                    "physical_assessment": gate_by_action_branch[action_index][selected_branch_id].report,
                    "physically_legal": bool(
                        gate_by_action_branch[action_index][selected_branch_id].physically_legal
                    ),
                })
                print(json.dumps({"rendered": artifact["path"], "sha256": artifact["sha256"]}), flush=True)

    if _sha(SOURCE_NPZ) != source_hash_before or _sha(POSTERIOR_NPZ) != posterior_hash_before:
        raise RuntimeError("immutable saved-array input changed during derived QMT/viewer run")
    audit = {
        "schema": "biospur-c2-replay002-s1-persistent-qmt-rooted-moment-diagnostic-v2",
        "created_local": datetime.now().astimezone().isoformat(),
        "source_replay_npz": {"path": str(SOURCE_NPZ), "sha256": source_hash_before},
        "posterior_replay002_npz": {"path": str(POSTERIOR_NPZ), "sha256": posterior_hash_before},
        "posterior_replay002_audit": {"path": str(POSTERIOR_AUDIT), "sha256": posterior_audit_hash},
        "heading_source_sha256": _sha(WORKSPACE / "src/biospur_fusion/v0/c2_progressive/heading.py"),
        "scientific_fk_source_sha256": _sha(WORKSPACE / "src/biospur_fusion/v0/c2_progressive/scientific_fk.py"),
        "derived_viewer_source_sha256": _sha(
            WORKSPACE / "tools/render_c2_nonhinge_s1_qmt_rooted_debug.py"
        ),
        "one_capture_persistent_heading_owner": True,
        "per_action_reset_or_profile_stitch": False,
        "external_full_s1_prefix_bound_exactly_once": True,
        "external_cumulative_prefix_multiplied_as_independent_evidence": False,
        "official_qmt_state_retained_separately": True,
        "one_coherent_segment_frame_history": True,
        "segment_frame_history": "PHYSICAL_TRAJECTORY_PREFIX15_FIXED_FOR_ACTIONS_00_THROUGH_15",
        "segment_frame_coordinate_switch_inside_persistent_qmt_owner": False,
        "early_viewer_uses_later_frame_calibration": True,
        "early_viewer_is_causal_metric": False,
        "squat_uses_causal_prefix15_frame_and_connection": True,
        "action_owner_audits": action_audits,
        "physical_gate_executed_before_viewer_for_every_candidate": True,
        "moment_representative_physical_check_passed_hinge_branch_ids": legal,
        "full_s1_physical_branch_qualification": False,
        "full_s1_cartesian_support_enumerated": False,
        "joint_s1_moment_representative_physically_checked": True,
        "prefix15_branch_weight_binding": {
            "source_array": "progressive_prefix/15/branch_weights",
            "sha256": _array_sha(prefix15_weights),
            "prequential_source_array": "progressive_prefix/15/prequential_prior_branch_weights",
            "prequential_sha256": _array_sha(prefix15_prequential_weights),
            "frozen_branch_weights_used_for_causal_prefix15_selection": False,
        },
        "prefix15_branch_weights_within_checked_hinge_support": {
            branch_id: float(weight)
            for branch_id, weight in zip(legal, legal_weights, strict=True)
        },
        "bayes_representative_rule": (
            "PREFIX15_WEIGHTED_HINGE_BRANCH_MEDOID_FOR_FIXED_JOINT_S1_CIRCULAR_MOMENT_DIAGNOSTIC"
        ),
        "bayes_expected_geodesic_loss_by_branch": expected_loss,
        "selected_branch_id": selected_branch_id,
        "derived_reusable_state": derived_state,
        "hard_map_or_heading_argmax_used": False,
        "squat_row_selection_rule": "REGISTERED_RESULT_INDEPENDENT_SAME_ACTION_QUANTILE_0P5",
        "squat_action_metric_argmax_used": False,
        "branch_selected_per_frame": False,
        "full_nonhinge_s1_uncertainty_retained_in_metadata": True,
        "render_audits": render_audits,
        "artifacts": artifacts,
        "exact_artifact_count": len(artifacts),
        "pixel_generation_enabled": GENERATE_PIXELS,
        "pixel_generation_deferred_until_bounded_h1_h2_h3_causal_pivot": True,
        "payload_reread": False,
        "heldout_opened": False,
        "fit_or_progressive_recomputed": False,
        "causal_progressive_metrics_mutated": False,
        "inverse_kinematics": False,
        "rebase": False,
        "repair": False,
        "action_label_pose_truth_used": False,
        "pixel_selected_candidate": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
        "focused_regression_scope": {
            "external_s1_and_retrospective_qmt_tests": "2_PASS",
            "qmt_partial_owner_subset": "5_PASS",
            "independent_full_owner_file": "55_PASS_3_BOUNDED_LEGACY_FIXTURE_FAIL",
            "legacy_failures_block_this_saved_array_derived_viewer": False,
        },
    }
    _write_json(OUT / "AUDIT.json", audit)
    print(json.dumps({"audit": str(OUT / "AUDIT.json"), "sha256": _sha(OUT / "AUDIT.json")}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
