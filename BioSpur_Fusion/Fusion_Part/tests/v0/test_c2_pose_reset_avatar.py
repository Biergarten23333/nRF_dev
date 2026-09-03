from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation

from tools.build_c2_avatar_white_mannequin import (
    add_oblique_front_named_camera_preset,
    correct_reversed_named_camera_presets,
)

from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import (
    _axial_heading_delta,
    _factor_interval_replay,
    _flexed_elbow_replay_delta,
    _frame_with_x_axis,
    _extended_hinge_replay_delta,
    _qmt_axis_heading_mod_pi,
    _select_upper_arm_close_body_branch,
    _window_inside_registered_interval,
    _qmt_window_starts,
    _robust_quantile_frame,
    _signed_horizontal_angle,
    _yaw_correction_matrix,
)
from biospur_fusion.c2_coupled_progressive.math_utils import rotation_to_qmt_wxyz
from biospur_fusion.c2_coupled_progressive.output_coordinates import (
    apply_output_coordinate_convention,
    freeze_capture_wide_lateral_reflection,
)


def test_yaw_correction_matrix_cancels_synthetic_drift() -> None:
    drift = np.linspace(0.0, np.deg2rad(80.0), 101)
    raw = Rotation.from_rotvec(
        np.column_stack([np.zeros(101), np.zeros(101), drift])
    ).as_matrix()
    corrected = _yaw_correction_matrix(-drift) @ raw
    identity = np.broadcast_to(np.eye(3), corrected.shape)
    assert np.allclose(corrected, identity, atol=1e-10)


def test_qmt_windows_cover_full_signal_without_three_row_selection() -> None:
    starts = _qmt_window_starts(701)
    assert starts[0] == 0
    assert starts[-1] + 40 == 701
    assert len(starts) > 30
    covered = np.zeros(701, dtype=bool)
    for start in starts:
        covered[start : start + 40] = True
    assert np.all(covered)


def test_factor_frame_is_right_handed_and_preserves_fitted_axis() -> None:
    axis = np.array([0.2, -0.7, 0.4])
    frame = _frame_with_x_axis(axis)
    assert np.allclose(frame.T @ frame, np.eye(3), atol=1e-12)
    assert np.linalg.det(frame) > 0.999999
    assert np.allclose(frame[:, 0], axis / np.linalg.norm(axis), atol=1e-12)


def test_horizontal_angle_rotates_thigh_toward_forward() -> None:
    thigh = np.array([[1.0, 0.0, -0.2]])
    forward = np.array([[0.0, 1.0, 0.0]])
    angle = _signed_horizontal_angle(thigh, forward)
    assert np.allclose(angle, np.array([np.pi / 2.0]))


def test_elbow_hinge_window_excludes_pronation_half() -> None:
    time_s = np.arange(0.0, 35.05, 0.05)
    assert _window_inside_registered_interval(
        "elbow_left", 5, time_s, 100, 400
    )
    assert not _window_inside_registered_interval(
        "elbow_left", 5, time_s, 400, 700
    )
    assert not _window_inside_registered_interval(
        "elbow_left", 6, time_s, 100, 400
    )


def test_protocol_replay_selects_registered_quantile_not_single_extreme() -> None:
    score = np.r_[np.linspace(0.0, 1.0, 100), 99.0]
    candidates = np.arange(len(score))
    frame, selected, audit = _robust_quantile_frame(score, candidates, 0.85)
    assert frame < 100
    assert selected < 1.0
    assert audit["formal_score_max"] == 99.0
    assert audit["registered_quantile"] == 0.85


def test_complete_extended_limb_interval_resolves_heading_branch() -> None:
    parent = Rotation.from_euler("y", -90.0, degrees=True)
    child = Rotation.from_euler("z", 70.0, degrees=True) * parent
    parent_quat = np.repeat(rotation_to_qmt_wxyz(parent)[None, :], 600, axis=0)
    child_quat = np.repeat(rotation_to_qmt_wxyz(child)[None, :], 600, axis=0)
    delta, audit = _extended_hinge_replay_delta(parent_quat, child_quat)
    assert np.isclose(np.degrees(delta), -70.0, atol=1e-10)
    assert audit["row_count"] == 600
    assert audit["post_replay_alignment_median_deg"] < 1e-8


def test_complete_flexed_elbow_interval_recovers_sagittal_heading() -> None:
    torso = Rotation.identity()
    upper = Rotation.identity()
    forearm = Rotation.from_euler("z", 35.0, degrees=True) * Rotation.from_euler(
        "x", 90.0, degrees=True
    )
    torso_quat = np.repeat(rotation_to_qmt_wxyz(torso)[None, :], 300, axis=0)
    upper_quat = np.repeat(rotation_to_qmt_wxyz(upper)[None, :], 300, axis=0)
    forearm_quat = np.repeat(
        rotation_to_qmt_wxyz(forearm)[None, :], 300, axis=0
    )
    delta, audit = _flexed_elbow_replay_delta(
        torso_quat, upper_quat, forearm_quat
    )
    assert np.isclose(np.degrees(delta), -35.0, atol=1e-10)
    assert audit["row_count"] == 300
    assert audit["post_replay_forward_alignment_median_deg"] < 1e-8


def test_qmt_axis_heading_resolves_pi_branch_from_functional_reference() -> None:
    measurements = np.deg2rad(np.array([42.0, 48.0, -137.0, 51.0]))
    updates = [
        {
            "registered_functional_window": True,
            "quality": 0.8,
            "delta_measurement_rad": value,
        }
        for value in measurements
    ]
    resolved, audit = _qmt_axis_heading_mod_pi(updates, np.deg2rad(-132.0))
    assert -145.0 < np.degrees(resolved) < -125.0
    assert audit["row_count"] == len(measurements)
    assert audit["double_angle_concentration"] > 0.99


def test_elbow_axis_factor_treats_axis_as_undirected_line() -> None:
    target = np.repeat(np.array([[1.0, 0.0, 0.0]]), 200, axis=0)
    axis = np.repeat(np.array([[0.0, -1.0, 0.0]]), 200, axis=0)
    delta, audit = _axial_heading_delta(
        axis, target, np.deg2rad(-85.0)
    )
    assert np.isclose(np.degrees(delta), -90.0, atol=1e-10)
    assert audit["double_angle_concentration"] > 0.999999
    opposite, _ = _axial_heading_delta(
        -axis, target, np.deg2rad(-85.0)
    )
    assert np.isclose(opposite, delta, atol=1e-12)


def test_factor_replay_holds_prior_state_until_next_evidence() -> None:
    time_s = np.arange(0.0, 101.0)
    factors = [
        {
            "start_time_s": 10.0,
            "stop_time_s": 40.0,
            "filtered_delta_rad": np.deg2rad(20.0),
        },
        {
            "start_time_s": 70.0,
            "stop_time_s": 90.0,
            "filtered_delta_rad": np.deg2rad(50.0),
        },
    ]
    replay = np.degrees(_factor_interval_replay(time_s, factors))
    assert np.allclose(replay[10:41], 20.0)
    assert np.allclose(replay[70:91], 50.0)
    assert np.allclose(replay[41:70], 20.0)


def test_directed_actions_own_pi_branch_over_close_body_cue() -> None:
    direction = np.repeat(np.array([[-0.2, 0.1, -0.95]]), 100, axis=0)
    outward = np.repeat(np.array([[-1.0, 0.0, 0.0]]), 100, axis=0)
    selected, audit = _select_upper_arm_close_body_branch(
        0.0, direction, outward, np.deg2rad(5.0)
    )
    assert np.isclose(selected, 0.0)
    assert audit["exact_upper_arm_angle_used"] is False
    assert audit["contralateral_mirror_used"] is False
    assert audit["close_body_cue_used_as_pi_branch_owner"] is False
    assert audit["close_body_pi_branch_resolvable"] is False


def test_capture_wide_output_reflection_is_fixed_involutive_and_length_preserving() -> None:
    angle = np.linspace(-0.15, 0.15, 31)
    pelvis = Rotation.from_euler("z", angle[:, None]).as_quat()[:, [3, 0, 1, 2]]
    trajectory = {
        "trajectory": {
            "00": {
                "pelvis": {"quat_world_segment_wxyz": pelvis},
            }
        }
    }
    convention = freeze_capture_wide_lateral_reflection(trajectory)
    matrix = convention["matrix_world_output_from_internal"]
    assert np.isclose(np.linalg.det(matrix), -1.0)
    assert np.allclose(matrix @ matrix, np.eye(3), atol=1e-12)
    joints = {
        "a": np.array([0.2, -0.4, 1.1]),
        "b": np.array([-0.5, 0.3, 0.2]),
    }
    output = apply_output_coordinate_convention(trajectory, joints)
    assert np.isclose(
        np.linalg.norm(output["b"] - output["a"]),
        np.linalg.norm(joints["b"] - joints["a"]),
    )
    restored = apply_output_coordinate_convention(
        trajectory,
        output,
    )
    assert np.allclose(restored["a"], joints["a"])
    assert np.allclose(restored["b"], joints["b"])


def test_capture_wide_output_reflection_does_not_modify_quaternions() -> None:
    pelvis = np.repeat(np.array([[1.0, 0.0, 0.0, 0.0]]), 10, axis=0)
    trajectory = {
        "trajectory": {
            "00": {
                "pelvis": {"quat_world_segment_wxyz": pelvis.copy()},
            }
        }
    }
    before = trajectory["trajectory"]["00"]["pelvis"][
        "quat_world_segment_wxyz"
    ].copy()
    freeze_capture_wide_lateral_reflection(trajectory)
    assert np.array_equal(
        trajectory["trajectory"]["00"]["pelvis"][
            "quat_world_segment_wxyz"
        ],
        before,
    )
    assert trajectory["output_coordinate_convention"]["quaternions_modified"] is False


def test_named_camera_preset_migration_swaps_only_camera_semantics() -> None:
    old_formula = (
        "const lateral=Math.atan2(dy,dx),front=Math.PI-lateral,"
        "rear=-lateral,top=-lateral;"
    )
    html = (
        '<script>const DATA={"jointNames":["pelvis_center"],'
        '"viewGauge":{"frontYawRad":3.25,"rearYawRad":0.1},'
        '"episodes":[{"frames":[[1,2,3]]}]};'
        + old_formula
        + "</script>"
    )
    corrected, changed = correct_reversed_named_camera_presets(html)
    assert changed is True
    assert '"frontYawRad":0.1,"rearYawRad":3.25' in corrected
    assert (
        "const lateral=Math.atan2(dy,dx),front=-lateral,"
        "rear=Math.PI-lateral,top=front;"
    ) in corrected
    assert '"frames":[[1,2,3]]' in corrected
    second, second_changed = correct_reversed_named_camera_presets(corrected)
    assert second_changed is False
    assert second == corrected


def test_oblique_camera_migration_changes_only_display_camera() -> None:
    html = (
        '<button class="view" data-view="front" type="button">从前方看</button>'
        "<script>const DATA={\"episodes\":[{\"frames\":[[1,2,3]]}]};"
        "const VIEW_LABEL={front:'从前方看',rear:'从后方看',side:'从右侧看',top:'从上方看',three:'3D 相机'};"
        "const rows={front:'相机从人体前方看。',rear:'相机从人体后方看。'};"
        "if(!['front','rear','side','top','three'].includes(viewMode))return;"
        "if(viewMode==='front'){yaw=front;pitch=0}else if(viewMode==='rear'){};"
        "allowedViews=new Set(['front','rear','side','top','three']);</script>"
    )
    migrated, changed = add_oblique_front_named_camera_preset(html)
    assert changed is True
    assert 'data-view="oblique"' in migrated
    assert "yaw=front-Math.PI/4;pitch=.18" in migrated
    assert '"frames":[[1,2,3]]' in migrated
    second, second_changed = add_oblique_front_named_camera_preset(migrated)
    assert second_changed is False
    assert second == migrated
