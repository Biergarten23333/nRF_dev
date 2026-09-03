from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_v1.structural_repair import (
    TorsoGaugeAudit, run_s0_s1_structural_repair,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT/"config"/"imu_only_multi_action_centerline_calibration_v1"


def _inputs() -> tuple[dict, dict, dict]:
    gates = json.loads((CONFIG/"gates_v1.json").read_text())
    repair = json.loads((CONFIG/"S0_S1_STRUCTURAL_REPAIR_GATES_V1.json").read_text())
    template = json.loads((ROOT.parent/repair["product_geometry"]["template_path"]).read_text())
    return gates, repair, template


@pytest.fixture(scope="module")
def result() -> dict:
    return run_s0_s1_structural_repair(*_inputs())


def test_analytic_finite_transform_multiplication_order_and_sign() -> None:
    audit = TorsoGaugeAudit(*_inputs())
    alpha = 0.071
    Q = audit.R_N_torso_from_B_torso_reference
    G = Rotation.from_rotvec(
        alpha*audit.torso_board_rotation_axis_per_heading
    ).as_matrix()
    left = Rotation.from_rotvec(np.array([0.0, 0.0, alpha])).as_matrix() @ Q @ G
    np.testing.assert_allclose(left, Q, rtol=0.0, atol=2e-15)
    value = audit.transform_value(alpha)
    assert value[audit.problem.slices["heading:torso"]][0] == alpha


def test_finite_scan_proves_residual_invariance_but_not_product_invariance(
        result: dict) -> None:
    scan = result["finite_transform_scan"]
    assert len(scan["rows"]) == 12
    assert scan["all_residual_vectors_and_costs_invariant"] is True
    assert scan["all_publishable_centerline_products_invariant"] is False
    assert scan["analytic_gauge_proven"] is False
    row = next(row for row in scan["rows"] if row["alpha_rad"] == 0.1)
    assert row["residual"]["maximum_absolute_delta"] < 1e-8
    assert row["products"]["per_action"]["trunk"][
        "maximum_segment_axis_angle_rad"
    ] > 0.03
    assert row["products"]["per_action"]["trunk"][
        "maximum_graphical_joint_displacement_m"
    ] > 0.019


def test_analytic_and_central_directional_derivatives_crosscheck(result: dict) -> None:
    check = result["jacobian_audit"]["directional_derivative_crosscheck"]
    assert check["analytic_vs_parameter_jacobian_max_abs"] < 1e-7
    assert check["analytic_vs_finite_transform_central_max_abs"] < 1e-7
    assert result["jacobian_audit"]["rank"] == 370
    assert result["jacobian_audit"]["nullity"] == 1
    assert len(result["jacobian_audit"]["actual_null_vector_all_components"]) == 371


def test_every_declared_calibration_action_has_information(result: dict) -> None:
    sensitivity = result["action_parameter_sensitivity"]
    assert sensitivity["declared_action_unused"] == []
    for action, row in sensitivity["action_summary"].items():
        if action not in ("left_heel", "right_heel"):
            assert row["has_nonzero_static_parameter_information"], action
        else:
            assert row["declared_role"] == "VALIDATION_ONLY_UNSUPPORTED_FOOT_DOF"


def test_s0_failure_forbids_quotient_repair_and_real_data(result: dict) -> None:
    assert result["verdict"] == "FAIL_MULTI_ACTION_NULLSPACE"
    assert result["phase_status"] == "STOPPED_AFTER_S0_AS_REQUIRED"
    assert result["repair_before_after"]["after"]["status"] == (
        "NO_REPAIR_APPLIED_BECAUSE_CANDIDATE_FAILED_PRODUCT_INVARIANCE"
    )
    assert result["repair_before_after"]["after"]["parameter_table"] == "IDENTICAL_TO_BEFORE"
    assert result["real_data_status"] == "ALL_REAL_AND_HELD_OUT_INPUTS_REMAIN_SEALED"
