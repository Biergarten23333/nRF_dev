from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.root_r4.contracts import (FactorLedger, FrameContract, LineageError,
    PhysicalSweepLimiter, authorize_uwb_state_target, validate_common_frame_configuration, yaw_rotation_v4_from_n)
from biospur_fusion.root_r4.lineage import disjoint_hybrid, negative_controls


def test_proper_rotation_and_direction():
    rotation = yaw_rotation_v4_from_n(0.4).T
    audit = FrameContract().validate(rotation)
    assert abs(audit["determinant"] - 1.0) < 1e-12
    assert audit["orthogonality_error_fro"] < 1e-12


def test_reflection_rejected():
    with pytest.raises(ValueError):
        FrameContract().validate(np.diag([-1.0, 1.0, 1.0]))


def test_duplicate_raw_factor_rejected():
    ledger = FactorLedger(); ledger.add_raw_factor("a", "raw-1")
    with pytest.raises(LineageError, match="DOUBLE_COUNTING"):
        ledger.add_t4_factor("t4", ("raw-1", "raw-2"))


def test_duplicate_inside_t4_rejected():
    with pytest.raises(LineageError):
        FactorLedger().add_t4_factor("t4", ("raw-1", "raw-1"))


def test_disjoint_real_policy_has_no_duplicates(c1):
    data, _ = c1; result = disjoint_hybrid(data, np.arange(300))
    assert result.audit()["pass"]
    assert result.audit()["maximum_active_factors_per_raw_event"] == 1


def test_naive_real_lineage_negative_control(c1):
    data, _ = c1; result = negative_controls(data)
    assert result["controls"]["naive_raw_plus_t4"]["detected"]
    assert result["controls"]["constituent_lineage_corruption"]["detected"]


def test_physical_sweep_cannot_split_cap():
    limiter = PhysicalSweepLimiter(0.05); applied = [limiter.apply("s", np.array([0.04, 0, 0])) for _ in range(8)]
    assert np.linalg.norm(np.sum(applied, axis=0)) <= 0.05 + 1e-12


@pytest.mark.parametrize("target", ["orientation", "forward_kinematics", "validity", "reset", "bone_length"])
def test_uwb_cannot_mutate_m1(target):
    with pytest.raises(PermissionError):
        authorize_uwb_state_target(target)


def test_common_frame_free_scale_and_per_node_rejected():
    with pytest.raises(ValueError, match="one common frame"):
        validate_common_frame_configuration(frame_count=10)
    with pytest.raises(ValueError, match="free scale"):
        validate_common_frame_configuration(frame_count=1, scale=0.9)
