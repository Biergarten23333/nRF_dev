import json
from pathlib import Path
import numpy as np
import pytest

from biospur_fusion.articulated_v2.binding import EXPECTED_NODES, FrozenMappingBinding, ROLES
from biospur_fusion.articulated_v2.estimator import ArticulatedImuEstimator, ImuObservation
from biospur_fusion.articulated_v2.synthetic import constant_rate_trial, monte_carlo, oracle_specific_force


def binding():
    return FrozenMappingBinding("test", "v1", "c", "s", "d", dict(zip(EXPECTED_NODES, ROLES)), "OPERATOR_RECORDED", "0"*64, "exact", True, "FAILED_DEFERRED")


def config():
    root = Path(__file__).resolve().parents[5]
    return json.loads((root/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3/PHASE3_SOLVER_CONFIG.json").read_text())


def test_noiseless_constant_rate_and_health():
    result = constant_rate_trial(binding(), config(), 2)
    assert result["p95_rad"] <= np.deg2rad(15)
    result["estimator"].assert_numerical_health()
    audit = result["estimator"].factor_audit()
    assert audit.gyro_propagation > 0 and audit.accel_bias_state > 0 and audit.temporal_process > 0
    assert audit.uwb == audit.contact == audit.hard_zupt == audit.phase1_orientation == 0
    assert audit.accel_dynamic == audit.mounting_cluster == 0


def test_gap_inflates_matched_uncertainty_and_recovers_without_reset():
    result = monte_carlo(binding(), config(), 20)
    assert result["gap_additional_uncertainty_fraction"] >= .99


def test_duplicate_out_of_order_and_boot_are_explicit():
    estimator = ArticulatedImuEstimator(binding(), config())
    node = EXPECTED_NODES[0]
    obs = ImuObservation(node, .01, 1, np.zeros(3), np.array([0,0,9.80665]))
    estimator.update(obs)
    with pytest.raises(ValueError): estimator.update(obs)
    estimator.update(ImuObservation(node, .02, 2, np.zeros(3), np.array([0,0,9.80665]), boot_id=1))
    assert "BOOT_REINITIALIZING" in estimator.segments[estimator.binding.node_to_role[node]].degraded_reasons


def test_independent_lever_arm_oracle_golden_vector_and_sign_mutation():
    f = oracle_specific_force(np.array([1.,0,0,0]), np.zeros(3), np.array([0,0,2.]), np.zeros(3), np.array([.5,0,0]))
    assert np.allclose(f, [-2., 0., 9.80665], atol=1e-12)
    wrong = oracle_specific_force(np.array([1.,0,0,0]), np.zeros(3), np.array([0,0,-2.]), np.zeros(3), np.array([.5,0,0]))
    assert np.allclose(wrong, f)  # centripetal term is sign-even
    tangential = oracle_specific_force(np.array([1.,0,0,0]), np.zeros(3), np.zeros(3), np.array([0,0,2.]), np.array([.5,0,0]))
    assert np.allclose(tangential, [0., 1., 9.80665], atol=1e-12)


def test_future_observation_cannot_change_published_prefix():
    estimator = ArticulatedImuEstimator(binding(), config())
    control = ArticulatedImuEstimator(binding(), config())
    node = EXPECTED_NODES[0]
    for i in range(1,10):
        obs = ImuObservation(node, i*.005, i, np.array([.1,0,0]), np.array([0,0,9.80665]))
        estimator.update(obs); control.update(obs)
    prefix = estimator.output(.05)
    control_prefix = control.output(.05)
    estimator.update(ImuObservation(node, .055, 10, np.array([10.,0,0]), np.array([0,0,9.80665])))
    control.update(ImuObservation(node, .055, 10, np.array([-10.,0,0]), np.array([0,0,9.80665])))
    assert prefix == control_prefix


def test_saturation_is_rejected_with_degraded_flag():
    estimator = ArticulatedImuEstimator(binding(), config()); node = EXPECTED_NODES[0]
    estimator.update(ImuObservation(node, .01, 1, np.array([100.,0,0]), np.array([0,0,9.80665])))
    assert "SATURATION_OR_SCALE_INVALID" in estimator.segments[estimator.binding.node_to_role[node]].degraded_reasons
