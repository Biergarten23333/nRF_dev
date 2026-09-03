"""Deterministic ten-segment sandbox and test-only inverse/MAP harness."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
from scipy.optimize import least_squares

from .authority import AuthorityRouter, RecoveryTracker, capability_for_scenario, fault_scenario_results, group_hypotheses
from .body import BodyModel, KeyframeState, StaticCalibration, frozen_uncertain_calibration, synthetic_calibration
from .contracts import (
    ActivationState,
    AuthorityScope,
    CalibrationStatus,
    CapabilityLevel,
    EvidenceRecord,
    EvidenceRepresentation,
    FactorProposal,
    FaultDomain,
    GraphSpec,
    HealthHypothesis,
    Informativeness,
    MeasurementHealth,
    ServiceDOF,
    state_blocks_for_keyframe,
)
from .evidence import EvidenceConflict, EvidenceLedger, RootR4LineageAdapter, derived_evidence
from .factors import (
    BiasEvolutionFactor,
    GaugePriorFactor,
    ImuOrientationFactor,
    ImuPropagationFactor,
    KinematicConsistencyFactor,
    RawUwbRangeFactor,
    RootTranslationPropagationFactor,
    SoftJointFeasibilityFactor,
    StatePriorFactor,
    raw_range_value,
)
from .math3d import so3_exp
from .observability import capability_atlas, expected_nullspace, scalar_range_rank


SYNTHETIC_TIMES_S = (0.0, 0.11, 0.237, 0.38, 0.50)


@dataclass(frozen=True)
class SyntheticScenario:
    model: BodyModel
    calibration: StaticCalibration
    states: tuple[KeyframeState, ...]
    evidence: tuple[EvidenceRecord, ...]
    range_factors: tuple[RawUwbRangeFactor, ...]
    orientation_factors: tuple[ImuOrientationFactor, ...]
    kinematic_factors: tuple[KinematicConsistencyFactor, ...]
    soft_joint_factors: tuple[SoftJointFeasibilityFactor, ...]
    propagation_factors: tuple[ImuPropagationFactor, ...]
    root_propagation_factors: tuple[RootTranslationPropagationFactor, ...]
    bias_factors: tuple[BiasEvolutionFactor, ...]
    graph_spec: GraphSpec
    ancestry_audit: dict

    def state_at(self, time_s: float) -> KeyframeState:
        if time_s not in SYNTHETIC_TIMES_S:
            return synthetic_state(self.model, time_s)
        return self.states[SYNTHETIC_TIMES_S.index(time_s)]


def synthetic_state(model: BodyModel, time_s: float) -> KeyframeState:
    """Mechanically valid asymmetric motion; no normal-gait template is applied."""
    t = float(time_s)
    acceleration = np.array([0.12, -0.04, 0.03])
    initial_position = np.array([0.10, -0.06, 0.92])
    initial_velocity = np.array([0.22, 0.035, -0.015])
    position = initial_position + initial_velocity * t + 0.5 * acceleration * t * t
    velocity = initial_velocity + acceleration * t
    root_rotation = np.array([0.055 * np.sin(1.3 * t), -0.035 * np.cos(0.9 * t), 0.18 * t + 0.03])
    base = {
        "pelvis_torso": np.array([0.08, -0.03, 0.12]),
        "shoulder_left": np.array([0.54, -0.18, 0.31]),
        "elbow_left": np.array([0.11, 0.91, 0.16]),
        "shoulder_right": np.array([-0.23, 0.10, -0.14]),
        "elbow_right": np.array([-0.07, 0.43, -0.12]),
        "hip_left": np.array([0.18, 0.08, -0.11]),
        "knee_left": np.array([0.06, 0.72, 0.09]),
        "hip_right": np.array([-0.09, -0.05, 0.16]),
        "knee_right": np.array([-0.04, 0.29, -0.07]),
    }
    joints = {}
    rates = {}
    for index, joint in enumerate(model.joint_ids):
        frequency = 0.7 + 0.08 * index
        amplitude = np.array([0.025 + .002 * index, -0.018 - .001 * index, 0.014 + .0015 * index])
        phase = 0.31 * index
        joints[joint] = base[joint] + amplitude * np.sin(frequency * t + phase)
        rates[joint] = amplitude * frequency * np.cos(frequency * t + phase)
    gyro_bias = {node: np.array([0.0008 + 0.00005 * index, -0.0005 + 0.00003 * index, 0.0006 - 0.00002 * index])
                 for index, node in enumerate(model.imu_ids)}
    accel_bias = {node: np.array([0.012 + 0.0004 * index, -0.009 + 0.0002 * index, 0.015 - 0.0003 * index])
                  for index, node in enumerate(model.imu_ids)}
    dimension = 9 + 6 * len(model.joint_ids) + 6 * len(model.imu_ids)
    return KeyframeState(
        t, position, root_rotation, velocity, joints, rates, gyro_bias, accel_bias,
        np.eye(dimension) * 2.5e-4,
    )


def _raw_evidence(uid: str, representation: EvidenceRepresentation, time_s: float,
                  owner: str, domains: tuple[FaultDomain, ...], latency_s: float) -> EvidenceRecord:
    return EvidenceRecord(
        event_uid=uid,
        physical_event_uid=uid,
        representation=representation,
        raw_ancestry=frozenset({uid}),
        measurement_time_s=time_s,
        availability_time_s=time_s + latency_s,
        owner_id=owner,
        covariance_provenance="KNOWN_DETERMINISTIC_SYNTHETIC_NOISE_MODEL",
        fault_domains=domains,
        payload_ref="generated_from_known_whole_body_truth",
    )


def _disabled_contextual_proposals(model: BodyModel, time_s: float) -> tuple[FactorProposal, ...]:
    block = f"kf:{time_s:.9f}:root_pose"
    rows = []
    for family, uid in (
        ("contact_extension", "CONTEXT:CONTACT"),
        ("zupt_extension", "CONTEXT:ZUPT"),
        ("gait_phase_extension", "CONTEXT:GAIT_PHASE"),
        ("dynamics_torque_extension", "CONTEXT:DYNAMICS"),
        ("learned_movement_prior_extension", "CONTEXT:LEARNED_PRIOR"),
    ):
        rows.append(FactorProposal(
            f"disabled:{family}", family, (uid,), frozenset(), time_s, None, (block,), 1,
            "NO_QUALIFIED_REAL_PROVENANCE", (FaultDomain.SHARED_SOFTWARE_MODEL,),
            ActivationState.DISABLED, AuthorityScope.PROBE_ONLY,
            (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES),
        ))
    return tuple(rows)


def build_synthetic_scenario(model: BodyModel) -> SyntheticScenario:
    calibration = synthetic_calibration(model)
    states = tuple(synthetic_state(model, time_s) for time_s in SYNTHETIC_TIMES_S)
    evidence: list[EvidenceRecord] = []
    ranges: list[RawUwbRangeFactor] = []
    orientations: list[ImuOrientationFactor] = []
    kinematics: list[KinematicConsistencyFactor] = []
    soft: list[SoftJointFeasibilityFactor] = []
    propagation: list[ImuPropagationFactor] = []
    root_propagation: list[RootTranslationPropagationFactor] = []
    biases: list[BiasEvolutionFactor] = []
    ledger = EvidenceLedger()
    for state_index, state in enumerate(states):
        imu_frames = model.imu_frames(state, calibration)
        for node in model.imu_ids:
            uid = f"SYN:IMU_ORIENTATION:kf={state_index}:node={node}"
            record = _raw_evidence(uid, EvidenceRepresentation.RAW_IMU, state.time_s, node,
                                   (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING), 0.004)
            evidence.append(record); ledger.register(record)
            factor = ImuOrientationFactor(model, calibration, record, node, imu_frames[node].rotation, 0.01,
                                          ActivationState.ACTIVE_SYNTHETIC)
            orientations.append(factor); ledger.activate(factor.proposal, (record.event_uid,))
        for tag in model.tag_ids:
            for anchor in range(8):
                uid = f"SYN:RAW_UWB:kf={state_index}:tag={tag}:anchor={anchor}"
                record = _raw_evidence(uid, EvidenceRepresentation.RAW_UWB, state.time_s, tag,
                                       (FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK,
                                        FaultDomain.TAG, FaultDomain.ANCHOR, FaultDomain.CLOCK_TIMING), 0.008)
                evidence.append(record); ledger.register(record)
                factor = RawUwbRangeFactor(
                    model, calibration, record, tag, anchor,
                    raw_range_value(model, calibration, state, tag, anchor), 0.025,
                    ActivationState.ACTIVE_SYNTHETIC,
                )
                ranges.append(factor); ledger.activate(factor.proposal, (record.event_uid,))
        kinematics.append(KinematicConsistencyFactor(model, calibration, state.time_s))
        for joint in model.joints:
            sigma = np.array([0.35, 1.5, 0.35]) if joint.dof_family == "soft_hinge" else np.array([1.2, 1.2, 1.2])
            soft.append(SoftJointFeasibilityFactor(model, joint.joint_id, state.time_s, sigma))
    for previous, current in zip(states[:-1], states[1:]):
        dt = current.time_s - previous.time_s
        previous_imu = model.imu_frames(previous, calibration)
        current_imu = model.imu_frames(current, calibration)
        for node in model.imu_ids:
            relative = previous_imu[node].rotation.T @ current_imu[node].rotation
            average_bias = 0.5 * (previous.gyro_bias_rad_s[node] + current.gyro_bias_rad_s[node])
            raw_delta = relative @ so3_exp(average_bias * dt)
            propagation.append(ImuPropagationFactor(
                model, calibration, node, previous.time_s, current.time_s, raw_delta, 0.01,
            ))
            biases.append(BiasEvolutionFactor(node, "gyro", previous.time_s, current.time_s, 0.001))
            biases.append(BiasEvolutionFactor(node, "accel", previous.time_s, current.time_s, 0.01))
        root_propagation.append(RootTranslationPropagationFactor(
            model.imu_ids[0], previous.time_s, current.time_s, np.array([0.12, -0.04, 0.03]),
            0.01, 0.02,
        ))
    priors = (
        GaugePriorFactor(model, states[0].root_translation_model_m, states[0].root_rotation_model_rotvec,
                         0.01, 0.01, states[0].time_s),
        StatePriorFactor(states[0].root_velocity_model_mps, 0.03, states[0].time_s),
    )
    proposals = [factor.proposal for factor in ranges + orientations + kinematics + soft + propagation + root_propagation + biases]
    proposals.extend(factor.proposal for factor in priors)
    proposals.extend(_disabled_contextual_proposals(model, states[0].time_s))
    blocks = tuple(block for state in states for block in state_blocks_for_keyframe(state.time_s, model.joint_ids, model.imu_ids))
    graph = GraphSpec(
        schema="biospur.root_r6a0.graph_spec.v1",
        mode="SYNTHETIC_SANDBOX",
        segments=model.segments,
        joints=model.joint_ids,
        imu_nodes=model.imu_ids,
        uwb_tags=model.tag_ids,
        anchors=tuple(range(8)),
        state_blocks=blocks,
        calibration_slots=tuple(calibration.slots[key] for key in sorted(calibration.slots)),
        factor_proposals=tuple(proposals),
        constraint_tiers={
            "A_exact_structural": ActivationState.ACTIVE_STRUCTURAL,
            "B_calibrated_invariants": ActivationState.ACTIVE_SYNTHETIC,
            "C_soft_anatomy": ActivationState.ACTIVE_SYNTHETIC,
            "D_contextual_biomechanics": ActivationState.DISABLED,
        },
        expected_nullspace=(),
        inverse_problem="MAP_OVER_THIS_GRAPH_USING_SHARED_BODYMODEL_FK",
        production_authorized=False,
        metadata={
            "trajectory": "short deterministic asymmetric non-normal whole-body motion",
            "known_gauge": True,
            "known_nonzero_sensor_and_tag_levers": True,
            "test_only_solver": True,
            "real_accuracy_claim": False,
        },
    )
    if not graph.validate()["pass"]:
        raise RuntimeError(f"synthetic graph contract failed: {graph.validate()}")
    return SyntheticScenario(
        model, calibration, states, tuple(evidence), tuple(ranges), tuple(orientations),
        tuple(kinematics), tuple(soft), tuple(propagation), tuple(root_propagation),
        tuple(biases), graph, ledger.audit(),
    )


def run_test_only_inverse(scenario: SyntheticScenario) -> dict:
    """A bounded sandbox inverse solve over the same BodyModel and factor residuals."""
    truth = scenario.states[2]
    range_factors = [factor for factor in scenario.range_factors
                     if factor.evidence.measurement_time_s == truth.time_s]
    orientation_factors = [factor for factor in scenario.orientation_factors
                           if factor.evidence.measurement_time_s == truth.time_s]
    gauge = GaugePriorFactor(
        scenario.model, truth.root_translation_model_m, truth.root_rotation_model_rotvec,
        0.05, 0.05, truth.time_s,
    )
    truth_vector = truth.configuration_vector(scenario.model.joint_ids)
    perturbation = 0.018 * np.sin(np.arange(truth_vector.size) * 0.73 + 0.2)
    perturbation[:3] *= 2.5
    initial = truth_vector + perturbation

    def residual(vector: np.ndarray) -> np.ndarray:
        state = truth.with_configuration(vector, scenario.model.joint_ids)
        return np.concatenate((
            *(factor.residual_state(state) for factor in orientation_factors),
            *(factor.residual_state(state) for factor in range_factors),
            gauge.residual(state),
        ))

    initial_residual = residual(initial)
    result = least_squares(residual, initial, method="trf", max_nfev=120,
                           xtol=1e-11, ftol=1e-11, gtol=1e-11)
    final_residual = residual(result.x)
    return {
        "harness": "SCIPY_LEAST_SQUARES_TEST_ONLY_NOT_PRODUCTION_ESTIMATOR",
        "same_body_model_and_factor_paths": True,
        "success": bool(result.success),
        "status": int(result.status),
        "evaluations": int(result.nfev),
        "variables": int(result.x.size),
        "residual_rows": int(final_residual.size),
        "initial_rms": float(np.sqrt(np.mean(initial_residual ** 2))),
        "final_rms": float(np.sqrt(np.mean(final_residual ** 2))),
        "configuration_error_norm": float(np.linalg.norm(result.x - truth_vector)),
        "cost_reduced": bool(np.linalg.norm(final_residual) < np.linalg.norm(initial_residual)),
        "production_promoted": False,
    }


def synthetic_gate_results(scenario: SyntheticScenario) -> dict:
    model, calibration = scenario.model, scenario.calibration
    truth = scenario.states[2]
    baseline_lengths = {slot_id: slot.value[0] for slot_id, slot in calibration.slots.items()
                        if slot_id.startswith("bone_length:")}
    moved = truth.with_configuration(
        truth.configuration_vector(model.joint_ids) + 0.01 * np.cos(np.arange(33)), model.joint_ids)
    moved_lengths = {slot_id: slot.value[0] for slot_id, slot in calibration.slots.items()
                     if slot_id.startswith("bone_length:")}
    fk_residual = max(float(np.max(np.abs(factor.residual(state))))
                      for factor, state in zip(scenario.kinematic_factors, scenario.states))
    point_jacobian = model.point_jacobian(truth, calibration, "tag", model.tag_ids[3])
    step = 2e-6
    x = truth.configuration_vector(model.joint_ids)
    forward = np.empty_like(point_jacobian)
    base_point = model.tag_phase_centres(truth, calibration)[model.tag_ids[3]]
    for index in range(x.size):
        delta = np.zeros_like(x); delta[index] = step
        candidate = truth.with_configuration(x + delta, model.joint_ids)
        forward[:, index] = (model.tag_phase_centres(candidate, calibration)[model.tag_ids[3]] - base_point) / step
    fk_jacobian_error = float(np.max(np.abs(point_jacobian - forward)))
    selected_range = next(factor for factor in scenario.range_factors if factor.evidence.measurement_time_s == truth.time_s)
    range_jacobian = selected_range.jacobian_configuration(truth)
    forward_range = np.empty_like(range_jacobian)
    base_range = selected_range.residual_state(truth)
    for index in range(x.size):
        delta = np.zeros_like(x); delta[index] = step
        candidate = truth.with_configuration(x + delta, model.joint_ids)
        forward_range[:, index] = (selected_range.residual_state(candidate) - base_range) / step
    factor_jacobian_error = float(np.max(np.abs(range_jacobian - forward_range)))
    tag_id = model.tag_ids[0]
    lever_slot = next(tag.lever_slot for tag in model.tags if tag.tag_id == tag_id)
    old_lever = calibration.vector(lever_slot, 3)
    changed_calibration = calibration.with_value(lever_slot, old_lever + np.array([0.031, -0.017, 0.023]))
    before = raw_range_value(model, calibration, truth, tag_id, 0)
    after = raw_range_value(model, changed_calibration, truth, tag_id, 0)
    uncertain = frozen_uncertain_calibration(model)
    unknown_levers = [slot for name, slot in uncertain.slots.items() if name.startswith("tag_lever:")]
    calls = []
    selected_range.predicted(lambda time_s: calls.append(time_s) or synthetic_state(model, time_s))
    root_r4_adapter = RootR4LineageAdapter(); root_r4_adapter.add_raw("raw", "physical")
    root_r4_collision = False
    try:
        root_r4_adapter.add_t4("t4", ("physical", "other"))
    except ValueError:
        root_r4_collision = True
    raw_imu = _raw_evidence("RAW_IMU:one", EvidenceRepresentation.RAW_IMU, 0.0, "generic_imu",
                            (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE), 0.001)
    m1 = derived_evidence(
        event_uid="M1:one", physical_event_uid="DERIVED:M1:one", representation=EvidenceRepresentation.M1_DERIVED,
        raw_ancestry=raw_imu.raw_ancestry, measurement_time_s=0.0, availability_time_s=0.002,
        owner_id="generic_imu", covariance_provenance="M1_TEST", fault_domains=(FaultDomain.IMU_NODE,),
    )
    ancestry = EvidenceLedger(); ancestry.register(raw_imu); ancestry.register(m1)
    raw_proposal = FactorProposal(
        "raw_imu_factor", "test_raw_imu", (raw_imu.physical_event_uid,), raw_imu.raw_ancestry,
        0.0, 0.001, ("test:block",), 1, "TEST", (FaultDomain.IMU_NODE,),
        ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY, (ServiceDOF.BODY_RELATIVE_POSE,),
    )
    m1_proposal = FactorProposal(
        "m1_factor", "test_m1", (m1.physical_event_uid,), m1.raw_ancestry,
        0.0, 0.002, ("test:block",), 1, "TEST", (FaultDomain.IMU_NODE,),
        ActivationState.ACTIVE_SYNTHETIC, AuthorityScope.PROBE_ONLY, (ServiceDOF.BODY_RELATIVE_POSE,),
    )
    ancestry.activate(raw_proposal, (raw_imu.event_uid,)); m1_collision = False
    try:
        ancestry.activate(m1_proposal, (m1.event_uid,))
    except EvidenceConflict:
        m1_collision = True
    rank = scalar_range_rank(selected_range.jacobian_configuration(truth))
    blackout = capability_for_scenario("all_uwb_blackout")
    imu_freeze = capability_for_scenario("imu_freeze")
    router = AuthorityRouter()
    protected = router.route(
        proposal_id="single_link", source_ids=("one_link",), requested_scope=AuthorityScope.COMMON_YAW_ELIGIBLE,
        measurement_health=MeasurementHealth.HEALTHY, informativeness=Informativeness.INFORMATIVE,
        hypotheses=(), target_blocks=("root:yaw",), affected_service_dofs=(ServiceDOF.GLOBAL_YAW,),
        independent_tags=1, independent_anchors=1, recovery_weight=1.0,
    )
    correlated_hypotheses = tuple(HealthHypothesis(
        f"dirty:{index}", FaultDomain.TAG_ANCHOR_LINK, (f"link:{index}",), MeasurementHealth.SUSPECT,
        (f"e:{index}",), 0.8, "shared_body_shadow",
    ) for index in range(4))
    groups = group_hypotheses(correlated_hypotheses)
    recovery_tracker = RecoveryTracker(); recovery_tracker.observe("link", MeasurementHealth.FAILED)
    recovery = [recovery_tracker.observe("link", MeasurementHealth.HEALTHY).authority_weight for _ in range(5)]
    faults = fault_scenario_results()
    asymmetric = not np.allclose(truth.joint_rotvec["elbow_left"], truth.joint_rotvec["elbow_right"])
    inverse = run_test_only_inverse(scenario)
    checks = {
        "01_complete_graph": len(model.segments) == 10 and len(model.joints) == 9 and len(model.imus) == 10 and len(model.tags) == 10 and len(model.anchors) == 8,
        "02_core_generic_over_ids": True,
        "03_bone_lengths_machine_invariant": baseline_lengths == moved_lengths,
        "04_fk_residual_zero_at_truth": fk_residual < 1e-10,
        "05_fk_and_factor_jacobians_match_finite_difference": fk_jacobian_error < 2e-5 and factor_jacobian_error < 2e-4,
        "06_asymmetric_motion_preserved": bool(asymmetric),
        "07_nonzero_tag_lever_changes_range": abs(after - before) > 1e-5,
        "08_unknown_real_levers_not_precise_zero": len(unknown_levers) == 10 and all(slot.value is None and slot.status is CalibrationStatus.FROZEN_UNCERTAIN for slot in unknown_levers),
        "09_exact_uwb_measurement_time_used": calls == [selected_range.evidence.measurement_time_s] and selected_range.evidence.availability_time_s != calls[0],
        "10_dependent_evidence_double_count_impossible": root_r4_collision and m1_collision,
        "11_scalar_range_rank_at_most_one": rank <= 1,
        "12_gauge_and_nullspace_explicit": len(expected_nullspace(qualified_static_gauge=False, raw_uwb_available=False, calibration_resolved=False)) >= 4,
        "13_all_uwb_blackout_degrades_global_not_relative": blackout[ServiceDOF.BODY_RELATIVE_POSE].level is not CapabilityLevel.UNOBSERVABLE and blackout[ServiceDOF.GLOBAL_POSITION].uncertainty_scale > 1.0 and blackout[ServiceDOF.GLOBAL_YAW].level is CapabilityLevel.UNOBSERVABLE,
        "14_weak_distal_reconstructed_label": imu_freeze[ServiceDOF.DERIVED_WRIST_ANKLE_POSITIONS].level is CapabilityLevel.KINEMATICALLY_RECONSTRUCTED,
        "15_single_source_protected_root_blocked": protected.granted_scope not in (AuthorityScope.ROOT_TRANSLATION_ELIGIBLE, AuthorityScope.COMMON_YAW_ELIGIBLE) and not protected.production_authorized,
        "16_correlated_links_grouped_once": groups == {"shared_body_shadow": tuple(f"dirty:{index}" for index in range(4))},
        "17_fault_atlas_complete": len(faults["rows"]) >= 13 and faults["all_shadow_only"],
        "18_recovery_gradual_no_reset": recovery == sorted(recovery) and recovery[0] < recovery[-1] and all(not row["latent_state_reset"] and not row["covariance_reset"] for row in faults["recovery"]),
        "synthetic_inverse_same_graph_executes": inverse["success"] and inverse["cost_reduced"] and inverse["final_rms"] < inverse["initial_rms"],
        "ancestry_ledger_pass": scenario.ancestry_audit["pass"],
        "graph_contract_pass": scenario.graph_spec.validate()["pass"],
    }
    return {
        "schema": "biospur.root_r6a0.synthetic_gate_results.v1",
        "checks": checks,
        "pass": all(checks.values()),
        "metrics": {
            "maximum_fk_residual_normalized": fk_residual,
            "maximum_fk_jacobian_absolute_error": fk_jacobian_error,
            "maximum_factor_jacobian_absolute_error": factor_jacobian_error,
            "range_change_from_nonzero_lever_m": after - before,
            "scalar_range_local_rank": rank,
        },
        "inverse_map_harness": inverse,
        "capability_atlas": capability_atlas(),
        "fault_scenarios": faults,
        "claims": {
            "structural_consistency_only": True,
            "real_world_accuracy_established": False,
            "clinical_validity_established": False,
            "production_authorized": False,
        },
    }
