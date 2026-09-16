import pickle
import hashlib
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

import biospur_fusion.c2_coupled_progressive.prospective_action00_initializer as initializer_module
from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    ContinuousEvent,
    ImuTimer2Fields,
    NodeClockBinding,
    UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    AuthoritativeContinuousGroupComposition,
    AuthoritativeContinuousHistoryOwner,
)
from biospur_fusion.c2_coupled_progressive.prospective_action00_initializer import (
    Action00StationarityEvidence,
    CalibratedImuErrorStateOrigin,
    InitializationCovariancePolicy,
    InitializationStateOwner,
    ProspectiveAction00Initializer,
    ProspectiveInitializationPolicy,
)
from biospur_fusion.c2_coupled_progressive.continuous_session_initializer import (
    BootstrapPoseReadinessStatus,
    ContinuousSessionInitializer,
)
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import build_causal_links
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
)
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import encode_group
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA,
    ROOT_FREE_REACHABILITY_ROLE,
    RangeInformationOwner,
    ReachabilityPolicyEvidence,
    RootFreeReferenceOwnerTemplate,
    materialize_reference_owner,
)
from biospur_fusion.c2_uwb_root_world.split_fusion import FixedLagConsensusDriftConfig
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_group_epoch_owner import _real_owner_fixture


def _fixture(*, fail_pose_call=None):
    source, rows, availability_ns = _real_owner_fixture()
    engine = source._composition.engine
    frames = source._composition.history.frames
    calls = [0]

    def pose_factory():
        calls[0] += 1
        if calls[0] == fail_pose_call:
            raise RuntimeError("injected prospective pose construction")
        source = engine.pose
        return CausalArticulatedPose(
            action_start_s=source.action_start_s,
            action_stop_s=source.action_stop_s,
            rotations_at_fraction=source.rotations_at_fraction,
            geometry=source.geometry,
            hinge_projector=source.hinge_projector,
            derivative_period_s=source.derivative_period_s,
            transition_period_s=source.transition_period_s,
            hinge_temporal_retention_contract=source.hinge_temporal_retention_contract,
        )

    covariance_policy = InitializationCovariancePolicy(
        1e-6, 1.0, 0.25,
        "EXISTING_ACTION00_RAW_JACOBIAN_AND_DIAGNOSTIC_STATE_FLOORS",
    )
    stationarity = Action00StationarityEvidence(
        frames[0].source_global_ns, availability_ns + 1, "1" * 64,
        0.01, 0.05, "2" * 64, True,
    )
    bindings = tuple(NodeClockBinding(
        node, clock.boot_epoch, "B306_TIMER2",
        hashlib.sha256(f"mapping:{node}:{clock.a_ns_per_us}:{clock.b_ns}".encode()).hexdigest(),
        clock.a_ns_per_us, clock.b_ns,
        hashlib.sha256(f"owner:{node}".encode()).hexdigest(),
        hashlib.sha256(f"source:{node}".encode()).hexdigest(),
    ) for node, clock in sorted(engine.static.clocks.items()))
    clock_owner = ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, bindings)
    static_template = RootFreeReferenceOwnerTemplate(
        root_config=engine.static.root_config,
        inertial=engine.static.inertial,
        anchors_m=engine.static.anchors_m,
        clocks=engine.static.clocks,
        anchor_delay_m=engine.static.anchor_delay_m,
        tag_delay_m=engine.static.tag_delay_m,
        range_information=RangeInformationOwner(
            engine.static.range_information.nominal_sigma_m,
            engine.static.range_information.positive_nlos_cauchy_scale_m,
            engine.static.range_information.weights_by_node,
            "SOURCE_BOUND_ACTION00_RANGE_INFORMATION",
        ),
        reachability_evidence=ReachabilityPolicyEvidence(
            ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA,
            ROOT_FREE_REACHABILITY_ROLE,
            replace(
                engine.static.nominal_envelope,
                provenance="SOURCE_BOUND_ACTION00_REACHABILITY_POLICY",
            ),
            hashlib.sha256(b"structural reachability source artifact").hexdigest(),
            hashlib.sha256(b"structural reachability evidence owner").hexdigest(),
            # Synthetic typed evidence exercises only structural materialization.
            # No production adapter binds this test-owned source artifact.
            "PRODUCTION_QUALIFIED",
        ),
        trust_config=engine.static.trust_config,
        root_config_provenance="SOURCE_BOUND_ROOT_FILTER_CONFIG",
        anchor_provenance="SOURCE_BOUND_EIGHT_ANCHOR_LAYOUT",
        clock_provenance="SOURCE_BOUND_TEN_NODE_CLOCK_TABLE",
        guard_policy="SOURCE_BOUND_CAUSAL_GUARD_POLICY",
    )
    initializer = ProspectiveAction00Initializer(
        static_template=static_template, pose_factory=pose_factory,
        a_sigma_owner=source._composition.history.a_sigma_owner,
        b_sigma_owner=source._composition.history.b_sigma_owner,
        native200_clock_owner_sha256=engine.native200_clock_owner_sha256,
        native200_base_pose_owner_digest=engine.native200_base_pose_owner_digest,
        b_shadow_provenance="existing body-shadow geometry owner",
        history_provenance="source-owned prospective Action00 history",
        acquired_action_ids=frozenset(("00_initial_still", "02_t_pose")),
        policy=ProspectiveInitializationPolicy(),
        covariance_policy=covariance_policy,
        state_owner=InitializationStateOwner(
            stationarity,
            CalibratedImuErrorStateOrigin("3" * 64, "4" * 64, True),
            covariance_policy.digest,
        ),
        clock_owner=clock_owner,
    )
    events = []
    for row in rows:
        clock = engine.static.clocks[row.node]
        binding = clock_owner.binding_for(row.node)
        common_ns = int(round(clock.link_time_ns(
            event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=0.0,
        )))
        events.append(ContinuousEvent(
            f"bootstrap:{row.node}:{row.sequence}", "UWB", 0,
            "00_initial_still", common_ns, availability_ns, row.node, row.boot,
            binding.clock_domain, binding.clock_mapping_digest,
            binding.clock_owner_sha256, binding.clock_source_sha256,
            "host-only", row,
            uwb_timer2=UwbTimer2Fields(row.strobe_us, row.frame_us),
        ))
    return initializer, frames, tuple(events)


def _composition_fingerprint(composition):
    history = composition.history.snapshot()
    return (
        composition.engine.root.publication_token().digest,
        composition.engine.pose.publication_token().digest,
        pickle.dumps(composition.engine.robust.snapshot(), protocol=5),
        composition.engine.robust.revision,
        history.revision,
        tuple(frame.digest for frame in history.frames),
        tuple(frame.digest for frame in history.retired_frames),
        history.post_gap_bootstrap,
    )


def test_per_link_pose_readiness_survives_common_epoch_crossing_then_expires_exactly():
    initializer, _frames, events = _fixture()
    queries = []
    for event in events:
        row = event.payload_owner
        clock = initializer._static.clocks[row.node]
        queries.extend(clock.link_time_ns(
            event_boot_epoch=row.boot,
            strobe_us=row.strobe_us,
            t_round_us=float(value),
        ) for value in row.t_round_us)
    first_common = min(event.common_global_ns for event in events)
    first_query = min(queries)
    assert first_common < first_query

    initializer._frames = (
        SimpleNamespace(
            source_global_ns=int(first_query - MAXIMUM_POSE_AGE_NS - 2),
            digest="0" * 64,
        ),
        SimpleNamespace(
            source_global_ns=int(first_query - MAXIMUM_POSE_AGE_NS - 1),
            digest="1" * 64,
        ),
    )
    waiting = initializer.bootstrap_pose_readiness(events)
    assert waiting.status is BootstrapPoseReadinessStatus.RETRYABLE_POSE_NOT_READY
    assert first_query in waiting.unresolved_link_queries_ns

    initializer._frames = (
        SimpleNamespace(source_global_ns=first_common - 4_000_000, digest="1" * 64),
        SimpleNamespace(source_global_ns=first_common + 1, digest="2" * 64),
    )
    crossed = initializer.bootstrap_pose_readiness(events)
    assert crossed.status is BootstrapPoseReadinessStatus.READY
    assert crossed.unresolved_link_queries_ns == ()
    assert initializer._frames[-1].source_global_ns > first_common
    assert initializer._frames[-1].source_global_ns < first_query

    initializer._frames = (
        SimpleNamespace(
            source_global_ns=int(first_query - MAXIMUM_POSE_AGE_NS - 1),
            digest="3" * 64,
        ),
        SimpleNamespace(source_global_ns=int(first_query), digest="4" * 64),
    )
    missed = initializer.bootstrap_pose_readiness(events)
    assert missed.status is BootstrapPoseReadinessStatus.TERMINALLY_MISSED
    assert first_query in missed.unresolved_link_queries_ns


def test_full_session_initializer_has_no_action_interval_or_action00_bootstrap():
    old, frames, events = _fixture()
    generic = ContinuousSessionInitializer(
        static_template=old._static,
        pose_factory=old._pose_factory,
        a_sigma_owner=old._a_sigma,
        b_sigma_owner=old._b_sigma,
        native200_clock_owner_sha256=old._clock_sha,
        native200_base_pose_owner_digest=old._base_pose_digest,
        b_shadow_provenance=old._shadow_provenance,
        history_provenance="full-session label-free history",
        acquired_action_ids=frozenset(("FULL_SESSION_CONTINUOUS_00_TO_19",)),
        policy=old._policy,
        covariance_policy=old._covariance_policy,
        state_owner=InitializationStateOwner(
            None,
            old._state_owner.imu_origin,
            old._covariance_policy.digest,
        ),
        clock_owner=old._clock_owner,
    )
    for frame in frames:
        generic.accept_native200(frame)
    session_events = tuple(replace(
        event,
        action_index=-1,
        action_id="FULL_SESSION_CONTINUOUS_00_TO_19",
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    ) for event in events)
    located = generic.commit_first_group(generic.prepare_first_group(session_events))
    assert located.a.journal[0].member_regions == (
        "FULL_SESSION_CONTINUOUS_00_TO_19",
    ) * 10
    assert "00_initial_still" not in generic.owner_bytes().decode()


def test_full_session_initializer_enables_consensus_drift_only_for_b_without_changing_a():
    old, frames, events = _fixture()

    def make(*, drift=None):
        return ContinuousSessionInitializer(
            static_template=old._static,
            pose_factory=old._pose_factory,
            a_sigma_owner=old._a_sigma,
            b_sigma_owner=old._b_sigma,
            native200_clock_owner_sha256=old._clock_sha,
            native200_base_pose_owner_digest=old._base_pose_digest,
            b_shadow_provenance=old._shadow_provenance,
            history_provenance="full-session drift wiring smoke",
            acquired_action_ids=frozenset(("FULL_SESSION_CONTINUOUS_00_TO_19",)),
            policy=old._policy,
            covariance_policy=old._covariance_policy,
            state_owner=InitializationStateOwner(
                None, old._state_owner.imu_origin, old._covariance_policy.digest,
            ),
            clock_owner=old._clock_owner,
            b_consensus_drift=drift,
        )

    disabled = make()
    explicit_disabled = make(drift=None)
    assert disabled.owner_bytes() == explicit_disabled.owner_bytes()
    config = FixedLagConsensusDriftConfig(
        0.32, 0.72, 0.48, 4, 1e-2, 0.50, 1e-12, None,
    )
    template = ContinuousConsensusDriftOwner(
        config=config, stream_owner_digest="a" * 64,
    )
    nonpristine = template.clone()
    nonpristine.commit(nonpristine.prepare_gap())
    with pytest.raises(ValueError, match="must be pristine"):
        make(drift=nonpristine)
    enabled = make(drift=template)
    enabled_owner_bytes = enabled.owner_bytes()
    enabled_template_digest = enabled._b_consensus_drift.owner_digest
    template.commit(template.prepare_gap())
    assert enabled.owner_bytes() == enabled_owner_bytes
    assert enabled._b_consensus_drift.owner_digest == enabled_template_digest
    assert enabled._actions == frozenset(("FULL_SESSION_CONTINUOUS_00_TO_19",))
    session_events = tuple(replace(
        event,
        action_index=-1,
        action_id="FULL_SESSION_CONTINUOUS_00_TO_19",
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    ) for event in events)
    for frame in frames:
        disabled.accept_native200(frame)
        explicit_disabled.accept_native200(frame)
        enabled.accept_native200(frame)
    assert disabled.owner_bytes() == explicit_disabled.owner_bytes()
    baseline = disabled.commit_first_group(disabled.prepare_first_group(session_events))
    explicit = explicit_disabled.commit_first_group(
        explicit_disabled.prepare_first_group(session_events)
    )
    installed = enabled.commit_first_group(enabled.prepare_first_group(session_events))
    assert baseline.a._composition.consensus_drift is None
    assert explicit.a._composition.consensus_drift is None
    assert installed.a._composition.consensus_drift is None
    assert installed.b._composition.consensus_drift is not template
    assert installed.b._composition.consensus_drift.owner_digest == enabled_template_digest
    assert installed.b._composition.consensus_drift.config == config
    assert not (
        installed.a.mutable_owner_tokens() & installed.b.mutable_owner_tokens()
    )
    for lhs, rhs in (
        (baseline.a._composition.engine.root.current_state, explicit.a._composition.engine.root.current_state),
        (baseline.a._composition.engine.root.current_state, installed.a._composition.engine.root.current_state),
    ):
        assert lhs.time_s == rhs.time_s
        assert np.array_equal(lhs.vector, rhs.vector)
        assert np.array_equal(lhs.covariance, rhs.covariance)

    source = frames[-1]
    binding = old._clock_owner.binding_for(source.node)
    next_timer_us = source.source_timer_us + 5_000
    next_global_ns = binding.global_ns(next_timer_us)
    availability_lag_ns = (
        round(source.imu_sample.availability_time_s * 1e9)
        - source.source_global_ns
    )
    next_availability_ns = next_global_ns + availability_lag_ns
    next_frame = replace(
        source,
        source_timer_us=next_timer_us,
        source_global_ns=next_global_ns,
        publication_revision=source.publication_revision + 1,
        source_frame=source.source_frame + 1,
        action_id="FULL_SESSION_CONTINUOUS_00_TO_19",
        imu_sample=replace(
            source.imu_sample,
            measurement_time_s=next_global_ns * 1e-9,
            availability_time_s=next_availability_ns * 1e-9,
            source_sequence=source.imu_sample.source_sequence + 1,
        ),
        raw_provenance=replace(
            source.raw_provenance,
            record_index=source.raw_provenance.record_index + 1,
            start_offset=source.raw_provenance.end_offset,
            end_offset=source.raw_provenance.end_offset + 1,
            encoded_sha256="b" * 64,
            sample_index=0,
        ),
        digest="",
    )
    event = ContinuousEvent(
        "phase-c-small-record", "IMU", -1,
        "FULL_SESSION_CONTINUOUS_00_TO_19",
        next_global_ns, next_availability_ns, source.node, source.boot_epoch,
        "B306_TIMER2", next_frame.clock_mapping_digest,
        next_frame.clock_owner_sha256, next_frame.clock_source_sha256,
        "synthetic-small-record", next_frame,
        imu_timer2=ImuTimer2Fields(
            source.timer2_base_us, next_timer_us,
        ),
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    )
    baseline_plan = baseline.a.prepare_continuous_event(event, commit_uwb=False)
    installed_a_plan = installed.a.prepare_continuous_event(event, commit_uwb=False)
    installed_b_plan = installed.b.prepare_continuous_event(event, commit_uwb=False)
    baseline.a.commit_continuous_event(baseline_plan)
    installed.a.commit_continuous_event(installed_a_plan)
    installed.b.commit_continuous_event(installed_b_plan)
    baseline_state = baseline.a._composition.engine.root.current_state
    installed_a_state = installed.a._composition.engine.root.current_state
    installed_b_state = installed.b._composition.engine.root.current_state
    for state in (installed_a_state, installed_b_state):
        assert state.time_s == baseline_state.time_s
        assert np.array_equal(state.vector, baseline_state.vector)
        assert np.array_equal(state.covariance, baseline_state.covariance)
    assert installed.b._composition.consensus_drift.pending_count == 0


def test_full_session_initializer_keeps_bounded_tail_after_long_incomplete_prefix():
    old, frames, _events = _fixture()
    generic = ContinuousSessionInitializer(
        static_template=old._static,
        pose_factory=old._pose_factory,
        a_sigma_owner=old._a_sigma,
        b_sigma_owner=old._b_sigma,
        native200_clock_owner_sha256=old._clock_sha,
        native200_base_pose_owner_digest=old._base_pose_digest,
        b_shadow_provenance=old._shadow_provenance,
        history_provenance="full-session bounded preworld tail",
        acquired_action_ids=frozenset(("FULL_SESSION_CONTINUOUS_00_TO_19",)),
        policy=old._policy,
        covariance_policy=old._covariance_policy,
        state_owner=InitializationStateOwner(
            None, old._state_owner.imu_origin, old._covariance_policy.digest,
        ),
        clock_owner=old._clock_owner,
    )
    seed = frames[-1]
    synthetic = tuple(replace(
        seed,
        source_timer_us=seed.source_timer_us + 5_000 * (index + 1),
        source_global_ns=seed.source_global_ns + 5_000_000 * (index + 1),
        publication_revision=seed.publication_revision + index + 1,
        source_frame=seed.source_frame + index + 1,
        pose_publication_digest=f"{index + 1000:064x}",
        digest="",
        imu_sample=replace(
            seed.imu_sample,
            measurement_time_s=(
                seed.source_global_ns + 5_000_000 * (index + 1)
            ) * 1e-9,
            availability_time_s=(
                seed.source_global_ns + 5_000_000 * (index + 1)
            ) * 1e-9,
        ),
    ) for index in range(80))
    for frame in (*frames, *synthetic):
        generic.accept_native200(frame)
    assert len(generic._frames) == 52
    assert generic.preworld_pose_omissions == len(frames) + len(synthetic) - 52
    assert generic._frames[-1] is synthetic[-1]


def test_unlocated_holds_only_pose_chronology_then_installs_prior_free_shared_root():
    initializer, frames, events = _fixture()
    assert not hasattr(initializer._static, "initial_state")
    assert not hasattr(initializer._static, "pose_links")
    assert initializer._static.reachability_evidence.qualification_status == "PRODUCTION_QUALIFIED"
    assert initializer._static.production_qualified is True
    assert initializer.publication is None
    before = initializer.owner_bytes()
    for frame in frames:
        initializer.accept_native200(frame)
        assert initializer.publication is None
    assert initializer.owner_bytes() != before

    prepared = initializer.prepare_first_group(events)
    assert initializer.publication is None
    located = initializer.commit_first_group(prepared)
    assert initializer.publication is located
    assert located.direct_nodes == tuple(sorted(event.node_id for event in events))
    assert located.propagated_nodes == ()
    assert len(located.node_positions_world_m) == 10
    with pytest.raises(ValueError):
        located.root_state.vector[0] = 99.0
    with pytest.raises(ValueError):
        located.root_state.covariance[0, 0] = 99.0

    a = located.a._composition
    b = located.b._composition
    at = a.engine.root.publication_token()
    bt = b.engine.root.publication_token()
    assert at.digest == bt.digest
    assert at.state.vector.tobytes() == bt.state.vector.tobytes()
    assert at.state.covariance.tobytes() == bt.state.covariance.tobytes()
    assert not (located.a.mutable_owner_tokens() & located.b.mutable_owner_tokens())
    assert a.engine.pose.publication_token().digest == b.engine.pose.publication_token().digest
    assert pickle.dumps(a.engine.robust.snapshot(), protocol=5) == pickle.dumps(b.engine.robust.snapshot(), protocol=5)
    assert tuple(frame.digest for frame in a.history.frames) == tuple(frame.digest for frame in b.history.frames)
    assert not (a.mutable_owner_tokens() & b.mutable_owner_tokens())
    assert a.engine.static.digest == b.engine.static.digest
    assert len(a.engine.static.pose_links) == len(b.engine.static.pose_links) == 80
    assert a.engine.static.initial_state.vector.tobytes() == b.engine.static.initial_state.vector.tobytes()
    event_by_node = {event.node_id: event for event in events}
    for link in a.engine.static.pose_links:
        event = event_by_node[link.node]
        row = event.payload_owner
        expected_query_ns = initializer._static.clocks[link.node].link_time_ns(
            event_boot_epoch=row.boot,
            strobe_us=row.strobe_us,
            t_round_us=float(row.t_round_us[link.anchor]),
        )
        eligible = tuple(frame for frame in frames if frame.source_global_ns < expected_query_ns)
        expected_frame = eligible[-1]
        assert link.query_time_ns == expected_query_ns
        assert link.pose_time_ns == expected_frame.source_global_ns
        assert link.source_epoch == expected_frame.source_frame
        assert link.source_revision == expected_frame.publication_revision
        assert link.source_sha256 == expected_frame.pose_publication_digest
        assert np.array_equal(link.offset_world_m, expected_frame.offsets_world_m[link.node])
        assert np.array_equal(
            link.offset_velocity_world_mps,
            expected_frame.offset_velocities_world_mps[link.node],
        )
    current = frames[-1]
    for node, position in located.node_positions_world_m:
        assert np.array_equal(
            np.asarray(position), located.root_state.vector[:3] + current.offsets_world_m[node]
        )

    with pytest.raises(RuntimeError, match="STALE_REPLAYED_OR_FOREIGN"):
        initializer.commit_first_group(prepared)
    with pytest.raises(RuntimeError, match="ALREADY_LOCATED"):
        initializer.prepare_first_group(events)

    for branch in (located.a, located.b):
        composition_before = _composition_fingerprint(branch._composition)
        for event in events:
            plan = branch.prepare_continuous_event(event, commit_uwb=True)
            branch.commit_continuous_event(plan)
        assert branch.journal[-1].reason == "LATE_SEALED_BUCKET_ROW"
        assert _composition_fingerprint(branch._composition) == composition_before


def test_label_free_full_session_envelope_can_bootstrap_without_action_control():
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(replace(
            frame, action_id="FULL_SESSION_CONTINUOUS_00_TO_19", digest="",
        ))
    label_free = tuple(replace(
        event,
        action_index=-1,
        action_id="FULL_SESSION_CONTINUOUS_00_TO_19",
        region_id="FULL_SESSION_CONTINUOUS_00_TO_19",
    ) for event in events)
    located = initializer.commit_first_group(
        initializer.prepare_first_group(label_free)
    )
    assert located.a.journal[0].reason == "BOOTSTRAP_GROUP_CONSUMED"
    assert located.b.journal[0].reason == "BOOTSTRAP_GROUP_CONSUMED"
    assert located.direct_nodes == tuple(sorted(event.node_id for event in events))


@pytest.mark.parametrize("failure_call", (1, 2))
def test_prospective_participant_failure_is_exact_unlocated_noop(failure_call):
    initializer, frames, events = _fixture(fail_pose_call=failure_call)
    for frame in frames:
        initializer.accept_native200(frame)
    before = initializer.owner_bytes()
    with pytest.raises(RuntimeError, match="injected prospective pose"):
        initializer.prepare_first_group(events)
    assert initializer.owner_bytes() == before
    assert initializer.publication is None


def test_stale_prepare_after_new_source_frame_is_rejected_without_swap():
    initializer, frames, events = _fixture()
    for frame in frames[:-1]:
        initializer.accept_native200(frame)
    # The group is intentionally too late without the final strict-floor frame.
    with pytest.raises(ValueError, match="stale|too old"):
        initializer.prepare_first_group(events)
    assert initializer.publication is None


def test_reproduced_44m_selected_link_physical_rms_is_typed_inert_rejection():
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)
    bad = []
    for index, event in enumerate(events):
        row = replace(
            event.payload_owner,
            ranges_mm=tuple(100 if (anchor + index) % 2 else 65_000 for anchor in range(8)),
        )
        bad.append(replace(event, payload_owner=row))
    before = initializer.owner_bytes()
    outcome = initializer.prepare_first_group_outcome(tuple(bad))
    assert not outcome.accepted and outcome.prepared is None
    assert outcome.reason == "ROBUST_SELECTED_LINK_RESIDUAL_RMS_REJECTED"
    assert outcome.robust_candidate is not None
    assert outcome.robust_candidate.accepted
    assert outcome.robust_candidate.physical_residual_rms_m == pytest.approx(
        44.19055023423781, abs=1e-12,
    )
    assert outcome.robust_candidate.physical_residual_rms_m > 0.50
    assert initializer.owner_bytes() == before
    assert initializer.publication is None


def test_corrupt_node_is_excluded_and_nine_node_bootstrap_succeeds(monkeypatch):
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)
    corrupted = list(events)
    bad_node = corrupted[0].node_id
    corrupted[0] = replace(
        corrupted[0],
        payload_owner=replace(
            corrupted[0].payload_owner,
            ranges_mm=tuple(100 if anchor % 2 else 65_000 for anchor in range(8)),
        ),
    )
    availability_seen = []
    original_packet = initializer._bootstrap_packet

    def exact_ns_packet(engine, rows, **kwargs):
        assert "availability_time_s" not in kwargs
        assert type(kwargs["availability_global_ns"]) is int
        availability_seen.append(kwargs["availability_global_ns"])
        packet = original_packet(engine, rows, **kwargs)
        assert packet.availability_global_ns == kwargs["availability_global_ns"]
        return packet

    monkeypatch.setattr(initializer, "_bootstrap_packet", exact_ns_packet)
    before = initializer.owner_bytes()
    outcome = initializer.prepare_first_group_outcome(tuple(corrupted))
    assert outcome.accepted and outcome.robust_candidate is not None
    assert bad_node not in outcome.robust_candidate.trusted_nodes
    assert len(outcome.robust_candidate.trusted_nodes) == 9
    assert outcome.robust_candidate.physical_residual_rms_m <= 0.50
    assert availability_seen == [
        max(event.availability_global_ns for event in corrupted)
    ]
    all_links, _audit, _measurement, _availability = build_causal_links(
        tuple(event.payload_owner for event in corrupted),
        clocks=initializer._static.clocks,
        strict_floor_offset=initializer._strict_floor,
        anchor_delay_m=initializer._static.anchor_delay_m,
        tag_delay_m=initializer._static.tag_delay_m,
        sigma_for_quality=initializer._static.range_information.sigma,
    )
    all_link_fit = solve_shared_root(
        all_links, anchors_m=initializer._static.anchors_m,
        initial_root_m=outcome.robust_candidate.root_position_m,
    )
    assert np.sqrt(np.mean(np.square(all_link_fit.residuals_m))) > 0.50
    assert any(
        np.any(weights < 1.0)
        for _node, weights in outcome.robust_candidate.external_information_weights
    )
    assert initializer.owner_bytes() == before
    installed = initializer.commit_first_group(outcome.prepared)
    assert installed.direct_nodes == outcome.robust_candidate.trusted_nodes
    assert installed.a.mutable_owner_tokens().isdisjoint(installed.b.mutable_owner_tokens())


def test_all_link_seed_failure_cannot_block_valid_runtime_robust_candidate(monkeypatch):
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)

    all_link_calls = 0

    def reject_all_link_seed(*_args, **_kwargs):
        nonlocal all_link_calls
        all_link_calls += 1
        return SimpleNamespace(
            success=False, rank=0, condition=float("inf"),
        )

    monkeypatch.setattr(initializer_module, "solve_shared_root", reject_all_link_seed)
    before = initializer.owner_bytes()
    outcome = initializer.prepare_first_group_outcome(events)
    assert all_link_calls == 8
    assert outcome.accepted and outcome.prepared is not None
    assert outcome.robust_candidate is not None
    assert outcome.robust_candidate.accepted
    assert outcome.robust_candidate.physical_residual_rms_m <= 0.50
    assert initializer.owner_bytes() == before
    installed = initializer.commit_first_group(outcome.prepared)
    assert installed.direct_nodes == tuple(sorted(event.node_id for event in events))
    assert installed.a.mutable_owner_tokens().isdisjoint(installed.b.mutable_owner_tokens())


@pytest.mark.parametrize("mutation", (
    "action", "time", "availability", "boot", "domain", "digest", "source",
))
def test_foreign_or_forged_first_group_event_is_rejected_before_solve(mutation):
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)
    rows = list(events)
    if mutation == "action":
        rows[0] = replace(rows[0], action_index=2, action_id="02_t_pose")
    elif mutation == "time":
        rows[0] = replace(rows[0], common_global_ns=rows[0].common_global_ns + 1)
    elif mutation == "availability":
        rows[0] = replace(rows[0], availability_global_ns=rows[0].common_global_ns)
    elif mutation == "boot":
        rows[0] = replace(rows[0], boot_epoch=rows[0].boot_epoch + 1)
    elif mutation == "domain":
        rows[0] = replace(rows[0], clock_domain="FOREIGN_TIMER")
    elif mutation == "digest":
        rows[0] = replace(rows[0], clock_mapping_digest="9" * 64)
    else:
        rows[0] = replace(rows[0], clock_source_sha256="8" * 64)
    before = initializer.owner_bytes()
    with pytest.raises(ValueError, match="mismatch|precedes|differs"):
        initializer.prepare_first_group(tuple(rows))
    assert initializer.owner_bytes() == before


def test_clock_coefficients_must_match_static_owner_exactly():
    initializer, _frames, _events = _fixture()
    bindings = list(initializer._clock_owner.bindings)
    bindings[0] = replace(bindings[0], a_ns_per_us=bindings[0].a_ns_per_us + 1e-6)
    with pytest.raises(ValueError, match="continuous/static clock owner mismatch"):
        ProspectiveAction00Initializer(
            static_template=initializer._static,
            pose_factory=initializer._pose_factory,
            a_sigma_owner=initializer._a_sigma,
            b_sigma_owner=initializer._b_sigma,
            native200_clock_owner_sha256=initializer._clock_sha,
            native200_base_pose_owner_digest=initializer._base_pose_digest,
            b_shadow_provenance=initializer._shadow_provenance,
            history_provenance=initializer._history_provenance,
            acquired_action_ids=initializer._actions,
            policy=initializer._policy,
            covariance_policy=initializer._covariance_policy,
            state_owner=initializer._state_owner,
            clock_owner=ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings)),
        )


def test_policy_rejects_unowned_zero_state_and_arbitrary_broad_gates():
    with pytest.raises(ValueError, match="established prior-free policy"):
        ProspectiveInitializationPolicy(consensus_m=20.0)
    with pytest.raises(ValueError, match="established prior-free policy"):
        ProspectiveInitializationPolicy(residual_rms_m=100.0)
    with pytest.raises(ValueError, match="stationarity evidence"):
        Action00StationarityEvidence(1, 2, "1" * 64, 1.0, 0.1, "2" * 64, False)


def test_root_free_template_does_not_promote_unqualified_policy():
    initializer, _frames, _events = _fixture()
    unqualified_evidence = replace(
        initializer._static.reachability_evidence,
        qualification_status="MECHANISM_ONLY_UNQUALIFIED",
        digest="",
    )
    unqualified = replace(
        initializer._static,
        reachability_evidence=unqualified_evidence,
        digest="",
    )
    with pytest.raises(ValueError, match="mechanism-only and unqualified"):
        materialize_reference_owner(
            unqualified,
            initial_state=RootState(0.0, np.zeros(9), np.eye(9)),
            pose_links=(),
            initial_state_provenance="must fail before link validation",
        )


def test_deferred_bootstrap_packet_and_robust_candidate_match_legacy(monkeypatch):
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)
    actual = initializer_module.prepare_pristine_robust_bootstrap_candidate
    compared = []

    def compare(static, root, packet):
        state = root.publication_token().state
        engine, history = initializer._make_engine_history(state, static.pose_links)
        legacy = AuthoritativeContinuousGroupComposition(
            engine=engine, history=history,
            clone_factory=lambda: initializer._make_engine_history(
                state, static.pose_links,
            ),
        ).materialize_group(
            tuple(packet.event.payload),
            availability_global_ns=packet.availability_global_ns,
            member_region_identities=tuple(event.action_id for event in sorted(
                events, key=lambda item: item.node_id,
            )),
            evidence_class="ACTION_EVIDENCE",
        ).packet
        assert packet.digest == legacy.digest
        assert encode_group(packet) == encode_group(legacy)
        direct = actual(static, root, packet)
        oracle = actual(engine.static, engine.root, legacy)
        assert pickle.dumps(direct, protocol=5) == pickle.dumps(oracle, protocol=5)
        compared.append(packet.digest)
        return direct

    monkeypatch.setattr(
        initializer_module, "prepare_pristine_robust_bootstrap_candidate", compare,
    )
    assert initializer.prepare_first_group_outcome(events).accepted
    assert len(compared) == 1


def test_rejected_bootstrap_skips_pose_history_replay(monkeypatch):
    initializer, frames, events = _fixture()
    for frame in frames:
        initializer.accept_native200(frame)
    corrupted = tuple(replace(
        event, payload_owner=replace(
            event.payload_owner,
            ranges_mm=tuple(
                100 if (anchor + index) % 2 else 65_000
                for anchor in range(8)
            ),
        ),
    ) for index, event in enumerate(events))

    monkeypatch.setattr(
        AuthoritativeContinuousHistoryOwner, "from_prospective_frames",
        lambda *args, **kwargs: pytest.fail("rejected candidate replayed pose history"),
    )
    outcome = initializer.prepare_first_group_outcome(corrupted)
    assert not outcome.accepted
    assert outcome.reason == "ROBUST_SELECTED_LINK_RESIDUAL_RMS_REJECTED"
