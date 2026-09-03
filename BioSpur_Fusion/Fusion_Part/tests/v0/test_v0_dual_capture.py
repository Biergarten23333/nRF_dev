from __future__ import annotations

import json
import inspect
import hashlib
import importlib.metadata
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from biospur_fusion.time.common_clock import (
    _integer_join_resolved, _required_clean_listener_pairs,
)
from biospur_fusion.v0.dual_capture import (
    PROFILE_SCHEMA, assert_profile_capture_match, build_protocol_ledger,
    historical_invalidation, load_protocol, _action_physical_qa, _locked_paths,
    _fit_sensor_from_segment_vector_pairs, calibrate_capture, create_code_lock,
    load_capture1_calibration_episode, run_action_replay, verify_code_lock,
)
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.v0.contracts import NODES, load_config
from biospur_fusion.v0.episode import PHASES, segment_five_phase_episode
from biospur_fusion.v0.model import (
    ResampledWindow, display_static_calibration, hard_reference_pose_fk_gate,
    initialize_common_action_display_yaw,
)
from biospur_fusion.root_r6a0.math3d import so3_exp
from tools.run_biospur_fusion_v0_dual_capture import (
    _derive_reviewed_replay_evidence, _record_and_validate_supervisor_qa,
    _persist_live_qa_after_second_rehash, finalize_after_supervisor_qa, run_all,
)
from tools.run_biospur_fusion_v0_reference_qa import (
    EXPECTED_REFERENCE_ACTIONS, REFERENCE_ROLE, run_reference_gate,
)


ROOT = Path(__file__).resolve().parents[2]


def _profile(capture_id: str) -> dict:
    return {"profile_schema": PROFILE_SCHEMA, "capture_id": capture_id}


def test_profile_capture_guard_allows_capture1_with_capture1() -> None:
    protocol = load_protocol(ROOT)
    capture1 = protocol["captures"]["CAPTURE1"]["capture_id"]
    assert_profile_capture_match(_profile(capture1), capture1)


def test_profile_capture_guard_allows_capture2_with_capture2() -> None:
    protocol = load_protocol(ROOT)
    capture2 = protocol["captures"]["CAPTURE2"]["capture_id"]
    assert_profile_capture_match(_profile(capture2), capture2)


def test_profile_capture_guard_rejects_capture1_with_capture2_before_reconstruction() -> None:
    protocol = load_protocol(ROOT)
    capture1 = protocol["captures"]["CAPTURE1"]["capture_id"]
    capture2 = protocol["captures"]["CAPTURE2"]["capture_id"]
    with pytest.raises(ValueError, match="PROFILE_CAPTURE_ID_MISMATCH_BEFORE_RECONSTRUCTION"):
        assert_profile_capture_match(_profile(capture1), capture2)


def test_profile_capture_guard_rejects_capture2_with_capture1_before_reconstruction() -> None:
    protocol = load_protocol(ROOT)
    capture1 = protocol["captures"]["CAPTURE1"]["capture_id"]
    capture2 = protocol["captures"]["CAPTURE2"]["capture_id"]
    with pytest.raises(ValueError, match="PROFILE_CAPTURE_ID_MISMATCH_BEFORE_RECONSTRUCTION"):
        assert_profile_capture_match(_profile(capture2), capture1)


@pytest.mark.parametrize(
    ("profile_capture_name", "input_capture_name"),
    [("CAPTURE1", "CAPTURE2"), ("CAPTURE2", "CAPTURE1")],
)
def test_cross_capture_rejection_never_loads_action_or_creates_output_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    profile_capture_name: str, input_capture_name: str,
) -> None:
    protocol = load_protocol(ROOT)
    profile_capture_id = protocol["captures"][profile_capture_name]["capture_id"]
    input_spec = protocol["captures"][input_capture_name]
    calls = {"load_action": 0}

    def forbidden_load(*_args: object, **_kwargs: object) -> None:
        calls["load_action"] += 1
        raise AssertionError("load_action must not be reached")

    monkeypatch.setattr("biospur_fusion.v0.dual_capture.load_action", forbidden_load)
    destination = tmp_path / f"{profile_capture_name}_into_{input_capture_name}"
    with pytest.raises(ValueError, match="PROFILE_CAPTURE_ID_MISMATCH_BEFORE_RECONSTRUCTION"):
        run_action_replay(
            ROOT, input_capture_name, input_spec, {"action": "must_not_load"},
            _profile(profile_capture_id), "0" * 64, "MAIN_SUITE_1", destination,
            lock_sha256="1" * 64,
        )
    assert calls["load_action"] == 0
    assert not destination.exists()


def test_hxx_is_complete_and_never_a_calibration_input() -> None:
    protocol = load_protocol(ROOT)
    expected = {"CAPTURE1": {"walk", "boxing", "golf_swing"},
                "CAPTURE2": {"H00_walk", "H01_boxing", "H02_golf"}}
    for capture_name, spec in protocol["captures"].items():
        hxx = {row["action"] for row in spec["hxx"]}
        assert hxx == expected[capture_name]
        assert hxx.isdisjoint(spec["calibration_inputs"])


@pytest.mark.parametrize("capture_name", ["CAPTURE1", "CAPTURE2"])
def test_hostile_calibration_loader_opens_exact_declared_inputs_and_never_hxx(
    capture_name: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = load_protocol(ROOT); spec = protocol["captures"][capture_name]
    hxx = {row["action"] for row in spec["hxx"]}
    action_names = list(spec["calibration_inputs"]) + sorted(hxx)
    capture_ledger = {"actions": [{"action": action} for action in action_names]}
    opened: list[str] = []

    def instrumented_load(
        _root: Path, observed_capture: str, _spec: dict, action: dict,
    ) -> tuple[dict, dict, dict]:
        assert observed_capture == capture_name
        opened.append(action["action"])
        if action["action"] in hxx:
            raise AssertionError("Hxx/reserved payload opened during calibration")
        return (
            {}, {"instrumented_selected_action": action["action"]},
            {"EPISODE_COMPLETENESS": "PASS", "action": action["action"]},
        )

    def deterministic_profile(*_args: object, **_kwargs: object) -> dict:
        return {
                "profile_schema": PROFILE_SCHEMA,
                "profile_id": f"PROFILE_{capture_name}",
                "capture_id": spec["capture_id"],
                "capture_attitude_frame": {"capture_id": spec["capture_id"]},
                "reference_pose_hard_fk_gates": {},
            }

    monkeypatch.setattr(
        "biospur_fusion.v0.dual_capture.load_calibration_episode", instrumented_load,
    )
    monkeypatch.setattr(
        "biospur_fusion.v0.dual_capture.load_action",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("ACTION_START-to-ACTION_STOP loader must not calibrate")
        ),
    )
    monkeypatch.setattr("biospur_fusion.v0.dual_capture._profile_from_rows", deterministic_profile)
    _, result = calibrate_capture(
        ROOT, capture_name, spec, capture_ledger, code_lock_sha256="0" * 64,
    )
    assert opened == list(spec["calibration_inputs"])
    assert hxx.isdisjoint(opened)
    assert result["hxx_used_for_calibration"] is False
    assert result["calibration_execution_count"] == 1
    assert result["per_action_calibration_executed"] is False
    assert result["cross_action_state_propagation_used"] is False


def _synthetic_episode_rows(*, onset: bool = True, recovery: bool = True,
                            always_moving: bool = False) -> dict[str, np.ndarray]:
    dtype = np.dtype([
        ("global_time_ns", "<i8"), ("status", "u1"),
        ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)),
    ])
    times = np.arange(0, 12_000_000_000, 10_000_000, dtype=np.int64)
    rows = {}
    for node_index, node in enumerate(NODES):
        values = np.zeros(len(times), dtype=dtype)
        values["global_time_ns"] = times
        values["status"] = 1
        values["acc_raw"][:, 2] = 2048
        active = np.full(len(times), always_moving)
        if onset:
            active |= (times >= 3_800_000_000) & (times < 4_500_000_000)
        if recovery:
            active |= (times >= 7_500_000_000) & (times < 8_200_000_000)
        values["gyro_raw"][active, node_index % 3] = 600
        rows[node] = values
    return rows


def _segment_synthetic(rows: dict[str, np.ndarray], action_kind: str) -> dict:
    return segment_five_phase_episode(
        rows, action="synthetic", action_kind=action_kind,
        formal_start_global_ns=4_000_000_000,
        formal_stop_global_ns_exclusive=8_000_000_000,
        contract=load_protocol(ROOT)["semantic_qa_contract"]["calibration_episode"],
        boundary_authority={"source": "SYNTHETIC_EXACT_COMPLETE_EPISODE"},
    )


def test_general_episode_segmenter_emits_ordered_five_phase_bidirectional_episode() -> None:
    diagnostic = _segment_synthetic(_synthetic_episode_rows(), "MOVEMENT_OR_POSE")
    assert diagnostic["EPISODE_COMPLETENESS"] == "PASS"
    assert tuple(row["phase"] for row in diagnostic["phases"]) == PHASES
    assert diagnostic["phases"][1]["duration_s"] > 0.0
    assert diagnostic["phases"][3]["duration_s"] > 0.0
    assert diagnostic["whole_buffer_labeled_rest"] is False
    assert diagnostic["manifest_labels_treated_as_rest_evidence"] is False
    assert diagnostic["same_method_for_every_capture_action_and_node"] is True


def test_general_episode_segmenter_rejects_missing_recovery_transition() -> None:
    diagnostic = _segment_synthetic(
        _synthetic_episode_rows(recovery=False), "MOVEMENT_OR_POSE",
    )
    assert diagnostic["EPISODE_COMPLETENESS"] == "FAIL"
    assert diagnostic["failure_result"] == "CALIBRATION_EPISODE_INCOMPLETE"
    assert "NO_SIGNAL_SUPPORTED_BIDIRECTIONAL_TRANSITION_REST_PAIR" in diagnostic["failures"]


def test_general_episode_segmenter_never_calls_a_whole_moving_buffer_rest() -> None:
    diagnostic = _segment_synthetic(
        _synthetic_episode_rows(always_moving=True), "MOVEMENT_OR_POSE",
    )
    assert diagnostic["EPISODE_COMPLETENESS"] == "FAIL"
    assert "NO_SIGNAL_VERIFIED_PREFIX_REST" in diagnostic["failures"]
    assert "NO_SIGNAL_VERIFIED_SUFFIX_REST" in diagnostic["failures"]


def test_stationary_hold_uses_signal_verified_pre_and_post_rest_without_pose_template() -> None:
    diagnostic = _segment_synthetic(
        _synthetic_episode_rows(onset=False, recovery=False), "STATIONARY_REFERENCE",
    )
    assert diagnostic["EPISODE_COMPLETENESS"] == "PASS"
    assert diagnostic["activity_partition"] == (
        "STATIONARY_FORMAL_HOLD_NO_POSE_TEMPLATE_CONDITIONING"
    )
    assert diagnostic["whole_buffer_labeled_rest"] is False


def test_neutral_plus_tpose_vector_fit_closes_tpose_only_twist_defect() -> None:
    # The two physical reference poses expose the axial twist that a single
    # gravity/T-pose observation cannot determine.
    segment = np.asarray([[0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]])
    truth = so3_exp(np.array([0.35, -0.20, 0.60]))
    sensor = np.einsum("ij,nj->ni", truth, segment)
    fitted = _fit_sensor_from_segment_vector_pairs(sensor, segment)
    assert np.allclose(fitted, truth, atol=1e-12)
    assert np.allclose(np.einsum("ji,nj->ni", fitted, sensor), segment, atol=1e-12)


def test_capture_local_forearm_identity_is_not_universally_reused() -> None:
    protocol = load_protocol(ROOT)
    c1 = protocol["captures"]["CAPTURE1"]["identity"]
    c2 = protocol["captures"]["CAPTURE2"]["identity"]
    assert c1["BSFB165"] == "forearm_left"
    assert c1["BSFEC35"] == "forearm_right"
    assert c2["BSFB165"] == "forearm_right"
    assert c2["BSFEC35"] == "forearm_left"


def test_protocol_suite_partition_is_exhaustive_and_nonoverlapping() -> None:
    protocol = load_protocol(ROOT)
    for spec in protocol["captures"].values():
        suite1 = set(spec["main_suite_1"]); suite2 = set(spec["main_suite_2"])
        hxx = {row["action"] for row in spec["hxx"]}
        assert suite1.isdisjoint(suite2)
        assert suite1.isdisjoint(hxx)
        assert suite2.isdisjoint(hxx)
        assert set(spec["reference_actions"]).issubset(spec["calibration_inputs"])


def test_capture1_native_hxx_crosswalk_and_final_still_separation() -> None:
    spec = load_protocol(ROOT)["captures"]["CAPTURE1"]
    assert {row["action"]: row["hxx_id"] for row in spec["hxx"]} == {
        "walk": "H00_walk", "boxing": "H01_boxing", "golf_swing": "H02_golf",
    }
    assert "final_still" not in {row["action"] for row in spec["hxx"]}
    assert "final_still" in spec["main_suite_2"]


def test_capture2_inventory_is_exactly_19_plus_3_with_retry_history_excluded() -> None:
    protocol = load_protocol(ROOT); spec = protocol["captures"]["CAPTURE2"]
    ledger = build_protocol_ledger(ROOT)["captures"]["CAPTURE2"]
    assert ledger["accepted_non_hxx_action_count"] == 19
    assert ledger["accepted_hxx_action_count"] == 3
    assert ledger["accepted_total_action_count"] == 22
    assert spec["inventory_contract"]["deleted_or_unexecuted"] == ["01_neutral_sway"]
    assert spec["inventory_contract"]["retry_or_skip_history_excluded"] == ["rep_02", "rep_03"]
    assert all(row["promoted_repetition_id"] == "rep_01" for row in ledger["actions"])
    assert all("attempt_number" not in row for row in ledger["actions"])


def test_complete_episode_ledger_preserves_real_capture1_transitions_and_attempt_history() -> None:
    rows = {
        row["action"]: row
        for row in build_protocol_ledger(ROOT)["captures"]["CAPTURE1"]["actions"]
    }
    assert rows["t_pose"]["episode_bounds"]["start_global_time_ns"] == (
        rows["initial_still"]["formal_action_bounds"]["stop_global_time_ns_exclusive"]
    )
    assert rows["t_pose"]["episode_bounds"]["pre_boundary_event"] == (
        "PREVIOUS_FINAL_ACCEPTED_NON_HXX_FORMAL_STOP"
    )
    assert rows["arms"]["formal_action_bounds"]["stop_boundary_authority"] == (
        "AUTHORITATIVE_SELECTED_ATTEMPT_ACTION_STOP"
    )
    assert rows["arms"]["formal_action_bounds"][
        "invalidation_reclassification_audit"
    ]["effect_on_formal_action_bounds"] == "NONE;ORIGINAL_ACTION_STOP_RESTORED"
    assert rows["right_elbow"]["attempt_number"] == 2
    assert rows["right_elbow"]["episode_bounds"]["pre_boundary_event"] == (
        "TOKEN_RECEIVED_SELECTED_ATTEMPT"
    )
    for action in ("trunk", "final_still"):
        episode = rows[action]["episode_bounds"]
        assert not any(
            max(episode["start_global_time_ns"], left)
            < min(episode["stop_global_time_ns_exclusive"], right)
            for left, right in rows[action]["forbidden_hxx_global_time_intervals_ns"]
        )


def test_capture2_complete_episode_is_exact_manifest_bidirectional_bracket() -> None:
    spec = load_protocol(ROOT)["captures"]["CAPTURE2"]
    rows = {
        row["action"]: row
        for row in build_protocol_ledger(ROOT)["captures"]["CAPTURE2"]["actions"]
    }
    for action in spec["calibration_inputs"]:
        row = rows[action]; episode = row["episode_bounds"]
        formal = row["formal_action_bounds"]
        assert episode["start_host_monotonic_ns"] < formal["start_host_monotonic_ns"]
        assert formal["start_host_monotonic_ns"] < formal["stop_host_monotonic_ns_exclusive"]
        assert formal["stop_host_monotonic_ns_exclusive"] < episode[
            "stop_host_monotonic_ns_exclusive"
        ]
        assert episode["pre_boundary_event"] == "REPETITION_START_BOUNDARY"
        assert episode["post_boundary_event"] == "REPETITION_END_BOUNDARY"
        assert row["read_bracket"]["start_byte_inclusive"] == episode[
            "start_byte_inclusive"
        ]
        assert row["read_bracket"]["stop_byte_exclusive"] == episode[
            "stop_byte_exclusive"
        ]
        assert row["promoted_repetition_id"] == "rep_01"


def test_capture1_complete_episode_access_persists_exact_per_node_hash_table() -> None:
    protocol = load_protocol(ROOT); spec = protocol["captures"]["CAPTURE1"]
    row = next(
        item for item in build_protocol_ledger(ROOT)["captures"]["CAPTURE1"]["actions"]
        if item["action"] == "initial_still"
    )
    rows, access = load_capture1_calibration_episode(ROOT, spec, row)
    assert set(rows) == set(NODES)
    assert set(access["nodes"]) == set(NODES)
    assert all(len(access["nodes"][node]["payload_sha256"]) == 64 for node in NODES)
    assert access["hxx_payload_opened"] is False


def test_main_suite_names_are_explicitly_logical_not_recorded() -> None:
    protocol = load_protocol(ROOT)
    assert "logical replay partitions" in protocol["suite_partition_policy"]
    for spec in protocol["captures"].values():
        assert spec["inventory_contract"]["main_suite_label_provenance"] == (
            "GOAL_LOCAL_LOGICAL_PARTITION_NOT_RECORDED_ACQUISITION_SUITE"
        )


def test_c91_is_hybrid_and_withdrawals_cover_both_capture_scopes() -> None:
    ledger = build_protocol_ledger(ROOT)
    c91 = ledger["historical_c91_profile_provenance"]
    assert c91["classification"] == "HYBRID_NOT_CAPTURE_BOUND"
    assert c91["valid_complete_profile_for_capture1"] is False
    assert c91["valid_complete_profile_for_capture2"] is False
    invalidation = historical_invalidation(ROOT)
    scopes = set(invalidation["withdrawn_claim_scope"])
    assert "CAPTURE1_FOREARM_DEPENDENT_PHYSICAL_INTERPRETATION_AND_WHOLE_BODY_READINESS" in scopes
    assert "ALL_CAPTURE2_PHYSICAL_INTERPRETATION" in scopes
    assert any(row["input_capture_id"] == ledger["captures"]["CAPTURE1"]["capture_id"]
               for row in invalidation["entries"])
    assert all(row["profile_capture_id"] == "HYBRID_NOT_CAPTURE_BOUND"
               for row in invalidation["entries"])


def test_semantic_contract_is_non_vacuous_body_relative_and_supervisor_gated() -> None:
    contract = load_protocol(ROOT)["semantic_qa_contract"]
    assert contract["minimum_joint_excursion_deg"] >= 10.0
    assert contract["minimum_endpoint_excursion_forearm_lengths"] > 0.0
    assert 0.0 < contract["sagittal_lateral_variance_max_fraction"] < 1.0
    assert contract["global_or_viewer_y_axis_may_define_physical_front"] is False
    assert contract["physical_pass_requires_supervisor_live_viewer_judgment"] is True


def _reference_fk_arrays(pose_kind: str, *, hostile_bent_neutral: bool = False) -> dict:
    segments = (
        "pelvis", "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
        "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
    )
    frames = 12; index = {name: i for i, name in enumerate(segments)}
    rotation = np.repeat(np.eye(3)[None, None], frames * len(segments), axis=0).reshape(
        frames, len(segments), 3, 3,
    )
    if pose_kind == "STRAIGHT_HORIZONTAL_TPOSE":
        for segment in ("upper_arm_left", "forearm_left"):
            rotation[:, index[segment]] = so3_exp(np.array([0.0, np.pi / 2.0, 0.0]))
        for segment in ("upper_arm_right", "forearm_right"):
            rotation[:, index[segment]] = so3_exp(np.array([0.0, -np.pi / 2.0, 0.0]))
    if hostile_bent_neutral:
        rotation[:, index["upper_arm_left"]] = so3_exp(np.array([0.0, np.pi / 2.0, 0.0]))
        rotation[:, index["forearm_left"]] = so3_exp(np.array([np.pi / 2.0, 0.0, 0.0]))
        rotation[:, index["upper_arm_right"]] = so3_exp(np.array([0.0, -np.pi / 2.0, 0.0]))
        rotation[:, index["forearm_right"]] = so3_exp(np.array([-np.pi / 2.0, 0.0, 0.0]))
    position = np.zeros((frames, len(segments), 3), float)
    position[:, index["torso"]] = [0.0, 0.0, 0.18]
    shoulders = {"left": np.array([-0.23, 0.0, 0.40]), "right": np.array([0.23, 0.0, 0.40])}
    hips = {"left": np.array([-0.10, 0.0, -0.06]), "right": np.array([0.10, 0.0, -0.06])}
    for side in ("left", "right"):
        upper = rotation[0, index[f"upper_arm_{side}"]]
        thigh = rotation[0, index[f"thigh_{side}"]]
        position[:, index[f"upper_arm_{side}"]] = shoulders[side]
        position[:, index[f"forearm_{side}"]] = shoulders[side] + upper @ np.array([0.0, 0.0, -0.30])
        position[:, index[f"thigh_{side}"]] = hips[side]
        position[:, index[f"shank_{side}"]] = hips[side] + thigh @ np.array([0.0, 0.0, -0.43])
    return {
        "segment_names": np.asarray(segments), "segment_position": position,
        "segment_rotation": rotation,
        # A hostile estimator may still report tiny q, perfect closure, and
        # high confidence; none is consumed by the hard posture gate.
        "joint_rotvec": np.zeros((frames, 9, 3)),
        "segment_confidence": np.ones((frames, len(segments))),
        "canonical_fk_closure_max_abs": np.asarray(0.0),
    }


@pytest.mark.parametrize("pose_kind", ["NEUTRAL_STANDING", "STRAIGHT_HORIZONTAL_TPOSE"])
def test_hard_reference_fk_gate_accepts_only_meaningful_geometry(pose_kind: str) -> None:
    contract = load_protocol(ROOT)["semantic_qa_contract"]["hard_reference_pose_fk"]
    gate = hard_reference_pose_fk_gate(_reference_fk_arrays(pose_kind), pose_kind, contract)
    assert gate["HARD_FK_STATE_GATE"] == "PASS"
    assert gate["canonical_fk_segment_position_consumed"] is True
    assert gate["joint_coordinate_magnitude_used_as_posture_evidence"] is False


def test_tiny_q_closure_confidence_and_self_report_cannot_pass_bent_neutral_fk_state() -> None:
    contract = load_protocol(ROOT)["semantic_qa_contract"]["hard_reference_pose_fk"]
    arrays = _reference_fk_arrays("NEUTRAL_STANDING", hostile_bent_neutral=True)
    gate = hard_reference_pose_fk_gate(arrays, "NEUTRAL_STANDING", contract)
    assert np.max(np.abs(arrays["joint_rotvec"])) == 0.0
    assert float(arrays["canonical_fk_closure_max_abs"]) == 0.0
    assert np.min(arrays["segment_confidence"]) == 1.0
    assert gate["HARD_FK_STATE_GATE"] == "FAIL"
    assert gate["checks"]["arms_down"] is False
    assert gate["fk_closure_confidence_or_self_report_may_override_failure"] is False


def test_action_initialization_applies_one_common_yaw_and_cannot_repair_relative_resets() -> None:
    protocol = load_protocol(ROOT); spec = protocol["captures"]["CAPTURE1"]
    segments = tuple(spec["identity"].values()); count = 20
    rotations = {}
    for i, segment in enumerate(segments):
        reset = so3_exp(np.array([0.0, 0.0, 0.17 * (i + 1)]))
        rotations[segment] = np.repeat(reset[None], count, axis=0)
    nodes = tuple(spec["identity"])
    source = ResampledWindow(
        np.arange(count, dtype=np.int64) * 100_000_000,
        rotations, {segment: np.zeros((count, 3)) for segment in segments},
        {node: np.zeros((count, 3)) for node in nodes},
        {node: np.zeros(count) for node in nodes},
        {node: np.ones(count, bool) for node in nodes},
        {segment: np.zeros(count, bool) for segment in segments},
        np.full(count, "CONTINUOUS", dtype="U24"),
    )
    frame = {
        "capture_id": spec["capture_id"],
        "common_yaw_root_segment": "pelvis",
        "root_display_reference_rotvec": [0.0, 0.0, 0.0],
        "dynamic_initialization_frame_count": 10,
    }
    initialized, audit = initialize_common_action_display_yaw(source, frame)
    assert audit["selected_action_payload_only"] is True
    assert audit["inter_action_payload_read"] is False
    assert audit["per_segment_yaw_parameters_fitted"] == 0
    assert audit["per_segment_extrinsics_refitted"] is False
    assert audit["joint_rest_refitted"] is False
    assert audit["action_specific_pose_template_used"] is False
    assert audit["state_propagated_from_other_action"] is False
    assert audit["remaining_unobservable_yaw_dimensions"] == 1
    for left, right in zip(segments[:-1], segments[1:]):
        before = np.einsum("nji,njk->nik", rotations[left], rotations[right])
        after = np.einsum(
            "nji,njk->nik", initialized.segment_rotation[left],
            initialized.segment_rotation[right],
        )
        assert np.allclose(after, before, atol=1e-12)
    assert np.allclose(initialized.segment_rotation["pelvis"], np.eye(3), atol=1e-12)


def test_forbidden_per_segment_action_gauge_refit_is_absent_from_runtime() -> None:
    from biospur_fusion.v0 import model, validation

    source = inspect.getsource(model) + inspect.getsource(validation)
    assert "register_action_capture_attitude_frame" not in source
    assert "selected_pose_template" not in source
    assert "pose_templates" not in source


def test_numerical_pca_and_large_joint_motion_cannot_auto_promote_physical_pass() -> None:
    segments = (
        "pelvis", "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
        "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
    )
    joints = (
        "pelvis_torso", "shoulder_left", "elbow_left", "shoulder_right", "elbow_right",
        "hip_left", "knee_left", "hip_right", "knee_right",
    )
    frames = 8
    joint_rotvec = np.zeros((frames, len(joints), 3), float)
    joint_rotvec[:, joints.index("elbow_left"), 0] = np.linspace(0.0, 0.8, frames)
    arrays = {
        "segment_names": np.asarray(segments), "joint_names": np.asarray(joints),
        "segment_position": np.zeros((frames, len(segments), 3), float),
        "segment_rotation": np.repeat(np.eye(3)[None, None], frames * len(segments), axis=0).reshape(frames, len(segments), 3, 3),
        "joint_rotvec": joint_rotvec,
    }
    ranked = [{"segment": segment} for segment in segments]
    metrics = {
        "gross_motion": {
            "segments_ranked_by_q95_excursion_deg": ranked,
            "segments": {segment: {"excursion_from_action_start_deg": {"q95": 100.0}}
                         for segment in segments},
        },
        "numerical_integrity": {"selected_v0": {
            "finite": True, "canonical_fk_closure_max_abs": 0.0,
            "continuous_steps_over_90_deg": 0,
        }},
    }
    profile = {
        "identity": {f"node-{i}": segment for i, segment in enumerate(segments)},
        "functional_axes": {"elbow_left": {
            "axis_parent_segment_session_reference": [1.0, 0.0, 0.0],
        }},
        "semantic_qa_calibration": {
            "meaningful_joint_excursion_threshold_deg": {joint: 10.0 for joint in joints},
        },
    }
    qa = _action_physical_qa("06_elbow_left", arrays, metrics, profile)
    assert qa["MEANINGFUL_MOTION"].startswith("PASS")
    assert qa["physical_qa_result"] == "INCONCLUSIVE"
    assert qa["HUMAN_LIKE_POSE"] == "PENDING_SUPERVISOR_LIVE_VIEWER_JUDGMENT"
    assert qa["finite_pca_numerical_fk_side_rank_or_objective_may_promote_physical_pass"] is False


def test_locked_run_stops_before_authoritative_finalization() -> None:
    source = inspect.getsource(run_all)
    assert "AWAITING_SUPERVISOR_VIEWER_QA" in source
    assert "_write_live_viewer_qa_template" in source
    assert "_finalize(" not in source


def test_reference_gate_executes_exactly_four_replays_and_halts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.run_biospur_fusion_v0_reference_qa as runner

    protocol = {
        "captures": {
            capture: {
                "capture_id": f"id-{capture.lower()}",
                "reference_actions": list(actions),
                "calibration_inputs": ["joint-calibration-a", "joint-calibration-b"],
            }
            for capture, actions in EXPECTED_REFERENCE_ACTIONS.items()
        }
    }
    ledger = {"captures": {
        capture: {"actions": [{"action": action} for action in actions]}
        for capture, actions in EXPECTED_REFERENCE_ACTIONS.items()
    }}
    replay_calls: list[tuple[str, str, str]] = []
    calibration_calls: list[str] = []

    monkeypatch.setattr(runner, "load_protocol", lambda _root: protocol)
    monkeypatch.setattr(runner, "write_milestone_a", lambda _root, _goal: (ledger, {}))
    monkeypatch.setattr(runner, "_focused_tests", lambda _root: {
        "pass": True, "returncode": 0, "command": [], "output": "ok",
    })
    monkeypatch.setattr(runner, "_prelock_episode_diagnostics", lambda *_args: {
        "all_31_complete_non_hxx_episodes_pass": True,
    })
    monkeypatch.setattr(
        runner, "create_code_lock",
        lambda _root, destination: destination.write_text("locked\n", encoding="utf-8"),
    )
    monkeypatch.setattr(runner, "verify_code_lock", lambda *_args: {"pass": True})

    def fake_calibrate(_root: Path, capture: str, spec: dict, *_args: object, **_kwargs: object):
        calibration_calls.append(capture)
        return ({
            "capture_id": spec["capture_id"], "profile_id": f"PROFILE_{capture}",
        }, {
            "calibration_execution_count": 1,
            "per_action_calibration_executed": False,
            "cross_action_state_propagation_used": False,
            "five_phase_episode_diagnostics": {
                action: {"EPISODE_COMPLETENESS": "PASS"}
                for action in spec["calibration_inputs"]
            },
        })

    def fake_replay(
        _root: Path, capture: str, spec: dict, action_row: dict,
        _profile: dict, _profile_sha: str, role: str, destination: Path,
        **_kwargs: object,
    ) -> dict:
        action = action_row["action"]
        replay_calls.append((capture, role, action))
        destination.mkdir(parents=True, exist_ok=False)
        fields = {
            "viewer_artifact": destination / "VIEWER.html",
            "state_artifact": destination / "STATE.npz",
            "metrics_artifact": destination / "METRICS.json",
            "physical_qa_artifact": destination / "PHYSICAL_QA.json",
            "access_artifact": destination / "ACCESS_AUDIT.json",
            "live_viewer_qa_artifact": destination / "LIVE_VIEWER_QA.json",
            "episode_diagnostics_artifact": destination / "FIVE_PHASE_EPISODE_DIAGNOSTICS.json",
        }
        for path in fields.values():
            path.write_text(f"{capture}:{action}:{path.name}\n", encoding="utf-8")
        return {
            "action": action, "role": role,
            "execution_result": "BLOCKED", "fixed_profile_observability": "BLOCKED",
            **{key: str(value) for key, value in fields.items()},
        }

    monkeypatch.setattr(runner, "calibrate_capture", fake_calibrate)
    monkeypatch.setattr(runner, "run_action_replay", fake_replay)
    status = run_reference_gate(tmp_path / "root", tmp_path / "goal")

    assert calibration_calls == ["CAPTURE1", "CAPTURE2"]
    assert replay_calls == [
        ("CAPTURE1", REFERENCE_ROLE, "initial_still"),
        ("CAPTURE1", REFERENCE_ROLE, "t_pose"),
        ("CAPTURE2", REFERENCE_ROLE, "00_initial_still"),
        ("CAPTURE2", REFERENCE_ROLE, "02_t_pose"),
    ]
    assert status["run_state"] == "AWAITING_SUPERVISOR_REFERENCE_VIEWER_QA"
    assert status["reference_viewer_count"] == 4
    assert status["calibration_self_replay_executed"] is False
    assert status["main_suite_executed"] is False
    assert status["hxx_executed"] is False
    assert not list((tmp_path / "goal").rglob("MAIN_SUITE*"))
    assert not list((tmp_path / "goal").rglob("HXX"))


ARTIFACT_CLASSES = (
    "profile", "viewer", "state", "metrics", "physical_qa",
    "access_audit", "live_viewer_qa",
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _qa_entry(
    base: Path, *, capture: str, role: str, action: str, physical: str,
) -> tuple[dict, dict]:
    entry_id = f"{capture}:{role}:{action}"
    artifacts = {}
    for artifact_class in ARTIFACT_CLASSES:
        path = base / f"{entry_id.replace(':', '_')}_{artifact_class}.artifact"
        path.write_text(f"immutable {entry_id} {artifact_class}\n", encoding="utf-8")
        artifacts[artifact_class] = {"path": str(path.resolve()), "sha256": _sha(path)}
    binding = hashlib.sha256(json.dumps(
        artifacts, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    authority = {
        "entry_id": entry_id, "capture": capture, "role": role, "action": action,
        "execution_result": "PASS", "artifacts": artifacts,
        "artifact_binding_sha256": binding,
    }
    external = {
        "entry_id": entry_id, "artifact_binding_sha256": binding,
        "LIVE_VIEWER_RUNTIME_QA": "PASS",
        "LIVE_VIEWER_STATE_CORRESPONDENCE": "PASS",
        "SUPERVISOR_PHYSICAL_JUDGMENT": physical,
        "SUPERVISOR_NOTES": "Independent browser review completed for this exact binding.",
    }
    if entry_id == "CAPTURE2:MAIN_SUITE_1:06_elbow_left":
        external["CAPTURE2_06_ELBOW_LEFT_IN_FRONT"] = "INCONCLUSIVE"
    return authority, external


def _qa_fixture(tmp_path: Path) -> tuple[Path, Path, dict]:
    goal = tmp_path / "goal"; goal.mkdir()
    first, first_external = _qa_entry(
        tmp_path, capture="CAPTURE1", role="HXX", action="walk", physical="PASS",
    )
    elbow, elbow_external = _qa_entry(
        tmp_path, capture="CAPTURE2", role="MAIN_SUITE_1",
        action="06_elbow_left", physical="INCONCLUSIVE",
    )
    template = {
        "schema": "biospur-fusion-v0-live-viewer-qa-template-v1",
        "entries": [first, elbow],
    }
    template_path = goal / "LIVE_VIEWER_QA_TEMPLATE.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")
    (goal / "RUN_STATUS.json").write_text(json.dumps({
        "run_state": "AWAITING_SUPERVISOR_VIEWER_QA",
        "live_viewer_qa_template_sha256": _sha(template_path),
    }), encoding="utf-8")
    external_path = tmp_path / "external.json"
    external_path.write_text(json.dumps({
        "schema": "biospur-fusion-v0-supervisor-live-viewer-qa-v1",
        "entries": [first_external, elbow_external],
    }), encoding="utf-8")
    return goal, external_path, template


def test_external_viewer_qa_is_append_only_and_keeps_all_judgments_separate(
    tmp_path: Path,
) -> None:
    goal, external_path, template = _qa_fixture(tmp_path)
    locked = Path(template["entries"][0]["artifacts"]["viewer"]["path"])
    locked_before = _sha(locked)
    result = _record_and_validate_supervisor_qa(goal, external_path)
    assert result["LIVE_VIEWER_QA"] == "PARTIAL"
    assert result["LIVE_VIEWER_RUNTIME_QA"] == "PASS"
    assert result["LIVE_VIEWER_STATE_CORRESPONDENCE"] == "PASS"
    assert result["SUPERVISOR_PHYSICAL_JUDGMENT"] == "INCONCLUSIVE"
    assert result["CAPTURE1_SUPERVISOR_PHYSICAL_COHERENCE"] == "PASS"
    assert result["CAPTURE2_SUPERVISOR_PHYSICAL_COHERENCE"] == "PARTIAL"
    assert result["CAPTURE2_06_ELBOW_LEFT_IN_FRONT"] == "INCONCLUSIVE"
    assert _sha(locked) == locked_before
    assert not (goal / "LIVE_VIEWER_QA.json").exists()
    assert not (goal / "FINAL_RESULT.json").exists()


@pytest.mark.parametrize("artifact_class", ARTIFACT_CLASSES)
def test_external_qa_fails_closed_if_any_bound_artifact_class_mutates(
    tmp_path: Path, artifact_class: str,
) -> None:
    goal, external_path, template = _qa_fixture(tmp_path)
    target = Path(template["entries"][0]["artifacts"][artifact_class]["path"])
    target.write_bytes(target.read_bytes() + b"hostile mutation\n")
    with pytest.raises(ValueError, match=f"immutable {artifact_class} artifact mutation"):
        _record_and_validate_supervisor_qa(goal, external_path)
    assert not (goal / "LIVE_VIEWER_QA.json").exists()
    assert not (goal / "FINAL_RESULT.json").exists()


def test_finalizer_rehashes_every_bound_artifact_after_qa_acceptance_and_before_final() -> None:
    source = inspect.getsource(finalize_after_supervisor_qa)
    acceptance = source.index("_record_and_validate_supervisor_qa")
    second_rehash = source.index("_persist_live_qa_after_second_rehash", acceptance)
    final = source.index("_finalize", second_rehash)
    assert acceptance < second_rehash < final
    gate_source = inspect.getsource(_persist_live_qa_after_second_rehash)
    assert gate_source.index("_verify_template_artifacts") < gate_source.index("dump_json")


def test_mutation_between_qa_acceptance_and_second_rehash_leaves_no_root_artifacts(
    tmp_path: Path,
) -> None:
    goal, external_path, template = _qa_fixture(tmp_path)
    accepted = _record_and_validate_supervisor_qa(goal, external_path)
    assert accepted["review_complete"] is True
    assert not (goal / "LIVE_VIEWER_QA.json").exists()
    target = Path(template["entries"][0]["artifacts"]["metrics"]["path"])
    target.write_bytes(target.read_bytes() + b"mutation after acceptance\n")
    status = json.loads((goal / "RUN_STATUS.json").read_text())
    with pytest.raises(ValueError, match="immutable metrics artifact mutation"):
        _persist_live_qa_after_second_rehash(goal, status, template, accepted)
    assert not (goal / "LIVE_VIEWER_QA.json").exists()
    assert not (goal / "FINAL_RESULT.json").exists()


def test_replay_classification_uses_exact_role_sets_and_role_local_execution() -> None:
    protocol = load_protocol(ROOT)
    entries = []
    for capture, spec in protocol["captures"].items():
        expected = {
            "CALIBRATION_SELF_REPLAY": spec["calibration_inputs"],
            "MAIN_SUITE_1": spec["main_suite_1"],
            "MAIN_SUITE_2": spec["main_suite_2"],
            "HXX": [row["action"] for row in spec["hxx"]],
        }
        for role, actions in expected.items():
            for action in actions:
                entries.append({
                    "entry_id": f"{capture}:{role}:{action}",
                    "capture": capture, "role": role, "action": action,
                    "execution_result": "PASS",
                })
    next(row for row in entries if row["capture"] == "CAPTURE2" and row["role"] == "MAIN_SUITE_1")[
        "execution_result"
    ] = "DEGRADED_FAIL"
    removed = next(
        row for row in entries
        if row["capture"] == "CAPTURE1" and row["role"] == "HXX"
    )
    entries.remove(removed)
    evidence = _derive_reviewed_replay_evidence(protocol, {"entries": entries})
    assert evidence["CAPTURE2"]["roles"]["CALIBRATION_SELF_REPLAY"]["role_execution_outcome"] == "PASS"
    assert evidence["CAPTURE2"]["roles"]["MAIN_SUITE_1"]["role_execution_outcome"] == "PARTIAL"
    assert evidence["CAPTURE1"]["roles"]["HXX"]["exact_expected_set_coverage"] is False
    assert evidence["CAPTURE1"]["roles"]["HXX"]["missing_actions"] == [removed["action"]]


def test_capture_bound_body_models_preserve_both_reversed_forearm_identities() -> None:
    protocol = load_protocol(ROOT)
    models = {}
    for capture, spec in protocol["captures"].items():
        model = corrected_body_model(
            ROOT, identity_mapping=spec["identity"],
            identity_provenance=f"CAPTURE_BOUND_PROFILE_IDENTITY:{spec['capture_id']}",
        )
        models[capture] = model
        assert model.identity_mapping == spec["identity"]
        assert {sensor.sensor_id: sensor.segment for sensor in model.imus} == spec["identity"]
        assert model.identity_provenance["source"] == (
            f"CAPTURE_BOUND_PROFILE_IDENTITY:{spec['capture_id']}"
        )
    assert models["CAPTURE1"].identity_mapping["BSFB165"] == "forearm_left"
    assert models["CAPTURE1"].identity_mapping["BSFEC35"] == "forearm_right"
    assert models["CAPTURE2"].identity_mapping["BSFB165"] == "forearm_right"
    assert models["CAPTURE2"].identity_mapping["BSFEC35"] == "forearm_left"


def test_body_model_has_no_implicit_universal_real_capture_identity() -> None:
    with pytest.raises(TypeError):
        corrected_body_model(ROOT)  # type: ignore[call-arg]


def test_joint_reference_calibration_provenance_is_exact_for_each_capture() -> None:
    protocol = load_protocol(ROOT)
    geometry = load_config(ROOT / "config/biospur_fusion_v0/config.json").section(
        "display_geometry"
    )
    for capture, spec in protocol["captures"].items():
        model = corrected_body_model(
            ROOT, identity_mapping=spec["identity"],
            identity_provenance=f"CAPTURE_BOUND_PROFILE_IDENTITY:{spec['capture_id']}",
        )
        source_action = spec["reference_actions"][0]
        window_hash = hashlib.sha256(f"{capture}:{source_action}".encode()).hexdigest()
        provenance = f"SAME_CAPTURE_REFERENCE_ACTION:{spec['capture_id']}:{source_action}:{window_hash}"
        profile = {
            "profile_schema": PROFILE_SCHEMA, "capture_id": spec["capture_id"],
            "joint_session_reference": {
                joint.joint_id: {
                    "rotvec_parent_from_child_session_neutral": [0.0, 0.0, 0.0],
                    "capture_id": spec["capture_id"], "source_action": source_action,
                    "source_window_binding_sha256": window_hash,
                    "provenance": provenance,
                }
                for joint in model.joints
            },
        }
        static = display_static_calibration(model, profile, geometry)
        assert all(
            static.slot(joint.rest_rotation_slot).provenance == provenance
            for joint in model.joints
        )
        assert "CAPTURE1_INITIAL_STILL_ATTEMPT_2_IMU_ONLY" not in provenance


def test_common_lock_closure_contains_previously_omitted_transitive_inputs() -> None:
    locked = {str(path.relative_to(ROOT)) for path in _locked_paths(ROOT)}
    assert {
        "config/root_r6a0/body_graph.json",
        "src/biospur_fusion/root_r6a0/contracts.py",
        "src/biospur_fusion/root_r6a0/factors.py",
        "src/biospur_fusion/imu/preintegration.py",
        "src/biospur_fusion/root_r6a1c/adapter.py",
        "src/biospur_fusion/root_r6a2a/contracts.py",
    }.issubset(locked)


@pytest.mark.parametrize("relative", [
    "config/root_r6a0/body_graph.json",
    "src/biospur_fusion/imu/preintegration.py",
])
def test_common_lock_verification_rejects_hostile_transitive_mutation(
    tmp_path: Path, relative: str,
) -> None:
    copied_root = tmp_path / "copied-root"; copied_root.mkdir()
    for source in _locked_paths(ROOT):
        destination = copied_root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    subprocess.run(["git", "init", "-q"], cwd=copied_root, check=True)
    manifest_path = copied_root / "LOCK.json"
    create_code_lock(copied_root, manifest_path)
    manifest_sha = _sha(manifest_path)
    target = copied_root / relative
    target.write_bytes(target.read_bytes() + b"\n# hostile mutation\n")
    with pytest.raises(RuntimeError, match="locked common code changed"):
        verify_code_lock(copied_root, manifest_path, manifest_sha)


def test_common_lock_verification_rechecks_exact_dependency_versions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    copied_root = tmp_path / "copied-root"; copied_root.mkdir()
    for source in _locked_paths(ROOT):
        destination = copied_root / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    subprocess.run(["git", "init", "-q"], cwd=copied_root, check=True)
    manifest_path = copied_root / "LOCK.json"
    create_code_lock(copied_root, manifest_path)
    manifest_sha = _sha(manifest_path)
    actual_version = importlib.metadata.version

    def hostile_version(package: str) -> str:
        return "0.0.0-hostile" if package == "numpy" else actual_version(package)

    monkeypatch.setattr(
        "biospur_fusion.v0.dual_capture.importlib.metadata.version", hostile_version,
    )
    with pytest.raises(RuntimeError, match="locked dependency versions changed"):
        verify_code_lock(copied_root, manifest_path, manifest_sha)


@pytest.mark.parametrize(
    ("duration_s", "expected"),
    [(29.999, 10), (30.0, 10), (30.044, 10), (32.999, 10), (33.0, 11), (300.0, 50)],
)
def test_listener_pair_count_uses_only_complete_three_second_intervals(
    duration_s: float, expected: int,
) -> None:
    assert _required_clean_listener_pairs(duration_s) == expected


def test_unique_modal_integer_seed_does_not_require_absolute_majority() -> None:
    assert _integer_join_resolved({
        "integer_choice_margin_seed_votes": 33,
        "unique_modal_seed_winner": True,
        "selected_seed_fraction": 0.475,
        "mod16_agreement_fraction": 1.0,
    })


def test_integer_seed_tie_remains_unresolved() -> None:
    assert not _integer_join_resolved({
        "integer_choice_margin_seed_votes": 0,
        "unique_modal_seed_winner": False,
        "selected_seed_fraction": 0.5,
        "mod16_agreement_fraction": 1.0,
    })
