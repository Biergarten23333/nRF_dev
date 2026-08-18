from __future__ import annotations

import inspect
import numpy as np
import pytest

from biospur_fusion.imu_pose_v2 import so3
from biospur_fusion.imu_pose_v2.calibration import fit_joint_calibration
from biospur_fusion.imu_pose_v2.estimator import (
    ContinuousArticulatedEstimator, reject_posthoc_covariance_rewrite,
)
from biospur_fusion.imu_pose_v2.fk import articulated_fk, old_torso_mutation
from biospur_fusion.imu_pose_v2.joints import JOINTS
from biospur_fusion.imu_pose_v2.observability import TOLERANCES, observability_report
from biospur_fusion.imu_pose_v2.scheduler import scheduled_replay
from biospur_fusion.imu_pose_v2.synthetic import (
    frontend_frame, identity_orientations, oracle_fk, oracle_quaternion,
    synthetic_calibration_rows,
)


def _bundle(mapping, fit_actions):
    return fit_joint_calibration(synthetic_calibration_rows(mapping, fit_actions), mapping, fit_actions)


def test_torso_orientation_drives_chest_and_shoulders_independently():
    q = identity_orientations()
    q["torso"] = oracle_quaternion(np.array([1., 0., 0.]), np.pi / 2)
    actual = articulated_fk(q); expected = oracle_fk(q); old = old_torso_mutation(q)
    assert np.allclose(actual["chest"], expected["chest"])
    assert np.allclose(actual["shoulder_left"], expected["shoulder_left"])
    assert not np.allclose(old["chest"], expected["chest"])


def test_coupled_solver_uses_cholesky_and_actual_factor_ledger(mapping, fit_actions):
    source = inspect.getsource(__import__(
        "biospur_fusion.imu_pose_v2.estimator", fromlist=["*"]))
    assert "np.linalg.inv" not in source
    bundle = _bundle(mapping, fit_actions)
    neutral = {joint.name: np.array([1., 0., 0., 0.]) for joint in JOINTS}
    estimator = ContinuousArticulatedEstimator(bundle, neutral_relative=neutral)
    nodes = tuple(sorted(mapping))
    for tick_index in range(3):
        time_ns = 10_000_000_000 + tick_index * 20_000_000
        frames = {node: frontend_frame(node, index, time_ns, yaw_rad=.002*index*tick_index)
                  for index, node in enumerate(nodes)}
        result = estimator.update(time_ns, frames)
    assert result.status == "FILTERED"
    assert np.linalg.eigvalsh(result.segment_covariance_rad2).min() >= -1e-10
    off_diagonal = result.segment_covariance_rad2.copy()
    for index in range(10):
        off_diagonal[index*3:index*3+3, index*3:index*3+3] = 0
    assert np.linalg.norm(off_diagonal) > 0
    names = {row.factor for row in estimator.factor_ledger}
    assert "raw_imu_orientation_likelihood" in names
    assert "neutral_relative_pose_reference" in names
    assert "temporal_relative_motion" in names
    assert "calibration_covariance" not in names
    assert all(row.source_uids and row.jacobian_blocks for row in estimator.factor_ledger)


def test_observability_uses_actual_runtime_matrices_with_fixed_sweep(mapping, fit_actions):
    bundle = _bundle(mapping, fit_actions)
    neutral = {joint.name: np.array([1., 0., 0., 0.]) for joint in JOINTS}
    estimator = ContinuousArticulatedEstimator(bundle, neutral_relative=neutral)
    time_ns = 5_000_000_000
    frames = {node: frontend_frame(node, index, time_ns) for index, node in enumerate(sorted(mapping))}
    estimator.update(time_ns, frames)
    report = observability_report(estimator.actual_information_components())
    assert report["matrix_source"] == "ACTUAL_ACCEPTED_RUNTIME_FACTORS"
    assert tuple(row["relative_tolerance"] for row in report["svd_relative_tolerance_sweep"]) == TOLERANCES
    assert "DATA_IDENTIFIED_GLOBAL_YAW=false" in report["gauge_statement"]


def test_fixed_50hz_grid_emits_every_gap_tick_and_marks_unavailable(mapping, fit_actions):
    bundle = _bundle(mapping, fit_actions)
    nodes = tuple(sorted(mapping)); start = 20_000_000_000
    frames = [frontend_frame(node, index, start) for index, node in enumerate(nodes)]
    output = scheduled_replay(
        ContinuousArticulatedEstimator(bundle), frames, nodes,
        start, start + 2_200_000_000,
    )
    assert len(output) == 110
    assert all(b.scheduled_time_ns-a.scheduled_time_ns == 20_000_000 for a, b in zip(output, output[1:]))
    assert output[-1].status == "UNAVAILABLE"
    early = float(np.trace(output[0].segment_covariance_rad2))
    late = float(np.trace(output[-1].segment_covariance_rad2))
    assert late >= early
    assert max(output[-1].input_age_ns.values()) > 2_000_000_000


def test_posthoc_covariance_or_availability_rewrite_always_fails():
    with pytest.raises(RuntimeError):
        reject_posthoc_covariance_rewrite(np.eye(3), scale=.15)


def test_h_action_label_permutation_or_deletion_cannot_change_solver_state(mapping, fit_actions):
    bundle = _bundle(mapping, fit_actions); time_ns = 31_000_000_000
    frames = {node: frontend_frame(node, index, time_ns, yaw_rad=.01*index)
              for index, node in enumerate(sorted(mapping))}
    states = []
    for label in ("H00_walk_turn", "H02_golf", ""):
        estimator = ContinuousArticulatedEstimator(bundle)
        estimator.notify_action_boundary(label)
        pose = estimator.update(time_ns, frames)
        states.append(b"".join(np.asarray(pose.segment_quaternions_W_S[s], dtype="<f8").tobytes()
                               for s in sorted(pose.segment_quaternions_W_S)))
    assert states[0] == states[1] == states[2]
