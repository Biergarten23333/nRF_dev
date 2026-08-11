import numpy as np
import pytest

from v47_s2_fusion import (S2Fusion,S2Parameters,corrected_range_m,
                           range_jacobian,require_full_vector_binding,scalar_range_update)
from v47_state_adaptive_fusion import merge_event_order,wrap_safe_delta_us


QUIET={"gyro_rms_dps":.01,"accel_dev_rms_g":.001,"gyro_std_dps":.005,"accel_std_g":.0002,
       "gyro_angle_1s_deg":.02,"gravity_change_deg":.05}
ACTIVE={"gyro_rms_dps":2.,"accel_dev_rms_g":.2,"gyro_std_dps":1.,"accel_std_g":.1,
        "gyro_angle_1s_deg":3.,"gravity_change_deg":3.}
VIBRATION={**ACTIVE,"gravity_change_deg":.05}


def parameters(scatter=.02,fleet=False):
    anchors=np.array([[0,0,0],[4,0,0],[4,3,0],[0,3,0],[0,0,2],[4,0,2],[4,3,2],[0,3,2]],float)
    return S2Parameters(np.eye(3)*scatter**2,np.ones(8)*.05,anchors,np.zeros(8),0.,
        .1,.01,.05,.005,.2,.5,candidate_window_s=.5,min_candidate_positions=4,
        suspected_confirm_dwell_s=.15,suspected_clear_dwell_s=.2,conflict_enter_dwell_s=.2,
        conflict_resolve_dwell_s=.2,moving_quiet_dwell_s=.2,settling_dwell_s=.3,
        candidate_scatter_normalized=4.,fleet_context_enabled=fleet)


def ranges(p,par): return np.linalg.norm(par.anchors_m-np.asarray(p),axis=1)*1000


def feed(f,start,end,p,evidence=QUIET,noise=0.):
    par=f.p
    for tick in range(round(start*20),round(end*20)):
        t=tick/20
        f.process_control(t,evidence)
        if tick%2==0:
            q=np.asarray(p,float)+np.array([noise if tick%4==0 else -noise,0,0])
            f.process_uwb(t+.01,q,ranges(q,par),0xff,tick)


def initialized(scatter=.02,mode="S2P"):
    f=S2Fusion(parameters(scatter),mode); feed(f,0,2,[1,1,1]); assert f.state=="STATIONARY"; return f


def test_published_lock_is_immutable_and_candidate_separate():
    f=initialized(); lock=f.published_position.copy(); feed(f,2,2.45,[1.3,1,1],QUIET)
    assert np.array_equal(f.published_position,lock)
    center,_=f.candidate(2.45); assert center[0]>lock[0]


def test_platform_conflict_entry_and_auditable_resolution():
    f=initialized(); feed(f,2,2.8,[1.4,1,1],QUIET)
    assert any(t["to_state"]=="PLATFORM_CONFLICT" for t in f.transitions)
    assert any(t["reason"]=="CONFLICT_RESOLVED_NEW_STATIONARY_PLATFORM" for t in f.transitions)
    assert f.state=="STATIONARY" and f.published_position[0]>1.3


@pytest.mark.parametrize("evidence",[ACTIVE,{**ACTIVE,"gyro_rms_dps":.2,"gravity_change_deg":1.}])
def test_fast_and_slow_movement_detection(evidence):
    f=initialized(); feed(f,2,3,[1.5,1,1],evidence)
    assert any(t["to_state"]=="MOTION_SUSPECTED" for t in f.transitions)
    assert any(t["to_state"]=="MOVING" for t in f.transitions)


def test_cumulative_gyro_evidence_is_used():
    f=initialized(); evidence={**QUIET,"gyro_angle_1s_deg":2.,"gravity_change_deg":1.}
    feed(f,2,3,[1.4,1,1],evidence); assert any(t["to_state"]=="MOVING" for t in f.transitions)


def test_common_mode_vibration_suspects_but_does_not_unlock_or_creep():
    f=initialized(); lock=f.published_position.copy(); feed(f,2,2.5,[1,1,1],VIBRATION,.01); feed(f,2.5,3.5,[1,1,1])
    assert not any(t["to_state"]=="MOVING" for t in f.transitions)
    assert np.array_equal(f.published_position,lock)


def test_fleet_context_suppresses_common_mode_confirmation():
    f=S2Fusion(parameters(fleet=True),"S2P"); feed(f,0,2,[1,1,1]);
    for tick in range(40,55):
        t=tick/20; f.process_control(t,VIBRATION,fleet_common_mode=True); f.process_uwb(t+.01,[1,1,1],ranges([1,1,1],f.p),0xff,tick)
    assert not any(t["to_state"]=="MOVING" for t in f.transitions)


def test_covariance_normalized_high_scatter_relock():
    f=initialized(scatter=.15); feed(f,2,3,[1.7,1,1],ACTIVE,.04); assert f.state=="MOVING"
    feed(f,3,5,[1.7,1,1],QUIET,.04); assert f.state=="STATIONARY"
    assert np.linalg.norm(f.published_position-[1.7,1,1])<.15


def test_raw_range_jacobian_and_bad_anchor_rejection():
    h=range_jacobian(np.array([1.,0,0]),np.zeros(3)); assert np.allclose(h,[1,0,0])
    x=np.r_[1.,1.,1.,0,0,0]; p=np.eye(6)*.01
    _,_,nis,take,_=scalar_range_update(x,p,100.,np.zeros(3),.01,10.827566)
    assert not take and nis>10.827566


def test_delay_is_applied_exactly_once():
    assert corrected_range_m(1100,60,0)==pytest.approx(1.04)
    with pytest.raises(ValueError,match="DOUBLE"):
        corrected_range_m(1100,60,0,transport_delay_applied=True)


def test_per_link_accounting_async_order_psd_and_determinism():
    assert merge_event_order(np.array([1.,1.05]),np.array([1.025]))[1][0]=="uwb"
    def once():
        f=initialized(mode="S2R"); feed(f,2,3,[1.5,1,1],ACTIVE); feed(f,3,5,[1.5,1,1]); return f
    a,b=once(),once(); assert a.accounting()==b.accounting(); assert a.transitions==b.transitions
    assert a.accounting()["closed"] and np.linalg.eigvalsh(a.covariance).min()>=-1e-10
    assert np.max(abs(a.covariance-a.covariance.T))<1e-12


def test_timestamp_wrap_and_frame_refusal():
    assert wrap_safe_delta_us(3,65534,bits=16)==5
    with pytest.raises(ValueError,match="BLOCKED_FRAME_BINDING"):
        require_full_vector_binding({"sensor_to_v4_transform_status":"UNIDENTIFIABLE"})


def test_causal_buffer_does_not_use_future_sample():
    f=initialized(); f.process_uwb(2.01,[1.5,1,1],ranges([1.5,1,1],f.p),0xff,1)
    center,_=f.candidate(2.01); before=center.copy()
    # No future input exists in the buffer; querying earlier trims/uses only past.
    center2,_=f.candidate(2.01); assert np.array_equal(before,center2)
