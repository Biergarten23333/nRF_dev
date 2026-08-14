import sys
from pathlib import Path
import numpy as np

TOOLS=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(TOOLS/'body_calibration_v1'))
import analyze_constrained_capture as subject

def test_exact_three_swap_space_and_expected_side_evidence():
    energy={n:{} for n in subject.base.NODES}
    for name,left,right in subject.PAIRS:
        for action in subject.ACTIONS[name]:
            energy[left][action]=10.0 if action.startswith('left_') else 1.0
            energy[right][action]=10.0 if action.startswith('right_') else 1.0
    ranked=subject.score_hypotheses(energy)
    assert len(ranked)==8
    assert ranked[0]['bits']==(0,0,0)
    assert ranked[0]['mapping']['BSFC2CC']=='Pelvis'

def test_session_body_frame_is_one_proper_rotation():
    mapping={**subject.FIXED,'BSFAA61':'Elbow_L','BSF1120':'Elbow_R','BSFB165':'Wrist_L','BSFEC35':'Wrist_R','BSF6C53':'Ankle_L','BSF8BC4':'Ankle_R'}
    xyz={'Central':(0,0,1800),'Pelvis':(0,0,1000),'Elbow_L':(-500,0,1500),'Elbow_R':(500,0,1500),'Wrist_L':(-900,0,1500),'Wrist_R':(900,0,1500),'Knee_L':(-200,0,500),'Knee_R':(200,0,500),'Ankle_L':(-200,0,0),'Ankle_R':(200,0,0)}
    t=np.arange(0,41,dtype=float);segments=[]
    for action,start,stop in [('initial_still',0,5),('t_pose',10,15),('left_knee',20,24),('right_knee',25,29),('left_heel',30,34),('right_heel',35,39)]:segments.append({'action':action,'start':start,'stop':stop,'selected':True})
    data={}
    for node,slot in mapping.items():
        p=np.zeros((len(t),6));p[:,0]=t;p[:,1:4]=xyz[slot]
        if slot=='Knee_L':p[20:25,2]+=100
        if slot=='Knee_R':p[25:30,2]+=100
        if slot=='Ankle_L':p[30:35,2]-=100
        if slot=='Ankle_R':p[35:40,2]-=100
        data[node]=p
    R,manifest=subject.body_frame(data,mapping,segments)
    assert np.allclose(R@R.T,np.eye(3),atol=1e-12)
    assert np.linalg.det(R)>0.999999
    assert manifest['vertical_chain_angle_rms_deg']<1e-9
