from __future__ import annotations

from dataclasses import replace
import numpy as np
import pytest

from biospur_fusion.c2_coupled_progressive.authenticated_vqf_tilt_join import (
    AuthenticatedVQFTiltClockJoin,
)
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA, ActionInterval, ContinuousClockOwner,
    NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner, adapt_verified_record,
)
from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent
from biospur_fusion.v0.c2_progressive.orientation import ContinuousVQFState
from biospur_fusion.v0.c2_progressive.pipeline_runtime import _runtime_vqf_tilt_authority
from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction, IMU_DTYPE
from biospur_fusion.c2_uwb_root_world.gap_tilt_recovery import (
    CausalTiltTrustStateMachine, GapTiltRecoveryConfig,
    TiltDiagnosticIssuerBinding, TiltEvidenceStatus, TiltTrustFrameEvidence,
)
from biospur_fusion.root_r3 import CausalDelayedRootFilter, RootFilterConfig, RootState


class _Guard:
    capture_id = "C2"
    def bind_vqf_instance(self, node, instance): pass
    def begin_episode(self, index, action): pass


def _fixture(count=8, sequence_start=10, same_record=False):
    node = "BSFC2CC"; boot = 3
    rows = np.zeros(count, dtype=IMU_DTYPE)
    rows["derived_boot_epoch"] = boot
    rows["imu_sample_sequence"] = (
        sequence_start + np.arange(count, dtype=np.int64)
    ) % 65536
    rows["node_timer_us"] = 1_000_000 + np.arange(count) * 5_000
    rows["acc_raw"][:, 2] = 2048
    if same_record:
        rows["raw_start_offset"] = 20_000
        rows["raw_end_offset"] = 20_256
    else:
        rows["raw_start_offset"] = 20_000 + np.arange(count) * 32
        rows["raw_end_offset"] = rows["raw_start_offset"] + 32
    rows["raw_sample_index"] = np.arange(count)
    rows["decode_acceptance_status"] = 1
    initial = {"nodes": {node: {
        "gyro_bias_rad_s": [0, 0, 0],
        "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-8).tolist(),
        "gyro_observation_covariance_rad2_s2": (np.eye(3) * 1e-7).tolist(),
        "accelerometer_norm_mps2": 9.80665,
        "accelerometer_observation_covariance_m2_s4": (np.eye(3) * 1e-4).tolist(),
    }}}
    settings = {"execution_contract": {"initial_stochastic_state_relative_path": "initial.json"}}
    authority, capability = _runtime_vqf_tilt_authority(
        seal_authority={"seal_sha256": "1"*64, "qualified_source_hashes": {"initial.json": "2"*64}},
        settings=settings, initial_semantic_sha256="3"*64, settings_semantic_sha256="4"*64,
    )
    state = ContinuousVQFState(
        initial, execution_guard=_Guard(), unknown_boot_orientation_sigma_rad=1.0,
        unknown_unusable_episode_orientation_sigma_rad=.5,
        tilt_diagnostic_runtime_authority=authority,
        _tilt_provenance_capability=capability,
    )
    oriented = state.process(DecodedAction(
        "00_initial_still", 0, (0, 1), {node: rows},
        {"slice_sha256": "5"*64}, {"decoder": "fixture"},
    ))
    binding = NodeClockBinding(node, boot, "B306_TIMER2", "6"*64, 1000.0, 0.0, "7"*64, "8"*64)
    clocks = ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, (binding,))
    action = ActionInterval(0, "00_initial_still", 0, 10_000_000_000)
    return oriented, rows, clocks, action


def _event(rows, clocks, action, index, *, record_index=None):
    raw = RawByteProvenance(
                            100 + index if record_index is None else record_index,
                            int(rows["raw_start_offset"][index]),
                            int(rows["raw_end_offset"][index]), "9"*64,
                            int(rows["raw_sample_index"][index]))
    record = TypedEvent(
        "BSFC2CC", 3, RecordType.IMU, int(rows["imu_sample_sequence"][index]),
        int(rows["node_timer_us"][index]), None, None, 0,
        {"base_timer2_us": int(rows["node_timer_us"][index]), "delta_us": 0},
        {}, EventStatus.DECODED, raw,
    )
    common = clocks.binding_for("BSFC2CC").global_ns(record.node_timer_us)
    return adapt_verified_record(record, availability_global_ns=common + 1_000_000,
                                 region_owner=EventRegionOwner(action=action), clock_owner=clocks)


def test_valid_join_is_unqualified_and_stream_prefix_equal():
    oriented, rows, clocks, action = _fixture()
    digest = oriented.vqf_tilt_diagnostic_provenance.digest
    batch = AuthenticatedVQFTiltClockJoin(clock_owner=clocks, diagnostic_provenance_digest=digest)
    streamed = AuthenticatedVQFTiltClockJoin(clock_owner=clocks, diagnostic_provenance_digest=digest)
    batch_rows = [batch.commit(batch.prepare(oriented, event=_event(rows, clocks, action, i), index=i)) for i in range(4)]
    stream_rows = []
    for i in range(2): stream_rows.append(streamed.commit(streamed.prepare(oriented, event=_event(rows, clocks, action, i), index=i)))
    for i in range(2, 4): stream_rows.append(streamed.commit(streamed.prepare(oriented, event=_event(rows, clocks, action, i), index=i)))
    assert [row.digest for row in batch_rows] == [row.digest for row in stream_rows]
    assert batch.owner_bytes() == streamed.owner_bytes()
    assert all(
        row.qualification == "UNQUALIFIED_DIAGNOSTIC"
        and row.recovery_evidence.status.value == "MISSING"
        and row.recovery_evidence.tilt_error_rad is None
        for row in batch_rows
    )


@pytest.mark.parametrize("mutation", ["clock", "boot", "sequence", "offset", "future", "replay", "status", "base"])
def test_foreign_stale_and_future_rows_are_atomic_noops(mutation):
    oriented, rows, clocks, action = _fixture()
    owner = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks,
        diagnostic_provenance_digest=oriented.vqf_tilt_diagnostic_provenance.digest,
    )
    first = _event(rows, clocks, action, 0)
    owner.commit(owner.prepare(oriented, event=first, index=0))
    event = _event(rows, clocks, action, 1)
    index = 1
    if mutation == "clock": event = replace(event, clock_mapping_digest="a"*64)
    elif mutation == "boot": event = replace(event, boot_epoch=4)
    elif mutation == "sequence": event = replace(event, payload_owner=replace(event.payload_owner, sequence=99))
    elif mutation == "offset":
        changed = replace(event.payload_owner.raw, start_offset=123)
        event = replace(event, payload_owner=replace(event.payload_owner, raw=changed))
    elif mutation == "future":
        before = owner.owner_bytes()
        with pytest.raises(ValueError, match="availability precedes"):
            replace(event, availability_global_ns=event.common_global_ns-1)
        assert owner.owner_bytes() == before
        return
    elif mutation == "replay": event, index = first, 0
    elif mutation == "status": event = replace(event, payload_owner=replace(event.payload_owner, status=EventStatus.REJECTED))
    elif mutation == "base":
        payload = dict(event.payload_owner.payload); payload["base_timer2_us"] -= 1
        event = replace(event, payload_owner=replace(event.payload_owner, payload=payload))
    before = owner.owner_bytes()
    with pytest.raises((ValueError, TypeError)):
        owner.prepare(oriented, event=event, index=index)
    assert owner.owner_bytes() == before


def test_missing_or_mutated_diagnostic_is_rejected_without_join_state():
    oriented, rows, clocks, action = _fixture()
    owner = AuthenticatedVQFTiltClockJoin(clock_owner=clocks, diagnostic_provenance_digest="a"*64)
    before = owner.owner_bytes()
    with pytest.raises(ValueError, match="provenance"):
        owner.prepare(oriented, event=_event(rows, clocks, action, 0), index=0)
    assert owner.owner_bytes() == before
    with pytest.raises(ValueError):
        oriented.world_tilt_innovation_rad_by_node["BSFC2CC"][0] = 3.0


def test_uint16_sequence_wrap_is_identity_not_chronology():
    oriented, rows, clocks, action = _fixture(count=3, sequence_start=65535)
    owner = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks,
        diagnostic_provenance_digest=oriented.vqf_tilt_diagnostic_provenance.digest,
    )
    first = owner.commit(owner.prepare(oriented, event=_event(rows, clocks, action, 0), index=0))
    second = owner.commit(owner.prepare(oriented, event=_event(rows, clocks, action, 1), index=1))
    assert first.source_sequence == 65535 and second.source_sequence == 0
    assert second.common_global_ns > first.common_global_ns


def test_same_batch_availability_may_tie_but_must_never_go_backwards():
    oriented, rows, clocks, action = _fixture(count=3, same_record=True)
    digest = oriented.vqf_tilt_diagnostic_provenance.digest
    owner = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks, diagnostic_provenance_digest=digest,
    )
    first_event = _event(rows, clocks, action, 0, record_index=100)
    second_event = _event(rows, clocks, action, 1, record_index=100)
    shared_availability = second_event.common_global_ns + 10_000_000
    first_event = replace(first_event, availability_global_ns=shared_availability)
    second_event = replace(second_event, availability_global_ns=shared_availability)
    first = owner.commit(owner.prepare(oriented, event=first_event, index=0))
    second = owner.commit(owner.prepare(oriented, event=second_event, index=1))
    assert first.availability_global_ns == second.availability_global_ns
    assert first.common_global_ns < second.common_global_ns

    assert first_event.payload_owner.raw.start_offset == second_event.payload_owner.raw.start_offset
    assert first_event.payload_owner.raw.sample_index != second_event.payload_owner.raw.sample_index
    third_event = _event(rows, clocks, action, 2, record_index=100)
    third_event = replace(
        third_event, availability_global_ns=shared_availability - 1,
    )
    before = owner.owner_bytes()
    with pytest.raises(ValueError, match="stale or replayed"):
        owner.prepare(oriented, event=third_event, index=2)
    assert owner.owner_bytes() == before


def test_mutated_base_time_is_rejected_by_source_binding_without_state_change():
    oriented, rows, clocks, action = _fixture()
    owner = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks,
        diagnostic_provenance_digest=oriented.vqf_tilt_diagnostic_provenance.digest,
    )
    oriented.time_us_by_node["BSFC2CC"][0] += 1
    before = owner.owner_bytes()
    with pytest.raises(ValueError):
        owner.prepare(oriented, event=_event(rows, clocks, action, 0), index=0)
    assert owner.owner_bytes() == before


def test_unqualified_missing_payload_does_not_touch_gap_tilt_or_root():
    oriented, rows, clocks, action = _fixture()
    join = AuthenticatedVQFTiltClockJoin(
        clock_owner=clocks,
        diagnostic_provenance_digest=oriented.vqf_tilt_diagnostic_provenance.digest,
    )
    joined = join.commit(join.prepare(oriented, event=_event(rows, clocks, action, 0), index=0))
    issuer = TiltDiagnosticIssuerBinding(
        "BSFC2CC", 3, "B306_TIMER2", "6"*64, "a"*64, "b"*64, "c"*64, .05,
    )
    initial = TiltTrustFrameEvidence(
        "initial", .5, .5, 0, "BSFC2CC", 3, "0", "B306_TIMER2", "6"*64,
        "a"*64, TiltEvidenceStatus.TRUSTED, .01, .05, "b"*64, issuer.digest,
    )
    trust = CausalTiltTrustStateMachine(GapTiltRecoveryConfig(.01, 3), issuer, initial)
    root = CausalDelayedRootFilter(
        RootState(.5, np.zeros(9), np.eye(9)), RootFilterConfig(), inertial=True,
    )
    trust_before = trust.owner_bytes()
    root_before = (root.publication_token().digest, root.current_state.vector.tobytes(),
                   root.current_state.covariance.tobytes())
    assert joined.recovery_evidence.status is TiltEvidenceStatus.MISSING
    assert trust.owner_bytes() == trust_before
    assert (root.publication_token().digest, root.current_state.vector.tobytes(),
            root.current_state.covariance.tobytes()) == root_before
