from __future__ import annotations

import numpy as np

from pure_imu_baseline.stage3.corrector import qz
from pure_imu_baseline.stage3r1.config import load_config
from pure_imu_baseline.stage3r1.corrector import correct
from pure_imu_baseline.stage3r1.qualification import PARENT, PELVIS, base


def test_pelvis_exact_and_common_yaw_rejected():
    t, q, valid, reset, stationary = base(12.0)
    q[:] = qz(0.01*t)[:, None, :]
    out = correct(t, q, valid, reset, stationary, PARENT, PELVIS, load_config())
    assert np.array_equal(q[:, PELVIS], out["corrected_q_GB_wxyz"][:, PELVIS])
    assert np.max(np.abs(out["edge_eta_rad"])) <= 1e-12


def test_unsupported_child_inherits_parent_correction():
    t, q, valid, reset, stationary = base(18.0)
    q[:, 2] = qz(0.01*t)
    enabled = np.ones(9, bool); enabled[2] = False
    out = correct(t, q, valid, reset, stationary, PARENT, PELVIS, load_config(), enabled)
    assert np.array_equal(out["node_correction_rad"][:, 3], out["node_correction_rad"][:, 2])


def test_subtree_gap_does_not_reset_unrelated_edge():
    t, q, valid, reset, stationary = base(24.0)
    q[:, 3] = qz(0.01*t)
    gap = (t >= 16) & (t < 17); valid[gap, 2] = False
    out = correct(t, q, valid, reset, stationary, PARENT, PELVIS, load_config())
    post = np.searchsorted(t, 17.0)
    assert np.all(out["edge_eta_rad"][post, [1, 2]] == 0)
    assert out["edge_epoch"][post, 0] == out["edge_epoch"][post-1, 0]
