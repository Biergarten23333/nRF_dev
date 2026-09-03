#!/usr/bin/env python3
"""Focused post-seal/prepayload center-budget and transaction-rollback gate."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
AMENDMENT_RELATIVE = RUN_RELATIVE / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_017.json"
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_017.json"
OUTPUT_RELATIVE = RUN_RELATIVE / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_001.json"


def _pair(rows: int):
    from biospur_fusion.v0.c2_progressive.functional_geometry import AlignedPair
    from biospur_fusion.v0.c2_progressive.timebase import PairAlignment

    index = np.arange(rows, dtype=np.int64)
    sample = index.astype(float)
    time_s = 1.0 + sample * 0.005
    gyro = np.column_stack((
        0.8 * np.sin(0.019 * sample),
        0.5 * np.cos(0.027 * sample + 0.2),
        0.3 * np.sin(0.043 * sample - 0.1),
    ))
    parent_acc = np.column_stack((
        0.7 * np.sin(0.017 * sample),
        0.4 * np.cos(0.031 * sample),
        9.80665 + 0.3 * np.sin(0.023 * sample),
    ))
    child_acc = parent_acc + np.column_stack((
        0.03 * np.cos(0.021 * sample),
        0.02 * np.sin(0.029 * sample),
        0.02 * np.cos(0.037 * sample),
    ))
    return AlignedPair(
        edge="pelvis_torso",
        action="03_pelvis_hula_circle",
        parent_acc=parent_acc,
        child_acc=child_acc,
        parent_gyro=gyro,
        child_gyro=0.94 * gyro,
        parent_observed_time_s=time_s,
        child_observed_time_s=time_s,
        parent_boot_epoch=np.zeros(rows, dtype=np.int64),
        child_boot_epoch=np.zeros(rows, dtype=np.int64),
        alignment=PairAlignment(
            parent_indices=index,
            child_indices=index,
            lag_samples=0,
            report={"lag_uncertainty_s": 0.005},
        ),
        contiguous_spans=(slice(0, rows),),
        provenance={
            "chronological_index": 2,
            "action": "03_pelvis_hula_circle",
            "runtime_owner_token": "FOCUSED_BUDGET_FIXTURE",
            "source_role": "SYNTHETIC_PREPAYLOAD",
            "heldout": False,
        },
    )


def _budget_exception(settings):
    from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
    from biospur_fusion.v0.c2_progressive import functional_geometry as fg

    center = deepcopy(dict(settings["joint_center"]))
    center["factor_maximum_solver_calls"] = 100
    center["factor_maximum_total_function_evaluations"] = 100
    noise = settings["synthetic"]["estimator_input_noise"]
    original = fg.least_squares

    def fake_least_squares(fun, x0, **_kwargs):
        x = np.asarray(x0, dtype=float).copy()
        residual = np.asarray(fun(x), dtype=float)
        return SimpleNamespace(
            x=x,
            fun=residual,
            cost=float(0.5 * residual @ residual),
            success=True,
            nfev=2,
            status=1,
            message="FOCUSED_DETERMINISTIC_BUDGET_FIXTURE",
        )

    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    try:
        fg.least_squares = fake_least_squares
        fg.estimate_joint_center_pair_local(
            "pelvis_torso", "pelvis", "torso", [_pair(3000)],
            settings=center,
            parent_acc_covariance=np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2,
            child_acc_covariance=np.eye(3) * float(noise["accelerometer_sigma_mps2"]) ** 2,
            parent_gyro_observation_covariance=np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2,
            child_gyro_observation_covariance=np.eye(3) * float(noise["gyroscope_sigma_rads"]) ** 2,
            parent_gyro_bias_covariance=np.eye(3) * float(noise["gyroscope_bias_sigma_rads"]) ** 2,
            child_gyro_bias_covariance=np.eye(3) * float(noise["gyroscope_bias_sigma_rads"]) ** 2,
            execution_guard=guard,
        )
    except fg.CenterFactorAggregateBudgetExceeded as exc:
        return exc
    finally:
        fg.least_squares = original
    raise RuntimeError("focused center fixture did not exhaust its aggregate total-nfev budget")


def _runtime(settings, initial):
    from biospur_fusion.v0.c2_progressive.pipeline_runtime import C2PipelineRuntime
    from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction, IMU_DTYPE

    runtime = C2PipelineRuntime(
        settings,
        initial,
        prefit_registry_seal_path=WORKSPACE / SEAL_RELATIVE,
        execution_role="SYNTHETIC_QUALIFICATION",
    )
    nodes = tuple(str(node) for node in initial["nodes"])
    for action_index, action in enumerate(
        settings["execution_contract"]["chronological_actions"]
    ):
        rows_by_node = {}
        for node_index, node in enumerate(nodes):
            row_count = 3_000 if action_index == 9 else 4
            sample = np.arange(row_count, dtype=float)
            rows = np.zeros(row_count, dtype=IMU_DTYPE)
            rows["derived_boot_epoch"] = 0
            rows["imu_sample_sequence"] = (
                np.arange(row_count, dtype=np.uint16) + 4 * action_index
            )
            rows["node_timer_us"] = (
                1_000_000 + action_index * 10_000_000 + node_index * 100
                + np.arange(row_count, dtype=np.uint64) * 5_000
            )
            rows["acc_raw"][:, 2] = 2_048
            if action_index == 9:
                rows["acc_raw"][:, 0] = np.rint(
                    120.0 * np.sin(0.019 * sample)
                ).astype(np.int16)
                rows["gyro_raw"][:, 0] = np.rint(
                    900.0 * np.sin(0.017 * sample)
                ).astype(np.int16)
                rows["gyro_raw"][:, 1] = np.rint(
                    600.0 * np.cos(0.023 * sample)
                ).astype(np.int16)
            rows["raw_start_offset"] = (
                np.arange(row_count, dtype=np.uint64) + node_index * (row_count + 16)
            )
            rows["raw_end_offset"] = rows["raw_start_offset"] + 1
            rows["raw_sample_index"] = np.arange(row_count, dtype=np.uint64) % 256
            rows["decode_acceptance_status"] = 1
            rows_by_node[node] = rows
        runtime.ingest_orientation_episode(DecodedAction(
            action=action,
            chronological_index=action_index,
            interval=(action_index, action_index + 1),
            rows_by_node=rows_by_node,
            access_audit={
                "reader_session_id": "SYNTHETIC_BOUNDED_CENTER_ROLLBACK",
                "action": action,
                "chronological_index": action_index,
            },
            decode_audit={"synthetic_bounded_center_rollback": True},
        ))
    runtime.finish_orientation_and_begin_calibration()
    return runtime


def _representative_geometry():
    from biospur_fusion.v0.c2_progressive.functional_geometry import (
        EDGE_SPECS,
        HINGE_EDGES,
        AxisEstimate,
        CenterEstimate,
    )

    points = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "torso": np.array([0.0, 0.0, 0.3]),
        "upper_arm_left": np.array([-0.22, 0.0, 0.38]),
        "forearm_left": np.array([-0.48, 0.0, 0.32]),
        "upper_arm_right": np.array([0.22, 0.0, 0.38]),
        "forearm_right": np.array([0.48, 0.0, 0.32]),
        "thigh_left": np.array([-0.08, 0.0, -0.34]),
        "shank_left": np.array([-0.12, 0.0, -0.82]),
        "thigh_right": np.array([0.08, 0.0, -0.34]),
        "shank_right": np.array([0.12, 0.0, -0.82]),
    }
    joints = {
        "pelvis_torso": np.array([0.0, 0.0, 0.20]),
        "shoulder_left": np.array([-0.10, 0.0, 0.40]),
        "elbow_left": np.array([-0.36, 0.0, 0.36]),
        "shoulder_right": np.array([0.10, 0.0, 0.40]),
        "elbow_right": np.array([0.36, 0.0, 0.36]),
        "hip_left": np.array([-0.06, 0.0, -0.12]),
        "knee_left": np.array([-0.12, 0.0, -0.66]),
        "hip_right": np.array([0.06, 0.0, -0.12]),
        "knee_right": np.array([0.12, 0.0, -0.68]),
    }
    statistical_center = np.eye(6) * 4e-4
    systematic_center = np.eye(6) * 0.03**2
    centers = {}
    for edge, parent, child in EDGE_SPECS:
        centers[edge] = CenterEstimate(
            edge=edge,
            parent=parent,
            child=child,
            joint_to_parent_sensor_m=-(joints[edge] - points[parent]),
            joint_to_child_sensor_m=-(joints[edge] - points[child]),
            covariance_m2=statistical_center + systematic_center,
            report={
                "synthetic_bounded_center_mature_fixture": True,
                "sandwich_covariance_m2": statistical_center.tolist(),
                "statistical_covariance_including_nullspace_prior_m2": (
                    statistical_center.tolist()
                ),
                "human_worn_model_floor_m": 0.03,
                "accelerometer_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "accelerometer_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_bias_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_bias_drift_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "gyro_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "persistent_clock_systematic_covariance_m2": np.zeros((6, 6)).tolist(),
                "human_worn_systematic_covariance_m2": systematic_center.tolist(),
                "total_systematic_covariance_m2": systematic_center.tolist(),
                "owner_update_eligible": True,
                "owner_update_mode": "GAUGE_REDUCED_ROBUST_BREAD_INFORMED_SUBSPACE",
                "gauge_reduced_robust_bread_informed_basis": np.eye(6).tolist(),
                "gauge_reduced_robust_bread_information_m2_inv": (
                    np.eye(6) / 4e-4
                ).tolist(),
            },
        )
    tangent_basis = np.array([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]])
    statistical_axis = np.eye(4) * np.deg2rad(2.0) ** 2
    systematic_axis = np.eye(4) * np.deg2rad(5.0) ** 2
    axes = {
        edge: AxisEstimate(
            edge=edge,
            parent_axis_sensor=np.array([0.0, 1.0, 0.0]),
            child_axis_sensor=np.array([0.0, 1.0, 0.0]),
            tangent_covariance_rad2=statistical_axis + systematic_axis,
            report={
                "synthetic_bounded_center_mature_fixture": True,
                "parent_tangent_basis_sensor": tangent_basis.tolist(),
                "child_tangent_basis_sensor": tangent_basis.tolist(),
                "statistical_tangent_covariance_rad2": statistical_axis.tolist(),
                "total_systematic_tangent_covariance_rad2": systematic_axis.tolist(),
                "systematic_component_tangent_covariances_rad2": {
                    "human_worn": systematic_axis.tolist(),
                },
                "systematic_human_worn_tangent_covariance_rad2": systematic_axis.tolist(),
                "owner_update_eligible": True,
                "owner_update_mode": "PRODUCT_S2_HESSIAN_INFORMED_UPDATE",
            },
        )
        for edge in HINGE_EDGES
    }
    return axes, centers


def _seed_full_geometry(runtime) -> None:
    axes, centers = _representative_geometry()
    for estimate in centers.values():
        runtime._geometry_owner.ingest_center(
            deepcopy(estimate),
            chronological_index=-1,
            action="SYNTHETIC_BOUNDED_CENTER_MATURE_SEED",
            reference_time_s=0.0,
        )
    for estimate in axes.values():
        runtime._geometry_owner.ingest_axis(
            deepcopy(estimate),
            chronological_index=-1,
            action="SYNTHETIC_BOUNDED_CENTER_MATURE_SEED",
            reference_time_s=0.0,
        )


def _commit_mature_prefix(runtime, settings, index: int) -> None:
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS

    action = settings["execution_contract"]["chronological_actions"][index]
    with runtime.calibration_episode_transaction(index, action):
        runtime.score_current_prequential()
        runtime.finish_current_geometry_update()
        branches = runtime.update_current_frame_branches()
        hard_support = runtime.current_heading_hard_support()
        branch_by_id = {branch.branch_id: branch for branch in branches}
        if not hard_support["branch_ids"] or any(
            branch_id not in branch_by_id
            for branch_id in hard_support["branch_ids"]
        ):
            raise RuntimeError(
                "mature rollback fixture hard-support IDs diverge from frame branches"
            )
        for branch_id in hard_support["branch_ids"]:
            branch = branch_by_id[branch_id]
            for edge, _, _ in EDGE_SPECS:
                runtime.record_current_heading_no_update(
                    branch_id=branch.branch_id,
                    edge=edge,
                    cause="SYNTHETIC_MATURE_ROLLBACK_PREFIX_LOCAL_NO_UPDATE",
                    hard_support_token=hard_support["owner_token"],
                )
        runtime.finish_current_heading()
        runtime.assess_current_physical_candidates()
        runtime.commit_current_progressive()


def _sha_mapping(runtime, value) -> str:
    return sha256(
        json.dumps(
            runtime._canonical_state(value),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _sha_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("focused center-budget gate requires canonical Fusion_Part")
    amendment = json.loads((WORKSPACE / AMENDMENT_RELATIVE).read_text(encoding="utf-8"))
    seal = json.loads((WORKSPACE / SEAL_RELATIVE).read_text(encoding="utf-8"))
    settings = amendment["effective_settings"]
    initial_path = WORKSPACE / settings["execution_contract"][
        "initial_stochastic_state_relative_path"
    ]
    initial = json.loads(initial_path.read_text(encoding="utf-8"))
    budget_exc = _budget_exception(settings)
    runtime = _runtime(settings, initial)
    _seed_full_geometry(runtime)
    for index in range(9):
        _commit_mature_prefix(runtime, settings, index)
    if "knee_left" not in runtime._geometry_owner.posterior_centers():
        raise RuntimeError("mature fixture did not create the temporary knee-left seed")
    if "knee_left" in runtime._center_prefix_owner.audit()["accepted_edges"]:
        raise RuntimeError("mature fixture unexpectedly accepted a knee-left prefix")
    # The complete temporary geometry seed is used only to construct mature
    # frame/heading/progressive histories.  The real causal state immediately
    # before action 10 has not accepted a knee-left center in either owner, so
    # remove that one temporary geometry state before the rollback baseline.
    del runtime._geometry_owner._center_state["knee_left"]
    if (
        "knee_left" in runtime._geometry_owner.posterior_centers()
        or "knee_left" in runtime._center_prefix_owner.audit()["accepted_edges"]
        or runtime._center_prefix_owner.audit()["pending_edges"]
    ):
        raise RuntimeError(
            "mature fixture knee-left accepted ownership did not return to the causal pre-fit state"
        )
    mature_component_hashes = dict(runtime._episode_checkpoint_component_hashes())
    mature_clock_audit = deepcopy(runtime._clock_owner.audit())
    mature_snapshot_count = len(runtime._progress_snapshots)
    mature_heading_history_count = len(runtime._heading_trajectory_history)
    mature_physical_history_count = len(runtime._physical_trajectory_history)
    mature_geometry_audit = deepcopy(runtime._geometry_owner.audit())
    mature_frame_audit = deepcopy(runtime._frame_owner.audit())
    current_selection_completed = False
    current_pair_token = None
    current_selection_token = None
    try:
        with runtime.calibration_episode_transaction(9, settings[
            "execution_contract"
        ]["chronological_actions"][9]):
            runtime.score_current_prequential()
            pair = runtime.align_current_pair(edge="knee_left")
            if (
                pair.edge != "knee_left"
                or pair.action != settings["execution_contract"][
                    "chronological_actions"
                ][9]
                or int(pair.provenance["chronological_index"]) != 9
                or "knee_left" in runtime._geometry_owner.posterior_centers()
                or "knee_left" in runtime._center_prefix_owner.audit()[
                    "accepted_edges"
                ]
            ):
                raise RuntimeError(
                    "mature fixture current knee pair/action/index or accepted ownership diverged"
                )
            selection = runtime.record_current_center_no_update(
                "knee_left",
                pair,
                cause="INJECTED_AGGREGATE_BUDGET_AFTER_CURRENT_SELECTION",
            )
            current_selection_completed = True
            current_pair_token = str(pair.provenance["runtime_owner_token"])
            current_selection_token = str(selection["selection_token"])
            raise budget_exc
    except type(budget_exc) as observed:
        if observed is not budget_exc:
            raise RuntimeError("transaction changed the exact budget exception object")
    event = runtime.audit()["transaction_events"][-1]
    clock = runtime._clock_owner.audit()
    budget = dict(budget_exc.audit)
    after_component_hashes = dict(runtime._episode_checkpoint_component_hashes())
    mature_components_present = bool(
        mature_snapshot_count == 9
        and mature_clock_audit["node_count"] > 0
        and mature_heading_history_count > 0
        and mature_physical_history_count > 0
        and len(mature_geometry_audit.get("center_edges", ())) > 0
        and len(mature_geometry_audit.get("axis_edges", ())) > 0
        and mature_frame_audit.get("branch_count", 0) > 0
        and runtime._heading_owner is not None
    )
    passed = bool(
        budget["budget_exhaustion_disposition"] == "LOCAL_NO_UPDATE_BUDGET_EXHAUSTED"
        and budget["observed_total_function_evaluations"]
        > budget["maximum_total_function_evaluations"]
        and budget["full_coherent_sensitivity_completed"] is False
        and budget["coherent_sensitivity_pass_claimed"] is False
        and event["status"] == "ROLLED_BACK"
        and event["exception_type"] == "CenterFactorAggregateBudgetExceeded"
        and event["owner_state_hashes_equal"] is True
        and event["owner_state_hash_before"] == event["owner_state_hash_after_rollback"]
        and event["mismatched_top_level_components"] == []
        and event["top_level_component_hashes_before"]
        == event["top_level_component_hashes_after"]
        and mature_components_present
        and current_selection_completed
        and mature_component_hashes == after_component_hashes
        and mature_snapshot_count == len(runtime._progress_snapshots) == 9
        and clock == mature_clock_audit
        and len(runtime._heading_trajectory_history) == mature_heading_history_count
        and len(runtime._physical_trajectory_history) == mature_physical_history_count
    )
    document = {
        "schema": "biospur-c2-bounded-center-budget-rollback-gate-v1",
        "status": "PASS" if passed else "FAIL",
        "pass": passed,
        "prefit_registry_seal": {
            "path": str(SEAL_RELATIVE),
            "sha256": _sha_file(WORKSPACE / SEAL_RELATIVE),
        },
        "settings_semantic_sha256": seal["settings_semantic_sha256"],
        "qualified_source_hashes": seal["qualified_source_hashes"],
        "budget_exception": {"type": type(budget_exc).__name__, "audit": budget},
        "transaction_event": event,
        "mature_state_before_injection": {
            "committed_prefix_count": mature_snapshot_count,
            "node_clock_audit_sha256": _sha_mapping(runtime, mature_clock_audit),
            "geometry_audit_sha256": _sha_mapping(runtime, mature_geometry_audit),
            "frame_audit_sha256": _sha_mapping(runtime, mature_frame_audit),
            "heading_trajectory_history_count": mature_heading_history_count,
            "physical_trajectory_history_count": mature_physical_history_count,
            "top_level_component_hashes": mature_component_hashes,
            "knee_left_geometry_center_accepted": False,
            "knee_left_center_prefix_accepted": False,
        },
        "injected_transaction": {
            "chronological_index": 9,
            "action": settings["execution_contract"]["chronological_actions"][9],
            "score_completed_before_injection": True,
            "current_center_selection_completed_before_injection": current_selection_completed,
            "current_pair_runtime_owner_token": current_pair_token,
            "current_center_selection_token": current_selection_token,
        },
        "mature_state_after_rollback": {
            "committed_prefix_count": len(runtime._progress_snapshots),
            "node_clock_audit_sha256": _sha_mapping(runtime, clock),
            "heading_trajectory_history_count": len(runtime._heading_trajectory_history),
            "physical_trajectory_history_count": len(runtime._physical_trajectory_history),
            "top_level_component_hashes": after_component_hashes,
        },
        "mature_component_hashes_exact_after_rollback": (
            mature_component_hashes == after_component_hashes
        ),
        "mature_components_present": mature_components_present,
        "all_direct_scipy_center_calls_have_max_nfev": True,
        "aggregate_budget_is_below_full_qualification_path": bool(
            settings["joint_center"]["factor_maximum_solver_calls"]
            < budget["expected_full_qualification_solver_calls"]
        ),
        "payload_opened": False,
        "heldout_opened": False,
        "scientific_pass_claimed": False,
    }
    path = WORKSPACE / OUTPUT_RELATIVE
    with path.open("x", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)
    if not passed:
        raise RuntimeError("focused bounded-center rollback gate failed")
    print(json.dumps({"path": str(OUTPUT_RELATIVE), "pass": True}, indent=2))


if __name__ == "__main__":
    main()
