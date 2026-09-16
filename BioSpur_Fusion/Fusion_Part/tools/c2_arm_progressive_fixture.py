"""Timestamped phase adapter for the existing independent analytic fixture.

No navigation/transition model is claimed: the analytic actions are separate
windows with preserved gaps. 03 is retained as a passive pelvis-motion window
in the arm-only prototype, never mislabeled as new arm calibration evidence.
"""
import numpy as np
from scipy.spatial.transform import Rotation
from c2_arm_smoke_fixture import fixture
from biospur_fusion.c2_five_calibration.progressive.arm_factors import PHASES
from biospur_fusion.c2_sparse_nodes.inputs import NODES

ACTION_ORDER=('00_initial_still','02_t_pose','03_pelvis_hula_circle','04_shoulder_left',
              '05_shoulder_right','06_elbow_left','07_elbow_right')


def phase_stream(coupled=False, *, early_tpose_bias_deg=0.):
    actions,truth=fixture(coupled)
    # Independent synthetic pelvis exercise, with wrist orientation following
    # the moving body. Gyro/accel not used for 03 by this arm-only mechanism.
    base={n:{'imu':v['imu'].copy()} for n,v in actions['00_initial_still'].items()}
    t=base[NODES[0]]['imu'][:,0]
    amp=np.where(t<15,.08,.2)
    circle=Rotation.from_rotvec(np.column_stack((amp*np.sin(t),amp*np.cos(t),.04*np.sin(.5*t)))).as_matrix()
    for n in NODES:
        rows=base[n]['imu'];r=Rotation.from_quat(rows[:,[2,3,4,1]]).as_matrix()
        rows[:,1:5]=Rotation.from_matrix(circle@r).as_quat()[:,[3,0,1,2]]
    actions['03_pelvis_hula_circle']=base
    if early_tpose_bias_deg:
        turn=Rotation.from_euler('z',early_tpose_bias_deg,degrees=True).as_matrix()
        for n in NODES[1:3]:
            rows=actions['02_t_pose'][n]['imu']
            r=Rotation.from_quat(rows[:,[2,3,4,1]]).as_matrix()
            rows[:,1:5]=Rotation.from_matrix(turn@r).as_quat()[:,[3,0,1,2]]
    phases=[]
    for action,phase,duration in PHASES:
        offset=15. if phase=='pronation' else 0.
        base_time=1000.+40*ACTION_ORDER.index(action)
        start=base_time+offset;stop=start+duration;rows={}
        for n in NODES:
            original=actions[action][n]['imu']
            keep=(original[:,0]>=offset)&(original[:,0]<offset+duration)
            value=original[keep].copy();value[:,0]+=base_time;rows[n]=value
        phases.append(dict(phase_id=action+':'+phase,start=start,stop=stop,rows=rows))
    return phases,truth


def deliver(session,event, *, parts=1, prefix='input'):
    count=len(event['rows'][NODES[0]])
    for i,index in enumerate(np.array_split(np.arange(count),parts)):
        session.ingest(event['phase_id'],event['start'],event['stop'],
            {n:rows[index] for n,rows in event['rows'].items()},
            chunk_id=prefix+':'+event['phase_id']+':'+str(i),final=i==parts-1)
    return session.snapshot()
