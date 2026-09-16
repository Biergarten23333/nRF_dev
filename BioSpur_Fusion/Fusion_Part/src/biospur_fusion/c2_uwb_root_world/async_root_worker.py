"""Fixture-only asynchronous U7B owner for ROOT_POSITION updates.

One long-lived spawned process exclusively owns the mutable Root-R3 filter.
The producer can submit immutable, monotonically available events but cannot
access root state or publication tokens. ARTICULATED transactions are absent.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import multiprocessing as mp
import os
import pickle
import queue
import time
from typing import Any, Sequence


THREAD_ENV = {"OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
              "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}


@dataclass(frozen=True)
class RootWorkerConfig:
    initial_time_s: float
    initial_vector: Any
    initial_covariance: Any
    anchors_m: Any
    clocks: Any
    anchor_delay_m: Any
    tag_delay_m: float
    nominal_envelope: Any
    queue_capacity: int = 64

    def __post_init__(self) -> None:
        if isinstance(self.queue_capacity, bool) or not 1 <= int(self.queue_capacity) <= 64:
            raise ValueError("U7B input queue capacity must be in [1,64]")


@dataclass(frozen=True)
class RootWorkerEvent:
    sequence: int
    availability_time_s: float
    kind: str
    payload: Any
    dynamic_envelope: Any = None
    activity: Any = None
    consensus: Any = None
    contact: Any = None

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or self.sequence < 0:
            raise ValueError("invalid event sequence")
        if not math.isfinite(float(self.availability_time_s)):
            raise ValueError("invalid event availability")
        if self.kind not in ("IMU", "UWB"):
            raise ValueError("invalid event kind")
        if self.kind == "IMU" and any(value is not None for value in (
            self.dynamic_envelope, self.activity, self.consensus, self.contact)):
            raise ValueError("IMU event cannot carry measurement corroboration")


def _strict_floor(node: str, query: float):
    import numpy as np
    from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import StrictFloorOffset
    pose_time = int(query // 5_000_000 * 5_000_000)
    if pose_time == query:
        pose_time -= 5_000_000
    return StrictFloorOffset(np.zeros(3), pose_time, query, query - pose_time,
                             pose_time // 5_000_000)


class _TimedRootProxy:
    def __init__(self, root):
        self.root = root
        self.plan = None
        self.prepare_calls = 0
        self.prepare_ns = 0
        self.validation_apply_ns = 0
        self.guard_calls = 0
        self.guard_ns = 0

    def prepare_position(self, *args, **kwargs):
        started = time.perf_counter_ns(); self.prepare_calls += 1
        try:
            self.plan = self.root.prepare_position(*args, **kwargs)
            return self.plan
        finally: self.prepare_ns += time.perf_counter_ns() - started

    def _validate_publication_token(self, *args, **kwargs):
        started=time.perf_counter_ns()
        try:return self.root._validate_publication_token(*args,**kwargs)
        finally:self.validation_apply_ns+=time.perf_counter_ns()-started

    def _prevalidate_position_plan(self, *args, **kwargs):
        started=time.perf_counter_ns()
        try:return self.root._prevalidate_position_plan(*args,**kwargs)
        finally:self.validation_apply_ns+=time.perf_counter_ns()-started

    def _apply_prevalidated_position(self, *args, **kwargs):
        started=time.perf_counter_ns()
        try:return self.root._apply_prevalidated_position(*args,**kwargs)
        finally:self.validation_apply_ns+=time.perf_counter_ns()-started

    def __getattr__(self, name): return getattr(self.root, name)


class _OwnedEngine:
    def __init__(self, config: RootWorkerConfig):
        import numpy as np
        from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootFilterConfig
        from biospur_fusion.root_r3.models import RootState
        self.np=np; self.config=config
        self.root=CausalDelayedRootFilter(RootState(config.initial_time_s,
            np.asarray(config.initial_vector,float),np.asarray(config.initial_covariance,float)),
            RootFilterConfig(fixed_lag_s=.2,nis_limit_3d=1e12),inertial=False)
        self.last_event=-math.inf; self.imu_count=0; self.group_count=0; self.imu_timing=[]

    def process(self,event:RootWorkerEvent,submitted_ns:int):
        import numpy as np
        from biospur_fusion.c2_uwb_calibration.adaptive_nodes import (
            adaptive_root_minimum_std_m,select_trusted_body_nodes)
        from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
        from biospur_fusion.c2_uwb_root_world import causal_update_transaction as tx
        from biospur_fusion.c2_uwb_root_world.causal_update_guard import CandidateKind
        from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import build_causal_links
        from biospur_fusion.root_r3.models import PositionObservation
        if event.availability_time_s < self.last_event:
            raise ValueError("worker event time reversed")
        self.last_event=event.availability_time_s
        received_ns=time.perf_counter_ns()
        if event.kind=="IMU":
            started=time.perf_counter_ns()
            if not self.root.add_imu(event.payload): raise RuntimeError("IMU_REJECTED")
            self.imu_count+=1;service=(time.perf_counter_ns()-started)*1e-6
            self.imu_timing.append(service)
            return {"kind":"IMU","sequence":event.sequence,"service_ms":service,
                "ipc_receive_ms":(received_ns-submitted_ns)*1e-6}
        group_started=time.perf_counter_ns()
        started=time.perf_counter_ns()
        links,audits,measurement,availability=build_causal_links(event.payload,
            clocks=self.config.clocks,strict_floor_offset=_strict_floor,
            anchor_delay_m=self.config.anchor_delay_m,tag_delay_m=self.config.tag_delay_m,
            sigma_for_quality=lambda _:.1)
        link_build_ns=time.perf_counter_ns()-started
        if abs(availability-event.availability_time_s)>1e-12:raise ValueError("availability mismatch")
        token=self.root.publication_token()
        started=time.perf_counter_ns()
        selection=select_trusted_body_nodes(links,anchors_m=self.config.anchors_m,
            initial_root_m=token.state.vector[:3],root_velocity_mps=token.state.vector[3:6],
            total_nodes=10)
        trust_ns=time.perf_counter_ns()-started
        if not selection.trusted_links:raise RuntimeError("NO_TRUSTED_NODE")
        started=time.perf_counter_ns()
        candidate=solve_shared_root(selection.trusted_links,anchors_m=self.config.anchors_m,
            initial_root_m=token.state.vector[:3],root_velocity_mps=token.state.vector[3:6])
        final_solve_ns=time.perf_counter_ns()-started
        if not candidate.success or candidate.rank!=3 or not math.isfinite(candidate.condition):
            raise RuntimeError(f"ROOT_CANDIDATE_{candidate.reason}")
        std=adaptive_root_minimum_std_m(len(selection.trusted_nodes)); r=np.eye(3)*std**2
        observation=PositionObservation(
            measurement_time_s=measurement,availability_time_s=availability,
            root_position_m=candidate.root_position_m,covariance_m2=r,
            tag_id="C2_SHARED_ROOT_DIAGNOSTIC",anchors=candidate.anchors_used,
            quality_state="DIAGNOSTIC_NODE_COUNT_FLOOR_ONLY_NOT_CALIBRATED_R",
            source_sequence=event.sequence)
        tail_edge=self.root._make_edge(start_time_s=self.root.current_state.time_s,
            end_time_s=availability,force=self.root._last_force,rotation=self.root._last_rotation,
            input_owner="EPHEMERAL_AVAILABILITY_HELD_INPUT") if availability>self.root.current_state.time_s+1e-12 else None
        located=self.root._state_at(measurement,tail_edge=tail_edge)
        if located is None:raise RuntimeError("missing delayed uncertainty owner")
        delayed_covariance=located[1].covariance.copy()
        proxy=_TimedRootProxy(self.root); original_guard=tx.evaluate_candidate_transition
        def timed_guard(*args,**kwargs):
            started=time.perf_counter_ns();proxy.guard_calls+=1
            try:return original_guard(*args,**kwargs)
            finally:proxy.guard_ns+=time.perf_counter_ns()-started
        tx.evaluate_candidate_transition=timed_guard
        started=time.perf_counter_ns()
        try:
            transaction=tx.execute_causal_update_transaction(root=proxy,observation=observation,
                kind=CandidateKind.ROOT_POSITION,nominal_envelope=self.config.nominal_envelope,
                dynamic_envelope=event.dynamic_envelope,activity=event.activity,
                consensus=event.consensus,contact=event.contact)
        finally:tx.evaluate_candidate_transition=original_guard
        transaction_ns=time.perf_counter_ns()-started
        if proxy.prepare_calls!=1 or proxy.guard_calls!=1:raise RuntimeError("call cardinality changed")
        plan=proxy.plan
        h=np.zeros((3,9));h[:,:3]=np.eye(3);innovation=plan.decision.innovation_m
        s=h@delayed_covariance@h.T+r
        nis=float(innovation@np.linalg.solve(s,innovation))
        self.group_count+=1;completed_ns=time.perf_counter_ns()
        return {"kind":"UWB","sequence":event.sequence,
            "decision":transaction.decision.reason.value,"root_reason":transaction.root_decision_reason,
            "committed":transaction.root_committed,"rejection_recorded":transaction.rejection_recorded,
            "state":self.root.current_state.vector.copy(),"covariance":self.root.current_state.covariance.copy(),
            "h":h,"r":r,"s":s,"nis":nis,"innovation":innovation.copy(),
            "rank":candidate.rank,"condition":candidate.condition,
            "timing_ms":{"link_build":link_build_ns*1e-6,"adaptive_trust_ten_nodes":trust_ns*1e-6,
                "final_shared_root":final_solve_ns*1e-6,"root_prepare":proxy.prepare_ns*1e-6,
                "guard":proxy.guard_ns*1e-6,"validation_apply":proxy.validation_apply_ns*1e-6,
                "transaction_total":transaction_ns*1e-6,
                "service_total":(completed_ns-group_started)*1e-6,
                "ipc_receive":(received_ns-submitted_ns)*1e-6,
                "end_to_end_publication_lag":(completed_ns-submitted_ns)*1e-6},
            "pose_age_max_ms":max(a.pose_age_ns for a in audits)*1e-6}

    def final(self):
        import resource
        return {"kind":"FINAL","state":self.root.current_state.vector.copy(),
            "covariance":self.root.current_state.covariance.copy(),
            "imu_count":self.imu_count,"group_count":self.group_count,
            "imu_timing_ms":tuple(self.imu_timing),
            "health":{key:{"accepted":value.accepted,"rejected":value.rejected,
                "consecutive_rejected":value.consecutive_rejected,"last_reason":value.last_reason}
                for key,value in self.root._health.items()},
            "worker_rusage_self_maxrss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "thread_environment":{key:os.environ.get(key) for key in THREAD_ENV},
            "future_imu_count":self.root.future_imu_count,"future_uwb_count":self.root.future_uwb_count}


def _worker_main(config,input_queue,output_queue):
    if any(os.environ.get(k)!=v for k,v in THREAD_ENV.items()):
        output_queue.put(("ERROR","thread environment not frozen"));return
    engine=_OwnedEngine(config)
    try:
        while True:
            item=input_queue.get()
            if item is None:break
            event_blob,submitted_ns=item;event=pickle.loads(event_blob)
            result=engine.process(event,submitted_ns)
            if result["kind"]=="UWB":output_queue.put(("RESULT",result))
        output_queue.put(("FINAL",engine.final()))
    except BaseException as exc:output_queue.put(("ERROR",f"{type(exc).__name__}: {exc}"))


class AsyncRootWorker:
    def __init__(self,config:RootWorkerConfig):
        self.config=config;self._last=-math.inf;self._submitted={};self._closed=False
        self.queue_high_watermark=0
        for key,value in THREAD_ENV.items():os.environ[key]=value
        context=mp.get_context("spawn")
        self._input=context.Queue(maxsize=config.queue_capacity)
        self._output=context.Queue(maxsize=config.queue_capacity)
        self._process=context.Process(target=_worker_main,args=(config,self._input,self._output))
        self._process.start()

    @property
    def pid(self):return self._process.pid

    def submit(self,event:RootWorkerEvent):
        if self._closed:raise RuntimeError("worker input closed")
        if event.availability_time_s<self._last:raise ValueError("producer event order reversed")
        self._last=event.availability_time_s;submitted=time.perf_counter_ns()
        self._submitted[event.sequence]=submitted
        self._input.put((pickle.dumps(event,protocol=5),submitted),timeout=5.)
        try:self.queue_high_watermark=max(self.queue_high_watermark,self._input.qsize())
        except NotImplementedError:pass

    def close_and_collect(self,expected_results:int,timeout_s:float=30.):
        self._closed=True;self._input.put(None,timeout=5.);results=[];final=None
        deadline=time.monotonic()+timeout_s
        while final is None:
            kind,payload=self._output.get(timeout=max(.01,deadline-time.monotonic()))
            if kind=="ERROR":raise RuntimeError(payload)
            if kind=="RESULT":
                results.append(payload)
            else:final=payload
        self._process.join(timeout=5.)
        if self._process.is_alive():self._process.terminate();raise RuntimeError("worker did not drain")
        if self._process.exitcode!=0 or len(results)!=expected_results:raise RuntimeError("worker result loss")
        return results,final


def run_synchronous(config:RootWorkerConfig,events:Sequence[RootWorkerEvent]):
    engine=_OwnedEngine(config);results=[]
    for event in events:results.append(engine.process(event,time.perf_counter_ns()))
    return results,engine.final()
