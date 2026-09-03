from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.root_r4.causality import ImmutableCausalRoot, TimedObservation, synthetic_causality_and_recovery
from biospur_fusion.root_r4.inertial import GRAVITY_N_MPS2, propagate_step, synthetic_goldens


def test_inertial_synthetic_goldens():
    result = synthetic_goldens()
    assert result["passed"]
    assert result["classification"] == "ROOT_INERTIAL_PROPAGATION_SYNTHETICALLY_QUALIFIED"


def test_stationary_specific_force_convention():
    p, v = propagate_step(np.zeros(3), np.zeros(3), np.zeros(3), -GRAVITY_N_MPS2, np.eye(3), 1.0)
    np.testing.assert_allclose(p, 0.0, atol=1e-12); np.testing.assert_allclose(v, 0.0, atol=1e-12)


def test_invalid_rotation_fails():
    with pytest.raises(ValueError):
        propagate_step(np.zeros(3), np.zeros(3), np.zeros(3), -GRAVITY_N_MPS2, np.diag([-1, 1, 1]), 0.1)


def test_future_uwb_and_preavailability_detected():
    root = ImmutableCausalRoot()
    with pytest.raises(ValueError, match="future"):
        root.update(TimedObservation(2.0, 1.0, np.zeros(3), "future"))
    root.update(TimedObservation(0.0, 1.0, np.zeros(3), "ok"))
    with pytest.raises(ValueError, match="pre-availability"):
        root.emit(0.5)


def test_emitted_output_is_immutable():
    root = ImmutableCausalRoot(); output = root.emit(0.0)
    assert not output.flags.writeable
    with pytest.raises(ValueError):
        output[0] = 1.0


def test_dropout_ramp_and_cap():
    causality, immutable, dropout = synthetic_causality_and_recovery()
    assert causality["all_zero_leakage"]
    assert immutable["pass"]
    assert dropout["covariance_grew"]
    assert dropout["physical_sweep_cap_pass"]
    assert dropout["reacquisition_updates"] == 5
