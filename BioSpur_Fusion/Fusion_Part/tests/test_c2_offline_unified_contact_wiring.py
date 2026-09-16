from __future__ import annotations

from types import SimpleNamespace
import pickle
from functools import partial

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world import offline_unified_contact_wiring as u4
from biospur_fusion.c2_uwb_root_world.ankle_contact import (
    AnkleContactConfig, AnkleContactDetector, DualFootFootholdCorrector,
    FootStillnessProfile, FootSupportState,
)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    ReachabilityClass, ReachabilityEnvelope,
)
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample, PositionObservation, RootState
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as pose_module
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS


class _Pose:
    def __init__(self):
        self.install_count = 0
        self.calls = 0
        self._digest = "0"
    def publication_token(self):
        return SimpleNamespace(digest=self._digest)
    def sample(self, time_s):
        self.calls += 1
        self._digest = str(self.calls)
        offsets = {"left": np.array([-.1, 0., -.9]), "right": np.array([.1, 0., -.9])}
        return SimpleNamespace(
            time_s=time_s, ankle_offset_world_m=offsets,
            ankle_offset_velocity_world_mps={side: np.zeros(3) for side in offsets},
            hinge_temporal=SimpleNamespace(time_s=time_s),
        )


def _root():
    return CausalDelayedRootFilter(
        RootState(0., np.r_[[0.,0.,1.], np.zeros(6)], np.eye(9)*.1),
        RootFilterConfig(fixed_lag_s=.2, nis_limit_3d=1e9), inertial=True)


def _wiring():
    profile = {side: FootStillnessProfile(.1, .1) for side in ("left", "right")}
    config = AnkleContactConfig(window_samples=5, enter_samples=1, exit_samples=1)
    return u4.OfflineUnifiedContactWiring(
        root=_root(), pose=_Pose(), detector=AnkleContactDetector(profile, config),
        footholds=DualFootFootholdCorrector(), maximum_root_age_s=.0075)


def _real_pose_wiring(monkeypatch):
    def public_projector(_base, correction, *, model):
        del model
        projected={segment:np.asarray(correction[segment],float).copy() for segment in SEGMENTS}
        return projected,{"post_projection_all_inside_rom":True,
            "fk_direction_residual_maximum_deg":0.,
            "joint":{name:{"post_projection_signed_deg":0.} for name in
                ("elbow_left","elbow_right","knee_left","knee_right")}}
    monkeypatch.setattr(pose_module,"project_hinge_corrections",public_projector)
    monkeypatch.setattr(pose_module,"corrected_proxy_points",lambda _b,_c,_g:{
        "ankle_left":np.array([-.1,0.,-.9]),"ankle_right":np.array([.1,0.,-.9])})
    pose=CausalArticulatedPose(action_start_s=0.,action_stop_s=1.,
        rotations_at_fraction=lambda _:{segment:np.eye(3) for segment in SEGMENTS},
        geometry=SimpleNamespace(),hinge_projector=partial(public_projector,model=object()))
    profile={side:FootStillnessProfile(.1,.1) for side in ("left","right")}
    detector=AnkleContactDetector(profile,AnkleContactConfig(window_samples=5,enter_samples=1,exit_samples=1))
    wiring=u4.OfflineUnifiedContactWiring(root=_root(),pose=pose,detector=detector,
        footholds=DualFootFootholdCorrector(),maximum_root_age_s=.0075)
    return wiring,pose._CausalArticulatedPose__hinge_temporal_owner


def _ankle(moving=False):
    return {side: u4.AnkleImuInput(
        np.array([2.,0.,9.80665]) if moving else np.array([0.,0.,9.80665]),
        np.array([2.,0.,0.]) if moving else np.zeros(3), 0., 1. if moving else 0.)
        for side in ("left", "right")}


def _publish(wiring, time_s, *, moving=False, raised=False):
    offsets = {"left": np.array([-.1,0.,-.7 if raised else -.9]),
               "right": np.array([.1,0.,-.7 if raised else -.9])}
    return wiring.publish_native200(
        pelvis_imu=ImuSample(time_s,time_s,np.array([0.,0.,9.80665]),np.eye(3),int(time_s*1000)),
        ankle_imu=_ankle(moving), analytic_ankle_offset_world_m=offsets)


def _envelope(displacement=10.):
    return ReachabilityEnvelope(
        ReachabilityClass.NOMINAL,displacement,10.,100.,1.,10.,100.,1.,1.,1.,
        .01,2,10.,1e8,"synthetic U4 fixture envelope")


def _observation(wiring, position=None):
    time = wiring.root.current_state.time_s
    return PositionObservation(time,time+.001,
        wiring.root.current_state.position_m.copy() if position is None else np.asarray(position,float),
        np.eye(3),"U4_FIXTURE",(0,1,2,3))


def test_native_event_order_calls_pose_once_detector_twice_and_foothold_once():
    wiring=_wiring(); result=_publish(wiring,.005)
    assert (result.pose_sample_calls,result.detector_updates,result.foothold_updates)==(1,2,1)
    assert wiring.pose.calls==1 and wiring.pose.install_count==0
    assert wiring.root.current_state.time_s==.005


def test_stance_uncertain_swing_flight_and_recontact_lifecycle():
    wiring=_wiring()
    for index in range(1,7): last=_publish(wiring,index*.005)
    assert all(row.resolved_support_state is FootSupportState.STANCE_CONFIRMED for row in last.evidence.values())
    original=wiring.footholds.footholds_world_m()
    uncertain=_publish(wiring,.035,moving=True,raised=False)
    assert all(row.resolved_support_state is FootSupportState.UNCERTAIN for row in uncertain.evidence.values())
    released=_publish(wiring,.040,moving=True,raised=True)
    assert released.contact_transition is not None
    assert released.contact_transition.released_sides==("left","right")
    assert not wiring.footholds.footholds_world_m()
    assert all(row.resolved_support_state is FootSupportState.SWING_CONFIRMED for row in released.evidence.values())
    for index in range(9,16): contact=_publish(wiring,index*.005)
    assert all(row.resolved_support_state is FootSupportState.STANCE_CONFIRMED for row in contact.evidence.values())
    renewed=wiring.footholds.footholds_world_m()
    assert set(renewed)==set(original) and all(not np.shares_memory(renewed[s],original[s]) for s in renewed)


def test_bilateral_cues_are_frozen_before_detector_updates(monkeypatch):
    wiring=_wiring(); seen=[]; original=wiring.detector.update
    def update(side,**kwargs):
        seen.append((side,dict(wiring.evidence)))
        return original(side,**kwargs)
    monkeypatch.setattr(wiring.detector,"update",update)
    _publish(wiring,.005)
    assert [row[0] for row in seen]==["left","right"]
    assert all(all(item.resolved_support_state is FootSupportState.UNOBSERVABLE
                   for item in snapshot.values()) for _,snapshot in seen)


def test_rejected_root_does_not_touch_pose_detector_foothold_or_temporal_state():
    wiring=_wiring()
    for index in range(1,7): _publish(wiring,index*.005)
    pose_before=wiring.pose.publication_token().digest
    contact_before=pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))
    result=wiring.admit_root_observation(
        _observation(wiring,[5.,0.,1.]),nominal_envelope=_envelope(1e-6))
    assert result.transaction.rejection_recorded and not result.immutable_contact_replayed
    assert result.pose_sample_calls==result.detector_updates==result.foothold_updates==0
    assert wiring.pose.publication_token().digest==pose_before
    assert pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))==contact_before


def test_accepted_root_replays_only_last_immutable_operator(monkeypatch):
    wiring=_wiring()
    for index in range(1,7): native=_publish(wiring,index*.005)
    assert native.contact.replay_operator is not None
    monkeypatch.setattr(wiring.footholds,"update",lambda *a,**k: (_ for _ in ()).throw(AssertionError("mutable update")))
    result=wiring.admit_root_observation(_observation(wiring),nominal_envelope=_envelope())
    assert result.transaction.root_committed and result.immutable_contact_replayed
    assert (result.transaction_calls,result.pose_sample_calls,result.detector_updates,result.foothold_updates)==(1,0,0,0)


def test_exactly_one_transaction_and_root_kind(monkeypatch):
    wiring=_wiring(); _publish(wiring,.005); calls=[]
    real=u4.execute_causal_update_transaction
    def execute(**kwargs): calls.append(kwargs); return real(**kwargs)
    monkeypatch.setattr(u4,"execute_causal_update_transaction",execute)
    wiring.admit_root_observation(_observation(wiring),nominal_envelope=_envelope())
    assert len(calls)==1 and calls[0]["kind"].value=="ROOT_POSITION"
    assert "pose" not in calls[0] and "dynamic_envelope" not in calls[0]


def test_future_or_reversed_events_fail_before_owner_mutation():
    wiring=_wiring(); _publish(wiring,.005); before=wiring.pose.publication_token().digest
    with pytest.raises(ValueError,match="reversed"):
        _publish(wiring,.005)
    with pytest.raises(ValueError,match="precedes"):
        obs=_observation(wiring); wiring.admit_root_observation(
            PositionObservation(obs.measurement_time_s,.004,obs.root_position_m,obs.covariance_m2,
                                obs.tag_id,obs.anchors),nominal_envelope=_envelope())
    assert wiring.pose.publication_token().digest==before


def test_partition_determinism_for_native_publications():
    first=_wiring(); second=_wiring()
    for index in range(1,9): _publish(first,index*.005)
    for block in ((1,2,3),(4,5),(6,7,8)):
        for index in block: _publish(second,index*.005)
    assert first.root.current_state.vector.tobytes()==second.root.current_state.vector.tobytes()
    assert first.footholds.footholds_world_m().keys()==second.footholds.footholds_world_m().keys()


def test_sealed_u3_bindings_and_offline_claim_ceiling():
    assert u4.U3_ORIGINAL_SEAL_SHA256.startswith("938862a8")
    assert u4.U3_CORRECTION_SEAL_SHA256.startswith("3faa2fe7")


def test_real_pose_native_sample_is_sole_temporal_advance_for_root_uwb(monkeypatch):
    wiring,temporal=_real_pose_wiring(monkeypatch)
    revision=lambda: temporal._CausalHingeTemporalOwner__revision
    before=revision()
    _publish(wiring,.005)
    assert revision()==before+1
    history_after_native=temporal._history_bytes()
    accepted=wiring.admit_root_observation(_observation(wiring),nominal_envelope=_envelope())
    assert accepted.transaction.root_committed
    assert revision()==before+1 and temporal._history_bytes()==history_after_native
    _publish(wiring,.010)
    assert revision()==before+2
    history_before_reject=temporal._history_bytes()
    rejected=wiring.admit_root_observation(
        _observation(wiring,[5.,0.,1.]),nominal_envelope=_envelope(1e-6))
    assert rejected.transaction.rejection_recorded
    assert revision()==before+2 and temporal._history_bytes()==history_before_reject


def test_rejection_changes_only_one_root_health_event_and_no_owner_bytes(monkeypatch):
    wiring,temporal=_real_pose_wiring(monkeypatch)
    for index in range(1,7): _publish(wiring,index*.005)
    root=wiring.root
    vector=root.current_state.vector.tobytes(); covariance=root.current_state.covariance.tobytes()
    snapshots=tuple((row.state.time_s,row.state.vector.tobytes(),row.state.covariance.tobytes(),
                     row.applied_constraint_cursor) for row in root._snapshots)
    constraints=tuple((row.sequence,row.time_s,row.owner) for row in root._constraint_events)
    health_before=root.health_snapshot(); pose_before=wiring.pose.publication_token().digest
    contact_before=pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))
    temporal_before=temporal._history_bytes()
    result=wiring.admit_root_observation(
        _observation(wiring,[5.,0.,1.]),nominal_envelope=_envelope(1e-6))
    assert result.transaction.rejection_recorded
    assert root.current_state.vector.tobytes()==vector and root.current_state.covariance.tobytes()==covariance
    assert tuple((row.state.time_s,row.state.vector.tobytes(),row.state.covariance.tobytes(),
                  row.applied_constraint_cursor) for row in root._snapshots)==snapshots
    assert tuple((row.sequence,row.time_s,row.owner) for row in root._constraint_events)==constraints
    health_after=root.health_snapshot()
    assert health_after["tags"]["U4_FIXTURE"]["rejected"]==1
    assert health_before.get("tags",{}).get("U4_FIXTURE") is None
    assert wiring.pose.publication_token().digest==pose_before
    assert temporal._history_bytes()==temporal_before
    assert pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))==contact_before


def test_accepted_immutable_replay_preserves_detector_and_foothold_bytes(monkeypatch):
    wiring,_temporal=_real_pose_wiring(monkeypatch)
    for index in range(1,7): _publish(wiring,index*.005)
    before=pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))
    constraints=len(wiring.root._constraint_events)
    result=wiring.admit_root_observation(_observation(wiring),nominal_envelope=_envelope())
    assert result.transaction.root_committed and result.immutable_contact_replayed
    assert pickle.dumps((wiring.detector.__dict__,wiring.footholds.__dict__))==before
    assert len(wiring.root._constraint_events)==constraints+1
    assert wiring.root._constraint_events[-1].owner=="U4_ACCEPTED_UWB_IMMUTABLE_CONTACT_REPLAY"
