from __future__ import annotations

import ast
from contextlib import contextmanager
from copy import deepcopy
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import tempfile
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.c2_progressive.architecture_guard import (
    C2ExecutionGuard,
    ClassAGuardViolation,
    run_owner_level_architecture_mutations,
)
from biospur_fusion.v0.c2_progressive.center_prefix import CausalCenterPrefixOwner
from biospur_fusion.v0.c2_progressive.center_prefix import CenterPrefixSelection
from biospur_fusion.v0.c2_progressive.calibration_posterior import (
    CaptureWideCalibrationPosterior,
    marginalize_axis_class_c,
    marginalize_center_class_c,
)
from biospur_fusion.v0.c2_progressive.functional_geometry import (
    EDGE_ACTIONS,
    AlignedPair,
    AxisEstimate,
    CenterEstimate,
    _axis_blocks,
    _blockwise_angular_acceleration_rms,
    _online_axis_candidate_mixture,
    _online_center_candidate_mixture,
    estimate_hinge_axis_qmt,
    estimate_joint_center_pair_local,
    aligned_pair,
    numeric_axis_centered_support_transform_gate,
    numeric_center_physical_time_ownership_gate,
)
from biospur_fusion.v0.c2_progressive.timebase import (
    PairAlignment,
    PersistentPairClockState,
    align_pair_by_gyro_energy,
)
from biospur_fusion.v0.c2_progressive.orientation import OrientedAction, assess_factor_rows
from biospur_fusion.v0.c2_progressive.orientation_uncertainty import (
    physical_orientation_covariance,
)
from biospur_fusion.v0.c2_progressive.geometry_posterior import (
    _axis_systematic_component_union,
    _psd_upper_envelope,
    numeric_low_information_geometry_owner_gate,
)
from biospur_fusion.v0.c2_progressive.heading import (
    HeadingTrajectoryResult,
    NONHINGE_EDGES,
    PersistentHeadingOwner,
    UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT,
    _array_sha256 as _heading_array_sha256,
    _qmt_compatible_local_time,
)
from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
    C2PipelineRuntime,
    _owner_authenticated_aligned_pair_replay_arrays,
    _owner_authenticated_orientation_replay_arrays,
    _validated_bounded_center_diagnostic_gate,
)
from biospur_fusion.v0.c2_progressive.progressive import ProgressiveCalibrationState
from biospur_fusion.v0.c2_progressive.quaternion_contract import numeric_round_trip_gate
from biospur_fusion.v0.c2_progressive.scientific_renderer import (
    _direct_two_sided_fk_points,
    _functional_landmark_proxy_fk_from_frozen_arrays,
    _render_sensor_axis_checkpoint,
    _rotate_world_from_sensor_wxyz,
    _validated_source_display,
    render_registered_scientific_triviews,
)
from biospur_fusion.v0.c2_progressive.scientific_fk import (
    direct_orientation_avatar_fk,
    fixed_landmark_proxy_avatar_fk,
    landmark_proxy_fk_points,
    landmark_proxy_sensitivity_profiles,
)
from biospur_fusion.v0.c2_progressive.segment_frames import (
    EdgeConnectionVectors,
    SegmentFrameBranchOwner,
    _construct_sensor_from_segment,
    _frame_from_z_y,
)
from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS, HINGE_EDGES
from biospur_fusion.v0.c2_progressive.synthetic import (
    _align_exact_post_qmt_fixture_pairs,
    _audit_post_frame_pair_identity_reuse,
    _near_axis_owner_evidence,
    _near_axis_owner_no_false_pass,
    _numeric_center_full_owner_physical_time_gate,
    _numeric_center_gap_eligibility_gate,
    _numeric_center_gyro_stochastic_sensitivity_gate,
    _numeric_registered_center_coherent_nuisance_mutation_gate,
    _pair_with_observation_conditions,
    _production_equivalent_synthetic_orientation_frontend,
    _static_low_information_owner_evidence,
    _static_low_information_owner_no_false_pass,
    generate_physical_pair,
)
from tools.run_c2_progressive_real import (
    _OperationFailure,
    _raise_operation_failure,
)


ROOT = Path(__file__).resolve().parents[2]
RUN_DIR = ROOT / "logs/c2_basis_progressive_20260829T102836Z"
SYNTHETIC_INITIAL_STILL_DURATION_S = 35.05


def _settings_with_explicit_synthetic_initial_still(settings: dict) -> dict:
    output = deepcopy(settings)
    from tools.amend_c2_p2_prefit_010 import build_effective_settings

    output["calibration_posterior"] = deepcopy(
        build_effective_settings(ROOT)["calibration_posterior"]
    )
    output["synthetic"]["initial_still_duration_s"] = (
        SYNTHETIC_INITIAL_STILL_DURATION_S
    )
    output["synthetic"]["initial_still_duration_provenance"] = (
        "P1_INITIAL_STILL_7010_ROWS_AT_REGISTERED_200_HZ_FOR_INPUT_DURATION_PLANNING_ONLY"
    )
    return output


def test_operation_failure_crosses_contextmanager_and_retains_rollback_diagnostic() -> None:
    @contextmanager
    def passthrough():
        try:
            yield
        except BaseException:
            raise

    class BudgetFailure(RuntimeError):
        def __init__(self) -> None:
            self.audit = {"budget_exhaustion_disposition": "LOCAL_NO_UPDATE_BUDGET_EXHAUSTED"}
            super().__init__("bounded center factor exhausted")

    with pytest.raises(_OperationFailure) as caught:
        with passthrough():
            _raise_operation_failure("CENTER_ESTIMATE", BudgetFailure(), edge="pelvis_torso")
    assert caught.value.original_type == "BudgetFailure"
    assert caught.value.original_diagnostic == {
        "budget_exhaustion_disposition": "LOCAL_NO_UPDATE_BUDGET_EXHAUSTED"
    }
    rollback = _validated_bounded_center_diagnostic_gate(
        ROOT,
        RUN_DIR / "CONTINUATION_SPRINT/BOUNDED_CENTER_BUDGET_ROLLBACK_GATE_001.json",
    )
    assert rollback["transaction_status"] == "ROLLED_BACK"
    assert rollback["owner_state_hashes_equal"] is True
    assert rollback["mismatched_top_level_components"] == []


def test_current_episode_pair_and_selection_deepcopy_has_semantic_slice_hash() -> None:
    pair = AlignedPair(
        edge="pelvis_torso",
        action="03_pelvis_hula_circle",
        parent_acc=np.zeros((2, 3)),
        child_acc=np.zeros((2, 3)),
        parent_gyro=np.zeros((2, 3)),
        child_gyro=np.zeros((2, 3)),
        parent_observed_time_s=np.array([0.0, 0.005]),
        child_observed_time_s=np.array([0.0, 0.005]),
        parent_boot_epoch=np.zeros(2, dtype=np.int64),
        child_boot_epoch=np.zeros(2, dtype=np.int64),
        alignment=PairAlignment(
            parent_indices=np.arange(2), child_indices=np.arange(2),
            lag_samples=0, report={},
        ),
        contiguous_spans=(slice(0, 2),),
        provenance={"runtime_owner_token": "BOUND_OWNER_TOKEN"},
    )
    selection = CenterPrefixSelection(
        edge=pair.edge,
        chronological_index=2,
        action=pair.action,
        pairs=(pair,),
        current_pair=pair,
        mode="CURRENT_ONLY",
        prequential_prediction_sha256="0" * 64,
        geometry_had_accepted_center_before_current=False,
        selection_token="1" * 64,
        report={},
    )
    before = {
        "current_owned_aligned_pairs": {"token": pair},
        "current_center_prefix_selections": {pair.edge: selection},
    }
    checkpoint = deepcopy(before)
    restored = deepcopy(checkpoint)
    before_pair = checkpoint["current_owned_aligned_pairs"]["token"]
    after_pair = restored["current_owned_aligned_pairs"]["token"]
    assert before_pair.contiguous_spans[0] is not after_pair.contiguous_spans[0]
    assert (
        before_pair.contiguous_spans[0].start,
        before_pair.contiguous_spans[0].stop,
        before_pair.contiguous_spans[0].step,
    ) == (
        after_pair.contiguous_spans[0].start,
        after_pair.contiguous_spans[0].stop,
        after_pair.contiguous_spans[0].step,
    )
    assert np.array_equal(before_pair.parent_acc, after_pair.parent_acc)
    assert before_pair.provenance["runtime_owner_token"] == (
        after_pair.provenance["runtime_owner_token"]
    )
    assert C2PipelineRuntime._canonical_state(restored) == (
        C2PipelineRuntime._canonical_state(checkpoint)
    )


@pytest.fixture(scope="module")
def settings() -> dict:
    path = RUN_DIR / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_010.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))["effective_settings"]
    # Bounded pre-seal owner smokes execute the exact generator output without
    # manufacturing the immutable amendment/seal artifacts. Qualification
    # reaches only the existing-file branch after the active successor seal is
    # legitimately made.
    from tools.amend_c2_p2_prefit_010 import build_effective_settings

    return build_effective_settings(ROOT)


@pytest.fixture(scope="module")
def initial_stochastic_state(settings: dict) -> dict:
    relative = settings["execution_contract"]["initial_stochastic_state_relative_path"]
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def prefit_registry_seal_path() -> Path:
    return RUN_DIR / "P2_PREFIT_REGISTRY_SEAL_010.json"


def _partial_frame_geometry_fixture() -> tuple[
    dict[str, AxisEstimate], dict[str, CenterEstimate],
]:
    points = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "torso": np.array([0.0, 0.0, 0.30]),
        "upper_arm_left": np.array([-0.23, 0.0, 0.38]),
        "forearm_left": np.array([-0.48, 0.0, 0.34]),
        "upper_arm_right": np.array([0.23, 0.0, 0.38]),
        "forearm_right": np.array([0.48, 0.0, 0.34]),
        "thigh_left": np.array([-0.08, 0.0, -0.38]),
        "shank_left": np.array([-0.10, 0.0, -0.82]),
        "thigh_right": np.array([0.08, 0.0, -0.38]),
        "shank_right": np.array([0.10, 0.0, -0.82]),
    }
    joints = {
        "pelvis_torso": np.array([0.0, 0.0, 0.18]),
        "shoulder_left": np.array([-0.10, 0.0, 0.38]),
        "elbow_left": np.array([-0.36, 0.0, 0.35]),
        "shoulder_right": np.array([0.10, 0.0, 0.38]),
        "elbow_right": np.array([0.36, 0.0, 0.35]),
        "hip_left": np.array([-0.06, 0.0, -0.12]),
        "knee_left": np.array([-0.10, 0.0, -0.60]),
        "hip_right": np.array([0.06, 0.0, -0.12]),
        "knee_right": np.array([0.10, 0.0, -0.60]),
    }
    centers = {
        edge: CenterEstimate(
            edge=edge,
            parent=parent,
            child=child,
            joint_to_parent_sensor_m=-(joints[edge] - points[parent]),
            joint_to_child_sensor_m=-(joints[edge] - points[child]),
            covariance_m2=np.eye(6) * 0.04**2,
            report={"focused_partial_frame_fixture": True},
        )
        for edge, parent, child in EDGE_SPECS
    }
    tangent_basis = np.array([[1.0, 0.0], [0.0, 0.0], [0.0, 1.0]])
    axes = {
        edge: AxisEstimate(
            edge=edge,
            parent_axis_sensor=np.array([0.0, 1.0, 0.0]),
            child_axis_sensor=np.array([0.0, 1.0, 0.0]),
            tangent_covariance_rad2=np.eye(4) * np.deg2rad(12.0) ** 2,
            report={
                "parent_tangent_basis_sensor": tangent_basis.tolist(),
                "child_tangent_basis_sensor": tangent_basis.tolist(),
                "focused_partial_frame_fixture": True,
            },
        )
        for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    return axes, centers


def _new_started_guard(settings: dict) -> C2ExecutionGuard:
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    return guard


def test_partial_frame_owner_authenticates_ready_edges_without_placeholder_wear(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    partial_centers = {
        edge: centers[edge]
        for edge in (
            "pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right",
            "elbow_right", "hip_left", "hip_right",
        )
    }
    partial_axes = {edge: axes[edge] for edge in ("elbow_left", "elbow_right")}
    owner = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=_new_started_guard(settings),
    )
    branches = owner.build_online(
        partial_axes, partial_centers,
        chronological_index=6, action="07_elbow_right",
    )
    expected_ready = (
        "pelvis_torso", "shoulder_left", "elbow_left",
        "shoulder_right", "elbow_right",
    )
    assert len(branches) == 16
    assert all(branch.retained for branch in branches)
    assert all(not branch.wear_gross_wrong_hemisphere for branch in branches)
    assert all(branch.wear_log_likelihood == 0.0 for branch in branches)
    assert all(tuple(branch.report["qmt_ready_edges"]) == expected_ready for branch in branches)
    assert all(
        branch.report["partial_hard_wear_rejection_disabled_until_full_geometry"]
        and branch.report["partial_soft_wear_likelihood_suppressed_until_full_geometry"]
        and not branch.report["unresolved_placeholder_wear_used_as_branch_evidence"]
        for branch in branches
    )

    runtime = object.__new__(C2PipelineRuntime)
    runtime._require = lambda *args, **kwargs: None
    runtime._current_index = 6
    runtime._current_action = "07_elbow_right"
    runtime._current_frame_branches = branches
    runtime._current_full_frame_geometry_ready = False
    runtime._current_qmt_ready_edges = expected_ready
    runtime._branch_ids = tuple(branch.branch_id for branch in branches)
    runtime._branch_support = np.ones(len(branches), dtype=bool)
    runtime._runtime_owner_id = "FOCUSED_PARTIAL_FRAME_RUNTIME"
    runtime._current_hard_support_token = None
    runtime.guard = _new_started_guard(settings)
    support = runtime.current_heading_hard_support()
    assert tuple(support["branch_ids"]) == runtime._branch_ids
    assert tuple(support["qmt_ready_edges"]) == expected_ready
    assert support["owner_token"] is not None
    runtime._current_qmt_ready_edges = expected_ready + ("hip_left",)
    with pytest.raises(ClassAGuardViolation):
        runtime._validate_current_hard_support_token(
            runtime._branch_ids[0], str(support["owner_token"]),
        )


def test_partial_frame_owner_runs_official_qmt_for_mature_edge_and_no_updates_unrelated(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    partial_centers = {
        edge: centers[edge]
        for edge in (
            "pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right",
            "elbow_right", "hip_left", "hip_right",
        )
    }
    partial_axes = {edge: axes[edge] for edge in ("elbow_left", "elbow_right")}
    guard = _new_started_guard(settings)
    frame_owner = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=guard,
    )
    branch = frame_owner.build_online(
        partial_axes, partial_centers,
        chronological_index=5, action="06_elbow_left",
    )[0]
    heading = PersistentHeadingOwner(
        settings["heading"], [branch], execution_guard=guard,
        first_chronological_index=5,
    )
    rows = 1801
    time = np.arange(rows, dtype=float) * 0.005
    phase = np.linspace(0.0, 8.0 * np.pi, rows)
    parent_gyro = np.column_stack((0.15 * np.sin(phase), 0.8 * np.cos(phase), 0.1 * np.sin(0.5 * phase)))
    child_gyro = np.column_stack((0.12 * np.sin(phase + 0.2), 0.75 * np.cos(phase + 0.1), 0.08 * np.sin(0.5 * phase)))
    parent_quat = np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (rows, 1))
    child_quat = parent_quat.copy()
    selected = np.arange(rows, dtype=np.int64)
    binding = {
        "schema": "biospur-c2-runtime-owned-heading-span-input-v1",
        "runtime_owner_token": "FOCUSED_PARTIAL_FRAME_PAIR_TOKEN",
        "edge": "elbow_left",
        "chronological_index": 5,
        "action": "06_elbow_left",
        "parent_gyro_sha256": _heading_array_sha256(parent_gyro),
        "child_gyro_sha256": _heading_array_sha256(child_gyro),
        "parent_quaternion_wxyz_sha256": _heading_array_sha256(parent_quat),
        "child_quaternion_wxyz_sha256": _heading_array_sha256(child_quat),
        "common_physical_time_s_sha256": _heading_array_sha256(time),
        "selected_source_row_indices_sha256": _heading_array_sha256(selected),
    }
    result = heading.process_span(
        branch_id=branch.branch_id,
        edge="elbow_left",
        chronological_index=5,
        action="06_elbow_left",
        parent_gyro_sensor=parent_gyro,
        child_gyro_sensor=child_gyro,
        parent_quaternion_world_sensor_wxyz=parent_quat,
        child_quaternion_world_sensor_wxyz=child_quat,
        common_physical_time_s=time,
        selected_source_row_indices=selected,
        owner_input_binding=binding,
    )
    unresolved = heading.record_action_no_update(
        branch_id=branch.branch_id,
        edge="knee_left",
        chronological_index=5,
        action="06_elbow_left",
        base_common_physical_time_s=time,
        cause="UNRELATED_EDGE_FUNCTIONAL_FRAMES_NOT_YET_MATURE",
    )
    assert result.report["qmt_executed"]
    assert result.report["official_callable"] == "qmt.headingCorrection"
    support = heading.factorized_unobserved_nonhinge_support(branch.branch_id)
    assert set(support) == set(NONHINGE_EDGES)
    expected_offsets = np.arange(
        UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT, dtype=float,
    ) * 2.0 * np.pi / UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT
    for edge, rows in support.items():
        assert edge not in HINGE_EDGES
        assert len(rows) == UNOBSERVED_NONHINGE_HEADING_S1_QUADRATURE_COUNT
        assert np.allclose(
            [row["offset_from_carried_coordinate_rad"] for row in rows],
            expected_offsets,
        )
        assert np.isclose(
            sum(row["normalized_factorized_weight"] for row in rows), 1.0,
        )
        assert all(row["official_observation_count"] == 0 for row in rows)
        assert all(not row["official_qmt_or_local_likelihood_used"] for row in rows)
    frozen = heading.frozen_evaluation_state()
    assert set(
        frozen["factorized_unobserved_nonhinge_heading_support"][branch.branch_id]
    ) == set(NONHINGE_EDGES)
    assert frozen["carried_zero_heading_coordinate_is_point_identified"] is False
    assert result.report["frame_tangent_uncertainty_variance_rad2"] > 0.0
    child_axis = np.asarray(branch.sensor_from_segment["forearm_left"]) @ np.array(
        [0.0, 1.0, 0.0]
    )
    np.testing.assert_allclose(
        child_axis,
        float(branch.axis_sign_by_edge["elbow_left"]) * np.array([0.0, 1.0, 0.0]),
        atol=1e-12,
    )
    assert unresolved["qmt_called"] is False


def test_external_nonhinge_circular_prior_binding_is_branch_independent_and_broad(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    guard = _new_started_guard(settings)
    branches = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=guard,
    ).build_online(
        {edge: axes[edge] for edge in ("elbow_left", "elbow_right")},
        {
            edge: centers[edge]
            for edge in (
                "pelvis_torso", "shoulder_left", "elbow_left",
                "shoulder_right", "elbow_right", "hip_left", "hip_right",
            )
        },
        chronological_index=5,
        action="06_elbow_left",
    )[:2]
    owner = PersistentHeadingOwner(
        settings["heading"], branches, execution_guard=guard,
        first_chronological_index=5,
    )
    grid = np.linspace(-np.pi, np.pi, 360, endpoint=False)
    weights = np.exp(0.08 * np.cos(grid - 0.7))
    weights /= np.sum(weights)
    audits = []
    for branch in branches:
        binding = {
            "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
            "branch_id": branch.branch_id,
            "edge": "shoulder_left",
            "chronological_index": 5,
            "action": "06_elbow_left",
            "delta_grid_rad_sha256": _heading_array_sha256(grid),
            "posterior_weights_sha256": _heading_array_sha256(weights),
            "source_audit_sha256": "a" * 64,
        }
        audits.append(owner.bind_external_nonhinge_circular_prior(
            branch_id=branch.branch_id,
            edge="shoulder_left",
            chronological_index=5,
            action="06_elbow_left",
            delta_grid_rad=grid,
            posterior_weights=weights,
            owner_input_binding=binding,
        ))
    assert np.isclose(
        audits[0]["moment_matched_coordinate_rad"],
        audits[1]["moment_matched_coordinate_rad"],
    )
    assert np.isclose(
        audits[0]["moment_matched_variance_rad2"],
        audits[1]["moment_matched_variance_rad2"],
    )
    assert audits[0]["moment_matched_variance_rad2"] > 5.0
    assert all(row["full_s1_grid_remains_authoritative"] for row in audits)
    assert all(row["map_or_argmax_used"] is False for row in audits)
    assert all(row["zero_heading_asserted"] is False for row in audits)
    for branch in branches:
        official = owner._edge_state[(branch.branch_id, "shoulder_left")]
        official.update({
            "delta_rad": 0.35,
            "variance_rad2": 0.2,
            "observation_count": 5,
        })
    later_weights = np.exp(0.35 * np.cos(grid + 0.4))
    later_weights /= np.sum(later_weights)
    later_audits = []
    for branch in branches:
        binding = {
            "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
            "branch_id": branch.branch_id,
            "edge": "shoulder_left",
            "chronological_index": 6,
            "action": "07_elbow_right",
            "delta_grid_rad_sha256": _heading_array_sha256(grid),
            "posterior_weights_sha256": _heading_array_sha256(later_weights),
            "source_audit_sha256": "b" * 64,
        }
        later_audits.append(owner.bind_external_nonhinge_circular_prior(
            branch_id=branch.branch_id,
            edge="shoulder_left",
            chronological_index=6,
            action="07_elbow_right",
            delta_grid_rad=grid,
            posterior_weights=later_weights,
            owner_input_binding=binding,
        ))
        official = owner._edge_state[(branch.branch_id, "shoulder_left")]
        assert official["delta_rad"] == 0.35
        assert official["variance_rad2"] == 0.2
        assert official["observation_count"] == 5
    assert np.isclose(
        later_audits[0]["combined_output_coordinate_rad"],
        later_audits[1]["combined_output_coordinate_rad"],
    )
    assert all(
        row["external_prefix_posterior_is_cumulative_and_replaces_previous_external_factor"]
        and not row["external_posterior_multiplied_as_new_independent_evidence"]
        and not row["official_qmt_state_erased_or_double_counted"]
        for row in later_audits
    )
    duplicate_binding = {
        "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
        "branch_id": branches[0].branch_id,
        "edge": "shoulder_left",
        "chronological_index": 6,
        "action": "07_elbow_right",
        "delta_grid_rad_sha256": _heading_array_sha256(grid),
        "posterior_weights_sha256": _heading_array_sha256(later_weights),
        "source_audit_sha256": "b" * 64,
    }
    with pytest.raises(ValueError, match="exactly once"):
        owner.bind_external_nonhinge_circular_prior(
            branch_id=branches[0].branch_id,
            edge="shoulder_left",
            chronological_index=6,
            action="07_elbow_right",
            delta_grid_rad=grid,
            posterior_weights=later_weights,
            owner_input_binding=duplicate_binding,
        )
    checkpoint = owner.checkpoint()
    assert len(checkpoint["external_nonhinge_circular_prior_records"]) == 4
    owner.restore(checkpoint)
    assert len(owner.audit()["external_nonhinge_circular_prior_records"]) == 4


def test_retrospective_nonhinge_prior_preserves_official_time_varying_deltafilt(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    guard = _new_started_guard(settings)
    branch = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=guard,
    ).build_online(
        {edge: axes[edge] for edge in ("elbow_left", "elbow_right")},
        {
            edge: centers[edge]
            for edge in (
                "pelvis_torso", "shoulder_left", "elbow_left",
                "shoulder_right", "elbow_right", "hip_left", "hip_right",
            )
        },
        chronological_index=5,
        action="06_elbow_left",
    )[0]
    owner = PersistentHeadingOwner(
        settings["heading"], [branch], execution_guard=guard,
        first_chronological_index=5,
    )
    grid = np.linspace(-np.pi, np.pi, 360, endpoint=False)
    source_weights = np.exp(0.2 * np.cos(grid - 0.1))
    target_weights = np.exp(0.4 * np.cos(grid - 0.8))
    source_weights /= np.sum(source_weights)
    target_weights /= np.sum(target_weights)

    records: dict[tuple[int, str], dict] = {}
    for chronological_index, action, weights, audit_hash in (
        (5, "06_elbow_left", source_weights, "a" * 64),
        (6, "07_elbow_right", target_weights, "b" * 64),
    ):
        for edge in NONHINGE_EDGES:
            binding = {
                "schema": "biospur-c2-external-nonhinge-circular-prior-input-v1",
                "branch_id": branch.branch_id,
                "edge": edge,
                "chronological_index": chronological_index,
                "action": action,
                "delta_grid_rad_sha256": _heading_array_sha256(grid),
                "posterior_weights_sha256": _heading_array_sha256(weights),
                "source_audit_sha256": audit_hash,
            }
            records[(chronological_index, edge)] = (
                owner.bind_external_nonhinge_circular_prior(
                    branch_id=branch.branch_id,
                    edge=edge,
                    chronological_index=chronological_index,
                    action=action,
                    delta_grid_rad=grid,
                    posterior_weights=weights,
                    owner_input_binding=binding,
                )
            )

    time = np.arange(4, dtype=float)
    official_trace = np.array([0.15, 0.27, 0.52, 0.41])
    official_variance = np.array([0.8, 0.7, 0.6, 0.5])
    official_count = np.array([1, 2, 3, 4], dtype=np.int64)
    for edge in NONHINGE_EDGES:
        owner._span_records[(branch.branch_id, edge, 5)] = [{
            "record_type": "QMT_OBSERVATION_SPAN",
            "common_physical_time_s": time.copy(),
            "official_qmt_filtered_delta_rad": official_trace.copy(),
            "official_qmt_filtered_variance_rad2": official_variance.copy(),
            "official_qmt_filtered_observation_count": official_count.copy(),
        }]
    edge_names = tuple(edge for edge, _, _ in EDGE_SPECS)
    source_variance = float(
        records[(5, NONHINGE_EDGES[0])]["moment_matched_variance_rad2"]
    )
    trajectory = HeadingTrajectoryResult(
        action="06_elbow_left",
        chronological_index=5,
        common_physical_time_s=time,
        edge_delta_filt_rad={edge: np.zeros(4) for edge in edge_names},
        edge_variance_rad2={
            edge: np.full(4, source_variance + 0.5) for edge in edge_names
        },
        edge_direct_observation_mask={
            edge: np.zeros(4, dtype=bool) for edge in edge_names
        },
        segment_global_delta_rad={
            segment: np.zeros(4)
            for _, parent, child in EDGE_SPECS for segment in (parent, child)
        },
        segment_global_variance_rad2={
            segment: np.zeros(4)
            for _, parent, child in EDGE_SPECS for segment in (parent, child)
        },
        report={},
    )
    derived = owner.derive_retrospective_rooted_trajectory(
        trajectory,
        branch_id=branch.branch_id,
        external_prior_chronological_index=6,
    )
    for edge in NONHINGE_EDGES:
        target_coordinate = float(
            records[(6, edge)]["moment_matched_coordinate_rad"]
        )
        target_near = target_coordinate + 2.0 * np.pi * np.rint(
            (official_trace - target_coordinate) / (2.0 * np.pi)
        )
        target_variance = float(
            records[(6, edge)]["moment_matched_variance_rad2"]
        )
        expected_variance = 1.0 / (
            1.0 / official_variance + 1.0 / target_variance
        )
        expected_coordinate = expected_variance * (
            official_trace / official_variance + target_near / target_variance
        )
        np.testing.assert_allclose(
            derived.edge_delta_filt_rad[edge],
            expected_coordinate,
            atol=1e-12,
            rtol=0.0,
        )
        assert np.ptp(derived.edge_delta_filt_rad[edge]) > 0.0
        np.testing.assert_allclose(
            derived.edge_variance_rad2[edge], expected_variance,
        )
        audit = derived.report["nonhinge_official_deltafilt_composition"][edge]
        assert audit["official_filtered_coordinate_trace_sha256"] == (
            _heading_array_sha256(official_trace)
        )
        assert audit["official_filtered_variance_trace_sha256"] == (
            _heading_array_sha256(official_variance)
        )
        assert audit["maximum_official_observation_count"] == 4
        assert audit["same_online_sum_or_precision_combination_used_row_by_row"] is True
        assert audit["raw_official_deltafilt_bypassed_rating_state_filter"] is False
        assert audit["variance_recovered_by_subtracting_source_external_variance"] is False
    assert derived.report["full_s1_physical_branch_qualification"] is False


def test_unobserved_nonhinge_heading_support_defers_full_body_physical_gate() -> None:
    runtime = object.__new__(C2PipelineRuntime)
    runtime._require = lambda *args, **kwargs: None
    runtime._transition = lambda *args, **kwargs: None
    runtime._current_index = 18
    runtime._current_action = "19_right_arm_forward"
    runtime._current_frame_branches = [SimpleNamespace(branch_id="BRANCH_0")]
    runtime._current_full_frame_geometry_ready = True
    runtime._current_qmt_ready_edges = tuple(edge for edge, _, _ in EDGE_SPECS)
    runtime._current_heading_trajectories = {
        "BRANCH_0": SimpleNamespace(report={
            "unobserved_nonhinge_heading_edges": list(NONHINGE_EDGES),
            "mean_rooted_trajectory_full_body_physical_gate_eligible": False,
        })
    }
    result = runtime.assess_current_physical_candidates()
    assert result == {}
    assert runtime._current_physical_gate_completed is False
    assert runtime._current_physical_input_audit[
        "unobserved_nonhinge_heading_edges_by_branch"
    ] == {"BRANCH_0": list(NONHINGE_EDGES)}
    assert runtime._current_physical_input_audit[
        "factorized_unobserved_nonhinge_heading_support_retained"
    ] is True
    assert runtime._current_physical_input_audit[
        "carried_zero_heading_coordinate_used_as_physical_pose"
    ] is False
    assert runtime._current_physical_input_audit[
        "full_body_hard_physical_rejection_executed"
    ] is False


def test_observed_hinge_qmt_evidence_survives_unknown_full_body_legality(
    settings: dict,
) -> None:
    runtime = object.__new__(C2PipelineRuntime)
    runtime._current_prediction = SimpleNamespace(prior_mean=np.zeros(1))
    runtime._current_index = 15
    runtime._current_action = "16_squat"
    runtime._progressive_dimension = 1
    runtime._progressive_layout = []
    runtime._geometry_owner = SimpleNamespace(
        posterior_centers=lambda: {}, posterior_axes=lambda: {},
    )
    runtime._geometry_factor_decisions = {}
    runtime._accepted_local_centers = {}
    runtime._accepted_local_axes = {}
    runtime._branch_ids = ("BRANCH_0",)
    runtime._branch_support = np.array([True])
    runtime._current_frame_branches = ()
    runtime._current_full_frame_geometry_ready = False
    runtime._current_physical_gate_completed = False
    runtime._current_physical_trajectory_assessments = {}
    runtime._current_physical_soft_log_likelihood = np.zeros(1)
    runtime._current_physical_input_audit = {
        "full_body_physical_legality_status": (
            "UNKNOWN_FACTORIZED_NONHINGE_HEADING_SUPPORT"
        )
    }
    runtime._heading_owner = SimpleNamespace(
        action_branch_log_evidence=lambda **kwargs: {
            "schema": "focused-observed-hinge-qmt-evidence-v1",
            "branch_id": "BRANCH_0",
            "log_evidence": 2.0,
            "edge_evidence": {
                edge: {
                    "regular_official_epoch_count": (
                        int(settings["heading"]["branch_evidence"][
                            "effective_epoch_cap_per_edge"
                        ]) if edge == "elbow_left" else 0
                    ),
                    "contribution": 2.0 if edge == "elbow_left" else 0.0,
                }
                for edge, _, _ in EDGE_SPECS
            },
        },
    )
    runtime.settings = settings
    assembled = runtime._assemble_owner_progressive_inputs()
    assert assembled["branch_log_likelihood"].tolist() == [2.0]
    expected_heading_fraction = 1.0 / len(EDGE_SPECS)
    assert np.isclose(
        assembled["audit"]["official_heading_effective_support_fraction"],
        expected_heading_fraction,
    )
    assert assembled["audit"]["trajectory_legal_branch_fraction"] is None
    assert assembled["audit"]["trajectory_legal_branch_fraction_status"] == (
        "UNKNOWN_CONSERVATIVE_ZERO_NUMERIC_CONTRIBUTION_NOT_HARD_REJECTION"
    )
    evidence = next(
        row for row in assembled["audit"]["branch_evidence"]
        if row.get("schema") == "focused-observed-hinge-qmt-evidence-v1"
    )
    assert evidence[
        "observed_edge_local_qmt_retained_when_full_body_legality_unknown"
    ] is True
    assert evidence["unobserved_nonhinge_support_counted_as_information"] is False


def test_partial_physical_input_is_local_no_update_but_full_illegal_input_hard_rejects(
    settings: dict,
) -> None:
    branch_ids = ("BRANCH_A", "BRANCH_B")

    partial = object.__new__(C2PipelineRuntime)
    partial._require = lambda *args, **kwargs: None
    partial._current_index = 0
    partial._current_action = "00_initial_still"
    partial._current_frame_branches = tuple(
        SimpleNamespace(branch_id=branch_id) for branch_id in branch_ids
    )
    partial._current_full_frame_geometry_ready = False
    partial._current_qmt_ready_edges = ("pelvis_torso",)
    partial._current_heading_trajectories = {}
    partial._current_physical_gate_completed = False
    partial._branch_support = np.ones(2, dtype=bool)
    partial._transition = lambda stage, *, cause: setattr(partial, "_stage", stage)
    partial._owner_derived_physical_prefix_inputs = lambda: (_ for _ in ()).throw(
        AssertionError("incomplete input reached the full-body physical owner")
    )
    support_before = partial._branch_support.copy()
    assert partial.assess_current_physical_candidates() == {}
    np.testing.assert_array_equal(partial._branch_support, support_before)
    assert not partial._current_physical_gate_completed
    assert (
        partial._current_physical_input_audit["status"]
        == "EDGE_LOCAL_QMT_PRESERVED;FULL_NINE_EDGE_PHYSICAL_GATE_UNRESOLVED"
    )
    assert not partial._current_physical_input_audit[
        "full_body_hard_physical_rejection_executed"
    ]

    complete = object.__new__(C2PipelineRuntime)
    complete._require = lambda *args, **kwargs: None
    complete._current_index = 10
    complete._current_action = "11_knee_right_seated"
    complete._current_frame_branches = tuple(
        SimpleNamespace(branch_id=branch_id) for branch_id in branch_ids
    )
    complete._current_full_frame_geometry_ready = True
    complete._current_qmt_ready_edges = tuple(edge for edge, _, _ in EDGE_SPECS)
    complete._current_heading_trajectories = {}
    complete._current_physical_gate_completed = False
    complete._branch_ids = branch_ids
    complete._branch_support = np.ones(2, dtype=bool)
    complete._current_all_physical_candidates_invalid = False
    complete.guard = _new_started_guard(settings)
    segment_count = len(settings["segment_frames"]["wear_authority"]["rows"])
    trajectory = np.tile(np.eye(3), (3, segment_count, 1, 1))
    covariance = np.tile(np.eye(3), (3, segment_count, 1, 1))
    complete._owner_derived_physical_prefix_inputs = lambda: (
        {branch_id: trajectory.copy() for branch_id in branch_ids},
        {branch_id: covariance.copy() for branch_id in branch_ids},
        {branch_id: {"owner": "FOCUSED_FULL_INPUT"} for branch_id in branch_ids},
        {"schema": "focused-complete-physical-input-v1"},
    )
    illegal = SimpleNamespace(
        physically_legal=False,
        rom_log_likelihood=0.0,
        bilateral_log_likelihood=0.0,
        gravity_log_likelihood=0.0,
        soft_total_log_likelihood=0.0,
    )
    complete._fk_owner = SimpleNamespace(
        assess_prefix_trajectory=lambda **kwargs: illegal,
    )
    with pytest.raises(
        ClassAGuardViolation,
        match="ALL_INVALID_PHYSICAL_CANDIDATE_ROLLBACK",
    ):
        complete.assess_current_physical_candidates()
    assert complete._current_all_physical_candidates_invalid


def test_online_full_geometry_delegates_to_identical_full_frame_path(settings: dict) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    online_owner = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=_new_started_guard(settings),
    )
    direct_owner = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=_new_started_guard(settings),
    )
    online = online_owner.build_online(
        axes, centers, chronological_index=16, action="17_final_still",
    )
    direct = direct_owner.build(
        axes, centers, chronological_index=16, action="17_final_still",
    )
    assert [branch.branch_id for branch in online] == [branch.branch_id for branch in direct]
    for first, second in zip(online, direct):
        assert first.retained == second.retained
        assert first.prior_weight == second.prior_weight
        assert first.wear_log_likelihood == second.wear_log_likelihood
        assert np.array_equal(
            first.joint_frame_tangent_covariance_rad2,
            second.joint_frame_tangent_covariance_rad2,
        )
        for segment in first.segment_from_sensor:
            assert np.array_equal(
                first.segment_from_sensor[segment], second.segment_from_sensor[segment],
            )


def test_direct_orientation_avatar_rejects_surface_sensor_as_internal_geometry(
    settings: dict,
) -> None:
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    assert len(profiles) == 5
    assert {row["forearm_left_m"] for row in profiles} == {0.245, 0.260, 0.265}
    assert {row["forearm_right_m"] for row in profiles} == {0.245, 0.260, 0.265}
    assert 0.2625 not in {row["forearm_left_m"] for row in profiles}
    assert any(
        row["forearm_left_m"] != row["forearm_right_m"] for row in profiles
    )
    world = {
        segment: np.eye(3)
        for _, parent, child in EDGE_SPECS
        for segment in (parent, child)
    }
    for profile in profiles:
        with pytest.raises(RuntimeError, match="surface sensor origins"):
            direct_orientation_avatar_fk(
                world_from_segment=world,
                profile=profile,
                pelvis_gauge_position_m=np.zeros(3),
            )


def test_fixed_landmark_proxy_avatar_requires_separate_3d_spine_mapping(
    settings: dict,
) -> None:
    """Surface scalars cannot silently become the viewer's spine vector."""

    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    world = {
        segment: np.eye(3)
        for _, parent, child in EDGE_SPECS
        for segment in (parent, child)
    }
    with pytest.raises(RuntimeError, match="separately owned preregistered 3D"):
        fixed_landmark_proxy_avatar_fk(
            world_from_segment=world,
            profile=profiles[0],
            viewer_gauge_position_m=np.zeros(3),
        )
    synthetic_mapping = {
        "owner": "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING",
        "surface_measurements_are_internal_truth": False,
        "uncertainty_or_sensitivity": "SYNTHETIC_TEST_ONLY_NONZERO",
    }
    result = fixed_landmark_proxy_avatar_fk(
        world_from_segment=world,
        profile=profiles[0],
        graphical_spine_vector_m=np.array([0.08, -0.03, 0.39]),
        graphical_spine_mapping=synthetic_mapping,
        viewer_gauge_position_m=np.zeros(3),
    )
    lengths = {
        name: float(np.linalg.norm(value[1] - value[0]))
        for name, value in result.line_segments_m.items()
    }
    assert lengths["spine_landmark_proxy"] == pytest.approx(
        np.linalg.norm([0.08, -0.03, 0.39])
    )
    assert (
        lengths["shoulder_crossbar_left_landmark_proxy"]
        + lengths["shoulder_crossbar_right_landmark_proxy"]
    ) == pytest.approx(0.400)
    assert (
        lengths["hip_crossbar_left_landmark_proxy"]
        + lengths["hip_crossbar_right_landmark_proxy"]
    ) == pytest.approx(0.335)
    assert lengths["upper_arm_left"] == pytest.approx(0.310)
    assert lengths["forearm_left"] == pytest.approx(0.245)
    assert lengths["thigh_left"] == pytest.approx(0.480)
    assert lengths["shank_left"] == pytest.approx(0.430)
    assert result.report["topology_and_node_degree_assertions_passed"]
    assert result.report["fixed_profile_distance_assertions_passed"]
    assert not result.report[
        "surface_profile_observations_are_internal_anatomical_truth"
    ]
    assert not result.report["functional_center_or_connection_required"]
    assert not result.report["functional_center_mean_used_as_fixed_link_geometry"]
    assert result.report["result_status"] == (
        "FIXED_LANDMARK_PROXY_NON_ANATOMICAL_NOT_PASS"
    )

    observer_b = fixed_landmark_proxy_avatar_fk(
        world_from_segment=world,
        profile=profiles[2],
        graphical_spine_vector_m=np.array([0.08, -0.03, 0.39]),
        graphical_spine_mapping=synthetic_mapping,
    )
    observer_b_lengths = {
        name: float(np.linalg.norm(value[1] - value[0]))
        for name, value in observer_b.line_segments_m.items()
    }
    assert observer_b_lengths["spine_landmark_proxy"] == pytest.approx(
        np.linalg.norm([0.08, -0.03, 0.39])
    )
    assert observer_b_lengths["forearm_left"] == pytest.approx(0.265)
    assert result.report["profile_id"] != observer_b.report["profile_id"]


def test_functional_landmark_proxy_keeps_surface_sensors_off_upright_joint_nodes(
) -> None:
    """Curved-mount extrinsics must not turn anterior sensors into a leaning spine."""

    segment_positions = {
        "pelvis": np.array([0.12, 0.0, 0.0]),
        "torso": np.array([0.18, 0.0, 0.30]),
        "upper_arm_left": np.array([0.08, 0.20, 0.45]),
        "upper_arm_right": np.array([0.08, -0.20, 0.45]),
        "forearm_left": np.array([0.06, 0.20, 0.20]),
        "forearm_right": np.array([0.06, -0.20, 0.20]),
        "thigh_left": np.array([0.10, 0.12, -0.18]),
        "thigh_right": np.array([0.10, -0.12, -0.18]),
        "shank_left": np.array([0.07, 0.12, -0.62]),
        "shank_right": np.array([0.07, -0.12, -0.62]),
    }
    joint_positions = {
        "pelvis_torso": np.array([0.0, 0.0, 0.20]),
        "shoulder_left": np.array([0.0, 0.20, 0.60]),
        "shoulder_right": np.array([0.0, -0.20, 0.60]),
        "elbow_left": np.array([0.0, 0.20, 0.29]),
        "elbow_right": np.array([0.0, -0.20, 0.29]),
        "hip_left": np.array([0.0, 0.12, 0.0]),
        "hip_right": np.array([0.0, -0.12, 0.0]),
        "knee_left": np.array([0.0, 0.12, -0.48]),
        "knee_right": np.array([0.0, -0.12, -0.48]),
    }
    world_from_segment = {segment: np.eye(3) for segment in segment_positions}
    segment_from_sensor = {segment: np.eye(3) for segment in segment_positions}
    segment_from_sensor["pelvis"] = Rotation.from_euler("y", 17.0, degrees=True).as_matrix()
    segment_from_sensor["torso"] = Rotation.from_euler("y", -13.0, degrees=True).as_matrix()
    connections = {}
    for edge, parent, child in EDGE_SPECS:
        parent_segment_vector = joint_positions[edge] - segment_positions[parent]
        child_segment_vector = joint_positions[edge] - segment_positions[child]
        connections[edge] = EdgeConnectionVectors(
            edge=edge,
            parent=parent,
            child=child,
            parent_sensor_to_joint_m=(
                segment_from_sensor[parent].T @ parent_segment_vector
            ),
            child_sensor_to_joint_m=(
                segment_from_sensor[child].T @ child_segment_vector
            ),
            covariance_m2=np.eye(6) * 1e-4,
        )
    profile = {
        "profile_id": "NONZERO_ANTERIOR_SENSOR_OFFSET_GEOMETRY_TEST",
        "upper_arm_left_m": 0.310,
        "upper_arm_right_m": 0.310,
        "forearm_left_m": 0.245,
        "forearm_right_m": 0.245,
        "thigh_left_m": 0.480,
        "thigh_right_m": 0.480,
        "shank_left_m": 0.430,
        "shank_right_m": 0.430,
        "torso_surface_proxy_length_m": 0.280,
    }
    result = landmark_proxy_fk_points(
        root_sensor_position_m=segment_positions["pelvis"],
        world_from_segment=world_from_segment,
        segment_from_sensor=segment_from_sensor,
        connection_vectors_by_edge=connections,
        profile=profile,
    )
    shoulder_mid = 0.5 * (
        result.shared_joint_positions_m["shoulder_left"]
        + result.shared_joint_positions_m["shoulder_right"]
    )
    hip_mid = 0.5 * (
        result.shared_joint_positions_m["hip_left"]
        + result.shared_joint_positions_m["hip_right"]
    )
    assert np.allclose(shoulder_mid, [0.0, 0.0, 0.60], atol=1e-12)
    assert np.allclose(hip_mid, [0.0, 0.0, 0.0], atol=1e-12)
    assert np.allclose(shoulder_mid - hip_mid, [0.0, 0.0, 0.60], atol=1e-12)
    assert np.allclose(
        result.segment_sensor_positions_m["pelvis"], [0.12, 0.0, 0.0], atol=1e-12,
    )
    assert np.allclose(
        result.segment_sensor_positions_m["torso"], [0.18, 0.0, 0.30], atol=1e-12,
    )
    assert not np.allclose(result.segment_sensor_positions_m["pelvis"], hip_mid)
    assert not np.allclose(result.segment_sensor_positions_m["torso"], shoulder_mid)
    assert result.report["surface_sensor_origins_and_shared_joint_nodes_are_distinct"]
    assert not result.report[
        "torso_surface_sensor_separation_m_used_as_internal_axis_or_length"
    ]

    altered_profile = dict(profile, torso_surface_proxy_length_m=0.910)
    altered = landmark_proxy_fk_points(
        root_sensor_position_m=segment_positions["pelvis"],
        world_from_segment=world_from_segment,
        segment_from_sensor=segment_from_sensor,
        connection_vectors_by_edge=connections,
        profile=altered_profile,
    )
    for segment in segment_positions:
        assert np.array_equal(
            result.segment_sensor_positions_m[segment],
            altered.segment_sensor_positions_m[segment],
        )
    for edge in joint_positions:
        assert np.array_equal(
            result.shared_joint_positions_m[edge],
            altered.shared_joint_positions_m[edge],
        )


def test_fresh_renderer_functional_helper_consumes_full_r3_connections(
    settings: dict,
) -> None:
    manifest_path = (
        RUN_DIR / "CONTINUATION_SPRINT/C2_FRESH_CONTINUATION_FROZEN_STATE_003.json"
    )
    arrays_path = manifest_path.with_suffix(".npz")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    branch_id = max(
        manifest["structure"]["physical_trajectory_support"]["16"],
        key=lambda value: (
            float(manifest["structure"]["physical_trajectory_support"]["16"][value][
                "physically_legal"
            ]),
            value,
        ),
    )
    prefix = f"physical_trajectory/16/{branch_id}"
    profile = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])[0]
    with np.load(arrays_path, allow_pickle=False) as arrays:
        result = _functional_landmark_proxy_fk_from_frozen_arrays(
            arrays, prefix=prefix, sample_index=0, profile=profile,
        )
    assert set(result.segment_sensor_positions_m) == {
        segment for _, parent, child in EDGE_SPECS for segment in (parent, child)
    }
    assert set(result.shared_joint_positions_m) == {edge for edge, _, _ in EDGE_SPECS}
    assert result.report["surface_sensor_origins_and_shared_joint_nodes_are_distinct"]
    assert not result.report[
        "torso_surface_sensor_separation_m_used_as_internal_axis_or_length"
    ]
    assert not result.report[
        "functional_center_covariance_propagated_through_viewer_rescaling"
    ]
    assert all(
        np.isfinite(value).all()
        for value in result.segment_sensor_position_covariance_m2.values()
    )


def test_single_joint_surface_lever_is_excluded_and_distal_axis_remains_unresolved(
    settings: dict,
) -> None:
    center_vectors = {
        "pelvis_torso": (np.array([0.0, 0.0, 0.40]), np.array([0.0, 0.0, -0.20])),
        "shoulder_left": (np.array([0.0, 0.20, 0.40]), np.array([0.0, 0.0, 0.20])),
        "shoulder_right": (np.array([0.0, -0.20, 0.40]), np.array([0.0, 0.0, 0.20])),
        "elbow_left": (np.array([0.0, 0.0, -0.11]), np.zeros(3)),
        "elbow_right": (np.array([0.0, 0.0, -0.11]), np.zeros(3)),
        "hip_left": (np.array([0.0, 0.15, -0.10]), np.array([0.0, 0.0, 0.20])),
        "hip_right": (np.array([0.0, -0.15, -0.10]), np.array([0.0, 0.0, 0.20])),
        "knee_left": (np.array([0.0, 0.0, -0.28]), np.zeros(3)),
        "knee_right": (np.array([0.0, 0.0, -0.28]), np.zeros(3)),
    }
    true_longitudinal = Rotation.from_euler("y", 15.0, degrees=True).apply(
        [0.0, 0.0, 1.0]
    )
    radial = Rotation.from_euler("y", 15.0, degrees=True).apply([0.12, 0.0, 0.0])
    oblique_lever = 0.25 * true_longitudinal + radial
    for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        parent, child = center_vectors[edge]
        center_vectors[edge] = (parent, oblique_lever.copy())
    axis_vectors = {
        edge: (np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0]))
        for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    signs = {edge: 1 for edge in axis_vectors}
    nominal_segment_from_sensor = {
        segment: np.eye(3)
        for _, parent, child in EDGE_SPECS
        for segment in (parent, child)
    }
    frames, flags = _construct_sensor_from_segment(
        center_vectors, axis_vectors, signs, nominal_segment_from_sensor,
    )
    naive = oblique_lever / np.linalg.norm(oblique_lever)
    naive_error = float(np.arccos(np.clip(naive @ true_longitudinal, -1.0, 1.0)))
    assert naive_error > 0.0
    for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right"):
        representative = frames[segment][:, 2]
        # The registered quadrature is only a deterministic placeholder.  The
        # injected truth is deliberately not used to choose or validate it.
        assert np.array_equal(representative, np.array([0.0, 0.0, 1.0]))
        assert any(
            "SINGLE_PROXIMAL_JOINT_LEVER_EXCLUDED_FROM_LONGITUDINAL_Z" in value
            for value in flags[segment]
        )
        assert any(
            "LONGITUDINAL_DIRECTION_UNRESOLVED" in value
            for value in flags[segment]
        )
        assert any(
            "DISTAL_ENDPOINT_TUNED_POSE_NOT_AUTHORIZED" in value
            for value in flags[segment]
        )

    changed = {
        edge: (first.copy(), second.copy())
        for edge, (first, second) in center_vectors.items()
    }
    for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right"):
        changed[edge] = (changed[edge][0], changed[edge][1] + np.array([0.40, 0.0, 0.0]))
    changed_frames, _ = _construct_sensor_from_segment(
        changed, axis_vectors, signs, nominal_segment_from_sensor,
    )
    for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right"):
        assert np.array_equal(frames[segment], changed_frames[segment])


def test_full_frame_owner_preserves_hinge_qmt_but_not_unresolved_distal_pose(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    owner = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=_new_started_guard(settings),
    )
    branches = owner.build(
        axes, centers, chronological_index=16, action="17_final_still",
    )
    expected_ready = tuple(edge for edge, _, _ in EDGE_SPECS)
    for branch in branches:
        assert tuple(branch.report["qmt_ready_edges"]) == expected_ready
        assert branch.report["complete_nine_edge_heading_input"]
        assert not branch.report["heading_readiness_is_distal_endpoint_pose_readiness"]
        assert set(
            branch.report["qualified_hinge_axis_qmt_preserved_for_unresolved_distal_segments"]
        ) == set(HINGE_EDGES)
        assert branch.report[
            "unresolved_distal_longitudinal_or_axial_twist_enters_paired_frame_covariance"
        ]
        assert branch.report[
            "hinge_joint_axis_coordinate_invariant_to_distal_longitudinal_placeholder"
        ]
        assert not branch.report[
            "official_qmt_delta_invariance_to_distal_longitudinal_placeholder_claimed"
        ]
        assert not branch.report["complete_nine_edge_frame_geometry"]
        assert not branch.report["distal_longitudinal_axis_qualification_complete"]
        assert not branch.report["distal_endpoint_tuned_pose_render_authorized"]
        for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right"):
            row = branch.report["distal_longitudinal_axis_status_by_segment"][segment]
            assert row["status"] == "UNRESOLVED_SINGLE_JOINT_PLUS_HINGE_INSUFFICIENT"
            assert not row["sole_joint_lever_used_as_longitudinal_axis"]
            assert row["explicit_longitudinal_candidate_support_materialized"]
            assert row["candidate_support_type"] == (
                "LEGACY_DIAGNOSTIC_EIGHT_POINT_QUADRATURE_OF_COMPLETE_S1_"
                "NOT_ACTIVE_POSTERIOR"
            )
            assert row["candidate_count"] == 8
            assert row["active_complete_s1_owner_required"]
            assert not row[
                "diagnostic_quadrature_may_select_map_mean_medoid_or_endpoint_pose"
            ]
            assert not row["tuned_endpoint_render_authorized"]
            support = branch.report[
                "distal_longitudinal_factorized_support_by_segment"
            ][segment]
            assert len(support) == 8
            weights = np.asarray([
                candidate["normalized_factorized_weight"] for candidate in support
            ])
            assert np.all(weights > 0.0)
            assert np.isclose(np.sum(weights), 1.0, rtol=0.0, atol=1e-12)
            assert np.allclose(
                [candidate["azimuth_from_placeholder_rad"] for candidate in support],
                np.arange(8) * np.pi / 4.0,
                rtol=0.0,
                atol=1e-12,
            )
            candidate_z = np.asarray([
                np.asarray(candidate["sensor_from_segment"])[:, 2]
                for candidate in support
            ])
            hinge_axis = np.asarray(support[0]["hinge_axis_sensor"], dtype=float)
            injected_longitudinal = Rotation.from_rotvec(
                hinge_axis * np.deg2rad(15.0)
            ).apply(candidate_z[0])
            nearest = float(np.min(np.arccos(np.clip(
                candidate_z @ injected_longitudinal, -1.0, 1.0,
            ))))
            assert nearest <= np.pi / 8.0 + 1e-12
            assert all(not candidate["sole_joint_lever_used"] for candidate in support)
        assert branch.report[
            "distal_longitudinal_support_factorized_not_global_pose_combinations"
        ]
        assert not branch.report[
            "distal_longitudinal_support_selected_by_pixels_or_pose_truth"
        ]
        assert branch.report["distal_longitudinal_eight_point_quadrature_role"] == (
            "LEGACY_DIAGNOSTIC_AND_CONSERVATIVE_COVARIANCE_ENVELOPE_ONLY"
        )
        assert not branch.report[
            "distal_longitudinal_eight_point_quadrature_is_active_complete_s1_posterior"
        ]
        assert not branch.report[
            "distal_longitudinal_eight_point_quadrature_authorizes_qmt_or_endpoint_pose"
        ]


def test_heading_owner_rejects_silent_frame_coordinate_change_after_edge_state(
    settings: dict,
) -> None:
    axes, centers = _partial_frame_geometry_fixture()
    guard = _new_started_guard(settings)
    branch = SegmentFrameBranchOwner(
        settings["segment_frames"], execution_guard=guard,
    ).build(
        axes, centers, chronological_index=16, action="17_final_still",
    )[0]
    axis_sensor = np.asarray(branch.sensor_from_segment["forearm_left"])[:, 1]
    coordinate_change = Rotation.from_rotvec(axis_sensor * 0.2).as_matrix()
    changed_sensor_from_segment = {
        name: np.asarray(value).copy()
        for name, value in branch.sensor_from_segment.items()
    }
    changed_sensor_from_segment["forearm_left"] = (
        coordinate_change @ changed_sensor_from_segment["forearm_left"]
    )
    changed_segment_from_sensor = {
        name: value.T.copy()
        for name, value in changed_sensor_from_segment.items()
    }
    changed_branch = replace(
        branch,
        sensor_from_segment=changed_sensor_from_segment,
        segment_from_sensor=changed_segment_from_sensor,
    )

    # A coordinate can be installed before its affected edge owns any state.
    unused_owner = PersistentHeadingOwner(
        settings["heading"], [branch], execution_guard=guard,
        first_chronological_index=16,
    )
    unused_owner.update_frame_branches([changed_branch])

    owner = PersistentHeadingOwner(
        settings["heading"], [branch], execution_guard=guard,
        first_chronological_index=16,
    )
    owner.record_action_no_update(
        branch_id=branch.branch_id,
        edge="elbow_left",
        chronological_index=16,
        action="17_final_still",
        base_common_physical_time_s=np.arange(8, dtype=float) * 0.005,
        cause="FOCUSED_COORDINATE_MIGRATION_BOUNDARY",
    )
    with pytest.raises(
        ValueError,
        match="coherent corrected-frame replay or an explicit state-coordinate migration",
    ):
        owner.update_frame_branches([changed_branch])

    # Covariance/report refresh with byte-identical coordinate matrices stays legal.
    owner.update_frame_branches([replace(branch, report={**branch.report, "refresh": True})])


def test_distal_longitudinal_placeholder_does_not_change_qualified_hinge_axis_coordinate() -> None:
    hinge_axis_sensor = np.array([0.0, 1.0, 0.0])
    longitudinal_placeholders = (
        np.array([0.0, 0.0, 1.0]),
        np.array([1.0, 0.0, 1.0]),
        np.array([-1.0, 0.0, 1.0]),
    )
    frames = [
        _frame_from_z_y(value, hinge_axis_sensor, label="distal-placeholder")[0]
        for value in longitudinal_placeholders
    ]
    for sensor_from_segment in frames:
        np.testing.assert_allclose(
            sensor_from_segment @ np.array([0.0, 1.0, 0.0]),
            hinge_axis_sensor,
            atol=1e-12,
        )
    assert not np.array_equal(frames[0], frames[1])
    assert not np.array_equal(frames[0], frames[2])


def test_frozen_direct_avatar_rejects_missing_or_wrong_render_source_delta(
    tmp_path: Path,
) -> None:
    seal = json.loads(
        (RUN_DIR / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json").read_text(
            encoding="utf-8"
        )
    )
    exact_settings = json.loads(
        (ROOT / seal["amendment"]["path"]).read_text(encoding="utf-8")
    )["effective_settings"]
    kwargs = {
        "manifest_path": RUN_DIR / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json",
        "settings": exact_settings,
        "viewer_geometry_product": "DIRECT_ORIENTATION_AVATAR_FK",
        "landmark_proxy_owner_amendment_path": (
            RUN_DIR / "USER_FIXED_MEASURED_GEOMETRY_OWNER_AMENDMENT_003.json"
        ),
    }
    with pytest.raises(
        RuntimeError,
        match="changed frozen-state viewer requires an immutable render-only source delta",
    ):
        render_registered_scientific_triviews(
            **kwargs, output_directory=tmp_path / "missing_delta",
        )
    with pytest.raises(RuntimeError, match="render-only source delta content is inconsistent"):
        render_registered_scientific_triviews(
            **kwargs,
            output_directory=tmp_path / "wrong_delta",
            authorized_renderer_source_delta_path=(
                RUN_DIR / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013_RENDERER_SOURCE_DELTA_001.json"
            ),
        )


def test_factor_quality_empty_and_all_clipped_are_local_no_update() -> None:
    empty = assess_factor_rows(
        np.empty(0, dtype=np.int64), np.empty(0, dtype=np.int64),
        np.empty((0, 3), dtype=np.int16), np.empty((0, 3), dtype=np.int16),
    )
    assert empty.report["local_no_update"]
    raw = np.full((20, 3), 32767, dtype=np.int16)
    clipped = assess_factor_rows(
        np.arange(20, dtype=np.int64) * 5000, np.zeros(20, dtype=np.int64), raw, raw,
    )
    assert clipped.report["local_no_update"]
    assert clipped.report["clipped_rows_excluded"] == 20


def test_synthetic_capture_uses_production_continuous_vqf_owner(
    settings: dict,
) -> None:
    frontend_settings = _settings_with_explicit_synthetic_initial_still(settings)
    scenario = settings["synthetic"]["positive_scenarios"][0]
    sample = generate_physical_pair(
        int(settings["synthetic"]["positive_seeds"][0]),
        model=settings["synthetic"]["generator_model"],
        duration_s=3.0,
        sample_period_s=float(settings["timing"]["sample_period_s"]),
        nonideal=bool(scenario["nonideal"]),
        wear_mode=str(scenario["wear_mode"]),
        parent_dimension_m=float(scenario["parent_dimension_m"]),
        child_dimension_m=float(scenario["child_dimension_m"]),
        asymmetry_fraction=float(scenario["asymmetry_fraction"]),
        excitation_scale=float(scenario["excitation_scale"]),
        observation_level=float(scenario["observation_level"]),
        imperfect_rest_return=bool(scenario["imperfect_rest_return"]),
    )
    calibrated, audit = _production_equivalent_synthetic_orientation_frontend(
        sample,
        settings=frontend_settings,
    )
    assert audit["owner_call_path"] == [
        "orientation.ContinuousVQFState.process:00_initial_still",
        "orientation.ContinuousVQFState.process:02_t_pose",
    ]
    assert audit["vqf_instances_per_node"] == 1
    assert audit["episode_reset_count"] == 0
    assert audit["new_yaw_gauge_count"] == 0
    assert audit["capture_wide_initial_bias_has_nonzero_covariance"]
    assert audit["vqf_residual_bias_consumed"]
    assert audit["vqf_bias_sigma_preserved"]
    assert audit["gravity_norm_informed_broad_calibration_posterior_updated"]
    assert not audit["gravity_vector_or_magnitude_point_estimated"]
    assert not audit["accelerometer_bias_or_scale_point_identifiability_claimed"]
    assert audit[
        "calibration_posterior_predictive_accelerometer_correction_applied"
    ]
    assert not audit[
        "oracle_accelerometer_scale_cross_axis_point_correction_applied"
    ]
    assert not audit["per_action_calibration_profile_created"]
    assert not audit["synthetic_truth_consumed_by_calibration_owner"]
    assert len(calibrated.time_s) == audit["motion_rows"]
    assert calibrated.parent_gyro.shape == (audit["motion_rows"], 3)
    assert calibrated.child_gyro.shape == (audit["motion_rows"], 3)
    assert audit["common_time_intersection_does_not_fabricate_rows"]
    assert audit["prefix_design_owner"] == (
        "EXPLICIT_SYNTHETIC_INITIAL_STILL_DURATION_NOT_COVARIANCE_HORIZON"
    )
    assert audit["initial_still_duration_s"] == pytest.approx(35.05)
    assert audit["motion_rows"] <= 600
    assert audit["motion_rows"] >= 550
    assert audit["capture_wide_calibration_posterior_predictive_mean_applied"]
    assert set(audit["calibration_posterior_snapshot_sha256_by_node"]) == {
        "SYNTHETIC_THIGH_LEFT", "SYNTHETIC_SHANK_LEFT",
    }
    for node, weights in audit["calibration_posterior_component_weights_by_node"].items():
        assert len(weights) == 5
        assert sum(weights) == pytest.approx(1.0)
        assert audit["calibration_posterior_actions_by_node"][node] == [
            "00_initial_still", "02_t_pose",
        ]


def test_class_c_calibration_uncertainty_marginalizes_without_boundary_bypass() -> None:
    zero = np.zeros((6, 6), dtype=float)
    human = np.eye(6) * 0.03**2
    statistical = np.eye(6) * 0.20**2
    report = {
        "solver_success": True,
        "boundary_guard_active": False,
        "multistart_basin_identifiable": True,
        "chronological_prefix_heldin_stability": {"pass": True},
        "gauge_reduced_robust_bread_informed_basis": np.eye(6).tolist(),
        "gauge_reduced_rank": 6,
        "local_parameter_dimension": 6,
        "unknown_boot_transition_pairs": False,
        "information_condition_eligible": False,
        "coherent_nuisance_refit_audit": {"pass": False, "components": []},
        "owner_update_eligible": False,
        "owner_update_mode": "LOCAL_NO_UPDATE_INFORMATION_MAGNITUDE",
        "status": "LOW_INFORMATION_OR_NUMERICAL_GUARD_CANDIDATE",
        "statistical_covariance_including_nullspace_prior_m2": statistical.tolist(),
        "accelerometer_bias_drift_systematic_covariance_m2": zero.tolist(),
        "accelerometer_scale_cross_axis_systematic_covariance_m2": zero.tolist(),
        "gyro_bias_systematic_covariance_m2": zero.tolist(),
        "gyro_bias_drift_systematic_covariance_m2": zero.tolist(),
        "gyro_scale_cross_axis_systematic_covariance_m2": zero.tolist(),
        "accelerometer_gyro_shared_scale_cross_axis_systematic_covariance_m2": zero.tolist(),
        "persistent_clock_systematic_covariance_m2": zero.tolist(),
        "human_worn_systematic_covariance_m2": human.tolist(),
    }
    estimate = CenterEstimate(
        edge="knee_left", parent="thigh_left", child="shank_left",
        joint_to_parent_sensor_m=np.array([0.0, 0.0, 0.40]),
        joint_to_child_sensor_m=np.array([0.0, 0.0, -0.40]),
        covariance_m2=statistical + human,
        report=report,
    )
    snapshot = {
        "semantic_sha256": "a" * 64,
        "accelerometer_scale_cross_axis_remaining_variance_fraction": 1.0,
        "gyroscope_scale_cross_axis_remaining_variance_fraction": 1.0,
    }
    marginalized = marginalize_center_class_c(
        estimate, parent_snapshot=snapshot, child_snapshot=snapshot,
    )
    assert marginalized.report["owner_update_eligible"]
    assert marginalized.report["calibration_class_c_promoted_to_marginal_factor"]
    assert np.trace(marginalized.covariance_m2) >= np.trace(estimate.covariance_m2)

    boundary = replace(
        estimate, report={**report, "boundary_guard_active": True},
    )
    rejected = marginalize_center_class_c(
        boundary, parent_snapshot=snapshot, child_snapshot=snapshot,
    )
    assert not rejected.report["owner_update_eligible"]
    assert not rejected.report["calibration_class_c_promoted_to_marginal_factor"]


def test_capture_wide_residual_bias_update_converges_without_absolute_bias_oscillation(
    settings: dict,
    initial_stochastic_state: dict,
) -> None:
    successor_settings = _settings_with_explicit_synthetic_initial_still(settings)
    owner = CaptureWideCalibrationPosterior(
        initial_stochastic_state,
        successor_settings["calibration_posterior"],
        sample_period_s=0.005,
    )
    node = sorted(initial_stochastic_state["nodes"])[0]
    truth = np.array([0.004, -0.003, 0.002], dtype=float)
    errors = []
    signs = []
    for episode, action in enumerate(("BIAS_EPISODE_0", "BIAS_EPISODE_1")):
        timer = np.arange(64, dtype=np.int64) * 5_000 + episode * 500_000
        boot = np.zeros(64, dtype=np.int64)
        acc = np.repeat(np.array([[0.0, 0.0, 9.80665]]), 64, axis=0)
        gyro = np.repeat(truth[None, :], 64, axis=0)
        corrected_acc, _, prediction = owner.predict_and_correct(
            node,
            action=action,
            time_us=timer,
            boot_epoch=boot,
            accelerometer_mps2=acc,
            gyroscope_rad_s=gyro,
        )
        predictive_snapshot = prediction["predictive_calibration_posterior"]
        assert (
            prediction["predictive_calibration_posterior_semantic_sha256"]
            == predictive_snapshot["semantic_sha256"]
        )
        assert np.asarray(
            predictive_snapshot["mixture_covariance"], dtype=float,
        ).shape == (24, 24)
        applied = np.asarray(prediction["applied_mixture_mean"], dtype=float)[3:6]
        residual = np.repeat((truth - applied)[None, :], 64, axis=0)
        snapshot = owner.update_episode(
            node,
            action=action,
            corrected_accelerometer_mps2=corrected_acc,
            vqf_residual_bias_rad_s=residual,
            vqf_bias_sigma_rad_s=np.full(64, 5e-4),
            vqf_rest_detected=np.zeros(64, dtype=bool),
        )
        estimate = np.asarray(snapshot["mixture_mean"], dtype=float)[3:6]
        errors.append(float(np.linalg.norm(estimate - truth)))
        signs.append(np.sign(estimate))
    assert errors[1] <= errors[0]
    assert np.array_equal(signs[0], np.sign(truth))
    assert np.array_equal(signs[1], np.sign(truth))
    assert snapshot["actions_consumed"] == ["BIAS_EPISODE_0", "BIAS_EPISODE_1"]
    events = owner.audit()["events"]
    updates = [row for row in events if row["kind"] == "CALIBRATION_POSTERIOR_UPDATE"]
    assert all(
        row["vqf_residual_equation"]
        == "RESIDUAL_BIAS_EQUALS_COMPONENT_BIAS_MINUS_APPLIED_MIXTURE_BIAS"
        for row in updates
    )


def test_axis_class_c_adds_between_branch_covariance_and_never_promotes_low_excitation(
    settings: dict,
    initial_stochastic_state: dict,
) -> None:
    successor_settings = _settings_with_explicit_synthetic_initial_still(settings)
    owner = CaptureWideCalibrationPosterior(
        initial_stochastic_state,
        successor_settings["calibration_posterior"],
        sample_period_s=0.005,
    )
    node = sorted(initial_stochastic_state["nodes"])[0]
    snapshot = owner.snapshot(node)
    statistical = np.eye(4) * 0.01**2
    human = np.eye(4) * np.deg2rad(5.0) ** 2
    report = {
        "owner_update_eligible": False,
        "owner_update_mode": "LOCAL_NO_UPDATE_LOW_EXCITATION_OR_RANK_OR_EFFECTIVE_SUPPORT",
        "block_selection_status": "NOISE_STANDARDIZED_THRESHOLD_MET",
        "input_rows_after_selection_before_cap": 400,
        "minimum_selected_observed_rows": 300,
        "effective_support_rows": 100.0,
        "minimum_effective_support_rows": 80.0,
        "hessian_informed_rank": 4,
        "exact_score_hessian_audit": {"pass": True},
        "initial_still_present": False,
        "axis_calibration_nuisance_push_forward_complete": False,
        "axis_calibration_nuisance_audit": {
            "accelerometer_scale_cross_axis": {
                "official_refit_linearization_validation": {
                    "all_rows_pass": False,
                    "rows": [{
                        "nuisance_scale": 1.0,
                        "required_for_owner_update": True,
                        "official_refit_antithetic_tangent_response_rad": [
                            0.02, -0.01, 0.015, -0.005,
                        ],
                        "pass": False,
                    }],
                },
            },
        },
        "statistical_tangent_covariance_rad2": statistical.tolist(),
        "systematic_component_tangent_covariances_rad2": {
            "human_worn": human.tolist(),
        },
    }
    estimate = AxisEstimate(
        edge="knee_left",
        parent_axis_sensor=np.array([1.0, 0.0, 0.0]),
        child_axis_sensor=np.array([1.0, 0.0, 0.0]),
        tangent_covariance_rad2=statistical + human,
        report=report,
    )
    marginalized = marginalize_axis_class_c(
        estimate, parent_snapshot=snapshot, child_snapshot=snapshot,
    )
    assert marginalized.report["owner_update_eligible"]
    assert marginalized.report["calibration_class_c_promoted_to_marginal_factor"]
    between = np.asarray(
        marginalized.report[
            "calibration_posterior_between_branch_tangent_covariance_rad2"
        ],
        dtype=float,
    )
    assert np.trace(between) > 0.0
    assert not marginalized.report[
        "raw_calibration_fraction_variance_relabelled_as_tangent_rad2"
    ]
    assert marginalized.report[
        "calibration_posterior_official_refit_covariance_retained_or_widened"
    ]
    assert np.trace(marginalized.tangent_covariance_rad2) > np.trace(
        estimate.tangent_covariance_rad2
    )

    low_excitation = replace(
        estimate,
        report={
            **report,
            "block_selection_status": "BELOW_NOISE_STANDARDIZED_THRESHOLD",
            "effective_support_rows": 0.0,
        },
    )
    rejected = marginalize_axis_class_c(
        low_excitation, parent_snapshot=snapshot, child_snapshot=snapshot,
    )
    assert not rejected.report["owner_update_eligible"]
    assert not rejected.report["calibration_class_c_promoted_to_marginal_factor"]


def test_successor_registry_generator_owns_synthetic_initial_still_duration() -> None:
    from tools.amend_c2_p2_prefit_010 import build_effective_settings

    generated = build_effective_settings(ROOT)
    synthetic = generated["synthetic"]
    assert synthetic["initial_still_duration_s"] == pytest.approx(35.05)
    assert synthetic["initial_still_duration_provenance"] == (
        "P1_INITIAL_STILL_7010_ROWS_AT_REGISTERED_200_HZ_FOR_"
        "INPUT_DURATION_PLANNING_ONLY"
    )
    assert synthetic["initial_still_duration_role"] == (
        "RESULT_INDEPENDENT_SYNTHETIC_INPUT_DESIGN_DURATION;"
        "NOT_JOINT_CENTER_COVARIANCE_HORIZON;NOT_CALIBRATION_TRUTH"
    )
    assert synthetic["initial_still_latent_calibration_role"] == (
        "GENERATOR_ROWS_ONLY;FORBIDDEN_FROM_ESTIMATOR_INPUT_OR_POINT_"
        "CORRECTION"
    )
    assert synthetic["initial_still_duration_s"] != pytest.approx(
        generated["joint_center"]["accelerometer_bias_drift_horizon_s"]
    )


def test_all_registered_positive_captures_reach_production_frontend_before_factors(
    settings: dict,
) -> None:
    frontend_settings = _settings_with_explicit_synthetic_initial_still(settings)
    dt = float(settings["timing"]["sample_period_s"])
    rows = []
    for seed, scenario in zip(
        settings["synthetic"]["positive_seeds"],
        settings["synthetic"]["positive_scenarios"],
        strict=True,
    ):
        sample = generate_physical_pair(
            int(seed),
            model=settings["synthetic"]["generator_model"],
            duration_s=float(scenario["duration_s"]),
            sample_period_s=dt,
            nonideal=bool(scenario["nonideal"]),
            wear_mode=str(scenario["wear_mode"]),
            parent_dimension_m=float(scenario["parent_dimension_m"]),
            child_dimension_m=float(scenario["child_dimension_m"]),
            asymmetry_fraction=float(scenario["asymmetry_fraction"]),
            excitation_scale=float(scenario["excitation_scale"]),
            observation_level=float(scenario["observation_level"]),
            imperfect_rest_return=bool(scenario["imperfect_rest_return"]),
        )
        frontend, audit = _production_equivalent_synthetic_orientation_frontend(
            sample,
            settings=frontend_settings,
        )
        pair, quality = _pair_with_observation_conditions(
            frontend,
            dt,
            seed=int(seed),
            gap_rows=int(scenario["gap_rows"]),
            duplicate_rows=int(scenario["duplicate_rows"]),
            jitter_us=int(scenario["jitter_us"]),
            clipping_rows=int(scenario["clipping_rows"]),
        )
        assert audit["vqf_instances_per_node"] == 1
        assert audit["episode_reset_count"] == 0
        assert audit["capture_wide_initial_bias_has_nonzero_covariance"]
        assert audit["vqf_residual_bias_consumed"]
        assert audit["vqf_bias_sigma_preserved"]
        assert not audit["per_action_calibration_profile_created"]
        assert not audit["synthetic_truth_consumed_by_calibration_owner"]
        assert len(pair.parent_acc) == len(pair.child_acc)
        assert len(pair.parent_acc) > 0
        assert quality["owner_call_path"][0] == "orientation.assess_factor_rows"
        rows.append({
            "seed": int(seed),
            "frontend_motion_rows": int(audit["motion_rows"]),
            "factor_rows": int(len(pair.parent_acc)),
        })
    assert len(rows) == 6


def test_clock_sensitivity_derivatives_are_block_and_endpoint_local() -> None:
    blocks = [
        {"gyr1": np.zeros((20, 3)), "gyr2": np.ones((20, 3)) * 1000.0},
        {"gyr1": np.ones((20, 3)) * -500.0, "gyr2": np.ones((20, 3)) * 250.0},
    ]
    rms, audit = _blockwise_angular_acceleration_rms(blocks, sample_period_s=0.005)
    assert rms == pytest.approx(0.0)
    assert audit["cross_block_derivative_count"] == 0
    assert audit["parent_to_child_boundary_derivative_count"] == 0


def test_timing_owner_recovers_noncyclic_shifts_without_boundary_bias(
    settings: dict,
) -> None:
    timing = settings["timing"]
    injected_lags = [int(value) for value in settings["synthetic"]["timing_lag_samples"]]
    rows = int(settings["synthetic"]["mutation_fixtures"]["timing"]["signal_rows"])
    dt = float(timing["sample_period_s"])
    maximum_injected = max(abs(value) for value in injected_lags)
    time_s = np.arange(rows + 2 * maximum_injected + 20) * dt
    source = np.column_stack((
        np.sin(0.7 * time_s) + 0.3 * np.sin(2.1 * time_s),
        np.cos(1.1 * time_s + 0.2),
        0.5 * np.sin(1.7 * time_s - 0.4),
    ))
    origin = maximum_injected + 5
    parent = source[origin:origin + rows]
    timing_kwargs = {
        "sample_period_s": dt,
        "maximum_lag_s": float(timing["maximum_lag_s"]),
        "smoothing_window_samples": int(timing["smoothing_window_samples"]),
        "minimum_overlap_s": float(timing["minimum_overlap_s"]),
    }
    for injected_lag in injected_lags:
        child = source[origin - injected_lag:origin - injected_lag + rows]
        aligned = align_pair_by_gyro_energy(parent, child, **timing_kwargs)
        biased = align_pair_by_gyro_energy(
            parent + np.array((0.4, -0.2, 0.1)),
            child + np.array((-0.3, 0.25, -0.15)),
            **timing_kwargs,
        )
        assert aligned.lag_samples == -injected_lag
        assert biased.lag_samples == -injected_lag
        assert aligned.report["candidate_overlap_selected_before_preprocessing"] is True
        assert aligned.report["complete_truncated_signal_boundaries_enter_candidate_score"] is False


def test_production_alignment_aggregates_corresponding_asymmetric_gap_windows(
    settings: dict,
) -> None:
    rows = 2400
    dt = float(settings["timing"]["sample_period_s"])
    base_time_us = 1_000_000 + np.arange(rows, dtype=np.int64) * int(dt * 1e6)
    time_s = np.arange(rows) * dt
    gyro = np.column_stack((
        np.sin(0.7 * time_s) + 0.3 * np.sin(2.1 * time_s),
        np.cos(1.1 * time_s + 0.2),
        0.5 * np.sin(1.7 * time_s - 0.4),
    ))
    parent_keep = np.ones(rows, dtype=bool)
    child_keep = np.ones(rows, dtype=bool)
    parent_keep[400:1000] = False
    child_keep[1400:2000] = False
    parent_time = base_time_us[parent_keep]
    child_time = base_time_us[child_keep]
    parent_span = np.r_[np.zeros(400, dtype=np.int32), np.ones(1400, dtype=np.int32)]
    child_span = np.r_[np.zeros(1400, dtype=np.int32), np.ones(400, dtype=np.int32)]
    nodes = ("thigh_left", "shank_left")
    action = OrientedAction(
        action="ASYMMETRIC_GAP_TIMING_OWNER",
        chronological_index=0,
        time_us_by_node={nodes[0]: parent_time, nodes[1]: child_time},
        derived_boot_epoch_by_node={
            nodes[0]: np.zeros(len(parent_time), dtype=np.int64),
            nodes[1]: np.zeros(len(child_time), dtype=np.int64),
        },
        contiguous_span_id_by_node={nodes[0]: parent_span, nodes[1]: child_span},
        acc_mps2_by_node={
            nodes[0]: np.zeros((len(parent_time), 3)),
            nodes[1]: np.zeros((len(child_time), 3)),
        },
        gyro_rads_by_node={nodes[0]: gyro[parent_keep], nodes[1]: gyro[child_keep]},
        quat_world_sensor_wxyz_by_node={
            nodes[0]: np.tile((1.0, 0.0, 0.0, 0.0), (len(parent_time), 1)),
            nodes[1]: np.tile((1.0, 0.0, 0.0, 0.0), (len(child_time), 1)),
        },
        gap_only_orientation_covariance_rad2_by_node={
            nodes[0]: np.zeros((len(parent_time), 3, 3)),
            nodes[1]: np.zeros((len(child_time), 3, 3)),
        },
        vqf_residual_bias_rad_s_by_node={
            nodes[0]: np.zeros((len(parent_time), 3)),
            nodes[1]: np.zeros((len(child_time), 3)),
        },
        vqf_residual_bias_sigma_rad_s_by_node={
            nodes[0]: np.zeros(len(parent_time)),
            nodes[1]: np.zeros(len(child_time)),
        },
        vqf_rest_detected_by_node={
            nodes[0]: np.zeros(len(parent_time), dtype=bool),
            nodes[1]: np.zeros(len(child_time), dtype=bool),
        },
        audit={"fixture": "ASYMMETRIC_ENDPOINT_GAPS"},
    )
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    with pytest.raises(ValueError, match="no retained clock hypothesis has a co-temporal overlap"):
        aligned_pair(
            action,
            edge="knee_left",
            parent_node=nodes[0],
            child_node=nodes[1],
            timing=settings["timing"],
            clock_state=clock,
            execution_guard=guard,
        )
    node_clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    prior_shift_us = 50_000_000
    node_clock.observe_episode_node_grids(
        action="PRIOR_CAPTURE_WIDE_PREFIX",
        chronological_index=0,
        root_node=nodes[0],
        time_us_by_node={
            nodes[0]: parent_time - prior_shift_us,
            nodes[1]: child_time - prior_shift_us,
        },
        boot_epoch_by_node={
            nodes[0]: np.zeros(len(parent_time), dtype=np.int64),
            nodes[1]: np.zeros(len(child_time), dtype=np.int64),
        },
        contiguous_span_id_by_node={nodes[0]: parent_span, nodes[1]: child_span},
    )
    update = node_clock.observe_episode_node_grids(
        action=action.action,
        chronological_index=1,
        root_node=nodes[0],
        time_us_by_node=action.time_us_by_node,
        boot_epoch_by_node=action.derived_boot_epoch_by_node,
        contiguous_span_id_by_node=action.contiguous_span_id_by_node,
    )
    assert update["prequential_prediction_already_issued"] is True
    node_pair = aligned_pair(
        replace(action, chronological_index=1),
        edge="knee_left",
        parent_node=nodes[0],
        child_node=nodes[1],
        timing=settings["timing"],
        clock_state=node_clock,
        execution_guard=guard,
    )
    node_audit = node_pair.alignment.report["capture_wide_node_clock_prior"]
    assert node_audit["node_clock_hypotheses_retained_before_factor_row_selection"] is True
    assert node_audit["future_or_heldout_used"] is False
    assert [row["hypothesis_id"] for row in node_audit["hypotheses"]] == [
        "START", "MIDPOINT", "END",
    ]
    assert all(
        row["parent_observation_count"] == 2
        and row["child_observation_count"] == 2
        for row in node_audit["hypotheses"]
    )
    assert len(node_pair.alignment.report["retained_clock_hypotheses"]) >= 2
    assert node_pair.alignment.report["between_hypothesis_variance_s2"] > 0.0
    assert node_pair.alignment.report["branch_specific_geometry_refits_per_clock_hypothesis"] is False
    assert node_pair.alignment.report["full_clock_multibranch_geometry_propagation_claimed"] is False
    assert node_pair.alignment.report["diagnostic_not_pass"] is True
    assert node_pair.alignment.report["clock_prior_source"] == (
        "CAPTURE_WIDE_NODE_HYPOTHESES_FOR_EDGE_BOOTSTRAP"
    )
    assert node_pair.alignment.report["rows_across_gap_aligned_or_differentiated"] == 0
    clock.observe(
        edge="knee_left",
        action="PRIOR_UNAMBIGUOUS_SINGLE_SPAN",
        chronological_index=-1,
        reference_time_s=0.0,
        observed_offset_s=0.0,
        observation_sigma_s=float(settings["timing"]["jitter_floor_s"]),
    )
    pair = aligned_pair(
        action,
        edge="knee_left",
        parent_node=nodes[0],
        child_node=nodes[1],
        timing=settings["timing"],
        clock_state=clock,
        execution_guard=guard,
    )
    local = pair.alignment.report["local_correlation_observation"]
    window_audit = pair.alignment.report["corresponding_timing_span_windows"]
    assert local["selected_lag_samples"] == 0
    assert local["independent_longest_endpoint_span_selection_used"] is False
    assert local["centering_or_smoothing_across_span_boundary"] is False
    assert window_audit["corresponding_window_count"] == 3
    assert [span.stop - span.start for span in pair.contiguous_spans] == [400, 400, 400]
    assert pair.alignment.report["rows_across_gap_aligned_or_differentiated"] == 0
    assert pair.alignment.report["clock_prior_source"] == (
        "PERSISTENT_EDGE_AFFINE_STATE_PREFERRED_AFTER_FIRST_EDGE_OBSERVATION"
    )

    transitioned = replace(
        action,
        derived_boot_epoch_by_node={
            nodes[0]: np.zeros(len(parent_time), dtype=np.int64),
            nodes[1]: np.r_[
                np.zeros(1400, dtype=np.int64),
                np.ones(400, dtype=np.int64),
            ],
        },
    )
    with pytest.raises(ValueError, match="unknown boot-transition interval"):
        aligned_pair(
            transitioned,
            edge="knee_left",
            parent_node=nodes[0],
            child_node=nodes[1],
            timing=settings["timing"],
            clock_state=PersistentPairClockState(
                maximum_abs_drift_ppm=float(
                    settings["timing"]["maximum_abs_drift_ppm"]
                ),
                jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
            ),
            execution_guard=guard,
        )


def _axis_pair_fixture(action: str, offset_s: float, rows: int) -> AlignedPair:
    indices = np.arange(rows, dtype=np.int64)
    time_s = offset_s + indices * 0.005
    parent_acc = np.column_stack((
        1.8 * np.sin(indices * 0.11),
        1.2 * np.cos(indices * 0.073),
        9.80665 + 0.9 * np.sin(indices * 0.047 + 0.2),
    ))
    child_acc = np.column_stack((
        1.5 * np.sin(indices * 0.11 + 0.3),
        1.0 * np.cos(indices * 0.073 - 0.2),
        9.80665 + 0.8 * np.sin(indices * 0.047 + 0.5),
    ))
    parent_gyro = np.column_stack((
        1.1 * np.sin(indices * 0.089),
        0.7 * np.cos(indices * 0.061),
        0.5 * np.sin(indices * 0.037 + 0.4),
    ))
    child_gyro = np.column_stack((
        0.9 * np.sin(indices * 0.089 + 0.2),
        0.8 * np.cos(indices * 0.061 - 0.1),
        0.6 * np.sin(indices * 0.037 + 0.6),
    ))
    return AlignedPair(
        edge="knee_left",
        action=action,
        parent_acc=parent_acc,
        child_acc=child_acc,
        parent_gyro=parent_gyro,
        child_gyro=child_gyro,
        parent_observed_time_s=time_s,
        child_observed_time_s=time_s,
        parent_boot_epoch=np.zeros(rows, dtype=np.int64),
        child_boot_epoch=np.zeros(rows, dtype=np.int64),
        alignment=PairAlignment(
            parent_indices=indices,
            child_indices=indices,
            lag_samples=0,
            report={"lag_uncertainty_s": 0.005},
        ),
        contiguous_spans=(slice(0, rows),),
        provenance={"action": action, "chronological_index": 0},
    )


def test_axis_blocks_bind_exact_multi_pair_cluster_identity() -> None:
    block_rows = 20

    blocks = _axis_blocks(
        [
            _axis_pair_fixture("FIRST", 0.0, 2 * block_rows),
            _axis_pair_fixture("SECOND", 10.0, 2 * block_rows),
        ],
        block_rows,
        sample_period_s=0.005,
    )
    assert [block["pair_index"] for block in blocks] == [0, 0, 1, 1]
    assert [block["action"] for block in blocks] == [
        "FIRST", "FIRST", "SECOND", "SECOND",
    ]


def test_hinge_axis_owner_reports_exact_multi_pair_block_provenance(
    settings: dict,
) -> None:
    block_rows = int(settings["hinge_axis"]["selection_block_rows"])
    pairs = [
        _axis_pair_fixture("FIRST", 0.0, block_rows),
        _axis_pair_fixture("SECOND", 10.0, block_rows),
    ]
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    estimate = estimate_hinge_axis_qmt(
        "knee_left",
        pairs,
        settings=settings["hinge_axis"],
        parent_acc_covariance=np.eye(3) * 0.02**2,
        child_acc_covariance=np.eye(3) * 0.02**2,
        parent_gyro_covariance=np.eye(3) * 0.005**2,
        child_gyro_covariance=np.eye(3) * 0.005**2,
        parent_gyro_bias_covariance=np.eye(3) * 0.003**2,
        child_gyro_bias_covariance=np.eye(3) * 0.003**2,
        execution_guard=guard,
    )
    provenance = [
        (
            row["pair_index"], row["action"],
            row["span_index"], row["block_index"],
        )
        for row in estimate.report["selection_block_audit"]
    ]
    assert provenance == [
        (0, "FIRST", 0, 0),
        (1, "SECOND", 0, 0),
    ]


def test_post_qmt_fixture_aligns_each_edge_once_pre_frame_and_reuses_binding() -> None:
    template = _axis_pair_fixture("POST_QMT_STAGE_FIXTURE", 0.0, 8)
    pairs = {
        edge: replace(
            template,
            edge=edge,
            provenance={
                **dict(template.provenance),
                "runtime_owner_token": sha256(
                    f"POST_QMT_STAGE_FIXTURE:{edge}".encode("utf-8")
                ).hexdigest(),
                "owner_binding_payload": {
                    "schema": "biospur-c2-test-runtime-owned-pair-binding-v1",
                    "edge": edge,
                    "action": template.action,
                },
            },
        )
        for edge, _, _ in EDGE_SPECS
    }

    class RecordingRuntime:
        stage = "CURRENT_EPISODE_LOCAL_FACTORS"

        def __init__(self) -> None:
            self.calls: list[str] = []

        def align_current_pair(self, *, edge: str) -> AlignedPair:
            self.calls.append(edge)
            return pairs[edge]

    runtime = RecordingRuntime()
    aligned, alignment_audit = _align_exact_post_qmt_fixture_pairs(runtime)
    expected = [edge for edge, _, _ in EDGE_SPECS]
    assert runtime.calls == expected
    assert alignment_audit["edge_call_order"] == expected
    assert set(alignment_audit["call_count_by_edge"].values()) == {1}
    assert not alignment_audit["private_alignment_owner_called_by_fixture"]

    runtime.stage = "CURRENT_EPISODE_FRAMES_UPDATED"
    consumed = {edge: aligned[edge] for edge in expected}
    reuse = _audit_post_frame_pair_identity_reuse(aligned, consumed)
    assert reuse["all_edges_same_object_and_binding"]
    assert reuse["realignment_or_reingestion_count"] == 0

    substituted = dict(consumed)
    substituted[expected[0]] = replace(consumed[expected[0]])
    rejected = _audit_post_frame_pair_identity_reuse(aligned, substituted)
    assert not rejected["all_edges_same_object_and_binding"]


def test_explicit_sensor_ledger_rows_use_canonical_owner_coverage_class() -> None:
    source = ROOT / "src/biospur_fusion/v0/c2_progressive/synthetic.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    assignment = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "sensor_mutation_cases"
            for target in node.targets
        )
    )
    assert isinstance(assignment.value, ast.Dict)
    explicit_count = 0
    for row in assignment.value.values:
        assert isinstance(row, ast.Dict)
        for key, value in zip(row.keys, row.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "coverage_class"
            ):
                assert isinstance(value, ast.Constant)
                assert value.value == "EXECUTED_OWNER_LEVEL"
                explicit_count += 1
    assert explicit_count == 42


def test_attempt006_low_information_predicates_bind_explicit_owner_no_update() -> None:
    attempt = json.loads(
        (
            RUN_DIR
            / "P2_PREFIT_SYNTHETIC_QUALIFICATION_ATTEMPT_006.json"
        ).read_text(encoding="utf-8")
    )
    summary = attempt["qualification_result"]["summary"]

    center = summary["low_excitation_center_report"]
    center_evidence = _static_low_information_owner_evidence(center)
    assert center_evidence["owner_update_eligible"] is False
    assert center_evidence["information_condition_eligible"] is False
    assert (
        center_evidence["minimum_informed_information_eigenvalue_m2_inv"]
        < center_evidence[
            "required_minimum_informed_information_eigenvalue_m2_inv"
        ]
    )
    assert (
        center_evidence["maximum_informed_observation_sigma_m"]
        > center_evidence["required_maximum_informed_observation_sigma_m"]
    )
    assert center_evidence["multistart_basin_identifiable"] is False
    assert center_evidence["prefix_stability_pass"] is False
    assert center_evidence["prefix_boundary_competitive"] is True
    assert _static_low_information_owner_no_false_pass(center)

    center_owner_promoted = json.loads(json.dumps(center))
    center_owner_promoted["owner_update_eligible"] = True
    assert not _static_low_information_owner_no_false_pass(center_owner_promoted)
    center_information_promoted = json.loads(json.dumps(center))
    center_information_promoted["information_condition_eligible"] = True
    assert not _static_low_information_owner_no_false_pass(
        center_information_promoted
    )
    center_basin_promoted = json.loads(json.dumps(center))
    center_basin_promoted["multistart_basin_identifiable"] = True
    assert not _static_low_information_owner_no_false_pass(center_basin_promoted)

    axis = summary["low_excitation_axis_report"]
    axis_evidence = _near_axis_owner_evidence(axis)
    assert axis_evidence["owner_update_eligible"] is False
    assert axis_evidence["axis_calibration_nuisance_push_forward_complete"] is False
    assert axis_evidence[
        "at_least_one_required_official_refit_component_failed"
    ]
    assert axis_evidence["exact_score_hessian_pass"] is True
    assert (
        axis_evidence["effective_support_rows"]
        >= axis_evidence["minimum_effective_support_rows"]
    )
    assert (
        axis_evidence["hessian_informed_rank"]
        == axis_evidence["local_tangent_parameter_dimension"]
    )
    assert axis_evidence["block_selection_status"] == (
        "NOISE_STANDARDIZED_THRESHOLD_MET"
    )
    assert _near_axis_owner_no_false_pass(axis)

    axis_owner_promoted = json.loads(json.dumps(axis))
    axis_owner_promoted["owner_update_eligible"] = True
    assert not _near_axis_owner_no_false_pass(axis_owner_promoted)
    axis_nuisance_promoted = json.loads(json.dumps(axis))
    axis_nuisance_promoted["axis_calibration_nuisance_push_forward_complete"] = True
    assert not _near_axis_owner_no_false_pass(axis_nuisance_promoted)
    axis_selection_not_met = json.loads(json.dumps(axis))
    axis_selection_not_met["block_selection_status"] = (
        "NOISE_STANDARDIZED_THRESHOLD_NOT_MET"
    )
    assert not _near_axis_owner_no_false_pass(axis_selection_not_met)
    axis_all_refits_promoted = json.loads(json.dumps(axis))
    for component in axis_all_refits_promoted[
        "axis_calibration_nuisance_audit"
    ].values():
        component["official_refit_linearization_validation"][
            "all_rows_pass"
        ] = True
    assert not _near_axis_owner_no_false_pass(axis_all_refits_promoted)


def test_axis_support_centering_removes_static_bias_and_gravity_mean(
    settings: dict,
) -> None:
    gate = numeric_axis_centered_support_transform_gate(settings["hinge_axis"])
    assert gate["pass"]
    assert gate["constant_bias_invariance_pass"]
    assert gate["centered_scale_quadratic_response_pass"]
    assert gate["static_accelerometer_bias_support_variance"] == 0.0
    assert gate["static_gyro_bias_support_variance"] == 0.0
    assert not gate["removed_gravity_or_mean_used_for_scale_support"]


def test_scientific_renderer_reconstructs_two_sided_prefix_geometry() -> None:
    expected = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "torso": np.array([0.0, 0.0, 0.4]),
        "upper_arm_left": np.array([0.0, 0.25, 0.38]),
        "forearm_left": np.array([0.0, 0.55, 0.34]),
        "upper_arm_right": np.array([0.0, -0.25, 0.38]),
        "forearm_right": np.array([0.0, -0.55, 0.34]),
        "thigh_left": np.array([0.0, 0.12, -0.45]),
        "shank_left": np.array([0.0, 0.12, -0.88]),
        "thigh_right": np.array([0.0, -0.12, -0.46]),
        "shank_right": np.array([0.0, -0.12, -0.91]),
    }
    prefix = "physical_trajectory/00/BRANCH"
    arrays = {}
    for segment in expected:
        arrays[f"{prefix}/world_from_segment/{segment}"] = np.eye(3)[None, :, :]
        arrays[f"{prefix}/segment_from_sensor/{segment}"] = np.eye(3)
    for edge, parent, child in EDGE_SPECS:
        joint = 0.5 * (expected[parent] + expected[child])
        arrays[f"{prefix}/connection/{edge}/parent"] = joint - expected[parent]
        arrays[f"{prefix}/connection/{edge}/child"] = joint - expected[child]
    observed, joints, closure = _direct_two_sided_fk_points(
        arrays, prefix=prefix, sample_index=0,
    )
    assert closure <= 1e-12
    for segment in expected:
        assert np.allclose(observed[segment], expected[segment], atol=1e-12)
    for edge, parent, child in EDGE_SPECS:
        expected_joint = 0.5 * (expected[parent] + expected[child])
        assert np.allclose(joints[edge], expected_joint, atol=1e-12)


def _online_branch_owner_settings() -> dict:
    from tools.amend_c2_p2_prefit_021 import build_effective_settings

    return build_effective_settings(ROOT)


def _online_owner_guard(settings: dict) -> C2ExecutionGuard:
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    return guard


def test_online_center_owner_retains_weighted_nominal_prefix_candidates() -> None:
    online = _online_branch_owner_settings()
    interior_a = {
        "result": SimpleNamespace(
            success=True,
            x=np.array([0.10, 0.20, 0.30, -0.10, -0.20, -0.30]),
        ),
        "interior": True,
        "normalized_cost": 1.0,
        "start_index": 0,
    }
    interior_b = {
        "result": SimpleNamespace(
            success=True,
            x=np.array([0.12, 0.18, 0.31, -0.11, -0.19, -0.28]),
        ),
        "interior": True,
        "normalized_cost": 1.5,
        "start_index": 1,
    }
    nonfinite = {
        "result": SimpleNamespace(success=True, x=np.full(6, np.nan)),
        "interior": True,
        "normalized_cost": 0.0,
        "start_index": 2,
    }
    boundary = {
        "result": SimpleNamespace(success=True, x=np.full(6, 0.9)),
        "interior": False,
        "normalized_cost": 0.0,
        "start_index": 3,
    }
    sigma = online["joint_center"]["online_branch_posterior_owner"][
        "incomplete_nuisance_center_sigma_m"
    ]
    mixture = _online_center_candidate_mixture(
        full_trials=[interior_a, nonfinite, boundary],
        prefix_trials=[interior_b],
        informed_observation_covariance_m2=np.eye(6) * 0.01**2,
        nullspace_covariance_m2=np.eye(6) * 0.02**2,
        systematic_covariance_m2=np.eye(6) * 0.03**2,
        enabled=True,
        incomplete_nuisance_sigma_m=sigma,
    )
    candidates = mixture["branches"]
    assert len(candidates) == 2
    assert sum(row["weight"] for row in candidates) == pytest.approx(1.0)
    assert all(row["finite"] and row["numerical_interior"] for row in candidates)
    assert {row["scope"] for row in candidates} == {
        "FULL_CAUSAL_PREFIX", "CHRONOLOGICAL_EARLY_PREFIX",
    }
    assert np.trace(mixture["between_candidate_covariance_m2"]) > 0.0
    assert np.allclose(
        mixture["incomplete_nuisance_covariance_m2"], np.eye(6) * sigma**2,
    )
    assert np.min(np.linalg.eigvalsh(
        mixture["total_covariance_m2"]
    )) >= -1e-12
    assert not mixture["nonfinite_or_boundary_rows_retained_as_online_branches"]

    rejected = _online_center_candidate_mixture(
        full_trials=[nonfinite, boundary],
        prefix_trials=[],
        informed_observation_covariance_m2=np.eye(6),
        nullspace_covariance_m2=np.eye(6),
        systematic_covariance_m2=np.eye(6),
        enabled=True,
        incomplete_nuisance_sigma_m=sigma,
    )
    assert rejected["branches"] == []
    assert not rejected["finite_interior_branch_available"]
    assert np.allclose(rejected["incomplete_nuisance_covariance_m2"], 0.0)


def test_online_axis_owner_retains_tangent_covariance_without_full_refits() -> None:
    online = _online_branch_owner_settings()
    block_rows = int(online["hinge_axis"]["selection_block_rows"])
    pairs = [
        _axis_pair_fixture("FIRST", 0.0, 2 * block_rows),
        _axis_pair_fixture("SECOND", 10.0, 2 * block_rows),
    ]
    estimate = estimate_hinge_axis_qmt(
        "knee_left", pairs,
        settings=online["hinge_axis"],
        parent_acc_covariance=np.eye(3) * 0.02**2,
        child_acc_covariance=np.eye(3) * 0.02**2,
        parent_gyro_covariance=np.eye(3) * 0.005**2,
        child_gyro_covariance=np.eye(3) * 0.005**2,
        parent_gyro_bias_covariance=np.eye(3) * 0.003**2,
        child_gyro_bias_covariance=np.eye(3) * 0.003**2,
        execution_guard=_online_owner_guard(online),
    )
    report = estimate.report
    assert report["online_branch_posterior_owner_enabled"]
    assert report["full_coherent_nuisance_refits_required_for_online_admission"] is False
    assert report["axis_calibration_nuisance_push_forward_complete"] is False
    assert np.trace(np.asarray(
        report["incomplete_nuisance_axis_covariance_rad2"], dtype=float,
    )) > 0.0
    candidates = report["online_axis_candidate_branches"]
    assert candidates
    assert sum(row["weight"] for row in candidates) == pytest.approx(1.0)
    assert report["raw_fraction_squared_relabelled_as_tangent_rad2"] is False


def test_online_axis_boundary_retains_finite_low_information_candidate() -> None:
    finite = {
        "index": 0,
        "cost": 2.0,
        "parent": np.array([1.0, 0.0, 0.0]),
        "child": np.array([0.0, 1.0, 0.0]),
    }
    secondary = {
        "index": 1,
        "cost": 2.5,
        "parent": np.array([0.99, 0.1, 0.0]),
        "child": np.array([0.0, 0.99, 0.1]),
    }
    nonfinite = {
        "index": 2,
        "cost": np.nan,
        "parent": np.array([np.nan, 0.0, 0.0]),
        "child": np.array([0.0, 1.0, 0.0]),
    }
    sigma = 0.75
    retained = _online_axis_candidate_mixture(
        [finite, secondary, nonfinite],
        best_cost=2.0,
        enabled=True,
        initial_still_present=False,
        core_axis_candidate_eligible=False,
        low_information_sigma_rad=sigma,
    )
    assert retained["owner_update_eligible"]
    assert retained["low_information_candidate_retained"]
    assert len(retained["branches"]) == 2
    assert retained["candidate_weight_sum"] == pytest.approx(1.0)
    assert retained["nonfinite_candidate_count"] == 1
    assert np.allclose(
        retained["low_information_systematic_covariance_rad2"],
        np.eye(4) * sigma**2,
    )
    rejected = _online_axis_candidate_mixture(
        [nonfinite],
        best_cost=2.0,
        enabled=True,
        initial_still_present=False,
        core_axis_candidate_eligible=False,
        low_information_sigma_rad=sigma,
    )
    assert not rejected["owner_update_eligible"]
    assert rejected["branches"] == []


def test_real_array_partial_renderer_produces_nonempty_nonanatomical_pixels() -> None:
    online = _online_branch_owner_settings()
    index = 0
    action = "00_initial_still"
    arrays: dict[str, np.ndarray] = {}
    mapping = online["segment_frames"]["wear_authority"][
        "identity_hardware_to_segment"
    ]
    for position, node in enumerate(sorted(mapping)):
        angle = 0.05 * position
        quaternion = np.array([
            np.cos(0.5 * angle), 0.0, 0.0, np.sin(0.5 * angle),
        ])
        prefix = f"orientation/{index:02d}/{node}"
        arrays[f"{prefix}/time_us"] = np.array(
            [1000, 6000], dtype=np.int64,
        ) + position * 1_000_000_000
        arrays[f"{prefix}/quat_world_sensor_wxyz"] = np.vstack((quaternion, quaternion))
        arrays[f"{prefix}/gap_only_covariance_rad2"] = np.repeat(
            (np.eye(3) * 0.01**2)[None, :, :], 2, axis=0,
        )
    edge = "knee_left"
    prefix = f"geometry_checkpoint/{index:02d}/axis/{edge}"
    arrays[f"{prefix}/parent"] = np.array([0.0, 1.0, 0.0])
    arrays[f"{prefix}/child"] = np.array([0.0, 1.0, 0.0])
    arrays[f"{prefix}/covariance"] = np.eye(4) * 0.1**2
    rotated = _rotate_world_from_sensor_wxyz(
        np.array([np.sqrt(0.5), 0.0, 0.0, np.sqrt(0.5)]),
        np.array([1.0, 0.0, 0.0]),
    )
    assert np.allclose(rotated, [0.0, 1.0, 0.0], atol=1e-12)
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    scratch_root = RUN_DIR / "CONTINUATION_SPRINT" / "tmp"
    scratch_root.mkdir(parents=True, exist_ok=True)
    output = Path(tempfile.mkdtemp(prefix="partial-renderer-", dir=scratch_root))
    result = _render_sensor_axis_checkpoint(
        plt=plt,
        arrays=arrays,
        renderer=online["scientific_renderer"],
        segment_frames=online["segment_frames"],
        workspace=ROOT,
        output_directory=output,
        chronological_index=index,
        action=action,
    )
    assert (ROOT / result["path"]).stat().st_size > 0
    assert result["actual_continuous_vqf_quaternion_arrays_rendered"]
    assert result["actual_accepted_functional_axis_arrays_rendered"]
    assert result["accepted_functional_axis_edges"] == [edge]
    assert set(result["selected_row_by_segment"].values()) == {0}
    assert not result["raw_node_timer_epoch_values_used_for_cross_node_nearest_selection"]
    assert not result["common_absolute_physical_timestamp_claimed"]
    assert not result["schematic_origins_are_measured_geometry"]
    assert not result["joint_centers_or_skeleton_rendered"]
    assert not result["qmt_trajectory_rendered"]
    assert result["footer_within_canvas"]
    x0, y0, x1, y1 = result["footer_bbox_pixels"]
    height, width = result["pixel_shape_rgba"][:2]
    assert 0.0 < x0 < x1 < float(width)
    assert 0.0 < y0 < y1 < float(height)
    assert result["status"] == "NON-ANATOMICAL / NOT PASS"


def test_qmt_local_time_preserves_physical_clock_and_exact_rate_compatibility() -> None:
    native_time_us = 8_123_000_000 + np.arange(2001, dtype=np.int64) * 5000
    physical = native_time_us.astype(float) * 1e-6
    local, audit = _qmt_compatible_local_time(physical, data_rate_hz=5.0)
    inferred_rate = 1.0 / np.diff(local)[1]
    assert np.isclose(inferred_rate % 5.0, 0.0) or np.isclose(
        inferred_rate % 5.0, 5.0,
    )
    assert len(local) == len(physical)
    assert np.array_equal(np.arange(len(local)), np.arange(len(physical)))
    assert audit["physical_time_remains_authoritative"]
    assert not audit["rows_resampled_interpolated_or_reordered"]
    assert audit["physical_time_sha256"] == sha256(
        np.ascontiguousarray(physical).view(np.uint8),
    ).hexdigest()
    assert audit["qmt_local_rate_hz"] == pytest.approx(200.0)
    assert audit["quantized_sample_period_us"] == 5000
    assert audit["compatible_rate_multiplier"] == 40
    assert audit["integer_microsecond_rate_exactly_divisible_by_data_rate"]
    assert audit["maximum_native_step_quantization_error_us"] < 1e-3


def test_axis_systematic_component_union_preserves_missing_later_component() -> None:
    old_wide = np.eye(4) * 0.25
    existing, observed = _axis_systematic_component_union(
        {
            "human_worn": np.eye(4) * 0.01,
            "online_low_information_retained_candidate": old_wide,
        },
        {"human_worn": np.eye(4) * 0.02},
    )
    assert set(existing) == set(observed) == {
        "human_worn",
        "online_low_information_retained_candidate",
    }
    assert np.allclose(
        existing["online_low_information_retained_candidate"], old_wide,
    )
    assert np.allclose(
        observed["online_low_information_retained_candidate"], np.zeros((4, 4)),
    )
    retained = _psd_upper_envelope(
        existing["online_low_information_retained_candidate"],
        observed["online_low_information_retained_candidate"],
        label="focused retained optional axis component",
    )
    human_worn = _psd_upper_envelope(
        existing["human_worn"], observed["human_worn"],
        label="focused retained human-worn axis component",
    )
    component_sum = retained + human_worn
    assert np.trace(retained) >= np.trace(old_wide)
    assert np.allclose(
        component_sum,
        sum(
            (
                retained,
                human_worn,
            ),
            start=np.zeros((4, 4)),
        ),
    )


def test_renderer_source_label_requires_exact_fresh_bound_authority() -> None:
    policy = {
        "source_label_policy": {
            "official_qmt_source": "OFFICIAL",
            "synthetic_fixture_source": "SYNTHETIC",
            "official_qmt_label": "OFFICIAL TIME-VARYING QMT ROOTED TRAJECTORY",
            "synthetic_fixture_label": "SYNTHETIC RENDERER FIXTURE / NOT OFFICIAL QMT",
            "official_label_requires_manifest_fresh_verification_pass": True,
            "official_label_required_fresh_verification_schema": (
                "biospur-c2-final-raw-independent-frozen-owner-comparison-v1"
            ),
            "official_label_required_fresh_scope": "FINAL_RAW_RANGE_FULL_FROZEN_PIPELINE",
            "official_label_requires_all_comparisons_and_causal_prefix_pass": True,
            "official_label_requires_distinct_reader_sessions": True,
            "unknown_source_rejected": True,
        }
    }
    label, official = _validated_source_display(
        physical_source="SYNTHETIC", manifest={}, renderer=policy,
    )
    assert label == "SYNTHETIC RENDERER FIXTURE / NOT OFFICIAL QMT"
    assert not official
    with pytest.raises(RuntimeError, match="fresh-bound"):
        _validated_source_display(
            physical_source="OFFICIAL", manifest={}, renderer=policy,
        )
    label, official = _validated_source_display(
        physical_source="OFFICIAL",
        manifest={
            "fresh_verification": {
                "schema": "biospur-c2-final-raw-independent-frozen-owner-comparison-v1",
                "scope": "FINAL_RAW_RANGE_FULL_FROZEN_PIPELINE",
                "pass": True,
                "primary_execution_role": "PRIMARY_CAUSAL",
                "fresh_execution_role": "FRESH_RAW_RECOMPUTATION",
                "primary_reader_session_id": "PRIMARY_READER",
                "fresh_reader_session_id": "FRESH_READER",
                "caller_attested_scientific_booleans_consumed": False,
                "comparisons": {
                    "all_causal_prefixes_and_supported_heading_trajectories": True
                },
                "array_allclose": {"frozen/example": True},
                "causal_prefix_comparison": {"pass": True},
            }
        },
        renderer=policy,
    )
    assert label == "OFFICIAL TIME-VARYING QMT ROOTED TRAJECTORY"
    assert official
    with pytest.raises(RuntimeError, match="unregistered"):
        _validated_source_display(
            physical_source="CALLER_TEXT", manifest={}, renderer=policy,
        )


def test_orientation_replay_export_binds_calibrated_accelerometer_rows() -> None:
    node = "BSFACC"
    count = 4
    accelerometer = np.arange(count * 3, dtype=float).reshape(count, 3) / 10.0
    oriented = OrientedAction(
        action="ACCELEROMETER_EXPORT_OWNER",
        chronological_index=3,
        time_us_by_node={node: 7_000_000 + np.arange(count, dtype=np.int64) * 5_000},
        derived_boot_epoch_by_node={node: np.zeros(count, dtype=np.int64)},
        contiguous_span_id_by_node={node: np.zeros(count, dtype=np.int32)},
        acc_mps2_by_node={node: accelerometer},
        gyro_rads_by_node={node: np.zeros((count, 3))},
        quat_world_sensor_wxyz_by_node={
            node: np.tile(np.array((1.0, 0.0, 0.0, 0.0)), (count, 1)),
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((count, 3, 3)),
        },
        vqf_residual_bias_rad_s_by_node={node: np.zeros((count, 3))},
        vqf_residual_bias_sigma_rad_s_by_node={node: np.zeros(count)},
        vqf_rest_detected_by_node={node: np.zeros(count, dtype=bool)},
        audit={"owner": "CAPTURE_WIDE_CALIBRATED_ORIENTATION"},
    )

    arrays = _owner_authenticated_orientation_replay_arrays(oriented)
    key = "orientation/03/BSFACC/acc_mps2"
    assert np.array_equal(arrays[key], accelerometer)
    assert not np.shares_memory(arrays[key], accelerometer)

    with pytest.raises(RuntimeError, match="node ownership is incomplete"):
        _owner_authenticated_orientation_replay_arrays(
            replace(oriented, acc_mps2_by_node={}),
        )
    nonfinite = accelerometer.copy()
    nonfinite[2, 1] = np.nan
    with pytest.raises(RuntimeError, match="arrays are inconsistent"):
        _owner_authenticated_orientation_replay_arrays(
            replace(oriented, acc_mps2_by_node={node: nonfinite}),
        )


def test_aligned_pair_replay_export_binds_exact_committed_source_maps() -> None:
    parent_indices = np.array([3, 4, 8, 9], dtype=np.int64)
    child_indices = np.array([5, 6, 10, 11], dtype=np.int64)
    parent_acc = np.arange(12, dtype=float).reshape(4, 3)
    child_acc = parent_acc + 0.5
    parent_gyro = parent_acc / 20.0
    child_gyro = child_acc / 20.0

    def digest(value: np.ndarray) -> str:
        return sha256(np.ascontiguousarray(value).view(np.uint8)).hexdigest()

    binding = {
        "edge": "shoulder_left",
        "chronological_index": 4,
        "parent_source_indices_sha256": digest(parent_indices),
        "child_source_indices_sha256": digest(child_indices),
        "parent_acc_sha256": digest(parent_acc),
        "child_acc_sha256": digest(child_acc),
        "parent_gyro_sha256": digest(parent_gyro),
        "child_gyro_sha256": digest(child_gyro),
    }
    alignment = PairAlignment(
        parent_indices=parent_indices,
        child_indices=child_indices,
        lag_samples=2,
        report={"owner": "TIMING_OWNER_FIXTURE"},
    )
    pair = AlignedPair(
        edge="shoulder_left",
        action="05_shoulder_left",
        parent_acc=parent_acc,
        child_acc=child_acc,
        parent_gyro=parent_gyro,
        child_gyro=child_gyro,
        parent_observed_time_s=np.arange(4, dtype=float) * 0.005,
        child_observed_time_s=np.arange(4, dtype=float) * 0.005 + 0.001,
        parent_boot_epoch=np.zeros(4, dtype=np.int64),
        child_boot_epoch=np.zeros(4, dtype=np.int64),
        alignment=alignment,
        contiguous_spans=(slice(0, 2), slice(2, 4)),
        provenance={
            "edge": "shoulder_left",
            "chronological_index": 4,
            "parent_node": "BSFPARENT",
            "child_node": "BSFCHILD",
            "owner_binding_payload": binding,
        },
    )
    arrays = _owner_authenticated_aligned_pair_replay_arrays(
        {4: {"shoulder_left": pair}}
    )
    prefix = "aligned_pair/04/shoulder_left"
    assert np.array_equal(
        arrays[f"{prefix}/parent_source_indices"], parent_indices,
    )
    assert np.array_equal(
        arrays[f"{prefix}/child_source_indices"], child_indices,
    )
    assert np.array_equal(
        arrays[f"{prefix}/contiguous_span_half_open"],
        np.array([[0, 2], [2, 4]], dtype=np.int64),
    )
    assert not np.shares_memory(
        arrays[f"{prefix}/parent_source_indices"], parent_indices,
    )

    substituted = replace(
        pair,
        alignment=replace(
            alignment,
            child_indices=np.array([5, 6, 11, 10], dtype=np.int64),
        ),
    )
    with pytest.raises(RuntimeError, match="array hash changed"):
        _owner_authenticated_aligned_pair_replay_arrays(
            {4: {"shoulder_left": substituted}}
        )


def test_complete_fresh_array_agreement_includes_uncategorized_frozen_state() -> None:
    primary = {
        "geometry/edge": np.array([1.0]),
        "frozen/heading_edge_state/branch:edge": np.array([0.1, 0.2]),
    }
    fresh = {
        "geometry/edge": np.array([1.0]),
        "frozen/heading_edge_state/branch:edge": np.array([0.1, 0.3]),
    }
    keys_equal, comparisons, complete = C2PipelineRuntime._compare_named_arrays(
        primary, fresh, atol=1e-12, rtol=1e-12,
    )
    assert keys_equal
    assert comparisons["geometry/edge"]
    assert not comparisons["frozen/heading_edge_state/branch:edge"]
    assert C2PipelineRuntime._comparison_category(
        "frozen/heading_edge_state/branch:edge"
    ) == "other_scientific_state"
    assert not complete


def test_training_and_heldout_share_sparse_quantile_orientation_uncertainty_owner() -> None:
    node = "BSFTEST"
    count = 100
    time = np.arange(count, dtype=np.int64) * 5_000
    gyro = np.column_stack((np.ones(count), np.zeros((count, 2))))
    oriented = OrientedAction(
        action="synthetic",
        chronological_index=0,
        time_us_by_node={node: time},
        derived_boot_epoch_by_node={node: np.zeros(count, dtype=np.int64)},
        contiguous_span_id_by_node={node: np.zeros(count, dtype=np.int32)},
        acc_mps2_by_node={node: np.zeros((count, 3))},
        gyro_rads_by_node={node: gyro},
        quat_world_sensor_wxyz_by_node={
            node: np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (count, 1))
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((count, 3, 3))
        },
        vqf_residual_bias_rad_s_by_node={node: np.zeros((count, 3))},
        vqf_residual_bias_sigma_rad_s_by_node={node: np.full(count, 0.001)},
        vqf_rest_detected_by_node={node: np.zeros(count, dtype=bool)},
        audit={},
    )
    initial = {
        "nodes": {
            node: {
                "gyro_observation_covariance_rad2_s2": (np.eye(3) * 1e-4).tolist(),
                "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-6).tolist(),
                "accelerometer_observation_covariance_m2_s4": (np.eye(3) * 1e-3).tolist(),
                "gyro_quantization_variance_rad2_s2": 1e-8,
            }
        }
    }
    orientation_settings = {"sample_period_s": 0.005}
    uncertainty = {
        "gyro_white_noise_multiplier": 1.0,
        "initial_bias_correlation_time_s": 30.0,
        "vqf_residual_bias_correlation_time_s": 10.0,
        "accelerometer_tilt_sensitivity_rad_per_mps2": 1.0 / 9.80665,
        "gyro_scale_cross_axis_fraction_sigma": 0.012,
        "clock_timing_sigma_multiplier": 1.0,
    }
    selected = np.array([0, 50, 99], dtype=np.int64)
    timing = np.full(3, 0.001)
    direct, audit = physical_orientation_covariance(
        [oriented], current_action_index=0, segment="pelvis", node=node,
        source_indices=selected, timing_sigma_s=timing,
        initial_stochastic_state=initial,
        initial_stochastic_state_semantic_sha256="synthetic",
        orientation_settings=orientation_settings,
        uncertainty_settings=uncertainty,
    )
    fake_runtime = object.__new__(C2PipelineRuntime)
    fake_runtime._current_index = 0
    fake_runtime._oriented_actions = [oriented]
    fake_runtime._initial_stochastic_state = initial
    fake_runtime._initial_stochastic_state_semantic_sha256 = "synthetic"
    fake_runtime.settings = {
        "orientation": orientation_settings,
        "physical_candidates": {"orientation_uncertainty": uncertainty},
    }
    runtime_owned, runtime_audit = C2PipelineRuntime._physical_orientation_covariance(
        fake_runtime,
        segment="pelvis", node=node,
        source_indices=selected, timing_sigma_s=timing,
    )
    assert np.array_equal(direct, runtime_owned)
    assert audit == runtime_audit
    durations = [row["observed_duration_s"] for row in audit["components"]]
    assert durations == pytest.approx([0.005, 0.255, 0.5])
    assert audit["sparse_selected_rows_treated_as_contiguous_five_ms_samples"] is False
    assert np.trace(direct[2]) > np.trace(direct[1]) > np.trace(direct[0])


def test_progressive_data_rank_chronology_freeze_and_fresh(settings: dict) -> None:
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    state = ProgressiveCalibrationState(
        2, execution_guard=guard,
        initial_sigma=settings["progressive"]["initial_sigma"], branch_count=2,
        chronological_actions=settings["execution_contract"]["chronological_actions"],
        rank_relative_tolerance=settings["progressive"]["rank_relative_tolerance"],
        fresh_absolute_tolerance=settings["progressive"]["fresh_absolute_tolerance"],
        fresh_relative_tolerance=settings["progressive"]["fresh_relative_tolerance"],
    )
    first = state.ingest_episode(
        chronological_index=0, action=settings["execution_contract"]["chronological_actions"][0],
        observation=np.zeros(2), observation_covariance=np.eye(2),
        data_information=np.zeros((2, 2)), branch_log_likelihood=[0.0, 0.0], physical_validity=0.0,
    )
    assert first.data_information_rank == 0
    with pytest.raises(RuntimeError, match="all 19"):
        state.freeze_fit()
    second = state.ingest_episode(
        chronological_index=1, action=settings["execution_contract"]["chronological_actions"][1],
        observation=np.ones(2), observation_covariance=np.eye(2),
        data_information=np.eye(2), branch_log_likelihood=[0.0, 0.0], physical_validity=1.0,
    )
    assert second.data_information_rank == 2
    assert state.fresh_recompute_and_compare()["pass"]


def test_center_gap_boundary_cannot_create_update_eligibility(settings: dict) -> None:
    gate = _numeric_center_gap_eligibility_gate(settings)
    assert gate["pass"]
    assert gate["gap_free"]["complete_center_blocks"] == 29
    assert gate["one_gap"]["complete_center_blocks"] == 29
    assert (
        gate["gap_free"]["owner_update_eligible"]
        == gate["one_gap"]["owner_update_eligible"]
    )
    assert gate["gap_free"]["rows_differentiated_or_capped_across_gap"] == 0
    assert gate["one_gap"]["rows_differentiated_or_capped_across_gap"] == 0
    assert gate["gap_free"]["matched_gap_local_acc_gyro_alpha_preprocessing"]
    assert gate["one_gap"]["matched_gap_local_acc_gyro_alpha_preprocessing"]
    assert not gate["gap_free"]["p1_covariance_reduced_by_smoothing"]
    assert not gate["one_gap"]["p1_covariance_reduced_by_smoothing"]
    assert gate["gap_free"]["cross_prefix_heldin_filter_support_rows"] == 0
    assert gate["one_gap"]["cross_prefix_heldin_filter_support_rows"] == 0
    assert gate["gap_free"]["cross_complete_block_filter_support_rows"] == 0
    assert gate["one_gap"]["cross_complete_block_filter_support_rows"] == 0
    assert not gate["gap_free"][
        "filtered_rows_counted_as_more_information_than_raw_rows"
    ]
    assert not gate["one_gap"][
        "filtered_rows_counted_as_more_information_than_raw_rows"
    ]
    assert gate["gap_free"]["serial_correlation_variance_envelope_multiplier"] >= 1.0
    assert gate["one_gap"]["serial_correlation_variance_envelope_multiplier"] >= 1.0
    assert gate["gap_free"]["kernel_covariance_envelope_psd_dominates"]
    assert gate["one_gap"]["kernel_covariance_envelope_psd_dominates"]
    assert gate["gap_free"]["retained_to_raw_complete_block_row_ratio"] == 0.9
    assert gate["one_gap"]["retained_to_raw_complete_block_row_ratio"] == 0.9
    for row in (gate["gap_free"], gate["one_gap"]):
        assert row["pair_clock_offset_convention"] == (
            "POSITIVE_OFFSET_EVALUATES_CHILD_LATER_AT_FIXED_PARENT_PHYSICAL_TIME"
        )
        assert row["systematic_clock_nuisance_jacobian"] == (
            "SIGNED_NEGATIVE_BLOCK_LOCAL_TIME_DERIVATIVE_OF_CORRECTED_CHILD_NORM"
        )
        assert not row["unsigned_clock_magnitude_used_as_systematic_direction"]
        assert row["signed_clock_gradient_negative_row_count"] > 0
        assert row["signed_clock_gradient_positive_row_count"] > 0
        assert row["signed_clock_gradient_cross_block_or_gap_derivative_count"] == 0
    assert "CENTER_SIGNED_PAIR_CLOCK_NUISANCE" in settings[
        "synthetic"
    ]["mandatory_sensor_and_numerical_mutations"]


@pytest.fixture(scope="module")
def center_stochastic_gate(settings: dict) -> dict:
    return _numeric_center_gyro_stochastic_sensitivity_gate(settings)


@pytest.fixture(scope="module")
def center_coherent_nuisance_gate(settings: dict) -> dict:
    return _numeric_registered_center_coherent_nuisance_mutation_gate(settings)


def test_center_physical_time_owner_preserves_known_gap_and_rejects_unknown_epoch(
    settings: dict,
) -> None:
    gate = numeric_center_physical_time_ownership_gate()
    expected = {
        "CENTER_PHYSICAL_TIME_SAME_BOOT_GAP_PRESERVED",
        "CENTER_PHYSICAL_TIME_UNKNOWN_EPOCH_LOCAL_NO_UPDATE",
    }
    assert expected <= set(settings["synthetic"][
        "mandatory_sensor_and_numerical_mutations"
    ])
    assert gate["pass"]
    assert gate["equal_retained_row_count"]
    assert gate["different_known_same_boot_gap_changes_drift_time"]
    assert gate["same_boot_cross_pair_boundary_is_trustworthy"]
    assert gate["cap_did_not_synthesize_physical_time"]
    assert gate["permutation_did_not_change_physical_time_values"]
    assert gate["unknown_epoch_or_reset_local_no_update"]
    assert not gate["progressive_unknown_interval_floor_consumption_proven"]


def test_causal_center_prefix_executes_multi_pair_clusters_once_and_excludes_failed_factor(
    settings: dict,
) -> None:
    actions = tuple(settings["execution_contract"]["chronological_actions"])
    edge = "pelvis_torso"
    route = EDGE_ACTIONS[edge]
    block_rows = int(settings["joint_center"]["selection_block_rows"])

    def pair(action: str, phase: float) -> AlignedPair:
        index = actions.index(action)
        sample = np.arange(block_rows, dtype=float)
        time = index * 10.0 + sample * 0.005
        gyro = np.column_stack((
            0.6 * np.sin(0.03 * sample + phase),
            0.4 * np.cos(0.02 * sample + phase),
            0.2 * np.sin(0.05 * sample - phase),
        ))
        parent_acc = np.column_stack((
            0.5 * np.sin(0.02 * sample + phase),
            0.3 * np.cos(0.04 * sample),
            9.80665 + 0.2 * np.sin(0.01 * sample),
        ))
        child_acc = parent_acc + np.column_stack((
            0.02 * np.cos(0.03 * sample),
            0.01 * np.sin(0.05 * sample),
            0.01 * np.cos(0.02 * sample),
        ))
        token = sha256(
            f"PREFIX_TEST:{edge}:{index}:{action}".encode("utf-8")
        ).hexdigest()
        indices = np.arange(block_rows, dtype=np.int64)
        return AlignedPair(
            edge=edge,
            action=action,
            parent_acc=parent_acc,
            child_acc=child_acc,
            parent_gyro=gyro,
            child_gyro=0.95 * gyro,
            parent_observed_time_s=time,
            child_observed_time_s=time,
            parent_boot_epoch=np.zeros(block_rows, dtype=np.int64),
            child_boot_epoch=np.zeros(block_rows, dtype=np.int64),
            alignment=PairAlignment(
                parent_indices=indices,
                child_indices=indices,
                lag_samples=0,
                report={"lag_uncertainty_s": 0.005},
            ),
            contiguous_spans=(slice(0, block_rows),),
            provenance={
                "chronological_index": index,
                "action": action,
                "runtime_owner_token": token,
                "source_role": "TRAINING_RANGE",
                "heldout": False,
            },
        )

    def owner() -> CausalCenterPrefixOwner:
        guard = C2ExecutionGuard(settings)
        guard.begin_capture("C2")
        return CausalCenterPrefixOwner(
            settings["joint_center"]["causal_historical_prefix_owner"],
            chronological_actions=actions,
            edge_actions=EDGE_ACTIONS,
            execution_guard=guard,
        )

    first, second, third = (pair(action, position) for position, action in enumerate(route))
    prefix = owner()
    first_selection = prefix.select(
        edge=edge,
        current_pair=first,
        chronological_index=actions.index(route[0]),
        action=route[0],
        prequential_prediction_sha256="1" * 64,
        geometry_has_accepted_center=False,
    )
    prefix.commit(
        first_selection,
        estimator_owner_update_eligible=False,
        geometry_update_accepted=False,
        estimator_completed=True,
        retain_for_future_prefix=True,
    )
    second_selection = prefix.select(
        edge=edge,
        current_pair=second,
        chronological_index=actions.index(route[1]),
        action=route[1],
        prequential_prediction_sha256="2" * 64,
        geometry_has_accepted_center=False,
    )
    assert second_selection.pairs == (first, second)
    assert second_selection.report["pair_count_consumed_by_estimator"] == 2
    assert len(second_selection.report["ordered_prefix_membership_sha256"]) == 64
    noise = settings["synthetic"]["estimator_input_noise"]
    estimate = estimate_joint_center_pair_local(
        edge,
        "pelvis",
        "torso",
        second_selection.pairs,
        settings=settings["joint_center"],
        parent_acc_covariance=np.eye(3) * float(
            noise["accelerometer_sigma_mps2"]
        ) ** 2,
        child_acc_covariance=np.eye(3) * float(
            noise["accelerometer_sigma_mps2"]
        ) ** 2,
        parent_gyro_observation_covariance=np.eye(3) * float(
            noise["gyroscope_sigma_rads"]
        ) ** 2,
        child_gyro_observation_covariance=np.eye(3) * float(
            noise["gyroscope_sigma_rads"]
        ) ** 2,
        parent_gyro_bias_covariance=np.eye(3) * float(
            noise["gyroscope_bias_sigma_rads"]
        ) ** 2,
        child_gyro_bias_covariance=np.eye(3) * float(
            noise["gyroscope_bias_sigma_rads"]
        ) ** 2,
        execution_guard=prefix.execution_guard,
    )
    cluster_rows = estimate.report["pair_block_cluster_identity_rows"]
    assert {row["pair_index"] for row in cluster_rows} == {0, 1}
    assert estimate.report["per_action_pair_cluster_identity_preserved"]
    assert not estimate.report["historical_rows_treated_as_iid_after_concatenation"]
    prefix.commit(
        second_selection,
        estimator_owner_update_eligible=False,
        geometry_update_accepted=False,
        estimator_completed=True,
        retain_for_future_prefix=True,
    )

    failed = owner()
    failed_selection = failed.select(
        edge=edge,
        current_pair=first,
        chronological_index=actions.index(route[0]),
        action=route[0],
        prequential_prediction_sha256="3" * 64,
        geometry_has_accepted_center=False,
    )
    failed.commit(
        failed_selection,
        estimator_owner_update_eligible=False,
        geometry_update_accepted=False,
        estimator_completed=False,
        retain_for_future_prefix=False,
    )
    after_failed = failed.select(
        edge=edge,
        current_pair=second,
        chronological_index=actions.index(route[1]),
        action=route[1],
        prequential_prediction_sha256="4" * 64,
        geometry_has_accepted_center=False,
    )
    assert after_failed.pairs == (second,)
    assert failed.audit()["immutable_seen_evidence"][edge][0][
        "retained_for_future_estimator_prefix"
    ] is False

    mutation = owner()
    mutation_selection = mutation.select(
        edge=edge,
        current_pair=first,
        chronological_index=actions.index(route[0]),
        action=route[0],
        prequential_prediction_sha256="5" * 64,
        geometry_has_accepted_center=False,
    )
    with pytest.raises(ClassAGuardViolation) as failure:
        mutation.commit(
            mutation_selection,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=False,
            retain_for_future_prefix=True,
        )
    assert failure.value.code == "CENTER_PREFIX_FAILED_FACTOR_REINGESTION"

    token_mutation = owner()
    token_first = token_mutation.select(
        edge=edge,
        current_pair=first,
        chronological_index=actions.index(route[0]),
        action=route[0],
        prequential_prediction_sha256="a" * 64,
        geometry_has_accepted_center=False,
    )
    token_mutation.commit(
        token_first,
        estimator_owner_update_eligible=False,
        geometry_update_accepted=False,
        estimator_completed=True,
        retain_for_future_prefix=True,
    )
    token_second = token_mutation.select(
        edge=edge,
        current_pair=second,
        chronological_index=actions.index(route[1]),
        action=route[1],
        prequential_prediction_sha256="b" * 64,
        geometry_has_accepted_center=False,
    )
    stale_token_membership = replace(
        token_second,
        pairs=(first, first, second),
    )
    token_mutation._pending[edge] = stale_token_membership
    with pytest.raises(ClassAGuardViolation) as token_failure:
        token_mutation.commit(
            stale_token_membership,
            estimator_owner_update_eligible=False,
            geometry_update_accepted=False,
            estimator_completed=True,
            retain_for_future_prefix=True,
        )
    assert token_failure.value.code == (
        "CENTER_PREFIX_MEMBERSHIP_OR_ORDER_TOKEN_SUBSTITUTION"
    )

    accepted = owner()
    accepted_first = accepted.select(
        edge=edge,
        current_pair=first,
        chronological_index=actions.index(route[0]),
        action=route[0],
        prequential_prediction_sha256="6" * 64,
        geometry_has_accepted_center=False,
    )
    accepted.commit(
        accepted_first,
        estimator_owner_update_eligible=False,
        geometry_update_accepted=False,
        estimator_completed=True,
        retain_for_future_prefix=True,
    )
    accepted_second = accepted.select(
        edge=edge,
        current_pair=second,
        chronological_index=actions.index(route[1]),
        action=route[1],
        prequential_prediction_sha256="7" * 64,
        geometry_has_accepted_center=False,
    )
    accepted.commit(
        accepted_second,
        estimator_owner_update_eligible=True,
        geometry_update_accepted=True,
        estimator_completed=True,
        retain_for_future_prefix=False,
    )
    accepted_third = accepted.select(
        edge=edge,
        current_pair=third,
        chronological_index=actions.index(route[2]),
        action=route[2],
        prequential_prediction_sha256="8" * 64,
        geometry_has_accepted_center=True,
    )
    assert accepted_third.pairs == (third,)
    contribution_counts = [
        row["information_contribution_count"]
        for row in accepted.audit()["immutable_seen_evidence"][edge]
    ]
    assert contribution_counts == [1, 1]


def test_center_full_owner_publishes_physical_time_before_eligibility_or_refits(
    settings: dict,
) -> None:
    gate = _numeric_center_full_owner_physical_time_gate(settings)
    assert gate["pass"]
    assert gate["coverage_class"] == "EXECUTED_FULL_ESTIMATOR_OWNER_LEVEL"
    assert gate["equal_retained_rows"]
    assert gate["known_gap_physical_time_hashes_distinct"]
    assert gate["known_gap_drift_stress_hashes_distinct"]
    assert gate["known_gap_cross_boundary_differentiation_count"] == [0, 0]
    assert len(set(gate["sensor_arrays_sha256_by_call"].values())) == 1
    for row in gate["known_same_boot_gap_calls"]:
        provenance = row["physical_time_provenance_diagnostic"]
        assert provenance["role"] == "RESULT_INDEPENDENT_PROVENANCE_DIAGNOSTIC_ONLY"
        assert provenance["computed_before_coherent_nuisance_refit_loop"]
        assert provenance["available_when_nominal_factor_is_local_no_update"]
        assert not provenance["may_promote_owner_update"]
        assert row["rows_differentiated_or_capped_across_gap"] == 0
    reset = gate["unknown_reset"]
    assert not reset["owner_update_eligible"]
    assert not reset["elapsed_continuity_invented"]
    assert reset["physical_time_provenance_diagnostic"][
        "unknown_boot_transition_local_no_update"
    ]
    assert not reset["physical_time_provenance_diagnostic"][
        "cross_epoch_elapsed_continuity_invented"
    ]


def test_center_coherent_nuisance_refit_mutations_reach_real_owner(
    settings: dict,
    center_coherent_nuisance_gate: dict,
) -> None:
    gate = center_coherent_nuisance_gate
    expected = {
        "CENTER_COHERENT_NUISANCE_REFIT_LOCAL_NO_UPDATE",
        "CENTER_COHERENT_NUISANCE_ANTITHETIC_MIDPOINT_GATE",
        "CENTER_CLOCK_REFIT_SYMMETRIC_INTERIOR_NO_CLAMP",
        "CENTER_COHERENT_NUISANCE_ORDINARY_FAILURE_RETAINED",
        "CENTER_COHERENT_NUISANCE_UNIT_RADIUS_FULL_SPAN_COVERAGE",
    }
    assert expected <= set(settings["synthetic"][
        "mandatory_sensor_and_numerical_mutations"
    ])
    assert gate["pass"]
    assert gate["fragility_no_promotion"]["pass"]
    assert not gate["fragility_no_promotion"]["final_owner_update_eligible"]
    assert gate["antithetic_midpoint_gate"]["pass"]
    assert any(
        row["observed_midpoint_shift_m"] is not None
        and row["observed_midpoint_shift_m"] > row["midpoint_limit_m"]
        and not row["observed_direction_pass"]
        for row in gate["antithetic_midpoint_gate"]["rows"]
    )
    clock = gate["symmetric_clock_interior"]
    assert clock["pass"]
    assert clock["signed_refit_count"] == 36
    assert clock["retained_row_counts"] == [5824]
    assert clock["guarded_endpoint_row_counts"] == [176]
    assert clock["endpoint_clamped_or_repeated_rows"] == 0
    assert clock["cross_block_or_gap_interpolation_count"] == 0
    failure = gate["ordinary_construction_failure_retention"]
    assert failure["pass"]
    assert failure["retained_failed_signed_refit_count"] == 36
    assert not failure["final_owner_update_eligible"]
    assert failure["next_component_direction_count"] == 18
    assert not failure["product_owner_state_restore_required"]
    assert not failure["mutation_fixture_performed_product_owner_state_restore"]
    assert failure[
        "mutation_fixture_restored_injected_numpy_callable_after_owner_return"
    ]
    assert gate["unit_radius_full_span"]["pass"]
    assert all(
        row["unit_radius_exact"] and row["full_span_before_cycle"]
        for row in gate["unit_radius_full_span"]["rows"]
    )
    assert not gate["attempt_125_or_external_artifact_counted_as_evidence"]


def test_center_consumes_gyro_stochastic_state_in_weights_and_systematic_uncertainty(
    settings: dict,
    center_stochastic_gate: dict,
) -> None:
    gate = center_stochastic_gate
    assert "CENTER_GYRO_STOCHASTIC_UNCERTAINTY_OMISSION" in settings[
        "synthetic"
    ]["mandatory_sensor_and_numerical_mutations"]
    assert gate["pass"]
    assert gate[
        "injected_gyro_observation_and_bias_covariance_multipliers"
    ] == [0.25, 1.0, 4.0]
    sigmas = [
        row["residual_sigma_rms_mps2"]
        for row in gate["fixed_linearization_outputs"]
    ]
    assert len(sigmas) == 4
    assert sigmas[0] < sigmas[1] < sigmas[2] < sigmas[3]
    information = [
        row["robust_bread_information_trace_m2_inv"]
        for row in gate["fixed_linearization_outputs"]
    ]
    assert information[0] > information[1] > information[2] > information[3]
    systematic = [
        row["systematic_covariance_trace_m2"]
        for row in gate["fixed_linearization_outputs"]
    ]
    total = [
        row["total_model_covariance_trace_m2"]
        for row in gate["fixed_linearization_outputs"]
    ]
    assert systematic[0] <= systematic[1] <= systematic[2] <= systematic[3]
    assert total[0] <= total[1] <= total[2] <= total[3]
    assert gate["formal_monotonicity_audit"] == {
        "comparison_role": (
            "FIXED_REGISTERED_PRIMARY_FIT_POINT_AND_NUISANCE_GRADIENTS;"
            "NO_REFIT_BETWEEN_COVARIANCE_LEVELS"
        ),
        "information_strictly_responds": True,
        "systematic_covariance_nonshrinking": True,
        "total_covariance_nonshrinking": True,
        "minimum_information_response_fraction": 0.01,
        "minimum_zeroed_covariance_response_fraction": 0.0001,
        "covariance_monotonic_absolute_tolerance_m2": 1e-12,
    }
    zeroed = gate["zeroed_gyro_observation_and_bias_covariance_owner_mutation"]
    assert zeroed["pass"]
    assert zeroed["observed"]["covariance_multiplier"] == 0.0
    assert zeroed["observed"][
        "gyro_bias_drift_systematic_covariance_trace_m2"
    ] == 0.0
    assert gate["outputs"][1][
        "gyro_bias_drift_systematic_covariance_trace_m2"
    ] > 0.0
    assert gate["end_to_end_refit_sensitivity"][
        "posterior_covariance_monotonicity_required"
    ] is False
    assert not any(
        row["shared_nuisance_shrinks_as_episode_information"]
        for row in gate["outputs"]
    )
    separation = gate[
        "shared_calibration_white_noise_envelope_contamination_mutation"
    ]
    assert separation["pass"]
    assert separation["coverage_class"] == "EXECUTED_OWNER_LEVEL"
    assert {
        "coverage_class", "owner_call_path", "injected", "expected",
        "observed", "pass",
    }.issubset(separation)
    assert "CENTER_SHARED_CALIBRATION_WHITE_NOISE_ENVELOPE_CONTAMINATION" in settings[
        "synthetic"
    ]["mandatory_sensor_and_numerical_mutations"]
    for row in separation["fixed_point_outputs"]:
        assert row["white_observation_serial_correlation_variance_envelope_multiplier"] > 1.0
        assert row["shared_calibration_noise_sigma_multiplier"] == 1.0
        assert row[
            "shared_calibration_serial_correlation_variance_envelope_multiplier"
        ] == 1.0
        assert row[
            "shared_calibration_marginal_used_only_for_robust_row_standardization"
        ]
        assert not row[
            "shared_calibration_covariance_added_to_repeatable_episode_information"
        ]
        assert not row["shared_calibration_white_noise_filter_envelope_applied"]
        assert not row["shared_calibration_white_noise_sigma_multiplier_applied"]


def test_center_consumes_accelerometer_calibration_nuisance_without_gravity_fit(
    settings: dict,
    center_stochastic_gate: dict,
) -> None:
    assert "CENTER_ACCELEROMETER_CALIBRATION_NUISANCE_OMISSION" in settings[
        "synthetic"
    ]["mandatory_sensor_and_numerical_mutations"]
    mutation = center_stochastic_gate[
        "accelerometer_calibration_nuisance_mutation"
    ]
    assert mutation["pass"]
    assert mutation["coverage_class"] == "EXECUTED_OWNER_LEVEL"
    assert {
        "coverage_class", "owner_call_path", "injected", "expected",
        "observed", "pass",
    }.issubset(mutation)
    assert mutation["zeroed_owner_configuration_rejected"]
    assert mutation["fixed_point_residual_sigma_strictly_increases"]
    assert mutation["fixed_point_information_strictly_decreases"]
    assert mutation["fixed_point_systematic_covariance_nonshrinking"]
    assert mutation["fixed_point_total_covariance_nonshrinking"]
    assert not mutation["initial_still_gravity_or_bias_fitted"]
    assert not mutation["per_action_profile_used"]
    fixed = mutation["fixed_primary_parameter_and_jacobian_outputs"]
    assert [
        row["accelerometer_calibration_covariance_multiplier"] for row in fixed
    ] == [0.0, 0.25, 1.0, 4.0]


def test_progressive_center_preserves_all_shared_systematic_components(
    settings: dict,
) -> None:
    gate = numeric_low_information_geometry_owner_gate(
        settings["geometry_progressive"]
    )
    assert gate["pass"]
    assert gate["center_systematic_component_names"] == [
        "accelerometer_bias_drift",
        "accelerometer_gyro_shared_scale_cross_axis",
        "accelerometer_scale_cross_axis",
        "gyro_bias",
        "gyro_bias_drift",
        "gyro_scale_cross_axis",
        "human_worn",
        "persistent_pair_clock",
    ]
    assert gate["center_systematic_components_preserved_without_episode_shrink"]
    assert gate["center_systematic_total_equals_component_sum"]
    assert not gate["center_shared_systematics_added_as_episode_information"]


def test_progressive_axis_preserves_all_shared_systematic_components(
    settings: dict,
) -> None:
    gate = numeric_low_information_geometry_owner_gate(
        settings["geometry_progressive"]
    )
    assert gate["pass"]
    assert gate["axis_systematic_component_names"] == [
        "accelerometer_bias_drift",
        "accelerometer_gyro_shared_scale_cross_axis",
        "accelerometer_scale_cross_axis",
        "gyro_bias",
        "gyro_bias_drift",
        "gyro_scale_cross_axis",
        "human_worn",
        "persistent_pair_clock",
    ]
    assert gate["axis_systematic_components_preserved_without_episode_shrink"]
    assert gate["axis_systematic_total_equals_component_sum"]
    assert not gate["axis_shared_systematics_added_as_episode_information"]


def test_underdimensioned_hinge_support_is_rejected_before_fit(settings: dict) -> None:
    mutated = dict(settings["hinge_axis"])
    mutated["minimum_effective_support_rows"] = 2.7
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    with pytest.raises(ValueError, match="effective-support floor"):
        estimate_hinge_axis_qmt(
            "knee_left", [object()], settings=mutated,
            parent_acc_covariance=None, child_acc_covariance=None,
            parent_gyro_covariance=None, child_gyro_covariance=None,
            parent_gyro_bias_covariance=None, child_gyro_bias_covariance=None,
            execution_guard=guard,
        )


def test_single_runtime_shares_exact_guard_through_sealed_authority(
    settings: dict,
    initial_stochastic_state: dict,
    prefit_registry_seal_path: Path,
) -> None:
    runtime = C2PipelineRuntime(
        settings,
        initial_stochastic_state,
        prefit_registry_seal_path=prefit_registry_seal_path,
        execution_role="SYNTHETIC_QUALIFICATION",
    )
    assert runtime.audit()["all_owner_guard_identities_equal"]
    assert runtime.audit()["prefit_registry_seal_authority"]["seal_path"] == str(
        prefit_registry_seal_path.resolve()
    )
    assert runtime.audit()["real_fit_activation_authority"] is None


def test_owner_mutations_and_quaternion_gate_use_current_sealed_api(
    settings: dict,
    initial_stochastic_state: dict,
    prefit_registry_seal_path: Path,
) -> None:
    architecture = run_owner_level_architecture_mutations(
        settings,
        prefit_registry_seal_path=prefit_registry_seal_path,
        initial_stochastic_state=initial_stochastic_state,
    )
    assert architecture["registered_names_match"]
    assert architecture["missing_registered_mutations"] == []
    assert architecture["unexpected_mutations"] == []
    assert architecture["owner_not_implemented"] == []
    assert architecture["pass"]
    assert numeric_round_trip_gate()["pass"]
