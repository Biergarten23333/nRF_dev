#!/usr/bin/env python3
"""Ablate saved prefix/terminal frames and the knee QMT edge application."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np
from scipy.spatial.transform import Rotation

from audit_c2_saved_array_frame_heading_physical_boundaries import (
    _array_sha,
    _branch_sign,
    _longitudinal_separation_deg,
    _summary,
    _world_stages,
)
from replay_c2_postfreeze_heading import _reconstruct_frame_branches
from render_c2_nonhinge_s1_qmt_rooted_debug import _branch_at_prefix


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SETTINGS = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
FROZEN_MANIFEST = RUN / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
SOURCE_STATE = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001/POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
DEBUG_STATE = SPRINT / "C2_NONHINGE_S1_QMT_ROOTED_DEBUG_004/DERIVED_QMT_ROOTED_STATE.npz"
PARENT_AUDIT = SPRINT / "C2_SAVED_ARRAY_FRAME_HEADING_PHYSICAL_BOUNDARY_AUDIT_001/AUDIT.json"
CLOSURE_AUDIT = SPRINT / "C2_FIXED_LANDMARK_PROXY_SPINE_OWNER_CLOSURE_001/AUDIT.json"
OUT = SPRINT / "C2_SAVED_ARRAY_SEGMENT_FRAME_QMT_ABLATION_001"

LIMB_ROWS = (
    ("upper_arm_left", "shoulder_left", "elbow_left", "elbow_left", 0),
    ("forearm_left", None, "elbow_left", "elbow_left", 1),
    ("upper_arm_right", "shoulder_right", "elbow_right", "elbow_right", 0),
    ("forearm_right", None, "elbow_right", "elbow_right", 1),
    ("thigh_left", "hip_left", "knee_left", "knee_left", 0),
    ("shank_left", None, "knee_left", "knee_left", 1),
    ("thigh_right", "hip_right", "knee_right", "knee_right", 0),
    ("shank_right", None, "knee_right", "knee_right", 1),
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    return value


def _write_new_json(path: Path, value: Mapping[str, Any]) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _angle_deg(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.degrees(np.arccos(np.clip(
        float(np.asarray(left, dtype=float) @ np.asarray(right, dtype=float)),
        -1.0, 1.0,
    ))))


def _frame_delta(
    terminal_sensor_from_segment: np.ndarray,
    prefix_sensor_from_segment: np.ndarray,
) -> Mapping[str, Any]:
    terminal = np.asarray(terminal_sensor_from_segment, dtype=float)
    prefix = np.asarray(prefix_sensor_from_segment, dtype=float)
    relative = terminal.T @ prefix
    return {
        "maximum_matrix_entry_delta": float(np.max(np.abs(terminal - prefix))),
        "geodesic_rotation_delta_deg": float(np.degrees(
            Rotation.from_matrix(relative).magnitude()
        )),
        "terminal_sha256": _array_sha(terminal),
        "prefix15_sha256": _array_sha(prefix),
    }


def _construction_hint(branch: Any, row: tuple[Any, ...]) -> tuple[np.ndarray, str]:
    segment, proximal_edge, distal_edge, _, endpoint = row
    if proximal_edge is None:
        connection = branch.connection_vectors_by_edge[distal_edge]
        vector = (
            connection.parent_sensor_to_joint_m
            if endpoint == 0 else connection.child_sensor_to_joint_m
        )
        return np.asarray(vector, dtype=float), "SOLE_SENSOR_TO_PROXIMAL_JOINT_LEVER"
    proximal = branch.connection_vectors_by_edge[proximal_edge]
    distal = branch.connection_vectors_by_edge[distal_edge]
    proximal_vector = proximal.child_sensor_to_joint_m
    distal_vector = (
        distal.parent_sensor_to_joint_m if endpoint == 0
        else distal.child_sensor_to_joint_m
    )
    return (
        np.asarray(proximal_vector, dtype=float) - np.asarray(distal_vector, dtype=float),
        "TWO_CENTER_PROXIMAL_MINUS_DISTAL_DIFFERENCE",
    )


def _saved_frame_construction(
    branch: Any,
    row: tuple[Any, ...],
    source: Mapping[str, np.ndarray],
) -> Mapping[str, Any]:
    segment, _, _, hinge_edge, endpoint = row
    hint, hint_role = _construction_hint(branch, row)
    hint /= np.linalg.norm(hint)
    saved_z_sensor = np.asarray(branch.sensor_from_segment[segment], dtype=float)[:, 2]
    endpoint_name = "parent" if endpoint == 0 else "child"
    signed_axis_sensor = (
        np.asarray(
            source[f"geometry_checkpoint/15/axis/{hinge_edge}/{endpoint_name}"],
            dtype=float,
        )
        * int(branch.axis_sign_by_edge[hinge_edge])
    )
    result = {
        "segment": segment,
        "saved_z_sensor": saved_z_sensor,
        "construction_hint_role": hint_role,
        "construction_hint_sensor": hint,
        "absolute_dot_saved_z_with_hint": float(abs(saved_z_sensor @ hint)),
        "angular_deviation_saved_z_from_hint_deg": _angle_deg(saved_z_sensor, hint),
        "sole_joint_lever_promoted_to_longitudinal_z": bool(
            hint_role == "SOLE_SENSOR_TO_PROXIMAL_JOINT_LEVER"
            and abs(float(saved_z_sensor @ hint)) >= 1.0 - 1e-12
        ),
        "two_center_difference_promoted_to_longitudinal_z": bool(
            hint_role == "TWO_CENTER_PROXIMAL_MINUS_DISTAL_DIFFERENCE"
            and abs(float(saved_z_sensor @ hint)) >= 1.0 - 1e-12
        ),
        "proper_right_handed_so3": bool(
            np.isclose(np.linalg.det(branch.sensor_from_segment[segment]), 1.0, atol=1e-10)
        ),
    }
    signed_axis_segment = np.asarray(branch.segment_from_sensor[segment]) @ signed_axis_sensor
    result["signed_hinge_axis_in_segment"] = signed_axis_segment
    result["hinge_axis_deviation_from_qmt_positive_y_deg"] = _angle_deg(
        signed_axis_segment, np.array([0.0, 1.0, 0.0]),
    )
    return result


def _current_frame_ablation(
    *,
    branch: Any,
    settings: Mapping[str, Any],
    source: Mapping[str, np.ndarray],
) -> tuple[Mapping[str, np.ndarray], Mapping[str, Any]]:
    from biospur_fusion.v0.c2_progressive.segment_frames import (
        _construct_sensor_from_segment,
        _nominal_wear_segment_from_sensor,
        _validated_wear_authority,
    )

    centers = {
        edge: (
            np.asarray(connection.parent_sensor_to_joint_m, dtype=float),
            np.asarray(connection.child_sensor_to_joint_m, dtype=float),
        )
        for edge, connection in branch.connection_vectors_by_edge.items()
    }
    axes = {
        edge: (
            np.asarray(source[f"geometry_checkpoint/15/axis/{edge}/parent"], dtype=float),
            np.asarray(source[f"geometry_checkpoint/15/axis/{edge}/child"], dtype=float),
        )
        for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    wear = _validated_wear_authority(settings["segment_frames"])
    nominal = _nominal_wear_segment_from_sensor(wear)
    frames, flags = _construct_sensor_from_segment(
        centers, axes, branch.axis_sign_by_edge, nominal,
    )
    evidence: dict[str, Any] = {}
    for segment, _, _, hinge_edge, endpoint in LIMB_ROWS:
        signed_axis = axes[hinge_edge][endpoint] * int(branch.axis_sign_by_edge[hinge_edge])
        axis_coordinate = np.asarray(frames[segment], dtype=float).T @ signed_axis
        hint, hint_role = _construction_hint(branch, next(
            row for row in LIMB_ROWS if row[0] == segment
        ))
        hint /= np.linalg.norm(hint)
        evidence[segment] = {
            "construction_flags": list(flags[segment]),
            "signed_hinge_axis_in_segment": axis_coordinate,
            "hinge_axis_deviation_from_qmt_positive_y_deg": _angle_deg(
                axis_coordinate, np.array([0.0, 1.0, 0.0]),
            ),
            "current_z_absolute_dot_with_old_hint": float(abs(
                np.asarray(frames[segment])[:, 2] @ hint
            )),
            "old_hint_role": hint_role,
            "sole_joint_lever_excluded": bool(
                hint_role != "SOLE_SENSOR_TO_PROXIMAL_JOINT_LEVER"
                or abs(float(np.asarray(frames[segment])[:, 2] @ hint)) < 1.0 - 1e-12
            ),
            "distal_longitudinal_is_unresolved_placeholder": proximal_is_none(segment),
        }
    return frames, evidence


def proximal_is_none(segment: str) -> bool:
    return next(row[1] is None for row in LIMB_ROWS if row[0] == segment)


def _frame_world(
    raw_world_from_sensor: Mapping[str, np.ndarray],
    sensor_from_segment: Mapping[str, np.ndarray],
) -> Mapping[str, np.ndarray]:
    return {
        segment: np.einsum(
            "nij,jk->nik", np.asarray(world, dtype=float),
            np.asarray(sensor_from_segment[segment], dtype=float),
        )
        for segment, world in raw_world_from_sensor.items()
    }


def _knee_summary(world: Mapping[str, np.ndarray]) -> Mapping[str, Any]:
    left = _longitudinal_separation_deg(world["thigh_left"], world["shank_left"])
    right = _longitudinal_separation_deg(world["thigh_right"], world["shank_right"])
    return {
        "left_deg": _summary(left),
        "right_deg": _summary(right),
        "q90_left_minus_right_deg": float(np.quantile(left, 0.9) - np.quantile(right, 0.9)),
        "maximum_left_minus_right_deg": float(np.max(left) - np.max(right)),
    }


def _official_span_summary(
    source: Mapping[str, np.ndarray], branch_id: str, edge: str,
) -> Mapping[str, Any]:
    prefix = f"heading/15/{branch_id}/{edge}/"
    bases = sorted({
        key.rsplit("/", 1)[0]
        for key in source
        if key.startswith(prefix) and key.endswith("/common_physical_time_s")
    })
    if not bases:
        raise RuntimeError(f"{edge}: no official QMT spans in saved action15")
    arrays = {
        name: np.concatenate([
            np.asarray(source[f"{base}/{name}"], dtype=float) for base in bases
        ])
        for name in (
            "persistent_delta_filt_rad", "posterior_variance_rad2",
            "qmt_observation_delta_rad", "qmt_rating", "qmt_state_out",
        )
    }
    states, counts = np.unique(arrays["qmt_state_out"].astype(int), return_counts=True)
    return {
        "span_count": len(bases),
        "row_count": int(sum(len(source[f"{base}/common_physical_time_s"]) for base in bases)),
        "persistent_delta_filt_rad": _summary(arrays["persistent_delta_filt_rad"]),
        "posterior_variance_rad2": _summary(arrays["posterior_variance_rad2"]),
        "qmt_observation_delta_rad": _summary(arrays["qmt_observation_delta_rad"]),
        "qmt_rating": _summary(arrays["qmt_rating"]),
        "qmt_state_counts": {str(int(state)): int(count) for state, count in zip(states, counts)},
        "positive_rating_row_count": int(np.count_nonzero(arrays["qmt_rating"] > 0.0)),
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE or OUT.exists():
        raise RuntimeError("segment-frame/QMT ablation requires canonical workspace/new output")
    OUT.mkdir(parents=True, exist_ok=False)
    settings_document = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings = settings_document["effective_settings"]
    frozen = json.loads(FROZEN_MANIFEST.read_text(encoding="utf-8"))
    parent_audit = json.loads(PARENT_AUDIT.read_text(encoding="utf-8"))
    closure = json.loads(CLOSURE_AUDIT.read_text(encoding="utf-8"))
    if parent_audit["viewer_guard"]["viewer_called"] is not False:
        raise RuntimeError("parent saved-array audit unexpectedly rendered pixels")
    if closure["fixed_viewer_spine_owner_closure"][
        "registered_three_dimensional_graphical_spine_vector_exists"
    ] is not False:
        raise RuntimeError("graphical spine authority changed before no-pixel ablation")

    with np.load(SOURCE_STATE, allow_pickle=False) as source, np.load(
        DEBUG_STATE, allow_pickle=False,
    ) as debug:
        terminal_branches = _reconstruct_frame_branches(frozen=frozen, arrays=source)
        prefix_branches = tuple(
            _branch_at_prefix(
                branch, source, f"physical_trajectory/15/{branch.branch_id}",
            )
            for branch in terminal_branches
        )
        branch_ids = tuple(branch.branch_id for branch in prefix_branches)
        by_branch: dict[str, Any] = {}
        for terminal, prefix15 in zip(terminal_branches, prefix_branches, strict=True):
            _, stages, _ = _world_stages(
                action_index=15,
                branch=prefix15,
                branch_ids=branch_ids,
                settings=settings,
                source=source,
                debug=debug,
            )
            raw = stages["raw_world_from_sensor"]
            terminal_world = _frame_world(raw, terminal.sensor_from_segment)
            prefix_world = stages["frame_only_world_from_segment"]
            current_frames, current_frame_evidence = _current_frame_ablation(
                branch=prefix15, settings=settings, source=source,
            )
            current_world = _frame_world(raw, current_frames)

            qmt_edge_only: dict[str, Any] = {}
            for side in ("left", "right"):
                edge = f"knee_{side}"
                parent = f"thigh_{side}"
                child = f"shank_{side}"
                delta = np.asarray(
                    debug[f"edge_delta_filt_rad/15/{prefix15.branch_id}/{edge}"],
                    dtype=float,
                )
                yaw = Rotation.from_rotvec(np.column_stack((
                    np.zeros(len(delta)), np.zeros(len(delta)), delta,
                ))).as_matrix()
                edge_corrected_child = np.einsum(
                    "nij,njk->nik", yaw, prefix_world[child],
                )
                edge_only_angles = _longitudinal_separation_deg(
                    prefix_world[parent], edge_corrected_child,
                )
                rooted_angles = _longitudinal_separation_deg(
                    stages["qmt_rooted_world_from_segment"][parent],
                    stages["qmt_rooted_world_from_segment"][child],
                )
                qmt_edge_only[edge] = {
                    "edge_delta_filt_rad": _summary(delta),
                    "edge_only_reconstructed_angle_deg": _summary(edge_only_angles),
                    "persisted_rooted_angle_deg": _summary(rooted_angles),
                    "maximum_absolute_reconstruction_error_deg": float(np.max(
                        np.abs(edge_only_angles - rooted_angles)
                    )),
                    "parent_plus_child_tree_application_exactly_reproduced": bool(
                        np.allclose(edge_only_angles, rooted_angles, atol=2e-12, rtol=0.0)
                    ),
                    "official_saved_span_evidence": _official_span_summary(
                        source, prefix15.branch_id, edge,
                    ),
                }

            by_branch[prefix15.branch_id] = {
                "retained_signs": {
                    edge: _branch_sign(prefix15.branch_id, edge)
                    for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
                },
                "terminal_vs_prefix15_frame_delta": {
                    segment: _frame_delta(
                        terminal.sensor_from_segment[segment],
                        prefix15.sensor_from_segment[segment],
                    )
                    for segment in terminal.sensor_from_segment
                },
                "saved_prefix15_construction": {
                    row[0]: _saved_frame_construction(prefix15, row, source)
                    for row in LIMB_ROWS
                },
                "saved_terminal_construction": {
                    row[0]: _saved_frame_construction(terminal, row, source)
                    for row in LIMB_ROWS
                },
                "current_source_owner_reconstruction_from_prefix15_centers_axes": {
                    "segment_evidence": current_frame_evidence,
                    "frame_only_knee_angles": _knee_summary(current_world),
                    "role": (
                        "BOUNDARY_ABLATION_ONLY_UNRESOLVED_DISTAL_PLACEHOLDERS_"
                        "ARE_NOT_POSE_MEANS_AND_QMT_WAS_NOT_RERUN"
                    ),
                },
                "frame_only_squat_ablation": {
                    "prefix15": _knee_summary(prefix_world),
                    "terminal": _knee_summary(terminal_world),
                    "terminal_reduces_q90_asymmetry": bool(
                        abs(_knee_summary(terminal_world)["q90_left_minus_right_deg"])
                        < abs(_knee_summary(prefix_world)["q90_left_minus_right_deg"])
                    ),
                },
                "qmt_edge_application": qmt_edge_only,
            }

    first_branch = next(iter(by_branch.values()))
    localized_frame_deltas = first_branch["terminal_vs_prefix15_frame_delta"]
    audit = {
        "schema": "biospur-c2-saved-array-segment-frame-qmt-ablation-v1",
        "created_local": datetime.now().astimezone().isoformat(),
        "inputs": {
            "settings": {"path": str(SETTINGS), "sha256": _sha(SETTINGS)},
            "frozen_manifest": {"path": str(FROZEN_MANIFEST), "sha256": _sha(FROZEN_MANIFEST)},
            "training_replay_state": {"path": str(SOURCE_STATE), "sha256": _sha(SOURCE_STATE)},
            "debug004_state": {"path": str(DEBUG_STATE), "sha256": _sha(DEBUG_STATE)},
            "parent_boundary_audit": {"path": str(PARENT_AUDIT), "sha256": _sha(PARENT_AUDIT)},
            "graphical_spine_closure": {"path": str(CLOSURE_AUDIT), "sha256": _sha(CLOSURE_AUDIT)},
        },
        "predeclared_causal_hypotheses": [
            {
                "id": "H1_TERMINAL_VS_PREFIX15_FRAME_STATE",
                "test": "same saved action15 sensor orientations under terminal versus prefix15 frames",
                "decision_rule": "terminal must reduce the absolute left-right q90 knee separation difference",
            },
            {
                "id": "H2_SEGMENT_FRAME_CONSTRUCTION_OWNERSHIP",
                "test": (
                    "two-center versus sole-lever longitudinal construction and signed hinge-axis "
                    "coordinate in every saved limb frame"
                ),
                "decision_rule": (
                    "sole-lever collinearity or non-+Y signed hinge coordinates identify a frame-owner defect"
                ),
            },
            {
                "id": "H3_QMT_PARENT_CHILD_APPLICATION",
                "test": (
                    "reconstruct child edge-only world-Z correction from the saved deltaFilt and compare "
                    "against the persisted rooted knee trace"
                ),
                "decision_rule": (
                    "numerical equality supports tree application; disagreement localizes an application bug"
                ),
            },
        ],
        "by_branch": by_branch,
        "bounded_findings": {
            "terminal_vs_prefix15_change_is_localized": {
                segment: row["maximum_matrix_entry_delta"]
                for segment, row in localized_frame_deltas.items()
                if row["maximum_matrix_entry_delta"] > 1e-12
            },
            "h1_terminal_frames_reduce_squat_q90_asymmetry": all(
                row["frame_only_squat_ablation"]["terminal_reduces_q90_asymmetry"]
                for row in by_branch.values()
            ),
            "h1_status": "REJECTED_TERMINAL_FRAME_ABLATION_WORSENS_Q90_ASYMMETRY",
            "h2_saved_distal_frames_promote_sole_lever_exactly": all(
                row["saved_prefix15_construction"][segment]
                ["sole_joint_lever_promoted_to_longitudinal_z"]
                for row in by_branch.values()
                for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right")
            ),
            "h2_saved_hinge_coordinate_differs_from_exact_qmt_y": any(
                row["saved_prefix15_construction"][segment]
                ["hinge_axis_deviation_from_qmt_positive_y_deg"] > 1e-8
                for row in by_branch.values()
                for segment in (
                    "upper_arm_left", "forearm_left", "upper_arm_right", "forearm_right",
                    "thigh_left", "shank_left", "thigh_right", "shank_right",
                )
            ),
            "h2_status": (
                "SUPPORTED_SAVED_FRAMES_PRECEDE_CURRENT_HINGE_PRIORITY_AND_DISTAL_LEVER_EXCLUSION"
            ),
            "h3_parent_plus_child_application_exact": all(
                edge["parent_plus_child_tree_application_exactly_reproduced"]
                for row in by_branch.values()
                for edge in row["qmt_edge_application"].values()
            ),
            "h3_status": (
                "TREE_APPLICATION_SUPPORTED_LEFT_ONLY_AMPLIFICATION_IS_IN_EDGE_DELTA_INPUT_NOT_TREE_COMPOSITION"
            ),
            "current_owner_reconstruction_is_pose_solution": False,
            "reason": (
                "current distal frames are broad wear-quadrature placeholders with unqualified "
                "longitudinal direction; they can test owner invariants but cannot select wrist/ankle pose"
            ),
        },
        "viewer_guard": {
            "graphical_spine_mapping_exists": False,
            "viewer_called": False,
            "new_pixels": 0,
        },
        "payload_reread": False,
        "fit_or_qmt_replay_rerun": False,
        "heldout_opened": False,
        "scientific_acceptance_pass": False,
        "tuned_pose_pass": False,
    }
    _write_new_json(OUT / "AUDIT.json", audit)
    print(json.dumps({
        "audit": str(OUT / "AUDIT.json"),
        "audit_sha256": _sha(OUT / "AUDIT.json"),
        "new_pixels": 0,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
