from __future__ import annotations

import numpy as np

from biospur_fusion.root_r6a0.observability import scalar_range_rank


def test_fk_and_factor_jacobian_gate(gates):
    assert gates["checks"]["05_fk_and_factor_jacobians_match_finite_difference"]
    assert gates["metrics"]["maximum_fk_jacobian_absolute_error"] < 2e-5
    assert gates["metrics"]["maximum_factor_jacobian_absolute_error"] < 2e-4


def test_scalar_range_adds_at_most_one_rank(scenario):
    state = scenario.states[2]
    factor = next(value for value in scenario.range_factors if value.evidence.measurement_time_s == state.time_s)
    assert scalar_range_rank(factor.jacobian_configuration(state)) <= 1


def test_range_uses_exact_measurement_not_availability_time(scenario):
    factor = scenario.range_factors[0]
    calls = []
    factor.predicted(lambda time_s: calls.append(time_s) or scenario.state_at(time_s))
    assert calls == [factor.evidence.measurement_time_s]
    assert factor.evidence.availability_time_s != factor.evidence.measurement_time_s


def test_nonzero_phase_centre_lever_changes_prediction(gates):
    assert gates["checks"]["07_nonzero_tag_lever_changes_range"]
    assert abs(gates["metrics"]["range_change_from_nonzero_lever_m"]) > 1e-5


def test_synthetic_factor_truth_residuals(scenario):
    states = {state.time_s: state for state in scenario.states}
    assert max(np.max(np.abs(factor.residual_state(states[factor.evidence.measurement_time_s])))
               for factor in scenario.range_factors) < 1e-10
    assert max(np.max(np.abs(factor.residual_state(states[factor.evidence.measurement_time_s])))
               for factor in scenario.orientation_factors) < 1e-10
    assert max(np.max(np.abs(factor.residual(states[factor.previous_time_s], states[factor.current_time_s])))
               for factor in scenario.propagation_factors) < 1e-8
    assert max(np.max(np.abs(factor.residual(states[factor.previous_time_s], states[factor.current_time_s])))
               for factor in scenario.root_propagation_factors) < 1e-10
    assert max(np.max(np.abs(factor.residual(states[factor.previous_time_s], states[factor.current_time_s])))
               for factor in scenario.bias_factors) < 1e-10


def test_every_required_factor_family_is_present(scenario):
    families = {proposal.family for proposal in scenario.graph_spec.factor_proposals}
    required = {
        "gauge_prior", "state_prior", "fk_fixed_bone_consistency", "soft_joint_feasibility",
        "raw_uwb_range_true_event_time", "imu_orientation_through_fk_sensor_state",
        "imu_propagation_through_fk_sensor_states", "imu_root_translation_propagation",
        "independent_gyro_bias_evolution", "independent_accel_bias_evolution",
    }
    assert required <= families
    assert {"contact_extension", "zupt_extension", "gait_phase_extension",
            "dynamics_torque_extension", "learned_movement_prior_extension"} <= families


def test_contextual_biomechanics_stays_disabled(scenario):
    contextual = [proposal for proposal in scenario.graph_spec.factor_proposals
                  if proposal.factor_id.startswith("disabled:")]
    assert len(contextual) == 5
    assert all(proposal.activation_state.value == "DISABLED" for proposal in contextual)
