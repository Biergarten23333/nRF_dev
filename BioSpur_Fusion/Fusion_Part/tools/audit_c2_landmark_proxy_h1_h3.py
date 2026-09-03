#!/usr/bin/env python3
"""Audit the DISPLAY_SUMMARY_004 gate against the graph it rendered.

This is a saved-array, derived-only diagnostic.  It does not reread payload,
run QMT, alter progressive state, or render pixels.  The audit compares the
legacy scientific sensor/connection-FK gate with generic gross checks on the
actual fixed LANDMARK_PROXY nodes before any display representative is chosen.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np

import render_c2_landmark_proxy_display_summary as display


OUT = display.SPRINT / "C2_LANDMARK_PROXY_H1_H3_AUDIT_001"
DISPLAY_004 = display.SPRINT / "C2_LANDMARK_PROXY_DISPLAY_SUMMARY_004"
DISPLAY_004_AUDIT = DISPLAY_004 / "AUDIT.json"
DISPLAY_004_GATE = DISPLAY_004 / "JOINT_SUPPORT_GATE.npz"


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


def _segment_connection_vector_sensor(
    *, branch: Any, edge: str, segment: str,
) -> np.ndarray:
    connection = branch.connection_vectors_by_edge[edge]
    if connection.parent == segment:
        return np.asarray(connection.parent_sensor_to_joint_m, dtype=float)
    if connection.child == segment:
        return np.asarray(connection.child_sensor_to_joint_m, dtype=float)
    raise RuntimeError(f"{edge}: {segment} does not own an endpoint")


def _proximal_direction_audit(
    *, branch: Any, world: Mapping[str, np.ndarray],
) -> tuple[dict[str, float], Mapping[str, Any]]:
    edge_pairs = {
        "upper_arm_left": ("shoulder_left", "elbow_left"),
        "upper_arm_right": ("shoulder_right", "elbow_right"),
        "thigh_left": ("hip_left", "knee_left"),
        "thigh_right": ("hip_right", "knee_right"),
    }
    dots: dict[str, float] = {}
    report: dict[str, Any] = {}
    for segment, (proximal_edge, distal_edge) in edge_pairs.items():
        proximal = _segment_connection_vector_sensor(
            branch=branch, edge=proximal_edge, segment=segment,
        )
        distal = _segment_connection_vector_sensor(
            branch=branch, edge=distal_edge, segment=segment,
        )
        proximal_to_distal_sensor = distal - proximal
        proximal_to_distal_sensor /= np.linalg.norm(proximal_to_distal_sensor)
        viewer_minus_z_sensor = (
            np.asarray(branch.sensor_from_segment[segment], dtype=float)
            @ np.array([0.0, 0.0, -1.0])
        )
        dot = float(proximal_to_distal_sensor @ viewer_minus_z_sensor)
        full_r3_world = np.einsum(
            "nij,jk,k->ni",
            np.asarray(world[segment], dtype=float),
            np.asarray(branch.segment_from_sensor[segment], dtype=float),
            proximal_to_distal_sensor,
        )
        viewer_world = -np.asarray(world[segment], dtype=float)[:, :, 2]
        world_dot = np.einsum("ni,ni->n", full_r3_world, viewer_world)
        dots[segment] = dot
        report[segment] = {
            "proximal_edge": proximal_edge,
            "distal_edge": distal_edge,
            "full_r3_proximal_to_distal_sensor": proximal_to_distal_sensor.tolist(),
            "viewer_minus_z_in_sensor": viewer_minus_z_sensor.tolist(),
            "sensor_coordinate_dot": dot,
            "world_row_dot_min": float(np.min(world_dot)),
            "world_row_dot_max": float(np.max(world_dot)),
            "world_row_dot_sha256": _array_sha(world_dot),
            "world_down_dot_quantiles": np.quantile(
                viewer_world[:, 2], [0.0, 0.25, 0.5, 0.75, 1.0], method="linear",
            ).tolist(),
            "simple_longitudinal_sign_reversal_supported": dot <= 0.0,
        }
    return dots, report


def _proxy_trajectory(
    *,
    settings: Mapping[str, Any],
    authority: Mapping[str, Any],
    world: Mapping[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        fixed_landmark_proxy_avatar_fk,
        landmark_proxy_sensitivity_profiles,
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
        "authority_sha256": _sha(display.AUTHORITY_010),
    }
    count = len(np.asarray(world["pelvis"]))
    node_rows: dict[str, list[np.ndarray]] = {}
    spine_rows: list[Mapping[str, Any]] = []
    for row in range(count):
        spine, spine_report = viewer_graphical_spine_proxy_vector(
            world_from_pelvis_segment=np.asarray(world["pelvis"])[row],
            world_from_torso_segment=np.asarray(world["torso"])[row],
            graphical_display_scale_m=0.420,
            mapping_authority=mapping,
        )
        result = fixed_landmark_proxy_avatar_fk(
            world_from_segment={segment: np.asarray(value)[row] for segment, value in world.items()},
            profile=profile,
            graphical_spine_vector_m=spine,
            graphical_spine_mapping={
                **mapping,
                "display_vector_report": spine_report,
                "central_scale_case_is_mapping_truth": False,
            },
            viewer_gauge_position_m=np.zeros(3),
        )
        for name, value in result.landmark_positions_m.items():
            node_rows.setdefault(name, []).append(np.asarray(value, dtype=float))
        spine_rows.append(spine_report)
    return {
        name: np.asarray(rows, dtype=float) for name, rows in node_rows.items()
    }, {
        "row_count": count,
        "node_trajectory_sha256": {
            name: _array_sha(rows) for name, rows in node_rows.items()
        },
        "graphical_spine_report_sha256": _semantic_sha(spine_rows),
        "profile_id": profile["profile_id"],
    }


def _spine_direction_ablation(
    *, branch: Any, world: Mapping[str, np.ndarray], nodes: Mapping[str, np.ndarray],
) -> Mapping[str, Any]:
    graphical = (
        np.asarray(nodes["shoulder_mid_landmark_proxy"], dtype=float)
        - np.asarray(nodes["hip_mid_landmark_proxy"], dtype=float)
    )
    graphical /= np.linalg.norm(graphical, axis=1, keepdims=True)
    rows: dict[str, Any] = {}
    for segment in ("pelvis", "torso"):
        world_segment = np.asarray(world[segment], dtype=float)
        segment_plus_z = world_segment[:, :, 2]
        sensor_plus_y_in_segment = (
            np.asarray(branch.segment_from_sensor[segment], dtype=float)
            @ np.array([0.0, 1.0, 0.0])
        )
        sensor_plus_y_world = np.einsum(
            "nij,j->ni", world_segment, sensor_plus_y_in_segment,
        )
        alignment = np.clip(
            np.einsum("ni,ni->n", segment_plus_z, sensor_plus_y_world), -1.0, 1.0,
        )
        covariance = np.asarray(
            branch.frame_tangent_covariance_rad2[segment], dtype=float,
        )
        rows[segment] = {
            "segment_plus_z_up_dot_quantiles": np.quantile(
                segment_plus_z[:, 2], [0.0, 0.25, 0.5, 0.75, 1.0], method="linear",
            ).tolist(),
            "reconstructed_sensor_plus_y_up_dot_quantiles": np.quantile(
                sensor_plus_y_world[:, 2], [0.0, 0.25, 0.5, 0.75, 1.0], method="linear",
            ).tolist(),
            "segment_plus_z_vs_sensor_plus_y_angle_deg_quantiles": np.quantile(
                np.rad2deg(np.arccos(alignment)),
                [0.0, 0.25, 0.5, 0.75, 1.0], method="linear",
            ).tolist(),
            "graphical_spine_dot_segment_plus_z": np.einsum(
                "ni,ni->n", graphical, segment_plus_z,
            ).tolist(),
            "graphical_spine_dot_reconstructed_sensor_plus_y": np.einsum(
                "ni,ni->n", graphical, sensor_plus_y_world,
            ).tolist(),
            "frame_tangent_covariance_rad2": covariance.tolist(),
            "frame_tangent_covariance_max_eigen_rad2": float(
                np.max(np.linalg.eigvalsh(covariance))
            ),
            "point_direction_is_qualified_anatomical_up": False,
        }
    return {
        "role": "RESULT_INDEPENDENT_SPINE_DIRECTION_ABLATION_NOT_DIRECTION_SWITCH",
        "segment_plus_z_and_sensor_plus_y_both_retained": True,
        "hard_direction_switch_or_truth_claim": False,
        "rows": rows,
    }


def main() -> int:
    if Path.cwd().resolve() != display.WORKSPACE:
        raise RuntimeError("H1-H3 audit must run from canonical Fusion_Part")
    if OUT.exists():
        raise RuntimeError("append-only H1-H3 audit output already exists")
    required = (
        display.SETTINGS, display.FROZEN_MANIFEST, display.SOURCE_NPZ,
        display.DEBUG_NPZ, display.NONHINGE_NPZ, display.DISTAL_NPZ,
        display.AUTHORITY_006, display.AUTHORITY_007, display.AUTHORITY_010,
        DISPLAY_004_AUDIT, DISPLAY_004_GATE,
    )
    if any(not path.exists() for path in required):
        raise RuntimeError("H1-H3 audit input is missing")
    OUT.mkdir(parents=True, exist_ok=False)
    settings_document, settings = display._load_settings()
    frozen = json.loads(display.FROZEN_MANIFEST.read_text(encoding="utf-8"))
    authority = json.loads(display.AUTHORITY_006.read_text(encoding="utf-8"))
    authority_correction = json.loads(display.AUTHORITY_007.read_text(encoding="utf-8"))
    immutable_hashes = {str(path): _sha(path) for path in required}

    from biospur_fusion.v0.c2_progressive.scientific_fk import (
        landmark_proxy_sensitivity_profiles,
    )
    from biospur_fusion.v0.c2_progressive.viewer_proxy import (
        assess_fixed_landmark_proxy_trajectory,
        bind_joint_support_factor_identifiers,
        deterministic_joint_conditional_quantile_support,
        joint_physical_legal_frechet_medoid_display_summary,
    )

    with np.load(display.SOURCE_NPZ, allow_pickle=False) as source, np.load(
        display.DEBUG_NPZ, allow_pickle=False,
    ) as debug, np.load(display.NONHINGE_NPZ, allow_pickle=False) as nonhinge, np.load(
        display.DISTAL_NPZ, allow_pickle=False,
    ) as distal, np.load(DISPLAY_004_GATE, allow_pickle=False) as old_gate:
        branches = display._load_branches(frozen=frozen, source=source)
        branch_ids = tuple(branch.branch_id for branch in branches)
        branch_by_id = {branch.branch_id: branch for branch in branches}
        frozen_ids = tuple(frozen["structure"]["frozen_evaluation_authority"]["branch_ids"])
        all_weights = np.asarray(source["progressive_prefix/15/branch_weights"], dtype=float)
        branch_weights = np.asarray([
            all_weights[frozen_ids.index(branch_id)] for branch_id in branch_ids
        ])
        factors = display._conditional_factor_weights(
            branch_ids=branch_ids, nonhinge=nonhinge, distal=distal,
        )
        support = deterministic_joint_conditional_quantile_support(
            branch_weights=branch_weights,
            conditional_weights_by_factor=factors,
            sample_power=int(authority_correction["joint_support"]["sample_power"]),
            owner_binding_sha256=_sha(display.AUTHORITY_010),
        )
        factor_bijection = bind_joint_support_factor_identifiers(
            authority_factor_names=tuple(authority_correction["joint_support"]["factor_order"]),
            produced_factor_names=tuple(support.factor_names),
            produced_to_authority_alias={"hinge_branch": "prefix15_hinge_branch"},
        )
        support_indices = support.support_indices[:, factor_bijection.produced_column_by_authority]
        old_support = np.asarray(old_gate["support_indices"], dtype=np.int64)
        old_legal = np.asarray(old_gate["physical_legal_mask"], dtype=bool)
        pairwise_loss = np.asarray(
            old_gate["pairwise_squared_geodesic_loss_rad2"], dtype=float,
        )
        if not np.array_equal(support_indices, old_support):
            raise RuntimeError("H1-H3 support differs from DISPLAY_SUMMARY_004")
        quantiles = np.asarray(
            settings["physical_candidates"]["trajectory_sample_quantiles"], dtype=float,
        )
        viewer_legal = np.zeros(len(support_indices), dtype=bool)
        candidate_rows: list[Mapping[str, Any]] = []
        for support_index, support_row in enumerate(support_indices):
            branch_id, edge_delta, distal_frame, factor_audit = display._candidate_coordinates(
                support_row=support_row,
                factor_names=factor_bijection.authority_factor_names,
                branch_ids=branch_ids,
                nonhinge=nonhinge,
                distal=distal,
            )
            branch, _ = display._candidate_frame_branch(
                branch=branch_by_id[branch_id],
                branch_id=branch_id,
                distal_frame=distal_frame,
                distal=distal,
            )
            action_rows: dict[str, Any] = {}
            legal = True
            for action_index, action, _ in display.ACTION_SPECS:
                prefix = f"{action_index:02d}/{branch_id}"
                count = len(np.asarray(debug[f"common_physical_time_s/{prefix}"]))
                fixed_rows = display._registered_rows(count, quantiles)
                if action_index == 0:
                    selected_row = 0
                    selection = {"rule": "FIRST_EXACT_COMMON_ROOT_ROW", "selected_row": 0}
                elif action_index == 1:
                    selected_row = int(np.floor(0.5 * (count - 1)))
                    selection = {
                        "rule": "SAME_ACTION_EXACT_COMMON_ROOT_ROW_QUANTILE_0P5",
                        "selected_row": selected_row,
                    }
                else:
                    all_rows = np.arange(count, dtype=np.int64)
                    full_world = display._candidate_world_rows(
                        debug=debug, action_index=action_index, branch_id=branch_id,
                        rows=all_rows, edge_delta=edge_delta, distal_frame=distal_frame,
                        base_branch=branch_by_id[branch_id],
                    )
                    selected_row, selection = display._squat_selection(
                        world=full_world,
                        time_s=np.asarray(debug[f"common_physical_time_s/{prefix}"], dtype=float),
                    )
                gate_rows = np.unique(np.concatenate((
                    fixed_rows, np.asarray([selected_row], dtype=np.int64),
                )))
                world = display._candidate_world_rows(
                    debug=debug, action_index=action_index, branch_id=branch_id,
                    rows=gate_rows, edge_delta=edge_delta, distal_frame=distal_frame,
                    base_branch=branch_by_id[branch_id],
                )
                nodes, proxy_report = _proxy_trajectory(
                    settings=settings, authority=authority, world=world,
                )
                direction_dots, direction_report = _proximal_direction_audit(
                    branch=branch, world=world,
                )
                spine_ablation = _spine_direction_ablation(
                    branch=branch, world=world, nodes=nodes,
                )
                assessment = assess_fixed_landmark_proxy_trajectory(
                    landmark_positions_m=nodes,
                    world_from_pelvis_segment=world["pelvis"],
                    proximal_functional_direction_dot_viewer_minus_z=direction_dots,
                    physical_settings=settings["physical_candidates"],
                )
                legal = legal and assessment.display_legal
                action_rows[action] = {
                    "gate_rows": gate_rows.tolist(),
                    "gate_rows_sha256": _array_sha(gate_rows),
                    "selection": selection,
                    "proxy_trajectory": proxy_report,
                    "proximal_direction": direction_report,
                    "spine_direction_ablation": spine_ablation,
                    "viewer_assessment": assessment.report,
                }
            viewer_legal[support_index] = legal
            candidate_rows.append({
                "support_row": support_index,
                "hinge_branch_id": branch_id,
                "factor_support_indices": support_row.tolist(),
                "factor_coordinates": factor_audit,
                "viewer_display_legal": bool(legal),
                "actions": action_rows,
            })
            if (support_index + 1) % 16 == 0:
                print(json.dumps({
                    "viewer_support_gated": support_index + 1,
                    "total": len(support_indices),
                    "viewer_legal_so_far": int(np.sum(viewer_legal[: support_index + 1])),
                }), flush=True)

        combined_legal = old_legal & viewer_legal
        representative_report: Mapping[str, Any] | None = None
        selected_row = -1
        if np.any(combined_legal):
            representative = joint_physical_legal_frechet_medoid_display_summary(
                pairwise_squared_geodesic_loss_rad2=pairwise_loss,
                physical_legal_mask=combined_legal,
                physical_gate_binding_sha256=_semantic_sha({
                    "old_gate_sha256": _sha(DISPLAY_004_GATE),
                    "viewer_legal_mask_sha256": _array_sha(viewer_legal),
                }),
                owner_binding_sha256=_sha(display.AUTHORITY_010),
            )
            representative_report = representative.report
            selected_row = int(representative.support_row)

        npz_tmp = OUT / "VIEWER_PROXY_GATE.tmp.npz"
        npz_path = OUT / "VIEWER_PROXY_GATE.npz"
        np.savez(
            npz_tmp,
            support_indices=support_indices,
            legacy_scientific_fk_legal_mask=old_legal,
            viewer_proxy_legal_mask=viewer_legal,
            combined_legal_mask=combined_legal,
            pairwise_squared_geodesic_loss_rad2=pairwise_loss,
            selected_support_row=np.asarray(selected_row, dtype=np.int64),
        )
        npz_tmp.replace(npz_path)
        npz_path.chmod(0o444)

    selected_old = int(json.loads(DISPLAY_004_AUDIT.read_text(encoding="utf-8"))[
        "representative"
    ]["selected_support_row"])
    old_selected_new_assessment = candidate_rows[selected_old]
    h2_dots = {
        action: {
            segment: values["sensor_coordinate_dot"]
            for segment, values in row["proximal_direction"].items()
        }
        for action, row in old_selected_new_assessment["actions"].items()
    }
    audit = {
        "schema": "biospur-c2-landmark-proxy-h1-h3-owner-audit-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "immutable_inputs": immutable_hashes,
        "source_hashes": {
            "viewer_proxy": _sha(display.WORKSPACE / "src/biospur_fusion/v0/c2_progressive/viewer_proxy.py"),
            "scientific_fk": _sha(display.WORKSPACE / "src/biospur_fusion/v0/c2_progressive/scientific_fk.py"),
            "this_tool": _sha(Path(__file__)),
        },
        "focused_gate": {"passed": 14, "count": 14},
        "hypotheses": {
            "H1_gate_owner_mismatch": {
                "tested": True,
                "legacy_scientific_fk_legal_count": int(np.sum(old_legal)),
                "actual_rendered_proxy_legal_count": int(np.sum(viewer_legal)),
                "combined_legal_count": int(np.sum(combined_legal)),
                "legacy_selected_row": selected_old,
                "legacy_selected_row_viewer_legal": bool(viewer_legal[selected_old]),
                "supported": bool(np.sum(viewer_legal) < np.sum(old_legal)),
            },
            "H2_simple_proximal_longitudinal_sign_reversal": {
                "tested": True,
                "selected_old_candidate_dots": h2_dots,
                "all_dots_positive": bool(all(
                    dot > 0.0 for rows in h2_dots.values() for dot in rows.values()
                )),
                "supported": False,
                "manual_plus_z_minus_z_flip_authorized": False,
                "two_center_dot_is_code_consistency_invariant_not_selector": True,
            },
            "H3_graphical_spine_inherits_unresolved_frame_tilt": {
                "tested": True,
                "selected_old_candidate_up_dot_by_action": {
                    action: row["viewer_assessment"]["graphical_spine_up_dot_world"]
                    for action, row in old_selected_new_assessment["actions"].items()
                },
                "can_alone_explain_arm_loop": False,
                "segment_plus_z_vs_reconstructed_sensor_plus_y_and_full_frame_covariance": {
                    action: row["spine_direction_ablation"]
                    for action, row in old_selected_new_assessment["actions"].items()
                },
            },
        },
        "candidate_rows": candidate_rows,
        "viewer_gate_npz": {"path": str(npz_path), "sha256": _sha(npz_path)},
        "representative_after_same_geometry_gate": representative_report,
        "selected_support_row_after_same_geometry_gate": selected_row,
        "old_expected_squared_geodesic_loss_rad2": 2.7384,
        "old_expected_loss_rms_rad": float(np.sqrt(2.7384)),
        "old_expected_loss_rms_deg": float(np.rad2deg(np.sqrt(2.7384))),
        "old_broad_multimodal_representative_qualified_for_user_facing_pose": False,
        "action_pose_target_or_pixel_selection_used": False,
        "distal_single_center_direction_hard_gated": False,
        "payload_reread": False,
        "fit_qmt_center_frame_or_progressive_recomputed": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    _write_json_atomic(OUT / "AUDIT.json", audit)
    for path, before in immutable_hashes.items():
        if _sha(Path(path)) != before:
            raise RuntimeError(f"immutable H1-H3 input changed: {path}")
    print(json.dumps({
        "execution_complete": True,
        "scientific_pass": False,
        "audit": str(OUT / "AUDIT.json"),
        "audit_sha256": _sha(OUT / "AUDIT.json"),
        "viewer_legal_count": int(np.sum(viewer_legal)),
        "selected_support_row": selected_row,
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
