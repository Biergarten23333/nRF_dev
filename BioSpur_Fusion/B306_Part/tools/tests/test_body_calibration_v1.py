import json, sys
from pathlib import Path
import numpy as np
import pytest

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS))
from body_calibration_v1.contract import *
from body_calibration_v1.dry_run import run
from body_calibration_v1.integrity import *
from body_calibration_v1.solver import *
from body_calibration_v1.state_machine import *
from body_calibration_v1.segmentation import *

def observation():
    return {"master":MASTER,"central":CENTRAL,"anchors":list(ANCHORS),"listeners_ok":True,
      "peers":[{"name":n,"connected":True,"subscribed":True,"marker":MARKER,"fwid":FWID,
                "active_sha":ACTIVE_SHA,"confirmed":1} for n in sorted(EXPECTED_NODES)]}

def test_random_nine_node_permutation_and_determinism():
    a=run(123);b=run(123);assert a==b;assert a["result"]["best"]==a["truth"]
def test_arbitrary_proper_and_mirrored_mounts():
    reflections=[np.diag([-1,1,1]),np.diag([1,-1,1])]
    for m in reflections:
        r=proper_rotation(np.eye(3),m);assert np.isclose(np.linalg.det(r),1)
    rng=np.random.default_rng(9)
    for _ in range(20):
        q,_=np.linalg.qr(rng.normal(size=(3,3)));q[:,0]*=np.sign(np.linalg.det(q));assert np.linalg.det(proper_rotation(np.eye(3),q))>.999
def test_global_yaw_gauge(): assert "GLOBAL_YAW" in run()["result"]["gauge"]
def test_missing_node_and_low_margin():
    with pytest.raises(SolveError):solve_assignment({"c":[0],"a":[1]},{"s0":[0]},central_node="c",central_slot="s0")
    r=solve_assignment({"c":[0],"a":[1],"b":[1]},{"s0":[0],"l":[1],"r":[1]},central_node="c",central_slot="s0")
    assert not r["identifiable"] and r["second"]!=r["best"]
@pytest.mark.parametrize("mutation",["duplicate","unexpected","central","missing"])
def test_membership_failures(mutation):
    o=observation()
    if mutation=="duplicate":o["peers"][-1]=dict(o["peers"][0])
    if mutation=="unexpected":o["peers"][-1]["name"]="BSFFFFF"
    if mutation=="central":o["central"]="BSFC2CC"
    if mutation=="missing":o["peers"].pop()
    with pytest.raises(ContractError):validate_readiness(o)
def test_readiness_accepts_exact_fleet(): validate_readiness(observation())
def test_stale_prefix_and_queue_backlog():
    c=LiveCatchup(3);assert not c.update(decoded_depth=9,raw_depth=4,source_age_delta_ms=100)
    assert not c.update(decoded_depth=0,raw_depth=0,source_age_delta_ms=0)
    assert not c.update(decoded_depth=0,raw_depth=0,source_age_delta_ms=0)
    assert c.update(decoded_depth=0,raw_depth=1,source_age_delta_ms=1)
def test_tokens_early_start_duplicate_stop_and_abort():
    m=ActionMachine()
    with pytest.raises(TokenError):m.accept("STOP",1)
    m.accept("READY_INITIAL_STILL",2)
    with pytest.raises(TokenError,match="early"):m.start_if_due(2+PRE_ACTION_NS-1)
    text=m.start_if_due(2+PRE_ACTION_NS);assert text.startswith("自然站立")
    m.accept("STOP",2+PRE_ACTION_NS+20)
    with pytest.raises(TokenError):m.accept("WEARING_READY",4)
    m.accept("ABORT_CAPTURE",5)
    with pytest.raises(TokenError):m.accept("DONE",6)
def test_fit_validation_frozen():
    p=ActionMachine().frozen_partition();assert p["walk"]==p["final_still"]=="validation";assert all(p[x]=="fit" for x in list(p)[:-2])
def test_reflected_geometry_rejected(tmp_path):
    p=tmp_path/"V4IO"/"anchor_layout.json";p.parent.mkdir();p.write_text("{}")
    c=CalibrationContract(p,p,tmp_path/"slots.json")
    with pytest.raises(ContractError,match="reflected"):c.validate()
def test_covariance_guards():
    covariance_gate(np.eye(4))
    for c in (np.array([[1,np.nan],[0,1]]),np.array([[1,.2],[.1,1]]),np.array([[1,0],[0,-1]])):
        with pytest.raises(SolveError):covariance_gate(c)
def test_raw_accounting_and_formal_integrity():
    assert StreamAudit(100,100,90,5,5).validate()
    with pytest.raises(IntegrityError):StreamAudit(100,99,89,5,5).validate()
    with pytest.raises(IntegrityError):StreamAudit(100,100,90,5,5,sequence_gaps=1).validate()
def test_stop_is_upper_bound_and_return_walk_unscored():
    t=np.arange(0,12,.1);m=np.zeros_like(t);m[(t>=1)&(t<5)]=2;m[(t>=9)&(t<11)]=2
    r=find_post_action_transition(t,m,action_start_s=0,stop_upper_s=11.5,quiet_threshold=.5)
    assert r["scored_end_s"]<5 and r["post_transition_start_s"]>=9
