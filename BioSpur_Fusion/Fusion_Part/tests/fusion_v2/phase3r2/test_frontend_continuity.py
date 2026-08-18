from __future__ import annotations

import numpy as np

from biospur_fusion.imu_pose_v2.frontend import ContinuousNodeFrontend
from biospur_fusion.imu_pose_v2.synthetic import synthetic_imu_stream


def _run(frontend, rows, age_us=2500.0):
    last = None
    for row in rows:
        last = frontend.update(row, sample_age_us=age_us)
    return last


def test_full_session_equals_chunked_serialized_handoff_byte_for_byte():
    rows = synthetic_imu_stream("BSF1120", samples=900)
    full = ContinuousNodeFrontend("BSF1120"); _run(full, rows)
    first = ContinuousNodeFrontend("BSF1120"); _run(first, rows[:451])
    second = ContinuousNodeFrontend.deserialize(first.serialize()); _run(second, rows[451:])
    assert full.serialize() == second.serialize()


def test_action_boundary_is_not_lifecycle_event():
    rows = synthetic_imu_stream("BSF1120", samples=500)
    frontend = ContinuousNodeFrontend("BSF1120")
    _run(frontend, rows[:250]); before = frontend.serialize()
    frontend.notify_action_boundary("02_t_pose")
    assert frontend.serialize() == before
    _run(frontend, rows[250:])
    assert frontend.action_boundary_reset_count == 0
    assert frontend.reset_epoch == 0


def test_boot_reset_is_explicit_and_only_reset_path():
    first = synthetic_imu_stream("BSF1120", boot=1, samples=20)
    second = synthetic_imu_stream("BSF1120", boot=2, samples=20)
    frontend = ContinuousNodeFrontend("BSF1120")
    _run(frontend, first)
    result = _run(frontend, second)
    assert frontend.reset_epoch == 1
    assert result.status == "REINITIALIZING"


def test_eskf_reset_jacobian_is_applied():
    frontend = ContinuousNodeFrontend("BSF1120")
    old = frontend.P.copy()
    innovation = np.array([1.0, -0.5, 0.25])
    H = np.zeros((3, 9)); H[:, :3] = np.eye(3)
    noise = np.eye(3) * 0.01
    S = H @ old @ H.T + noise
    gain = np.linalg.solve(S, H @ old).T
    delta = gain @ innovation
    identity = np.eye(9)
    joseph = (identity-gain@H) @ old @ (identity-gain@H).T + gain@noise@gain.T
    frontend._correct(innovation, H, noise)
    assert not np.allclose(frontend.P, joseph, atol=1e-16, rtol=1e-12)
    assert np.linalg.eigvalsh(frontend.P).min() >= -1e-12
    assert np.linalg.norm(delta[:3]) > 0


def test_sample_age_sweep_has_nonzero_deterministic_uncertainty_response():
    rows = synthetic_imu_stream("BSF1120", samples=700, gyro=np.array([0.1, -0.04, 0.06]))
    traces = []
    states = []
    for age_us in (500.0, 1000.0, 2000.0, 5000.0):
        frontend = ContinuousNodeFrontend("BSF1120")
        _run(frontend, rows, age_us)
        traces.append(float(np.trace(frontend.P[:3, :3])))
        states.append(frontend.serialize())
    assert traces == sorted(traces)
    assert len(set(states)) == 4
    repeat = ContinuousNodeFrontend("BSF1120"); _run(repeat, rows, 2000.0)
    assert repeat.serialize() == states[2]
