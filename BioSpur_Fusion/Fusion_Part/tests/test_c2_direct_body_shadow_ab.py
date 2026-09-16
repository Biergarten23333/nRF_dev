from __future__ import annotations

from dataclasses import replace
import inspect

import numpy as np
import pytest

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    outward_facing_reliability,
)
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    MATERIAL_WEIGHT_FLOOR,
    DirectNative200Clock,
    DirectNodeLinkClock,
    DirectPoseSnapshot,
    DirectShadowEvidence,
    DirectShadowPolicy,
    PoseUnavailableError,
    SegmentShadowDescriptor,
    commit_a_after_paired_results,
    direct_shadow_evidence,
    direct_shadow_evidence_batch,
    direct_shadow_weights_batch,
    prepare_direct_ab_links,
    segment_shadow_exposure,
    shadow_reliability_factors,
    solve_direct_ab,
    summarize_pose_support,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    SharedRootResult,
    solve_shared_root,
)


def _geometry() -> DisplayProxyGeometry:
    return DisplayProxyGeometry(
        torso_height_m=0.50,
        hip_span_m=0.22,
        shoulder_span_m=0.38,
        segment_length_m={
            "upper_arm_left": 0.30,
            "forearm_left": 0.25,
            "upper_arm_right": 0.30,
            "forearm_right": 0.25,
            "thigh_left": 0.43,
            "shank_left": 0.40,
            "thigh_right": 0.43,
            "shank_right": 0.40,
        },
    )


def _joints() -> dict[str, np.ndarray]:
    return {
        "pelvis_center": np.array([0.0, 0.0, 0.9]),
        "shoulder_mid": np.array([0.0, 0.0, 1.4]),
        "shoulder_left": np.array([-0.19, 0.0, 1.4]),
        "shoulder_right": np.array([0.19, 0.0, 1.4]),
        "hip_left": np.array([-0.11, 0.0, 0.9]),
        "hip_right": np.array([0.11, 0.0, 0.9]),
        "elbow_left": np.array([-0.49, 0.0, 1.4]),
        "elbow_right": np.array([0.49, 0.0, 1.4]),
        "wrist_left": np.array([-0.74, 0.0, 1.4]),
        "wrist_right": np.array([0.74, 0.0, 1.4]),
        "knee_left": np.array([-0.11, 0.0, 0.47]),
        "knee_right": np.array([0.11, 0.0, 0.47]),
        "ankle_left": np.array([-0.11, 0.0, 0.07]),
        "ankle_right": np.array([0.11, 0.0, 0.07]),
    }


def _snapshot(*, query_ns: float = 1_004_000_000.0) -> DirectPoseSnapshot:
    joints = _joints()
    offsets = {
        "BSF31CC": joints["shoulder_mid"],
        "BSFC2CC": joints["pelvis_center"],
        "BSFAA61": joints["elbow_left"],
        "BSF1120": joints["elbow_right"],
        "BSFEC35": joints["wrist_left"],
        "BSFB165": joints["wrist_right"],
        "BSF44AD": joints["knee_left"],
        "BSF3C79": joints["knee_right"],
        "BSF6C53": joints["ankle_left"],
        "BSF8BC4": joints["ankle_right"],
    }
    normals = {node: np.array([1.0, 0.0, 0.0]) for node in offsets}
    return DirectPoseSnapshot(
        action="04_shoulder_left",
        frame=0,
        pose_global_ns=1_000_000_000,
        query_global_ns=query_ns,
        pose_age_ns=query_ns - 1_000_000_000,
        root_world_m=np.zeros(3),
        offsets_world_m=offsets,
        normals_world=normals,
        joints_relative_world_m=joints,
    )


def _anchors() -> np.ndarray:
    return np.array([
        [-3.0, -3.0, 0.2], [3.0, -3.0, 0.3],
        [-3.0, 3.0, 0.8], [3.0, 3.0, 1.2],
        [-2.0, 0.0, 2.4], [2.0, 0.0, 2.6],
        [0.0, -2.5, 2.0], [0.0, 2.5, 2.2],
    ])


def _links(node: str = "BSFEC35") -> tuple[SharedRangeLink, ...]:
    offset = _snapshot().offsets_world_m[node]
    return tuple(
        SharedRangeLink(
            node=node,
            anchor=anchor,
            range_m=float(np.linalg.norm(_anchors()[anchor] - offset)),
            tag_offset_world_m=offset,
            link_dt_s=anchor * 1e-4,
            sigma_m=0.08,
            facing_score=0.2,
        )
        for anchor in range(8)
    )


def _evidence(node: str = "BSFEC35") -> dict[tuple[str, int], DirectShadowEvidence]:
    return {
        (node, anchor): direct_shadow_evidence(
            node=node,
            anchor_position_world_m=_anchors()[anchor],
            snapshot=_snapshot(),
            geometry=_geometry(),
        )
        for anchor in range(8)
    }


@pytest.mark.parametrize("root_delta",(np.zeros(3),np.array([.31,-.22,.17])))
def test_batch_shadow_evidence_matches_scalar_all_nodes_and_changed_pose(root_delta):
    base=_snapshot();joints={key:value+np.array([0.,0.,.03]) for key,value in base.joints_relative_world_m.items()}
    snapshot=DirectPoseSnapshot(base.action,base.frame+int(np.any(root_delta)),base.pose_global_ns,base.query_global_ns,
        base.pose_age_ns,root_delta,base.offsets_world_m,base.normals_world,joints)
    maximum=0.0
    for node in base.offsets_world_m:
        scalar=tuple(direct_shadow_evidence(node=node,anchor_position_world_m=anchor,snapshot=snapshot,geometry=_geometry()) for anchor in _anchors())
        batch=direct_shadow_evidence_batch(node=node,anchor_positions_world_m=_anchors(),snapshot=snapshot,geometry=_geometry())
        weights=direct_shadow_weights_batch(node=node,anchor_positions_world_m=_anchors(),snapshot=snapshot,geometry=_geometry())
        assert np.array_equal(weights,np.asarray([row.b_combined_weight for row in batch]))
        for left,right in zip(scalar,batch):
            assert (left.node,left.incident_segments_excluded,left.near_field_ambiguous_segments)==(right.node,right.incident_segments_excluded,right.near_field_ambiguous_segments)
            maximum=max(maximum,max(abs(getattr(left,name)-getattr(right,name)) for name in ("own_facing_score","torso_severity","limb_severity","a_large_weight","b_limb_factor","b_combined_weight")))
            assert tuple((x.name,x.family,x.near_field_ambiguous) for x in left.segments)==tuple((x.name,x.family,x.near_field_ambiguous) for x in right.segments)
            for x,y in zip(left.segments,right.segments):
                maximum=max(maximum,max(abs(getattr(x,name)-getattr(y,name)) for name in ("normalized_clearance","normalized_chord_depth","chord_length_m","ray_fraction","exposure")))
    assert maximum<=1e-12


def test_policy_bounds_and_facing_monotonicity_are_exact() -> None:
    policy = DirectShadowPolicy()
    assert policy.torso_strength == 0.8
    assert policy.limb_strength == 0.5
    assert policy.maximum_nfev == 75
    assert (0.25 * (1.0 - policy.torso_strength)) == pytest.approx(0.05)
    assert 0.75 * 1.0 == pytest.approx(0.75)
    snapshot = _snapshot()
    inward = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=np.array([-3.0, 0.0, 1.4]),
        snapshot=snapshot, geometry=_geometry(),
    )
    outward = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=np.array([3.0, 0.0, 1.4]),
        snapshot=snapshot, geometry=_geometry(),
    )
    assert inward.own_facing_score < outward.own_facing_score
    # Facing monotonicity is conditional on the same torso severity; the two
    # real rays above deliberately have different body intersections.
    fixed_torso_factor = 1.0 - policy.torso_strength * 0.4
    assert (
        outward_facing_reliability(-1.0) * fixed_torso_factor
        < outward_facing_reliability(0.0) * fixed_torso_factor
        < outward_facing_reliability(1.0) * fixed_torso_factor
    )
    for row in (inward, outward):
        assert 0.05 <= row.a_large_weight <= 0.75
        assert 0.5 <= row.b_limb_factor <= 1.0
        assert 0.025 <= row.b_combined_weight <= 0.75


def test_clearance_chord_and_union_are_continuous_bounded_and_ordered() -> None:
    snapshot = _snapshot()
    central = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=np.array([3.0, 0.0, 1.4]),
        snapshot=snapshot, geometry=_geometry(),
    )
    grazing = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=np.array([3.0, 0.65, 1.4]),
        snapshot=snapshot, geometry=_geometry(),
    )
    assert 0.0 <= central.torso_severity <= 1.0
    assert 0.0 <= central.limb_severity <= 1.0
    assert 0.0 <= grazing.limb_severity <= 1.0
    assert central.limb_severity > grazing.limb_severity
    central_depth = max(row.normalized_chord_depth for row in central.segments)
    grazing_depth = max(row.normalized_chord_depth for row in grazing.segments)
    assert central_depth >= grazing_depth
    # Segment ordering is canonical and union is non-additive/bounded.
    assert [row.name for row in central.segments] == sorted(
        row.name for row in central.segments
    )
    assert central.limb_severity <= sum(
        row.exposure for row in central.segments if row.family == "limb"
    ) + 1e-15


def test_canonical_equal_facing_shadow_factors_are_monotone_and_bounded() -> None:
    exposures = {
        name: segment_shadow_exposure(clearance, 1.0)[0]
        for name, clearance in (("central", 0.0), ("grazing", 1.0), ("clear", 3.0))
    }
    assert exposures["central"] > exposures["grazing"] > exposures["clear"]
    factors = {
        name: shadow_reliability_factors(
            torso_severity=exposure, limb_severity=exposure
        )
        for name, exposure in exposures.items()
    }
    torso = {name: value[0] for name, value in factors.items()}
    limb = {name: value[1] for name, value in factors.items()}
    assert torso["central"] <= 0.25
    assert torso["grazing"] >= 0.70
    assert torso["clear"] >= 0.99
    assert 0.50 <= limb["central"] <= 0.60
    assert limb["grazing"] >= 0.80
    assert limb["clear"] >= 0.99
    assert torso["central"] < torso["grazing"] < torso["clear"]
    assert limb["central"] < limb["grazing"] < limb["clear"]


@pytest.mark.parametrize(
    ("node", "expected"),
    [
        ("BSFAA61", {"upper_arm_left", "forearm_left"}),
        ("BSF1120", {"upper_arm_right", "forearm_right"}),
        ("BSF44AD", {"thigh_left", "shank_left"}),
        ("BSF3C79", {"thigh_right", "shank_right"}),
        ("BSFEC35", {"forearm_left"}),
        ("BSF6C53", {"shank_left"}),
    ],
)
def test_emitting_incident_segments_are_excluded(node: str, expected: set[str]) -> None:
    evidence = direct_shadow_evidence(
        node=node,
        anchor_position_world_m=np.array([3.0, 0.0, 1.5]),
        snapshot=_snapshot(),
        geometry=_geometry(),
    )
    assert set(evidence.incident_segments_excluded) == expected
    assert expected.isdisjoint({row.name for row in evidence.segments})
    assert all("hand" not in row.name and "foot" not in row.name for row in evidence.segments)


def test_feature_owner_accepts_no_range_and_is_past_only() -> None:
    names = tuple(inspect.signature(direct_shadow_evidence).parameters)
    assert "range_m" not in names
    fixed = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=_anchors()[0],
        snapshot=_snapshot(), geometry=_geometry(),
    )
    # A future snapshot can be arbitrarily different without mutating fixed evidence.
    future = _snapshot(query_ns=1_009_000_000.0)
    altered_joints = dict(future.joints_relative_world_m)
    altered_joints["elbow_right"] = np.array([99.0, 99.0, 99.0])
    altered_future = replace(future, joints_relative_world_m=altered_joints)
    assert not np.array_equal(
        future.joints_relative_world_m["elbow_right"],
        altered_future.joints_relative_world_m["elbow_right"],
    )
    repeated = direct_shadow_evidence(
        node="BSFEC35", anchor_position_world_m=_anchors()[0],
        snapshot=_snapshot(), geometry=_geometry(),
    )
    assert fixed == repeated
    with pytest.raises(ValueError, match="strictly precede"):
        _snapshot(query_ns=1_000_000_000.0)


def test_strict_native_clock_and_per_link_half_round_ownership() -> None:
    timer = np.array([100_000, 105_000, 110_000], dtype=np.int64)
    owner = DirectNative200Clock(
        action="04_shoulder_left",
        time_root_s=timer.astype(float) * 1e-6,
        source_pelvis_timer_us=timer,
        source_contiguous_span_id=np.zeros(3, dtype=np.int64),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=50.0,
        valid_mask=np.ones(3, dtype=bool),
    )
    assert owner.strict_floor(105_000_051.0).frame == 1
    # Exact-time queries deliberately floor to the previous published sample.
    assert owner.strict_floor(105_000_050.0).frame == 0
    link_clock = DirectNodeLinkClock(
        node="BSFEC35", a_ns_per_us=999.5, b_ns=123.0, boot_epoch=7,
        first_timer_us=10_000, last_timer_us=20_000,
    )
    assert link_clock.link_time_ns(
        event_boot_epoch=7, strobe_us=12_000, t_round_us=800.0
    ) == pytest.approx(999.5 * 12_400.0 + 123.0)
    with pytest.raises(ValueError, match="boot"):
        link_clock.link_time_ns(
            event_boot_epoch=8, strobe_us=12_000, t_round_us=800.0
        )
    with pytest.raises(PoseUnavailableError) as stale:
        owner.strict_floor(116_000_051.0)
    assert stale.value.audit.reason == "STALE_VALID_POSE"
    assert stale.value.audit.selected_frame == 2
    assert stale.value.audit.selected_timer_us == 110_000
    assert stale.value.audit.selected_span_id == 0
    assert stale.value.audit.signed_age_ns == pytest.approx(6_000_001.0)
    with pytest.raises(PoseUnavailableError) as absent:
        owner.strict_floor(100_000_000.0)
    assert absent.value.audit.reason == "NO_STRICTLY_PRECEDING_VALID_POSE"
    assert absent.value.audit.selected_frame is None


def test_pose_support_requires_terminal_suffix_fraction_and_count() -> None:
    passed = summarize_pose_support(["FRESH"] * 100 + ["POSE_UNAVAILABLE"])
    assert passed["pass"] and passed["terminal_unavailable_suffix"]
    assert passed["fresh_fraction"] == pytest.approx(100 / 101)
    assert not summarize_pose_support(["FRESH"] * 99)["pass"]
    assert not summarize_pose_support(
        ["FRESH"] * 100 + ["POSE_UNAVAILABLE", "FRESH"]
    )["pass"]


def test_all_positive_links_are_retained_and_branches_differ_only_in_weights() -> None:
    links = _links()
    prepared = prepare_direct_ab_links(
        links,
        evidence_by_identity=_evidence(),
        anchors_m=_anchors(),
        initial_root_m=np.zeros(3),
        root_velocity_mps=np.array([0.1, -0.2, 0.05]),
    )
    assert len(prepared.a_links) == len(prepared.b_links) == len(links) == 8
    assert prepared.forced_retained_identities == ()
    assert prepared.geometry.rank == 3
    assert np.isfinite(prepared.geometry.condition)
    assert prepared.a_material.weight_floor == MATERIAL_WEIGHT_FLOOR
    assert prepared.b_material.weight_floor == MATERIAL_WEIGHT_FLOOR
    assert prepared.a_material.material_count >= 4
    assert prepared.b_material.material_count >= 4
    assert prepared.a_material.support_prefix_rank == 3
    assert prepared.b_material.support_prefix_rank == 3
    assert prepared.a_material.ordered_identities == tuple(sorted(
        prepared.a_material.ordered_identities,
        key=lambda identity: (
            -prepared.a_material.ordered_weights[
                prepared.a_material.ordered_identities.index(identity)
            ],
            identity[1],
        ),
    ))
    for raw, a_link, b_link in zip(links, prepared.a_links, prepared.b_links, strict=True):
        assert (raw.node, raw.anchor) == (a_link.node, a_link.anchor) == (
            b_link.node, b_link.anchor
        )
        assert raw.range_m == a_link.range_m == b_link.range_m
        assert raw.sigma_m == a_link.sigma_m == b_link.sigma_m
        assert raw.link_dt_s == a_link.link_dt_s == b_link.link_dt_s
        np.testing.assert_array_equal(raw.tag_offset_world_m, a_link.tag_offset_world_m)
        np.testing.assert_array_equal(raw.tag_offset_world_m, b_link.tag_offset_world_m)
        for field in SharedRangeLink.__dataclass_fields__:
            if field == "information_weight":
                continue
            left = getattr(a_link, field)
            right = getattr(b_link, field)
            if isinstance(left, np.ndarray):
                np.testing.assert_array_equal(left, right)
            else:
                assert left == right
        assert 0.0 < b_link.information_weight <= a_link.information_weight <= 1.0


def test_material_floor_never_inflates_low_reliability_links() -> None:
    evidence = _evidence()
    low = {
        identity: replace(
            row, a_large_weight=0.04, b_limb_factor=0.5, b_combined_weight=0.02
        )
        for identity, row in evidence.items()
    }
    with pytest.raises(ValueError, match="material floor"):
        prepare_direct_ab_links(
            _links(), evidence_by_identity=low, anchors_m=_anchors(),
            initial_root_m=np.zeros(3), root_velocity_mps=np.zeros(3),
        )


def test_arbitrary_current_range_mutation_cannot_change_ab_weights() -> None:
    links = _links()
    evidence = _evidence()
    common = dict(
        evidence_by_identity=evidence,
        anchors_m=_anchors(),
        initial_root_m=np.zeros(3),
        root_velocity_mps=np.array([0.1, -0.2, 0.05]),
    )
    before = prepare_direct_ab_links(links, **common)
    mutated = tuple(
        replace(link, range_m=999.0 + 17.0 * index)
        for index, link in enumerate(links)
    )
    after = prepare_direct_ab_links(mutated, **common)
    np.testing.assert_array_equal(
        [link.information_weight for link in before.a_links],
        [link.information_weight for link in after.a_links],
    )
    np.testing.assert_array_equal(
        [link.information_weight for link in before.b_links],
        [link.information_weight for link in after.b_links],
    )
    assert before.evidence == after.evidence


def test_solve_owner_calls_same_solver_exactly_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    import biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab as owner

    calls: list[tuple[tuple[SharedRangeLink, ...], dict[str, object]]] = []

    def capture(links, **kwargs):
        calls.append((tuple(links), kwargs))
        return SharedRootResult(
            root_position_m=np.zeros(3), success=True, reason="ACCEPTED",
            residuals_m=np.zeros(len(links)), standardized_residuals=np.zeros(len(links)),
            anchors_used=tuple(range(8)), nodes_used=("BSFEC35",), rank=3,
            condition=2.0, nfev=1, cost=0.0,
        )

    monkeypatch.setattr(owner, "solve_shared_root", capture)
    result = solve_direct_ab(
        _links(), evidence_by_identity=_evidence(), anchors_m=_anchors(),
        initial_root_m=np.zeros(3), root_velocity_mps=np.zeros(3),
    )
    assert result.a_result.success and result.b_result.success
    assert len(calls) == 2
    assert calls[0][1]["maximum_nfev"] == calls[1][1]["maximum_nfev"] == 75
    assert calls[0][1].keys() == calls[1][1].keys()
    for key in calls[0][1]:
        np.testing.assert_array_equal(calls[0][1][key], calls[1][1][key])
    for a_link, b_link in zip(calls[0][0], calls[1][0], strict=True):
        assert a_link.range_m == b_link.range_m
        assert a_link.sigma_m == b_link.sigma_m
        assert a_link.link_dt_s == b_link.link_dt_s
        np.testing.assert_array_equal(a_link.tag_offset_world_m, b_link.tag_offset_world_m)
        assert b_link.information_weight <= a_link.information_weight


def test_only_a_commits_after_both_results_are_frozen() -> None:
    prepared = prepare_direct_ab_links(
        _links(), evidence_by_identity=_evidence(), anchors_m=_anchors(),
        initial_root_m=np.zeros(3), root_velocity_mps=np.zeros(3),
    )
    accepted = SharedRootResult(
        root_position_m=np.array([1.0, 2.0, 3.0]), success=True,
        reason="ACCEPTED", residuals_m=np.zeros(8), standardized_residuals=np.zeros(8),
        anchors_used=tuple(range(8)), nodes_used=("BSFEC35",), rank=3,
        condition=2.0, nfev=1, cost=0.0,
    )
    b = replace(accepted, root_position_m=np.array([9.0, 9.0, 9.0]))
    from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectABResult
    paired = DirectABResult(prepared=prepared, a_result=accepted, b_result=b)
    committed: list[np.ndarray] = []
    commit_a_after_paired_results(paired, committed.append)
    assert len(committed) == 1
    np.testing.assert_array_equal(committed[0], accepted.root_position_m)
    failed = replace(b, success=False, reason="OPTIMIZER_FAILURE")
    with pytest.raises(RuntimeError, match="paired solver failure"):
        commit_a_after_paired_results(
            replace(paired, b_result=failed), committed.append
        )
    assert len(committed) == 1


def test_duplicate_and_geometry_failures_block_both_branches_before_solver() -> None:
    links = _links()
    with pytest.raises(ValueError, match="duplicate"):
        prepare_direct_ab_links(
            (*links[:4], links[0]),
            evidence_by_identity={key: value for key, value in list(_evidence().items())[:4]},
            anchors_m=_anchors(), initial_root_m=np.zeros(3),
            root_velocity_mps=np.zeros(3),
        )
    with pytest.raises(ValueError, match="at least four"):
        prepare_direct_ab_links(
            links[:3],
            evidence_by_identity={
                (link.node, link.anchor): _evidence()[(link.node, link.anchor)]
                for link in links[:3]
            },
            anchors_m=_anchors(), initial_root_m=np.zeros(3),
            root_velocity_mps=np.zeros(3),
        )


def test_policy_rejects_nonpositive_or_unbounded_controls() -> None:
    with pytest.raises(ValueError):
        DirectShadowPolicy(torso_strength=1.0)
    with pytest.raises(ValueError):
        DirectShadowPolicy(limb_strength=-0.1)
    with pytest.raises(ValueError):
        DirectShadowPolicy(near_field_scale_m=0.0)
    with pytest.raises(ValueError):
        DirectShadowPolicy(maximum_nfev=0)
    with pytest.raises(ValueError):
        DirectShadowPolicy(maximum_nfev=1.5)
    with pytest.raises(ValueError):
        DirectShadowPolicy(maximum_nfev=True)
    assert inspect.signature(solve_shared_root).parameters["maximum_nfev"].default == 50
