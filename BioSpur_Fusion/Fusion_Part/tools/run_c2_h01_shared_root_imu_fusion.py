#!/usr/bin/env python3
"""Fuse C2 pelvis IMU with causal ten-node shared-root UWB observations.

The calibration actions and the two HXX holdouts use the same estimator.  They
only differ in the read-only frozen-pose adapter: C2 00--19 use the public 3A
FK, while H01/H02 use the sealed holdout renderer adapter.
"""
from __future__ import annotations

import argparse
from functools import partial
import hashlib
import json
import math
from pathlib import Path
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_articulated_biomechanics import (
    apply_orientation_constrained_ik,
    fit_articulated_model,
    project_hinge_corrections,
)
from biospur_fusion.c2_coupled_progressive.contracts import ROOT
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    body_proxy_at_fraction,
    FrozenHoldoutBodyProxy,
    frozen_world_alignment,
    nearest_frame,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    rotation_from_wxyz,
    select_best_geometry,
)
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
    adaptive_root_minimum_std_m,
    propagation_mode,
    select_trusted_body_nodes,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    ArticulatedRangeConfig,
    SEGMENTS as ARTICULATED_SEGMENTS,
    active_segments_for_nodes,
    corrected_proxy_points,
    solve_articulated_ranges,
)
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)
from biospur_fusion.c2_uwb_calibration.pair_bias import (
    PairBiasEstimate,
    load_pair_bias_table,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    estimate_leave_node_uncertainty,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)
from biospur_fusion.c2_uwb_root_world.calibration import CALIBRATION_ORDER
from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    AnkleContactConfig,
    AnkleContactDetector,
    DualFootFootholdCorrector,
    FootContactEvidence,
    FootSupportState,
    fit_stillness_profiles,
)
from biospur_fusion.ingest.events import RecordType
from biospur_fusion.ingest.v47 import decode_measurements
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, PositionObservation, RootState

from evaluate_c2_hxx_shared_root_regression import (
    DEFAULT_BIAS_TABLE,
    DEFAULT_CLOCK,
    _load_holdout,
)
from evaluate_c2_pair_bias_gate import (
    EPOCH_NS,
    _build_links,
    _load_layout,
    _prediction,
    _reference_time,
    _tracker,
    _update_tracker,
    _load_episode,
    _valid_slots,
)
from run_c2_h01_tight_raw_range_fusion import PELVIS_NODE, _pelvis_imu
from tools.build_c2_avatar_interactive import _load_trajectory


DEFAULT_ACTION = "H01_boxing"
SUPPORTED_ACTIONS = tuple(CALIBRATION_ORDER) + ("H01_boxing", "H02_golf")
MINIMUM_ROOT_STD_M = 0.12
MINIMUM_BODY_NODES_PER_EPOCH = 1
LINK_SELECTIONS = ("all", "top4_facing")
BODY_UPDATES = ("shared_root", "articulated_consensus")
NODE_SELECTIONS = ("adaptive", "all_available")
STATIC_CALIBRATION_ACTIONS = {
    "00_initial_still",
    "02_t_pose",
    "17_final_still",
}
ANKLE_NODE_TO_SIDE = {"BSF6C53": "left", "BSF8BC4": "right"}
H02_CONTACT_REFERENCE_NPZ = (
    ROOT / "logs/c2_h02_ankle_contact_root_pilot_v5_20260904/"
    "H02_golf_SHARED_ROOT_IMU_FUSION.npz"
)
H02_CONTACT_HORIZONTAL_LIMIT_M = {
    "left": 0.06977610728810382,
    "right": 0.06144224073601858,
}
H02_ROOT_XY_STEP_REFERENCE_M = {
    "maximum": 0.061020,
    "p99": 0.007366,
}


def _resolve_artifact_path(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def _json_ready(value: Any) -> Any:
    """Normalize NumPy scalars without changing diagnostic values."""

    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, dict):
        return {key: _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _load_hxx_trajectory_npz(path: Path) -> dict[str, Any]:
    trajectory: dict[str, Any] = {"trajectory": {}}
    with np.load(path, allow_pickle=False) as archive:
        for action in ("H01_boxing", "H02_golf"):
            trajectory["trajectory"][action] = {}
            for segment in SEGMENTS:
                base = f"trajectory/{action}/{segment}"
                trajectory["trajectory"][action][segment] = {
                    "time_root_s": np.asarray(archive[f"{base}/time_root_s"]),
                    "quat_world_segment_wxyz": np.asarray(
                        archive[f"{base}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.asarray(archive[f"{base}/mask"], dtype=bool),
                }
        trajectory["output_coordinate_convention"] = {
            "matrix_world_output_from_internal": np.asarray(
                archive["output_coordinates/matrix_world_output_from_internal"]
            ),
            "plane_normal_world_internal": np.asarray(
                archive["output_coordinates/plane_normal_world_internal"]
            ),
        }
    return trajectory


def _load_analytic_pose_owner(
    result_path: Path,
    calibration_report_path: Path,
    pre_ik_hxx_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Load one hash-bound native-200 pose and its public hinge owner."""

    result = json.loads(result_path.read_text(encoding="utf-8"))
    if not (
        result.get("schema") == "biospur-c2-native200-orientation-ik-result-v1"
        and result.get("mechanism_pass") is True
        and result.get("scientific_pass") is False
        and abs(float(result.get("sample_rate_hz", 0.0)) - 200.0) < 1e-9
    ):
        raise ValueError("analytic pose result is not the accepted mechanism artifact")
    artifact = result["hxx_trajectory"]
    trajectory_path = _resolve_artifact_path(str(artifact["path"]))
    if _sha256(trajectory_path) != artifact["sha256"]:
        raise RuntimeError("analytic HXX trajectory hash mismatch")
    trajectory = _load_hxx_trajectory_npz(trajectory_path)

    calibration_report = json.loads(
        calibration_report_path.read_text(encoding="utf-8")
    )
    if calibration_report.get("schema") != (
        "biospur-c2-native200-pose-reset-calibration-diagnostic-v1"
    ):
        raise ValueError("functional-axis calibration report schema mismatch")
    calibration_artifact = calibration_report["trajectory"]
    calibration_path = _resolve_artifact_path(str(calibration_artifact["path"]))
    if _sha256(calibration_path) != calibration_artifact["sha256"]:
        raise RuntimeError("analytic calibration trajectory hash mismatch")
    calibration_trajectory = _load_trajectory(calibration_path)
    hinge_model = fit_articulated_model(
        calibration_trajectory, calibration_report
    )
    accepted_calibration_artifact = result["calibration_trajectory"]
    accepted_calibration_path = _resolve_artifact_path(
        str(accepted_calibration_artifact["path"])
    )
    if _sha256(accepted_calibration_path) != accepted_calibration_artifact["sha256"]:
        raise RuntimeError("accepted analytic calibration hash mismatch")
    accepted_calibration = _load_trajectory(accepted_calibration_path)
    regenerated_calibration, _ = apply_orientation_constrained_ik(
        calibration_trajectory, hinge_model
    )
    for action, segments in accepted_calibration["trajectory"].items():
        for segment, row in segments.items():
            if not np.array_equal(
                np.asarray(row["quat_world_segment_wxyz"]),
                np.asarray(regenerated_calibration["trajectory"][action][segment][
                    "quat_world_segment_wxyz"
                ]),
            ):
                raise RuntimeError(
                    "functional-axis model does not reproduce accepted analytic calibration"
                )

    pre_ik_report = json.loads(
        pre_ik_hxx_report_path.read_text(encoding="utf-8")
    )
    if pre_ik_report.get("schema") != "biospur-c2-hxx-frozen-replay-report-v1":
        raise ValueError("pre-IK native-200 HXX report schema mismatch")
    pre_ik_artifact = pre_ik_report["trajectory"]
    pre_ik_path = _resolve_artifact_path(str(pre_ik_artifact["path"]))
    if _sha256(pre_ik_path) != pre_ik_artifact["sha256"]:
        raise RuntimeError("pre-IK native-200 HXX trajectory hash mismatch")
    pre_ik_trajectory = _load_hxx_trajectory_npz(pre_ik_path)

    sample_audit: dict[str, Any] = {}
    difference_audit: dict[str, Any] = {}
    for action in ("H01_boxing", "H02_golf"):
        reference_time = np.asarray(
            trajectory["trajectory"][action]["pelvis"]["time_root_s"],
            dtype=float,
        )
        dt = np.diff(reference_time)
        if (
            len(reference_time) < 2
            or not np.all(np.isfinite(reference_time))
            or not np.all(dt > 0.0)
            or abs(float(np.median(dt)) - 0.005) > 2e-6
        ):
            raise ValueError(f"{action}: analytic pose is not a native 5 ms grid")
        sample_audit[action] = {
            "frame_count": len(reference_time),
            "median_dt_s": float(np.median(dt)),
            "sample_rate_hz": float(1.0 / np.median(dt)),
        }
        changed = {}
        for segment in SEGMENTS:
            row = trajectory["trajectory"][action][segment]
            if not np.array_equal(np.asarray(row["time_root_s"]), reference_time):
                raise ValueError(f"{action}: analytic segment grids differ")
            analytic = np.asarray(row["quat_world_segment_wxyz"], dtype=float)
            frozen = np.asarray(
                pre_ik_trajectory["trajectory"][action][segment][
                    "quat_world_segment_wxyz"
                ], dtype=float,
            )
            if analytic.shape != frozen.shape:
                raise ValueError(f"{action}/{segment}: native-200 shape changed")
            analytic_xyzw = np.c_[analytic[:, 1:4], analytic[:, 0]]
            frozen_xyzw = np.c_[frozen[:, 1:4], frozen[:, 0]]
            delta = Rotation.from_quat(frozen_xyzw).inv() * Rotation.from_quat(
                analytic_xyzw
            )
            changed[segment] = float(np.degrees(np.max(delta.magnitude())))
        difference_audit[action] = {
            "maximum_orientation_difference_deg_by_segment": changed,
            "changed_segment_count_over_1e_6_deg": sum(
                value > 1e-6 for value in changed.values()
            ),
        }
    if difference_audit["H01_boxing"][
        "changed_segment_count_over_1e_6_deg"
    ] < 1:
        raise RuntimeError("analytic pose metadata changed but orientations did not")
    owner = "NATIVE200_ANALYTIC_ORIENTATION_HINGE_IK:" + artifact["sha256"]
    return trajectory, hinge_model, {
        "trajectory_owner": owner,
        "analytic_result": str(result_path),
        "analytic_result_sha256": _sha256(result_path),
        "trajectory": str(trajectory_path),
        "trajectory_sha256": artifact["sha256"],
        "calibration_report": str(calibration_report_path),
        "calibration_report_sha256": _sha256(calibration_report_path),
        "calibration_trajectory": str(calibration_path),
        "calibration_trajectory_sha256": calibration_artifact["sha256"],
        "accepted_analytic_calibration": str(accepted_calibration_path),
        "accepted_analytic_calibration_sha256": accepted_calibration_artifact[
            "sha256"
        ],
        "accepted_calibration_reproduced_from_public_projector": True,
        "pre_ik_hxx_report": str(pre_ik_hxx_report_path),
        "pre_ik_hxx_report_sha256": _sha256(pre_ik_hxx_report_path),
        "pre_ik_hxx_trajectory": str(pre_ik_path),
        "pre_ik_hxx_trajectory_sha256": pre_ik_artifact["sha256"],
        "sample_grid": sample_audit,
        "orientation_difference_from_pre_ik": difference_audit,
    }


def _effective_body_update(action: str, requested: str) -> str:
    if requested not in BODY_UPDATES:
        raise ValueError(f"unsupported body update: {requested}")
    if requested == "articulated_consensus" and action in STATIC_CALIBRATION_ACTIONS:
        return "shared_root"
    return requested


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _usable_epoch_groups(
    episode: dict[str, Any], clocks: dict[str, Any]
) -> tuple[list[list[Any]], dict[str, Any]]:
    """Keep partial body epochs without allowing duplicate node ownership.

    A body node contributes only when its own sweep has at least four valid
    anchor ranges. One such node is sufficient for a conservative root-only
    update; relative-pose correction is enabled later only when at least two
    nodes pass the adaptive trust gate. If duplicate rows land in one 120 ms
    bucket, retain the row with more valid links and then the earlier globally
    aligned strobe.
    """

    buckets: dict[int, dict[str, Any]] = defaultdict(dict)
    duplicate_rows = 0
    rows_with_fewer_than_four_links = 0
    for row in episode["retained"]:
        valid_count = len(_valid_slots(row))
        if valid_count < 4:
            rows_with_fewer_than_four_links += 1
            continue
        clock = clocks[row.node]
        global_ns = int(round(
            clock.a_ns_per_us * row.strobe_us + clock.b_ns
        ))
        bucket = buckets[int(round(global_ns / EPOCH_NS))]
        previous = bucket.get(row.node)
        if previous is not None:
            duplicate_rows += 1
            previous_score = (
                len(_valid_slots(previous)),
                -int(round(
                    clocks[previous.node].a_ns_per_us * previous.strobe_us
                    + clocks[previous.node].b_ns
                )),
            )
            current_score = (valid_count, -global_ns)
            if current_score <= previous_score:
                continue
        bucket[row.node] = row
    groups = [
        sorted(by_node.values(), key=lambda row: row.node)
        for _, by_node in sorted(buckets.items())
        if len(by_node) >= MINIMUM_BODY_NODES_PER_EPOCH
    ]
    histogram = Counter(len(group) for group in groups)
    if len(groups) < 10:
        raise RuntimeError("fewer than ten usable partial body epochs")
    return groups, {
        "minimum_nodes_required": MINIMUM_BODY_NODES_PER_EPOCH,
        "node_count_histogram": {
            str(count): int(frequency)
            for count, frequency in sorted(histogram.items())
        },
        "partial_epochs_consumed": int(sum(
            frequency for count, frequency in histogram.items() if count < 10
        )),
        "complete_ten_node_epochs": int(histogram.get(10, 0)),
        "duplicate_rows_resolved": duplicate_rows,
        "rows_rejected_below_four_ranges": rows_with_fewer_than_four_links,
    }


def _project_carry_at_current_base(
    base_rotations_world: dict[str, np.ndarray],
    carry_correction_rotvec: dict[str, np.ndarray],
    hinge_projector: Callable[
        [dict[str, np.ndarray], dict[str, np.ndarray]],
        tuple[dict[str, np.ndarray], dict[str, Any]],
    ] | None,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Transport a carried correction onto the current native-pose base.

    Correction rotvecs are right-multiplicative coordinates relative to one
    base orientation.  Reusing the same coordinates after that base changes
    does not preserve hinge-manifold ownership.  The public projector is the
    single owner that re-expresses the carry at every current base.
    """

    carry = {
        segment: np.asarray(carry_correction_rotvec[segment], dtype=float)
        .reshape(3).copy()
        for segment in ARTICULATED_SEGMENTS
    }
    if hinge_projector is None:
        return carry, {}
    projected, metrics = hinge_projector(base_rotations_world, carry)
    projected = {
        segment: np.asarray(projected[segment], dtype=float).reshape(3).copy()
        for segment in ARTICULATED_SEGMENTS
    }
    if (
        not all(np.isfinite(value).all() for value in projected.values())
        or metrics.get("post_projection_all_inside_rom") is not True
    ):
        raise RuntimeError("current-base carry projection failed")
    return projected, {
        **dict(metrics),
        "projection_stage": "CURRENT_BASE_CARRY",
    }


def _write_final_hinge_projection_failure(
    output: Path,
    *,
    action: str,
    trajectory_owner: str,
    observation_count: int,
    projected_update_count: int,
    final_gate: dict[str, Any],
    final_row_audit: list[dict[str, Any]],
    wall_s_at_failure: float,
) -> Path:
    """Persist exact subgate ownership before a fail-closed final raise."""

    correction_cap = float(final_gate["segment_correction_limit_rad"])
    failure = {
        "schema": "biospur-c2-final-hinge-projection-failure-v1",
        "status": "REJECTED_FINAL_HINGE_PROJECTION_GATE",
        "scientific_pass": False,
        "action": action,
        "trajectory_owner": trajectory_owner,
        "observation_count": observation_count,
        "projected_update_count": projected_update_count,
        "final_gate": final_gate,
        "failure_rows": {
            "outside_rom": [
                row for row in final_row_audit if row["outside_rom_joints"]
            ],
            "fk_direction_residual": [
                row for row in final_row_audit
                if row["fk_direction_residual_maximum_deg"] > 3e-6
            ],
            "projection_idempotency": [
                row for row in final_row_audit
                if row["idempotency_maximum_rad"] > 1e-6
            ],
            "segment_correction": [
                row for row in final_row_audit
                if row["segment_correction_maximum_rad"] > correction_cap
            ],
        },
        "wall_s_at_failure": wall_s_at_failure,
    }
    path = output / "HINGE_PROJECTION_FAILURE.json"
    path.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
    return path


def _shared_root_observations(
    episode: dict[str, Any],
    *,
    proxy_at_fraction: Callable[
        [float], tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]
    ],
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict[str, Any],
    policy: str,
    biases: dict[tuple[str, int], PairBiasEstimate] | None,
    link_selection: str = "all",
    body_update: str = "shared_root",
    base_rotations_at_fraction: Callable[[float], dict[str, np.ndarray]] | None = None,
    geometry: Any | None = None,
    articulated_stride: int = 4,
    node_selection: str = "adaptive",
    hinge_projector: Callable[
        [dict[str, np.ndarray], dict[str, np.ndarray]],
        tuple[dict[str, np.ndarray], dict[str, Any]],
    ] | None = None,
) -> list[dict[str, Any]]:
    room_initial = np.array([
        float(np.mean(anchors[:, 0])),
        float(np.mean(anchors[:, 1])),
        0.95,
    ])
    tracker = _tracker(room_initial)
    observations = []
    previous_correction = {
        segment: np.zeros(3) for segment in ARTICULATED_SEGMENTS
    }
    for sequence, group in enumerate(episode["groups"]):
        epoch_ns = int(np.median([
            int(round(clocks[row.node].a_ns_per_us * row.strobe_us + clocks[row.node].b_ns))
            for row in group
        ]))
        fraction = (epoch_ns - episode["lo"]) / (episode["hi"] - episode["lo"])
        offsets, normals, frozen_frame = proxy_at_fraction(fraction)
        current_base_rotations = (
            None if base_rotations_at_fraction is None
            else base_rotations_at_fraction(fraction)
        )
        if hinge_projector is not None and current_base_rotations is None:
            raise ValueError("hinge carry projection requires current rotations")
        current_carry, carry_projection = _project_carry_at_current_base(
            current_base_rotations or {}, previous_correction, hinge_projector
        )
        previous_correction = current_carry
        reference_time = _reference_time(group, clocks)
        predicted, dt = _prediction(tracker, reference_time)
        links = _build_links(
            group,
            offsets=offsets,
            normals=normals,
            anchors=anchors,
            delays=delays,
            tag_delay=tag_delay,
            layout_sigma=layout_sigma,
            clocks=clocks,
            reference_time_s=reference_time,
            predicted_root=predicted,
            velocity=tracker["velocity"],
            policy=policy,
            biases=biases,
        )
        raw_link_count = len(links)
        links = _select_geometry_links(links, link_selection)
        available_nodes = tuple(sorted({link.node for link in links}))
        if node_selection == "adaptive":
            selection = select_trusted_body_nodes(
                links,
                anchors_m=anchors,
                initial_root_m=predicted,
                root_velocity_mps=tracker["velocity"],
            )
            links = list(selection.trusted_links)
            trusted_nodes = selection.trusted_nodes
            adaptive_mode = selection.mode
            trust_rows = [
                {
                    "node": row.node,
                    "trusted": row.trusted,
                    "reason": row.reason,
                    "score": row.score,
                    "link_count": row.link_count,
                    "root_condition": row.root_condition,
                    "median_abs_standardized_residual": (
                        row.median_abs_standardized_residual
                    ),
                    "consensus_distance_m": row.consensus_distance_m,
                    "facing_confirmed_positive_nlos_fraction": (
                        row.facing_confirmed_positive_nlos_fraction
                    ),
                }
                for row in selection.assessments
            ]
        elif node_selection == "all_available":
            trusted_nodes = available_nodes
            adaptive_mode = propagation_mode(len(trusted_nodes))
            trust_rows = []
        else:
            raise ValueError(f"unsupported node selection: {node_selection}")
        if len(links) < 4:
            continue
        result = solve_shared_root(
            links,
            anchors_m=anchors,
            initial_root_m=predicted,
            root_velocity_mps=tracker["velocity"],
        )
        if not result.success:
            continue
        uncertainty = estimate_leave_node_uncertainty(
            links,
            anchors_m=anchors,
            initial_root_m=result.root_position_m,
            root_velocity_mps=tracker["velocity"],
            minimum_std_m=adaptive_root_minimum_std_m(
                len(trusted_nodes), full_node_std_m=MINIMUM_ROOT_STD_M
            ),
        )
        shared_root_position = result.root_position_m.copy()
        shared_root_covariance = uncertainty.covariance_m2.copy()
        measurement_tracker_velocity = np.asarray(
            tracker["velocity"], dtype=float
        ).copy()
        root_position = result.root_position_m
        covariance = uncertainty.covariance_m2
        corrections = current_carry
        root_pose_gauge_correction = {
            segment: np.zeros(3) for segment in ARTICULATED_SEGMENTS
        }
        root_pose_gauge_owner = "SHARED_ROOT_BASE_ONLY_PROXY"
        selected_hinge_projection = carry_projection
        facing_nlos_weight = np.ones(len(links))
        condition = result.condition
        leave_node_success = uncertainty.successful_leave_node_solves
        articulated_attempted = False
        articulated_accepted = False
        articulated_prefit_median_abs_m = None
        articulated_postfit_median_abs_m = None
        pose_observable_rank = 0
        active_segments = ()
        if (
            body_update == "articulated_consensus"
            and len(trusted_nodes) >= 2
        ):
            if sequence % articulated_stride == 0:
                if current_base_rotations is None or geometry is None:
                    raise ValueError(
                        "articulated consensus requires rotations and geometry"
                )
                articulated_attempted = True
                active_segments = active_segments_for_nodes(trusted_nodes)
                articulated = solve_articulated_ranges(
                    links,
                    anchors_m=anchors,
                    base_rotations_world=current_base_rotations,
                    geometry=geometry,
                    initial_root_m=result.root_position_m,
                    root_velocity_mps=tracker["velocity"],
                    previous_correction_rotvec=previous_correction,
                    active_segments=active_segments,
                    use_facing_nlos_prior=True,
                    hinge_projector=hinge_projector,
                )
                pose_observable_rank = articulated.pose_observable_rank
                if articulated.success and pose_observable_rank >= 1:
                    articulated_accepted = True
                    articulated_prefit_median_abs_m = float(np.median(
                        np.abs(articulated.prefit_physical_residual_m)
                    ))
                    articulated_postfit_median_abs_m = float(np.median(
                        np.abs(articulated.physical_residual_m)
                    ))
                    corrections = articulated.segment_correction_rotvec
                    root_position = articulated.root_position_m.copy()
                    covariance = articulated.root_covariance_m2.copy()
                    previous_correction = {
                        segment: value.copy() for segment, value in corrections.items()
                    }
                    root_pose_gauge_correction = {
                        segment: value.copy()
                        for segment, value in corrections.items()
                    }
                    root_pose_gauge_owner = (
                        "ARTICULATED_RANGE_PROJECTED_CORRECTION"
                    )
                    facing_nlos_weight = articulated.facing_nlos_weight
                    selected_hinge_projection = {
                        **dict(articulated.hinge_projection),
                        "projection_stage": "ARTICULATED_RANGE_SOLVE",
                    }
        elif body_update not in {"shared_root", "articulated_consensus"}:
            raise ValueError(f"unsupported body update: {body_update}")
        availability_time = max(
            clocks[row.node].seconds(row.frame_us) for row in group
        )
        if availability_time + 1e-12 < reference_time:
            raise RuntimeError("shared root became available before its range epoch")
        observations.append({
            "measurement_time_s": reference_time,
            "availability_time_s": availability_time,
            "position_m": root_position.copy(),
            "covariance_m2": covariance.copy(),
            "shared_root_position_m": shared_root_position,
            "shared_root_covariance_m2": shared_root_covariance,
            "measurement_tracker_velocity_mps": measurement_tracker_velocity,
            "condition": condition,
            "links": len(links),
            "raw_links": raw_link_count,
            "nodes": len({link.node for link in links}),
            "available_nodes": len(available_nodes),
            "trusted_nodes": len(trusted_nodes),
            "trusted_node_ids": trusted_nodes,
            "adaptive_mode": adaptive_mode,
            "node_trust": trust_rows,
            "anchors": tuple(sorted({link.anchor for link in links})),
            "frozen_frame": frozen_frame,
            "leave_node_success": leave_node_success,
            "segment_correction_rotvec": np.stack([
                corrections[segment] for segment in ARTICULATED_SEGMENTS
            ]),
            "root_pose_gauge_correction_rotvec": np.stack([
                root_pose_gauge_correction[segment]
                for segment in ARTICULATED_SEGMENTS
            ]),
            "root_pose_gauge_owner": root_pose_gauge_owner,
            "facing_nlos_weight": facing_nlos_weight.copy(),
            "articulated_attempted": articulated_attempted,
            "articulated_accepted": articulated_accepted,
            "articulated_prefit_median_abs_m": articulated_prefit_median_abs_m,
            "articulated_postfit_median_abs_m": articulated_postfit_median_abs_m,
            "articulated_pose_observable_rank": pose_observable_rank,
            "articulated_active_segments": active_segments,
            "hinge_projection": selected_hinge_projection,
            "_articulated_links": tuple(links),
            "_fraction": float(fraction),
            "sequence": sequence,
        })
        _update_tracker(tracker, root_position, reference_time, dt)
    return observations


_FOOTHOLD_ACTIVE_SEGMENTS = {
    "left": ("pelvis", "thigh_left", "shank_left"),
    "right": ("pelvis", "thigh_right", "shank_right"),
}

_ROOT_POSE_GAUGE_SOURCE_OWNERS = frozenset({
    "SHARED_ROOT_BASE_ONLY_PROXY",
    "ARTICULATED_RANGE_PROJECTED_CORRECTION",
    "CONTACT_AWARE_ARTICULATED_RANGE_PROJECTED_CORRECTION",
})


def _validate_root_pose_gauge_source(payload: Mapping[str, Any]) -> None:
    """Fail closed when a root target's declared pose gauge is inconsistent."""

    owner = str(payload.get("root_pose_gauge_owner", ""))
    if owner not in _ROOT_POSE_GAUGE_SOURCE_OWNERS:
        raise RuntimeError(f"unknown root pose gauge source owner: {owner}")
    gauge = np.asarray(
        payload.get("root_pose_gauge_correction_rotvec"), dtype=float
    )
    expected_shape = (len(ARTICULATED_SEGMENTS), 3)
    if gauge.shape != expected_shape or not np.isfinite(gauge).all():
        raise RuntimeError("root pose gauge source correction is invalid")
    if owner == "SHARED_ROOT_BASE_ONLY_PROXY":
        if not np.array_equal(gauge, np.zeros(expected_shape)):
            raise RuntimeError("base-only root pose gauge is not zero")
        return
    owned_target = np.asarray(
        payload.get("segment_correction_rotvec"), dtype=float
    )
    if owned_target.shape != expected_shape or not np.array_equal(
        gauge, owned_target
    ):
        raise RuntimeError(
            "articulated root pose gauge does not match its owned target"
        )


def _apply_measurement_time_footholds_to_observation(
    payload: dict[str, Any],
    *,
    footholds_world_m: Mapping[str, np.ndarray],
    anchors: np.ndarray,
    rotations_at_fraction: Callable[[float], dict[str, np.ndarray]],
    geometry: Any,
    hinge_projector: Callable[
        [dict[str, np.ndarray], dict[str, np.ndarray]],
        tuple[dict[str, np.ndarray], dict[str, Any]],
    ],
) -> dict[str, Any]:
    """Atomically refine one scheduled articulated observation with footholds.

    The ordinary range-only articulated result remains the single fallback
    initialization.  When contact was already owned at the *measurement*
    time, the same raw links are solved once more with those world ankle XY
    points.  Only a complete successful result replaces root, covariance and
    segment correction together; callers still consume exactly one position
    observation.
    """

    diagnostic: dict[str, Any] = {
        "attempted": False,
        "accepted": False,
        "reason": "NO_MEASUREMENT_TIME_FOOTHOLD",
        "measurement_time_s": float(payload["measurement_time_s"]),
        "sides": sorted(footholds_world_m),
        "projected_foothold_xy_residual_m": {},
    }
    payload["contact_aware_articulated"] = diagnostic
    if not footholds_world_m:
        return diagnostic
    if not (
        payload.get("articulated_attempted")
        and payload.get("articulated_accepted")
    ):
        diagnostic["reason"] = "EXISTING_ARTICULATED_STRIDE_OR_FALLBACK"
        return diagnostic
    unknown_sides = set(footholds_world_m) - set(_FOOTHOLD_ACTIVE_SEGMENTS)
    if unknown_sides:
        raise ValueError(f"unknown foothold sides: {sorted(unknown_sides)}")

    diagnostic["attempted"] = True
    base_rotations = rotations_at_fraction(float(payload["_fraction"]))
    previous = {
        segment: np.asarray(
            payload["segment_correction_rotvec"], dtype=float
        )[index].copy()
        for index, segment in enumerate(ARTICULATED_SEGMENTS)
    }
    active = set(payload["articulated_active_segments"])
    for side in footholds_world_m:
        active.update(_FOOTHOLD_ACTIVE_SEGMENTS[side])
    ordered_active = tuple(
        segment for segment in ARTICULATED_SEGMENTS if segment in active
    )
    point_constraints = {
        f"ankle_{side}": np.asarray(point, dtype=float).copy()
        for side, point in footholds_world_m.items()
    }
    result = solve_articulated_ranges(
        payload["_articulated_links"],
        anchors_m=anchors,
        base_rotations_world=base_rotations,
        geometry=geometry,
        initial_root_m=np.asarray(payload["position_m"], dtype=float),
        root_velocity_mps=np.asarray(
            payload["measurement_tracker_velocity_mps"], dtype=float
        ),
        previous_correction_rotvec=previous,
        active_segments=ordered_active,
        point_constraints_world_m=point_constraints,
        point_constraint_sigma_m=0.03,
        point_constraint_axes=(0, 1),
        use_facing_nlos_prior=True,
        hinge_projector=hinge_projector,
        config=ArticulatedRangeConfig(root_prior_sigma_m=0.02),
    )
    diagnostic.update({
        "reason": result.reason,
        "pose_observable_rank": int(result.pose_observable_rank),
        "range_prefit_median_abs_m": (
            None if not len(result.prefit_physical_residual_m)
            else float(np.median(np.abs(result.prefit_physical_residual_m)))
        ),
        "range_postfit_median_abs_m": (
            None if not len(result.physical_residual_m)
            else float(np.median(np.abs(result.physical_residual_m)))
        ),
        "maximum_projected_foothold_residual_m": float(
            result.maximum_joint_closure_m
        ),
        "solver_projection_metrics": _json_ready(
            dict(getattr(result, "hinge_projection", {}))
        ),
    })
    if not (result.success and result.pose_observable_rank >= 1):
        return diagnostic

    points = corrected_proxy_points(
        base_rotations, result.segment_correction_rotvec, geometry
    )
    residual_by_side = {
        side: float(np.linalg.norm(
            (result.root_position_m + points[f"ankle_{side}"] - target)[:2]
        ))
        for side, target in footholds_world_m.items()
    }
    diagnostic["projected_foothold_xy_residual_m"] = residual_by_side
    diagnostic["accepted"] = True
    payload.update({
        "position_m": result.root_position_m.copy(),
        "covariance_m2": result.root_covariance_m2.copy(),
        "segment_correction_rotvec": np.stack([
            result.segment_correction_rotvec[segment]
            for segment in ARTICULATED_SEGMENTS
        ]),
        "facing_nlos_weight": result.facing_nlos_weight.copy(),
        "articulated_prefit_median_abs_m": float(np.median(
            np.abs(result.prefit_physical_residual_m)
        )),
        "articulated_postfit_median_abs_m": float(np.median(
            np.abs(result.physical_residual_m)
        )),
        "articulated_pose_observable_rank": int(result.pose_observable_rank),
        "articulated_active_segments": ordered_active,
        "hinge_projection": {
            **dict(result.hinge_projection),
            "projection_stage": "CONTACT_AWARE_ARTICULATED_RANGE_SOLVE",
        },
        "root_pose_gauge_correction_rotvec": np.stack([
            result.segment_correction_rotvec[segment]
            for segment in ARTICULATED_SEGMENTS
        ]),
        "root_pose_gauge_owner": (
            "CONTACT_AWARE_ARTICULATED_RANGE_PROJECTED_CORRECTION"
        ),
    })
    return diagnostic


def _published_pose_gauge_at_measurement(
    history: list[dict[str, Any]],
    *,
    measurement_time_s: float,
    pose_owner: CausalArticulatedPose,
) -> tuple[
    dict[str, np.ndarray], dict[str, FootContactEvidence], float, str,
    dict[str, np.ndarray],
] | None:
    """Use only the latest correction published by measurement time."""

    measurement = float(measurement_time_s)
    eligible = [
        row for row in history
        if float(row["time_s"]) <= measurement + 1e-12
    ]
    if not eligible:
        return None
    row = eligible[-1]
    source_time = float(row["time_s"])
    if source_time > measurement + 1e-12:
        raise RuntimeError("published pose history used a future sample")
    primary_side = row.get("primary_side")
    if primary_side not in ("left", "right"):
        raise RuntimeError("published pose history lacks primary-side ownership")
    evidence = row.get("evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        raise RuntimeError("published pose history lacks contact evidence")
    correction_row = row.get("correction_rotvec")
    if not isinstance(correction_row, Mapping):
        raise RuntimeError("published pose history lacks pose correction")
    published_correction = {
        segment: np.asarray(
            correction_row[segment], dtype=float
        ).copy()
        for segment in ARTICULATED_SEGMENTS
    }
    offsets = pose_owner.ankle_offsets_for_published_correction(
        measurement, published_correction
    )
    return (
        offsets,
        dict(evidence),
        source_time,
        primary_side,
        published_correction,
    )


def _reexpress_observation_root_to_published_pose(
    payload: dict[str, Any],
    *,
    footholds_world_m: Mapping[str, np.ndarray],
    published_ankle_offset_world_m: Mapping[str, np.ndarray] | None,
    published_correction_rotvec: Mapping[str, np.ndarray] | None,
    measurement_evidence: Mapping[str, FootContactEvidence] | None,
    measurement_primary_side: str | None,
    foothold_corrector: DualFootFootholdCorrector | None,
    rotations_at_fraction: Callable[[float], dict[str, np.ndarray]],
    geometry: Any,
) -> dict[str, Any]:
    """Apply the sole pre-filter root pose-gauge adapter for one UWB row."""

    _validate_root_pose_gauge_source(payload)
    if not footholds_world_m:
        return {
            "applied": False,
            "reason": "NO_MEASUREMENT_TIME_FOOTHOLD_BITWISE_IDENTITY",
            "source_owner": payload["root_pose_gauge_owner"],
            "contact_manifold": {
                "evaluated": False,
                "compatible": True,
                "reason": "NO_MEASUREMENT_TIME_FOOTHOLD",
            },
        }
    if (
        published_ankle_offset_world_m is None
        or published_correction_rotvec is None
        or measurement_evidence is None
        or measurement_primary_side is None
        or foothold_corrector is None
    ):
        raise RuntimeError(
            "measurement foothold lacks a complete published pose gauge"
        )
    source_owner = str(payload["root_pose_gauge_owner"])
    source_correction = {
        segment: np.asarray(
            payload["root_pose_gauge_correction_rotvec"], dtype=float
        )[index].copy()
        for index, segment in enumerate(ARTICULATED_SEGMENTS)
    }
    base = rotations_at_fraction(float(payload["_fraction"]))
    source_points = corrected_proxy_points(
        base, source_correction, geometry
    )
    source_ankle_offsets = {
        side: np.asarray(
            source_points[f"ankle_{side}"], dtype=float
        ).copy()
        for side in ("left", "right")
    }
    covariance_before = np.asarray(payload["covariance_m2"], dtype=float).copy()
    root_before = np.asarray(payload["position_m"], dtype=float).copy()
    gauge = foothold_corrector.reexpress_root_between_pose_gauges(
        root_before,
        owned_footholds_world_m=footholds_world_m,
        evidence=measurement_evidence,
        source_ankle_offset_world_m=source_ankle_offsets,
        target_ankle_offset_world_m=published_ankle_offset_world_m,
        primary_side_at_measurement=measurement_primary_side,
    )
    published_stack = np.stack([
        np.asarray(published_correction_rotvec[segment], dtype=float)
        for segment in ARTICULATED_SEGMENTS
    ])
    payload.update({
        "position_m": gauge.root_position_m.copy(),
        "root_pose_gauge_correction_rotvec": published_stack,
        "root_pose_gauge_owner": (
            "MEASUREMENT_TIME_CAUSALLY_PUBLISHED_CORRECTION"
        ),
    })
    if not np.array_equal(
        np.asarray(payload["covariance_m2"], dtype=float), covariance_before
    ):
        raise RuntimeError("root pose gauge changed observation covariance")
    contact_target = foothold_corrector.root_target_for_owned_footholds(
        gauge.root_position_m,
        owned_footholds_world_m=footholds_world_m,
        evidence=measurement_evidence,
        ankle_offset_world_m=published_ankle_offset_world_m,
        primary_side_at_measurement=measurement_primary_side,
    )
    consistency_xy = float(np.linalg.norm(
        contact_target.applied_position_delta_m[:2]
    ))
    consistency_limit = float(
        foothold_corrector.config.
        maximum_bilateral_root_target_disagreement_m
    )
    per_foot_xy = {
        side: float(np.linalg.norm((
            gauge.root_position_m
            + np.asarray(published_ankle_offset_world_m[side], dtype=float)
            - np.asarray(footholds_world_m[side], dtype=float)
        )[:2]))
        for side in contact_target.active_sides
    }
    return {
        "applied": True,
        "reason": "REEXPRESSED_TO_MEASUREMENT_TIME_PUBLISHED_POSE",
        "source_owner": source_owner,
        "destination_owner": payload["root_pose_gauge_owner"],
        "active_sides": list(gauge.active_sides),
        "constrained_sides": list(gauge.constrained_sides),
        "primary_side": gauge.primary_side,
        "root_position_before_m": root_before,
        "root_position_after_m": gauge.root_position_m,
        "applied_position_delta_m": gauge.applied_position_delta_m,
        "covariance_changed": False,
        "contact_manifold": {
            "evaluated": True,
            "compatible": consistency_xy <= consistency_limit + 1e-12,
            "root_xy_residual_m": consistency_xy,
            "per_foot_xy_residual_m": per_foot_xy,
            "active_sides": list(contact_target.active_sides),
            "selected_sides": list(contact_target.constrained_sides),
            "primary_side": contact_target.primary_side,
            "root_target_m": contact_target.root_position_m,
            "threshold_m": consistency_limit,
            "owner": "DUAL_FOOT_FOOTHOLD_CORRECTOR_PURE_HISTORICAL_OWNER",
        },
    }


def _contact_manifold_quality_state(
    footholds_world_m: Mapping[str, np.ndarray],
    root_pose_gauge_diagnostic: Mapping[str, Any],
) -> str:
    """Map the optional contact consistency result to one filter quality."""

    if not footholds_world_m:
        return "NOMINAL_DIAGNOSTIC_RAW_HUBER"
    compatible = bool(
        root_pose_gauge_diagnostic["contact_manifold"]["compatible"]
    )
    return (
        "NOMINAL_DIAGNOSTIC_RAW_HUBER"
        if compatible else "REJECT_CONTACT_MANIFOLD_CONFLICT"
    )


def _add_position_payload_once(
    payload: Mapping[str, Any],
    *,
    fused_filter: CausalDelayedRootFilter,
    processing_time_s: float,
    influence_multiplier: float,
    quality_state: str,
) -> tuple[PositionObservation, Any]:
    """Construct and consume exactly one finalized UWB root observation."""

    observation = PositionObservation(
        measurement_time_s=payload["measurement_time_s"],
        availability_time_s=payload["availability_time_s"],
        root_position_m=payload["position_m"],
        covariance_m2=payload["covariance_m2"],
        tag_id="C2_SHARED_10_NODE_ROOT",
        anchors=payload["anchors"],
        quality_state=quality_state,
        frame_valid=True,
        physical_point_valid=True,
        source_sequence=payload["sequence"],
    )
    decision = fused_filter.add_position(
        observation,
        processing_time_s=processing_time_s,
        influence_multiplier=influence_multiplier,
    )
    return observation, decision


def _select_geometry_links(links: list[Any], policy: str) -> list[Any]:
    """Apply the pre-registered per-node antenna-facing link policy.

    Selection stays outside the numerical root solver: the body-mounted
    antenna contract owns which observations are presented, while
    ``solve_shared_root`` remains agnostic to that policy.  Four links are
    retained for every usable node because that is the minimum 3-D ranging
    geometry and the only count that passed the prior held-link/rank gate.
    """

    if policy not in LINK_SELECTIONS:
        raise ValueError(f"unsupported link selection: {policy}")
    if policy == "all":
        return list(links)

    by_node: dict[str, list[Any]] = defaultdict(list)
    for link in links:
        if link.facing_score is None:
            raise ValueError("top4_facing requires a facing score for every link")
        by_node[link.node].append(link)

    selected: list[Any] = []
    for node in sorted(by_node):
        node_links = by_node[node]
        scores = {int(link.anchor): float(link.facing_score) for link in node_links}
        anchors = select_best_geometry(
            scores, scores, target_count=4, minimum_count=4
        )
        if not anchors:
            continue
        chosen = set(anchors)
        selected.extend(link for link in node_links if int(link.anchor) in chosen)
    return selected


def _initial_bias(imu: list[dict[str, Any]], action_start_s: float) -> np.ndarray:
    gravity_up = np.array([0.0, 0.0, 9.80665])
    preparation = [
        row for row in imu
        if action_start_s - 2.0 <= row["time_s"] < action_start_s
    ]
    if len(preparation) < 100:
        raise RuntimeError("insufficient still preparation for accelerometer bias")
    estimates = [
        row["acceleration"] - row["rotation_world"].T @ gravity_up
        for row in preparation
    ]
    return np.median(np.asarray(estimates), axis=0)


def _new_filter(
    first: dict[str, Any], bias_mps2: np.ndarray, config: RootFilterConfig
) -> CausalDelayedRootFilter:
    covariance = np.zeros((9, 9), dtype=float)
    covariance[:3, :3] = first["covariance_m2"]
    covariance[3:6, 3:6] = np.eye(3) * 1.0
    covariance[6:9, 6:9] = np.eye(3) * 0.04
    state = RootState(
        time_s=first["availability_time_s"],
        vector=np.r_[first["position_m"], np.zeros(3), bias_mps2],
        covariance=covariance,
    )
    return CausalDelayedRootFilter(state, config, inertial=True)


def _ankle_imu_rows(
    events: list[Any],
    clocks: dict[str, Any],
    lo_ns: int,
    hi_ns: int,
) -> list[dict[str, Any]]:
    rows = []
    for event in events:
        side = ANKLE_NODE_TO_SIDE.get(event.node_id)
        if side is None or event.record_type is not RecordType.IMU:
            continue
        time_s = clocks[event.node_id].seconds(int(event.node_timer_us))
        if lo_ns * 1e-9 <= time_s < hi_ns * 1e-9:
            rows.append({
                "side": side,
                "time_s": time_s,
                "acceleration": np.asarray(event.payload["acc_raw"], float)
                / 2048.0 * 9.80665,
                "gyro": np.deg2rad(
                    np.asarray(event.payload["gyro_raw"], float) / 16.384
                ),
                "sequence": int(event.sequence),
            })
    rows.sort(key=lambda row: (row["time_s"], row["side"]))
    return rows


def _fit_contact_profiles(
    clocks: dict[str, Any],
    bridges: list[tuple[float, float]],
    config: AnkleContactConfig,
) -> dict[str, Any]:
    samples: dict[str, list[tuple[np.ndarray, np.ndarray]]] = {
        "left": [], "right": [],
    }
    for action in ("00_initial_still", "17_final_still"):
        episode = _load_episode(action, clocks, bridges)
        events, _ = decode_measurements(episode["raw"])
        rows = _ankle_imu_rows(
            events, clocks, episode["lo"], episode["hi"]
        )
        for side in ("left", "right"):
            selected = [row for row in rows if row["side"] == side]
            if len(selected) < config.window_samples:
                raise RuntimeError(f"insufficient {side} ankle still samples")
            samples[side].append((
                np.stack([row["acceleration"] for row in selected]),
                np.stack([row["gyro"] for row in selected]),
            ))
    return fit_stillness_profiles(samples, config)


def _ankle_proxy_interpolator(
    proxy_at_fraction: Callable[
        [float], tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]
    ],
    action_start_s: float,
    action_stop_s: float,
    *,
    pose_time_grid_s: np.ndarray | None = None,
) -> Callable[[float], tuple[dict[str, np.ndarray], dict[str, np.ndarray]]]:
    duration = action_stop_s - action_start_s
    if pose_time_grid_s is None:
        count = max(2, int(np.ceil(duration * 10.0)) + 1)
        fractions = np.linspace(0.0, 1.0, count)
    else:
        pose_time = np.asarray(pose_time_grid_s, dtype=float)
        if (
            pose_time.ndim != 1
            or len(pose_time) < 2
            or not np.all(np.diff(pose_time) > 0.0)
        ):
            raise ValueError("pose time grid must be strictly increasing")
        fractions = (pose_time - pose_time[0]) / (pose_time[-1] - pose_time[0])
    times = action_start_s + duration * fractions
    offsets = {"left": [], "right": []}
    for fraction in fractions:
        body_offsets, _, _ = proxy_at_fraction(float(fraction))
        offsets["left"].append(np.asarray(body_offsets["BSF6C53"], float))
        offsets["right"].append(np.asarray(body_offsets["BSF8BC4"], float))
    offset_arrays = {
        side: np.stack(values) for side, values in offsets.items()
    }
    velocity_arrays = {
        side: np.gradient(values, times, axis=0, edge_order=1)
        for side, values in offset_arrays.items()
    }

    def interpolate(
        time_s: float,
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        query = float(np.clip(time_s, times[0], times[-1]))
        position = {
            side: np.asarray([
                np.interp(query, times, offset_arrays[side][:, axis])
                for axis in range(3)
            ])
            for side in ("left", "right")
        }
        velocity = {
            side: np.asarray([
                np.interp(query, times, velocity_arrays[side][:, axis])
                for axis in range(3)
            ])
            for side in ("left", "right")
        }
        return position, velocity

    return interpolate


def _maximum_active_episode_drift(
    positions: np.ndarray, active: np.ndarray
) -> float:
    maximum = 0.0
    start = None
    for index, is_active in enumerate(active):
        if is_active and start is None:
            start = index
        if start is not None and (not is_active or index == len(active) - 1):
            stop = index + 1 if is_active else index
            block = positions[start:stop]
            if len(block):
                maximum = max(
                    maximum,
                    float(np.max(np.linalg.norm(block - block[0], axis=1))),
                )
            start = None
    return maximum


def _contact_xy_episode_metrics(
    time_s: np.ndarray,
    ankle_world_m: np.ndarray,
    mask: np.ndarray,
) -> dict[str, Any]:
    """Measure every contiguous support episode on the final displayed XY."""

    times = np.asarray(time_s, dtype=float)
    positions = np.asarray(ankle_world_m, dtype=float)
    active = np.asarray(mask, dtype=bool)
    if (
        times.ndim != 1
        or positions.shape != (len(times), 2, 3)
        or active.shape != (len(times), 2)
    ):
        raise ValueError("contact XY audit arrays do not share one timeline")
    result: dict[str, Any] = {}
    for side_index, side in enumerate(("left", "right")):
        episodes = []
        start = None
        for index, is_active in enumerate(active[:, side_index]):
            if is_active and start is None:
                start = index
            if start is not None and (
                not is_active or index == len(active) - 1
            ):
                stop = index + 1 if is_active else index
                xy = positions[start:stop, side_index, :2]
                if len(xy):
                    relative_start = np.linalg.norm(xy - xy[0], axis=1)
                    envelope = np.max(xy, axis=0) - np.min(xy, axis=0)
                    episodes.append({
                        "start_index": int(start),
                        "stop_index_exclusive": int(stop),
                        "start_time_s": float(times[start]),
                        "stop_time_s": float(times[stop - 1]),
                        "sample_count": int(stop - start),
                        "maximum_relative_start_xy_m": float(
                            np.max(relative_start)
                        ),
                        "xy_axis_envelope_norm_m": float(np.linalg.norm(envelope)),
                        "x_peak_to_peak_m": float(envelope[0]),
                        "y_peak_to_peak_m": float(envelope[1]),
                    })
                start = None
        result[side] = {
            "episode_count": len(episodes),
            "maximum_relative_start_xy_m": (
                max(
                    row["maximum_relative_start_xy_m"] for row in episodes
                ) if episodes else 0.0
            ),
            "maximum_xy_axis_envelope_norm_m": (
                max(
                    row["xy_axis_envelope_norm_m"] for row in episodes
                ) if episodes else 0.0
            ),
            "episodes": episodes,
        }
    return result


def _h02_contact_reference_masks(
    reference_npz: Path,
    time_s: np.ndarray,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Load the frozen accepted v5 masks on the exact H02 output grid."""

    if _sha256(reference_npz) != (
        "fdf4e90419cb9b0856960f1af59ad35b354f07a0948944b181c7469063be6944"
    ):
        raise RuntimeError("H02 contact-v5 reference hash mismatch")
    with np.load(reference_npz, allow_pickle=False) as archive:
        reference_time = np.asarray(archive["time_s"], dtype=float)
        active = np.asarray(archive["contact_active"], dtype=bool)
        constrained = np.asarray(archive["contact_constrained"], dtype=bool)
    times = np.asarray(time_s, dtype=float)
    if len(reference_time) >= len(times):
        reference_time = reference_time[:len(times)]
        active = active[:len(times)]
        constrained = constrained[:len(times)]
    if (
        reference_time.shape != times.shape
        or not np.allclose(reference_time, times, atol=2e-6, rtol=0.0)
        or active.shape != (len(times), 2)
        or constrained.shape != (len(times), 2)
    ):
        raise RuntimeError("H02 contact-v5 reference grid does not match")
    return {"active": active, "constrained": constrained}, {
        "path": str(reference_npz),
        "sha256": _sha256(reference_npz),
        "time_grid_exact_within_2us": True,
        "prefix_sample_count": int(len(times)),
    }


def _native200_pose_continuity(
    *,
    absolute_time_s: np.ndarray,
    root_position_world_m: np.ndarray,
    correction_rotvec: np.ndarray,
    rotations_at_fraction: Callable[[float], Mapping[str, np.ndarray]],
    action_start_s: float,
    action_stop_s: float,
    transition_period_s: float,
    correction_cap_rad: float,
) -> tuple[dict[str, Any], dict[str, np.ndarray]]:
    """Audit final emitted root and segment SO(3) continuity at native rate."""

    sample_time = np.asarray(absolute_time_s, dtype=float)
    root = np.asarray(root_position_world_m, dtype=float)
    correction = np.asarray(correction_rotvec, dtype=float)
    if (
        sample_time.ndim != 1
        or len(sample_time) < 2
        or not np.all(np.diff(sample_time) > 0.0)
        or root.shape != (len(sample_time), 3)
        or correction.shape != (len(sample_time), len(ARTICULATED_SEGMENTS), 3)
    ):
        raise ValueError("native200 continuity arrays are invalid")
    duration = action_stop_s - action_start_s
    base_rows = []
    final_rows = []
    for time_s, correction_row in zip(sample_time, correction):
        fraction = float(np.clip((time_s - action_start_s) / duration, 0.0, 1.0))
        base = rotations_at_fraction(fraction)
        base_row = np.stack([
            np.asarray(base[segment], dtype=float).reshape(3, 3)
            for segment in ARTICULATED_SEGMENTS
        ])
        correction_row_matrix = Rotation.from_rotvec(
            correction_row
        ).as_matrix()
        base_rows.append(base_row)
        final_rows.append(np.einsum(
            "sij,sjk->sik", base_row, correction_row_matrix
        ))
    base_array = np.asarray(base_rows)
    final_array = np.asarray(final_rows)
    base_relative = np.einsum(
        "nsji,nsjk->nsik", base_array[:-1], base_array[1:]
    )
    final_relative = np.einsum(
        "nsji,nsjk->nsik", final_array[:-1], final_array[1:]
    )
    base_step = Rotation.from_matrix(
        base_relative.reshape(-1, 3, 3)
    ).magnitude().reshape(len(sample_time) - 1, len(ARTICULATED_SEGMENTS))
    final_step = Rotation.from_matrix(
        final_relative.reshape(-1, 3, 3)
    ).magnitude().reshape(len(sample_time) - 1, len(ARTICULATED_SEGMENTS))
    dt = np.diff(sample_time)
    transition_allowance = (
        2.0 * correction_cap_rad * dt / transition_period_s
    )[:, None]
    root_xy_step = np.linalg.norm(np.diff(root[:, :2], axis=0), axis=1)
    continuity = {
        "sample_count": int(len(sample_time)),
        "sample_period_ms_median": float(1e3 * np.median(dt)),
        "root_xy_step_m": {
            "maximum": float(np.max(root_xy_step)),
            "p99": float(np.percentile(root_xy_step, 99)),
        },
        "segment_final_so3_step_rad": {
            "maximum": float(np.max(final_step)),
            "p99": float(np.percentile(final_step, 99)),
        },
        "segment_final_angular_rate_rad_s": {
            "maximum": float(np.max(final_step / dt[:, None])),
            "p99": float(np.percentile(final_step / dt[:, None], 99)),
        },
        "segment_native_base_so3_step_rad": {
            "maximum": float(np.max(base_step)),
            "p99": float(np.percentile(base_step, 99)),
        },
        "transition_triangle_excess_maximum_rad": float(np.max(
            final_step - base_step - transition_allowance
        )),
        "transition_triangle_tolerance_rad": 2e-6,
    }
    return continuity, {
        "root_xy_step_m": root_xy_step,
        "base_segment_so3_step_rad": base_step,
        "final_segment_so3_step_rad": final_step,
        "transition_allowance_rad": transition_allowance,
    }


def _no_foothold_accepted_gap_audit(
    decisions: list[dict[str, Any]], *, limit_s: float
) -> dict[str, Any]:
    """Bound UWB update starvation only while contact owns no root support."""

    intervals = []
    current = None
    maximum_gap = 0.0
    for row in decisions:
        time_s = float(row["availability_time_s"])
        no_foothold = not row.get("measurement_time_foothold_sides")
        if not no_foothold:
            if current is not None:
                intervals.append(current)
                current = None
            continue
        if current is None:
            current = {
                "start_time_s": time_s,
                "stop_time_s": time_s,
                "accepted_count": 0,
                "maximum_accepted_gap_s": 0.0,
            }
            last_accepted_or_start = time_s
        current["stop_time_s"] = time_s
        gap = time_s - last_accepted_or_start
        current["maximum_accepted_gap_s"] = max(
            float(current["maximum_accepted_gap_s"]), gap
        )
        maximum_gap = max(maximum_gap, gap)
        if row.get("accepted"):
            current["accepted_count"] += 1
            last_accepted_or_start = time_s
    if current is not None:
        intervals.append(current)
    return {
        "owner": "NO_OWNED_FOOTHOLD_UWB_AVAILABILITY_INTERVALS",
        "limit_s": float(limit_s),
        "maximum_gap_s": float(maximum_gap),
        "intervals": intervals,
        "pass": maximum_gap <= float(limit_s) + 1e-12,
    }


def _articulated_mechanism_nondegeneracy_gate(
    *,
    absolute_time_s: np.ndarray,
    correction_high_rate_rotvec: np.ndarray,
    transition_active: np.ndarray,
    pose_install_events: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    uwb_dropout_s: float,
    required: bool,
) -> dict[str, Any]:
    """Reject a numerically clean run that never exercised fusion mechanics."""

    sample_time = np.asarray(absolute_time_s, dtype=float)
    correction = np.asarray(correction_high_rate_rotvec, dtype=float)
    active = np.asarray(transition_active, dtype=bool)
    completed = []
    for index, event in enumerate(pose_install_events):
        start = float(event["availability_time_s"])
        period = float(event["transition_period_s"])
        next_start = (
            math.inf
            if index + 1 == len(pose_install_events)
            else float(pose_install_events[index + 1]["availability_time_s"])
        )
        candidates = np.flatnonzero(
            (sample_time >= start + period + 0.004)
            & (sample_time < next_start - 1e-12)
            & ~active
        )
        completed.append({
            **event,
            "completed_on_emitted_native200_sample": bool(len(candidates)),
            "completion_sample_time_s": (
                None if not len(candidates)
                else float(sample_time[int(candidates[0])])
            ),
        })
    completed_nonzero = sum(
        row["completed_on_emitted_native200_sample"]
        and float(row["target_delta_maximum_rad"]) > 1e-8
        for row in completed
    )
    emitted_maximum = float(np.max(np.linalg.norm(
        correction, axis=2
    ))) if correction.size else 0.0
    accepted_with_contact = sum(
        bool(row.get("accepted"))
        and bool(row.get("measurement_time_foothold_sides"))
        for row in decisions
    )
    contact_rows = [
        row for row in decisions if row.get("measurement_time_foothold_sides")
    ]
    contact_conflicts = sum(
        row.get("reason") == "REJECT_CONTACT_MANIFOLD_CONFLICT"
        for row in contact_rows
    )
    no_foothold_gap = _no_foothold_accepted_gap_audit(
        decisions, limit_s=uwb_dropout_s
    )
    subgates = {
        "accepted_articulated_pose_install": bool(pose_install_events),
        "completed_nonzero_transition": completed_nonzero >= 1,
        "nonzero_emitted_high_rate_correction": emitted_maximum > 1e-8,
        "no_foothold_uwb_dropout_bound": bool(no_foothold_gap["pass"]),
        "accepted_uwb_root_with_owned_foothold": accepted_with_contact >= 1,
    }
    qualified = all(subgates.values())
    return {
        "owner": "FULL_RUN_FUSION_MECHANISM_NONDEGENERACY",
        "required": bool(required),
        "pose_install_events": completed,
        "accepted_pose_install_count": len(pose_install_events),
        "completed_nonzero_transition_count": int(completed_nonzero),
        "emitted_high_rate_correction_maximum_rad": emitted_maximum,
        "no_foothold_accepted_uwb_gap": no_foothold_gap,
        "contact_uwb_observation_count": len(contact_rows),
        "contact_manifold_conflict_count": int(contact_conflicts),
        "accepted_uwb_root_with_owned_foothold_count": int(
            accepted_with_contact
        ),
        "all_contact_uwb_physically_conflicting": bool(
            contact_rows and contact_conflicts == len(contact_rows)
        ),
        "subgates": subgates,
        "mechanism_qualified": qualified,
        "pass": (not required) or qualified,
    }


def _contact_reconcile_step_audit(
    reconcile: Any,
    contact_decision: Any,
    *,
    primary_side_after_update: str | None,
    soft_xy_delta_m: float,
    soft_velocity_delta_mps: float,
    maximum_position_step_m: float,
    maximum_velocity_step_mps: float,
) -> dict[str, Any]:
    """Keep ownership identity and ordinary contact step caps fail-closed."""

    entered = tuple(contact_decision.entered_sides)
    released = tuple(contact_decision.released_sides)
    lifecycle_transition = bool(entered or released)
    owner_identity_match = (
        tuple(reconcile.active_sides)
        == tuple(contact_decision.active_sides)
        and tuple(reconcile.constrained_sides)
        == tuple(contact_decision.constrained_sides)
        and reconcile.primary_side == primary_side_after_update
    )
    position_step_pass = (
        soft_xy_delta_m <= maximum_position_step_m + 1e-12
    )
    velocity_step_pass = (
        soft_velocity_delta_mps <= maximum_velocity_step_mps + 1e-12
    )
    passed = (
        (lifecycle_transition or owner_identity_match)
        and position_step_pass
        and velocity_step_pass
    )
    if not lifecycle_transition and not owner_identity_match:
        reason = "OWNER_IDENTITY_MISMATCH"
    elif not position_step_pass:
        reason = "ORDINARY_CONTACT_POSITION_STEP_EXCEEDED"
    elif not velocity_step_pass:
        reason = "ORDINARY_CONTACT_VELOCITY_STEP_EXCEEDED"
    elif lifecycle_transition:
        reason = "CONTACT_OWNERSHIP_TRANSITION"
    else:
        reason = "POSE_REGAUGE_THEN_BOUNDED_CONTACT"
    return {
        "same_owner_identity_checked": not lifecycle_transition,
        "ownership_transition": lifecycle_transition,
        "owner_identity_match": owner_identity_match,
        "reason": reason,
        "entered_sides": list(entered),
        "released_sides": list(released),
        "active_sides_after_update": list(contact_decision.active_sides),
        "constrained_sides_after_update": list(
            contact_decision.constrained_sides
        ),
        "primary_side_after_update": primary_side_after_update,
        "ordinary_contact_applied_xy_delta_m": float(soft_xy_delta_m),
        "ordinary_contact_position_step_limit_m": float(
            maximum_position_step_m
        ),
        "ordinary_contact_position_step_pass": position_step_pass,
        "ordinary_contact_applied_velocity_delta_mps": float(
            soft_velocity_delta_mps
        ),
        "ordinary_contact_velocity_step_limit_mps": float(
            maximum_velocity_step_mps
        ),
        "ordinary_contact_velocity_step_pass": velocity_step_pass,
        "pass": passed,
    }


def _causal_root_at_ankle_sample(
    state: RootState,
    query_time_s: float,
    *,
    maximum_age_s: float,
) -> tuple[np.ndarray | None, float]:
    """Propagate the last causal root state to an ankle sample without mutation."""

    age_s = float(query_time_s - state.time_s)
    if (
        not math.isfinite(age_s)
        or age_s < -1e-12
        or age_s > maximum_age_s + 1e-12
    ):
        return None, age_s
    return state.position_m + age_s * state.velocity_mps, age_s


def _positive_swing_cue(
    *,
    side: str,
    query_time_s: float,
    analytic_ankle_offset_world_m: Mapping[str, np.ndarray],
    root_state: RootState,
    foothold_corrector: DualFootFootholdCorrector,
    evidence: Mapping[str, FootContactEvidence],
    maximum_root_age_s: float,
    positive_swing_height_m: float,
) -> dict[str, Any]:
    """Return the causal, explicitly owned height cue for confirmed swing."""

    footholds = foothold_corrector.footholds_world_m()
    if side in footholds:
        root, age_s = _causal_root_at_ankle_sample(
            root_state, query_time_s, maximum_age_s=maximum_root_age_s
        )
        if root is None:
            return {
                "observable": False,
                "positive": False,
                "height_m": math.nan,
                "owner": "OWNED_FOOTHOLD_ROOT_TIME_UNOBSERVABLE",
                "root_owner_age_s": age_s,
            }
        height_m = float(
            root[2]
            + analytic_ankle_offset_world_m[side][2]
            - footholds[side][2]
        )
        return {
            "observable": True,
            "positive": height_m >= positive_swing_height_m,
            "height_m": height_m,
            "owner": "OWNED_FOOTHOLD_WORLD_Z",
            "root_owner_age_s": age_s,
        }

    other = "right" if side == "left" else "left"
    if evidence[other].is_confirmed_stance:
        height_m = float(
            analytic_ankle_offset_world_m[side][2]
            - analytic_ankle_offset_world_m[other][2]
        )
        return {
            "observable": True,
            "positive": height_m >= positive_swing_height_m,
            "height_m": height_m,
            "owner": "OPPOSITE_CONFIRMED_STANCE_RELATIVE_HEIGHT",
            "root_owner_age_s": math.nan,
        }
    return {
        "observable": False,
        "positive": False,
        "height_m": math.nan,
        "owner": "UNOBSERVABLE",
        "root_owner_age_s": math.nan,
    }


def _positive_swing_cues(
    *,
    query_time_s: float,
    analytic_ankle_offset_world_m: Mapping[str, np.ndarray],
    root_state: RootState,
    foothold_corrector: DualFootFootholdCorrector,
    evidence: Mapping[str, FootContactEvidence],
    maximum_root_age_s: float,
    positive_swing_height_m: float,
) -> dict[str, dict[str, Any]]:
    """Compute raw bilateral lift evidence without applying an activity prior."""

    cues = {
        side: _positive_swing_cue(
            side=side,
            query_time_s=query_time_s,
            analytic_ankle_offset_world_m=analytic_ankle_offset_world_m,
            root_state=root_state,
            foothold_corrector=foothold_corrector,
            evidence=evidence,
            maximum_root_age_s=maximum_root_age_s,
            positive_swing_height_m=positive_swing_height_m,
        )
        for side in ("left", "right")
    }
    positive_sides = tuple(
        side for side in ("left", "right") if cues[side]["positive"]
    )
    classification = {
        (): "NONE",
        ("left",): "UNILATERAL_LEFT",
        ("right",): "UNILATERAL_RIGHT",
        ("left", "right"): "BILATERAL",
    }[positive_sides]
    observed_lift_m = {
        side: (
            float(cues[side]["height_m"])
            if math.isfinite(cues[side]["height_m"]) else None
        )
        for side in ("left", "right")
    }
    return {
        side: {
            **cue,
            "positive_lift_classification": classification,
            "positive_lift_sides": positive_sides,
            "observed_lift_m": observed_lift_m,
        }
        for side, cue in cues.items()
    }


def _confirmed_measurement_footholds(
    footholds_world_m: Mapping[str, np.ndarray],
    evidence: Mapping[str, FootContactEvidence],
) -> dict[str, np.ndarray]:
    """Expose only observed stance to delayed UWB contact residuals."""

    return {
        side: np.asarray(point, dtype=float).copy()
        for side, point in footholds_world_m.items()
        if evidence[side].is_confirmed_stance
    }


def _prior_held_uwb_gate(
    relative_time_s: np.ndarray,
    prior_held: np.ndarray,
    decisions: Sequence[Mapping[str, Any]],
    *,
    bootstrap_time_s: float,
    dropout_s: float,
    required: bool,
) -> dict[str, Any]:
    """Require UWB root acceptance inside every long prior-held episode."""

    time_s = np.asarray(relative_time_s, dtype=float)
    mask = np.asarray(prior_held, dtype=bool)
    if mask.shape != (len(time_s), 2):
        raise ValueError("prior-held mask must be Nx2")
    accepted_time_s = np.asarray([
        float(row["availability_time_s"]) - bootstrap_time_s
        for row in decisions if bool(row["accepted"])
    ])
    episodes = []
    for side_index, side in enumerate(("left", "right")):
        padded = np.r_[False, mask[:, side_index], False]
        starts = np.flatnonzero(~padded[:-1] & padded[1:])
        stops = np.flatnonzero(padded[:-1] & ~padded[1:])
        for start, stop in zip(starts, stops):
            start_s = float(time_s[start])
            stop_s = float(time_s[stop - 1])
            duration_s = max(0.0, stop_s - start_s)
            accepted = int(np.sum(
                (accepted_time_s >= start_s - 1e-12)
                & (accepted_time_s <= stop_s + 1e-12)
            ))
            needs_acceptance = duration_s > dropout_s + 1e-12
            episodes.append({
                "side": side,
                "start_s": start_s,
                "stop_s": stop_s,
                "duration_s": duration_s,
                "accepted_uwb_root_count": accepted,
                "requires_accepted_uwb_root": needs_acceptance,
                "pass": (not needs_acceptance) or accepted > 0,
            })
    passed = all(row["pass"] for row in episodes)
    return {
        "required": bool(required),
        "dropout_limit_s": float(dropout_s),
        "episodes": episodes,
        "pass": (not required) or passed,
        "reason": (
            "PASS" if (not required) or passed else "PRIOR_STARVED_UWB"
        ),
    }


def run(
    output: Path,
    clock_path: Path,
    *,
    action: str = DEFAULT_ACTION,
    bias_table_path: Path = DEFAULT_BIAS_TABLE,
    maximum_position_influence_m: float = 0.05,
    update_stride: int = 1,
    update_offset: int = 0,
    link_selection: str = "all",
    body_update: str = "shared_root",
    articulated_stride: int = 4,
    node_selection: str = "adaptive",
    ankle_contact: bool = False,
    stationary_no_flight_prior: bool = False,
    analytic_pose_result_path: Path | None = None,
    analytic_calibration_report_path: Path | None = None,
    pre_ik_hxx_report_path: Path | None = None,
    maximum_duration_s: float | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported C2 action: {action}")
    if not 0.0 < maximum_position_influence_m <= 0.25:
        raise ValueError("position influence must be in (0, 0.25] metres")
    if update_stride < 1 or not 0 <= update_offset < update_stride:
        raise ValueError("invalid position-update stride/offset")
    if link_selection not in LINK_SELECTIONS:
        raise ValueError(f"unsupported link selection: {link_selection}")
    if body_update not in BODY_UPDATES:
        raise ValueError(f"unsupported body update: {body_update}")
    if node_selection not in NODE_SELECTIONS:
        raise ValueError(f"unsupported node selection: {node_selection}")
    if body_update == "articulated_consensus" and link_selection != "all":
        raise ValueError("articulated consensus retains all links")
    if articulated_stride < 1:
        raise ValueError("articulated stride must be positive")
    if maximum_duration_s is not None and not (
        math.isfinite(maximum_duration_s) and maximum_duration_s >= 1.0
    ):
        raise ValueError("pilot maximum duration must be at least one second")
    if stationary_no_flight_prior and action != "H02_golf":
        raise ValueError(
            "stationary no-flight prior is explicitly restricted to H02_golf"
        )
    if stationary_no_flight_prior and not ankle_contact:
        raise ValueError("stationary no-flight prior requires ankle contact")
    output.mkdir(parents=True, exist_ok=False)
    clocks = _clock_models(clock_path)
    bridges = _beacon_boundary_bridges(clock_path)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    alignment, frozen_forward = frozen_world_alignment(calibration)
    analytic_owner: dict[str, Any] | None = None
    hinge_model: dict[str, Any] = {}
    hinge_projector = None
    pose_time_grid_s = None
    analytic_inputs = (
        analytic_pose_result_path,
        analytic_calibration_report_path,
        pre_ik_hxx_report_path,
    )
    if any(value is None for value in analytic_inputs) and any(
        value is not None for value in analytic_inputs
    ):
        raise ValueError(
            "analytic result, calibration, and pre-IK HXX reports are one input set"
        )
    if action in CALIBRATION_ORDER:
        if analytic_pose_result_path is not None:
            raise ValueError("analytic HXX pose input is not valid for 00--18")
        episode = _load_episode(action, clocks, bridges)
        episode_key = episode["episode_key"]

        def proxy(fraction: float):
            return body_proxy_at_fraction(
                calibration, episode_key, fraction, alignment
            )

        def rotations(fraction: float):
            frame = nearest_frame(calibration, episode_key, fraction)
            return {
                segment: alignment @ rotation_from_wxyz(
                    calibration.series(episode_key, segment)
                    .quat_world_segment_wxyz[frame]
                )
                for segment in ARTICULATED_SEGMENTS
            }

        proxy_scope = "FROZEN_C2_3A_PUBLIC_FK_DISPLAY_PROXY"
        if action == CALIBRATION_ORDER[0]:
            range_policy = "raw_all"
            biases = None
            bias_role = "NOT_APPLIED_TO_ITS_OWN_CALIBRATION_OWNER"
        else:
            range_policy = "bounded_bias_variance"
            biases = load_pair_bias_table(
                bias_table_path, nodes=NODE_TO_PROXY_POINT
            )
            bias_role = "FROZEN_00_TABLE_TRANSFER_WITHOUT_EPISODE_REFIT"
    else:
        episode = _load_holdout(action, clocks, bridges)
        holdout = load_frozen_c2_hxx_diagnostics()
        if analytic_pose_result_path is not None:
            analytic_trajectory, hinge_model, analytic_owner = (
                _load_analytic_pose_owner(
                    analytic_pose_result_path,
                    analytic_calibration_report_path,
                    pre_ik_hxx_report_path,
                )
            )
            body = FrozenHoldoutBodyProxy.create(
                holdout, calibration, trajectory=analytic_trajectory
            )
            hinge_projector = partial(
                project_hinge_corrections, model=hinge_model
            )
            pose_time_grid_s = body.time_grid_s(action)
            proxy_scope = "NATIVE200_ANALYTIC_IK_HXX_DISPLAY_PROXY"
        else:
            body = FrozenHoldoutBodyProxy.create(holdout, calibration)
            proxy_scope = "FROZEN_HXX_DISPLAY_PROXY"

        def proxy(fraction: float):
            return body.at_fraction(action, fraction, alignment)

        def rotations(fraction: float):
            return body.rotations_at_fraction(action, fraction, alignment)

        range_policy = "raw_all"
        biases = None
        bias_role = "00_PAIR_TRANSFER_NOT_USED_ON_HXX"
    effective_body_update = _effective_body_update(action, body_update)
    groups, epoch_group_audit = _usable_epoch_groups(episode, clocks)
    if maximum_duration_s is not None:
        group_epoch_s = np.asarray([
            np.median([
                clocks[row.node].seconds(row.strobe_us) for row in group
            ])
            for group in groups
        ])
        groups = [
            group for group, epoch_s in zip(groups, group_epoch_s)
            if epoch_s <= group_epoch_s[0] + maximum_duration_s + 1e-12
        ]
        pilot_histogram = Counter(len(group) for group in groups)
        epoch_group_audit = {
            **epoch_group_audit,
            "node_count_histogram": {
                str(count): int(frequency)
                for count, frequency in sorted(pilot_histogram.items())
            },
            "partial_epochs_consumed": int(sum(
                frequency for count, frequency in pilot_histogram.items()
                if count < 10
            )),
            "complete_ten_node_epochs": int(pilot_histogram.get(10, 0)),
            "pilot_maximum_duration_s": maximum_duration_s,
            "pilot_retained_epochs": len(groups),
        }
    episode = {**episode, "groups": groups}
    observations = _shared_root_observations(
        episode,
        proxy_at_fraction=proxy,
        anchors=anchors,
        delays=delays,
        tag_delay=tag_delay,
        layout_sigma=layout_sigma,
        clocks=clocks,
        policy=range_policy,
        biases=biases,
        link_selection=link_selection,
        body_update=effective_body_update,
        base_rotations_at_fraction=rotations,
        geometry=calibration.geometry,
        articulated_stride=articulated_stride,
        node_selection=node_selection,
        hinge_projector=hinge_projector,
    )
    if len(observations) < 10:
        raise RuntimeError("insufficient accepted shared-root observations")

    events, decode = decode_measurements(episode["raw"])
    imu, orientation_audit = _pelvis_imu(
        events, clocks[PELVIS_NODE], episode["lo"], 0.0
    )
    action_start_s = episode["lo"] * 1e-9
    action_stop_s = episode["hi"] * 1e-9
    contact_config = AnkleContactConfig()
    contact_profiles = None
    contact_detector = None
    foothold_corrector = None
    ankle_proxy = None
    ankle_rows: list[dict[str, Any]] = []
    latest_contact_evidence = {
        side: FootContactEvidence(
            side, action_start_s, 0.0, False, np.nan, np.nan, np.nan,
            "DISABLED" if not ankle_contact else "NO_SAMPLE",
            support_state=FootSupportState.UNOBSERVABLE.value,
        )
        for side in ("left", "right")
    }
    if ankle_contact:
        contact_profiles = _fit_contact_profiles(clocks, bridges, contact_config)
        contact_detector = AnkleContactDetector(
            contact_profiles,
            contact_config,
            stationary_no_flight_prior=stationary_no_flight_prior,
        )
        foothold_corrector = DualFootFootholdCorrector()
        ankle_proxy = _ankle_proxy_interpolator(
            proxy,
            action_start_s,
            action_stop_s,
            pose_time_grid_s=pose_time_grid_s,
        )
        ankle_rows = _ankle_imu_rows(
            events, clocks, episode["lo"], episode["hi"]
        )
        if min(
            sum(row["side"] == side for row in ankle_rows)
            for side in ("left", "right")
        ) < 100:
            raise RuntimeError("insufficient bilateral ankle IMU for contact")
    bias = _initial_bias(imu, action_start_s)
    config = RootFilterConfig(
        fixed_lag_s=0.10,
        maximum_position_influence_m=maximum_position_influence_m,
    )
    fused_filter = _new_filter(observations[0], bias, config)
    imu_filter = _new_filter(observations[0], bias, config)
    bootstrap_time = observations[0]["availability_time_s"]
    processing_stop_s = (
        action_stop_s if maximum_duration_s is None
        else min(action_stop_s, bootstrap_time + maximum_duration_s)
    )
    causal_pose: CausalArticulatedPose | None = None
    if (
        ankle_contact
        and effective_body_update == "articulated_consensus"
        and hinge_projector is not None
    ):
        causal_pose = CausalArticulatedPose(
            action_start_s=action_start_s,
            action_stop_s=action_stop_s,
            rotations_at_fraction=rotations,
            geometry=calibration.geometry,
            hinge_projector=hinge_projector,
        )
        bootstrap_correction = {
            segment: np.asarray(
                observations[0]["segment_correction_rotvec"], dtype=float
            )[segment_index]
            for segment_index, segment in enumerate(ARTICULATED_SEGMENTS)
        }
        if observations[0]["articulated_accepted"]:
            causal_pose.install(
                bootstrap_correction,
                measurement_time_s=observations[0]["measurement_time_s"],
                availability_time_s=bootstrap_time,
            )

    def ankle_pose_at(
        query_time_s: float,
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        Any | None,
    ]:
        if causal_pose is not None:
            sample = causal_pose.sample(query_time_s)
            return (
                dict(sample.ankle_offset_world_m),
                dict(sample.ankle_offset_velocity_world_mps),
                sample,
            )
        if ankle_proxy is None:
            zero = {side: np.zeros(3) for side in ("left", "right")}
            return zero, {side: value.copy() for side, value in zero.items()}, None
        offsets, velocities = ankle_proxy(query_time_s)
        return offsets, velocities, None

    timeline: list[tuple[float, int, Any]] = []
    for row in ankle_rows:
        if bootstrap_time < row["time_s"] < processing_stop_s:
            timeline.append((row["time_s"], 0, row))
    for row in imu:
        if bootstrap_time < row["time_s"] < processing_stop_s:
            timeline.append((row["time_s"], 1, row))
    observation_update_mask = np.zeros(len(observations), dtype=bool)
    observation_update_mask[0] = True
    for index, row in enumerate(observations[1:], start=1):
        selected = (index - 1) % update_stride == update_offset
        observation_update_mask[index] = selected
        if selected and row["availability_time_s"] < processing_stop_s:
            timeline.append((row["availability_time_s"], 2, row))
    timeline.sort(key=lambda item: (item[0], item[1]))

    output_time = []
    fused_position = []
    fused_velocity = []
    fused_bias = []
    imu_position = []
    imu_velocity = []
    decisions = []
    contact_confidence = []
    contact_active = []
    contact_confirmed = []
    contact_prior_held = []
    contact_prior_held_support_confidence = []
    contact_replay_operator_confidence = []
    contact_support_state = []
    contact_constrained = []
    contact_ankle_world = []
    contact_position_delta = []
    contact_velocity_delta = []
    contact_foothold_world = []
    contact_foothold_xy_residual = []
    articulated_correction_high_rate = []
    articulated_projection_fk_residual_deg = []
    articulated_projection_inside_rom = []
    articulated_velocity_baseline_reset = []
    articulated_transition_step_rad = []
    articulated_transition_active = []
    pose_install_events: list[dict[str, Any]] = []
    pose_reconcile_root_delta = []
    pose_reconcile_events = []
    contact_events = []
    contact_detector_cues = []
    contact_aware_articulated_events = []
    published_pose_history: list[dict[str, Any]] = []
    post_uwb_contact_reapplications = 0
    native_pose_dt_s = (
        float(np.median(np.diff(pose_time_grid_s)))
        if pose_time_grid_s is not None and len(pose_time_grid_s) > 1
        else 0.005
    )
    contact_root_owner_maximum_age_s = 1.5 * native_pose_dt_s
    for availability_time, kind, payload in timeline:
        if kind == 0:
            # Detector ownership is intentionally pre-UWB analytic pose.  The
            # corrected causal pose remains the owner of emitted ankles and
            # foothold/root constraints below.
            offsets, offset_velocity = ankle_proxy(availability_time)
            side = payload["side"]
            previous_support_state = (
                latest_contact_evidence[side].resolved_support_state
            )
            lower_height = min(offsets[side][2] for side in ("left", "right"))
            swing_cues = _positive_swing_cues(
                query_time_s=availability_time,
                analytic_ankle_offset_world_m=offsets,
                root_state=fused_filter.current_state,
                foothold_corrector=foothold_corrector,
                evidence=latest_contact_evidence,
                maximum_root_age_s=contact_root_owner_maximum_age_s,
                positive_swing_height_m=contact_config.maximum_height_margin_m,
            )
            swing = swing_cues[side]
            evidence = contact_detector.update(
                side,
                time_s=availability_time,
                acceleration_mps2=payload["acceleration"],
                gyro_rad_s=payload["gyro"],
                relative_height_m=(
                    offsets[side][2] - lower_height
                ),
                relative_speed_mps=float(np.linalg.norm(
                    offset_velocity[side]
                )),
                positive_swing=swing["positive"],
                swing_observable=swing["observable"],
            )
            latest_contact_evidence[side] = evidence
            contact_detector_cues.append({
                "time_s": float(availability_time - bootstrap_time),
                "side": side,
                "support_state": evidence.resolved_support_state.value,
                "reason": evidence.reason,
                "confidence": evidence.confidence,
                "relative_height_m": evidence.relative_height_m,
                "relative_speed_mps": float(np.linalg.norm(
                    offset_velocity[side]
                )),
                "positive_swing": evidence.positive_swing,
                "swing_observable": evidence.swing_observable,
                "swing_height_m": swing["height_m"],
                "swing_height_owner": swing["owner"],
                "root_owner_age_s": swing["root_owner_age_s"],
                "prior_held": evidence.prior_held,
                "prior_held_support_confidence": (
                    evidence.prior_held_support_confidence
                ),
                "activity_prior_conflict": (
                    evidence.activity_prior_conflict
                ),
                "positive_lift_classification": swing[
                    "positive_lift_classification"
                ],
                "positive_lift_sides": swing["positive_lift_sides"],
                "observed_lift_m": swing["observed_lift_m"],
            })
            if evidence.resolved_support_state is not previous_support_state:
                contact_events.append({
                    "time_s": evidence.time_s - bootstrap_time,
                    "side": evidence.side,
                    "event": evidence.reason,
                    "confidence": evidence.confidence,
                    "from": previous_support_state.value,
                    "to": evidence.resolved_support_state.value,
                    "positive_swing": evidence.positive_swing,
                    "swing_height_m": swing["height_m"],
                    "swing_height_owner": swing["owner"],
                    "prior_held": evidence.prior_held,
                    "activity_prior_conflict": (
                        evidence.activity_prior_conflict
                    ),
                    "positive_lift_classification": swing[
                        "positive_lift_classification"
                    ],
                    "positive_lift_sides": swing["positive_lift_sides"],
                    "observed_lift_m": swing["observed_lift_m"],
                })
        elif kind == 1:
            sample = ImuSample(
                measurement_time_s=payload["time_s"],
                availability_time_s=payload["time_s"],
                specific_force_sensor_mps2=payload["acceleration"],
                rotation_world_from_sensor=payload["rotation_world"],
                source_sequence=payload["sequence"],
            )
            if not fused_filter.add_imu(sample) or not imu_filter.add_imu(sample):
                raise RuntimeError("causal IMU propagation rejected an in-order sample")
            offsets = {side: np.zeros(3) for side in ("left", "right")}
            offset_velocity = {
                side: np.zeros(3) for side in ("left", "right")
            }
            pose_sample = None
            sample_reconcile_delta = np.zeros(3)
            sample_reconcile_event = None
            if ankle_contact:
                previous_gauge_offsets = None
                previous_gauge_velocity = None
                if causal_pose is not None and published_pose_history:
                    previous_correction = published_pose_history[-1][
                        "correction_rotvec"
                    ]
                    (
                        previous_gauge_offsets,
                        previous_gauge_velocity,
                    ) = causal_pose.ankle_kinematics_for_published_correction(
                        availability_time, previous_correction
                    )
                offsets, offset_velocity, pose_sample = ankle_pose_at(
                    availability_time
                )
                if previous_gauge_offsets is None:
                    previous_gauge_offsets = {
                        side: value.copy() for side, value in offsets.items()
                    }
                    previous_gauge_velocity = {
                        side: value.copy()
                        for side, value in offset_velocity.items()
                    }
                root_before_pose_reconcile = (
                    fused_filter.current_state.position_m.copy()
                )
                if causal_pose is not None:
                    reconciled, reconcile = (
                        foothold_corrector.reconcile_pose_change(
                            fused_filter.current_state,
                            evidence=latest_contact_evidence,
                            previous_ankle_offset_world_m=(
                                previous_gauge_offsets
                            ),
                            previous_ankle_offset_velocity_world_mps=(
                                previous_gauge_velocity
                            ),
                            ankle_offset_world_m=offsets,
                            ankle_offset_velocity_world_mps=offset_velocity,
                        )
                    )
                    if reconcile.accepted:
                        fused_filter.apply_current_constraint(
                            reconciled,
                            operator=reconcile.replay_operator,
                            owner="NATIVE200_POSE_REGAUGE",
                        )
                        sample_reconcile_delta = (
                            reconcile.applied_position_delta_m.copy()
                        )
                        sample_reconcile_event = {
                            "event": "NATIVE200_IMU_POSE_TRANSITION",
                            "availability_time_s": availability_time,
                            "active_sides": list(reconcile.active_sides),
                            "constrained_sides": list(
                                reconcile.constrained_sides
                            ),
                            "primary_side": reconcile.primary_side,
                            "pre_xy_residual_m": dict(
                                reconcile.pre_xy_residual_m
                            ),
                            "post_xy_residual_m": dict(
                                reconcile.post_xy_residual_m
                            ),
                            "applied_position_delta_m": (
                                reconcile.applied_position_delta_m.tolist()
                            ),
                            "applied_velocity_delta_mps": (
                                reconcile.applied_velocity_delta_mps.tolist()
                            ),
                            "transition_step_maximum_rad": float(
                                pose_sample.transition_step_maximum_rad
                            ),
                        }
                root_after_pose_reconcile = (
                    fused_filter.current_state.position_m.copy()
                )
                constrained, contact_decision = foothold_corrector.update(
                    fused_filter.current_state,
                    evidence=latest_contact_evidence,
                    ankle_offset_world_m=offsets,
                    ankle_offset_velocity_world_mps=offset_velocity,
                )
                if contact_decision.accepted:
                    fused_filter.apply_current_constraint(
                        constrained,
                        operator=contact_decision.replay_operator,
                        owner="NATIVE200_FOOTHOLD_SOFT_UPDATE",
                    )
                root_after_soft_contact = (
                    fused_filter.current_state.position_m.copy()
                )
                same_pose_soft_xy_delta = float(np.linalg.norm(
                    contact_decision.applied_position_delta_m[:2]
                ))
                if sample_reconcile_event is not None:
                    owner_audit = _contact_reconcile_step_audit(
                        reconcile,
                        contact_decision,
                        primary_side_after_update=(
                            foothold_corrector.primary_side
                        ),
                        soft_xy_delta_m=same_pose_soft_xy_delta,
                        soft_velocity_delta_mps=float(np.linalg.norm(
                            contact_decision.applied_velocity_delta_mps[:2]
                        )),
                        maximum_position_step_m=(
                            foothold_corrector.config.maximum_position_step_m
                        ),
                        maximum_velocity_step_mps=(
                            foothold_corrector.config.maximum_velocity_step_mps
                        ),
                    )
                    sample_reconcile_event.update({
                        **owner_audit,
                        "event": (
                            "CONTACT_OWNERSHIP_TRANSITION"
                            if owner_audit["ownership_transition"]
                            else "NATIVE200_IMU_POSE_TRANSITION"
                        ),
                        "timeline_event_kind": "NATIVE200_IMU",
                        "root_position_before_reconcile_m": (
                            root_before_pose_reconcile.tolist()
                        ),
                        "root_position_after_reconcile_m": (
                            root_after_pose_reconcile.tolist()
                        ),
                        "root_position_after_soft_contact_m": (
                            root_after_soft_contact.tolist()
                        ),
                        "prior_held_support_confidence": (
                            foothold_corrector.prior_held_support_confidence()
                        ),
                        "ordinary_contact_operator_confidence": (
                            None
                            if contact_decision.replay_operator is None
                            else contact_decision.replay_operator.confidence
                        ),
                    })
                    pose_reconcile_events.append(sample_reconcile_event)
                    if not owner_audit["pass"]:
                        failure = {
                            "reason": owner_audit["reason"],
                            "event": sample_reconcile_event,
                            "latest_contact_evidence": {
                                side: {
                                    "time_s": row.time_s,
                                    "confidence": row.confidence,
                                    "contact": row.contact,
                                    "reason": row.reason,
                                }
                                for side, row in (
                                    latest_contact_evidence.items()
                                )
                            },
                            "latest_uwb_decision": (
                                None if not decisions else decisions[-1]
                            ),
                        }
                        (
                            output / "RUNTIME_CONTACT_ORDER_FAILURE.json"
                        ).write_text(
                            json.dumps(_json_ready(failure), indent=2) + "\n",
                            encoding="utf-8",
                        )
                        raise RuntimeError(
                            "native200 foothold ownership/idempotency check "
                            f"failed: {owner_audit['reason']}"
                        )
                contact_position_delta.append(
                    contact_decision.applied_position_delta_m
                )
                contact_velocity_delta.append(
                    contact_decision.applied_velocity_delta_mps
                )
                constrained_sides = set(contact_decision.constrained_sides)
            else:
                contact_position_delta.append(np.zeros(3))
                contact_velocity_delta.append(np.zeros(3))
                constrained_sides = set()
            fused = fused_filter.emit(availability_time)
            inertial = imu_filter.emit(availability_time)
            if pose_sample is not None:
                published_pose_history.append({
                    "time_s": float(availability_time),
                    "correction_rotvec": {
                        segment: np.asarray(
                            pose_sample.correction_rotvec[segment], dtype=float
                        ).copy()
                        for segment in ARTICULATED_SEGMENTS
                    },
                    "evidence": dict(latest_contact_evidence),
                    "primary_side": foothold_corrector.primary_side,
                })
            output_time.append(availability_time)
            fused_position.append(fused.root_position_m)
            fused_velocity.append(fused.root_velocity_mps)
            fused_bias.append(fused_filter.current_state.accelerometer_bias_mps2.copy())
            imu_position.append(inertial.root_position_m)
            imu_velocity.append(inertial.root_velocity_mps)
            contact_confidence.append([
                latest_contact_evidence[side].confidence
                for side in ("left", "right")
            ])
            footholds = (
                foothold_corrector.footholds_world_m()
                if foothold_corrector is not None else {}
            )
            contact_active.append([
                side in footholds
                for side in ("left", "right")
            ])
            contact_confirmed.append([
                latest_contact_evidence[side].is_confirmed_stance
                for side in ("left", "right")
            ])
            contact_prior_held.append([
                latest_contact_evidence[side].prior_held
                for side in ("left", "right")
            ])
            prior_confidence_owner = (
                foothold_corrector.prior_held_support_confidence()
                if foothold_corrector is not None else {}
            )
            contact_prior_held_support_confidence.append([
                prior_confidence_owner.get(side, np.nan)
                for side in ("left", "right")
            ])
            contact_replay_operator_confidence.append(
                float(contact_decision.replay_operator.confidence)
                if ankle_contact and contact_decision.replay_operator is not None
                else np.nan
            )
            contact_support_state.append([
                latest_contact_evidence[side].resolved_support_state.value
                for side in ("left", "right")
            ])
            contact_constrained.append([
                side in constrained_sides for side in ("left", "right")
            ])
            contact_ankle_world.append([
                fused.root_position_m + offsets[side]
                for side in ("left", "right")
            ])
            pose_reconcile_root_delta.append(sample_reconcile_delta)
            contact_foothold_world.append([
                footholds.get(side, np.full(3, np.nan))
                for side in ("left", "right")
            ])
            contact_foothold_xy_residual.append([
                float(np.linalg.norm(
                    (fused.root_position_m + offsets[side]
                     - footholds[side])[:2]
                )) if side in footholds else np.nan
                for side in ("left", "right")
            ])
            if pose_sample is not None:
                articulated_correction_high_rate.append(np.stack([
                    pose_sample.correction_rotvec[segment]
                    for segment in ARTICULATED_SEGMENTS
                ]))
                articulated_projection_fk_residual_deg.append(float(
                    pose_sample.projection[
                        "fk_direction_residual_maximum_deg"
                    ]
                ))
                articulated_projection_inside_rom.append(bool(
                    pose_sample.projection["post_projection_all_inside_rom"]
                ))
                articulated_velocity_baseline_reset.append(bool(
                    pose_sample.velocity_baseline_reset
                ))
                articulated_transition_step_rad.append(float(
                    pose_sample.transition_step_maximum_rad
                ))
                articulated_transition_active.append(bool(
                    pose_sample.transition_active
                ))
            else:
                articulated_correction_high_rate.append(np.zeros(
                    (len(ARTICULATED_SEGMENTS), 3), dtype=float
                ))
                articulated_projection_fk_residual_deg.append(0.0)
                articulated_projection_inside_rom.append(True)
                articulated_velocity_baseline_reset.append(False)
                articulated_transition_step_rad.append(0.0)
                articulated_transition_active.append(False)
        else:
            measurement_identity_footholds = (
                foothold_corrector.footholds_at_time(
                    float(payload["measurement_time_s"])
                )
                if (
                    foothold_corrector is not None
                    and effective_body_update == "articulated_consensus"
                    and hinge_projector is not None
                )
                else {}
            )
            published_gauge = None
            published_gauge_error = None
            if causal_pose is not None and measurement_identity_footholds:
                try:
                    published_gauge = _published_pose_gauge_at_measurement(
                        published_pose_history,
                        measurement_time_s=float(
                            payload["measurement_time_s"]
                        ),
                        pose_owner=causal_pose,
                    )
                except (KeyError, RuntimeError, ValueError) as exc:
                    published_gauge_error = str(exc)
            measurement_evidence = (
                {} if published_gauge is None else published_gauge[1]
            )
            measurement_footholds = (
                _confirmed_measurement_footholds(
                    measurement_identity_footholds,
                    measurement_evidence,
                )
                if published_gauge is not None else {}
            )
            measurement_support_class = (
                "CONFIRMED"
                if measurement_footholds else (
                    "PRIOR_HELD"
                    if any(
                        row.prior_held for row in measurement_evidence.values()
                    ) else "NO_CONTACT_CONSTRAINT"
                )
            )
            if (
                effective_body_update == "articulated_consensus"
                and (not measurement_footholds or published_gauge is not None)
            ):
                contact_aware_diagnostic = (
                    _apply_measurement_time_footholds_to_observation(
                        payload,
                        footholds_world_m=measurement_footholds,
                        anchors=anchors,
                        rotations_at_fraction=rotations,
                        geometry=calibration.geometry,
                        hinge_projector=hinge_projector,
                    )
                    if hinge_projector is not None else {
                        "attempted": False,
                        "accepted": False,
                        "reason": "NO_HINGE_PROJECTOR",
                    }
                )
                if published_gauge is not None:
                    contact_aware_diagnostic[
                        "published_pose_history_source_time_s"
                    ] = published_gauge[2]
                    contact_aware_diagnostic[
                        "published_pose_history_is_not_future"
                    ] = bool(
                        published_gauge[2]
                        <= float(payload["measurement_time_s"]) + 1e-12
                    )
                contact_aware_articulated_events.append(
                    _json_ready(contact_aware_diagnostic)
                )
            elif effective_body_update == "articulated_consensus":
                contact_aware_diagnostic = {
                    "attempted": False,
                    "accepted": False,
                    "reason": "NO_CAUSALLY_PUBLISHED_MEASUREMENT_POSE",
                    "detail": published_gauge_error,
                    "measurement_time_s": float(
                        payload["measurement_time_s"]
                    ),
                    "sides": sorted(measurement_footholds),
                }
                contact_aware_articulated_events.append(
                    _json_ready(contact_aware_diagnostic)
                )
            else:
                contact_aware_diagnostic = {
                    "attempted": False,
                    "accepted": False,
                    "reason": "SHARED_ROOT_ONLY",
                }
            root_pose_gauge_diagnostic = None
            if measurement_identity_footholds and published_gauge is None:
                root_pose_gauge_diagnostic = {
                    "applied": False,
                    "reason": "REJECTED_MISSING_MEASUREMENT_TIME_POSE_GAUGE",
                    "detail": published_gauge_error,
                    "source_owner": payload["root_pose_gauge_owner"],
                }
                fused_filter.advance_to_availability(availability_time)
                decisions.append({
                    "measurement_time_s": payload["measurement_time_s"],
                    "availability_time_s": availability_time,
                    "accepted": False,
                    "reason": root_pose_gauge_diagnostic["reason"],
                    "nis": None,
                    "innovation_m": [0.0, 0.0, 0.0],
                    "applied_position_delta_m": [0.0, 0.0, 0.0],
                    "influence_scale": 0.0,
                    "availability_applied_position_delta_m": [0.0, 0.0, 0.0],
                    "availability_applied_velocity_delta_mps": [0.0, 0.0, 0.0],
                    "availability_influence_scale": 0.0,
                    "contact_influence_multiplier": 0.0,
                    "articulated_pose_installed": False,
                    "measurement_time_foothold_sides": sorted(
                        measurement_footholds
                    ),
                    "measurement_time_identity_foothold_sides": sorted(
                        measurement_identity_footholds
                    ),
                    "measurement_support_class": "UNKNOWN_MISSING_HISTORY",
                    "contact_aware_articulated": _json_ready(
                        contact_aware_diagnostic
                    ),
                    "root_pose_gauge": root_pose_gauge_diagnostic,
                    "pose_reconcile": None,
                })
                continue
            root_pose_gauge_diagnostic = (
                _reexpress_observation_root_to_published_pose(
                    payload,
                    footholds_world_m=measurement_footholds,
                    published_ankle_offset_world_m=(
                        None if published_gauge is None else published_gauge[0]
                    ),
                    published_correction_rotvec=(
                        None if published_gauge is None else published_gauge[4]
                    ),
                    measurement_evidence=(
                        None if published_gauge is None else published_gauge[1]
                    ),
                    measurement_primary_side=(
                        None if published_gauge is None else published_gauge[3]
                    ),
                    foothold_corrector=foothold_corrector,
                    rotations_at_fraction=rotations,
                    geometry=calibration.geometry,
                )
            )
            observation_quality_state = _contact_manifold_quality_state(
                measurement_footholds, root_pose_gauge_diagnostic
            )
            support_confidence = max(
                evidence.confidence if evidence.contact else 0.0
                for evidence in latest_contact_evidence.values()
            )
            contact_influence_multiplier = (
                max(0.10, 1.0 - 0.90 * support_confidence)
                if ankle_contact else 1.0
            )
            observation, decision = _add_position_payload_once(
                payload,
                fused_filter=fused_filter,
                processing_time_s=availability_time,
                influence_multiplier=contact_influence_multiplier,
                quality_state=observation_quality_state,
            )
            fused_filter.advance_to_availability(availability_time)
            filter_root_after_uwb = (
                fused_filter.current_state.position_m.copy()
            )
            pose_install_accepted = bool(
                causal_pose is not None
                and decision.accepted
                and payload["articulated_accepted"]
            )
            reconcile_diagnostic = None
            if ankle_contact:
                offsets_before_install, velocity_before_install, _sample_before = (
                    ankle_pose_at(availability_time)
                )
                if pose_install_accepted:
                    correction = {
                        segment: np.asarray(
                            payload["segment_correction_rotvec"], dtype=float
                        )[segment_index]
                        for segment_index, segment in enumerate(
                            ARTICULATED_SEGMENTS
                        )
                    }
                    causal_pose.install(
                        correction,
                        measurement_time_s=payload["measurement_time_s"],
                        availability_time_s=availability_time,
                    )
                    transition = causal_pose.transition_snapshot()
                    pose_install_events.append({
                        "measurement_time_s": float(
                            payload["measurement_time_s"]
                        ),
                        "availability_time_s": float(availability_time),
                        "transition_period_s": float(
                            transition["period_s"]
                        ),
                        "target_delta_maximum_rad": float(
                            transition["target_delta_maximum_rad"]
                        ),
                    })
                offsets, offset_velocity, pose_sample_after = ankle_pose_at(
                    availability_time
                )
                if causal_pose is not None:
                    root_before_pose_reconcile = (
                        fused_filter.current_state.position_m.copy()
                    )
                    reconciled, reconcile = (
                        foothold_corrector.reconcile_pose_change(
                            fused_filter.current_state,
                            evidence=latest_contact_evidence,
                            previous_ankle_offset_world_m=(
                                offsets_before_install
                            ),
                            previous_ankle_offset_velocity_world_mps=(
                                velocity_before_install
                            ),
                            ankle_offset_world_m=offsets,
                            ankle_offset_velocity_world_mps=offset_velocity,
                        )
                    )
                    if reconcile.accepted:
                        fused_filter.apply_current_constraint(
                            reconciled,
                            operator=reconcile.replay_operator,
                            owner="UWB_AVAILABILITY_POSE_REGAUGE",
                        )
                constrained, post_uwb_contact = foothold_corrector.update(
                    fused_filter.current_state,
                    evidence=latest_contact_evidence,
                    ankle_offset_world_m=offsets,
                    ankle_offset_velocity_world_mps=offset_velocity,
                )
                if post_uwb_contact.accepted:
                    fused_filter.apply_current_constraint(
                        constrained,
                        operator=post_uwb_contact.replay_operator,
                        owner="UWB_AVAILABILITY_FOOTHOLD_SOFT_UPDATE",
                    )
                    post_uwb_contact_reapplications += 1
                if causal_pose is not None:
                    reconcile_diagnostic = {
                        "event": "UWB_AVAILABILITY_POSE_TARGET",
                        "measurement_time_s": payload["measurement_time_s"],
                        "availability_time_s": availability_time,
                        "active_sides": list(reconcile.active_sides),
                        "constrained_sides": list(reconcile.constrained_sides),
                        "primary_side": reconcile.primary_side,
                        "pre_xy_residual_m": dict(reconcile.pre_xy_residual_m),
                        "post_xy_residual_m": dict(reconcile.post_xy_residual_m),
                        "applied_position_delta_m": (
                            reconcile.applied_position_delta_m.tolist()
                        ),
                        "applied_velocity_delta_mps": (
                            reconcile.applied_velocity_delta_mps.tolist()
                        ),
                        "root_position_before_reconcile_m": (
                            root_before_pose_reconcile.tolist()
                        ),
                        "foothold_manifold_root_position_m": (
                            reconciled.position_m.tolist()
                        ),
                        "soft_check_applied_xy_delta_m": float(np.linalg.norm(
                            post_uwb_contact.applied_position_delta_m[:2]
                        )),
                        "velocity_baseline_reset": bool(
                            pose_sample_after.velocity_baseline_reset
                        ),
                    }
                    if reconcile.accepted:
                        owner_audit = _contact_reconcile_step_audit(
                            reconcile,
                            post_uwb_contact,
                            primary_side_after_update=(
                                foothold_corrector.primary_side
                            ),
                            soft_xy_delta_m=reconcile_diagnostic[
                                "soft_check_applied_xy_delta_m"
                            ],
                            soft_velocity_delta_mps=float(np.linalg.norm(
                                post_uwb_contact.applied_velocity_delta_mps[:2]
                            )),
                            maximum_position_step_m=(
                                foothold_corrector.config.maximum_position_step_m
                            ),
                            maximum_velocity_step_mps=(
                                foothold_corrector.config.maximum_velocity_step_mps
                            ),
                        )
                        reconcile_diagnostic.update(owner_audit)
                        pose_reconcile_events.append(reconcile_diagnostic)
                        if not owner_audit["pass"]:
                            failure = {
                                "reason": owner_audit["reason"],
                                "event": reconcile_diagnostic,
                                "latest_contact_evidence": {
                                    side: {
                                        "time_s": row.time_s,
                                        "confidence": row.confidence,
                                        "contact": row.contact,
                                        "reason": row.reason,
                                    }
                                    for side, row in (
                                        latest_contact_evidence.items()
                                    )
                                },
                                "uwb_payload": {
                                    "measurement_time_s": payload[
                                        "measurement_time_s"
                                    ],
                                    "availability_time_s": availability_time,
                                    "articulated_accepted": payload[
                                        "articulated_accepted"
                                    ],
                                    "contact_aware_articulated_accepted": (
                                        payload.get(
                                            "contact_aware_articulated_accepted",
                                            False,
                                        )
                                    ),
                                },
                            }
                            (
                                output / "RUNTIME_CONTACT_ORDER_FAILURE.json"
                            ).write_text(
                                json.dumps(_json_ready(failure), indent=2)
                                + "\n",
                                encoding="utf-8",
                            )
                            raise RuntimeError(
                                "UWB foothold ownership/idempotency check "
                                f"failed: {owner_audit['reason']}"
                            )
            decisions.append({
                "measurement_time_s": payload["measurement_time_s"],
                "availability_time_s": availability_time,
                "accepted": decision.accepted,
                "reason": decision.reason,
                "nis": decision.nis,
                "innovation_m": decision.innovation_m.tolist(),
                "applied_position_delta_m": decision.applied_position_delta_m.tolist(),
                "influence_scale": decision.influence_scale,
                "availability_applied_position_delta_m": (
                    decision.availability_applied_position_delta_m.tolist()
                ),
                "availability_applied_velocity_delta_mps": (
                    decision.availability_applied_velocity_delta_mps.tolist()
                ),
                "availability_influence_scale": (
                    decision.availability_influence_scale
                ),
                "measurement_residual_before_m": float(
                    np.linalg.norm(decision.innovation_m)
                ),
                "measurement_residual_after_m": float(np.linalg.norm(
                    decision.innovation_m
                    - decision.applied_position_delta_m
                )),
                "observation_root_position_m": (
                    np.asarray(payload["position_m"], dtype=float).tolist()
                ),
                "observation_covariance_m2": (
                    np.asarray(payload["covariance_m2"], dtype=float).tolist()
                ),
                "filter_root_after_uwb_before_contact_m": (
                    filter_root_after_uwb.tolist()
                ),
                "contact_influence_multiplier": contact_influence_multiplier,
                "articulated_pose_installed": pose_install_accepted,
                "measurement_time_foothold_sides": sorted(
                    measurement_footholds
                ),
                "measurement_time_identity_foothold_sides": sorted(
                    measurement_identity_footholds
                ),
                "measurement_support_class": measurement_support_class,
                "contact_aware_articulated": _json_ready(
                    contact_aware_diagnostic
                ),
                "root_pose_gauge": _json_ready(
                    root_pose_gauge_diagnostic
                ),
                "pose_reconcile": reconcile_diagnostic,
            })

    if len(output_time) < 100 or not decisions:
        raise RuntimeError("fusion produced insufficient output")
    times = np.asarray(output_time) - bootstrap_time
    fused = np.asarray(fused_position)
    inertial = np.asarray(imu_position)
    uwb_position = np.asarray([row["position_m"] for row in observations])
    uwb_time = np.asarray([row["measurement_time_s"] for row in observations]) - bootstrap_time
    uwb_covariance = np.asarray([row["covariance_m2"] for row in observations])
    lower = np.min(anchors, axis=0) - 0.75
    upper = np.max(anchors, axis=0) + 0.75
    fused_outside = np.any((fused < lower) | (fused > upper), axis=1)
    imu_outside = np.any((inertial < lower) | (inertial > upper), axis=1)
    accepted = [row for row in decisions if row["accepted"]]
    accepted_step_norm = np.asarray([
        np.linalg.norm(row["applied_position_delta_m"]) for row in accepted
    ])
    accepted_availability_step_norm = np.asarray([
        np.linalg.norm(row["availability_applied_position_delta_m"])
        for row in accepted
    ])
    accepted_availability_velocity_delta_norm = np.asarray([
        np.linalg.norm(row["availability_applied_velocity_delta_mps"])
        for row in accepted
    ])
    delayed_uwb_influence_gate = {
        "owner": "CausalDelayedRootFilter_EFFECTIVE_GAIN_BEFORE_COMMIT",
        "position_influence_limit_m": config.maximum_position_influence_m,
        "measurement_time_position_delta_maximum_m": (
            float(np.max(accepted_step_norm)) if accepted else 0.0
        ),
        "availability_time_position_delta_maximum_m": (
            float(np.max(accepted_availability_step_norm))
            if accepted else 0.0
        ),
        "availability_time_velocity_delta_maximum_mps": (
            float(np.max(accepted_availability_velocity_delta_norm))
            if accepted else 0.0
        ),
        "availability_time_velocity_threshold": None,
        "availability_gain_rescaled_count": sum(
            row["availability_influence_scale"] < 1.0 - 1e-12
            for row in accepted
        ),
        "availability_gain_scale_minimum": (
            float(min(row["availability_influence_scale"] for row in accepted))
            if accepted else None
        ),
        "measurement_residual_median_before_m": (
            float(np.median([
                row["measurement_residual_before_m"] for row in accepted
            ])) if accepted else None
        ),
        "measurement_residual_median_after_m": (
            float(np.median([
                row["measurement_residual_after_m"] for row in accepted
            ])) if accepted else None
        ),
        "mean_covariance_update": (
            "SAME_FINAL_SCALED_GAIN_WITH_JOSEPH_COVARIANCE"
        ),
    }
    delayed_uwb_influence_gate["pass_by_subgate"] = {
        "measurement_time_position": (
            not accepted
            or float(np.max(accepted_step_norm))
            <= config.maximum_position_influence_m + 1e-10
        ),
        "availability_time_position": (
            not accepted
            or float(np.max(accepted_availability_step_norm))
            <= config.maximum_position_influence_m + 1e-10
        ),
    }
    delayed_uwb_influence_gate["pass"] = all(
        delayed_uwb_influence_gate["pass_by_subgate"].values()
    )
    if not delayed_uwb_influence_gate["pass"]:
        (output / "DELAYED_UWB_INFLUENCE_FAILURE.json").write_text(
            json.dumps(_json_ready({
                "gate": delayed_uwb_influence_gate,
                "uwb_decisions": decisions,
            }), indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "delayed UWB availability-time influence gate failed"
        )
    fused_velocity_array = np.asarray(fused_velocity)
    fused_bias_array = np.asarray(fused_bias)
    contact_confidence_array = np.asarray(contact_confidence, dtype=float)
    contact_active_array = np.asarray(contact_active, dtype=bool)
    contact_confirmed_array = np.asarray(contact_confirmed, dtype=bool)
    contact_prior_held_array = np.asarray(contact_prior_held, dtype=bool)
    contact_prior_held_support_confidence_array = np.asarray(
        contact_prior_held_support_confidence, dtype=float
    )
    contact_replay_operator_confidence_array = np.asarray(
        contact_replay_operator_confidence, dtype=float
    )
    contact_support_state_array = np.asarray(contact_support_state)
    contact_constrained_array = np.asarray(contact_constrained, dtype=bool)
    contact_ankle_world_array = np.asarray(contact_ankle_world, dtype=float)
    contact_position_delta_array = np.asarray(contact_position_delta, dtype=float)
    contact_velocity_delta_array = np.asarray(contact_velocity_delta, dtype=float)
    contact_foothold_world_array = np.asarray(
        contact_foothold_world, dtype=float
    )
    contact_foothold_xy_residual_array = np.asarray(
        contact_foothold_xy_residual, dtype=float
    )
    articulated_correction_high_rate_array = np.asarray(
        articulated_correction_high_rate, dtype=float
    )
    articulated_projection_fk_residual_array = np.asarray(
        articulated_projection_fk_residual_deg, dtype=float
    )
    articulated_projection_inside_rom_array = np.asarray(
        articulated_projection_inside_rom, dtype=bool
    )
    articulated_velocity_baseline_reset_array = np.asarray(
        articulated_velocity_baseline_reset, dtype=bool
    )
    articulated_transition_step_array = np.asarray(
        articulated_transition_step_rad, dtype=float
    )
    articulated_transition_active_array = np.asarray(
        articulated_transition_active, dtype=bool
    )
    pose_reconcile_root_delta_array = np.asarray(
        pose_reconcile_root_delta, dtype=float
    )
    own_world_height = np.asarray([
        row["swing_height_m"]
        for row in contact_detector_cues
        if row["swing_height_owner"] == "OWNED_FOOTHOLD_WORLD_Z"
        and math.isfinite(row["swing_height_m"])
    ], dtype=float)
    root_owner_age = np.asarray([
        row["root_owner_age_s"]
        for row in contact_detector_cues
        if math.isfinite(row["root_owner_age_s"])
    ], dtype=float)
    activity_prior_conflict_rows = [
        row for row in contact_detector_cues
        if row["activity_prior_conflict"]
    ]
    positive_lift_prior_rows = [
        row for row in activity_prior_conflict_rows
        if row["positive_swing"] and row["prior_held"]
    ]
    foothold_history = (
        foothold_corrector.foothold_ownership_history()
        if foothold_corrector is not None else ()
    )
    contact_detector_audit = {
        "classification_owner": (
            "RAW_ANKLE_IMU_PLUS_NATIVE200_ANALYTIC_BASE_FK"
            if ankle_contact else "DISABLED"
        ),
        "uwb_articulated_correction_used_for_classification": False,
        "stationary_no_flight_prior": bool(stationary_no_flight_prior),
        "stationary_no_flight_prior_scope": (
            "H02_GOLF_ONLY" if stationary_no_flight_prior else "DISABLED"
        ),
        "support_state_sample_count": {
            side: dict(Counter(contact_support_state_array[:, index]))
            for index, side in enumerate(("left", "right"))
        },
        "transition_count": len(contact_events),
        "transition_histogram": dict(Counter(
            f"{row['side']}:{row['from']}->{row['to']}"
            for row in contact_events
        )),
        "positive_swing_sample_count": int(sum(
            bool(row["positive_swing"]) for row in contact_detector_cues
        )),
        "positive_swing_release_count": int(sum(
            row["to"] == FootSupportState.SWING_CONFIRMED.value
            for row in contact_events
        )),
        "activity_prior_conflict_sample_count": len(
            activity_prior_conflict_rows
        ),
        "activity_prior_conflict_classification": dict(Counter(
            row["positive_lift_classification"]
            for row in activity_prior_conflict_rows
        )),
        "observed_positive_lift_but_prior_held_sample_count": len(
            positive_lift_prior_rows
        ),
        "observed_positive_lift_but_prior_held": [
            {
                "time_s": row["time_s"],
                "side": row["side"],
                "classification": row["positive_lift_classification"],
                "height_m": row["swing_height_m"],
                "height_owner": row["swing_height_owner"],
                "observed_lift_m": row["observed_lift_m"],
            }
            for row in positive_lift_prior_rows
        ],
        "activity_prior_conflict_confirmed_sample_count": int(sum(
            row["support_state"]
            == FootSupportState.STANCE_CONFIRMED.value
            for row in activity_prior_conflict_rows
        )),
        "foothold_release_count": int(sum(
            owner.release_time_s is not None for owner in foothold_history
        )),
        "foothold_reanchor_count": int(sum(
            max(0, count - 1)
            for count in Counter(
                owner.side for owner in foothold_history
            ).values()
        )),
        "positive_lift_observation_height_m": {
            side: {
                "minimum": float(min(values)),
                "maximum": float(max(values)),
            } if values else {"minimum": None, "maximum": None}
            for side in ("left", "right")
            for values in [[
                float(row["swing_height_m"])
                for row in positive_lift_prior_rows
                if row["side"] == side
                and math.isfinite(row["swing_height_m"])
            ]]
        },
        "prior_held_fraction": {
            side: float(np.mean(contact_prior_held_array[:, index]))
            for index, side in enumerate(("left", "right"))
        },
        "own_foothold_world_z_lift_m": {
            "minimum": (
                float(np.min(own_world_height)) if len(own_world_height) else None
            ),
            "maximum": (
                float(np.max(own_world_height)) if len(own_world_height) else None
            ),
        },
        "root_owner_age_s": {
            "maximum": (
                float(np.max(root_owner_age)) if len(root_owner_age) else None
            ),
            "limit": float(contact_root_owner_maximum_age_s),
        },
    }
    prior_held_uwb_gate = _prior_held_uwb_gate(
        times,
        contact_prior_held_array,
        decisions,
        bootstrap_time_s=bootstrap_time,
        dropout_s=RootFilterConfig().uwb_dropout_s,
        required=bool(stationary_no_flight_prior),
    )
    contact_articulated = {
        "architecture": (
            "CAUSAL_MEASUREMENT_TIME_FOOTHOLD_RAW_RANGE_IK_THEN_"
            "POSE_CHANGE_ROOT_FOOTHOLD_RECLOSURE"
        ),
        "measurement_time_contact_aware_events": (
            contact_aware_articulated_events
        ),
        "measurement_time_contact_aware_attempted": int(sum(
            bool(row.get("attempted"))
            for row in contact_aware_articulated_events
        )),
        "measurement_time_contact_aware_accepted": int(sum(
            bool(row.get("accepted"))
            for row in contact_aware_articulated_events
        )),
        "pose_installs": int(sum(
            row["articulated_pose_installed"] for row in decisions
        )),
        "reconciliations": len(pose_reconcile_events),
        "reconcile_events": pose_reconcile_events,
        "maximum_post_reconcile_xy_residual_m": float(max(
            (
                residual
                for event in pose_reconcile_events
                for residual in event["post_xy_residual_m"].values()
            ),
            default=0.0,
        )),
        "maximum_ordinary_contact_xy_delta_m": float(max(
            (
                event["ordinary_contact_applied_xy_delta_m"]
                for event in pose_reconcile_events
            ),
            default=0.0,
        )),
        "maximum_ownership_transition_soft_xy_delta_m": float(max(
            (
                event["ordinary_contact_applied_xy_delta_m"]
                for event in pose_reconcile_events
                if event.get("ownership_transition", False)
            ),
            default=0.0,
        )),
        "residual_axes": ["WORLD_X", "WORLD_Y"],
    }

    def runtime_contact_diagnostic() -> dict[str, Any]:
        return {
            "contact_aware_articulated_events": (
                contact_aware_articulated_events
            ),
            "foothold_ownership_history": [
                {
                    "side": owner.side,
                    "start_time_s": owner.start_time_s,
                    "release_time_s": owner.release_time_s,
                    "world_point_m": owner.world_point_m,
                }
                for owner in (
                    foothold_corrector.foothold_ownership_history()
                    if foothold_corrector is not None else ()
                )
            ],
            "contact_events": contact_events,
            "contact_detector": contact_detector_audit,
            "prior_held_uwb_gate": prior_held_uwb_gate,
            "causal_pose_reconciliation": contact_articulated,
        }
    stationary_prior_gate = {
        "enabled": bool(stationary_no_flight_prior),
        "policy": "H02_STATIONARY_NO_FLIGHT_ACTIVITY_PRIOR_CONFLICT",
        "positive_swing_release_count": contact_detector_audit[
            "positive_swing_release_count"
        ],
        "foothold_release_count": contact_detector_audit[
            "foothold_release_count"
        ],
        "foothold_reanchor_count": contact_detector_audit[
            "foothold_reanchor_count"
        ],
        "activity_prior_conflict_confirmed_sample_count": (
            contact_detector_audit[
                "activity_prior_conflict_confirmed_sample_count"
            ]
        ),
        "prior_held_uwb": prior_held_uwb_gate,
    }
    stationary_prior_gate["pass"] = bool(
        not stationary_no_flight_prior
        or (
            stationary_prior_gate["positive_swing_release_count"] == 0
            and stationary_prior_gate["foothold_release_count"] == 0
            and stationary_prior_gate["foothold_reanchor_count"] == 0
            and stationary_prior_gate[
                "activity_prior_conflict_confirmed_sample_count"
            ] == 0
            and prior_held_uwb_gate["pass"]
        )
    )
    stationary_prior_gate["reason"] = (
        "PASS" if stationary_prior_gate["pass"] else (
            "ACTIVITY_PRIOR_IDENTITY_OWNERSHIP_FAILURE"
            if any((
                stationary_prior_gate["positive_swing_release_count"] > 0,
                stationary_prior_gate["foothold_release_count"] > 0,
                stationary_prior_gate["foothold_reanchor_count"] > 0,
                stationary_prior_gate[
                    "activity_prior_conflict_confirmed_sample_count"
                ] > 0,
            )) else prior_held_uwb_gate["reason"]
        )
    )
    if not stationary_prior_gate["pass"]:
        (output / "STATIONARY_PRIOR_FAILURE.json").write_text(
            json.dumps(_json_ready({
                "gate": stationary_prior_gate,
                **runtime_contact_diagnostic(),
            }), indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "H02 stationary no-flight prior gate failed: "
            f"{stationary_prior_gate['reason']}"
        )
    continuity_gate: dict[str, Any] = {
        "enabled": False,
        "pass": True,
    }
    continuity_arrays: dict[str, np.ndarray] = {}
    if causal_pose is not None:
        correction_cap = (
            ArticulatedRangeConfig().maximum_segment_correction_rad
            * np.sqrt(3.0) + 1e-10
        )
        continuity_metrics, continuity_arrays = _native200_pose_continuity(
            absolute_time_s=np.asarray(output_time, dtype=float),
            root_position_world_m=fused,
            correction_rotvec=articulated_correction_high_rate_array,
            rotations_at_fraction=rotations,
            action_start_s=action_start_s,
            action_stop_s=action_stop_s,
            transition_period_s=causal_pose.transition_period_s,
            correction_cap_rad=correction_cap,
        )
        maximum_reconcile_root_xy_delta = float(max(
            (
                np.linalg.norm(
                    np.asarray(event["applied_position_delta_m"], float)[:2]
                )
                for event in pose_reconcile_events
            ),
            default=0.0,
        ))
        maximum_soft_xy_delta = float(max(
            (
                event.get("ordinary_contact_applied_xy_delta_m", 0.0)
                for event in pose_reconcile_events
            ),
            default=0.0,
        ))
        maximum_soft_velocity_delta = float(max(
            (
                event.get(
                    "ordinary_contact_applied_velocity_delta_mps", 0.0
                )
                for event in pose_reconcile_events
            ),
            default=0.0,
        ))
        continuity_gate = {
            "enabled": True,
            "owner": "FINAL_EMITTED_NATIVE200_POSE",
            "causal_transition": {
                "period_s": causal_pose.transition_period_s,
                "basis": "ONE_DOCUMENTED_8P33HZ_UWB_PERIOD",
                "future_target_interpolation": False,
                "transition_step_maximum_rad": float(np.max(
                    articulated_transition_step_array
                )),
            },
            **continuity_metrics,
            "pose_reconcile_root_xy_delta_maximum_m": (
                maximum_reconcile_root_xy_delta
            ),
            "ordinary_contact_xy_delta_maximum_m": maximum_soft_xy_delta,
            "ordinary_contact_xy_delta_limit_m": (
                foothold_corrector.config.maximum_position_step_m
            ),
            "ordinary_contact_velocity_delta_maximum_mps": (
                maximum_soft_velocity_delta
            ),
            "ordinary_contact_velocity_delta_limit_mps": (
                foothold_corrector.config.maximum_velocity_step_mps
            ),
            "root_reference_limit_m": H02_ROOT_XY_STEP_REFERENCE_M,
        }
        continuity_gate["pass_by_subgate"] = {
            "root_xy_maximum": (
                action != "H02_golf"
                or continuity_metrics["root_xy_step_m"]["maximum"]
                <= H02_ROOT_XY_STEP_REFERENCE_M["maximum"] + 1e-9
            ),
            "root_xy_p99": (
                action != "H02_golf"
                or continuity_metrics["root_xy_step_m"]["p99"]
                <= H02_ROOT_XY_STEP_REFERENCE_M["p99"] + 1e-9
            ),
            "reconcile_root_xy": (
                action != "H02_golf"
                or maximum_reconcile_root_xy_delta
                <= H02_ROOT_XY_STEP_REFERENCE_M["maximum"] + 1e-9
            ),
            "segment_transition_triangle": (
                continuity_metrics[
                    "transition_triangle_excess_maximum_rad"
                ] <= continuity_metrics[
                    "transition_triangle_tolerance_rad"
                ]
            ),
            "ordinary_contact_position_step": (
                maximum_soft_xy_delta
                <= foothold_corrector.config.maximum_position_step_m + 1e-12
            ),
            "ordinary_contact_velocity_step": (
                maximum_soft_velocity_delta
                <= foothold_corrector.config.maximum_velocity_step_mps + 1e-12
            ),
        }
        continuity_gate["pass"] = all(
            continuity_gate["pass_by_subgate"].values()
        )
        if not continuity_gate["pass"]:
            np.savez_compressed(
                output / "CONTINUITY_FAILURE.npz",
                time_s=times,
                fused_root_position_world_m=fused,
                articulated_segment_order=np.asarray(ARTICULATED_SEGMENTS),
                articulated_segment_correction_high_rate_rotvec=(
                    articulated_correction_high_rate_array
                ),
                contact_prior_held_support_confidence=(
                    contact_prior_held_support_confidence_array
                ),
                contact_replay_operator_confidence=(
                    contact_replay_operator_confidence_array
                ),
                **continuity_arrays,
            )
            (output / "CONTINUITY_FAILURE.json").write_text(
                json.dumps(_json_ready({
                    "gate": continuity_gate,
                    "uwb_decisions": decisions,
                    **runtime_contact_diagnostic(),
                }), indent=2) + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                "final native200 pose continuity gate failed: "
                + json.dumps(
                    continuity_gate["pass_by_subgate"], sort_keys=True
                )
            )
    high_rate_pose_gate = {
        "enabled": causal_pose is not None,
        "sample_count": int(len(articulated_correction_high_rate_array)),
        "all_samples_inside_rom": bool(np.all(
            articulated_projection_inside_rom_array
        )),
        "fk_direction_residual_maximum_deg": float(np.max(
            articulated_projection_fk_residual_array
        )),
        "fk_direction_residual_limit_deg": 3e-6,
        "segment_correction_maximum_rad": float(np.max(np.linalg.norm(
            articulated_correction_high_rate_array, axis=2
        ))),
        "segment_correction_limit_rad": (
            ArticulatedRangeConfig().maximum_segment_correction_rad
            * np.sqrt(3.0) + 1e-10
        ),
        "velocity_baseline_reset_count": int(np.sum(
            articulated_velocity_baseline_reset_array
        )),
    }
    high_rate_pose_gate["pass"] = bool(
        high_rate_pose_gate["all_samples_inside_rom"]
        and high_rate_pose_gate["fk_direction_residual_maximum_deg"]
        <= high_rate_pose_gate["fk_direction_residual_limit_deg"]
        and high_rate_pose_gate["segment_correction_maximum_rad"]
        <= high_rate_pose_gate["segment_correction_limit_rad"]
    )
    if causal_pose is not None and not high_rate_pose_gate["pass"]:
        (output / "HIGH_RATE_POSE_FAILURE.json").write_text(
            json.dumps(_json_ready({
                "gate": high_rate_pose_gate,
                "continuity_gate": continuity_gate,
                **runtime_contact_diagnostic(),
            }), indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError("causal high-rate pose ROM/FK gate failed")

    contact_final_pose_gate: dict[str, Any] = {
        "enabled": False,
        "pass": True,
    }
    if action == "H02_golf" and causal_pose is not None:
        reference_masks, reference_mask_owner = _h02_contact_reference_masks(
            H02_CONTACT_REFERENCE_NPZ, times
        )
        masks = {
            "new_active": contact_active_array,
            "new_confirmed": contact_confirmed_array,
            "new_prior_held": contact_prior_held_array,
            "new_constrained": contact_constrained_array,
            "reference_active": reference_masks["active"],
            "reference_constrained": reference_masks["constrained"],
            "union_active": (
                contact_active_array | reference_masks["active"]
            ),
            "union_constrained": (
                contact_constrained_array | reference_masks["constrained"]
            ),
        }
        metrics = {
            name: _contact_xy_episode_metrics(
                times, contact_ankle_world_array, mask
            )
            for name, mask in masks.items()
        }
        hard_mask_names = (
            "new_constrained",
            "new_prior_held",
            "reference_constrained",
            "union_constrained",
        )
        hard_values = {
            side: max(
                metrics[name][side]["maximum_relative_start_xy_m"]
                for name in hard_mask_names
            )
            for side in ("left", "right")
        }
        contact_final_pose_gate = {
            "enabled": True,
            "owner": "FINAL_EMITTED_NATIVE200_POSE",
            "reference_mask_owner": reference_mask_owner,
            "metric_definition": (
                "MAXIMUM_RELATIVE_EPISODE_START_XY_ON_FINAL_DISPLAY_POSE"
            ),
            "reported_masks": list(masks),
            "hard_masks": list(hard_mask_names),
            "metrics": metrics,
            "overall_hard_value_m": hard_values,
            "limit_m": H02_CONTACT_HORIZONTAL_LIMIT_M,
            "pass_by_side": {
                side: hard_values[side]
                <= H02_CONTACT_HORIZONTAL_LIMIT_M[side] + 1e-9
                for side in ("left", "right")
            },
        }
        contact_final_pose_gate["pass"] = all(
            contact_final_pose_gate["pass_by_side"].values()
        )
        if not contact_final_pose_gate["pass"]:
            np.savez_compressed(
                output / "CONTACT_COHERENCE_FAILURE.npz",
                time_s=times,
                fused_root_position_world_m=fused,
                contact_ankle_proxy_world_m=contact_ankle_world_array,
                contact_active=contact_active_array,
                contact_confirmed=contact_confirmed_array,
                contact_prior_held=contact_prior_held_array,
                contact_prior_held_support_confidence=(
                    contact_prior_held_support_confidence_array
                ),
                contact_replay_operator_confidence=(
                    contact_replay_operator_confidence_array
                ),
                contact_support_state=contact_support_state_array,
                contact_constrained=contact_constrained_array,
                reference_contact_active=reference_masks["active"],
                reference_contact_constrained=reference_masks["constrained"],
                articulated_segment_order=np.asarray(ARTICULATED_SEGMENTS),
                articulated_segment_correction_high_rate_rotvec=(
                    articulated_correction_high_rate_array
                ),
            )
            (output / "CONTACT_COHERENCE_FAILURE.json").write_text(
                json.dumps(
                    _json_ready({
                        "gate": contact_final_pose_gate,
                        "continuity_gate": continuity_gate,
                        "native200_pose_gate": high_rate_pose_gate,
                        **runtime_contact_diagnostic(),
                    }), indent=2
                ) + "\n",
                encoding="utf-8",
            )
            raise RuntimeError(
                "final emitted H02 foothold coherence gate failed: "
                + json.dumps(hard_values, sort_keys=True)
            )
    hinge_names: tuple[str, ...] = ()
    hinge_coordinate_rows = np.empty((len(observations), 0), dtype=float)
    hinge_inside_rows = np.empty((len(observations), 0), dtype=bool)
    hinge_projection_audit: dict[str, Any] = {
        "enabled": False,
        "trajectory_owner": None,
    }
    if hinge_projector is not None:
        hinge_names = tuple(hinge_model)
        coordinate_rows = []
        inside_rows = []
        idempotency_rad = []
        final_residual_deg = []
        proposed_below = 0
        proposed_above = 0
        projected_update_count = 0
        final_row_audit = []
        for row_index, row in enumerate(observations):
            before = {
                segment: np.asarray(row["segment_correction_rotvec"])[index]
                for index, segment in enumerate(ARTICULATED_SEGMENTS)
            }
            after, final_metrics = hinge_projector(
                rotations(row["_fraction"]), before
            )
            row["segment_correction_rotvec"] = np.stack([
                after[segment] for segment in ARTICULATED_SEGMENTS
            ])
            segment_idempotency = {
                segment: float((
                    Rotation.from_rotvec(before[segment]).inv()
                    * Rotation.from_rotvec(after[segment])
                ).magnitude())
                for segment in ARTICULATED_SEGMENTS
            }
            idempotency_rad.append(max(segment_idempotency.values()))
            joint_rows = final_metrics["joint"]
            coordinate_rows.append([
                joint_rows[name]["post_projection_signed_deg"]
                for name in hinge_names
            ])
            inside_rows.append([
                joint_rows[name]["post_projection_inside_rom"]
                for name in hinge_names
            ])
            final_residual_deg.append(
                final_metrics["fk_direction_residual_maximum_deg"]
            )
            final_row_audit.append({
                "row_index": row_index,
                "fraction": float(row["_fraction"]),
                "outside_rom_joints": [
                    name for name in hinge_names
                    if not joint_rows[name]["post_projection_inside_rom"]
                ],
                "fk_direction_residual_maximum_deg": final_metrics[
                    "fk_direction_residual_maximum_deg"
                ],
                "idempotency_maximum_rad": max(segment_idempotency.values()),
                "idempotency_maximum_segment": max(
                    segment_idempotency, key=segment_idempotency.get
                ),
                "segment_correction_maximum_rad": float(max(
                    np.linalg.norm(after[segment])
                    for segment in ARTICULATED_SEGMENTS
                )),
                "segment_correction_maximum_segment": max(
                    ARTICULATED_SEGMENTS,
                    key=lambda segment: float(np.linalg.norm(after[segment])),
                ),
            })
            proposal = row.get("hinge_projection", {})
            if proposal:
                projected_update_count += 1
                proposed_below += int(
                    proposal["pre_projection_below_rom_count"]
                )
                proposed_above += int(
                    proposal["pre_projection_above_rom_count"]
                )
        hinge_coordinate_rows = np.asarray(coordinate_rows, dtype=float)
        hinge_inside_rows = np.asarray(inside_rows, dtype=bool)
        maximum_idempotency = float(max(idempotency_rad))
        maximum_fk_residual = float(max(final_residual_deg))
        maximum_correction = float(max(
            np.linalg.norm(row["segment_correction_rotvec"], axis=1).max()
            for row in observations
        ))
        all_inside = bool(np.all(hinge_inside_rows))
        correction_cap = (
            ArticulatedRangeConfig().maximum_segment_correction_rad
            * np.sqrt(3.0) + 1e-10
        )
        final_gate = {
            "all_frames_inside_rom": all_inside,
            "fk_direction_residual_maximum_deg": maximum_fk_residual,
            "fk_direction_residual_limit_deg": 3e-6,
            "fk_direction_residual_pass": maximum_fk_residual <= 3e-6,
            "projection_idempotency_maximum_rad": maximum_idempotency,
            "projection_idempotency_limit_rad": 1e-6,
            "projection_idempotency_pass": maximum_idempotency <= 1e-6,
            "segment_correction_maximum_rad": maximum_correction,
            "segment_correction_limit_rad": correction_cap,
            "segment_correction_pass": maximum_correction <= correction_cap,
        }
        if not all(bool(final_gate[name]) for name in (
            "all_frames_inside_rom",
            "fk_direction_residual_pass",
            "projection_idempotency_pass",
            "segment_correction_pass",
        )):
            _write_final_hinge_projection_failure(
                output,
                action=action,
                trajectory_owner=analytic_owner["trajectory_owner"],
                observation_count=len(observations),
                projected_update_count=projected_update_count,
                final_gate=final_gate,
                final_row_audit=final_row_audit,
                wall_s_at_failure=time.perf_counter() - started,
            )
            raise RuntimeError(
                "final articulated hinge/ROM projection gate failed: "
                + json.dumps(final_gate, sort_keys=True)
            )
        hinge_projection_audit = {
            "enabled": True,
            "owner": (
                "biospur_fusion.c2_articulated_biomechanics."
                "project_hinge_corrections"
            ),
            "trajectory_owner": analytic_owner["trajectory_owner"],
            "hinge_names": list(hinge_names),
            "projected_update_count": projected_update_count,
            "proposed_below_rom_count": proposed_below,
            "proposed_above_rom_count": proposed_above,
            "final_all_frames_inside_rom": all_inside,
            "final_fk_direction_residual_maximum_deg": maximum_fk_residual,
            "final_projection_idempotency_maximum_rad": maximum_idempotency,
            "final_segment_correction_maximum_rad": maximum_correction,
            "final_gate": final_gate,
        }
    mechanism_nondegeneracy_gate = _articulated_mechanism_nondegeneracy_gate(
        absolute_time_s=np.asarray(output_time, dtype=float),
        correction_high_rate_rotvec=articulated_correction_high_rate_array,
        transition_active=articulated_transition_active_array,
        pose_install_events=pose_install_events,
        decisions=decisions,
        uwb_dropout_s=RootFilterConfig().uwb_dropout_s,
        required=bool(
            action == "H02_golf"
            and maximum_duration_s is None
            and causal_pose is not None
        ),
    )
    if not mechanism_nondegeneracy_gate["pass"]:
        (output / "MECHANISM_NONDEGENERACY_FAILURE.json").write_text(
            json.dumps(_json_ready({
                "gate": mechanism_nondegeneracy_gate,
                "continuity_gate": continuity_gate,
                "native200_pose_gate": high_rate_pose_gate,
                "contact_final_pose_gate": contact_final_pose_gate,
                "uwb_decisions": decisions,
                **runtime_contact_diagnostic(),
            }), indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(
            "full-run fusion mechanism nondegeneracy gate failed: "
            + json.dumps(
                mechanism_nondegeneracy_gate["subgates"], sort_keys=True
            )
        )
    node_order = tuple(sorted(NODE_TO_PROXY_POINT))
    node_trust_score = np.full((len(observations), len(node_order)), np.nan)
    node_trusted = np.zeros((len(observations), len(node_order)), dtype=bool)
    for row_index, row in enumerate(observations):
        if row["node_trust"]:
            by_node = {item["node"]: item for item in row["node_trust"]}
            for node_index, node in enumerate(node_order):
                if node in by_node:
                    node_trust_score[row_index, node_index] = by_node[node]["score"]
                    node_trusted[row_index, node_index] = by_node[node]["trusted"]
        else:
            node_trusted[row_index] = [
                node in row["trusted_node_ids"] for node in node_order
            ]
    position_update_by_support: dict[str, dict[str, Any]] = {}
    for support_class in (
        "CONFIRMED", "PRIOR_HELD", "NO_CONTACT_CONSTRAINT",
        "UNKNOWN_MISSING_HISTORY",
    ):
        rows = [
            row for row in decisions
            if row.get("measurement_support_class") == support_class
        ]
        position_update_by_support[support_class] = {
            "attempted": len(rows),
            "accepted": sum(bool(row["accepted"]) for row in rows),
            "rejected": sum(not bool(row["accepted"]) for row in rows),
            "reason_histogram": dict(Counter(row["reason"] for row in rows)),
        }
    np.savez_compressed(
        output / f"{action}_SHARED_ROOT_IMU_FUSION.npz",
        time_s=times,
        fused_root_position_world_m=fused,
        fused_root_velocity_world_mps=fused_velocity_array,
        fused_accelerometer_bias_sensor_mps2=fused_bias_array,
        imu_only_root_position_world_m=inertial,
        imu_only_root_velocity_world_mps=np.asarray(imu_velocity),
        shared_root_time_s=uwb_time,
        shared_root_position_world_m=uwb_position,
        shared_root_covariance_m2=uwb_covariance,
        shared_root_used_for_filter=observation_update_mask,
        articulated_segment_order=np.asarray(ARTICULATED_SEGMENTS),
        articulated_segment_correction_rotvec=np.stack([
            row["segment_correction_rotvec"] for row in observations
        ]),
        articulated_segment_correction_high_rate_rotvec=(
            articulated_correction_high_rate_array
        ),
        articulated_projection_fk_residual_high_rate_deg=(
            articulated_projection_fk_residual_array
        ),
        articulated_projection_inside_rom_high_rate=(
            articulated_projection_inside_rom_array
        ),
        articulated_velocity_baseline_reset=(
            articulated_velocity_baseline_reset_array
        ),
        articulated_transition_step_maximum_rad=(
            articulated_transition_step_array
        ),
        articulated_transition_active=articulated_transition_active_array,
        pose_reconcile_root_delta_m=pose_reconcile_root_delta_array,
        articulated_hinge_name=np.asarray(hinge_names),
        articulated_hinge_coordinate_deg=hinge_coordinate_rows,
        articulated_hinge_inside_rom=hinge_inside_rows,
        adaptive_node_order=np.asarray(node_order),
        adaptive_available_node_count=np.asarray([
            row["available_nodes"] for row in observations
        ]),
        adaptive_trusted_node_count=np.asarray([
            row["trusted_nodes"] for row in observations
        ]),
        adaptive_mode=np.asarray([
            row["adaptive_mode"] for row in observations
        ]),
        adaptive_node_trust_score=node_trust_score,
        adaptive_node_trusted=node_trusted,
        anchors_world_m=anchors,
        expanded_volume_lower_m=lower,
        expanded_volume_upper_m=upper,
        contact_side_order=np.asarray(("left", "right")),
        contact_confidence=contact_confidence_array,
        contact_active=contact_active_array,
        contact_confirmed=contact_confirmed_array,
        contact_prior_held=contact_prior_held_array,
        contact_prior_held_support_confidence=(
            contact_prior_held_support_confidence_array
        ),
        contact_replay_operator_confidence=(
            contact_replay_operator_confidence_array
        ),
        contact_support_state=contact_support_state_array,
        contact_constrained=contact_constrained_array,
        contact_ankle_proxy_world_m=contact_ankle_world_array,
        contact_foothold_world_m=contact_foothold_world_array,
        contact_foothold_xy_residual_m=contact_foothold_xy_residual_array,
        contact_applied_position_delta_m=contact_position_delta_array,
        contact_applied_velocity_delta_mps=contact_velocity_delta_array,
        continuity_root_xy_step_m=continuity_arrays.get(
            "root_xy_step_m", np.empty(0)
        ),
        continuity_base_segment_so3_step_rad=continuity_arrays.get(
            "base_segment_so3_step_rad", np.empty((0, 0))
        ),
        continuity_final_segment_so3_step_rad=continuity_arrays.get(
            "final_segment_so3_step_rad", np.empty((0, 0))
        ),
    )
    result = {
        "schema": "biospur-c2-adaptive-node-imu-fusion-v4",
        "status": "C2_SHARED_ROOT_IMU_DIAGNOSTIC_COMPLETE",
        "scientific_pass": False,
        "action": action,
        "contact_support_policy": {
            "mode": (
                "H02_STATIONARY_NO_FLIGHT_ACTIVITY_PRIOR_CONFLICT"
                if stationary_no_flight_prior else "NO_ACTIVITY_PRIOR"
            ),
            "classification_source": (
                "RAW_ANKLE_IMU_PLUS_NATIVE200_ANALYTIC_BASE_FK"
                if ankle_contact else "DISABLED"
            ),
            "states": [state.value for state in FootSupportState],
            "uncertain_identity_retained": True,
            "uncertain_enters_uwb_contact_residual": False,
            "prior_held_enters_confirmed_mask": False,
            "gate": stationary_prior_gate,
            "scientific_pass": False,
        },
        "execution_scope": (
            "FULL_ACTION" if maximum_duration_s is None
            else "BOUNDED_DIAGNOSTIC_PREFIX"
        ),
        "maximum_duration_s": maximum_duration_s,
        "architecture": {
            "imu": "PELVIS_200HZ_VQF_ORIENTATION_PLUS_SPECIFIC_FORCE",
            "pose": (
                "NATIVE200_ANALYTIC_ORIENTATION_HINGE_IK"
                if analytic_owner is not None
                else "LEGACY_FROZEN_POSE_PROXY"
            ),
            "uwb": "ADAPTIVE_TRUSTED_X_OF_TEN_RAW_RANGE_ROOT_AT_8.333HZ",
            "uwb_policy": f"{range_policy.upper()}_PLUS_HUBER",
            "link_selection": link_selection,
            "node_selection": node_selection,
            "adaptive_node_modes": {
                "one": "ROOT_TRANSLATION_ONLY",
                "partial": "TRUSTED_KINEMATIC_PATHS_PLUS_FK_PROPAGATION",
                "ten": "FULL_ARTICULATED_CONSENSUS",
            },
            "body_update_requested": body_update,
            "body_update": effective_body_update,
            "static_pose_update_policy": (
                "DISABLE_ARTICULATED_CORRECTION;USE_FOR_ZERO_AND_RANGE_CALIBRATION"
                if action in STATIC_CALIBRATION_ACTIONS
                else "DYNAMIC_ARTICULATED_CORRECTION_ALLOWED"
            ),
            "articulated_update": (
                "ROOT_PLUS_TEN_SMALL_SEGMENT_ROTATIONS_WITH_FROZEN_FK"
                if effective_body_update == "articulated_consensus" else "DISABLED"
            ),
            "articulated_update_stride": articulated_stride,
            "valid_raw_ranges_per_tag": "FOUR_TO_EIGHT;MISSING_LINKS_OMITTED",
            "state": "POSITION_VELOCITY_ACCELEROMETER_BIAS",
            "update": (
                "CAUSAL_DELAYED_POSITION_KALMAN_UPDATE_WITH_MEASUREMENT_AND_"
                "AVAILABILITY_REPLAY_BOUNDED_EFFECTIVE_GAIN"
            ),
            "maximum_position_influence_m": maximum_position_influence_m,
            "position_update_stride": update_stride,
            "position_update_offset": update_offset,
            "ankle_contact": {
                "enabled": ankle_contact,
                "sensor_nodes": ANKLE_NODE_TO_SIDE,
                "detector": (
                    "BILATERAL_200HZ_RAW_ANKLE_IMU_PLUS_NATIVE200_"
                    "ANALYTIC_BASE_FK_FOUR_STATE_SUPPORT"
                ),
                "constraint": (
                    "SOFT_WORLD_ANKLE_PROXY_HORIZONTAL_POSITION_AND_VELOCITY_PER_"
                    "CONTACT_EPISODE"
                ),
                "constrained_axes": ["WORLD_X", "WORLD_Y"],
                "vertical_ankle_or_centre_of_pressure_claimed": False,
                "vertical_support_envelope": {
                    "type": "ONE_SIDED_FEASIBILITY_NOT_Z_PIN",
                    "lower_margin_m": 0.05,
                    "upper_excursion_m": 0.43,
                    "upper_owner": (
                        "SUBJECT_REPORTED_SHANK_SURFACE_CHORD_"
                        "CONSERVATIVE_BOUND_NOT_FOOT_LENGTH"
                    ),
                },
                "force_or_pressure_sensor_claimed": False,
                "articulated_ik_consumes_contact": bool(
                    ankle_contact
                    and effective_body_update == "articulated_consensus"
                ),
                "joint_residual": (
                    "MEASUREMENT_TIME_OWNED_FOOTHOLD_XY_PLUS_RAW_RANGES_"
                    "IN_ONE_ATOMIC_ARTICULATED_OBSERVATION;CONTACT_THEN_"
                    "RECLOSES_ROOT_AFTER_AVAILABLE_POSE_CHANGE"
                    if ankle_contact
                    and effective_body_update == "articulated_consensus"
                    else "DISABLED"
                ),
                "pose_change_reconciliation": (
                    "CURRENT_TIME_EXACT_PRIMARY_SUPPORT_XY_ROOT_REGAUGE_"
                    "THEN_SAME_POSE_SOFT_CHECK"
                    if ankle_contact
                    and effective_body_update == "articulated_consensus"
                    else "DISABLED"
                ),
            },
            "measurement_availability": "MAX_B306_TIMER2_FRAME_US_ACROSS_AVAILABLE_NODES",
            "per_link_measurement_time": "STROBE_US_PLUS_T_ROUND_US_OVER_2",
            "old_t4_or_position_consumed": False,
            "drift_correction": {
                "translation": "UWB_UPDATES_ROOT_POSITION_VELOCITY_AND_ACCELEROMETER_BIAS",
                "relative_pose": (
                    "UWB_RAW_RANGE_INCREMENTAL_IK_CORRECTIONS_THROUGH_FROZEN_FK"
                    if effective_body_update == "articulated_consensus"
                    else "FROZEN_IMU_POSE"
                ),
                "standalone_gyro_bias_state": False,
            },
            "antenna_orientation_nlos": {
                "enabled": effective_body_update == "articulated_consensus",
                "rule": (
                    "DOWNWEIGHT_ONLY_WHEN_MEASURED_MINUS_PREDICTED_IS_POSITIVE_"
                    "AND_EXCEEDS_ONE_SIGMA;NEVER_DELETE_FROM_ORIENTATION_ALONE"
                ),
                "outward_axis": "NODE_MINUS_Z_IN_FROZEN_SEGMENT_FRAME",
            },
            "node_propagation": {
                "enabled": effective_body_update == "articulated_consensus",
                "method": "ONE_CONNECTED_FK_TREE;NO_INDEPENDENT_NODE_TRANSLATIONS",
                "missing_or_held_node": "PREDICTED_FROM_REMAINING_NODES_AND_KINEMATIC_TREE",
            },
            "trajectory_ownership": {
                "proxy": (
                    analytic_owner["trajectory_owner"]
                    if analytic_owner is not None else proxy_scope
                ),
                "articulated_base_rotations": (
                    analytic_owner["trajectory_owner"]
                    if analytic_owner is not None else proxy_scope
                ),
                "ankle_contact_kinematics": (
                    analytic_owner["trajectory_owner"]
                    if analytic_owner is not None else proxy_scope
                ),
                "all_three_identical": analytic_owner is not None,
                "contact_classification": (
                    analytic_owner["trajectory_owner"]
                    if analytic_owner is not None else proxy_scope
                ),
                "contact_constraint_pose": (
                    "CAUSAL_FINAL_ARTICULATED_NATIVE200_POSE"
                    if causal_pose is not None else proxy_scope
                ),
            },
        },
        "clock_contract": {
            "global": "BEACON_LBD_GLOBAL_TDMA",
            "node": "B306_TIMER2_SHARED_BY_IMU_AND_UWB",
            "listener_measurements": ["LBD"],
            "forbidden_listener_measurements": ["LPD", "LRD"],
        },
        "body_proxy": {
            "scope": proxy_scope + "_NOT_ANTENNA_PHASE_CENTRES",
            "same_named_points_as_00_to_19": True,
            "refit_on_hxx": False,
            "alignment_source": "FROZEN_00_INITIAL_HEADING_ONLY",
            "frozen_forward": frozen_forward.tolist(),
            "analytic_pose": analytic_owner,
        },
        "biomechanics_projection": {
            **hinge_projection_audit,
            "native200_final_pose_gate": high_rate_pose_gate,
        },
        "range_bias": {
            "role": bias_role,
            "path": str(bias_table_path) if biases is not None else None,
            "sha256": _sha256(bias_table_path) if biases is not None else None,
            "episode_refit": False,
        },
        "uncertainty": {
            "method": "ADAPTIVE_DELETE_ONE_TRUSTED_NODE_JACKKNIFE_PLUS_COUNT_SCALED_FLOOR",
            "full_ten_node_minimum_std_m": MINIMUM_ROOT_STD_M,
            "scientific_R_pending_vicon": True,
        },
        "counts": {
            "decoded_measurements": decode.emitted_measurements,
            "decode_errors": decode.decode_errors,
            "shared_root_epochs": len(episode["groups"]),
            "complete_ten_node_epochs": epoch_group_audit[
                "complete_ten_node_epochs"
            ],
            "partial_node_epochs_consumed": epoch_group_audit[
                "partial_epochs_consumed"
            ],
            "accepted_shared_roots": len(observations),
            "position_updates_attempted_after_bootstrap": len(decisions),
            "position_updates_accepted": len(accepted),
            "position_updates_rejected": len(decisions) - len(accepted),
            "position_updates_by_contact_support": position_update_by_support,
            "shared_roots_withheld_from_filter": int(np.sum(
                ~observation_update_mask
            )),
            "imu_outputs": len(times),
            "ankle_imu_samples": len(ankle_rows),
            "contact_transitions": len(contact_events),
            "post_uwb_contact_reapplications": (
                post_uwb_contact_reapplications
            ),
            "contact_aware_articulated_attempted": sum(
                bool(row.get("attempted"))
                for row in contact_aware_articulated_events
            ),
            "contact_aware_articulated_accepted": sum(
                bool(row.get("accepted"))
                for row in contact_aware_articulated_events
            ),
            "articulated_updates_attempted": sum(
                row["articulated_attempted"] for row in observations
            ),
            "articulated_updates_accepted": sum(
                row["articulated_accepted"] for row in observations
            ),
            "articulated_pose_installs": contact_articulated["pose_installs"],
            "pose_change_reconciliations": contact_articulated[
                "reconciliations"
            ],
            "adaptive_mode_histogram": dict(Counter(
                row["adaptive_mode"] for row in observations
            )),
            "available_node_count_histogram": {
                str(count): frequency
                for count, frequency in sorted(Counter(
                    row["available_nodes"] for row in observations
                ).items())
            },
            "trusted_node_count_histogram": {
                str(count): frequency
                for count, frequency in sorted(Counter(
                    row["trusted_nodes"] for row in observations
                ).items())
            },
            "node_trust_rejection_reasons": dict(Counter(
                trust["reason"]
                for row in observations
                for trust in row["node_trust"]
                if not trust["trusted"]
            )),
            "articulated_pose_observable_rank_histogram": {
                str(rank): frequency
                for rank, frequency in sorted(Counter(
                    row["articulated_pose_observable_rank"]
                    for row in observations
                    if row["articulated_attempted"]
                ).items())
            },
        },
        "epoch_grouping": epoch_group_audit,
        "timing": {
            "median_uwb_availability_delay_ms": float(1e3 * np.median([
                row["availability_time_s"] - row["measurement_time_s"]
                for row in observations
            ])),
            "uwb_period_ms_median": float(1e3 * np.median(np.diff(
                [row["measurement_time_s"] for row in observations]
            ))),
            "bootstrap_time_s": bootstrap_time,
        },
        "trajectory_diagnostics": {
            "duration_s": float(times[-1]),
            "native200_pose_continuity_gate": continuity_gate,
            "mechanism_nondegeneracy_gate": mechanism_nondegeneracy_gate,
            "delayed_uwb_influence_gate": delayed_uwb_influence_gate,
            "imu_only_end_displacement_m": float(np.linalg.norm(inertial[-1] - inertial[0])),
            "fused_end_displacement_m": float(np.linalg.norm(fused[-1] - fused[0])),
            "shared_root_first_to_last_m": float(np.linalg.norm(uwb_position[-1] - uwb_position[0])),
            "imu_only_max_distance_from_shared_root_median_m": float(np.max(
                np.linalg.norm(inertial - np.median(uwb_position, axis=0), axis=1)
            )),
            "fused_max_distance_from_shared_root_median_m": float(np.max(
                np.linalg.norm(fused - np.median(uwb_position, axis=0), axis=1)
            )),
            "imu_only_fraction_outside_expanded_anchor_volume": float(np.mean(imu_outside)),
            "fused_fraction_outside_expanded_anchor_volume": float(np.mean(fused_outside)),
            "maximum_applied_position_delta_m": float(max(
                np.linalg.norm(row["applied_position_delta_m"]) for row in accepted
            )) if accepted else 0.0,
            "median_applied_position_delta_m": float(
                np.median(accepted_step_norm)
            ) if accepted else None,
            "p95_applied_position_delta_m": float(
                np.percentile(accepted_step_norm, 95)
            ) if accepted else None,
            "position_influence_cap_fraction": float(
                np.mean(accepted_step_norm >= config.maximum_position_influence_m - 1e-6)
            ) if accepted else None,
            "fused_root_velocity_end_norm_mps": float(
                np.linalg.norm(fused_velocity_array[-1])
            ),
            "fused_root_velocity_p95_norm_mps": float(np.percentile(
                np.linalg.norm(fused_velocity_array, axis=1), 95
            )),
            "fused_root_horizontal_path_m": float(np.sum(np.linalg.norm(
                np.diff(fused[:, :2], axis=0), axis=1
            ))),
            "contact": {
                "active_fraction": {
                    side: float(np.mean(contact_active_array[:, index]))
                    for index, side in enumerate(("left", "right"))
                },
                "confirmed_fraction": {
                    side: float(np.mean(contact_confirmed_array[:, index]))
                    for index, side in enumerate(("left", "right"))
                },
                "prior_held_fraction": {
                    side: float(np.mean(contact_prior_held_array[:, index]))
                    for index, side in enumerate(("left", "right"))
                },
                "detector": contact_detector_audit,
                "stationary_prior_gate": stationary_prior_gate,
                "maximum_active_episode_ankle_drift_m": {
                    side: _maximum_active_episode_drift(
                        contact_ankle_world_array[:, index],
                        contact_active_array[:, index],
                    )
                    for index, side in enumerate(("left", "right"))
                },
                "maximum_constrained_episode_ankle_drift_m": {
                    side: _maximum_active_episode_drift(
                        contact_ankle_world_array[:, index],
                        contact_constrained_array[:, index],
                    )
                    for index, side in enumerate(("left", "right"))
                },
                "maximum_constrained_episode_ankle_horizontal_drift_m": {
                    side: _maximum_active_episode_drift(
                        contact_ankle_world_array[:, index, :2],
                        contact_constrained_array[:, index],
                    )
                    for index, side in enumerate(("left", "right"))
                },
                "maximum_constrained_episode_ankle_vertical_excursion_m": {
                    side: _maximum_active_episode_drift(
                        contact_ankle_world_array[:, index, 2:3],
                        contact_constrained_array[:, index],
                    )
                    for index, side in enumerate(("left", "right"))
                },
                "ankle_proxy_path_m": {
                    side: float(np.sum(np.linalg.norm(np.diff(
                        contact_ankle_world_array[:, index], axis=0
                    ), axis=1)))
                    for index, side in enumerate(("left", "right"))
                },
                "applied_position_delta_p95_m": float(np.percentile(
                    np.linalg.norm(contact_position_delta_array, axis=1), 95
                )),
                "applied_velocity_delta_p95_mps": float(np.percentile(
                    np.linalg.norm(contact_velocity_delta_array, axis=1), 95
                )),
                "causal_articulated_pose_reconciliation": contact_articulated,
                "final_emitted_pose_gate": contact_final_pose_gate,
            },
            "accelerometer_bias_change_norm_mps2": float(np.linalg.norm(
                fused_bias_array[-1] - fused_bias_array[0]
            )),
            "shared_root_links_per_epoch": {
                "minimum": int(min(row["links"] for row in observations)),
                "median": float(np.median([
                    row["links"] for row in observations
                ])),
                "maximum": int(max(row["links"] for row in observations)),
            },
            "raw_shared_root_links_per_epoch": {
                "minimum": int(min(row["raw_links"] for row in observations)),
                "median": float(np.median([
                    row["raw_links"] for row in observations
                ])),
                "maximum": int(max(row["raw_links"] for row in observations)),
            },
            "median_accepted_nis": float(np.median([
                row["nis"] for row in accepted
            ])) if accepted else None,
            "articulated_segment_correction_rad": {
                "median": float(np.median([
                    np.linalg.norm(row["segment_correction_rotvec"], axis=1).max()
                    for row in observations
                ])),
                "p95": float(np.percentile([
                    np.linalg.norm(row["segment_correction_rotvec"], axis=1).max()
                    for row in observations
                ], 95)),
                "maximum": float(max(
                    np.linalg.norm(row["segment_correction_rotvec"], axis=1).max()
                    for row in observations
                )),
            },
            "facing_confirmed_positive_nlos_downweight_fraction": float(
                np.mean(np.concatenate([
                    row["facing_nlos_weight"] < 0.999999
                    for row in observations
                ]))
            ),
            "articulated_range_fit": {
                "accepted_update_count": int(sum(
                    row["articulated_accepted"] for row in observations
                )),
                "median_prefit_median_abs_m": float(np.median([
                    row["articulated_prefit_median_abs_m"]
                    for row in observations if row["articulated_accepted"]
                ])) if any(
                    row["articulated_accepted"] for row in observations
                ) else None,
                "median_postfit_median_abs_m": float(np.median([
                    row["articulated_postfit_median_abs_m"]
                    for row in observations if row["articulated_accepted"]
                ])) if any(
                    row["articulated_accepted"] for row in observations
                ) else None,
            },
        },
        "accelerometer_bias_initial_sensor_mps2": bias.tolist(),
        "contact_calibration": {
            "source_actions": ["00_initial_still", "17_final_still"],
            "profiles": {
                side: {
                    "gyro_rms_rad_s": profile.gyro_rms_rad_s,
                    "acceleration_std_mps2": profile.acceleration_std_mps2,
                }
                for side, profile in (contact_profiles or {}).items()
            },
            "config": {
                "window_samples": contact_config.window_samples,
                "calibration_quantile": contact_config.calibration_quantile,
                "threshold_scale": contact_config.threshold_scale,
                "enter_confidence": contact_config.enter_confidence,
                "exit_confidence": contact_config.exit_confidence,
                "enter_samples": contact_config.enter_samples,
                "exit_samples": contact_config.exit_samples,
                "full_height_margin_m": contact_config.full_height_margin_m,
                "maximum_height_margin_m": contact_config.maximum_height_margin_m,
                "kinematic_speed_scale_mps": (
                    contact_config.kinematic_speed_scale_mps
                ),
            },
        },
        "orientation": orientation_audit,
        "position_update_reasons": {
            reason: sum(row["reason"] == reason for row in decisions)
            for reason in sorted({row["reason"] for row in decisions})
        },
        "known_boundaries": [
            f"No external world-position truth exists in {action}.",
            "The 0.12 m covariance floor is diagnostic; Vicon-derived R remains pending.",
            "Display-proxy joint points are not measured antenna phase centres.",
            "This run validates causal plumbing and drift containment, not metric accuracy.",
            "The hinge/ROM model uses functional axes and population ROM priors; it is not subject-specific clinical biomechanics.",
            "Ankle contact is inferred from ankle-node IMU and a display proxy; no plantar pressure or force plate was measured.",
        ],
        "inputs": {
            "raw": str(episode["raw"]),
            "raw_sha256": _sha256(episode["raw"]),
            "clock": str(clock_path),
            "clock_sha256": _sha256(clock_path),
            "analytic_pose_result": (
                None if analytic_owner is None
                else analytic_owner["analytic_result"]
            ),
            "analytic_pose_result_sha256": (
                None if analytic_owner is None
                else analytic_owner["analytic_result_sha256"]
            ),
        },
        "output": f"{action}_SHARED_ROOT_IMU_FUSION.npz",
        "wall_s": time.perf_counter() - started,
    }
    (output / "RESULT.json").write_text(
        json.dumps(_json_ready(result), indent=2) + "\n"
    )
    (output / "DECISIONS.json").write_text(
        json.dumps(_json_ready(decisions), indent=2) + "\n"
    )
    (output / "CONTACT_EVENTS.json").write_text(
        json.dumps(_json_ready(contact_events), indent=2) + "\n"
    )
    (output / "CONTACT_AWARE_ARTICULATED_EVENTS.json").write_text(
        json.dumps(
            _json_ready(contact_aware_articulated_events), indent=2
        ) + "\n"
    )
    sealed = sorted(path for path in output.iterdir() if path.name != "SHA256SUMS")
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sealed)
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--bias-table", type=Path, default=DEFAULT_BIAS_TABLE)
    parser.add_argument("--action", choices=SUPPORTED_ACTIONS, default=DEFAULT_ACTION)
    parser.add_argument("--maximum-position-influence-m", type=float, default=0.05)
    parser.add_argument("--update-stride", type=int, default=1)
    parser.add_argument("--update-offset", type=int, default=0)
    parser.add_argument("--link-selection", choices=LINK_SELECTIONS, default="all")
    parser.add_argument("--body-update", choices=BODY_UPDATES, default="shared_root")
    parser.add_argument("--articulated-stride", type=int, default=4)
    parser.add_argument(
        "--node-selection", choices=NODE_SELECTIONS, default="adaptive"
    )
    parser.add_argument(
        "--ankle-contact", action="store_true",
        help="enable bilateral ankle-IMU contact and soft world footholds",
    )
    parser.add_argument(
        "--stationary-no-flight-prior", action="store_true",
        help="H02-only explicit stationary/no-flight support prior",
    )
    parser.add_argument(
        "--analytic-pose-result", type=Path,
        help="accepted native-200 analytic IK FINAL_RESULT.json",
    )
    parser.add_argument(
        "--analytic-calibration-report", type=Path,
        help="native-200 calibration report owning the functional hinge axes",
    )
    parser.add_argument(
        "--pre-ik-hxx-report", type=Path,
        help="native-200 HXX report before analytic hinge projection",
    )
    parser.add_argument(
        "--maximum-duration-s", type=float,
        help="bounded diagnostic prefix after bootstrap; omitted for full run",
    )
    args = parser.parse_args()
    result = run(
        args.output.resolve(),
        args.clock.resolve(),
        action=args.action,
        bias_table_path=args.bias_table.resolve(),
        maximum_position_influence_m=args.maximum_position_influence_m,
        update_stride=args.update_stride,
        update_offset=args.update_offset,
        link_selection=args.link_selection,
        body_update=args.body_update,
        articulated_stride=args.articulated_stride,
        node_selection=args.node_selection,
        ankle_contact=args.ankle_contact,
        stationary_no_flight_prior=args.stationary_no_flight_prior,
        analytic_pose_result_path=(
            None if args.analytic_pose_result is None
            else args.analytic_pose_result.resolve()
        ),
        analytic_calibration_report_path=(
            None if args.analytic_calibration_report is None
            else args.analytic_calibration_report.resolve()
        ),
        pre_ik_hxx_report_path=(
            None if args.pre_ik_hxx_report is None
            else args.pre_ik_hxx_report.resolve()
        ),
        maximum_duration_s=args.maximum_duration_s,
    )
    print(json.dumps(_json_ready({
        "status": result["status"],
        "counts": result["counts"],
        "timing": result["timing"],
        "trajectory_diagnostics": result["trajectory_diagnostics"],
        "position_update_reasons": result["position_update_reasons"],
        "wall_s": result["wall_s"],
    }), indent=2))


if __name__ == "__main__":
    main()
