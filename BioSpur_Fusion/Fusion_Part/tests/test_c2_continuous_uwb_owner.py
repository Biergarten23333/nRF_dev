import copy
from dataclasses import replace

import pytest

from biospur_fusion.ingest.events import EventStatus, RawByteProvenance, RecordType, TypedEvent

from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    ActionInterval,
    CAPTURE2_PHYSICAL_ACTIONS,
    CONTINUOUS_FRONTEND_SCHEMA,
    ContinuousClockOwner,
    ContinuousEvent,
    ImuTimer2Fields,
    NodeClockBinding,
    SourceBoundGap,
    UwbTimer2Fields,
)
from biospur_fusion.c2_coupled_progressive.continuous_uwb_owner import (
    Capture2ContinuousUwbCalibrationOwner,
    PreparedSubownerUpdate,
    SubownerResult,
)
from biospur_fusion.c2_coupled_progressive.continuous_stage2_adapter import (
    EventRegionOwner,
    adapt_verified_record,
)


class Recorder:
    def __init__(self, *, fail_commit=False):
        self.events = []
        self.gauge = {"generation": 7}
        self.fail_commit = fail_commit

    def clone(self):
        return copy.deepcopy(self)

    def mutable_owner_tokens(self):
        return frozenset((id(self), id(self.events), id(self.gauge)))

    def continuous_snapshot(self):
        return copy.deepcopy((self.events, self.gauge))

    def prepare_continuous_event(self, event, *, commit_uwb):
        result = SubownerResult(
            fixed_parameter_changed=event.kind == "UWB" and commit_uwb,
            time_varying_state_changed=True,
            gap_covariance_grown=event.kind == "GAP",
        )
        return PreparedSubownerUpdate(result, (event.event_id, event.kind, commit_uwb))

    def commit_continuous_event(self, prepared):
        self.events.append(prepared.token)
        if self.fail_commit:
            raise RuntimeError("injected subowner commit failure")

    def restore_continuous_snapshot(self, snapshot):
        self.events, self.gauge = copy.deepcopy(snapshot)


def clock():
    return ContinuousClockOwner(
        CONTINUOUS_FRONTEND_SCHEMA,
        (NodeClockBinding(
            "BSFC2CC", 3, "B306_TIMER2", "a" * 64,
            1000.0, 0.0, "c" * 64, "d" * 64,
        ),),
    )


def timeline():
    step = 1_000_000
    return tuple(
        ActionInterval(
            row.index, row.action_id, row.index * step, (row.index + 1) * step,
            end_inclusive=row.index == 19,
        )
        for row in CAPTURE2_PHYSICAL_ACTIONS if row.acquired
    )


def source_gaps():
    return (SourceBoundGap(
        "UNASSIGNED_INTER_ACTION_GAP", 1_000_000, 2_000_000,
        "00_initial_still", "02_t_pose", "left.json", "e" * 64,
        "right.json", "f" * 64,
    ),)


def event(kind, action_index, when, *, event_id=None, availability=None):
    action = CAPTURE2_PHYSICAL_ACTIONS[action_index]
    timer_us = int(when)
    common = timer_us * 1000
    availability = common + 10_000 if availability is None else int(availability)
    kwargs = {}
    if kind == "IMU":
        kwargs["imu_timer2"] = ImuTimer2Fields(max(0, timer_us - 5), timer_us)
    elif kind == "UWB":
        kwargs["uwb_timer2"] = UwbTimer2Fields(timer_us, timer_us + 8)
    else:
        action_index = -1
        action = type("Gap", (), {"action_id": "UNASSIGNED_INTER_ACTION_GAP"})()
        common = 2_000_000
        availability = max(2_000_000, availability)
        kwargs.update(
            gap_start_global_ns=1_000_000,
            gap_covariance_growth=0.25,
            region_id="UNASSIGNED_INTER_ACTION_GAP",
        )
    return ContinuousEvent(
        event_id or f"{action_index}-{kind}-{when}", kind,
        action_index, action.action_id, common, availability,
        "BSFC2CC", 3, "B306_TIMER2", "a" * 64, "c" * 64, "d" * 64,
        "host-label-not-ordering", object(), **kwargs,
    )


def owner(recorder=None):
    recorder = recorder or Recorder()
    return Capture2ContinuousUwbCalibrationOwner(
        clock_owner=clock(), action_intervals=timeline(),
        source_gaps=source_gaps(),
        subowners=(("existing-estimator", recorder),),
    ), recorder


def test_manifest_accounts_for_all_physical_actions_and_skipped_01():
    assert len(CAPTURE2_PHYSICAL_ACTIONS) == 20
    assert [row.index for row in CAPTURE2_PHYSICAL_ACTIONS] == list(range(20))
    assert CAPTURE2_PHYSICAL_ACTIONS[1].action_id == "01_neutral_sway"
    assert CAPTURE2_PHYSICAL_ACTIONS[1].acquired is False
    assert sum(row.acquired for row in CAPTURE2_PHYSICAL_ACTIONS) == 19


def test_one_owner_crosses_19_boundaries_without_resetting_state_or_gauge():
    session, recorder = owner()
    fixed = []
    states = []
    for index, action in enumerate(CAPTURE2_PHYSICAL_ACTIONS):
        session.enter_action(
            action.action_id, common_global_ns=index * 1_000_000,
            availability_global_ns=index * 1_000_000 + 1,
        )
        if action.acquired:
            session.ingest(event("IMU", index, 102 + index * 1000))
        elif index == 1:
            continue
        fixed.append(session.snapshot().fixed_parameter_revision)
        states.append(session.snapshot().time_varying_state_revision)
    session.ingest(event("GAP", 1, 1100, availability=20_000_001))
    finished = session.finish()
    assert finished.action_index == 19
    assert len(finished.action_boundaries) == 19
    assert recorder.gauge == {"generation": 7}
    assert all(a <= b for a, b in zip(fixed, fixed[1:]))
    assert all(a <= b for a, b in zip(states, states[1:]))
    assert len({id(session)}) == 1


def test_events_use_source_clock_fields_and_host_label_cannot_reorder():
    session, _ = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.ingest(replace(event("IMU", 0, 10), host_time_label="host-9999"))
    session.ingest(replace(event("UWB", 0, 20), host_time_label="host-minus-one"))
    assert session.snapshot().last_availability_global_ns == 30_000
    assert clock().imu_trigger_field == "trigger_timer2_us"
    assert clock().uwb_strobe_field == "strobe_timer2_us"


def test_gap_only_grows_covariance_and_never_interpolates_or_resets():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action("01_neutral_sway", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    session.enter_action("02_t_pose", common_global_ns=2_000_000, availability_global_ns=2_000_001)
    before = dict(recorder.gauge)
    session.ingest(event("GAP", 0, 20, availability=2_100_000))
    assert recorder.gauge == before
    assert session.snapshot().time_varying_state_revision == 1
    assert recorder.events[-1][1] == "GAP"


@pytest.mark.parametrize("field,value", [
    ("boot_epoch", 4),
    ("clock_domain", "HOST"),
    ("clock_mapping_digest", "b" * 64),
    ("node_id", "UNKNOWN"),
])
def test_clock_identity_mismatch_fails_before_subowner(field, value):
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    with pytest.raises(ValueError, match="clock|node"):
        session.ingest(replace(event("IMU", 0, 10), **{field: value}))
    assert recorder.events == []
    assert session.snapshot().event_count == 0


def test_missing_duplicate_and_reordered_actions_fail_closed():
    session, _ = owner()
    with pytest.raises(ValueError, match="missing.*reordered"):
        session.enter_action("01_neutral_sway", common_global_ns=0, availability_global_ns=1)
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    with pytest.raises(ValueError, match="missing.*reordered"):
        session.enter_action("00_initial_still", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    with pytest.raises(ValueError, match="missing.*reordered"):
        session.enter_action("02_t_pose", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    with pytest.raises(ValueError, match="missing physical"):
        session.finish()


def test_event_identity_and_both_time_axes_are_monotonic():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    first = event("IMU", 0, 10, event_id="same", availability=20_000)
    session.ingest(first)
    for poisoned, match in (
        (replace(event("IMU", 0, 11), event_id="same"), "duplicate"),
        (event("IMU", 0, 9, availability=13_000), "order reversed"),
        (event("IMU", 0, 11, availability=11_000), "order reversed"),
    ):
        with pytest.raises(ValueError, match=match):
            session.ingest(poisoned)
    assert len(recorder.events) == 1


def test_ab_fork_clones_one_pre00_state_and_only_uwb_commit_policy_differs():
    seed_recorder = Recorder()
    seed, _ = owner(seed_recorder)
    pair = seed.fork_ab()
    left_recorder = pair.without_uwb_commits.subowners[0][1]
    right_recorder = pair.with_uwb_commits.subowners[0][1]
    assert left_recorder is not right_recorder and left_recorder is not seed_recorder
    assert right_recorder is not seed_recorder

    for branch in (pair.without_uwb_commits, pair.with_uwb_commits):
        branch.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
        branch.ingest(event("IMU", 0, 10))
        branch.ingest(event("UWB", 0, 20))
    assert left_recorder.events[:-1] == right_recorder.events[:-1]
    assert left_recorder.events[-1][:2] == right_recorder.events[-1][:2]
    assert left_recorder.events[-1][2] is False
    assert right_recorder.events[-1][2] is True
    assert pair.without_uwb_commits.snapshot().fixed_parameter_revision == 0
    assert pair.with_uwb_commits.snapshot().fixed_parameter_revision == 1
    left_recorder.gauge["generation"] = 99
    assert right_recorder.gauge["generation"] == 7
    assert seed_recorder.gauge["generation"] == 7
    with pytest.raises(ValueError, match="pre-00"):
        pair.with_uwb_commits.fork_ab()


def test_delayed_prior_action_uwb_dispatches_by_availability_not_measurement():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action("01_neutral_sway", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    session.enter_action("02_t_pose", common_global_ns=2_000_000, availability_global_ns=2_000_001)
    session.ingest(event("IMU", 2, 2100, availability=2_110_000))
    delayed = event("UWB", 0, 50, availability=2_120_000)
    session.ingest(delayed)
    assert recorder.events[-1][0] == delayed.event_id
    assert session.snapshot().protocol_markers[0].action_id == "01_neutral_sway"
    assert session.snapshot().protocol_markers[0].synthetic_data_created is False


def test_source_mapping_coefficients_and_owner_shas_are_exactly_enforced():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    valid = event("UWB", 0, 50)
    for poisoned in (
        replace(valid, common_global_ns=valid.common_global_ns + 1),
        replace(valid, clock_owner_sha256="e" * 64),
        replace(valid, clock_source_sha256="f" * 64),
        replace(valid, uwb_timer2=UwbTimer2Fields(50, 5000)),
    ):
        with pytest.raises(ValueError, match="mapping|owner|source|frame"):
            session.ingest(poisoned)
    assert recorder.events == []


def test_tie_break_and_per_source_timer_cursors_fail_closed():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    first = event("IMU", 0, 10, event_id="z", availability=20_000)
    session.ingest(first)
    with pytest.raises(ValueError, match="tie-break"):
        session.ingest(event("IMU", 0, 10, event_id="a", availability=20_000))
    with pytest.raises(ValueError, match="TIMER2"):
        session.ingest(event("IMU", 0, 9, availability=30_000))
    assert len(recorder.events) == 1


def test_second_subowner_commit_failure_rolls_back_every_owner_and_session():
    first = Recorder()
    second = Recorder(fail_commit=True)
    session = Capture2ContinuousUwbCalibrationOwner(
        clock_owner=clock(), action_intervals=timeline(), source_gaps=source_gaps(),
        subowners=(("first", first), ("second", second)),
    )
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    before = session.snapshot()
    first_before = first.continuous_snapshot()
    second_before = second.continuous_snapshot()
    with pytest.raises(RuntimeError, match="injected"):
        session.ingest(event("IMU", 0, 10))
    assert session.snapshot() == before
    assert first.continuous_snapshot() == first_before
    assert second.continuous_snapshot() == second_before


def test_action_interval_label_and_boundary_availability_poison_fail_closed():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.ingest(event("IMU", 0, 900, availability=1_100_000))
    with pytest.raises(ValueError, match="behind dispatched availability"):
        session.enter_action(
            "01_neutral_sway", common_global_ns=1_000_000,
            availability_global_ns=1_050_000,
        )
    assert session.snapshot().action_index == 0
    mislabeled = replace(
        event("UWB", 0, 1100, availability=1_200_000),
        action_index=0, action_id="00_initial_still",
    )
    with pytest.raises(ValueError, match="does not own"):
        session.ingest(mislabeled)
    assert len(recorder.events) == 1


def test_final_interval_has_explicit_closed_end_and_no_future_inference():
    session, recorder = owner()
    for row in CAPTURE2_PHYSICAL_ACTIONS:
        session.enter_action(
            row.action_id, common_global_ns=row.index * 1_000_000,
            availability_global_ns=row.index * 1_000_000 + 1,
        )
    session.ingest(event("GAP", 1, 1100, availability=19_000_002))
    final = event("IMU", 19, 20_000, availability=20_000_001)
    session.ingest(final)
    assert session.finish().event_count == 2
    with pytest.raises(ValueError, match="does not own"):
        session.ingest(event("IMU", 19, 20_001, availability=20_001_001))


def test_ab_rejects_new_wrappers_around_shared_nested_mutable_state():
    shared_events = []

    class SharedNestedRecorder(Recorder):
        def __init__(self, events):
            super().__init__()
            self.events = events

        def clone(self):
            return SharedNestedRecorder(self.events)

    seed = Capture2ContinuousUwbCalibrationOwner(
        clock_owner=clock(), action_intervals=timeline(), source_gaps=source_gaps(),
        subowners=(("shared-wrapper", SharedNestedRecorder(shared_events)),),
    )
    with pytest.raises(ValueError, match="nested mutable ownership"):
        seed.fork_ab()


def test_skipped_action_rejects_imu_and_delayed_uwb_before_subowner():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action(
        "01_neutral_sway", common_global_ns=1_000_000,
        availability_global_ns=1_000_001,
    )
    with pytest.raises(ValueError, match="unacquired protocol slot"):
        session.ingest(event("IMU", 1, 1100))
    session.enter_action(
        "02_t_pose", common_global_ns=2_000_000,
        availability_global_ns=2_000_001,
    )
    with pytest.raises(ValueError, match="unacquired protocol slot"):
        session.ingest(event("UWB", 1, 1200, availability=2_100_000))
    assert recorder.events == []
    assert session.snapshot().event_count == 0


def test_unassigned_gap_explicit_elapsed_event_only_grows_uncertainty_without_uwb_commit():
    session, recorder = owner()
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action(
        "01_neutral_sway", common_global_ns=1_000_000,
        availability_global_ns=1_000_001,
    )
    session.enter_action(
        "02_t_pose", common_global_ns=2_000_000,
        availability_global_ns=2_000_001,
    )
    session.ingest(event("GAP", 1, 1100, availability=2_100_000))
    snapshot = session.snapshot()
    assert recorder.events == [("-1-GAP-1100", "GAP", False)]
    assert snapshot.fixed_parameter_revision == 0
    assert snapshot.time_varying_state_revision == 1
    assert snapshot.protocol_marker_and_gap_accounted is True


@pytest.mark.parametrize("fixed,state", [(True, True), (False, False), (True, False)])
def test_gap_rejects_any_fixed_or_time_varying_state_change_before_commit(fixed, state):
    class PoisonGapRecorder(Recorder):
        def prepare_continuous_event(self, event, *, commit_uwb):
            prepared = super().prepare_continuous_event(event, commit_uwb=commit_uwb)
            return replace(
                prepared,
                result=replace(
                    prepared.result,
                    fixed_parameter_changed=fixed,
                    time_varying_state_changed=state,
                ),
            )

    recorder = PoisonGapRecorder()
    session, _ = owner(recorder)
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action(
        "01_neutral_sway", common_global_ns=1_000_000,
        availability_global_ns=1_000_001,
    )
    session.enter_action(
        "02_t_pose", common_global_ns=2_000_000,
        availability_global_ns=2_000_001,
    )
    before = session.snapshot()
    owner_before = recorder.continuous_snapshot()
    with pytest.raises(ValueError, match="time-varying covariance|cannot mutate fixed"):
        session.ingest(event("GAP", 1, 1100, availability=2_100_000))
    assert session.snapshot() == before
    assert recorder.continuous_snapshot() == owner_before


def decoded(record_type, timer_us, *, base_us=None, frame_us=None):
    payload = (
        {"base_timer2_us": timer_us - 5 if base_us is None else base_us}
        if record_type is RecordType.IMU
        else {"strobe_us": timer_us, "frame_us": timer_us + 8 if frame_us is None else frame_us}
    )
    return TypedEvent(
        "BSFC2CC", 3, record_type, 9, timer_us, None, None, 999999,
        payload, {}, EventStatus.DECODED,
        RawByteProvenance(7, 100, 200, "9" * 64),
    )


def test_stateless_adapter_uses_supplied_region_and_clock_not_host_time():
    action = timeline()[0]
    imu = adapt_verified_record(
        decoded(RecordType.IMU, 500), availability_global_ns=700_000,
        region_owner=EventRegionOwner(action=action), clock_owner=clock(),
    )
    assert (imu.action_index, imu.common_global_ns, imu.availability_global_ns) == (0, 500_000, 700_000)
    assert imu.host_time_label == "999999"
    gap_uwb = adapt_verified_record(
        decoded(RecordType.UWB, 1500), availability_global_ns=1_600_000,
        region_owner=EventRegionOwner(gap=source_gaps()[0]), clock_owner=clock(),
    )
    assert gap_uwb.region_id == "UNASSIGNED_INTER_ACTION_GAP"
    assert gap_uwb.action_index == -1


def test_stateless_adapter_rejects_result_time_and_region_inference_poisons():
    record = decoded(RecordType.IMU, 500)
    for poisoned, match in (
        (replace(record, global_time_ns=500_001), "disagrees"),
        (replace(record, raw=None), "provenance"),
        (replace(record, status=EventStatus.ACCEPTED), "decoded"),
    ):
        with pytest.raises(ValueError, match=match):
            adapt_verified_record(
                poisoned, availability_global_ns=700_000,
                region_owner=EventRegionOwner(action=timeline()[0]), clock_owner=clock(),
            )
    with pytest.raises(ValueError, match="does not own"):
        adapt_verified_record(
            record, availability_global_ns=700_000,
            region_owner=EventRegionOwner(action=timeline()[1]), clock_owner=clock(),
        )


def test_gap_sensor_dynamic_update_allowed_but_fixed_or_action_prior_rejected():
    class GapDynamicRecorder(Recorder):
        def prepare_continuous_event(self, event, *, commit_uwb):
            result = SubownerResult(False, True, action_specific_prior_used=False)
            return PreparedSubownerUpdate(result, (event.event_id, event.kind, commit_uwb))

    session, recorder = owner(GapDynamicRecorder())
    session.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    session.enter_action("01_neutral_sway", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    gap_imu = adapt_verified_record(
        decoded(RecordType.IMU, 1500), availability_global_ns=1_600_000,
        region_owner=EventRegionOwner(gap=source_gaps()[0]), clock_owner=clock(),
    )
    session.ingest(gap_imu)
    assert session.snapshot().time_varying_state_revision == 1
    assert recorder.events[-1][1:] == ("IMU", False)

    class PriorPoison(GapDynamicRecorder):
        def prepare_continuous_event(self, event, *, commit_uwb):
            item = super().prepare_continuous_event(event, commit_uwb=commit_uwb)
            return replace(item, result=replace(item.result, action_specific_prior_used=True))

    poisoned, poison_recorder = owner(PriorPoison())
    poisoned.enter_action("00_initial_still", common_global_ns=0, availability_global_ns=1)
    poisoned.enter_action("01_neutral_sway", common_global_ns=1_000_000, availability_global_ns=1_000_001)
    with pytest.raises(ValueError, match="action priors"):
        poisoned.ingest(gap_imu)
    assert poison_recorder.events == []
