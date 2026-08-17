import numpy as np,pytest
from biospur_fusion.calibration_v2.association import *

N=tuple(f"N{i}" for i in range(10));R=ROLES
def diagonal_blocks(n=8,noise=.02):
 rng=np.random.default_rng(4);base=np.eye(10)*5
 return np.stack([base+rng.normal(0,noise,(10,10)) for _ in range(n)])
def test_known_permutation_recovery():
 p=np.array([3,1,8,0,6,2,9,4,7,5]);s=np.zeros((10,10));s[np.arange(10),p]=10
 assert tuple(R.index(topk_assignments(N,R,s,1)[0]["mapping"][n]) for n in N)==tuple(p)
def test_mirror_ambiguity_stays_tied():
 s=np.eye(10)*3;s[2,2]=s[2,3]=3;s[3,2]=s[3,3]=3
 t=topk_assignments(N,R,s,2);assert t[0]["score"]==t[1]["score"]
def test_unilateral_semantics_resolves_mirror():
 s=np.eye(10)*3;s[2,2]+=2;s[3,3]+=2;assert topk_assignments(N,R,s,1)[0]["mapping"]["N2"]=="upper_arm_left"
def test_uwb_jitter_does_not_flip_supported_mapping():
 s=np.eye(10)*20;u=np.random.default_rng(1).normal(0,.1,(10,10));assert topk_assignments(N,R,s+u,1)[0]["mapping"]==topk_assignments(N,R,s,1)[0]["mapping"]
def test_uwb_disabled_same_mapping():
 s=np.eye(10)*5;assert topk_assignments(N,R,s,1)[0]["mapping"]==topk_assignments(N,R,s+np.eye(10),1)[0]["mapping"]
def test_missing_repetition_not_false_freeze():
 b=bootstrap_assignments(N,R,diagonal_blocks(),500);n={"margin_p99":0};leave={"x":True};assert freeze_classification(b,n,100,leave,{"A":True},True,True,False)!="AUTHORITATIVE_FREEZE_CANDIDATE"
def test_bootstrap_and_wilson():
 b=bootstrap_assignments(N,R,diagonal_blocks(),500);assert b["complete_frequency"]==1 and b["wilson_lower_one_sided_95"]>.99
def test_permutation_null_count():assert permutation_null_margins(N,R,diagonal_blocks(3),100,3)["valid_permutations"]==100
def test_joint_assignment_beats_greedy_collision():
 s=np.eye(10);s[0,:2]=[10,9];s[1,:2]=[9.5,0]
 m=topk_assignments(N,R,s,1)[0]["mapping"];assert m["N0"]==R[1] and m["N1"]==R[0]
@pytest.mark.parametrize("bad",[(["N0"]*10,R,np.eye(10)),(N,R[:-1],np.eye(10)),(N,R,np.ones((9,10)))])
def test_invalid_problem_rejected(bad):
 with pytest.raises(ValueError):topk_assignments(*bad)
def test_complete_mapping_is_one_to_one():
 m=topk_assignments(N,R,np.eye(10),1)[0]["mapping"];assert len(set(m.values()))==10
def test_wilson_low_for_ambiguous():assert wilson_lower(400,500)<.8
