from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
import inspect
import math
import multiprocessing
import os
from pathlib import Path
import json
import time

import numpy as np
import pytest

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.causal_body_shadow_validation import (
    CausalPoseSnapshot,
    HeldRangeLabeler,
    INCIDENT_SEGMENTS_BY_NODE,
    MAXIMUM_POSE_AGE_NS,
    NESTED_MODEL_CONTRACT,
    Native200CommonClock,
    NodeLinkClock,
    PILOT_ACTIONS,
    TRAIN_ACTIONS,
    VALIDATION_ACTIONS,
    causal_shadow_features,
    compact_feature_bytes,
    common_nuisance_values,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink
from biospur_fusion.c2_uwb_calibration import causal_body_shadow_validation as shadow_owner
from tools.run_c2_causal_body_shadow_o2_pre import (
    _accelerated_crc16_ccitt_false,
    _accelerated_transport_crc,
    _assert_pilot_rows_byte_equivalent,
    _collect_indexed_future_results,
    _parallel_held_labels,
    _seal,
    _verify_sealed_directory,
    _worker_record_pid_then_fail,
    _worker_runtime_probe,
    PERF2_MAX_WORKERS,
    THREAD_LIMIT_ENVIRONMENT,
)


def _geometry() -> DisplayProxyGeometry:
    lengths = {
        "upper_arm_left": 0.30,
        "upper_arm_right": 0.30,
        "forearm_left": 0.27,
        "forearm_right": 0.27,
        "thigh_left": 0.42,
        "thigh_right": 0.42,
        "shank_left": 0.40,
        "shank_right": 0.40,
    }
    return DisplayProxyGeometry(0.55, 0.22, 0.38, lengths)


def _joints() -> dict[str, np.ndarray]:
    return {
        "pelvis_center": np.array([0.0, 0.0, 0.0]),
        "shoulder_mid": np.array([0.0, 0.0, 0.55]),
        "shoulder_left": np.array([-0.19, 0.0, 0.55]),
        "elbow_left": np.array([-0.45, 0.0, 0.45]),
        "wrist_left": np.array([-0.65, 0.0, 0.30]),
        "shoulder_right": np.array([0.19, 0.0, 0.55]),
        "elbow_right": np.array([0.45, 0.0, 0.45]),
        "wrist_right": np.array([0.65, 0.0, 0.30]),
        "hip_left": np.array([-0.11, 0.0, 0.0]),
        "knee_left": np.array([-0.11, 0.0, -0.42]),
        "ankle_left": np.array([-0.11, 0.0, -0.82]),
        "hip_right": np.array([0.11, 0.0, 0.0]),
        "knee_right": np.array([0.11, 0.0, -0.42]),
        "ankle_right": np.array([0.11, 0.0, -0.82]),
    }


def _snapshot(
    *,
    joints: dict[str, np.ndarray] | None = None,
    offsets: dict[str, np.ndarray] | None = None,
    frame: int = 1,
) -> CausalPoseSnapshot:
    points = _joints() if joints is None else joints
    node_offsets = (
        {node: points[point] for node, point in NODE_TO_PROXY_POINT.items()}
        if offsets is None else offsets
    )
    normals = {node: np.array([1.0, 0.0, 0.0]) for node in node_offsets}
    return CausalPoseSnapshot(
        action="00_initial_still",
        frame=frame,
        pose_global_ns=1_005_000_000,
        query_global_ns=1_007_000_000.0,
        pose_age_ns=2_000_000.0,
        root_world_m=np.array([2.0, 2.0, 1.0]),
        offsets_world_m=node_offsets,
        normals_world=normals,
        joints_relative_world_m=points,
    )


def _anchors() -> np.ndarray:
    return np.asarray([
        [0.0, 0.0, 0.0],
        [4.0, 0.0, 0.0],
        [0.0, 4.0, 0.0],
        [4.0, 4.0, 0.0],
        [0.0, 0.0, 3.0],
        [4.0, 0.0, 3.0],
        [0.0, 4.0, 3.0],
        [4.0, 4.0, 3.0],
    ])


def _links(*, node: str = "BSFEC35") -> list[SharedRangeLink]:
    root = np.array([2.1, 1.8, 1.05])
    offset = np.array([0.2, -0.1, 0.15])
    return [
        SharedRangeLink(
            node=node,
            anchor=anchor,
            range_m=float(np.linalg.norm(_anchors()[anchor] - (root + offset))),
            tag_offset_world_m=offset,
            link_dt_s=0.0,
            sigma_m=0.1,
        )
        for anchor in range(8)
    ]


def test_native200_clock_is_direct_timer_mapping_and_strict_floor() -> None:
    clock = Native200CommonClock(
        action="00_initial_still",
        time_root_s=np.array([1.000, 1.005, 1.010, 1.015]),
        source_pelvis_timer_us=np.array([1_000_000, 1_005_000, 1_010_000, 1_015_000]),
        source_contiguous_span_id=np.zeros(4, dtype=int),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=1_000_000_000.0,
    )
    link_clock = NodeLinkClock(
        "BSFEC35", 999.0, 18_991_000.0, 0, 1_000_000, 3_000_000
    )
    link = link_clock.link_time_ns(
        event_boot_epoch=0, strobe_us=1_990_000, t_round_us=2_000.0
    )
    # Target-node link time is mapped by its own affine clock, not the pelvis
    # pose clock.  The chosen values put it 3 ms after the 1.005 s pose row.
    assert (link_clock.a_ns_per_us, link_clock.b_ns) != (
        clock.a_ns_per_us, clock.b_ns
    )
    assert link == float(clock.global_ns[1]) + 3_000_000.0
    # Query is exactly 1.008 seconds in TIMER2 units; the 1.005 row owns it.
    row = clock.strict_floor(link)
    assert row.frame == 1
    assert row.age_ns == 3_000_000.0
    assert clock.audit()["progress_or_formal_bound_scaling"] is False


def test_link_clock_rejects_wrong_boot_before_mapping() -> None:
    clock = NodeLinkClock("BSFEC35", 1000.0, 5.0, 3, 900_000, 1_100_000)
    with pytest.raises(ValueError, match="boot"):
        clock.link_time_ns(
            event_boot_epoch=2, strobe_us=1_000_000, t_round_us=5_000.0
        )


def test_link_clock_rejects_support_extrapolation_and_half_round_crossing() -> None:
    clock = NodeLinkClock("BSFEC35", 1000.0, 5.0, 0, 1_000, 2_000)
    with pytest.raises(ValueError, match="support"):
        clock.link_time_ns(
            event_boot_epoch=0, strobe_us=999, t_round_us=0.0
        )
    with pytest.raises(ValueError, match="support"):
        clock.link_time_ns(
            event_boot_epoch=0, strobe_us=2_001, t_round_us=0.0
        )
    with pytest.raises(ValueError, match="support"):
        clock.link_time_ns(
            event_boot_epoch=0, strobe_us=1_999, t_round_us=4.0
        )
    assert math.isfinite(clock.link_time_ns(
        event_boot_epoch=0, strobe_us=1_999, t_round_us=2.0
    ))


def test_strict_floor_exact_equality_uses_previous_and_gaps_fail() -> None:
    clock = Native200CommonClock(
        action="gap",
        time_root_s=np.array([1.000, 1.005, 1.015]),
        source_pelvis_timer_us=np.array([1_000_000, 1_005_000, 1_015_000]),
        source_contiguous_span_id=np.array([0, 0, 1]),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=0.0,
    )
    exact = clock.strict_floor(1_005_000_000.0)
    assert exact.frame == 0 and exact.age_ns == 5_000_000.0
    with pytest.raises(ValueError, match="pose age"):
        clock.strict_floor(1_012_000_000.0)
    assert clock.audit()["gap_count"] == 1


def test_pose_age_upper_gate_is_not_relaxed() -> None:
    clock = Native200CommonClock(
        action="age",
        time_root_s=np.array([1.000, 1.010]),
        source_pelvis_timer_us=np.array([1_000_000, 1_010_000]),
        source_contiguous_span_id=np.array([0, 1]),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=0.0,
    )
    assert clock.strict_floor(1_000_000_000.0 + MAXIMUM_POSE_AGE_NS).frame == 0
    with pytest.raises(ValueError, match="pose age"):
        clock.strict_floor(1_000_000_000.0 + MAXIMUM_POSE_AGE_NS + 1.0)


def test_invalid_non_native_grid_fails_closed() -> None:
    with pytest.raises(ValueError, match="gap-safe"):
        Native200CommonClock(
            action="bad",
            time_root_s=np.array([1.0, 1.006]),
            source_pelvis_timer_us=np.array([1_000_000, 1_006_000]),
            source_contiguous_span_id=np.zeros(2, dtype=int),
            common_clock_a_ns_per_us=1000.0,
            common_clock_b_ns=0.0,
        )


def test_pose_clock_refuses_unbound_action_relative_time() -> None:
    with pytest.raises(ValueError, match="source pelvis TIMER2"):
        Native200CommonClock(
            action="relative_is_forbidden",
            time_root_s=np.array([0.000, 0.005]),
            source_pelvis_timer_us=np.array([4_130_000_000, 4_130_005_000]),
            source_contiguous_span_id=np.zeros(2, dtype=int),
            common_clock_a_ns_per_us=1000.0,
            common_clock_b_ns=0.0,
        )


def test_held_label_omits_every_duplicate_of_target_identity() -> None:
    links = _links()
    links.append(replace(links[0], range_m=999.0))
    kept, omitted = HeldRangeLabeler.omit_identity(
        links, (links[0].node, links[0].anchor)
    )
    assert omitted == 2
    assert all((link.node, link.anchor) != (links[0].node, 0) for link in kept)
    labeler = HeldRangeLabeler(anchors_m=_anchors())
    with pytest.raises(ValueError, match="duplicate"):
        labeler.label(
            links,
            target=links[0],
            initial_root_m=np.array([2.0, 2.0, 1.0]),
            root_velocity_mps=np.zeros(3),
        )


def test_held_range_mutation_changes_only_its_label_not_prediction() -> None:
    links = _links()
    labeler = HeldRangeLabeler(anchors_m=_anchors())
    first = labeler.label(
        links,
        target=links[0],
        initial_root_m=np.array([2.0, 2.0, 1.0]),
        root_velocity_mps=np.zeros(3),
    )
    target = replace(links[0], range_m=999.0)
    mutated = [target, *links[1:]]
    second = labeler.label(
        mutated,
        target=target,
        initial_root_m=np.array([2.0, 2.0, 1.0]),
        root_velocity_mps=np.zeros(3),
    )
    assert np.array_equal(first.root_position_m, second.root_position_m)
    assert first.predicted_range_m == second.predicted_range_m
    assert first.training_identities == second.training_identities
    assert first.eligibility_rank == second.eligibility_rank == 3
    assert first.eligibility_condition == second.eligibility_condition
    assert first.signed_innovation_m != second.signed_innovation_m


def test_held_labeler_owns_validated_immutable_nfev_without_changing_global_default() -> None:
    import inspect
    from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root

    owner = HeldRangeLabeler(anchors_m=_anchors())
    assert owner.maximum_nfev == 75
    assert inspect.signature(solve_shared_root).parameters["maximum_nfev"].default == 50
    with pytest.raises(FrozenInstanceError):
        owner.maximum_nfev = 50
    for invalid in (0, -1, 1.5, True):
        with pytest.raises(ValueError, match="positive integer"):
            HeldRangeLabeler(anchors_m=_anchors(), maximum_nfev=invalid)


def test_frozen_epoch9_held_call_cap50_fails_and_owner75_matches_cap150(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    diagnostic = json.loads((
        root / "logs/c2_o2_full_failure_diagnostic_20260905T220115Z/RESULT.json"
    ).read_text(encoding="utf-8"))
    qualification = json.loads((
        root / "logs/c2_o2_solver_cap_fixture_20260905T220552Z/RESULT.json"
    ).read_text(encoding="utf-8"))
    assert diagnostic["identity"] == {"node": "BSF1120", "anchor": 5}
    expected_calls = {
        row["cap"]: row for row in qualification["fixtures"]["9"]["calls"]
    }
    anchors, _delays, _tag_delay, _layout_sigma = __import__(
        "evaluate_c2_pair_bias_gate"
    )._load_layout()
    target_row = diagnostic["target"]
    target = SharedRangeLink(
        node="BSF1120",
        anchor=5,
        range_m=target_row["range_m"],
        tag_offset_world_m=np.asarray(target_row["tag_offset_world_m"]),
        link_dt_s=target_row["link_dt_s"],
        sigma_m=target_row["sigma_m"],
    )
    training = [SharedRangeLink(
        node=row["node"],
        anchor=row["anchor"],
        range_m=row["range_m"],
        tag_offset_world_m=np.asarray(row["tag_offset_world_m"]),
        link_dt_s=row["link_dt_s"],
        sigma_m=row["sigma_m"],
    ) for row in diagnostic["training_links"]]
    links = tuple([*training[:5], target, *training[5:]])
    initial = np.asarray(diagnostic["initial_root_m"])
    velocity = np.asarray(diagnostic["root_velocity_mps"])
    captured = []
    original = shadow_owner.solve_shared_root

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append((kwargs["maximum_nfev"], result))
        return result

    monkeypatch.setattr(shadow_owner, "solve_shared_root", capture)
    with pytest.raises(RuntimeError, match="OPTIMIZER_FAILURE"):
        HeldRangeLabeler(anchors_m=anchors, maximum_nfev=50).label(
            links,
            target=target,
            initial_root_m=initial,
            root_velocity_mps=velocity,
        )
    accepted = HeldRangeLabeler(anchors_m=anchors).label(
        links,
        target=target,
        initial_root_m=initial,
        root_velocity_mps=velocity,
    )
    assert [cap for cap, _result in captured] == [50, 75]
    failed, solved = (result for _cap, result in captured)
    assert failed.reason == "OPTIMIZER_FAILURE" and failed.nfev == 50
    expected = expected_calls[75]
    reference = expected_calls[150]
    assert solved.success and solved.nfev == expected["nfev"] == 71
    assert np.array_equal(solved.root_position_m, np.asarray(expected["root_position_m"]))
    assert solved.cost == expected["cost"] == reference["cost"]
    assert np.array_equal(
        solved.root_position_m, np.asarray(reference["root_position_m"])
    )
    assert accepted.predicted_range_m == expected["predicted_held_range_m"]
    assert accepted.signed_innovation_m == expected["signed_innovation_m"]


def test_ordinary_held_call_finishing_below_50_is_numerically_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    links = tuple(_links())
    initial = np.array([2.0, 2.0, 1.0])
    velocity = np.zeros(3)
    captured = []
    original = shadow_owner.solve_shared_root

    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        captured.append((kwargs["maximum_nfev"], result))
        return result

    monkeypatch.setattr(shadow_owner, "solve_shared_root", capture)
    old = HeldRangeLabeler(anchors_m=_anchors(), maximum_nfev=50).label(
        links, target=links[0], initial_root_m=initial, root_velocity_mps=velocity,
    )
    owned = HeldRangeLabeler(anchors_m=_anchors()).label(
        links, target=links[0], initial_root_m=initial, root_velocity_mps=velocity,
    )
    assert [cap for cap, _result in captured] == [50, 75]
    assert all(result.nfev < 50 for _cap, result in captured)
    assert np.array_equal(old.root_position_m, owned.root_position_m)
    assert old.predicted_range_m == owned.predicted_range_m
    assert old.signed_innovation_m == owned.signed_innovation_m
    assert captured[0][1].cost == captured[1][1].cost


def test_training_range_can_change_label_root_but_not_causal_features() -> None:
    links = _links()
    labeler = HeldRangeLabeler(anchors_m=_anchors())
    before_feature = causal_shadow_features(
        node=links[0].node,
        anchor_position_world_m=_anchors()[0],
        snapshot=_snapshot(),
        geometry=_geometry(),
    )
    first = labeler.label(
        links,
        target=links[0],
        initial_root_m=np.array([2.0, 2.0, 1.0]),
        root_velocity_mps=np.zeros(3),
    )
    changed = [links[0]] + [replace(link, range_m=link.range_m + 0.25) for link in links[1:]]
    second = labeler.label(
        changed,
        target=changed[0],
        initial_root_m=np.array([2.0, 2.0, 1.0]),
        root_velocity_mps=np.zeros(3),
    )
    after_feature = causal_shadow_features(
        node=links[0].node,
        anchor_position_world_m=_anchors()[0],
        snapshot=_snapshot(),
        geometry=_geometry(),
    )
    assert not np.allclose(first.root_position_m, second.root_position_m)
    assert compact_feature_bytes(before_feature) == compact_feature_bytes(after_feature)


def test_future_pose_mutation_cannot_change_current_feature_row() -> None:
    current = _snapshot(frame=10)
    before = causal_shadow_features(
        node="BSFAA61",
        anchor_position_world_m=np.array([6.0, 2.0, 1.4]),
        snapshot=current,
        geometry=_geometry(),
    )
    future_joints = _joints()
    future_joints["wrist_right"] = np.array([-10.0, 0.0, 0.0])
    _future = _snapshot(joints=future_joints, frame=11)
    after = causal_shadow_features(
        node="BSFAA61",
        anchor_position_world_m=np.array([6.0, 2.0, 1.4]),
        snapshot=current,
        geometry=_geometry(),
    )
    assert compact_feature_bytes(before) == compact_feature_bytes(after)


@pytest.mark.parametrize(
    "node,local_points",
    [
        ("BSFAA61", ("shoulder_left", "elbow_left", "wrist_left")),
        ("BSF1120", ("shoulder_right", "elbow_right", "wrist_right")),
        ("BSF44AD", ("hip_left", "knee_left", "ankle_left")),
        ("BSF3C79", ("hip_right", "knee_right", "ankle_right")),
    ],
)
def test_joint_mounted_emitters_exclude_both_incident_segments(
    node: str, local_points: tuple[str, ...]
) -> None:
    base = _snapshot()
    first = causal_shadow_features(
        node=node,
        anchor_position_world_m=np.array([8.0, 2.0, 1.0]),
        snapshot=base,
        geometry=_geometry(),
    )
    changed_points = _joints()
    for index, name in enumerate(local_points):
        changed_points[name] = np.array([-20.0 - index, 20.0, 10.0])
    changed = causal_shadow_features(
        node=node,
        anchor_position_world_m=np.array([8.0, 2.0, 1.0]),
        snapshot=_snapshot(
            joints=changed_points,
            offsets={
                node_name: value.copy()
                for node_name, value in base.offsets_world_m.items()
            },
        ),
        geometry=_geometry(),
    )
    assert INCIDENT_SEGMENTS_BY_NODE[node] == frozenset(first.incident_segments_excluded)
    assert first.torso_exposure == changed.torso_exposure
    assert first.other_limb_exposure == changed.other_limb_exposure


def test_body_union_is_bounded_and_nested_features_are_strict() -> None:
    evidence = causal_shadow_features(
        node="BSFEC35",
        anchor_position_world_m=np.array([6.0, 2.0, 1.0]),
        snapshot=_snapshot(),
        geometry=_geometry(),
    )
    assert all(
        0.0 <= value <= 1.0
        for value in (
            evidence.own_inward_probability,
            evidence.torso_exposure,
            evidence.other_limb_exposure,
            evidence.combined_other_body_exposure,
        )
    )
    nested = evidence.nested()
    assert nested["B1"][:1] == nested["B0"]
    assert nested["B2"][:2] == nested["B1"]
    assert NESTED_MODEL_CONTRACT["shadow_parameter_count"] <= 6


def test_feature_api_has_no_range_or_future_argument() -> None:
    names = tuple(inspect.signature(causal_shadow_features).parameters)
    assert names == ("node", "anchor_position_world_m", "snapshot", "geometry")
    assert not any("range" in name or "future" in name for name in names)


def test_common_nuisance_is_response_free_and_model_common() -> None:
    row = common_nuisance_values(
        node="BSFEC35",
        anchor=0,
        causal_tag_origin_m=np.array([2.0, 1.0, 1.2]),
        anchor_position_m=np.zeros(3),
        base_sigma_m=0.12,
    )
    assert tuple(row) == (
        "pair_identity",
        "causal_predicted_range_m",
        "causal_tag_origin_x_m",
        "causal_tag_origin_y_m",
        "causal_tag_origin_z_m",
        "base_sigma_m",
    )
    assert not any("innovation" in name or "action" in name for name in row)
    common = NESTED_MODEL_CONTRACT["common_nuisance"]
    assert all(model[0] == "common_nuisance" for model in (
        NESTED_MODEL_CONTRACT["B0"],
        NESTED_MODEL_CONTRACT["B1"],
        NESTED_MODEL_CONTRACT["B2"],
    ))
    assert "action_intercept" in NESTED_MODEL_CONTRACT["forbidden_nuisance"]


def test_action_split_and_fixed_pilot_are_frozen_without_overlap() -> None:
    assert len(TRAIN_ACTIONS) == 11
    assert len(VALIDATION_ACTIONS) == 8
    assert not set(TRAIN_ACTIONS) & set(VALIDATION_ACTIONS)
    assert TRAIN_ACTIONS[0] == "00_initial_still"
    assert VALIDATION_ACTIONS[0] == "12_heel_raise_left"
    assert PILOT_ACTIONS == (
        "00_initial_still", "06_elbow_left", "09_hip_right", "16_squat"
    )


def test_preflight_seal_mutation_fails_before_raw_owner(tmp_path: Path) -> None:
    member = tmp_path / "MODEL_CONTRACT.json"
    member.write_text('{"frozen":true}\n', encoding="utf-8")
    digest = _seal(tmp_path)
    _verify_sealed_directory(tmp_path, digest)
    member.write_text('{"frozen":false}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="sealed file changed"):
        _verify_sealed_directory(tmp_path, digest)


def test_accelerated_transport_crc_is_bit_exact_and_restored() -> None:
    import fusion_host_binary as transport

    original = transport.crc16_ccitt_false
    vectors = (
        b"",
        b"123456789",
        bytes(range(256)),
        bytes((index * 73 + 19) & 0xFF for index in range(4096)),
    )
    expected = tuple(original(value) for value in vectors)
    with _accelerated_transport_crc():
        assert transport.crc16_ccitt_false is _accelerated_crc16_ccitt_false
        assert tuple(transport.crc16_ccitt_false(value) for value in vectors) == expected
    assert transport.crc16_ccitt_false is original


def test_pilot_row_equivalence_is_byte_exact_including_order(tmp_path: Path) -> None:
    reference = tmp_path / "reference.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    rows = (
        b'{"anchor":0,"condition":12.0,"feature":0.125,"node":"A"}\n'
        b'{"anchor":1,"condition":13.0,"feature":0.25,"node":"A"}\n'
    )
    reference.write_bytes(rows)
    candidate.write_bytes(rows)
    assert _assert_pilot_rows_byte_equivalent(candidate, reference) == _sha256_for_test(rows)
    candidate.write_bytes(rows.splitlines(keepends=True)[1] + rows.splitlines(keepends=True)[0])
    with pytest.raises(RuntimeError, match="differ"):
        _assert_pilot_rows_byte_equivalent(candidate, reference)


def _sha256_for_test(value: bytes) -> str:
    import hashlib

    return hashlib.sha256(value).hexdigest()


def _delayed_indexed_result(index: int, delay_s: float) -> tuple[tuple[int, int, None], ...]:
    time.sleep(delay_s)
    return ((index, index * 10, None),)


def _raise_worker_error() -> tuple[tuple[int, int, None], ...]:
    raise RuntimeError("deliberate worker failure")


def test_parallel_collection_is_original_index_order_despite_completion_order() -> None:
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(_delayed_indexed_result, 0, 0.03): 0,
            executor.submit(_delayed_indexed_result, 1, 0.02): 1,
            executor.submit(_delayed_indexed_result, 2, 0.00): 2,
        }
        assert _collect_indexed_future_results(
            futures, expected_count=3
        ) == ((0, None), (10, None), (20, None))


def test_parallel_collection_propagates_worker_exception() -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(_delayed_indexed_result, 0, 0.02): 0,
            executor.submit(_raise_worker_error): 1,
        }
        with pytest.raises(RuntimeError, match="deliberate worker failure"):
            _collect_indexed_future_results(futures, expected_count=2)


def test_spawn_worker_exception_propagates_and_worker_is_reaped(tmp_path: Path) -> None:
    pid_path = tmp_path / "worker.pid"
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        future = executor.submit(_worker_record_pid_then_fail, str(pid_path))
        with pytest.raises(RuntimeError, match="deliberate spawn worker failure"):
            future.result(timeout=10.0)
        worker_pid = int(pid_path.read_text(encoding="utf-8"))
        assert Path(f"/proc/{worker_pid}").exists()
    deadline = time.monotonic() + 2.0
    while Path(f"/proc/{worker_pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not Path(f"/proc/{worker_pid}").exists()


def test_spawn_workers_are_isolated_and_nested_threads_are_capped() -> None:
    assert PERF2_MAX_WORKERS == 4
    assert all(os.environ[name] == "1" for name in THREAD_LIMIT_ENVIRONMENT)
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as executor:
        probes = tuple(executor.map(_worker_runtime_probe, (0.02, 0.02)))
    assert all(pid != os.getpid() for pid, _environment in probes)
    assert all(
        all(environment[name] == "1" for name in THREAD_LIMIT_ENVIRONMENT)
        for _pid, environment in probes
    )


def test_parallel_held_labels_are_numerically_identical_to_serial_calls() -> None:
    links = tuple(_links())
    anchors = _anchors()
    initial = np.array([2.0, 2.0, 1.0])
    velocity = np.zeros(3)
    serial_owner = HeldRangeLabeler(anchors_m=anchors)
    serial = tuple(
        serial_owner.label(
            links,
            target=link,
            initial_root_m=initial,
            root_velocity_mps=velocity,
        )
        for link in links
    )
    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=2, mp_context=context) as executor:
        parallel = _parallel_held_labels(
            executor,
            links,
            anchors=anchors,
            initial_root=initial,
            velocity=velocity,
        )
    for expected, (actual, error) in zip(serial, parallel, strict=True):
        assert error is None and actual is not None
        assert np.array_equal(actual.root_position_m, expected.root_position_m)
        assert actual.predicted_range_m == expected.predicted_range_m
        assert actual.signed_innovation_m == expected.signed_innovation_m
        assert actual.rank == expected.rank
        assert actual.condition == expected.condition
        assert actual.training_identities == expected.training_identities
