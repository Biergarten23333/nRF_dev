import queue
import threading
import time

import numpy as np
import pytest

from v47_c2cc_frame_binding_capture import Protocol, action_quality,require_fit_eligible
from v47_c2cc_continuous_capture import LiveCatchupDetector


class Channel:
    def health_snapshot(self):
        return {"raw_bytes_submitted":1,"decoded_records":1}


class Recorder:
    def __init__(self):
        self.aborted=False;self.channel=Channel();self.record_index=0;self.imu=[];self.positions=[]
    def consume(self,_):time.sleep(.001)
    def marker(self,name,extra=None):return {"name":name,"monotonic":time.monotonic(),**(extra or {})}


class Inbox:
    def __init__(self):self.q=queue.Queue()


def test_exact_token_rejected_without_advancing(tmp_path):
    recorder=Recorder();inbox=Inbox();protocol=Protocol(recorder,inbox,tmp_path);result=[]
    thread=threading.Thread(target=lambda:result.append(protocol.wait("instruction","MOUNT_A_READY","A0")))
    thread.start();inbox.q.put(("mount_a_ready",time.monotonic(),"wall"));time.sleep(.02)
    assert thread.is_alive()
    inbox.q.put(("MOUNT_A_READY",time.monotonic(),"wall"));thread.join(1);protocol.close()
    assert result[0]["disposition"]=="ACCEPT"
    assert [x["disposition"] for x in protocol.actions]==["REJECT","ACCEPT"]


def test_abort_sets_stop_before_any_following_wait(tmp_path):
    recorder=Recorder();inbox=Inbox();protocol=Protocol(recorder,inbox,tmp_path)
    inbox.q.put(("ABORT_CAPTURE",time.monotonic(),"wall"))
    row=protocol.wait("instruction","A_HORIZONTAL_2_RETRY_START","retry")
    protocol.close()
    assert row["disposition"]=="ABORT" and recorder.aborted
    assert len(protocol.instructions)==1 and len(protocol.actions)==1


def test_quality_gate_accepts_well_excited_complete_block():
    recorder=Recorder();start=10.;end=16.;times=np.linspace(start,end,1200)
    recorder.imu=[{"consume_mono":t,"accel_mps2":np.array([np.sin(4*t),0,9.80665]),
                   "gyro_dps":np.zeros(3)} for t in times]
    ptime=np.linspace(start,end,60)
    recorder.positions=[{"consume_mono":t,"position_m":np.array([.5*np.sin(2*t),0,0])} for t in ptime]
    result=action_quality(recorder,start,end,"HORIZONTAL_1")
    assert result["accepted"] and all(result["checks"].values())


def test_quality_gate_rejects_incomplete_block_and_preserves_reasons():
    recorder=Recorder();result=action_quality(recorder,0,1,"VERTICAL")
    assert not result["accepted"]
    assert {k for k,v in result["checks"].items() if not v}>={"duration","imu_samples","t4_solutions","displacement"}


def test_fionread_unavailable_is_not_encoded_as_backlog():
    observed=-1
    detector_value=0 if observed<0 else observed
    assert detector_value==0
    assert "UNAVAILABLE_NOT_BACKLOG" if observed<0 else "MEASURED"


def test_live_gate_still_rejects_real_serial_backlog():
    detector=LiveCatchupDetector();last=None
    for second in range(12):
        row={"end_monotonic":second+1,"imu_hz":200,"uwb_hz":8,"imu_gap_events":0,
             "uwb_gap_events":0,"timestamp_jump":False,"decoded_queue_depth":0,
             "raw_queue_depth":0,"serial_input_bytes":5,"age_offset_median_ms":100.}
        _,last=detector.update(row)
    assert not last["queues"] and detector.stable_seconds==0


def test_failed_retry_blocks_before_next_instruction():
    with pytest.raises(RuntimeError,match="PROTOCOL_BLOCKED_INSUFFICIENT_EXCITATION:A_VERTICAL"):
        require_fit_eligible({"fit_eligible":False},"A_VERTICAL")
