from types import SimpleNamespace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as owner
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)


def _correction(left_x: float) -> dict[str, np.ndarray]:
    result = {segment: np.zeros(3) for segment in SEGMENTS}
    result["shank_left"] = np.array([left_x, 0.0, 0.0])
    return result


def test_pose_owner_installs_only_at_availability_and_excludes_regauge_velocity(
    monkeypatch,
) -> None:
    def fake_points(base, correction, _geometry):
        base_y = float(base["pelvis"][0, 0])
        return {
            "ankle_left": np.array([
                correction["shank_left"][0], base_y, -0.9
            ]),
            "ankle_right": np.array([0.1, base_y, -0.9]),
        }

    monkeypatch.setattr(owner, "corrected_proxy_points", fake_points)

    def rotations(fraction):
        result = {segment: np.eye(3) for segment in SEGMENTS}
        result["pelvis"] = np.diag([fraction, 1.0, 1.0])
        return result

    projector_calls = []

    def projector(_base, correction):
        projector_calls.append(correction["shank_left"][0])
        return correction, {
            "post_projection_all_inside_rom": True,
            "fk_direction_residual_maximum_deg": 0.0,
        }

    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=rotations,
        geometry=SimpleNamespace(),
        hinge_projector=projector,
    )
    before = pose.sample(0.1)
    assert before.ankle_offset_world_m["left"][0] == 0.0

    pose.install(
        _correction(0.25), measurement_time_s=0.1, availability_time_s=0.2
    )
    with pytest.raises(ValueError, match="before installed UWB availability"):
        pose.sample(0.199)
    installed = pose.sample(0.2)
    assert installed.velocity_baseline_reset
    assert installed.ankle_offset_world_m["left"][0] == 0.0
    # The 25 cm estimator re-gauge is not divided by a 5 ms sample period.
    assert abs(installed.ankle_offset_velocity_world_mps["left"][0]) < 1e-12
    assert np.isclose(
        installed.ankle_offset_velocity_world_mps["left"][1], 1.0
    )
    held = pose.sample(0.205)
    assert not held.velocity_baseline_reset
    assert 0.0 < held.ankle_offset_world_m["left"][0] < 0.25
    assert held.transition_active
    # The transition itself is also an estimator re-gauge, so it is excluded
    # from the contact detector's physical ankle-speed feature.
    assert abs(held.ankle_offset_velocity_world_mps["left"][0]) < 1e-12
    assert len(projector_calls) >= 5


def test_pose_owner_rejects_availability_reversal(monkeypatch) -> None:
    monkeypatch.setattr(
        owner,
        "corrected_proxy_points",
        lambda _base, _correction, _geometry: {
            "ankle_left": np.zeros(3), "ankle_right": np.zeros(3)
        },
    )
    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=SimpleNamespace(),
        hinge_projector=lambda _base, correction: (
            correction,
            {
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
        ),
    )
    pose.install(
        _correction(0.1), measurement_time_s=0.1, availability_time_s=0.2
    )
    with pytest.raises(ValueError, match="availability reversed"):
        pose.install(
            _correction(0.2), measurement_time_s=0.05,
            availability_time_s=0.15,
        )


def _partition_pose(monkeypatch) -> CausalArticulatedPose:
    monkeypatch.setattr(
        owner,
        "corrected_proxy_points",
        lambda _base, correction, _geometry: {
            "ankle_left": np.array([
                correction["shank_left"][0], 0.0, -0.9
            ]),
            "ankle_right": np.array([0.1, 0.0, -0.9]),
        },
    )
    return CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=SimpleNamespace(),
        hinge_projector=lambda _base, correction: (
            correction,
            {
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
        ),
    )


def test_transition_is_partition_invariant_and_same_time_idempotent(
    monkeypatch,
) -> None:
    owners = [_partition_pose(monkeypatch) for _ in range(3)]
    for pose in owners:
        pose.install(_correction(0.0), measurement_time_s=0.0, availability_time_s=0.0)
        pose.sample(0.0)
        pose.install(_correction(0.24), measurement_time_s=0.0, availability_time_s=0.01)
    for time_s in np.arange(0.015, 0.131, 0.005):
        dense = owners[0].sample(float(time_s))
    sparse = owners[1].sample(0.13)
    for time_s in (0.011, 0.017, 0.017, 0.043, 0.071, 0.129, 0.13):
        extra = owners[2].sample(time_s)
    np.testing.assert_allclose(
        dense.correction_rotvec["shank_left"],
        sparse.correction_rotvec["shank_left"], atol=1e-14,
    )
    np.testing.assert_allclose(
        extra.correction_rotvec["shank_left"],
        sparse.correction_rotvec["shank_left"], atol=1e-14,
    )
    repeated = owners[2].sample(0.13)
    np.testing.assert_array_equal(
        repeated.correction_rotvec["shank_left"],
        extra.correction_rotvec["shank_left"],
    )


def test_mid_transition_retarget_freezes_old_pose_without_future_leak(
    monkeypatch,
) -> None:
    dense = _partition_pose(monkeypatch)
    sparse = _partition_pose(monkeypatch)
    for pose in (dense, sparse):
        pose.install(_correction(0.0), measurement_time_s=0.0, availability_time_s=0.0)
        pose.sample(0.0)
        pose.install(_correction(0.24), measurement_time_s=0.0, availability_time_s=0.01)
    for time_s in np.arange(0.015, 0.06, 0.005):
        dense.sample(float(time_s))
    # Neither caller samples at the retarget epoch.  install() itself freezes
    # the old absolute-time transition before assigning the new causal target.
    for pose in (dense, sparse):
        pose.install(_correction(-0.12), measurement_time_s=0.05, availability_time_s=0.06)
        with pytest.raises(ValueError, match="before installed UWB availability"):
            pose.sample(0.059)
    dense_result = dense.sample(0.09)
    sparse_result = sparse.sample(0.09)
    np.testing.assert_allclose(
        dense_result.correction_rotvec["shank_left"],
        sparse_result.correction_rotvec["shank_left"], atol=1e-14,
    )
