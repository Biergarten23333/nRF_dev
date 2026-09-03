from __future__ import annotations

import numpy as np

from biospur_fusion.root_r5a.data import block_masks
from biospur_fusion.root_r5a.profiles import circular_profile, raw_evaluator, raw_groups, t4_evaluator


def test_t4_profile_is_complete_and_converged(c1):
    data, _, support = c1; profile = circular_profile("T4", "TEST", *t4_evaluator(data, block_masks(support)[0]))
    assert len(profile["grid_yaw_deg"]) == 360
    assert profile["profile_grid_converged"]
    assert profile["support"]["tags"] == 10


def test_raw_profile_profiles_root_nuisance(c1):
    data, _, support = c1; groups = raw_groups(data, block_masks(support)[0])
    objective, residual, support_record = raw_evaluator(groups)
    assert np.isfinite(objective(0.0))
    assert residual(0.0).size == support_record["samples"]
    assert "profiled" in support_record["nuisance"]
