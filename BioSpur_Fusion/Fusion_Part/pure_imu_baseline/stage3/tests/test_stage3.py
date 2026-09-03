from __future__ import annotations

import numpy as np

from pure_imu_baseline.math3d import to_matrix
from pure_imu_baseline.stage3.config import load_config
from pure_imu_baseline.stage3.corrector import correct, qz
from pure_imu_baseline.stage3.qualification import (_base, _tree,
                                                     run_negative_controls,
                                                     run_synthetic_qualification)


def test_synthetic_qualification():
    result = run_synthetic_qualification(load_config())
    assert result["all_passed"], result


def test_twenty_negative_controls():
    result = run_negative_controls(load_config())
    assert result["executed_count"] == 20
    assert result["all_passed"], result


def test_gravity_and_pelvis_invariants():
    t, q, valid, reset, stationary = _base(60.0)
    q[:, 3] = qz(0.01*t)
    parent, pelvis = _tree()
    out = correct(t, q, valid, reset, stationary, parent, pelvis, load_config())
    qc = out["corrected_q_GB_wxyz"]
    ez = np.array([0., 0., 1.])
    raw_g = np.einsum("...ji,j->...i", to_matrix(q), ez)
    cor_g = np.einsum("...ji,j->...i", to_matrix(qc), ez)
    assert np.max(np.linalg.norm(raw_g-cor_g, axis=-1)) <= 1e-12
    assert np.array_equal(q[:, pelvis], qc[:, pelvis])


def test_prefix_causality_with_nonzero_update():
    t, q, valid, reset, stationary = _base(80.0)
    q[:, 3] = qz(0.012*t)
    parent, pelvis = _tree(); cfg = load_config()
    full = correct(t, q, valid, reset, stationary, parent, pelvis, cfg)
    for stop in (241, 701, 1337, 2601, 4000):
        part = correct(t[:stop], q[:stop], valid[:stop], reset[:stop], stationary[:stop], parent, pelvis, cfg)
        for key in ("corrected_q_GB_wxyz", "correction_rad", "bias_rad_s", "correction_confidence", "correction_epoch"):
            assert np.array_equal(full[key][:stop], part[key]), (stop, key)
