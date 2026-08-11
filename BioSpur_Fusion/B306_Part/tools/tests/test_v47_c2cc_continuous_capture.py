from v47_c2cc_continuous_capture import (LiveCatchupDetector, classify_observation,
    formal_start_disposition,lifecycle_is_single_timeline)
from analyze_v47_c2cc_stationary import formal_source_masks,IMU_DTYPE,UWB_DTYPE
import numpy as np


def second(i, *, gap=0, queue=0, offset=1000.0):
    return {"end_monotonic":float(i),"imu_hz":200,"uwb_hz":8,"imu_gap_events":gap,
            "uwb_gap_events":0,"age_offset_median_ms":offset,"decoded_queue_depth":queue,
            "raw_queue_depth":0,"serial_input_bytes":0,"timestamp_jump":False}


def test_live_catchup_requires_continuous_plateau_and_resets_on_gap():
    d=LiveCatchupDetector()
    for i in range(35):ok,detail=d.update(second(i,offset=1000+(i%2)*.1))
    assert ok and detail["stable_seconds"]>=25
    ok,detail=d.update(second(36,gap=1));assert not ok and detail["stable_seconds"]==0


def test_queue_backlog_is_not_live_catchup():
    d=LiveCatchupDetector()
    for i in range(15):ok,_=d.update(second(i,queue=5))
    assert not ok


def test_unexpected_observation_is_classified_not_raised():
    result=classify_observation(["FUSION_MASTER_STATUS marker=wrong count=2 ready=1"])
    assert result["status"]=="OBSERVED_UNEXPECTED"

def test_degraded_timeout_transitions_without_abort():
    assert formal_start_disposition(179,0,30,180) is None
    assert formal_start_disposition(180,0,30,180)=="STARTED_DEGRADED"
    assert formal_start_disposition(70,30,30,180)=="LIVE_CATCHUP_OBSERVED"

def test_single_open_first_byte_and_markers_share_timeline():
    phases={"COLLECTOR_OPEN":1.,"RAW_RECORDING_FROM_FIRST_BYTE":1.1,"WARMUP_RECORDING":1.,"FORMAL_T0":91.,"CLEAN_STOP":691.,"one_raw_file":True}
    assert lifecycle_is_single_timeline(phases,{"serial_open_count":1})
    assert not lifecycle_is_single_timeline(phases,{"serial_open_count":2})

def test_exact_formal_source_slice_excludes_stale_crossing_records():
    imu=np.zeros(4,dtype=IMU_DTYPE);imu["b306_us"]=[100000,145000,150000,200000]
    uwb=np.zeros(4,dtype=UWB_DTYPE);uwb["strobe_us"]=[90,100,110,200]
    bounds={"t0_exclusive":{"imu_base_us":100000,"imu_n":10,"uwb_strobe_us":100},
            "t1_inclusive":{"imu_base_us":155000,"imu_n":10,"uwb_strobe_us":110}}
    im,um,_,_=formal_source_masks(imu,uwb,bounds)
    assert im.tolist()==[False,False,True,True]
    assert um.tolist()==[False,False,True,False]
