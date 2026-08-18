from __future__ import annotations

import numpy as np
import pytest

from biospur_fusion.imu_pose_v2.qualification import directional_gate, static_wobble_gate
from biospur_fusion.imu_pose_v2.split import (
    WindowSample, assert_h_payload_sealed, assert_validation_sealed_before_candidate,
    assign_frozen_split, deduplicate_uid_bytes,
)


def _rows(action, count, *, phase="FORMAL_ACTION", cycle=True):
    return [WindowSample(f"{action}:{i}", action, phase, f"c{i//5}" if cycle else None, i) for i in range(count)]


def test_static_55_guard10_validation35_is_disjoint_exact_cover():
    rows = _rows("00_initial_still", 100, cycle=False)
    split = assign_frozen_split(rows, master_seed="fixed")
    assert list(split.values()).count("CALIBRATION_FIT") == 55
    assert list(split.values()).count("PROPAGATION_ONLY") == 10
    assert list(split.values()).count("CALIBRATION_VALIDATION") == 35
    assert len(split) == len(rows)


def test_preparation_never_becomes_still_fit_and_final_is_validation_only():
    rows = _rows("17_final_still", 30) + _rows("16_squat", 10, phase="PREPARATION")
    split = assign_frozen_split(rows, master_seed="fixed")
    assert all(split[row.uid] == "CALIBRATION_VALIDATION" for row in rows[:30])
    assert all(split[row.uid] == "PROPAGATION_ONLY" for row in rows[30:])


def test_dynamic_cycle_assignment_is_stable_and_whole_cycle():
    rows = _rows("16_squat", 40)
    first = assign_frozen_split(rows, master_seed="fixed")
    second = assign_frozen_split(list(reversed(rows)), master_seed="fixed")
    assert first == second
    for cycle in {row.cycle_id for row in rows}:
        assert len({first[row.uid] for row in rows if row.cycle_id == cycle}) == 1


def test_duplicate_uid_conflict_and_validation_or_h_early_open_are_rejected():
    assert deduplicate_uid_bytes([("u", b"same"), ("u", b"same")]) == (("u", b"same"),)
    with pytest.raises(ValueError): deduplicate_uid_bytes([("u", b"a"), ("u", b"b")])
    split = {"v": "CALIBRATION_VALIDATION", "f": "CALIBRATION_FIT"}
    with pytest.raises(RuntimeError): assert_validation_sealed_before_candidate(split, ["v"])
    assert_validation_sealed_before_candidate(split, ["f"])
    with pytest.raises(RuntimeError): assert_h_payload_sealed(False, True)
    with pytest.raises(RuntimeError): assert_h_payload_sealed(True, False)
    assert_h_payload_sealed(True, True)


def test_natural_down_and_tpose_semantic_fixture_reject_axis_antipode():
    down = np.tile([0., 0., -1.], (100, 1))
    horizontal = np.tile([1., 0., 0.], (100, 1))
    assert directional_gate(down, np.array([0., 0., -1.]), 15., 25.)["pass"]
    assert directional_gate(horizontal, np.array([1., 0., 0.]), 15., 25.)["pass"]
    assert not directional_gate(-down, np.array([0., 0., -1.]), 15., 25.)["pass"]


def test_static_wobble_cannot_hide_solver_motion_injection():
    raw = np.full(200, .01); b0 = np.full(200, .01); injected = np.full(200, .02)
    report = static_wobble_gate(raw, b0, injected, .015, rest_established=True)
    assert report["classification"] == "COUPLED_SOLVER_STATIC_MOTION_INJECTION"
    assert report["pass"] is False
    assert static_wobble_gate(raw, b0, injected, .015, rest_established=False)["classification"] == "REST_NOT_ESTABLISHED"
