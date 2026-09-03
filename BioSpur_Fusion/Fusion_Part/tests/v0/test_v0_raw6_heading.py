from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from biospur_fusion.v0.contracts import IDENTITY
from biospur_fusion.v0.raw6_heading import (
    EDGES,
    Raw6Episode,
    SEGMENTS,
    fit_edgewise,
    fit_unified_graph,
    qmt_edge_heading,
    raw6_episode_from_rows,
    sensor_display_frames,
    _selected_rows,
)
from biospur_fusion.v0.raw6_synthetic import synthetic_case
from biospur_fusion.v0.unified_calibration import UnifiedCalibrationObjective


ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "logs/pure_imu_v0_raw6_edge_global_20260828T050124Z"


def _raw_rows(forbidden_value: float) -> dict[str, np.ndarray]:
    dtype = np.dtype([
        ("global_time_ns", "<i8"),
        ("status", "u1"),
        ("acc_raw", "<i2", (3,)),
        ("gyro_raw", "<i2", (3,)),
        # Deliberately present hostile fields.  The direct path must neither
        # inspect nor change behavior when these values change.
        ("vendor_quat", "<f8", (4,)),
        ("shared_ik_rotation", "<f8", (3, 3)),
        ("corrected_quaternion", "<f8", (4,)),
        ("viewer_pose", "<f8", (3,)),
        ("action_pose_truth", "<f8", (3,)),
    ])
    time = np.arange(0, 2_000_000_000, 10_000_000, dtype=np.int64)
    output = {}
    for index, node in enumerate(IDENTITY):
        rows = np.zeros(len(time), dtype=dtype)
        rows["global_time_ns"] = time
        rows["status"] = 1
        rows["acc_raw"][:, 2] = 2048
        rows["gyro_raw"][:, index % 3] = 16
        for name in (
            "vendor_quat", "shared_ik_rotation", "corrected_quaternion",
            "viewer_pose", "action_pose_truth",
        ):
            rows[name] = forbidden_value
        output[node] = rows
    return output


class _VQFSpy:
    constructors: list[tuple[tuple[object, ...], dict[str, object]]] = []
    updates: list[tuple[np.ndarray, np.ndarray]] = []

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.constructors.append((args, kwargs))

    def updateBatch(self, gyr: np.ndarray, acc: np.ndarray) -> dict[str, np.ndarray]:
        self.updates.append((gyr.copy(), acc.copy()))
        quat = np.zeros((len(gyr), 4))
        quat[:, 0] = 1.0
        return {
            "quat6D": quat,
            "restDetected": np.zeros(len(gyr), bool),
            "bias": np.zeros((len(gyr), 3)),
        }


def _episode_diagnostic() -> dict[str, object]:
    return {
        "EPISODE_COMPLETENESS": "PASS",
        "phases": [{
            "phase": "FORMAL_ACTION_OR_HOLD",
            "start_global_time_ns": 0,
            "stop_global_time_ns_exclusive": 2_000_000_000,
        }],
    }


def test_direct_frontend_ignores_prohibited_orientation_ik_and_pose_fields(
    monkeypatch,
) -> None:
    _VQFSpy.constructors.clear()
    _VQFSpy.updates.clear()
    monkeypatch.setattr("biospur_fusion.v0.raw6_heading.VQF", _VQFSpy)
    first = raw6_episode_from_rows(
        capture="SYNTHETIC_CAPTURE_A",
        action="ordinary_action",
        partition="IDENTIFICATION_TRAIN",
        rows_by_node=_raw_rows(1.0),
        identity=IDENTITY,
        episode_diagnostic=_episode_diagnostic(),
    )
    first_updates = [(g.copy(), a.copy()) for g, a in _VQFSpy.updates]
    _VQFSpy.updates.clear()
    second = raw6_episode_from_rows(
        capture="SYNTHETIC_CAPTURE_A",
        action="ordinary_action",
        partition="IDENTIFICATION_TRAIN",
        rows_by_node=_raw_rows(-987654.0),
        identity=IDENTITY,
        episode_diagnostic=_episode_diagnostic(),
    )
    assert len(_VQFSpy.constructors) == 20
    assert all(call[1] == {"magDistRejectionEnabled": False}
               for call in _VQFSpy.constructors)
    assert len(first_updates) == len(_VQFSpy.updates) == 10
    for (gyr_a, acc_a), (gyr_b, acc_b) in zip(first_updates, _VQFSpy.updates):
        np.testing.assert_array_equal(gyr_a, gyr_b)
        np.testing.assert_array_equal(acc_a, acc_b)
    for segment in SEGMENTS:
        np.testing.assert_array_equal(
            first.rotation_world_sensor[segment],
            second.rotation_world_sensor[segment],
        )
    assert first.audit["magnetometer_used"] is False
    assert first.audit["vendor_orientation_truth_used"] is False
    assert first.audit["old_profile_or_ik_state_used"] is False
    assert first.audit["action_label_pose_truth_used"] is False


def test_b4_absence_contributes_no_factor_and_is_not_a_prerequisite() -> None:
    objective = object.__new__(UnifiedCalibrationObjective)
    objective.obs = SimpleNamespace(r3d_actions={})
    assert objective._b4_en_bloc_common_rate_blocks({}) == []


def test_complete_episode_selection_excludes_unclassified_slack_and_keeps_phases() -> None:
    phase = np.asarray(
        ["UNCLASSIFIED_COMPLETE_EPISODE"] * 10
        + ["VERIFIED_PRE_REST"] * 20
        + ["REST_TO_ACTION_TRANSITION"] * 30
        + ["FORMAL_ACTION_OR_HOLD"] * 80
        + ["ACTION_TO_REST_TRANSITION"] * 30
        + ["VERIFIED_POST_REST"] * 20
        + ["UNCLASSIFIED_COMPLETE_EPISODE"] * 10,
        dtype="U40",
    )
    episode = SimpleNamespace(time_ns=np.arange(len(phase)), phase=phase)
    selected = _selected_rows(episode, include_transitions=True)
    assert len(selected) <= 90
    assert "UNCLASSIFIED_COMPLETE_EPISODE" not in set(phase[selected])
    assert set(phase[selected]) == {
        "VERIFIED_PRE_REST",
        "REST_TO_ACTION_TRANSITION",
        "FORMAL_ACTION_OR_HOLD",
        "ACTION_TO_REST_TRANSITION",
        "VERIFIED_POST_REST",
    }


def test_immutable_preselection_excludes_b4_hxx_and_capture3() -> None:
    path = RUN / "METADATA_PRESELECTION.json"
    selection = json.loads(path.read_text(encoding="utf-8"))
    assert path.stat().st_mode & 0o222 == 0
    assert selection["scientific_semantics"]["b4_selected"] is False
    assert selection["global_exclusions"]["b4"] == "ABSENT_AND_NOT_REQUIRED"
    assert selection["global_exclusions"]["capture3"] == "ALL_PATHS_AND_PAYLOADS"
    for capture in selection["captures"].values():
        actions = {row["action"] for row in capture["selected_actions"]}
        assert not any("b4" in action.lower() for action in actions)
        assert not any(action.lower().startswith("h0") for action in actions)


def test_broad_independent_full_circle_staging_recovers_exact_nine_heading_graph() -> None:
    truth, factors = synthetic_case(noise_mps2=0.0)
    edgewise = fit_edgewise(factors)
    initial = np.asarray([
        edgewise["accumulated_headings_rad"][segment]
        for segment in SEGMENTS[1:]
    ])
    result = fit_unified_graph(factors, initial, starts=3, seed=9301)
    runs = result["multistart"]
    assert result["numeric_rank_after_gauge"] == 9
    assert result["multistart_max_spread_deg"] < 1e-3
    assert result["multistart_contract"]["local_basin_only"] is False
    assert result["multistart_contract"]["broad_independent_start_count"] == 2
    assert max(abs(value) for run in runs[1:]
               for value in run["raw_broad_start_headings_deg"]) > 150.0
    assert max(run["cost"] for run in runs) - min(run["cost"] for run in runs) < 1e-8
    for run in runs:
        assert set(run["full_circle_profile"]) == {edge for edge, *_ in EDGES}
        assert all(row["grid_count"] == 72
                   for row in run["full_circle_profile"].values())
        assert all(np.isclose(row["full_circle_coverage_rad"], 2.0 * np.pi)
                   for row in run["full_circle_profile"].values())
    observed = np.asarray([
        result["headings_rad"][segment] for segment in SEGMENTS[1:]
    ])
    error = (observed - truth + np.pi) % (2.0 * np.pi) - np.pi
    assert np.degrees(np.max(np.abs(error))) < 1e-3


def test_qmt_gate_pools_only_predeclared_excited_training_windows(monkeypatch) -> None:
    """An unrelated action remains diagnostic but cannot veto or move the fit."""

    n = 300
    identity = np.repeat(np.eye(3)[None, :, :], n, axis=0)
    quat = np.zeros((n, 4), float)
    quat[:, 0] = 1.0
    phase = np.full(n, "FORMAL_ACTION_OR_HOLD", dtype="U32")
    zero3 = np.zeros((n, 3), float)

    def episode(action: str, partition: str) -> Raw6Episode:
        gyro = {segment: zero3.copy() for segment in SEGMENTS}
        gyro["upper_arm_left"][:, 2] = 0.5
        return Raw6Episode(
            capture="SYNTHETIC_QMT_SEMANTICS",
            action=action,
            partition=partition,
            time_ns=np.arange(n, dtype=np.int64) * 20_000_000,
            phase=phase.copy(),
            acc={segment: zero3.copy() for segment in SEGMENTS},
            gyro=gyro,
            rotation_world_sensor={segment: identity.copy() for segment in SEGMENTS},
            quat_world_sensor_wxyz={segment: quat.copy() for segment in SEGMENTS},
            rest_detected={segment: np.zeros(n, bool) for segment in SEGMENTS},
            bias_rad_s={segment: zero3.copy() for segment in SEGMENTS},
            audit={},
        )

    headings = iter((0.20, 2.50, -0.10))

    def fake_heading_correction(*args, **kwargs):
        del args, kwargs
        heading = next(headings)
        values = np.full(n, heading, float)
        return quat.copy(), values.copy(), values.copy(), np.ones(n), np.zeros(n, int)

    monkeypatch.setattr(
        "biospur_fusion.v0.raw6_heading.qmt.headingCorrection",
        fake_heading_correction,
    )
    report = qmt_edge_heading(
        "elbow_left",
        (
            episode("left_elbow", "IDENTIFICATION_TRAIN"),
            episode("unrelated_squat", "IDENTIFICATION_TRAIN"),
            episode("left_heel", "HELD_OUT_VALIDATION"),
        ),
        np.array([0.0, 0.0, 1.0]),
        np.array([0.0, 0.0, 1.0]),
        intended_actions={"left_elbow", "left_heel"},
        axis_multistart_spread_deg=1.0,
    )

    assert report["qualification"]["pass"] is True
    assert report["qualification"]["signal_qualified_training_actions"] == [
        "left_elbow"
    ]
    assert np.isclose(report["heading_rad"], 0.20)
    assert report["action_estimate_spread_deg"] > 100.0
    assert report["held_out_comparison"]["signal_qualified_actions"] == [
        "left_heel"
    ]
    assert np.isclose(
        report["held_out_comparison"]["train_to_heldout_heading_difference_deg"],
        np.degrees(-0.30),
    )
    records = {row["action"]: row for row in report["records"]}
    assert records["unrelated_squat"]["signal_qualified"] is True
    assert records["unrelated_squat"]["predeclared_edge_factor_relevant"] is False
    assert records["unrelated_squat"]["qualification_training_candidate"] is False


def test_display_trunk_frames_follow_fitted_bilateral_joint_centers() -> None:
    levers = {
        "pelvis_torso": [0.0, 0.0, 0.20, 0.0, 0.0, -0.20],
        "shoulder_left": [-0.20, 0.0, 0.20, 0.0, 0.0, 0.15],
        "elbow_left": [0.0, 0.0, -0.15, 0.0, 0.0, 0.15],
        "shoulder_right": [0.20, 0.0, 0.20, 0.0, 0.0, 0.15],
        "elbow_right": [0.0, 0.0, -0.15, 0.0, 0.0, 0.15],
        "hip_left": [-0.10, 0.0, 0.0, 0.0, 0.0, 0.20],
        "knee_left": [0.0, 0.0, -0.20, 0.0, 0.0, 0.20],
        "hip_right": [0.10, 0.0, 0.0, 0.0, 0.0, 0.20],
        "knee_right": [0.0, 0.0, -0.20, 0.0, 0.0, 0.20],
    }
    factors = {
        edge: SimpleNamespace(hinge_axis_parent=None, hinge_axis_child=None)
        for edge, *_ in EDGES
    }
    frames = sensor_display_frames({"lever_by_edge": levers}, factors)
    np.testing.assert_allclose(frames["pelvis"], np.eye(3), atol=1e-12)
    np.testing.assert_allclose(frames["torso"], np.eye(3), atol=1e-12)
    assert all(np.isclose(np.linalg.det(frame), 1.0) for frame in frames.values())
