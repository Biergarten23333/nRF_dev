from dataclasses import replace
import inspect

import numpy as np
import pytest

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world.async_root_worker import (
    AsyncRootWorker, RootWorkerConfig, RootWorkerEvent, run_synchronous)
from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CausalContactTransitionEvidence,CausalImuActivitySummary,
    IndependentNodeConsensusEvidence,ReachabilityClass,ReachabilityEnvelope)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_uwb_root_world.u0 import UwbRow
from biospur_fusion.root_r3.models import ImuSample


ANCHORS=np.array([[1,0,0],[0,1,0],[0,0,1],[-1,-1,-1],
                  [2,0,0],[0,2,0],[0,0,2],[-2,-2,-2]],float)
CLOCKS={f"N{i}":DirectNodeLinkClock(f"N{i}",1000.,0.,0,0,10_000_000) for i in range(10)}


def _envelope(limit=1.,kind=ReachabilityClass.NOMINAL):
    return ReachabilityEnvelope(kind,limit,10.,100.,1.,10.,100.,.2,.2,1.,.02,2,1.,1e8,
        "explicit U7B fixture envelope")


def _config(envelope=None,capacity=64):
    return RootWorkerConfig(0.,np.zeros(9),np.eye(9)*.1,ANCHORS,CLOCKS,np.zeros(8),0.,
        _envelope() if envelope is None else envelope,capacity)


def _group(index=0,target=np.zeros(3),nodes=10):
    availability_us=100_000+120_048*index;strobe=availability_us-40_000
    ranges=tuple(int(round(np.linalg.norm(ANCHORS[i]-target)*1000)) for i in range(8))
    return tuple(UwbRow(f"N{i}",0,1,1,strobe,availability_us,tuple(range(8)),ranges,
        (100,200,300,400,500,600,700,800),(100,)*8,0xff) for i in range(nodes))


def _event(rows,index=0,**kwargs):
    _epochs,_measurement,availability=group_epoch_times_ns(rows,clocks=CLOCKS)
    return RootWorkerEvent(index,availability*1e-9,"UWB",rows,**kwargs)


def _imu(index,time_s):
    return RootWorkerEvent(index,time_s,"IMU",ImuSample(time_s,time_s,
        np.array([0.,0.,9.80665]),np.eye(3),index))


def test_worker_matches_synchronous_decisions_state_covariance_and_uncertainty():
    events=[_imu(0,.005),_imu(1,.010),_event(_group(),2)]
    reference,reference_final=run_synchronous(_config(),events)
    reference_uwb=[row for row in reference if row["kind"]=="UWB"]
    worker=AsyncRootWorker(_config());[worker.submit(event) for event in events]
    actual,final=worker.close_and_collect(1)
    assert [(x["decision"],x["root_reason"],x["committed"]) for x in actual]==[
        (x["decision"],x["root_reason"],x["committed"]) for x in reference_uwb]
    for name in ("state","covariance","h","r","s","innovation"):
        np.testing.assert_allclose(actual[0][name],reference_uwb[0][name],atol=1e-12,rtol=0)
    assert actual[0]["nis"]==pytest.approx(reference_uwb[0]["nis"],abs=1e-12)
    np.testing.assert_allclose(final["state"],reference_final["state"],atol=1e-12,rtol=0)
    np.testing.assert_allclose(final["covariance"],reference_final["covariance"],atol=1e-12,rtol=0)
    assert final["future_imu_count"]==final["future_uwb_count"]==0


def test_rejection_records_one_health_event_and_preserves_causal_prediction():
    event=_event(_group(target=np.array([.8,0.,0.])),0)
    result,final=run_synchronous(_config(_envelope(.001)),[event])
    assert result[0]["rejection_recorded"] and not result[0]["committed"]
    assert final["health"]["C2_SHARED_ROOT_DIAGNOSTIC"]["rejected"]==1
    assert np.isfinite(final["state"]).all() and np.isfinite(final["covariance"]).all()


def _corroboration(rows):
    _epochs,measurement_ns,availability_ns=group_epoch_times_ns(rows,clocks=CLOCKS)
    measurement=measurement_ns*1e-9;availability=availability_ns*1e-9
    activity=CausalImuActivitySummary(measurement,availability,measurement-.04,("N0","N1"),
        np.full(2,measurement-.001),np.full(2,.6),np.full(2,2.),np.full(2,.03),"U7B activity")
    consensus=IndependentNodeConsensusEvidence(measurement,("N0","N1"),
        np.array([[.3,0,0],[.31,0,0]]),np.full(2,3,dtype=np.int64),np.full(2,4.),True,
        "U7B independent consensus")
    contact=CausalContactTransitionEvidence(availability,
        {"left":"STANCE_CONFIRMED","right":"STANCE_CONFIRMED"},
        {"left":"SWING_CONFIRMED","right":"STANCE_CONFIRMED"},("left",),("left",),
        "U7B contact release")
    return activity,consensus,contact


def test_single_and_multi_node_instant_lie_reject_without_corroboration():
    for nodes in (1,10):
        result,_=run_synchronous(_config(_envelope(.001)),
            [_event(_group(target=np.array([.8,0,0.]),nodes=nodes))])
        assert result[0]["rejection_recorded"] and not result[0]["committed"]


def test_sustained_fall_passes_only_with_all_causal_signals():
    rows=_group(target=np.array([.35,0,0.]));activity,consensus,contact=_corroboration(rows)
    dynamic=_envelope(2.,ReachabilityClass.DYNAMIC_FALL);config=_config(_envelope(.001))
    for values in ({"activity":activity},{"activity":activity,"consensus":consensus}):
        result,_=run_synchronous(config,[replace(_event(rows),dynamic_envelope=dynamic,**values)])
        assert not result[0]["committed"]
    result,_=run_synchronous(config,[_event(rows,dynamic_envelope=dynamic,activity=activity,
        consensus=consensus,contact=contact)])
    assert result[0]["committed"] and result[0]["decision"]=="ACCEPT_CORROBORATED_DYNAMIC"


def test_finite_speed_locomotion_accepts_and_cardinality_is_one():
    result,_=run_synchronous(_config(),[_event(_group(target=np.array([.03,0,0.])))])
    assert result[0]["committed"] and result[0]["decision"]=="ACCEPT_NOMINAL"
    assert set(result[0]["timing_ms"])=={"link_build","adaptive_trust_ten_nodes",
        "final_shared_root","root_prepare","guard","validation_apply","transaction_total",
        "service_total","ipc_receive","end_to_end_publication_lag"}


def test_fixed_queue_order_capacity_and_private_root_owner():
    with pytest.raises(ValueError):_config(capacity=65)
    worker=AsyncRootWorker(_config(capacity=1))
    assert not hasattr(worker,"root") and worker.pid is not None
    worker.submit(_imu(0,.005))
    with pytest.raises(ValueError):worker.submit(_imu(1,.004))
    _rows,final=worker.close_and_collect(0);assert final["imu_count"]==1


def test_timed_worker_path_does_not_use_sys_setprofile():
    import biospur_fusion.c2_uwb_root_world.async_root_worker as owner
    source=inspect.getsource(owner)
    assert "sys.setprofile" not in source
