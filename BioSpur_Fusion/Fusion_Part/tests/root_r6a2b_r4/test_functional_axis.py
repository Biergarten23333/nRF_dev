from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a0.math3d import so3_exp
from biospur_fusion.root_r6a2b.functional_axis import (
    axis_line_angle, estimate_undirected_axis, motion_weights,
    relative_increment_evidence, sampled_relative_evidence, scatter_axis,
    synthetic_causal_suite,
)
from biospur_fusion.root_r6a2b.real_profile import profile_checksum


FUSION = Path(__file__).resolve().parents[2]
RESULT = FUSION / "logs/root_r6a2b_r4_functional_axis_20260826T170000Z"
R3 = FUSION / "logs/root_r6a2b_r3_session_relative_joint_calibration_20260826T160000Z"
SYNTHETIC = synthetic_causal_suite()


def load(name: str):
    return json.loads((RESULT / name).read_text())


def test_undirected_axis_is_sign_invariant() -> None:
    axis = np.array([.2, -.3, .932737905])
    fitted, _, _ = scatter_axis(np.stack((axis, -axis)), np.ones(2))
    assert axis_line_angle(fitted, axis) < 1e-12
    assert axis_line_angle(axis, -axis) < 1e-12


def test_perfect_hinge_is_invariant_to_moving_parent() -> None:
    assert SYNTHETIC["perfect_hinge_stationary_parent_axis_error_rad"] < 1e-12
    assert SYNTHETIC["perfect_hinge_moving_parent_axis_error_rad"] < 1e-12
    assert SYNTHETIC["moving_parent_invariance_axis_line_error_rad"] < 1e-12


def test_flexion_extension_reversal_is_one_axis_line() -> None:
    assert SYNTHETIC["flexion_extension_undirected_sign_error_rad"] < 1e-12
    assert SYNTHETIC["pauses_present"] is True
    assert SYNTHETIC["pause_random_axis_domination"] is False


def test_quaternion_sign_flip_does_not_change_orientation_or_axis_input() -> None:
    assert SYNTHETIC["quaternion_sign_flip_max_rotation_difference"] < 1e-12
    assert SYNTHETIC["quaternion_sign_flip_phi_difference_linf"] < 1e-12


def test_continuous_low_motion_weight_approaches_zero() -> None:
    weights = motion_weights(np.array([0.0, 1e-9, 1.0, 1e9]), 1.0)
    assert weights[0] == 0.0
    assert 0.0 < weights[1] < weights[2] < weights[3] <= 1.0
    assert SYNTHETIC["low_rate_noise_weight_limit"] < 1e-10


def test_native_variable_dt_and_near_pi_branch_are_finite() -> None:
    minimum, maximum = SYNTHETIC["irregular_native_dt_min_max_s"]
    assert minimum > 0.0 and maximum > minimum
    assert SYNTHETIC["irregular_dt_axis_error_rad"] < 1e-12
    assert np.isclose(SYNTHETIC["near_pi_log_norm_rad"], np.pi - 1e-7)
    assert SYNTHETIC["near_pi_axis_error_rad"] < 1e-12


def test_no_increment_crosses_declared_gap_or_window() -> None:
    rotations = np.asarray([so3_exp(np.array([0.0, 0.0, .1 * index])) for index in range(6)])
    times = np.arange(6, dtype=np.int64) * 5_000_000
    parent = np.repeat(np.eye(3)[None], 6, axis=0)
    intervals = np.array([0, 0, 0, 1, 1, 1])
    evidence = relative_increment_evidence(times, parent, rotations, intervals)
    assert len(evidence.dt_s) == 4
    assert SYNTHETIC["cross_gap_increment_created"] is False


def test_parent_frame_transport_is_explicit_in_real_method() -> None:
    native = load("NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json")
    method = native["method"]
    assert method["parent_motion_removed"] is True
    assert method["axis_frame"] == "calibrated parent-segment session-reference frame"
    assert method["native_dt_used"] is True
    assert method["gap_crossing"] is False


def test_bounded_off_axis_motion_is_reported_not_forced_away() -> None:
    assert SYNTHETIC["bounded_off_axis_energy_fraction"] > 0.0
    native = load("NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json")["per_joint"]
    assert all(row["axis_estimate"]["off_axis_energy_fraction"] > 0.0 for row in native.values())


def test_bout_uncertainty_resamples_complete_bouts() -> None:
    bouts = load("FUNCTIONAL_AXIS_BOUT_AUDIT.json")
    assert bouts["bout_resampling_not_individual_sample_bootstrap"] is True
    for row in load("NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json")["per_joint"].values():
        uncertainty = row["axis_estimate"]["principal_axis_uncertainty_bout_bootstrap"]
        assert uncertainty["resampling_unit"] == "complete motion bout"
        assert uncertainty["replicates"] == 400
        assert row["axis_estimate"]["bout_count"] > 0


def test_historical_dispersion_is_reproduced_and_defects_are_explicit() -> None:
    historical = load("HISTORICAL_FUNCTIONAL_AXIS_REPRODUCTION.json")
    causal = load("FUNCTIONAL_AXIS_CAUSAL_AUDIT.json")
    assert historical["status"] == "REPRODUCED" and historical["all_four_within_tolerance"]
    assert historical["implementation"]["axis_sign"].startswith("correct undirected")
    assert "changing child frames" in historical["implementation"]["coordinate_frame"]
    assert causal["implementation_repaired"] is True


def test_native_replay_is_deterministic() -> None:
    evidence = np.load(RESULT / "NATIVE_AXIS_EVIDENCE.npz", allow_pickle=False)
    phi = evidence["knee_right_action_phi_parent"]
    dt = evidence["knee_right_action_dt_s"]
    interval = evidence["knee_right_action_interval_id"]
    times = evidence["knee_right_action_time_ns"]
    dummy = sampled_relative_evidence(np.arange(len(phi) + 1, dtype=np.int64),
                                      np.repeat(np.eye(3)[None], len(phi) + 1, axis=0))
    # Determinism of the estimator itself is verified using the stored native
    # vectors as a direct RelativeIncrementEvidence replacement.
    from dataclasses import replace
    direct = replace(dummy, start_time_ns=times, stop_time_ns=times + np.rint(dt * 1e9).astype(np.int64),
                     dt_s=dt, interval_id=interval, phi_parent_session=phi,
                     phi_right_local_child=phi, omega_parent_session=phi / dt[:, None],
                     parent_rotation=np.repeat(np.eye(3)[None], len(phi), axis=0),
                     child_rotation=np.repeat(np.eye(3)[None], len(phi), axis=0))
    stored = load("NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json")["per_joint"]["knee_right"]
    scale = stored["stationary_distribution"]["scale_rms_rad_s"]
    first = estimate_undirected_axis(direct, scale, bootstrap_replicates=40)
    second = estimate_undirected_axis(direct, scale, bootstrap_replicates=40)
    assert first == second


def test_profile_and_predecessor_are_immutable_and_no_heldout_was_opened() -> None:
    profile = load("DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json")
    r3 = json.loads((R3 / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    access = load("RAW_EVIDENCE_ACCESS_AUDIT.json")
    assert profile_checksum(profile) == profile["profile_checksum_sha256"]
    assert profile["predecessor"]["profile_checksum"] == r3["profile_checksum_sha256"]
    assert access["held_out_members_or_intervals_opened"] == []
    assert access["Golf_or_Boxing_opened"] is False
    assert set(access["opened_actions"]) == {
        "initial_still2", "left_elbow", "right_elbow2", "left_knee", "right_knee",
    }
