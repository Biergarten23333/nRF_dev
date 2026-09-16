from dataclasses import replace
from functools import partial
import hashlib
import pickle
import math
import time
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.root_r3.models import ImuSample

from biospur_fusion.c2_articulated_biomechanics.model import HINGE_SPECS, HingeJoint
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    evaluate_hinge_projection_batch,
)
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    ObsoleteNative200SourcePair,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS, corrected_proxy_points
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    AuthoritativeArticulatedFusion,
)
from biospur_fusion.c2_uwb_root_world import authoritative_articulated_fusion as integration
from biospur_fusion.c2_uwb_root_world import owner_bound_async_worker as robust_runtime
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    BoundGroupPacket,
    _prepare_dynamic_owner,
)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectNative200Clock,
    PoseUnavailableError,
)
from test_c2_owner_bound_async_worker import sequence


def _model():
    return {
        name: HingeJoint(
            name, parent, child, action, (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0,
            minimum, maximum, 1, 1,
        )
        for name, (parent, child, action, minimum, maximum) in HINGE_SPECS.items()
    }


def _engine_and_packet(
    nominal_displacement=None, clock_owner_sha256=None,
    hinge_temporal_retention_contract=None,
):
    if clock_owner_sha256 is None:
        clock_owner_sha256 = "a" * 64
    owner, items = sequence()
    original_packet = next(
        item for item in items if isinstance(item, BoundGroupPacket)
    )
    packet = original_packet
    if nominal_displacement is not None:
        owner = replace(
            owner,
            nominal_envelope=replace(
                owner.nominal_envelope,
                maximum_root_displacement_m=nominal_displacement,
                maximum_root_speed_change_mps=nominal_displacement,
                maximum_root_implied_acceleration_mps2=nominal_displacement,
            ),
            digest="",
        )
        packet = BoundGroupPacket(
            owner.digest, packet.event, packet.pose_links,
            packet.information_weights, packet.a_sigma_owner,
            packet.b_sigma_owner, packet.b_shadow_owner,
            packet.a_weight_policy,
            availability_global_ns=packet.availability_global_ns,
        )
    geometry = packet.b_shadow_owner.geometry
    projector = partial(project_hinge_corrections, model=_model())
    from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
    pose_kwargs = {} if hinge_temporal_retention_contract is None else {
        "hinge_temporal_retention_contract": hinge_temporal_retention_contract,
    }
    pose = CausalArticulatedPose(
        action_start_s=0.0, action_stop_s=1.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=geometry, hinge_projector=projector,
        **pose_kwargs,
    )
    pose.sample(0.0)
    pose.sample(0.005)
    pose.sample(0.010)
    engine = AuthoritativeArticulatedFusion(
        static_owner=owner, pose=pose,
        native200_clock_owner_sha256=clock_owner_sha256,
        native200_base_pose_owner_digest="f" * 64,
    )
    for item in items:
        if item is original_packet:
            break
        if isinstance(item, RootWorkerEvent):
            engine.add_imu(item.payload)
    return engine, _independent_zero_pose_packet(engine, packet)


def _independent_zero_pose_packet(engine, packet):
    """Create current UWB ranges from the declared zero-pose trajectory only."""
    _epochs, measurement_ns, _availability_ns = group_epoch_times_ns(
        packet.event.payload, clocks=engine.static.clocks
    )
    token = engine.root.publication_token()
    velocity = token.state.vector[3:6]
    root_at_measurement = (
        token.state.vector[:3]
        + (measurement_ns * 1e-9 - token.time_s) * velocity
    )
    poses = {(row.node, row.anchor): row for row in packet.pose_links}
    rows = []
    for row in packet.event.payload:
        ranges = list(row.ranges_mm)
        for anchor in range(8):
            if not row.valid_mask & (1 << anchor):
                continue
            pose = poses[(row.node, anchor)]
            dt = (pose.query_time_ns - measurement_ns) * 1e-9
            tag = root_at_measurement + pose.offset_world_m + dt * velocity
            distance = float(np.linalg.norm(tag - engine.static.anchors_m[anchor]))
            ranges[anchor] = int(round(1000.0 * (
                distance + engine.static.anchor_delay_m[anchor]
                + engine.static.tag_delay_m
            )))
        rows.append(replace(row, ranges_mm=tuple(ranges)))
    event = replace(packet.event, payload=tuple(rows))
    return BoundGroupPacket(
        packet.static_owner_digest, event, packet.pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        packet.b_shadow_owner, packet.a_weight_policy,
        availability_global_ns=packet.availability_global_ns,
    )


def _packet_with_nodes(packet, count):
    indices = (
        (0, 1, 2, 5) if count == 4 else
        (0, 1, 4, 5, 7, 8, 9) if count == 7 else
        tuple(range(count))
    )
    keep = {packet.event.payload[index].node for index in indices}
    rows = tuple(
        row if row.node in keep else replace(row, valid_mask=0)
        for row in packet.event.payload
    )
    event = replace(packet.event, payload=rows)
    return BoundGroupPacket(
        packet.static_owner_digest, event, packet.pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        packet.b_shadow_owner, packet.a_weight_policy,
        availability_global_ns=packet.availability_global_ns,
    )


def _packet_with_exact_row_subset(packet, count):
    rows = tuple(packet.event.payload[:count])
    nodes = {row.node for row in rows}
    pose_links = tuple(row for row in packet.pose_links if row.node in nodes)
    shadow = replace(
        packet.b_shadow_owner,
        snapshots=tuple(
            row for row in packet.b_shadow_owner.snapshots if row.node in nodes
        ),
        digest="",
    )
    return BoundGroupPacket(
        packet.static_owner_digest, replace(packet.event, payload=rows), pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        shadow, packet.a_weight_policy,
        availability_global_ns=packet.availability_global_ns,
    )


def _later_zero_pose_packet(engine, packet, *, delta_us=120_000):
    delta_ns = 1_000 * delta_us
    rows = tuple(replace(
        row, sequence=row.sequence + 1, sweep=row.sweep + 1,
        strobe_us=row.strobe_us + delta_us,
        frame_us=row.frame_us + delta_us,
        node_ms=row.node_ms + delta_us // 1_000,
    ) for row in packet.event.payload)
    def node_delta_ns(node):
        return engine.static.clocks[node].a_ns_per_us * delta_us

    pose_links = tuple(replace(
        link, query_time_ns=link.query_time_ns + node_delta_ns(link.node),
        pose_time_ns=link.pose_time_ns + int(round(node_delta_ns(link.node))),
        source_epoch=link.source_epoch + 1,
        source_revision=link.source_revision + 1,
    ) for link in packet.pose_links)
    shadow = replace(
        packet.b_shadow_owner,
        snapshots=tuple(replace(
            snapshot, frame=snapshot.frame + delta_us // 5_000,
            pose_global_ns=(snapshot.pose_global_ns
                            + int(round(node_delta_ns(snapshot.node)))),
            query_global_ns=(snapshot.query_global_ns
                             + node_delta_ns(snapshot.node)),
        ) for snapshot in packet.b_shadow_owner.snapshots),
        digest="",
    )
    availability_ns = packet.availability_global_ns + delta_ns + 1_000_000
    event = replace(
        packet.event, sequence=packet.event.sequence + 1,
        availability_time_s=availability_ns * 1e-9, payload=rows,
    )
    later = BoundGroupPacket(
        packet.static_owner_digest, event, pose_links,
        packet.information_weights, packet.a_sigma_owner, packet.b_sigma_owner,
        shadow, packet.a_weight_policy,
        availability_global_ns=availability_ns,
    )
    return _independent_zero_pose_packet(engine, later)


@pytest.mark.parametrize("count", (1, 4, 7, 10))
def test_runtime_partial_packet_preserves_adaptive_x_of_ten_partition(count):
    engine, packet = _engine_and_packet()
    packet = _packet_with_exact_row_subset(packet, count)
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    assert plan.source_rows == count
    assert set(plan.factor_nodes) == {row.node for row in packet.event.payload}
    assert set(plan.selection.trusted_nodes) <= set(plan.factor_nodes)
    assert len(packet.pose_links) == 8 * count
    assert len(plan.selection.trusted_nodes) <= count


def test_unsuccessful_robust_candidate_is_typed_inert_and_later_valid_recovers(
    monkeypatch,
):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = robust_runtime.solve_shared_root

    def rejected_candidate(*args, **kwargs):
        return replace(
            actual(*args, **kwargs), success=False, reason="OPTIMIZER_FAILURE",
        )

    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    monkeypatch.setattr(robust_runtime, "solve_shared_root", rejected_candidate)
    rejected = engine.prepare_admission(packet, epoch)
    diagnostic = rejected.robust_candidate_rejection_diagnostic
    assert rejected.causal_transaction is None
    assert rejected.prepared_result.accepted is False
    assert rejected.prepared_result.reason == "OPTIMIZER_FAILURE"
    assert diagnostic is rejected.prepared_result.robust_candidate_rejection_diagnostic
    assert diagnostic.solver_reason == "OPTIMIZER_FAILURE"
    assert diagnostic.solver_success is False
    assert diagnostic.source_rows == 10
    assert diagnostic.packet_digest == packet.digest
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )

    monkeypatch.setattr(robust_runtime, "solve_shared_root", actual)
    accepted = engine.prepare_admission(packet, epoch)
    assert accepted.causal_transaction is not None
    assert accepted.robust_candidate_rejection_diagnostic is None
    engine.commit_admission(accepted)
    assert engine.robust.revision == 1


def test_empty_trusted_selection_is_typed_inert_rejection(monkeypatch):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = robust_runtime.select_trusted_body_nodes

    def empty_selection(*args, **kwargs):
        return replace(
            actual(*args, **kwargs), trusted_nodes=(), trusted_links=(),
        )

    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    monkeypatch.setattr(
        robust_runtime, "select_trusted_body_nodes", empty_selection,
    )
    rejected = engine.admit(packet, epoch)
    diagnostic = rejected.robust_candidate_rejection_diagnostic

    assert rejected.accepted is False
    assert rejected.reason == "NO_TRUSTED_NODE"
    assert rejected.transaction is None
    assert rejected.trusted_nodes == ()
    assert diagnostic.solver_reason == "NO_TRUSTED_NODE"
    assert diagnostic.solver_success is False
    assert diagnostic.rank == 0
    assert diagnostic.nfev == 0
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )


def test_valid_group_commits_after_empty_trusted_selection_rejection(monkeypatch):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    later_packet = _later_zero_pose_packet(engine, packet)
    actual = robust_runtime.select_trusted_body_nodes
    calls = 0

    def reject_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        selected = actual(*args, **kwargs)
        if calls == 1:
            return replace(selected, trusted_nodes=(), trusted_links=())
        return selected

    monkeypatch.setattr(
        robust_runtime, "select_trusted_body_nodes", reject_once,
    )
    committed_before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    rejected = engine.admit(packet, epoch)
    assert committed_before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    later_epoch = _epoch(engine, later_packet, continuous_history=False)
    accepted = engine.admit(later_packet, later_epoch)

    assert rejected.accepted is False
    assert rejected.reason == "NO_TRUSTED_NODE"
    assert later_packet.event.sequence > packet.event.sequence
    assert later_epoch.measurement_time_s > epoch.measurement_time_s
    assert later_epoch.availability_time_s > epoch.availability_time_s
    assert accepted.accepted is True
    assert accepted.reason == "ACCEPTED"
    assert engine.robust.revision == 1


def test_fractional_nanosecond_robust_rejection_binds_exact_measurement_without_mutation(
    monkeypatch,
):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual_build = robust_runtime.build_causal_links
    actual_solve = robust_runtime.solve_shared_root
    _, _, original_measurement_s, _ = actual_build(
        tuple(packet.event.payload), clocks=engine.static.clocks,
        strict_floor_offset=engine.static.pose,
        anchor_delay_m=engine.static.anchor_delay_m,
        tag_delay_m=engine.static.tag_delay_m,
        sigma_for_quality=packet.b_sigma_owner.sigma,
    )
    measurement_s = original_measurement_s + 0.375e-9
    assert measurement_s != int(round(measurement_s * 1e9)) * 1e-9
    epoch = replace(epoch, measurement_time_s=measurement_s)

    def fractional_links(*args, **kwargs):
        links, audits, _measurement_s, availability_s = actual_build(*args, **kwargs)
        return links, audits, measurement_s, availability_s

    def rejected_candidate(*args, **kwargs):
        return replace(
            actual_solve(*args, **kwargs),
            success=False, reason="FRACTIONAL_NS_REJECTION",
        )

    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    monkeypatch.setattr(robust_runtime, "build_causal_links", fractional_links)
    monkeypatch.setattr(robust_runtime, "solve_shared_root", rejected_candidate)
    prepared = engine.prepare_admission(packet, epoch)
    diagnostic = prepared.robust_candidate_rejection_diagnostic
    assert prepared.causal_transaction is None
    assert prepared.prepared_result.reason == "FRACTIONAL_NS_REJECTION"
    assert diagnostic.measurement_s == measurement_s
    assert diagnostic.measurement_time_ns == round(measurement_s * 1e9)
    with pytest.raises(
        ValueError, match="invalid robust-candidate measurement rejection",
    ):
        replace(diagnostic, measurement_time_ns=diagnostic.measurement_time_ns + 1)
    with pytest.raises(
        ValueError, match="invalid robust-candidate measurement rejection",
    ):
        replace(diagnostic, measurement_s=float("nan"))
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )


@pytest.mark.parametrize(
    ("rank", "condition", "expected_condition", "expected_reason"),
    (
        (2, 12.0, 12.0, "ROBUST_RANK_INVALID"),
        (3, math.nan, "NAN", "ROBUST_CONDITION_NONFINITE"),
        (3, math.inf, "POSITIVE_INFINITY", "ROBUST_CONDITION_NONFINITE"),
        (3, -math.inf, "NEGATIVE_INFINITY", "ROBUST_CONDITION_NONFINITE"),
    ),
)
def test_successful_but_numerically_unusable_robust_candidate_is_typed(
    monkeypatch, rank, condition, expected_condition, expected_reason,
):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = robust_runtime.solve_shared_root

    def unusable(*args, **kwargs):
        return replace(
            actual(*args, **kwargs), success=True, reason="ACCEPTED",
            rank=rank, condition=condition,
        )

    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )
    monkeypatch.setattr(robust_runtime, "solve_shared_root", unusable)
    prepared = engine.prepare_admission(packet, epoch)
    diagnostic = prepared.robust_candidate_rejection_diagnostic
    assert prepared.causal_transaction is None
    assert prepared.prepared_result.accepted is False
    assert prepared.prepared_result.reason == expected_reason
    assert diagnostic.solver_success is True
    assert diagnostic.solver_reason == "ACCEPTED"
    assert diagnostic.rank == rank
    assert diagnostic.condition == expected_condition
    if isinstance(expected_condition, str):
        with pytest.raises(
            ValueError, match="invalid robust-candidate measurement rejection",
        ):
            replace(diagnostic, condition=condition)
    with pytest.raises(
        ValueError, match="invalid robust-candidate measurement rejection",
    ):
        replace(diagnostic, condition="INFINITY")
    with pytest.raises(
        ValueError, match="invalid robust-candidate measurement rejection",
    ):
        replace(diagnostic, solver_success=True, rank=3, condition=1.0)
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.robust.snapshot()),
    )


_INDEPENDENT_PRIOR_HINGE_DEG_BY_TRUST_COUNT = {
    10: (0.18842029449083292, 0.2170489346770609,
         0.498893319895757, 0.10289286077743316),
    4: (0.16773200331998814, 0.01710931450261072, 0.0, 0.0),
}
_PRODUCTION_SHAPED_SERVICE_MS = []


def _epoch(engine, packet, with_contact=False, *, continuous_history=True):
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    trusted_count=len(plan.selection.trusted_nodes)
    if continuous_history and trusted_count in _INDEPENDENT_PRIOR_HINGE_DEG_BY_TRUST_COUNT:
        angles=np.radians(_INDEPENDENT_PRIOR_HINGE_DEG_BY_TRUST_COUNT[trusted_count])
        for segment,angle in zip(
            ("forearm_left","forearm_right","shank_left","shank_right"),angles
        ):
            zero[segment]=np.array([angle,0.0,0.0])
        engine.pose.install(zero,measurement_time_s=.011,availability_time_s=.011)
    constraints = None
    if with_contact:
        points = corrected_proxy_points(rotations, zero, engine.pose.geometry)
        constraints = {"ankle_left": plan.candidate.root_position_m + points["ankle_left"]}
    mapping = _native200_mapping_owner(engine)
    measurement_ns = int(round(plan.measurement_s * 1e9))
    current_timer = int(math.floor((measurement_ns - 1 - mapping.b_ns) / mapping.a_ns_per_us))
    previous_timer = current_timer - 5_000
    for timer_us in (current_timer - 10_000, previous_timer, current_timer):
        global_ns = mapping.global_ns(timer_us)
        if global_ns * 1e-9 > engine.pose.publication_token().latest_sample_s:
            pair = engine.native200_source_pair(
                clock_mapping_owner=mapping,
                previous_timer_us=timer_us - 5_000,
                current_timer_us=timer_us,
                previous_global_ns=mapping.global_ns(timer_us - 5_000),
                current_global_ns=global_ns,
            )
            engine.sample_native200_pose(
                time_s=global_ns * 1e-9, native200_source_pair=pair,
                previous_base_pose=engine.exact_native200_base_pose(
                    native200_source_pair=pair, role="previous",
                    base_rotations_world=rotations,
                    base_pose_owner_digest="f" * 64,
                ),
                current_base_pose=engine.exact_native200_base_pose(
                    native200_source_pair=pair, role="current",
                    base_rotations_world=rotations,
                    base_pose_owner_digest="f" * 64,
                ),
            )
    source_pair = engine.native200_source_pair(
        clock_mapping_owner=mapping, previous_timer_us=previous_timer,
        current_timer_us=current_timer,
        previous_global_ns=mapping.global_ns(previous_timer),
        current_global_ns=mapping.global_ns(current_timer),
    )
    return engine.epoch(
        measurement_time_s=plan.measurement_s,
        availability_time_s=plan.availability_s,
        previous_orientation_time_s=source_pair.previous_global_ns * 1e-9,
        base_rotations_world=rotations,
        previous_correction_rotvec=zero,
        point_constraints_world_m=constraints,
        provenance="NO_RAW_PHYSICALLY_BOUND_FIXTURE",
        native200_source_pair=source_pair,
    )


def _native200_mapping_owner(engine, sha=None):
    if sha is None:
        sha = engine.native200_clock_owner_sha256
    return engine.native200_clock_mapping_owner(
        node="BSFC2CC", clock_owner_sha256=sha
    )


def _obsolete_epoch(engine, packet):
    epoch = _epoch(engine, packet)
    source = epoch.native200_source_pair
    mapping = _native200_mapping_owner(engine)
    next_timer = source.current_timer_us + 5_000
    next_global = mapping.global_ns(next_timer)
    pair = engine.native200_source_pair(
        clock_mapping_owner=mapping,
        previous_timer_us=source.current_timer_us,
        current_timer_us=next_timer,
        previous_global_ns=source.current_global_ns,
        current_global_ns=next_global,
    )
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    engine.sample_native200_pose(
        time_s=next_global * 1e-9, native200_source_pair=pair,
        previous_base_pose=engine.exact_native200_base_pose(
            native200_source_pair=pair, role="previous",
            base_rotations_world=rotations,
            base_pose_owner_digest="f" * 64,
        ),
        current_base_pose=engine.exact_native200_base_pose(
            native200_source_pair=pair, role="current",
            base_rotations_world=rotations,
            base_pose_owner_digest="f" * 64,
        ),
    )
    return replace(
        epoch, pose_token_digest=engine.pose.publication_token().digest,
    )


def test_typed_obsolete_sparse_multi_node_uses_existing_root_transaction_only(
    monkeypatch,
):
    engine, packet = _engine_and_packet()
    packet = _packet_with_exact_row_subset(packet, 4)
    epoch = _obsolete_epoch(engine, packet)
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.pose.transition_snapshot()),
        pickle.dumps(engine.robust.snapshot()),
    )
    actual_solve = integration.solve_articulated_ranges
    solve_calls = 0

    def counted_solve(*args, **kwargs):
        nonlocal solve_calls
        solve_calls += 1
        return actual_solve(*args, **kwargs)

    monkeypatch.setattr(integration, "solve_articulated_ranges", counted_solve)
    with pytest.raises(ObsoleteNative200SourcePair) as eager:
        engine.prepare_admission(
            packet, epoch, received_at_committed_horizon=True,
        )
    assert solve_calls == 1
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        pickle.dumps(engine.pose.transition_snapshot()),
        pickle.dumps(engine.robust.snapshot()),
    )

    prepared = engine.prepare_admission(
        packet, epoch, received_at_committed_horizon=True,
        allow_obsolete_native200_root_fallback=True,
    )
    result = prepared.prepared_result
    diagnostic = prepared.obsolete_native200_source_pair_diagnostic
    assert solve_calls == 1
    assert prepared.root_only and prepared.causal_transaction is not None
    assert result.accepted
    assert result.reason == "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR"
    assert diagnostic is result.obsolete_native200_source_pair_diagnostic
    assert diagnostic == eager.value.diagnostic
    assert diagnostic.requested_current_global_ns == epoch.orientation_time_ns
    assert diagnostic.latest_current_global_ns > epoch.orientation_time_ns
    assert result.direct_nodes == prepared.trusted_partition
    assert set(result.node_position_m) == set(NODE_TO_PROXY_POINT)
    assert set(result.propagated_nodes) == (
        set(NODE_TO_PROXY_POINT) - set(result.direct_nodes)
    )
    pose_before = engine.pose.publication_token().digest
    correction_before = engine.pose.transition_snapshot()["target_correction"]
    robust_before = engine.robust.revision
    engine.commit_admission(prepared)
    assert engine.pose.publication_token().digest == pose_before
    for segment in SEGMENTS:
        np.testing.assert_array_equal(
            engine.pose.transition_snapshot()["target_correction"][segment],
            correction_before[segment],
        )
    assert engine.robust.revision == robust_before + 1


def test_obsolete_preflight_is_read_only_and_fresh_multi_node_still_solves(monkeypatch):
    engine, packet = _engine_and_packet()
    packet = _packet_with_exact_row_subset(packet, 4)
    epoch = _epoch(engine, packet)
    source = epoch.native200_source_pair
    binding = {
        "source_node": source.node,
        "source_boot_epoch": source.boot_epoch,
        "previous_timer_us": source.previous_timer_us,
        "current_timer_us": source.current_timer_us,
        "previous_global_ns": source.previous_global_ns,
        "current_global_ns": source.current_global_ns,
        "source_clock_mapping_digest": source.mapping_digest,
    }
    before = engine.pose.publication_token().digest
    assert engine.pose.obsolete_native200_source_pair_diagnostic(**binding) is None
    assert engine.pose.publication_token().digest == before
    with pytest.raises(ValueError, match="NATIVE200_SOURCE_BINDING_INVALID"):
        engine.pose.obsolete_native200_source_pair_diagnostic(
            **{**binding, "current_timer_us": source.previous_timer_us},
        )

    actual_solve = integration.solve_articulated_ranges
    solve_calls = 0

    def counted_solve(*args, **kwargs):
        nonlocal solve_calls
        solve_calls += 1
        return actual_solve(*args, **kwargs)

    monkeypatch.setattr(integration, "solve_articulated_ranges", counted_solve)
    prepared = engine.prepare_admission(
        packet, epoch, received_at_committed_horizon=True,
        allow_obsolete_native200_root_fallback=True,
    )
    assert solve_calls == 1
    assert prepared.obsolete_native200_source_pair_diagnostic is None


def _scalar_batch_oracle(base, correction_batch, model):
    projected = []
    projections = []
    for index in range(len(next(iter(correction_batch.values())))):
        row, metrics = project_hinge_corrections(
            base,
            {segment: value[index] for segment, value in correction_batch.items()},
            model,
        )
        projected.append(row)
        projections.append(metrics)
    return projected, projections


def _matched_scenario_snapshot(evaluator, scenario):
    pose_module.evaluate_hinge_projection_batch = evaluator
    displacement = 1e-9 if scenario == "4_reject" else None
    engine, packet = _engine_and_packet(nominal_displacement=displacement)
    count = 4 if scenario.startswith("4_") else 1
    packet = _packet_with_nodes(packet, count)
    epoch = _epoch(engine, packet)
    if scenario.startswith("two_foot"):
        plan = engine.robust.prepare(
            engine.static, engine.root, packet,
            _prepare_dynamic_owner(engine.static, packet),
        )
        rotations = {segment: np.eye(3) for segment in SEGMENTS}
        zero = {segment: np.zeros(3) for segment in SEGMENTS}
        points = corrected_proxy_points(rotations, zero, engine.pose.geometry)
        constraints = {
            ankle: plan.candidate.root_position_m + points[ankle]
            for ankle in ("ankle_left", "ankle_right")
        }
        if scenario == "two_foot_reject":
            constraints["ankle_right"] = (
                constraints["ankle_right"] + np.array([0.5, 0.0, 0.0])
            )
        epoch = replace(epoch, point_constraints_world_m=constraints)
    result = engine.admit(packet, epoch)
    return {
        "accepted": result.accepted,
        "reason": result.reason,
        "trusted": result.trusted_nodes,
        "direct": result.direct_nodes,
        "propagated": result.propagated_nodes,
        "root": result.root_position_m.tobytes(),
        "correction": tuple(
            result.segment_correction_rotvec[segment].tobytes()
            for segment in SEGMENTS
        ),
        "root_digest": engine.root.publication_token().digest,
        "pose_digest": engine.pose.publication_token().digest,
        "robust_digest": hashlib.sha256(
            pickle.dumps(engine.robust.snapshot(), protocol=5)
        ).hexdigest(),
    }


@pytest.mark.parametrize(
    "scenario", ("4_accept", "4_reject", "two_foot_accept", "two_foot_reject")
)
def test_scalar_and_batch_match_explicit_authoritative_scenarios(
    monkeypatch, scenario,
):
    scalar = _matched_scenario_snapshot(_scalar_batch_oracle, scenario)
    batched = _matched_scenario_snapshot(evaluate_hinge_projection_batch, scenario)
    assert scalar == batched
    monkeypatch.setattr(
        pose_module, "evaluate_hinge_projection_batch", evaluate_hinge_projection_batch
    )


@pytest.mark.parametrize(
    ("transition_period_s", "derivative_period_s"),
    [
        (0.12, 0.005),
        (0.117, 0.005),
        (0.12, 0.007),
        (0.005, 0.005),
    ],
)
def test_retired_transition_timing_never_builds_a_pose_schedule(
    transition_period_s, derivative_period_s,
):
    engine, _packet = _engine_and_packet()
    geometry = engine.pose.geometry
    model = _model()
    projector = partial(project_hinge_corrections, model=model)
    target = {
        segment: np.array([0.002 * (index + 1), -0.001, 0.0005])
        for index, segment in enumerate(SEGMENTS)
    }

    def make_pose():
        from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
            CausalArticulatedPose,
        )
        pose = CausalArticulatedPose(
            action_start_s=0.0,
            action_stop_s=1.0,
            rotations_at_fraction=lambda _fraction: {
                segment: np.eye(3) for segment in SEGMENTS
            },
            geometry=geometry,
            hinge_projector=projector,
            transition_period_s=transition_period_s,
            derivative_period_s=derivative_period_s,
        )
        pose.sample(0.0)
        pose.sample(0.005)
        pose.sample(0.010)
        return pose

    pose = make_pose()
    calls = []
    original = pose.hinge_projector

    def counted(base, correction):
        calls.append(1)
        return original(base, correction)

    pose.hinge_projector = counted
    plan = pose.prepare_install(
        target, measurement_time_s=0.010, availability_time_s=0.020
    )
    assert calls == [1]
    assert not hasattr(plan, "planned_hinge")
    assert plan.transition_start_s == 0.020
    assert plan.transition_origin_correction is plan.target_correction
    assert pose.transition_period_s == transition_period_s
    assert pose.derivative_period_s == derivative_period_s


def test_authoritative_articulated_accepts_full_node_group():
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    result = engine.admit(packet, epoch)
    assert result.accepted
    assert len(result.trusted_nodes) == 10
    assert result.direct_nodes == result.trusted_nodes
    assert result.propagated_nodes == ()
    np.testing.assert_array_equal(
        result.root_covariance_m2, engine.root.current_state.covariance[:3, :3]
    )
    assert result.joint_covariance_status == "UNAVAILABLE_NOT_PROPAGATED"
    assert np.isfinite(result.root_covariance_m2).all()


@pytest.mark.parametrize("count", [10, 4])
def test_causal_continuous_fixture_solves_current_packet_once_and_reuses_projection(
    monkeypatch, count,
):
    engine, packet = _engine_and_packet()
    if count == 4:
        packet = _packet_with_nodes(packet, 4)
    epoch = _epoch(engine, packet)
    solves=[]; projections=[]
    original_solve=integration.solve_articulated_ranges
    original_transaction=integration.prepare_causal_update_transaction
    def counted_solve(*args,**kwargs):
        result=original_solve(*args,**kwargs); solves.append(result); return result
    def observed_transaction(*args,**kwargs):
        projections.append(kwargs["hinge_projection_at_source"])
        return original_transaction(*args,**kwargs)
    monkeypatch.setattr(integration,"solve_articulated_ranges",counted_solve)
    monkeypatch.setattr(integration,"prepare_causal_update_transaction",observed_transaction)
    result=engine.admit(packet,epoch)
    assert result.accepted and len(solves)==1
    assert projections==[solves[0].hinge_projection]


@pytest.mark.parametrize("count", [10, 4])
def test_production_shaped_delayed_stream_rebases_then_warms_at_native200(
    monkeypatch,count,
):
    engine,packet=_engine_and_packet()
    if count==4: packet=_packet_with_nodes(packet,4)
    epoch=_epoch(engine,packet)
    calls=[]; original=integration.solve_articulated_ranges
    def counted(*args,**kwargs):
        result=original(*args,**kwargs); calls.append(result); return result
    monkeypatch.setattr(integration,"solve_articulated_ranges",counted)
    started=time.perf_counter(); result=engine.admit(packet,epoch)
    service_ms=(time.perf_counter()-started)*1000.0
    _PRODUCTION_SHAPED_SERVICE_MS.append(service_ms)
    assert result.accepted and len(calls)==1 and service_ms<120.048
    mapping=epoch.native200_source_pair.clock_mapping_owner
    timer=epoch.native200_source_pair.current_timer_us
    availability_ns=int(round(packet.event.availability_time_s*1e9))
    while mapping.global_ns(timer)<=availability_ns: timer+=5_000
    validity=[]; output_times=[]
    for _ in range(3):
        global_ns=mapping.global_ns(timer); seconds=global_ns*1e-9
        assert global_ns>availability_ns
        engine.add_imu(ImuSample(seconds,seconds,np.array([0.,0.,9.80665]),np.eye(3),timer))
        pair=engine.native200_source_pair(
            clock_mapping_owner=mapping,previous_timer_us=timer-5_000,
            current_timer_us=timer,previous_global_ns=mapping.global_ns(timer-5_000),
            current_global_ns=global_ns)
        sample=engine.sample_native200_pose(
            time_s=seconds,native200_source_pair=pair,
            previous_base_pose=engine.exact_native200_base_pose(
                native200_source_pair=pair, role="previous",
                base_rotations_world={segment:np.eye(3) for segment in SEGMENTS},
                base_pose_owner_digest="f"*64),
            current_base_pose=engine.exact_native200_base_pose(
                native200_source_pair=pair, role="current",
                base_rotations_world={segment:np.eye(3) for segment in SEGMENTS},
                base_pose_owner_digest="f"*64))
        validity.append(sample.hinge_temporal.validity.name)
        output_times.append(global_ns)
        timer+=5_000
    expected_global_delta=mapping.global_ns(timer)-mapping.global_ns(timer-5_000)
    assert output_times[1]-output_times[0]==expected_global_delta
    assert output_times[2]-output_times[1]==expected_global_delta
    assert validity==["RESET_SEQUENCE_GAP","WARMUP_QDDOT","QUALIFIED"]


def test_exact_source_base_pair_bypasses_fraction_and_rejects_identity_errors(
    monkeypatch,
):
    engine,_packet=_engine_and_packet(clock_owner_sha256="e"*64)
    mapping=_native200_mapping_owner(engine)
    current_timer=50_000
    while mapping.global_ns(current_timer)*1e-9<=engine.pose.publication_token().latest_sample_s:
        current_timer+=5_000
    pair=engine.native200_source_pair(
        clock_mapping_owner=mapping,previous_timer_us=current_timer-5_000,
        current_timer_us=current_timer,
        previous_global_ns=mapping.global_ns(current_timer-5_000),
        current_global_ns=mapping.global_ns(current_timer))
    previous_base={segment:np.eye(3) for segment in SEGMENTS}
    current_base={segment:np.eye(3) for segment in SEGMENTS}
    previous=engine.exact_native200_base_pose(
        native200_source_pair=pair,role="previous",
        base_rotations_world=previous_base,base_pose_owner_digest="f"*64)
    current=engine.exact_native200_base_pose(
        native200_source_pair=pair,role="current",
        base_rotations_world=current_base,base_pose_owner_digest="f"*64)
    assert all(not value.flags.writeable for value in previous.base_rotations_world.values())
    before=(engine.root.publication_token().digest,engine.pose.publication_token().digest,
        repr(engine.robust.snapshot()))
    for bad_previous,bad_current in ((current,previous),(previous,None),(None,current)):
        with pytest.raises(ValueError):
            engine.sample_native200_pose(
                time_s=pair.current_global_ns*1e-9,
                native200_source_pair=pair,
                previous_base_pose=bad_previous,current_base_pose=bad_current)
        assert (engine.root.publication_token().digest,engine.pose.publication_token().digest,
            repr(engine.robust.snapshot()))==before
    with pytest.raises(ValueError,match="owner mismatch"):
        engine.exact_native200_base_pose(
            native200_source_pair=pair,role="current",
            base_rotations_world=current_base,base_pose_owner_digest="0"*64)
    malformed=dict(current_base); malformed["pelvis"]=np.full((3,3),np.nan)
    with pytest.raises(ValueError,match="non-finite"):
        engine.exact_native200_base_pose(
            native200_source_pair=pair,role="current",
            base_rotations_world=malformed,base_pose_owner_digest="f"*64)
    monkeypatch.setattr(
        engine.pose,"rotations_at_fraction",
        lambda _fraction: (_ for _ in ()).throw(
            AssertionError("source-owned sample called action fraction")
        ))
    sample=engine.sample_native200_pose(
        time_s=pair.current_global_ns*1e-9,native200_source_pair=pair,
        previous_base_pose=previous,current_base_pose=current)
    assert sample.fraction is None


def test_one_node_admit_has_zero_solver_projector_and_temporal_mutation(monkeypatch):
    engine,packet=_engine_and_packet(); packet=_packet_with_nodes(packet,1)
    epoch=_epoch(engine,packet); temporal=engine.pose._CausalArticulatedPose__hinge_temporal_owner
    before=temporal._state_bytes(); solve_calls=[]; projector_calls=[]
    monkeypatch.setattr(integration,"solve_articulated_ranges",
        lambda *args,**kwargs: solve_calls.append(1))
    engine.pose.hinge_projector=lambda *args,**kwargs: projector_calls.append(1)
    started=time.perf_counter(); result=engine.admit(packet,epoch)
    _PRODUCTION_SHAPED_SERVICE_MS.append((time.perf_counter()-started)*1000.0)
    assert result.accepted and solve_calls==[] and projector_calls==[]
    assert temporal._state_bytes()==before
    values=np.asarray(_PRODUCTION_SHAPED_SERVICE_MS,float)
    print("PRODUCTION_SHAPED_UWB_MS",{
        "p50":float(np.percentile(values,50)),
        "p99":float(np.percentile(values,99)),"max":float(np.max(values)),
        "rows":values.tolist()})


def test_articulated_rejection_diagnostic_maps_existing_gates_and_is_inert(monkeypatch):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = integration.solve_articulated_ranges
    solved = actual(
        engine.robust.prepare(engine.static, engine.root, packet).selection.trusted_links,
        anchors_m=engine.static.anchors_m,
        base_rotations_world=epoch.base_rotations_world,
        geometry=engine.pose.geometry,
        initial_root_m=engine.root.current_state.vector[:3],
        fixed_root_position_m=engine.root.current_state.vector[:3],
        root_velocity_mps=engine.root.current_state.vector[3:6],
        previous_correction_rotvec=epoch.previous_correction_rotvec,
        active_segments=tuple(SEGMENTS),
        point_constraints_world_m=epoch.point_constraints_world_m,
        hinge_projector=engine.pose.hinge_projector, config=engine.config,
    )
    before = (engine.root.publication_token().digest,
              engine.pose.publication_token().digest, engine.robust.revision)
    cases = (
        ("OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE", True, True),
        ("PROJECTED_JOINT_OBJECTIVE_FAILURE", False, True),
        ("PROJECTED_FOOTHOLD_GATE_FAILURE", True, False),
    )
    for reason, projection_gate, foothold_gate in cases:
        projection = dict(solved.hinge_projection)
        projection.update(
            projection_acceptance_gate=projection_gate,
            foothold_projection_gate=foothold_gate,
        )
        if reason == "PROJECTED_FOOTHOLD_GATE_FAILURE":
            projection.update(
                range_projection_gate=False, joint_projection_gate=True,
                projection_acceptance_gate=True,
                projection_acceptance_owner="FULL_EXISTING_NORMALIZED_RESIDUAL_OBJECTIVE",
                projection_acceptance_branch="JOINT_FULL_RESIDUAL",
            )
        rejected = replace(
            solved, success=False, reason=reason, nfev=17, cost=12.5,
            rank=2, condition=float("inf"), hinge_projection=projection,
        )
        monkeypatch.setattr(integration, "solve_articulated_ranges",
                            lambda *_args, _value=rejected, **_kwargs: _value)
        prepared = engine.prepare_admission(packet, epoch)
        diagnostic = prepared.articulated_rejection_diagnostic
        assert diagnostic is prepared.prepared_result.articulated_rejection_diagnostic
        assert diagnostic.solver_reason == reason
        assert diagnostic.trusted_partition == prepared.trusted_partition
        assert diagnostic.projection_acceptance_gate is projection_gate
        assert diagnostic.foothold_projection_gate is foothold_gate
        if reason == "PROJECTED_FOOTHOLD_GATE_FAILURE":
            assert diagnostic.range_projection_gate is False
            assert diagnostic.joint_projection_gate is True
            assert diagnostic.projection_acceptance_gate is True
        assert diagnostic.contact_gate_passed is None
        assert diagnostic.optimizer_nfev == 17 and diagnostic.optimizer_cost == 12.5
        assert diagnostic.rank == 2 and diagnostic.condition == "POSITIVE_INFINITY"
        assert diagnostic.optimizer_success is projection["optimizer_success"]
        assert diagnostic.optimizer_status == projection["optimizer_status"]
        assert diagnostic.optimizer_message_class == projection["optimizer_message_class"]
        assert diagnostic.optimizer_message == projection["optimizer_message"]
        assert diagnostic.fk_gate_passed is diagnostic.contact_gate_passed is None
        for field in (
            "projection_acceptance_owner", "projection_acceptance_branch",
            "projection_applied", "point_constraints_present",
            "point_constraint_count", "point_constraint_identity",
            "prefit_range_median_abs_m", "raw_optimized_range_median_abs_m",
            "projected_range_median_abs_m", "range_projection_gate",
            "prefit_full_residual_objective", "raw_optimized_full_residual_objective",
            "projected_full_residual_objective", "full_residual_objective_tolerance",
            "joint_projection_gate", "projection_acceptance_tolerance",
            "numerical_geometry_success", "optimizer_root_finite", "rank_gate",
            "condition_finite", "condition_gate", "correction_gate",
            "correction_tolerance_rad", "physical_residual_finite",
        ):
            assert getattr(diagnostic, field) == projection[field]
        assert (engine.root.publication_token().digest,
                engine.pose.publication_token().digest,
                engine.robust.revision) == before

    peer, peer_packet = _engine_and_packet()
    peer_prepared = peer.prepare_admission(peer_packet, _epoch(peer, peer_packet))
    assert peer_prepared.articulated_rejection_diagnostic == diagnostic

    monkeypatch.setattr(integration, "solve_articulated_ranges", actual)
    accepted_engine, accepted_packet = _engine_and_packet()
    accepted = accepted_engine.prepare_admission(
        accepted_packet, _epoch(accepted_engine, accepted_packet),
    )
    assert accepted.prepared_result.accepted
    assert accepted.articulated_rejection_diagnostic is None


def test_multi_node_articulated_failure_uses_guarded_root_fallback(monkeypatch):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = integration.solve_articulated_ranges
    solved = actual(
        engine.robust.prepare(engine.static, engine.root, packet).selection.trusted_links,
        anchors_m=engine.static.anchors_m,
        base_rotations_world=epoch.base_rotations_world,
        geometry=engine.pose.geometry,
        initial_root_m=engine.root.current_state.vector[:3],
        fixed_root_position_m=engine.root.current_state.vector[:3],
        root_velocity_mps=engine.root.current_state.vector[3:6],
        previous_correction_rotvec=epoch.previous_correction_rotvec,
        active_segments=tuple(SEGMENTS),
        point_constraints_world_m=epoch.point_constraints_world_m,
        hinge_projector=engine.pose.hinge_projector, config=engine.config,
    )
    rejected_solve = replace(
        solved, success=False, reason="OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE",
    )
    monkeypatch.setattr(
        integration, "solve_articulated_ranges",
        lambda *_args, **_kwargs: rejected_solve,
    )
    pose_before = engine.pose.publication_token().digest
    correction_before = engine.pose.transition_snapshot()["target_correction"]
    robust_before = engine.robust.revision

    prepared = engine.prepare_admission(packet, epoch)
    result = prepared.prepared_result
    assert prepared.root_only and prepared.causal_transaction is not None
    assert result.accepted
    assert result.reason == (
        "ACCEPTED_ROOT_FALLBACK_ARTICULATED_REJECTED:"
        "OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE"
    )
    assert result.trusted_nodes == prepared.trusted_partition
    assert len(result.direct_nodes) > 1
    assert result.direct_nodes == result.trusted_nodes
    assert result.articulated_rejection_diagnostic is prepared.articulated_rejection_diagnostic
    assert result.pose_token_digest == pose_before
    for segment in SEGMENTS:
        np.testing.assert_array_equal(
            result.segment_correction_rotvec[segment], correction_before[segment],
        )

    peer, peer_packet = _engine_and_packet()
    peer_epoch = _epoch(peer, peer_packet)
    peer_prepared = peer.prepare_admission(peer_packet, peer_epoch)
    assert peer_prepared.trusted_partition == prepared.trusted_partition
    assert (peer_prepared.articulated_rejection_diagnostic
            == prepared.articulated_rejection_diagnostic)
    assert peer_prepared.prepared_result.reason == result.reason
    peer_before = (
        peer.root.publication_token().digest,
        peer.pose.publication_token().digest,
        peer.robust.snapshot(),
    )
    original_apply = peer.robust._apply_prevalidated_commit

    def apply_then_fail(ticket):
        original_apply(ticket)
        raise RuntimeError("INJECTED_FALLBACK_SIDECAR_FAILURE")

    monkeypatch.setattr(peer.robust, "_apply_prevalidated_commit", apply_then_fail)
    with pytest.raises(RuntimeError, match="FALLBACK_SIDECAR_FAILURE"):
        peer.commit_admission(peer_prepared)
    assert peer_before == (
        peer.root.publication_token().digest,
        peer.pose.publication_token().digest,
        peer.robust.snapshot(),
    )
    engine.commit_admission(prepared)
    assert engine.pose.publication_token().digest == pose_before
    assert engine.robust.revision == robust_before + 1


def test_articulated_root_fallback_still_rejects_teleport_and_foothold(monkeypatch):
    actual = integration.solve_articulated_ranges

    def force_failure(engine, packet, epoch):
        solved = actual(
            engine.robust.prepare(engine.static, engine.root, packet).selection.trusted_links,
            anchors_m=engine.static.anchors_m,
            base_rotations_world=epoch.base_rotations_world,
            geometry=engine.pose.geometry,
            initial_root_m=engine.root.current_state.vector[:3],
            fixed_root_position_m=engine.root.current_state.vector[:3],
            root_velocity_mps=engine.root.current_state.vector[3:6],
            previous_correction_rotvec=epoch.previous_correction_rotvec,
            active_segments=tuple(SEGMENTS),
            point_constraints_world_m=epoch.point_constraints_world_m,
            hinge_projector=engine.pose.hinge_projector, config=engine.config,
        )
        monkeypatch.setattr(
            integration, "solve_articulated_ranges",
            lambda *_args, **_kwargs: replace(
                solved, success=False,
                reason="OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE",
            ),
        )

    teleport, teleport_packet = _engine_and_packet(nominal_displacement=1e-9)
    teleport_epoch = _epoch(teleport, teleport_packet)
    force_failure(teleport, teleport_packet, teleport_epoch)
    before = (teleport.root.publication_token().digest,
              teleport.pose.publication_token().digest, teleport.robust.snapshot())
    rejected = teleport.admit(teleport_packet, teleport_epoch)
    assert not rejected.accepted and rejected.transaction is not None
    assert before == (teleport.root.publication_token().digest,
                      teleport.pose.publication_token().digest, teleport.robust.snapshot())

    foothold, foothold_packet = _engine_and_packet()
    foothold_epoch = _epoch(foothold, foothold_packet, with_contact=True)
    displaced = dict(foothold_epoch.point_constraints_world_m)
    displaced["ankle_left"] = displaced["ankle_left"] + np.array([0.5, 0.0, 0.0])
    foothold_epoch = replace(foothold_epoch, point_constraints_world_m=displaced)
    force_failure(foothold, foothold_packet, foothold_epoch)
    before = (foothold.root.publication_token().digest,
              foothold.pose.publication_token().digest, foothold.robust.snapshot())
    rejected = foothold.admit(foothold_packet, foothold_epoch)
    assert not rejected.accepted and rejected.reason == "STANCE_FOOT_SLIP_REJECTED"
    assert rejected.articulated_rejection_diagnostic is not None
    assert before == (foothold.root.publication_token().digest,
                      foothold.pose.publication_token().digest, foothold.robust.snapshot())


def test_multi_node_root_fallback_requires_authenticated_native200_source(monkeypatch):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    actual = integration.solve_articulated_ranges
    solved = actual(
        engine.robust.prepare(engine.static, engine.root, packet).selection.trusted_links,
        anchors_m=engine.static.anchors_m,
        base_rotations_world=epoch.base_rotations_world,
        geometry=engine.pose.geometry,
        initial_root_m=engine.root.current_state.vector[:3],
        fixed_root_position_m=engine.root.current_state.vector[:3],
        root_velocity_mps=engine.root.current_state.vector[3:6],
        previous_correction_rotvec=epoch.previous_correction_rotvec,
        active_segments=tuple(SEGMENTS),
        point_constraints_world_m=epoch.point_constraints_world_m,
        hinge_projector=engine.pose.hinge_projector, config=engine.config,
    )
    monkeypatch.setattr(
        integration, "solve_articulated_ranges",
        lambda *_args, **_kwargs: replace(
            solved, success=False,
            reason="OPTIMIZER_OR_PROJECTED_GEOMETRY_FAILURE",
        ),
    )
    measurement_ns = int(round(epoch.measurement_time_s * 1_000_000_000))
    missing_source = engine.epoch(
        measurement_time_s=measurement_ns * 1e-9,
        availability_time_s=epoch.availability_time_s,
        previous_orientation_time_s=(measurement_ns - 5_000_000) * 1e-9,
        orientation_time_ns=measurement_ns,
        previous_orientation_time_ns=measurement_ns - 5_000_000,
        base_rotations_world=epoch.base_rotations_world,
        previous_correction_rotvec=epoch.previous_correction_rotvec,
        point_constraints_world_m=epoch.point_constraints_world_m,
        provenance=epoch.provenance,
    )
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    prepared = engine.prepare_admission(packet, missing_source)
    result = prepared.prepared_result
    assert not result.accepted
    assert result.reason == "ARTICULATED_ROOT_FALLBACK_NATIVE200_SOURCE_REQUIRED"
    assert prepared.causal_transaction is None and prepared.sidecar_ticket is None
    assert result.articulated_rejection_diagnostic is not None
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )


def test_action04_call_site_uses_only_source_owned_native200_publication():
    source=(Path(__file__).parents[1]/"tools/run_c2_authoritative_articulated_action04.py").read_text()
    assert "engine.pose.sample(event_time)" not in source
    assert "engine.sample_native200_pose(" in source
    assert "return pose_at_exact_native200_global_ns(query_ns)[1]" in source
    assert "selected, _rotations, points = pose_strictly_before_async_global_ns(" in source
    assert "selected = pose_clocks[ACTION].strict_floor(int(query_ns))" in source


@pytest.mark.parametrize(("count","expected_acceleration"),[(10,348.29324194),(4,117.09902876)])
def test_discontinuous_impossible_candidate_remains_rejected(
    monkeypatch,count,expected_acceleration,
):
    engine,packet=_engine_and_packet()
    if count==4: packet=_packet_with_nodes(packet,4)
    epoch=_epoch(engine,packet,continuous_history=False)
    result=engine.admit(packet,epoch)
    assert not result.accepted and result.reason=="REJECT_REACHABILITY_UNQUALIFIED"
    assert result.transaction is not None
    assert result.transaction.decision.metrics[
        "joint_angular_acceleration_maximum_rad_s2"
    ]==pytest.approx(expected_acceleration,rel=0,abs=2e-8)


def test_one_node_contact_slip_rejects_without_any_owner_mutation():
    engine, packet = _engine_and_packet()
    packet = _packet_with_nodes(packet, 1)
    epoch = _epoch(engine, packet, with_contact=True)
    displaced = dict(epoch.point_constraints_world_m)
    displaced["ankle_left"] = displaced["ankle_left"] + np.array([0.5, 0.0, 0.0])
    epoch = replace(epoch, point_constraints_world_m=displaced)
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
        engine.pose._CausalArticulatedPose__hinge_temporal_owner._state_bytes(),
    )
    result = engine.admit(packet, epoch)
    after = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
        engine.pose._CausalArticulatedPose__hinge_temporal_owner._state_bytes(),
    )
    assert not result.accepted
    assert result.reason == "STANCE_FOOT_SLIP_REJECTED"
    assert before == after
    assert result.direct_nodes == ()
    np.testing.assert_array_equal(
        result.root_position_m, engine.root.current_state.vector[:3]
    )


@pytest.mark.parametrize("count", [7, 4, 1])
def test_partial_and_one_node_groups_update_one_root_and_propagate_fk(count):
    engine, packet = _engine_and_packet()
    packet = _packet_with_nodes(packet, count)
    epoch = _epoch(engine, packet)

    result = engine.admit(packet, epoch)

    assert result.accepted
    assert len(result.trusted_nodes) == count
    assert result.direct_nodes == result.trusted_nodes
    assert len(result.propagated_nodes) == 10 - count
    assert set(result.node_position_m) == set(engine.static.clocks)
    assert engine.robust.revision == 1


def test_untrusted_contact_leg_remains_in_real_ik_without_bias_membership(monkeypatch):
    engine, packet = _engine_and_packet()
    packet = _packet_with_nodes(packet, 4)
    epoch = _epoch(engine, packet, with_contact=True)
    actual = integration.solve_articulated_ranges
    captured = {}

    def observed(*args, **kwargs):
        captured["active_segments"] = kwargs["active_segments"]
        return actual(*args, **kwargs)

    monkeypatch.setattr(integration, "solve_articulated_ranges", observed)
    result = engine.admit(packet, epoch)

    assert result.accepted
    assert {"pelvis", "thigh_left", "shank_left"} <= set(
        captured["active_segments"]
    )
    assert "BSF6C53" not in result.trusted_nodes
    assert "BSF6C53" in result.propagated_nodes


@pytest.mark.parametrize("failure_owner", ["robust", "pose", "root"])
def test_failure_after_each_commit_participant_rolls_every_owner_back(
    monkeypatch, failure_owner,
):
    engine, packet = _engine_and_packet()
    epoch = _epoch(engine, packet)
    target = {
        "robust": (engine.robust, "_apply_prevalidated_commit"),
        "pose": (engine.pose, "_apply_prevalidated_install"),
        "root": (engine.root, "_apply_prevalidated_position"),
    }[failure_owner]
    original = getattr(*target)

    def apply_then_fail(ticket):
        original(ticket)
        raise RuntimeError(f"INJECTED_AFTER_{failure_owner.upper()}")

    monkeypatch.setattr(*target, apply_then_fail)
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
        engine.pose._CausalArticulatedPose__hinge_temporal_owner._state_bytes(),
    )
    with pytest.raises(RuntimeError, match="INJECTED_AFTER"):
        engine.admit(packet, epoch)
    after = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
        engine.pose._CausalArticulatedPose__hinge_temporal_owner._state_bytes(),
    )
    assert before == after


def test_u1_impossible_transition_and_bad_native_cadence_fail_closed():
    engine, packet = _engine_and_packet(nominal_displacement=1e-9)
    packet = _packet_with_nodes(packet, 4)
    epoch = _epoch(engine, packet)
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    result = engine.admit(packet, epoch)
    assert not result.accepted
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    with pytest.raises(ValueError, match="timing/ownership"):
        replace(epoch, previous_orientation_time_s=epoch.measurement_time_s - 0.01)


def test_two_foot_contact_accepts_and_slip_rejects_without_mutation():
    engine, packet = _engine_and_packet()
    packet = _packet_with_nodes(packet, 1)
    epoch = _epoch(engine, packet)
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    points = corrected_proxy_points(rotations, zero, engine.pose.geometry)
    constraints = {
        ankle: plan.candidate.root_position_m + points[ankle]
        for ankle in ("ankle_left", "ankle_right")
    }
    accepted_epoch = replace(epoch, point_constraints_world_m=constraints)
    accepted = engine.admit(packet, accepted_epoch)
    assert accepted.accepted
    assert accepted.maximum_foothold_residual_m <= 1e-12

    rejected_engine, rejected_packet = _engine_and_packet()
    rejected_packet = _packet_with_nodes(rejected_packet, 1)
    rejected_epoch = _epoch(rejected_engine, rejected_packet)
    rejected_plan = rejected_engine.robust.prepare(
        rejected_engine.static, rejected_engine.root, rejected_packet,
        _prepare_dynamic_owner(rejected_engine.static, rejected_packet),
    )
    rejected_points = corrected_proxy_points(
        rotations, zero, rejected_engine.pose.geometry
    )
    displaced = {
        ankle: rejected_plan.candidate.root_position_m + rejected_points[ankle]
        for ankle in ("ankle_left", "ankle_right")
    }
    displaced["ankle_right"] = displaced["ankle_right"] + np.array([0.5, 0.0, 0.0])
    rejected_epoch = replace(
        rejected_epoch, point_constraints_world_m=displaced
    )
    before = (
        rejected_engine.root.publication_token().digest,
        rejected_engine.pose.publication_token().digest,
        rejected_engine.robust.snapshot(),
    )
    rejected = rejected_engine.admit(rejected_packet, rejected_epoch)
    assert not rejected.accepted
    assert rejected.reason == "STANCE_FOOT_SLIP_REJECTED"
    assert before == (
        rejected_engine.root.publication_token().digest,
        rejected_engine.pose.publication_token().digest,
        rejected_engine.robust.snapshot(),
    )


def test_large_common_global_cadence_is_owned_by_exact_integer_ticks():
    engine, _packet = _engine_and_packet()
    clock = engine.static.clocks["BSFC2CC"]
    current_timer_us = 235_093_760_000
    previous_timer_us = current_timer_us - 5_000
    current_ns = int(round(
        clock.a_ns_per_us * current_timer_us + clock.b_ns
    ))
    previous_ns = int(round(
        clock.a_ns_per_us * previous_timer_us + clock.b_ns
    ))
    current_s = current_ns * 1e-9
    previous_s = previous_ns * 1e-9
    # This is the exact false-failure mechanism sealed by raw revision003.
    assert current_s - previous_s != integration.NATIVE200_PERIOD_S
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}

    source_pair = engine.native200_source_pair(
        clock_mapping_owner=_native200_mapping_owner(engine),
        previous_timer_us=previous_timer_us,
        current_timer_us=current_timer_us,
        previous_global_ns=previous_ns,
        current_global_ns=current_ns,
    )
    epoch = engine.epoch(
        measurement_time_s=(current_ns + 1_000_000) * 1e-9,
        availability_time_s=(current_ns + 10_000_000) * 1e-9,
        previous_orientation_time_s=previous_s,
        base_rotations_world=rotations,
        previous_correction_rotvec=zero,
        provenance="SEALED_REV003_FALSE_FLOAT_SUBTRACTION_REPRODUCTION",
        native200_source_pair=source_pair,
    )

    assert epoch.orientation_time_ns == current_ns
    assert epoch.previous_orientation_time_ns == previous_ns
    assert (
        epoch.native200_source_pair.current_timer_us
        - epoch.native200_source_pair.previous_timer_us
        == 5_000
    )
    assert epoch.orientation_time_s == current_ns * 1e-9
    assert epoch.previous_orientation_time_s == previous_ns * 1e-9


@pytest.mark.parametrize(
    ("current_ns", "previous_ns"),
    [
        (235_093_762_417_886, 235_093_757_417_885),
        (235_093_762_417_886, 235_093_757_417_887),
        (235_093_762_417_886.0, 235_093_757_417_886),
        (True, 235_093_757_417_886),
    ],
)
def test_integer_tick_owner_rejects_wrong_or_noncanonical_cadence(
    current_ns, previous_ns
):
    engine, _packet = _engine_and_packet()
    display_current_ns = 235_093_762_417_886
    display_previous_ns = display_current_ns - 5_000_000
    with pytest.raises(ValueError):
        engine.epoch(
            measurement_time_s=display_current_ns * 1e-9,
            availability_time_s=(display_current_ns + 10_000_000) * 1e-9,
            previous_orientation_time_s=display_previous_ns * 1e-9,
            base_rotations_world={segment: np.eye(3) for segment in SEGMENTS},
            previous_correction_rotvec={segment: np.zeros(3) for segment in SEGMENTS},
            provenance="NEGATIVE_INTEGER_TICK_OWNER_FIXTURE",
            orientation_time_ns=current_ns,
            previous_orientation_time_ns=previous_ns,
        )


def test_native200_source_pair_rejects_gap_duplicate_reorder_and_clock_mismatch():
    engine, packet = _engine_and_packet()
    clock = engine.static.clocks["BSFC2CC"]
    current_timer_us = 55_000
    current_global_ns = int(round(
        clock.a_ns_per_us * current_timer_us + clock.b_ns
    ))
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    for previous_timer_us in (50_001, 55_000, 60_000):
        previous_global_ns = int(round(
            clock.a_ns_per_us * previous_timer_us + clock.b_ns
        ))
        with pytest.raises(ValueError):
            engine.native200_source_pair(
                clock_mapping_owner=_native200_mapping_owner(engine),
                previous_timer_us=previous_timer_us,
                current_timer_us=current_timer_us,
                previous_global_ns=previous_global_ns,
                current_global_ns=current_global_ns,
            )
    with pytest.raises(ValueError, match="static clock"):
        engine.native200_clock_mapping_owner(
            node="MISSING", clock_owner_sha256="b" * 64
        )
    with pytest.raises(ValueError, match="SHA differs"):
        _native200_mapping_owner(engine, "b" * 64)
    with pytest.raises(ValueError, match="mapping mismatch"):
        engine.native200_source_pair(
            clock_mapping_owner=_native200_mapping_owner(engine),
            previous_timer_us=50_000, current_timer_us=55_000,
            previous_global_ns=int(round(clock.a_ns_per_us * 50_000 + clock.b_ns)),
            current_global_ns=current_global_ns + 1,
        )
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )


def test_native200_mapping_digest_and_measurement_binding_reject_pre_mutation():
    engine, packet = _engine_and_packet(clock_owner_sha256="c" * 64)
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    clock = engine.static.clocks["BSFC2CC"]
    measurement_ns=int(round(plan.measurement_s*1e9))
    current_timer_us=int(math.floor((measurement_ns-1-clock.b_ns)/clock.a_ns_per_us))
    previous_timer_us=current_timer_us-5_000
    source = engine.native200_source_pair(
        clock_mapping_owner=_native200_mapping_owner(engine, "c" * 64),
        previous_timer_us=previous_timer_us, current_timer_us=current_timer_us,
        previous_global_ns=int(round(clock.a_ns_per_us * previous_timer_us + clock.b_ns)),
        current_global_ns=int(round(clock.a_ns_per_us * current_timer_us + clock.b_ns)),
    )
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    zero = {segment: np.zeros(3) for segment in SEGMENTS}
    epoch = engine.epoch(
        measurement_time_s=plan.measurement_s,
        availability_time_s=plan.availability_s,
        previous_orientation_time_s=source.previous_global_ns * 1e-9,
        base_rotations_world=rotations,
        previous_correction_rotvec=zero,
        provenance="SOURCE_PAIR_MAPPING_NEGATIVE_FIXTURE",
        native200_source_pair=source,
    )
    before = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    wrong_owner = replace(
        source.clock_mapping_owner, clock_owner_sha256="d" * 64, digest=""
    )
    tampered = replace(
        epoch, native200_source_pair=replace(
            source, clock_mapping_owner=wrong_owner
        )
    )
    with pytest.raises(ValueError, match="owner SHA"):
        engine.admit(packet, tampered)
    with pytest.raises(ValueError, match="timing/ownership"):
        engine.epoch(
            measurement_time_s=(source.current_global_ns - 1) * 1e-9,
            availability_time_s=plan.availability_s,
            previous_orientation_time_s=source.previous_global_ns * 1e-9,
            base_rotations_world=rotations,
            previous_correction_rotvec=zero,
            provenance="SOURCE_AFTER_MEASUREMENT_NEGATIVE_FIXTURE",
            native200_source_pair=source,
        )
    assert before == (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )


@pytest.mark.parametrize("bad_seconds", [float("nan"), float("inf"), -float("inf")])
def test_orientation_owner_rejects_nonfinite_legacy_seconds(bad_seconds):
    engine, packet = _engine_and_packet()
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    with pytest.raises(ValueError):
        engine.epoch(
            measurement_time_s=plan.measurement_s,
            availability_time_s=plan.availability_s,
            previous_orientation_time_s=bad_seconds,
            base_rotations_world={segment: np.eye(3) for segment in SEGMENTS},
            previous_correction_rotvec={segment: np.zeros(3) for segment in SEGMENTS},
            provenance="NONFINITE_LEGACY_SECONDS_FIXTURE",
        )


def test_source_owned_epoch_uses_strict_premeasurement_native_tick():
    engine, packet = _engine_and_packet()
    plan = engine.robust.prepare(
        engine.static, engine.root, packet,
        _prepare_dynamic_owner(engine.static, packet),
    )
    legacy = _epoch(engine, packet)
    source = legacy.native200_source_pair
    assert source is not None
    assert source.current_global_ns < round(plan.measurement_s * 1e9)
    exact = engine.epoch(
        measurement_time_s=plan.measurement_s,
        availability_time_s=plan.availability_s,
        previous_orientation_time_s=source.previous_global_ns * 1e-9,
        base_rotations_world={segment: np.eye(3) for segment in SEGMENTS},
        previous_correction_rotvec={segment: np.zeros(3) for segment in SEGMENTS},
        provenance="NO_RAW_PHYSICALLY_BOUND_FIXTURE",
        native200_source_pair=source,
    )
    assert legacy.orientation_time_ns == exact.orientation_time_ns
    assert legacy.previous_orientation_time_ns == exact.previous_orientation_time_ns
    assert legacy.orientation_time_s == exact.orientation_time_s
    assert legacy.previous_orientation_time_s == exact.previous_orientation_time_s


def test_native200_publication_exact_tick_and_uwb_prelink_strict_floor_diverge():
    timer = np.array([100_000, 105_000, 110_000, 115_000], dtype=np.int64)
    owner = DirectNative200Clock(
        action="04_shoulder_left",
        time_root_s=timer.astype(float) * 1e-6,
        source_pelvis_timer_us=timer,
        source_contiguous_span_id=np.zeros(4, dtype=np.int64),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=37.0,
        valid_mask=np.ones(4, dtype=bool),
    )
    # Deliberately nonconstant frames; both neighbors are poison values that
    # make an off-by-one publication immediately observable.
    frame_pose = np.stack([
        Rotation.from_rotvec([0.0, 0.0, value]).as_matrix()
        for value in (0.11, -0.37, 0.83, -1.21)
    ])
    frame = 2
    source_global_ns = int(owner.global_ns[frame])
    published = owner.exact_tick(
        source_global_ns, source_timer_us=int(timer[frame])
    )
    prior = owner.strict_floor(source_global_ns)
    assert published.frame == frame
    assert published.pose_global_ns == source_global_ns
    assert published.age_ns == 0.0
    np.testing.assert_array_equal(frame_pose[published.frame], frame_pose[frame])
    assert not np.array_equal(frame_pose[published.frame], frame_pose[frame - 1])
    assert not np.array_equal(frame_pose[published.frame], frame_pose[frame + 1])
    assert prior.frame == frame - 1
    assert prior.pose_global_ns < source_global_ns
    np.testing.assert_array_equal(frame_pose[prior.frame], frame_pose[frame - 1])
    with pytest.raises(ValueError, match="timer/global identity"):
        owner.exact_tick(source_global_ns, source_timer_us=int(timer[frame - 1]))


def test_noncommensurate_ankle_clock_uses_strictly_prior_pelvis_pose():
    timer = np.array([200_000, 205_000, 210_000, 215_000], dtype=np.int64)
    owner = DirectNative200Clock(
        action="04_shoulder_left",
        time_root_s=timer.astype(float) * 1e-6,
        source_pelvis_timer_us=timer,
        source_contiguous_span_id=np.zeros(4, dtype=np.int64),
        common_clock_a_ns_per_us=1000.0,
        common_clock_b_ns=91.0,
        valid_mask=np.ones(4, dtype=bool),
    )
    poses = np.stack([
        Rotation.from_rotvec([value, 0.0, 0.0]).as_matrix()
        for value in (-0.91, 0.23, 1.07, -1.33)
    ])
    pelvis_frame = 2
    pelvis_ns = int(owner.global_ns[pelvis_frame])
    asynchronous_ankle_ns = pelvis_ns + 2_345_678

    published = owner.exact_tick(
        pelvis_ns, source_timer_us=int(timer[pelvis_frame])
    )
    ankle = owner.strict_floor(asynchronous_ankle_ns)
    ankle_equal = owner.strict_floor(pelvis_ns)
    uwb_boundary = owner.strict_floor(pelvis_ns)

    assert published.frame == pelvis_frame
    assert ankle.frame == pelvis_frame
    assert ankle_equal.frame == pelvis_frame - 1
    assert uwb_boundary.frame == pelvis_frame - 1
    np.testing.assert_array_equal(poses[published.frame], poses[pelvis_frame])
    np.testing.assert_array_equal(poses[ankle.frame], poses[pelvis_frame])
    assert not np.array_equal(poses[ankle.frame], poses[pelvis_frame + 1])
    np.testing.assert_array_equal(poses[uwb_boundary.frame], poses[pelvis_frame - 1])
    with pytest.raises(PoseUnavailableError, match="NO_EXACT_VALID_NATIVE200_POSE"):
        owner.exact_tick(asynchronous_ankle_ns)


def test_swing_is_unpinned_and_fk_preserves_identity_and_bone_lengths():
    engine, packet = _engine_and_packet()
    result = engine.admit(packet, _epoch(engine, packet, with_contact=False))
    assert result.accepted and result.maximum_foothold_residual_m == 0.0
    points = corrected_proxy_points(
        {segment: np.eye(3) for segment in SEGMENTS},
        {
            segment: value.copy()
            for segment, value in result.segment_correction_rotvec.items()
        },
        engine.pose.geometry,
    )
    length = engine.pose.geometry.segment_length_m
    for proximal, distal, expected in (
        ("shoulder_left", "elbow_left", length["upper_arm_left"]),
        ("elbow_left", "wrist_left", length["forearm_left"]),
        ("shoulder_right", "elbow_right", length["upper_arm_right"]),
        ("elbow_right", "wrist_right", length["forearm_right"]),
        ("hip_left", "knee_left", length["thigh_left"]),
        ("knee_left", "ankle_left", length["shank_left"]),
        ("hip_right", "knee_right", length["thigh_right"]),
        ("knee_right", "ankle_right", length["shank_right"]),
    ):
        assert np.isclose(np.linalg.norm(points[distal] - points[proximal]), expected)
    for node, point in integration.NODE_TO_PROXY_POINT.items():
        np.testing.assert_allclose(
            result.node_position_m[node], result.root_position_m + points[point]
        )
    assert result.root_joint_cross_covariance_status == "UNAVAILABLE_NOT_PROPAGATED"


def test_published_readonly_rotvec_is_adapted_only_at_scipy_boundary():
    from scipy.spatial.transform import Rotation

    engine, packet = _engine_and_packet()
    result = engine.admit(packet, _epoch(engine, packet, with_contact=False))
    assert result.accepted
    published = result.segment_correction_rotvec
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    before_state = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    before_owner = {
        segment: {
            "bytes_sha256": hashlib.sha256(value.tobytes()).hexdigest(),
            "shape": value.shape,
            "dtype": value.dtype.str,
            "writeable": value.flags.writeable,
            "c_contiguous": value.flags.c_contiguous,
        }
        for segment, value in published.items()
    }
    assert all(not value.flags.writeable for value in published.values())
    with pytest.raises(ValueError, match="read-only"):
        Rotation.from_rotvec(next(iter(published.values())))

    writable_reference = {
        segment: np.array(value, dtype=float, order="C", copy=True)
        for segment, value in published.items()
    }
    expected = corrected_proxy_points(rotations, writable_reference, engine.pose.geometry)
    actual = corrected_proxy_points(rotations, published, engine.pose.geometry)
    for point in expected:
        np.testing.assert_array_equal(actual[point], expected[point])

    after_owner = {
        segment: {
            "bytes_sha256": hashlib.sha256(value.tobytes()).hexdigest(),
            "shape": value.shape,
            "dtype": value.dtype.str,
            "writeable": value.flags.writeable,
            "c_contiguous": value.flags.c_contiguous,
        }
        for segment, value in published.items()
    }
    after_state = (
        engine.root.publication_token().digest,
        engine.pose.publication_token().digest,
        engine.robust.snapshot(),
    )
    assert after_owner == before_owner
    assert after_state == before_state


def test_writable_and_noncontiguous_rotvec_paths_preserve_exact_fk_math():
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    writable = {segment: np.zeros(3) for segment in SEGMENTS}
    writable["forearm_left"][:] = [0.03, -0.02, 0.01]
    legacy = corrected_proxy_points(rotations, writable, _engine_and_packet()[0].pose.geometry)

    storage = {segment: np.zeros(6) for segment in SEGMENTS}
    noncontiguous = {segment: value[::2] for segment, value in storage.items()}
    noncontiguous["forearm_left"][:] = writable["forearm_left"]
    assert not noncontiguous["forearm_left"].flags.c_contiguous
    adapted = corrected_proxy_points(
        rotations, noncontiguous, _engine_and_packet()[0].pose.geometry
    )
    for point in legacy:
        np.testing.assert_array_equal(adapted[point], legacy[point])


def test_fk_rotvec_conversion_is_one_exact_batched_scipy_call(monkeypatch):
    import biospur_fusion.c2_uwb_calibration.articulated_range as articulated
    from scipy.spatial.transform import Rotation as SciPyRotation

    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    correction = {
        segment: np.array([0.01 * index, -0.003 * index, 0.002 * index])
        for index, segment in enumerate(SEGMENTS)
    }
    expected_delta = np.stack([
        SciPyRotation.from_rotvec(correction[segment]).as_matrix()
        for segment in SEGMENTS
    ])
    geometry = _engine_and_packet()[0].pose.geometry
    observed_shapes = []

    class ObservedRotation:
        @staticmethod
        def from_rotvec(value):
            observed_shapes.append(np.asarray(value).shape)
            return SciPyRotation.from_rotvec(value)

    monkeypatch.setattr(articulated, "Rotation", ObservedRotation)
    actual = corrected_proxy_points(
        rotations, correction, geometry, _batch_rotation_conversion=True
    )

    assert observed_shapes == [(len(SEGMENTS), 3)]
    np.testing.assert_array_equal(
        SciPyRotation.from_rotvec(np.stack(list(correction.values()))).as_matrix(),
        expected_delta,
    )
    assert all(np.isfinite(point).all() for point in actual.values())
