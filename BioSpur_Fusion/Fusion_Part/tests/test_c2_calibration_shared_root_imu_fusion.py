from types import SimpleNamespace
import json

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

import run_c2_h01_shared_root_imu_fusion as runner

from run_c2_h01_shared_root_imu_fusion import (
    EPOCH_NS,
    _add_position_payload_once,
    _articulated_mechanism_nondegeneracy_gate,
    _apply_measurement_time_footholds_to_observation,
    _positive_swing_cues,
    _contact_manifold_quality_state,
    _confirmed_measurement_footholds,
    _effective_body_update,
    _json_ready,
    _native200_pose_continuity,
    _no_foothold_accepted_gap_audit,
    _prior_held_uwb_gate,
    _published_pose_gauge_at_measurement,
    _project_carry_at_current_base,
    _reexpress_observation_root_to_published_pose,
    _select_geometry_links,
    _contact_reconcile_step_audit,
    _shared_root_observations,
    _usable_epoch_groups,
    _validate_root_pose_gauge_source,
    _write_final_hinge_projection_failure,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink
from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    DualFootFootholdCorrector,
    FootContactEvidence,
)
from biospur_fusion.root_r3.models import RootState


def _row(node: str, epoch: int, valid_count: int, *, offset_us: int = 0):
    valid_slots = tuple(range(valid_count))
    return SimpleNamespace(
        node=node,
        strobe_us=epoch * (EPOCH_NS // 1_000) + offset_us,
        valid_mask=sum(1 << slot for slot in valid_slots),
        anchor_ids=tuple(range(8)),
        ranges_mm=tuple(1_000 if slot in valid_slots else 0 for slot in range(8)),
        t_round_us=tuple(100.0 for _ in range(8)),
    )


def test_usable_epoch_groups_keeps_partial_epochs_and_best_duplicate():
    clocks = {
        node: SimpleNamespace(a_ns_per_us=1_000.0, b_ns=0.0)
        for node in ("A", "B", "C", "D")
    }
    retained = []
    for epoch in range(10):
        retained.extend(_row(node, epoch, 7) for node in clocks)
    retained.extend([
        _row("A", 3, 5, offset_us=100),
        _row("A", 3, 8, offset_us=200),
        _row("B", 3, 3, offset_us=300),
    ])

    groups, audit = _usable_epoch_groups({"retained": retained}, clocks)

    assert len(groups) == 10
    assert all(len(group) == 4 for group in groups)
    selected_a = next(row for row in groups[3] if row.node == "A")
    assert selected_a.valid_mask == 0xFF
    assert audit == {
        "minimum_nodes_required": 1,
        "node_count_histogram": {"4": 10},
        "partial_epochs_consumed": 10,
        "complete_ten_node_epochs": 0,
        "duplicate_rows_resolved": 2,
        "rows_rejected_below_four_ranges": 1,
    }


def test_usable_epoch_groups_accepts_single_node_with_four_ranges():
    clocks = {"A": SimpleNamespace(a_ns_per_us=1_000.0, b_ns=0.0)}
    retained = [_row("A", epoch, 4) for epoch in range(10)]

    groups, audit = _usable_epoch_groups({"retained": retained}, clocks)

    assert len(groups) == 10
    assert all(len(group) == 1 for group in groups)
    assert audit["minimum_nodes_required"] == 1
    assert audit["node_count_histogram"] == {"1": 10}


def test_usable_epoch_groups_fails_closed_without_ten_epochs():
    clocks = {
        node: SimpleNamespace(a_ns_per_us=1_000.0, b_ns=0.0)
        for node in ("A", "B", "C", "D")
    }
    retained = [
        _row(node, epoch, 8)
        for epoch in range(9)
        for node in clocks
    ]

    with pytest.raises(RuntimeError, match="fewer than ten"):
        _usable_epoch_groups({"retained": retained}, clocks)


def _link(node: str, anchor: int, score: float) -> SharedRangeLink:
    return SharedRangeLink(
        node=node,
        anchor=anchor,
        range_m=1.0,
        tag_offset_world_m=[0.0, 0.0, 0.0],
        link_dt_s=0.0,
        sigma_m=0.1,
        facing_score=score,
    )


def test_top4_facing_selects_four_best_links_per_node():
    links = [
        _link(node, anchor, float(anchor) + (0.1 if node == "B" else 0.0))
        for node in ("A", "B")
        for anchor in range(8)
    ]

    selected = _select_geometry_links(links, "top4_facing")

    assert len(selected) == 8
    assert {
        node: {link.anchor for link in selected if link.node == node}
        for node in ("A", "B")
    } == {"A": {4, 5, 6, 7}, "B": {4, 5, 6, 7}}


def test_all_link_selection_is_inert():
    links = [_link("A", anchor, float(anchor)) for anchor in range(4)]

    assert _select_geometry_links(links, "all") == links


def test_static_actions_keep_root_fusion_but_disable_articulated_correction():
    for action in ("00_initial_still", "02_t_pose", "17_final_still"):
        assert _effective_body_update(action, "articulated_consensus") == "shared_root"
    assert _effective_body_update("16_squat", "articulated_consensus") == "articulated_consensus"
    assert _effective_body_update("H01_boxing", "articulated_consensus") == "articulated_consensus"


def _base_at(angle_rad: float) -> dict[str, np.ndarray]:
    base = {segment: np.eye(3) for segment in SEGMENTS}
    base["pelvis"] = Rotation.from_rotvec([0.0, 0.0, angle_rad]).as_matrix()
    return base


def _base_owned_projector(base, _carry):
    angle = float(np.arctan2(base["pelvis"][1, 0], base["pelvis"][0, 0]))
    projected = {segment: np.zeros(3) for segment in SEGMENTS}
    projected["pelvis"] = np.array([angle, 0.0, 0.0])
    return projected, {
        "joint": {},
        "pre_projection_below_rom_count": 0,
        "pre_projection_above_rom_count": 0,
        "post_projection_all_inside_rom": True,
        "fk_direction_residual_maximum_deg": 0.0,
    }


def test_current_base_carry_is_idempotent_after_base_changes() -> None:
    carry = {segment: np.zeros(3) for segment in SEGMENTS}

    first, _ = _project_carry_at_current_base(
        _base_at(0.2), carry, _base_owned_projector
    )
    second, _ = _project_carry_at_current_base(
        _base_at(0.8), first, _base_owned_projector
    )
    repeated, _ = _project_carry_at_current_base(
        _base_at(0.8), second, _base_owned_projector
    )

    np.testing.assert_allclose(first["pelvis"], [0.2, 0.0, 0.0])
    np.testing.assert_allclose(second["pelvis"], [0.8, 0.0, 0.0])
    for segment in SEGMENTS:
        np.testing.assert_array_equal(repeated[segment], second[segment])


def test_final_hinge_failure_persists_subgates_and_row_owners(tmp_path) -> None:
    final_gate = {
        "all_frames_inside_rom": False,
        "fk_direction_residual_maximum_deg": 4e-6,
        "fk_direction_residual_limit_deg": 3e-6,
        "fk_direction_residual_pass": False,
        "projection_idempotency_maximum_rad": 2e-6,
        "projection_idempotency_limit_rad": 1e-6,
        "projection_idempotency_pass": False,
        "segment_correction_maximum_rad": 0.4,
        "segment_correction_limit_rad": 0.31,
        "segment_correction_pass": False,
    }
    row = {
        "row_index": 7,
        "fraction": 0.25,
        "outside_rom_joints": ["elbow_right"],
        "fk_direction_residual_maximum_deg": 4e-6,
        "idempotency_maximum_rad": 2e-6,
        "idempotency_maximum_segment": "forearm_right",
        "segment_correction_maximum_rad": 0.4,
        "segment_correction_maximum_segment": "forearm_right",
    }

    path = _write_final_hinge_projection_failure(
        tmp_path,
        action="H02_golf",
        trajectory_owner="owner:sha",
        observation_count=10,
        projected_update_count=2,
        final_gate=final_gate,
        final_row_audit=[row],
        wall_s_at_failure=1.25,
    )
    payload = json.loads(path.read_text())

    assert payload["final_gate"] == final_gate
    for name in (
        "outside_rom", "fk_direction_residual",
        "projection_idempotency", "segment_correction",
    ):
        assert payload["failure_rows"][name][0]["row_index"] == 7


def test_json_ready_normalizes_nested_numpy_scalars_without_value_change() -> None:
    source = {
        "flag": np.bool_(True),
        "count": np.int64(7),
        "value": np.float64(1.25),
        "nested": [np.asarray([1.0, 2.0])],
    }

    normalized = _json_ready(source)

    assert normalized == {
        "flag": True,
        "count": 7,
        "value": 1.25,
        "nested": [[1.0, 2.0]],
    }
    json.dumps(normalized)


def test_native200_continuity_audits_root_and_final_segment_so3_steps() -> None:
    times = np.array([0.0, 0.005, 0.010])
    root = np.array([[0.0, 0.0, 0.9], [0.002, 0.0, 0.9], [0.004, 0.0, 0.9]])
    correction = np.zeros((3, len(SEGMENTS), 3))
    correction[1:, SEGMENTS.index("pelvis"), 2] = [0.01, 0.02]
    metrics, arrays = _native200_pose_continuity(
        absolute_time_s=times,
        root_position_world_m=root,
        correction_rotvec=correction,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        action_start_s=0.0,
        action_stop_s=1.0,
        transition_period_s=0.12,
        correction_cap_rad=0.32,
    )
    assert np.isclose(metrics["root_xy_step_m"]["maximum"], 0.002)
    assert np.isclose(metrics["segment_final_so3_step_rad"]["maximum"], 0.01)
    assert arrays["final_segment_so3_step_rad"].shape == (2, len(SEGMENTS))


def test_unscheduled_and_failed_solve_store_current_base_carry(monkeypatch) -> None:
    nodes = ("BSF31CC", "BSFAA61")
    groups = [
        [SimpleNamespace(node=node, strobe_us=strobe, frame_us=strobe)
         for node in nodes]
        for strobe in (20, 80)
    ]
    clocks = {
        node: SimpleNamespace(
            a_ns_per_us=1_000.0,
            b_ns=0.0,
            seconds=lambda value: value * 1e-6,
        )
        for node in nodes
    }
    links = [
        SharedRangeLink(
            node=node, anchor=anchor, range_m=1.0,
            tag_offset_world_m=np.zeros(3), link_dt_s=0.0,
            sigma_m=0.1, facing_score=0.0,
        )
        for node in nodes for anchor in (0, 1)
    ]
    monkeypatch.setattr(runner, "_tracker", lambda _initial: {"velocity": np.zeros(3)})
    monkeypatch.setattr(
        runner, "_prediction", lambda _tracker, _time: (np.ones(3), 0.1)
    )
    monkeypatch.setattr(
        runner, "_reference_time",
        lambda group, model: model[group[0].node].seconds(group[0].strobe_us),
    )
    monkeypatch.setattr(runner, "_build_links", lambda *args, **kwargs: links)
    monkeypatch.setattr(
        runner, "solve_shared_root",
        lambda *args, **kwargs: SimpleNamespace(
            success=True, root_position_m=np.ones(3), condition=1.0
        ),
    )
    monkeypatch.setattr(
        runner, "estimate_leave_node_uncertainty",
        lambda *args, **kwargs: SimpleNamespace(
            covariance_m2=np.eye(3), successful_leave_node_solves=2
        ),
    )
    monkeypatch.setattr(runner, "_update_tracker", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        runner, "solve_articulated_ranges",
        lambda *args, **kwargs: SimpleNamespace(
            success=False, pose_observable_rank=0,
            reason="SYNTHETIC_SCHEDULED_REJECT",
        ),
    )

    observations = _shared_root_observations(
        {"groups": groups, "lo": 0, "hi": 100_000},
        proxy_at_fraction=lambda _fraction: ({}, {}, 0),
        anchors=np.zeros((8, 3)),
        delays=np.zeros(8),
        tag_delay=0.0,
        layout_sigma=0.1,
        clocks=clocks,
        policy="raw_all",
        biases=None,
        body_update="articulated_consensus",
        base_rotations_at_fraction=_base_at,
        geometry=SimpleNamespace(),
        articulated_stride=4,
        node_selection="all_available",
        hinge_projector=_base_owned_projector,
    )

    assert observations[0]["articulated_attempted"]
    assert not observations[0]["articulated_accepted"]
    assert not observations[1]["articulated_attempted"]
    pelvis_index = SEGMENTS.index("pelvis")
    np.testing.assert_allclose(
        observations[0]["segment_correction_rotvec"][pelvis_index],
        [0.2, 0.0, 0.0],
    )
    np.testing.assert_allclose(
        observations[1]["segment_correction_rotvec"][pelvis_index],
        [0.8, 0.0, 0.0],
    )


def test_accepted_articulated_observation_owns_root_covariance_and_tracker(
    monkeypatch,
) -> None:
    nodes = ("BSF31CC", "BSFAA61")
    group = [
        SimpleNamespace(node=node, strobe_us=20, frame_us=25)
        for node in nodes
    ]
    clocks = {
        node: SimpleNamespace(
            a_ns_per_us=1_000.0,
            b_ns=0.0,
            seconds=lambda value: value * 1e-6,
        )
        for node in nodes
    }
    links = [
        SharedRangeLink(
            node=node, anchor=anchor, range_m=1.0,
            tag_offset_world_m=np.zeros(3), link_dt_s=0.0,
            sigma_m=0.1, facing_score=0.0,
        )
        for node in nodes for anchor in (0, 1)
    ]
    monkeypatch.setattr(runner, "_tracker", lambda _initial: {"velocity": np.zeros(3)})
    monkeypatch.setattr(runner, "_prediction", lambda *_args: (np.ones(3), 0.1))
    monkeypatch.setattr(runner, "_reference_time", lambda *_args: 20e-6)
    monkeypatch.setattr(runner, "_build_links", lambda *args, **kwargs: links)
    monkeypatch.setattr(
        runner, "solve_shared_root",
        lambda *args, **kwargs: SimpleNamespace(
            success=True, root_position_m=np.ones(3), condition=1.0
        ),
    )
    monkeypatch.setattr(
        runner, "estimate_leave_node_uncertainty",
        lambda *args, **kwargs: SimpleNamespace(
            covariance_m2=np.eye(3), successful_leave_node_solves=2
        ),
    )
    articulated_root = np.array([9.0, 8.0, 7.0])
    articulated_covariance = np.diag([4.0, 5.0, 6.0])
    monkeypatch.setattr(
        runner, "solve_articulated_ranges",
        lambda *args, **kwargs: SimpleNamespace(
            success=True,
            pose_observable_rank=1,
            root_position_m=articulated_root,
            root_covariance_m2=articulated_covariance,
            segment_correction_rotvec={
                segment: np.zeros(3) for segment in SEGMENTS
            },
            facing_nlos_weight=np.ones(len(links)),
            hinge_projection={
                "joint": {},
                "pre_projection_below_rom_count": 0,
                "pre_projection_above_rom_count": 0,
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
            prefit_physical_residual_m=np.ones(len(links)) * 0.1,
            physical_residual_m=np.ones(len(links)) * 0.05,
        ),
    )
    tracker_updates = []
    monkeypatch.setattr(
        runner, "_update_tracker",
        lambda _tracker, root, *_args: tracker_updates.append(root.copy()),
    )
    observations = _shared_root_observations(
        {"groups": [group], "lo": 0, "hi": 100_000},
        proxy_at_fraction=lambda _fraction: ({}, {}, 0),
        anchors=np.zeros((8, 3)), delays=np.zeros(8), tag_delay=0.0,
        layout_sigma=0.1, clocks=clocks, policy="raw_all", biases=None,
        body_update="articulated_consensus",
        base_rotations_at_fraction=_base_at,
        geometry=SimpleNamespace(), articulated_stride=1,
        node_selection="all_available", hinge_projector=_base_owned_projector,
    )
    np.testing.assert_array_equal(observations[0]["position_m"], articulated_root)
    np.testing.assert_array_equal(
        observations[0]["covariance_m2"], articulated_covariance
    )
    np.testing.assert_array_equal(tracker_updates[0], articulated_root)


def _contact_aware_payload() -> dict:
    return {
        "measurement_time_s": 1.0,
        "availability_time_s": 1.07,
        "anchors": (0, 1, 2, 3),
        "sequence": 7,
        "position_m": np.array([1.0, 1.0, 1.0]),
        "covariance_m2": np.eye(3),
        "measurement_tracker_velocity_mps": np.array([0.2, 0.0, 0.0]),
        "segment_correction_rotvec": np.zeros((len(SEGMENTS), 3)),
        "root_pose_gauge_correction_rotvec": np.zeros((len(SEGMENTS), 3)),
        "root_pose_gauge_owner": "ARTICULATED_RANGE_PROJECTED_CORRECTION",
        "facing_nlos_weight": np.ones(8),
        "articulated_attempted": True,
        "articulated_accepted": True,
        "articulated_active_segments": ("pelvis",),
        "articulated_prefit_median_abs_m": 0.1,
        "articulated_postfit_median_abs_m": 0.05,
        "articulated_pose_observable_rank": 1,
        "hinge_projection": {},
        "_articulated_links": tuple(range(8)),
        "_fraction": 0.25,
    }


def test_measurement_time_two_footholds_atomically_replace_one_observation(
    monkeypatch,
) -> None:
    payload = _contact_aware_payload()
    final_root = np.array([2.0, 3.0, 4.0])
    final_covariance = np.diag([0.2, 0.3, 0.4])
    final_correction = {
        segment: np.full(3, 0.001 * (index + 1))
        for index, segment in enumerate(SEGMENTS)
    }
    calls = []

    def fake_solve(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            success=True,
            reason="ACCEPTED",
            pose_observable_rank=2,
            root_position_m=final_root,
            root_covariance_m2=final_covariance,
            segment_correction_rotvec=final_correction,
            facing_nlos_weight=np.full(8, 0.75),
            prefit_physical_residual_m=np.array([0.2, -0.1]),
            physical_residual_m=np.array([0.02, -0.01]),
            maximum_joint_closure_m=0.01,
            hinge_projection={
                "joint": {},
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
        )

    monkeypatch.setattr(runner, "solve_articulated_ranges", fake_solve)
    monkeypatch.setattr(
        runner, "corrected_proxy_points",
        lambda *_args: {"ankle_left": np.zeros(3), "ankle_right": np.zeros(3)},
    )
    footholds = {"left": final_root.copy(), "right": final_root.copy()}
    diagnostic = _apply_measurement_time_footholds_to_observation(
        payload,
        footholds_world_m=footholds,
        anchors=np.zeros((8, 3)),
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
        hinge_projector=_base_owned_projector,
    )

    assert len(calls) == 1
    assert set(calls[0][1]["point_constraints_world_m"]) == {
        "ankle_left", "ankle_right",
    }
    assert set(calls[0][1]["active_segments"]) >= {
        "pelvis", "thigh_left", "shank_left", "thigh_right", "shank_right",
    }
    assert diagnostic["accepted"]
    assert diagnostic["projected_foothold_xy_residual_m"] == {
        "left": 0.0, "right": 0.0,
    }
    np.testing.assert_array_equal(payload["position_m"], final_root)
    np.testing.assert_array_equal(payload["covariance_m2"], final_covariance)
    assert payload["root_pose_gauge_owner"] == (
        "CONTACT_AWARE_ARTICULATED_RANGE_PROJECTED_CORRECTION"
    )
    np.testing.assert_array_equal(
        payload["segment_correction_rotvec"],
        np.stack([final_correction[segment] for segment in SEGMENTS]),
    )


def test_published_pose_measurement_query_is_exact_time_and_never_future() -> None:
    calls = []

    class PoseOwner:
        def ankle_offsets_for_published_correction(self, time_s, correction):
            calls.append((time_s, correction["pelvis"].copy()))
            return {
                "left": np.array([time_s, 0.0, 0.0]),
                "right": np.array([-time_s, 0.0, 0.0]),
            }

    evidence = {
        side: SimpleNamespace(side=side, confidence=0.9, contact=True)
        for side in ("left", "right")
    }
    history = [
        {
            "time_s": time_s,
            "correction_rotvec": {
                segment: np.full(3, time_s) for segment in SEGMENTS
            },
            "evidence": evidence,
            "primary_side": "left",
        }
        for time_s in (1.0, 1.1)
    ]

    assert _published_pose_gauge_at_measurement(
        history, measurement_time_s=0.9, pose_owner=PoseOwner()
    ) is None
    offsets, _evidence, source_time, primary, correction = (
        _published_pose_gauge_at_measurement(
            history, measurement_time_s=1.05, pose_owner=PoseOwner()
        )
    )
    assert source_time == 1.0
    assert primary == "left"
    assert calls[-1][0] == 1.05
    np.testing.assert_array_equal(calls[-1][1], np.full(3, 1.0))
    np.testing.assert_array_equal(offsets["left"], [1.05, 0.0, 0.0])
    np.testing.assert_array_equal(correction["pelvis"], np.full(3, 1.0))

    missing_primary = [{**history[0], "primary_side": None}]
    with pytest.raises(RuntimeError, match="primary-side"):
        _published_pose_gauge_at_measurement(
            missing_primary,
            measurement_time_s=1.05,
            pose_owner=PoseOwner(),
        )


@pytest.mark.parametrize(
    ("source_owner", "source_offset_x"),
    [
        ("SHARED_ROOT_BASE_ONLY_PROXY", 0.0),
        ("ARTICULATED_RANGE_PROJECTED_CORRECTION", 0.10),
        (
            "CONTACT_AWARE_ARTICULATED_RANGE_PROJECTED_CORRECTION",
            0.20,
        ),
    ],
)
def test_generic_root_gauge_adapter_uses_explicit_payload_source_branch(
    monkeypatch, source_owner, source_offset_x
) -> None:
    payload = _contact_aware_payload()
    payload["root_pose_gauge_owner"] = source_owner
    source = np.zeros((len(SEGMENTS), 3))
    source[0, 0] = source_offset_x
    payload["root_pose_gauge_correction_rotvec"] = source
    if source_owner != "SHARED_ROOT_BASE_ONLY_PROXY":
        payload["segment_correction_rotvec"] = source.copy()
    covariance_before = payload["covariance_m2"].copy()

    def fake_proxy(_base, correction, _geometry):
        x = float(correction[SEGMENTS[0]][0])
        return {
            "ankle_left": np.array([x, 0.0, 0.0]),
            "ankle_right": np.array([x, 0.0, 0.0]),
        }

    monkeypatch.setattr(runner, "corrected_proxy_points", fake_proxy)
    evidence = {
        side: FootContactEvidence(
            side, 1.0, 0.9, True, 0.0, 0.0, 0.0, "TEST"
        )
        for side in ("left", "right")
    }
    target_offsets = {
        side: np.array([0.05, 0.0, 0.0])
        for side in ("left", "right")
    }
    published = {
        segment: np.zeros(3) for segment in SEGMENTS
    }
    diagnostic = _reexpress_observation_root_to_published_pose(
        payload,
        footholds_world_m={
            "left": np.zeros(3), "right": np.zeros(3)
        },
        published_ankle_offset_world_m=target_offsets,
        published_correction_rotvec=published,
        measurement_evidence=evidence,
        measurement_primary_side="left",
        foothold_corrector=DualFootFootholdCorrector(),
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
    )

    assert diagnostic["source_owner"] == source_owner
    np.testing.assert_allclose(
        payload["position_m"],
        np.array([1.0 + source_offset_x - 0.05, 1.0, 1.0]),
    )
    np.testing.assert_array_equal(payload["covariance_m2"], covariance_before)
    assert diagnostic["covariance_changed"] is False
    assert not diagnostic["contact_manifold"]["compatible"]
    assert diagnostic["contact_manifold"]["threshold_m"] == 0.06
    assert set(
        diagnostic["contact_manifold"]["per_foot_xy_residual_m"]
    ) == {"left", "right"}
    assert payload["root_pose_gauge_owner"] == (
        "MEASUREMENT_TIME_CAUSALLY_PUBLISHED_CORRECTION"
    )


def test_generic_root_gauge_adapter_no_foothold_is_bitwise_identity() -> None:
    payload = _contact_aware_payload()
    before = {
        name: np.asarray(payload[name]).copy()
        for name in (
            "position_m", "covariance_m2",
            "root_pose_gauge_correction_rotvec",
        )
    }
    owner_before = payload["root_pose_gauge_owner"]

    diagnostic = _reexpress_observation_root_to_published_pose(
        payload,
        footholds_world_m={},
        published_ankle_offset_world_m=None,
        published_correction_rotvec=None,
        measurement_evidence=None,
        measurement_primary_side=None,
        foothold_corrector=None,
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
    )

    assert diagnostic["reason"].endswith("BITWISE_IDENTITY")
    assert diagnostic["contact_manifold"]["compatible"]
    for name, value in before.items():
        np.testing.assert_array_equal(payload[name], value)
    assert payload["root_pose_gauge_owner"] == owner_before


def test_no_contact_uwb_loop_seam_is_nominal_and_consumed_exactly_once() -> None:
    payload = _contact_aware_payload()
    position_before = payload["position_m"].copy()
    covariance_before = payload["covariance_m2"].copy()
    diagnostic = _reexpress_observation_root_to_published_pose(
        payload,
        footholds_world_m={},
        published_ankle_offset_world_m=None,
        published_correction_rotvec=None,
        measurement_evidence=None,
        measurement_primary_side=None,
        foothold_corrector=None,
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
    )
    quality = _contact_manifold_quality_state({}, diagnostic)

    class OneCallFilter:
        def __init__(self):
            self.calls = []

        def add_position(self, observation, **kwargs):
            self.calls.append((observation, kwargs))
            return SimpleNamespace(accepted=True, reason="ACCEPTED")

    filter_double = OneCallFilter()
    observation, decision = _add_position_payload_once(
        payload,
        fused_filter=filter_double,
        processing_time_s=payload["availability_time_s"],
        influence_multiplier=1.0,
        quality_state=quality,
    )

    assert len(filter_double.calls) == 1
    assert decision.accepted
    assert observation.quality_state == "NOMINAL_DIAGNOSTIC_RAW_HUBER"
    np.testing.assert_array_equal(payload["position_m"], position_before)
    np.testing.assert_array_equal(payload["covariance_m2"], covariance_before)


def test_generic_root_gauge_adapter_source_equals_destination_has_zero_shift(
    monkeypatch,
) -> None:
    payload = _contact_aware_payload()
    correction = {
        segment: np.asarray(
            payload["root_pose_gauge_correction_rotvec"], dtype=float
        )[index].copy()
        for index, segment in enumerate(SEGMENTS)
    }
    offsets = {
        "left": np.array([0.1, 0.2, -0.7]),
        "right": np.array([-0.1, 0.2, -0.7]),
    }
    monkeypatch.setattr(
        runner,
        "corrected_proxy_points",
        lambda *_args: {
            "ankle_left": offsets["left"].copy(),
            "ankle_right": offsets["right"].copy(),
        },
    )
    evidence = {
        side: FootContactEvidence(
            side, 1.0, 0.9, True, 0.0, 0.0, 0.0, "TEST"
        )
        for side in ("left", "right")
    }
    root_before = payload["position_m"].copy()

    diagnostic = _reexpress_observation_root_to_published_pose(
        payload,
        footholds_world_m={
            "left": np.zeros(3), "right": np.zeros(3)
        },
        published_ankle_offset_world_m=offsets,
        published_correction_rotvec=correction,
        measurement_evidence=evidence,
        measurement_primary_side="left",
        foothold_corrector=DualFootFootholdCorrector(),
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
    )

    np.testing.assert_array_equal(payload["position_m"], root_before)
    np.testing.assert_array_equal(
        diagnostic["applied_position_delta_m"], np.zeros(3)
    )


def test_generic_root_gauge_adapter_accepts_contact_compatible_root(
    monkeypatch,
) -> None:
    payload = _contact_aware_payload()
    offsets = {
        "left": np.array([-0.1, 0.0, -0.9]),
        "right": np.array([0.1, 0.0, -0.9]),
    }
    monkeypatch.setattr(
        runner,
        "corrected_proxy_points",
        lambda *_args: {
            "ankle_left": offsets["left"].copy(),
            "ankle_right": offsets["right"].copy(),
        },
    )
    evidence = {
        side: FootContactEvidence(
            side, 1.0, 0.9, True, 0.0, 0.0, 0.0, "TEST"
        )
        for side in ("left", "right")
    }
    correction = {segment: np.zeros(3) for segment in SEGMENTS}
    footholds = {
        side: payload["position_m"] + offsets[side]
        for side in ("left", "right")
    }

    diagnostic = _reexpress_observation_root_to_published_pose(
        payload,
        footholds_world_m=footholds,
        published_ankle_offset_world_m=offsets,
        published_correction_rotvec=correction,
        measurement_evidence=evidence,
        measurement_primary_side="left",
        foothold_corrector=DualFootFootholdCorrector(),
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
    )

    assert diagnostic["contact_manifold"]["compatible"]
    assert diagnostic["contact_manifold"]["root_xy_residual_m"] < 1e-12
    np.testing.assert_array_equal(
        payload["position_m"], np.array([1.0, 1.0, 1.0])
    )


@pytest.mark.parametrize(
    ("owner", "gauge_value", "target_value", "accepted"),
    [
        ("SHARED_ROOT_BASE_ONLY_PROXY", 0.0, 0.2, True),
        ("SHARED_ROOT_BASE_ONLY_PROXY", 0.1, 0.2, False),
        ("ARTICULATED_RANGE_PROJECTED_CORRECTION", 0.2, 0.2, True),
        ("ARTICULATED_RANGE_PROJECTED_CORRECTION", 0.1, 0.2, False),
        (
            "CONTACT_AWARE_ARTICULATED_RANGE_PROJECTED_CORRECTION",
            0.2, 0.2, True,
        ),
        ("UNKNOWN_OWNER", 0.0, 0.0, False),
    ],
)
def test_root_pose_gauge_source_owner_and_correction_invariant(
    owner, gauge_value, target_value, accepted
) -> None:
    payload = _contact_aware_payload()
    payload["root_pose_gauge_owner"] = owner
    payload["root_pose_gauge_correction_rotvec"] = np.full(
        (len(SEGMENTS), 3), gauge_value
    )
    payload["segment_correction_rotvec"] = np.full(
        (len(SEGMENTS), 3), target_value
    )

    if accepted:
        _validate_root_pose_gauge_source(payload)
    else:
        with pytest.raises(RuntimeError, match="root pose gauge|unknown"):
            _validate_root_pose_gauge_source(payload)


def test_all_uwb_rejected_cannot_false_pass_mechanism_nondegeneracy() -> None:
    decisions = [
        {
            "availability_time_s": 0.12 * index,
            "accepted": False,
            "reason": "REJECT_NIS",
            "measurement_time_foothold_sides": [],
        }
        for index in range(5)
    ]
    gate = _articulated_mechanism_nondegeneracy_gate(
        absolute_time_s=np.arange(100) * 0.005,
        correction_high_rate_rotvec=np.zeros((100, len(SEGMENTS), 3)),
        transition_active=np.zeros(100, dtype=bool),
        pose_install_events=[],
        decisions=decisions,
        uwb_dropout_s=0.36,
        required=True,
    )

    assert not gate["pass"]
    assert not gate["mechanism_qualified"]
    assert not gate["subgates"]["accepted_articulated_pose_install"]
    assert not gate["subgates"]["no_foothold_uwb_dropout_bound"]


def test_nondegeneracy_gate_requires_real_completed_transition_and_contact() -> None:
    times = np.arange(61) * 0.005
    correction = np.zeros((len(times), len(SEGMENTS), 3))
    correction[times >= 0.12, 0, 0] = 0.02
    transition_active = (times > 0.0) & (times < 0.125)
    decisions = [
        {
            "availability_time_s": 0.0,
            "accepted": True,
            "reason": "ACCEPTED",
            "measurement_time_foothold_sides": [],
        },
        {
            "availability_time_s": 0.12,
            "accepted": True,
            "reason": "ACCEPTED",
            "measurement_time_foothold_sides": ["left"],
        },
        {
            "availability_time_s": 0.24,
            "accepted": True,
            "reason": "ACCEPTED",
            "measurement_time_foothold_sides": [],
        },
    ]
    gate = _articulated_mechanism_nondegeneracy_gate(
        absolute_time_s=times,
        correction_high_rate_rotvec=correction,
        transition_active=transition_active,
        pose_install_events=[{
            "measurement_time_s": 0.0,
            "availability_time_s": 0.0,
            "transition_period_s": 0.12,
            "target_delta_maximum_rad": 0.02,
        }],
        decisions=decisions,
        uwb_dropout_s=0.36,
        required=True,
    )

    assert gate["pass"]
    assert gate["completed_nonzero_transition_count"] == 1
    assert gate["accepted_uwb_root_with_owned_foothold_count"] == 1
    assert gate["no_foothold_accepted_uwb_gap"]["pass"]


def test_no_foothold_gap_exempts_contact_owned_interval() -> None:
    audit = _no_foothold_accepted_gap_audit(
        [
            {
                "availability_time_s": 0.0, "accepted": False,
                "measurement_time_foothold_sides": [],
            },
            {
                "availability_time_s": 1.0, "accepted": False,
                "measurement_time_foothold_sides": ["left"],
            },
            {
                "availability_time_s": 2.0, "accepted": False,
                "measurement_time_foothold_sides": [],
            },
        ],
        limit_s=0.36,
    )

    assert audit["pass"]
    assert len(audit["intervals"]) == 2


def test_same_owner_soft_xy_respects_existing_contact_step_cap() -> None:
    reconcile = SimpleNamespace(
        active_sides=("left",),
        constrained_sides=("left",),
        primary_side="left",
    )
    update = SimpleNamespace(
        active_sides=("left",),
        constrained_sides=("left",),
        entered_sides=(),
        released_sides=(),
    )

    audit = _contact_reconcile_step_audit(
        reconcile,
        update,
        primary_side_after_update="left",
        soft_xy_delta_m=0.04,
        soft_velocity_delta_mps=0.12,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
    )

    assert audit["same_owner_identity_checked"]
    assert audit["pass"]

    exceeded = _contact_reconcile_step_audit(
        reconcile,
        update,
        primary_side_after_update="left",
        soft_xy_delta_m=0.0401,
        soft_velocity_delta_mps=0.12,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
    )
    assert exceeded["reason"] == "ORDINARY_CONTACT_POSITION_STEP_EXCEEDED"
    assert not exceeded["pass"]


@pytest.mark.parametrize(
    ("entered", "released", "primary_after"),
    [(("right",), (), "left"), ((), ("left",), "right")],
)
def test_foothold_owner_transition_keeps_real_soft_delta_under_continuity_gate(
    entered, released, primary_after
) -> None:
    reconcile = SimpleNamespace(
        active_sides=("left",),
        constrained_sides=("left",),
        primary_side="left",
    )
    update = SimpleNamespace(
        active_sides=("left", "right") if entered else ("right",),
        constrained_sides=("left", "right") if entered else ("right",),
        entered_sides=entered,
        released_sides=released,
    )

    audit = _contact_reconcile_step_audit(
        reconcile,
        update,
        primary_side_after_update=primary_after,
        soft_xy_delta_m=0.02,
        soft_velocity_delta_mps=0.05,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
    )

    assert audit["ownership_transition"]
    assert not audit["same_owner_identity_checked"]
    assert audit["pass"]
    # The actual displacement remains visible to the existing continuity gate.
    assert audit["ordinary_contact_applied_xy_delta_m"] == 0.02


def test_no_lifecycle_owner_identity_mismatch_fails_closed() -> None:
    reconcile = SimpleNamespace(
        active_sides=("left", "right"),
        constrained_sides=("left",),
        primary_side="left",
    )
    update = SimpleNamespace(
        active_sides=("left", "right"),
        constrained_sides=("left",),
        entered_sides=(),
        released_sides=(),
    )

    stable = _contact_reconcile_step_audit(
        reconcile,
        update,
        primary_side_after_update="left",
        soft_xy_delta_m=0.0,
        soft_velocity_delta_mps=0.0,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
    )
    changed = _contact_reconcile_step_audit(
        reconcile,
        update,
        primary_side_after_update="right",
        soft_xy_delta_m=0.0,
        soft_velocity_delta_mps=0.0,
        maximum_position_step_m=0.04,
        maximum_velocity_step_mps=0.12,
    )

    assert stable["same_owner_identity_checked"] and stable["pass"]
    assert not changed["ownership_transition"]
    assert changed["reason"] == "OWNER_IDENTITY_MISMATCH"
    assert not changed["pass"]


def test_failed_contact_aware_solve_keeps_atomic_range_only_fallback(
    monkeypatch,
) -> None:
    payload = _contact_aware_payload()
    before = {
        name: np.asarray(payload[name]).copy()
        for name in ("position_m", "covariance_m2", "segment_correction_rotvec")
    }
    monkeypatch.setattr(
        runner,
        "solve_articulated_ranges",
        lambda *_args, **_kwargs: SimpleNamespace(
            success=False,
            reason="PROJECTED_FOOTHOLD_GATE_FAILURE",
            pose_observable_rank=0,
            prefit_physical_residual_m=np.empty(0),
            physical_residual_m=np.empty(0),
            maximum_joint_closure_m=np.inf,
        ),
    )
    diagnostic = _apply_measurement_time_footholds_to_observation(
        payload,
        footholds_world_m={"left": np.array([2.0, 3.0, 0.0])},
        anchors=np.zeros((8, 3)),
        rotations_at_fraction=lambda _fraction: _base_at(0.0),
        geometry=SimpleNamespace(),
        hinge_projector=_base_owned_projector,
    )

    assert diagnostic["attempted"] and not diagnostic["accepted"]
    for name, value in before.items():
        np.testing.assert_array_equal(payload[name], value)


def test_prior_held_foothold_is_not_exposed_to_uwb_contact_residual() -> None:
    footholds = {
        "left": np.array([1.0, 2.0, 0.0]),
        "right": np.array([1.1, 2.0, 0.0]),
    }
    evidence = {
        "left": FootContactEvidence(
            "left", 1.0, 0.1, False, 1.0, 1.0, 0.0,
            "SUPPORT_UNCERTAIN_MOTION_ONLY_PRIOR_HELD",
            support_state="UNCERTAIN", prior_held=True,
        ),
        "right": FootContactEvidence(
            "right", 1.0, 0.9, True, 0.1, 0.1, 0.0,
            "CONTACT_HELD", support_state="STANCE_CONFIRMED",
        ),
    }

    selected = _confirmed_measurement_footholds(footholds, evidence)

    assert tuple(selected) == ("right",)
    np.testing.assert_array_equal(selected["right"], footholds["right"])


def test_prior_held_episode_requires_accepted_uwb_before_dropout() -> None:
    times = np.arange(0.0, 1.0, 0.1)
    prior = np.zeros((len(times), 2), dtype=bool)
    prior[1:7, 0] = True
    rejected = [{"availability_time_s": 0.4, "accepted": False}]
    accepted = [{"availability_time_s": 0.4, "accepted": True}]

    failed = _prior_held_uwb_gate(
        times, prior, rejected,
        bootstrap_time_s=0.0, dropout_s=0.36, required=True,
    )
    passed = _prior_held_uwb_gate(
        times, prior, accepted,
        bootstrap_time_s=0.0, dropout_s=0.36, required=True,
    )

    assert failed["reason"] == "PRIOR_STARVED_UWB"
    assert not failed["pass"]
    assert passed["pass"]


def test_stationary_no_flight_prior_is_rejected_for_h01_before_io(tmp_path) -> None:
    with pytest.raises(ValueError, match="restricted to H02_golf"):
        runner.run(
            tmp_path / "must-not-exist",
            tmp_path / "not-read.json",
            action="H01_boxing",
            ankle_contact=True,
            stationary_no_flight_prior=True,
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_raw_swing_cues_classify_bilateral_without_activity_prior() -> None:
    corrector = DualFootFootholdCorrector()
    covariance = np.eye(9) * 0.1
    state = RootState(1.0, np.r_[[0.0, 0.0, 1.0], np.zeros(6)], covariance)
    evidence = {
        side: FootContactEvidence(
            side, 1.0, 0.9, True, 0.1, 0.1, 0.0,
            "CONTACT_HELD", support_state="STANCE_CONFIRMED",
        )
        for side in ("left", "right")
    }
    offsets = {
        "left": np.array([-0.1, 0.0, -1.0]),
        "right": np.array([0.1, 0.0, -1.0]),
    }
    zero_velocity = {side: np.zeros(3) for side in offsets}
    corrector.update(
        state, evidence=evidence,
        ankle_offset_world_m=offsets,
        ankle_offset_velocity_world_mps=zero_velocity,
    )
    raised = RootState(
        1.005, np.r_[[0.0, 0.0, 1.08], np.zeros(6)], covariance
    )

    cues = _positive_swing_cues(
        query_time_s=1.005,
        analytic_ankle_offset_world_m=dict(reversed(tuple(offsets.items()))),
        root_state=raised,
        foothold_corrector=corrector,
        evidence=evidence,
        maximum_root_age_s=0.0075,
        positive_swing_height_m=0.075,
    )

    assert all(cues[side]["positive"] for side in cues)
    assert all(cues[side]["observable"] for side in cues)
    assert all(
        cues[side]["positive_lift_classification"] == "BILATERAL"
        for side in cues
    )
    assert all(
        cues[side]["owner"] == "OWNED_FOOTHOLD_WORLD_Z"
        for side in cues
    )
    assert set(corrector.footholds_world_m()) == {"left", "right"}


def test_raw_swing_cues_classify_unilateral_lift() -> None:
    corrector = DualFootFootholdCorrector()
    covariance = np.eye(9) * 0.1
    state = RootState(1.0, np.r_[[0.0, 0.0, 1.0], np.zeros(6)], covariance)
    evidence = {
        side: FootContactEvidence(
            side, 1.0, 0.9, True, 0.1, 0.1, 0.0,
            "CONTACT_HELD", support_state="STANCE_CONFIRMED",
        )
        for side in ("left", "right")
    }
    initial = {
        "left": np.array([-0.1, 0.0, -1.0]),
        "right": np.array([0.1, 0.0, -1.0]),
    }
    corrector.update(
        state, evidence=evidence,
        ankle_offset_world_m=initial,
        ankle_offset_velocity_world_mps={
            side: np.zeros(3) for side in initial
        },
    )
    asymmetric = {
        "left": initial["left"],
        "right": initial["right"] - np.array([0.0, 0.0, 0.02]),
    }
    raised = RootState(
        1.005, np.r_[[0.0, 0.0, 1.08], np.zeros(6)], covariance
    )

    cues = _positive_swing_cues(
        query_time_s=1.005,
        analytic_ankle_offset_world_m=asymmetric,
        root_state=raised,
        foothold_corrector=corrector,
        evidence=evidence,
        maximum_root_age_s=0.0075,
        positive_swing_height_m=0.075,
    )

    assert cues["left"]["positive"] and cues["left"]["observable"]
    assert not cues["right"]["positive"]
    assert cues["right"]["observable"]
    assert all(
        cues[side]["positive_lift_classification"] == "UNILATERAL_LEFT"
        for side in cues
    )
