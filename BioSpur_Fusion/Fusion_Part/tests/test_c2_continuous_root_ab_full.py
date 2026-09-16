from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.continuous_full_session import (
    ContinuousUwbInstrumentation,
    FULL_SESSION_BYTE_COUNT,
    FULL_SESSION_SCHEMA,
    FULL_SESSION_START_OFFSET,
    FULL_SESSION_STOP_OFFSET,
    evaluate_anti_drift_gates,
    load_continuous_session_inventory,
    maximum_adjacent_position_jump,
    summarize_root_trajectory,
    validate_result_schema,
)


ROOT = Path(__file__).resolve().parents[1]


def test_inventory_is_one_contiguous_19_action_18_gap_session():
    inventory = load_continuous_session_inventory(ROOT)
    assert len(inventory.regions) == 37
    assert sum(row.kind == "ACTION" for row in inventory.regions) == 19
    assert sum(row.kind == "INTER_ACTION_GAP" for row in inventory.regions) == 18
    assert inventory.start_offset == FULL_SESSION_START_OFFSET
    assert inventory.stop_offset == FULL_SESSION_STOP_OFFSET
    assert sum(row.byte_count for row in inventory.regions) == FULL_SESSION_BYTE_COUNT
    assert all(
        left.stop_offset == right.start_offset and left.stop_ns == right.start_ns
        for left, right in zip(inventory.regions, inventory.regions[1:])
    )
    actions = [row for row in inventory.regions if row.kind == "ACTION"]
    gaps = [row for row in inventory.regions if row.kind == "INTER_ACTION_GAP"]
    assert all(row.expected_sha256 is not None for row in actions)
    assert all(row.expected_sha256 is None for row in gaps)


def test_all_twenty_labels_exist_and_01_is_only_marker():
    inventory = load_continuous_session_inventory(ROOT)
    assert [row.index for row in inventory.labels] == list(range(20))
    assert [row.index for row in inventory.labels if row.marker_only] == [1]
    assert inventory.labels[1].action_id == "01_neutral_sway"
    action00 = next(row for row in inventory.regions if row.action_index == 0)
    action02 = next(row for row in inventory.regions if row.action_index == 2)
    assert inventory.labels[1].enter_ns == action00.stop_ns
    assert inventory.labels[2].enter_ns == action02.start_ns


def test_full_runner_has_single_state_constructors_and_no_reset_api():
    path = ROOT / "tools/run_c2_continuous_root_ab_full.py"
    source = path.read_text()
    tree = ast.parse(source)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert calls.count("PelvisContinuousVQF") == 1
    assert calls.count("ContinuousRootAB") == 1
    assert ".reset(" not in source
    assert ".label_boundary(" in source
    assert "merge.finish(CompleteWindowBarrier" in source
    assert "_verify_runtime_manifest(runtime_manifest)" in source
    assert "selected_events == merge.submitted == merge.dispatched" in source
    assert "metrics.a_uwb_commits != 0" in source
    assert "MAX_OUTPUT_BYTES" in source
    assert "MAX_SELECTED_PELVIS_EVENTS" in source
    assert "MAX_TRAJECTORY_SAMPLES" in source
    assert "FORMAL_FREEZE_MANIFEST_SHA256" in source
    assert '"mtime_ns": raw_stat.st_mtime_ns' in source
    assert 'required[f"loaded_module:{name}"]' in source


def test_trajectory_summary_reports_requested_metrics():
    anchors = np.array([
        [0, 0, 0], [4, 0, 0], [4, 3, 0], [0, 3, 0],
        [0, 0, 2], [4, 0, 2], [4, 3, 2], [0, 3, 2],
    ], dtype=float)
    positions = np.array([[1, 1, .5], [2, 1, .75], [5, 1, 1]], dtype=float)
    velocities = np.array([[0, 0, 0], [1, 0, .25], [3, 0, .25]], dtype=float)
    summary = summarize_root_trajectory(positions, velocities, anchors)
    assert summary["samples"] == 3
    assert summary["endpoint_displacement_m"] == pytest.approx(np.sqrt(16.25))
    assert summary["maximum_speed_mps"] == pytest.approx(np.sqrt(9.0625))
    assert summary["z_excursion_m"] == pytest.approx(.5)
    assert summary["outside_anchor_volume_fraction"] == pytest.approx(1 / 3)


def test_result_schema_is_diagnostic_and_fail_closed():
    result = {
        "schema": FULL_SESSION_SCHEMA,
        "status": "DIAGNOSTIC_PASS",
        "scientific_pass": False,
        "source": {},
        "inventory": {},
        "event_conservation": {},
        "admission_reasons": {},
        "branch_a": {},
        "branch_b": {},
        "uwb_transactions": {},
        "anti_drift_gates": {},
        "vqf": {},
        "merge": {},
        "regions": [],
        "protocol_labels": [],
        "runtime": {},
    }
    validate_result_schema(result)
    with pytest.raises(ValueError, match="scientific pass"):
        validate_result_schema({**result, "scientific_pass": True})
    incomplete = dict(result)
    del incomplete["regions"]
    with pytest.raises(ValueError, match="lacks required fields"):
        validate_result_schema(incomplete)


def test_transaction_instrumentation_owns_commit_maximum_and_recovery_lifecycle():
    owner = ContinuousUwbInstrumentation(maximum_committed_correction_m=0.05)
    owner.observe(time_ns=100, sequence=1, sweep=11, committed=False,
                  reason="REJECT_PROPOSED_STATE_UWB_RECOVERY_1_OF_3",
                  solver_accepted=True, correction_norm_m=0.01,
                  recovery_good_events=1, recovery_required_events=3)
    owner.observe(time_ns=200, sequence=2, sweep=12, committed=False,
                  reason="REJECT_PROPOSED_STATE_UWB_RECOVERY_2_OF_3",
                  solver_accepted=True, correction_norm_m=0.02,
                  recovery_good_events=2, recovery_required_events=3)
    owner.observe(time_ns=300, sequence=3, sweep=13, committed=True,
                  reason="ACCEPTED_COMMITTED_POSITION_ONLY",
                  solver_accepted=True, correction_norm_m=0.03,
                  recovery_good_events=3, recovery_required_events=3)
    owner.observe(time_ns=400, sequence=4, sweep=14, committed=True,
                  reason="ACCEPTED_COMMITTED_POSITION_ONLY",
                  solver_accepted=True, correction_norm_m=0.04,
                  recovery_good_events=3, recovery_required_events=3)
    summary = owner.summary()
    assert summary["acceptance_ratio"] == 0.5
    assert summary["maximum_committed_correction"] == {
        "norm_m": 0.04,
        "event": {"time_ns": 400, "sequence": 4, "sweep": 14},
    }
    assert summary["recovery"]["started"] == 1
    assert summary["recovery"]["completed"] == 1
    assert summary["recovery"]["open_at_end"] is None


def test_recovery_stays_open_across_solver_reject_and_fails_at_end():
    owner = ContinuousUwbInstrumentation(maximum_committed_correction_m=0.05)
    owner.observe(time_ns=100, sequence=1, sweep=11, committed=False,
                  reason="REJECT_PROPOSED_STATE_UWB_RECOVERY_1_OF_5",
                  solver_accepted=True, correction_norm_m=0.01,
                  recovery_good_events=1, recovery_required_events=5)
    owner.observe(time_ns=200, sequence=2, sweep=12, committed=False,
                  reason="REJECT_SOLVER_SOLVER_OR_GEOMETRY_REJECT",
                  solver_accepted=False, correction_norm_m=0.0,
                  recovery_good_events=0, recovery_required_events=5)
    summary = owner.summary()
    open_episode = summary["recovery"]["open_at_end"]
    assert open_episode is not None
    assert open_episode["reset_events"] == 1
    assert open_episode["last_good_count"] == 0
    gates = evaluate_anti_drift_gates(
        branch_a={"endpoint_displacement_m": 200.0},
        branch_b={"endpoint_displacement_m": 0.5,
                  "rms_displacement_m": 0.6,
                  "maximum_displacement_m": 4.0},
        transactions=summary,
        maximum_adjacent_jump={"norm_m": 0.08},
    )
    assert not gates["gates"]["recovery_lifecycle_closed"]["passed"]
    with pytest.raises(RuntimeError, match="exceeded"):
        owner.observe(time_ns=300, sequence=3, sweep=13, committed=True,
                      reason="ACCEPTED_COMMITTED_POSITION_ONLY",
                      solver_accepted=True, correction_norm_m=0.051,
                      recovery_good_events=5, recovery_required_events=5)


def test_recovery_reject_reset_then_five_admissible_and_commit_closes_episode():
    owner = ContinuousUwbInstrumentation(maximum_committed_correction_m=0.05)
    owner.observe(time_ns=100, sequence=1, sweep=11, committed=False,
                  reason="REJECT_PROPOSED_STATE_UWB_RECOVERY_1_OF_5",
                  solver_accepted=True, correction_norm_m=0.01,
                  recovery_good_events=1, recovery_required_events=5)
    owner.observe(time_ns=200, sequence=2, sweep=12, committed=False,
                  reason="REJECT_PROPOSED_STATE_POSITION_INFLUENCE_EXCEEDED",
                  solver_accepted=True, correction_norm_m=1.0,
                  recovery_good_events=0, recovery_required_events=5)
    for count in range(1, 5):
        owner.observe(time_ns=200 + count * 100, sequence=2 + count,
                      sweep=12 + count, committed=False,
                      reason=f"REJECT_PROPOSED_STATE_UWB_RECOVERY_{count}_OF_5",
                      solver_accepted=True, correction_norm_m=0.01,
                      recovery_good_events=count, recovery_required_events=5)
    owner.observe(time_ns=700, sequence=7, sweep=17, committed=True,
                  reason="ACCEPTED_COMMITTED_POSITION_ONLY",
                  solver_accepted=True, correction_norm_m=0.01,
                  recovery_good_events=5, recovery_required_events=5)
    summary = owner.summary()
    assert summary["recovery"]["started"] == 1
    assert summary["recovery"]["completed"] == 1
    assert summary["recovery"]["open_at_end"] is None
    episode = summary["recovery"]["closed_episodes"][0]
    assert episode["reset_events"] == 1
    assert episode["last_reset_reason"] == "REJECT_PROPOSED_STATE_POSITION_INFLUENCE_EXCEEDED"
    assert episode["last_good_count"] == 5


def test_adjacent_jump_identity_and_fixed_anti_drift_gates():
    positions = np.array([[0, 0, 0], [.01, 0, 0], [.09, 0, 0]], dtype=float)
    jump = maximum_adjacent_position_jump(
        positions, np.array([100, 200, 300], dtype=np.int64),
    )
    assert jump == {
        "norm_m": 0.08, "before_index": 1, "after_index": 2,
        "before_time_ns": 200, "after_time_ns": 300,
    }
    transactions = ContinuousUwbInstrumentation(
        maximum_committed_correction_m=0.05,
    ).summary()
    gates = evaluate_anti_drift_gates(
        branch_a={"endpoint_displacement_m": 200.0},
        branch_b={"endpoint_displacement_m": 0.5,
                  "rms_displacement_m": 0.6,
                  "maximum_displacement_m": 4.0},
        transactions=transactions,
        maximum_adjacent_jump=jump,
    )
    assert gates["passed"]
    failed = evaluate_anti_drift_gates(
        branch_a={"endpoint_displacement_m": 20.0},
        branch_b={"endpoint_displacement_m": 1.0,
                  "rms_displacement_m": 1.0,
                  "maximum_displacement_m": 5.0},
        transactions=transactions,
        maximum_adjacent_jump={**jump, "norm_m": 0.10},
    )
    assert not failed["passed"]
