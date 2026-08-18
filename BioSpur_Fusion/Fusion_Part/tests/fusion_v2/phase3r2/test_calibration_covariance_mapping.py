from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.imu_pose_v2 import so3
from biospur_fusion.imu_pose_v2.calibration import (
    assert_h9_pool, bundle_payload, fit_joint_calibration, validate_mapping,
)
from biospur_fusion.imu_pose_v2.synthetic import oracle_matrix, oracle_quaternion, synthetic_calibration_rows


def test_exact_operator_mapping_and_c2cc_layout_are_structural(mapping):
    assert validate_mapping({"node_to_role": mapping, "automapping_runtime_factor_count": 0}) == mapping
    bad = dict(mapping); bad["BSFC2CC"], bad["BSF31CC"] = bad["BSF31CC"], bad["BSFC2CC"]
    with pytest.raises(ValueError):
        validate_mapping({"node_to_role": bad, "automapping_runtime_factor_count": 0})
    with pytest.raises(ValueError):
        assert_h9_pool({"BSFC2CC", "BSF31CC"})


def test_all_eighteen_actions_jointly_form_one_frozen_bundle(mapping, fit_actions):
    truth = {
        node: oracle_quaternion(np.array([1., 2., 3.]), np.deg2rad(index - 5))
        for index, node in enumerate(sorted(mapping))
    }
    rows = synthetic_calibration_rows(mapping, fit_actions, truth)
    bundle = fit_joint_calibration(rows, mapping, fit_actions)
    assert set(bundle.fit_factor_counts) == set(fit_actions)
    assert bundle.final_still_static_factor_count == 0
    assert len(bundle.by_node) == 10
    for node, calibration in bundle.by_node.items():
        actual = so3.matrix(calibration.q_I_S)
        assert np.allclose(actual, oracle_matrix(truth[node]), atol=1e-10)
        assert calibration.fit_action_ids == tuple(sorted(fit_actions))
    assert bundle.by_node["BSFC2CC"].layout_class == "C2CC_DISTINCT"
    assert bundle.parameter_covariance_rad2.shape == (30, 30)
    off_diagonal = bundle.parameter_covariance_rad2.copy()
    for index in range(10): off_diagonal[index*3:index*3+3, index*3:index*3+3] = 0
    assert np.linalg.norm(off_diagonal) > 0
    assert bundle_payload(bundle)["frozen_sha256"] == bundle.frozen_sha256


def test_validation_or_final_still_cannot_flow_into_static_fit(mapping, fit_actions):
    rows = synthetic_calibration_rows(mapping, fit_actions)
    poisoned = rows + [type(rows[0])(
        "17_final_still", "cycle", "FIT", "BSF1120",
        np.array([1., 0., 0.]), np.array([1., 0., 0.]), 1., "poison",
    )]
    with pytest.raises(ValueError):
        fit_joint_calibration(poisoned, mapping, fit_actions)
    validation = [type(row)(row.action_id, row.cycle_id, "VALIDATION", row.node_id,
                            row.direction_S, -row.direction_I, row.weight, row.source_uid)
                  for row in rows]
    baseline = fit_joint_calibration(rows, mapping, fit_actions)
    guarded = fit_joint_calibration(rows + validation, mapping, fit_actions)
    assert baseline.frozen_sha256 == guarded.frozen_sha256


def test_right_tangent_covariance_adjoint_90_degree_anisotropic_golden():
    q_i_s = oracle_quaternion(np.array([0., 0., 1.]), np.pi / 2)
    covariance_wi = np.array([[1., .3, .1], [.3, 4., .2], [.1, .2, 9.]])
    covariance_is = np.diag([.1, .2, .3])
    actual = so3.compose_right_covariance(q_i_s, covariance_wi, covariance_is)
    expected = np.array([[4.1, -.3, .2], [-.3, 1.2, -.1], [.2, -.1, 9.3]])
    assert np.allclose(actual, expected, atol=1e-12)
    leaked = covariance_wi + covariance_is
    wrong_inverse_direction = oracle_matrix(q_i_s) @ covariance_wi @ oracle_matrix(q_i_s).T + covariance_is
    assert not np.allclose(actual, leaked)
    assert not np.allclose(actual, wrong_inverse_direction)
