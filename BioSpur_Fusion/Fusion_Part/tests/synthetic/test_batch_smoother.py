import importlib.util
from pathlib import Path

import numpy as np

from biospur_fusion.body_graph.batch_smoother import (
    ArticulatedBatchSmoother, EstimatorSamples, GenuineFixedLagArticulatedEstimator,
)
from biospur_fusion.calibration.articulated_batch import (
    SEGMENTS, forward_antenna_positions, segment_rotations, unpack_static,
)


def source_fixture():
    path = Path(__file__).with_name("test_articulated_calibration.py")
    spec = importlib.util.spec_from_file_location("calibration_fixture", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    samples, truth = module.synthetic_samples()
    r_nv, extrinsics, geometry = unpack_static(truth)
    rotations = segment_rotations(samples.orientation_N_from_B, extrinsics)
    position_n = np.einsum("ij,ntj->nti", r_nv, samples.position_v4_m)
    covariance = np.tile(np.eye(3) * .01**2, (len(samples.time_ns), len(SEGMENTS), 1, 1))
    return samples, rotations, position_n, covariance, geometry


def test_batch_rejects_flyers_and_preserves_distal_endpoints():
    source, rotations, position, covariance, geometry = source_fixture()
    count = 8; position = position[:count].copy(); rotations = rotations[:count]
    covariance = covariance[:count]; time_ns = source.time_ns[:count]
    for knot, distance in zip((1, 3, 6), (.5, 1.0, 2.0)):
        position[knot, 3, 0] += distance
    samples = EstimatorSamples(time_ns, rotations, position, covariance,
                               np.ones(position.shape[:2], bool), np.zeros(len(position), bool))
    result = ArticulatedBatchSmoother(geometry).solve(samples, max_nfev=30)
    assert len(result.rejected) == 3
    assert all(not row["inserted_into_graph"] for row in result.rejected)
    assert np.isfinite(result.state_vector).all()
    # Forearm/Shank antennas are explicit distal wrist/ankle endpoints.
    for name in ("Forearm_L", "Forearm_R", "Shank_L", "Shank_R"):
        assert np.isfinite(result.antenna_position_m[:, SEGMENTS.index(name)]).all()
    assert max(abs(result.root_position_m - position[:, 0]).ravel()) < .03


def test_unilateral_factor_changes_limb_rotation_not_only_global_root():
    source, rotations, position, covariance, geometry = source_fixture()
    source_count = 5; valid = np.zeros((source_count, len(SEGMENTS)), bool)
    valid[:, 0] = True; valid[:, 3] = True
    shifted = position[:source_count].copy(); shifted[-1, 3, 1] += .025
    covariance = covariance[:source_count].copy(); covariance[:, 3] = np.eye(3) * .02**2
    samples = EstimatorSamples(source.time_ns[:source_count], rotations[:source_count], shifted,
                               covariance, valid, np.zeros(source_count, bool))
    result = ArticulatedBatchSmoother(geometry).solve(samples, max_nfev=40)
    state = result.state_vector.reshape(source_count, -1)
    correction = state[-1, 6+3*3:9+3*3]
    right_forearm = state[-1, 6+3*5:9+3*5]
    assert np.linalg.norm(correction) > 1e-5
    assert np.linalg.norm(right_forearm) < 1e-8


def test_fixed_lag_relinearizes_previous_knots():
    _, rotations, position, covariance, geometry = source_fixture()
    manager = GenuineFixedLagArticulatedEstimator(geometry, lag_s=2.0)
    def row(knot, offset):
        observed = position[knot].copy(); observed[:, 0] += offset
        valid = np.zeros(len(SEGMENTS), bool); valid[0] = True
        return {"time_ns": knot * 100_000_000,
                "base_orientation_N_from_S": rotations[knot], "position_N_m": observed,
                "covariance_N_m2": np.tile(np.eye(3)*.05**2, (len(SEGMENTS), 1, 1)),
                "valid_position": valid, "stationary": False}
    manager.append(row(0, 0)); second = manager.append(row(1, 0), max_nfev=30)
    before = second.root_position_m[0].copy()
    third = manager.append(row(2, .08), max_nfev=40)
    after = third.root_position_m[0]
    assert np.linalg.norm(after - before) > 1e-5
    assert len(third.root_position_m) == 3
