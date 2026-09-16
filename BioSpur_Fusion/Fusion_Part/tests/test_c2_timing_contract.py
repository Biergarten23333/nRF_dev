from pathlib import Path

from biospur_fusion.c2_articulated_biomechanics import hinge_temporal
from biospur_fusion.c2_timing_contract import (
    MAXIMUM_POSE_AGE_NS,
    NATIVE200_PERIOD_NS,
    NATIVE200_PERIOD_US,
    NATIVE200_POSE_AGE_MARGIN_NS,
)
from biospur_fusion.c2_uwb_calibration import causal_body_shadow_validation


def test_shared_native200_pose_age_contract_preserves_frozen_exact_value():
    assert NATIVE200_PERIOD_US == 5_000
    assert NATIVE200_PERIOD_NS == 5_000_000
    assert NATIVE200_POSE_AGE_MARGIN_NS == 5_000
    assert MAXIMUM_POSE_AGE_NS == 5_005_000.0
    assert hinge_temporal.MAXIMUM_POSE_AGE_NS == MAXIMUM_POSE_AGE_NS
    assert causal_body_shadow_validation.MAXIMUM_POSE_AGE_NS == MAXIMUM_POSE_AGE_NS


def test_biomechanics_timing_owner_has_no_upward_uwb_dependency():
    source = Path(hinge_temporal.__file__).read_text(encoding="utf-8")
    assert "biospur_fusion.c2_uwb_calibration" not in source
