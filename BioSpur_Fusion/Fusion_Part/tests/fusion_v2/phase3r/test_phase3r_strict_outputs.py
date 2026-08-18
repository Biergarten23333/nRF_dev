import json
from types import MappingProxyType

import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.baselines import BaselineTrajectory,build_b1
from biospur_fusion.imu_pose_v1.estimator import EstimatorConfig
from biospur_fusion.imu_pose_v1.frontend import ErrorStateImuFrontend,FrontendConfig
from biospur_fusion.imu_pose_v1.joints import JOINTS
from biospur_fusion.imu_pose_v1.official import VqfNodeResult
from biospur_fusion.imu_pose_v1.pipeline import calibration_from_known,run_coupled,run_frontends
from biospur_fusion.imu_pose_v1.real_runner import RealSessionRunner
from biospur_fusion.imu_pose_v1.synthetic import generate
from biospur_fusion.imu_pose_v1.types import ImuSample,SEGMENTS
from conftest import mapping_for


def test_vqf_full_state_and_exact_lineage_are_persisted(tmp_path):
    result=VqfNodeResult(np.array([1.,1.005]),np.tile([1.,0,0,0],(2,1)),np.ones((2,3)),
                         np.full(2,.02),np.array([True,False]),(('a',),('a','b')),0.01)
    runner=RealSessionRunner.__new__(RealSessionRunner)
    manifest=runner._persist_vqf(tmp_path,{'BSF0001':result})
    with np.load(tmp_path/'vqf_full_state.npz') as stored:
        assert {'time_BSF0001','q6d_BSF0001','bias_BSF0001','bias_sigma_BSF0001','rest_BSF0001'}<=set(stored.files)
    lines=[json.loads(x) for x in (tmp_path/'vqf_lineage.jsonl').read_text().splitlines()]
    assert lines[1]['source_sample_uids']==['a','b'] and manifest['nodes']['BSF0001']['lineage_records']==2


def test_b1_soft_hinge_projection_changes_off_axis_motion():
    t=np.arange(6.)
    q={s:np.tile([1.,0,0,0],(6,1)) for s in SEGMENTS}
    q['forearm_left']=so3.exp(np.column_stack((np.linspace(0,.5,6),np.linspace(0,.4,6),np.zeros(6))))
    joints={j.name:so3.mul(so3.inv(q[j.parent]),q[j.child]) for j in JOINTS}
    sigma={s:np.full(6,.1) for s in SEGMENTS};js={j.name:np.full(6,.2) for j in JOINTS}
    b0=BaselineTrajectory('B0',t,q,joints,tuple({} for _ in t),{s:'x' for s in SEGMENTS},sigma,js,{})
    b1=build_b1(b0,{}, {'elbow_left':1.0},{'elbow_left':np.array([1.,0,0])})
    before=so3.log(so3.between(b0.joint_quaternions['elbow_left'][:-1],b0.joint_quaternions['elbow_left'][1:]))
    after=so3.log(so3.between(b1.joint_quaternions['elbow_left'][:-1],b1.joint_quaternions['elbow_left'][1:]))
    assert np.linalg.norm(after[:,1:])<np.linalg.norm(before[:,1:])


def test_qmt_source_action_is_excluded_from_production_inputs():
    values={'elbow_left':1,'elbow_right':2}
    excluded,*active=RealSessionRunner._qmt_inputs_for_action(
        '06_elbow_left',{'elbow_left':'06_elbow_left','elbow_right':'07_elbow_right'},values,values,values,values)
    assert excluded==['elbow_left'] and all('elbow_left' not in x and x['elbow_right']==2 for x in active)


def test_boot_reset_is_explicit_and_epoch_increments():
    d=generate(seed=4,duration_s=4,noise=False,irregular=False,gaps=False,biases=False,
               transients=False,outliers=False,boot_resets=True)
    rows=next(iter(d.samples_by_node.values()));frontend=ErrorStateImuFrontend(rows[0].node_id)
    outputs=frontend.run(rows)
    assert 'different_boot_reset' in d.events and max(x.reset_epoch for x in outputs)==1
    assert len({x.sample_uid.split(':')[1] for x in outputs})==2


def test_gravity_shrinks_tilt_while_global_yaw_can_grow():
    cfg=FrontendConfig(initial_orientation_sigma_rad=.3)
    frontend=ErrorStateImuFrontend('BSF0001',cfg);traces=[]
    for i in range(300):
        out=frontend.update(ImuSample('BSF0001',i*.005,i*5000,i,np.zeros(3),np.array([0,0,9.80665]),0,0,False))
        traces.append(np.diag(out.covariance[:3,:3]))
    traces=np.asarray(traces)
    assert traces[-1,:2].max()<traces[0,:2].max() and traces[-1,2]>traces[-1,:2].max()


def test_joint_information_shrinks_relative_uncertainty_and_weak_state_is_27d():
    d=generate(seed=5,duration_s=2.5,noise=False,irregular=False,gaps=False,biases=False,transients=False,outliers=False)
    mapping=mapping_for(d);front,_=run_frontends(d.samples_by_node,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()})
    cal=calibration_from_known(mapping,d.q_I_S)
    on,e_on=run_coupled(front,mapping,cal)
    off,_=run_coupled(front,mapping,cal,EstimatorConfig(enable_joint_closure=False))
    assert np.median([on[-1].joint_relative_sigma_rad[j.name] for j in JOINTS]) < np.median([off[-1].joint_relative_sigma_rad[j.name] for j in JOINTS])
    report=e_on.weak_mode_report();assert report['dimension']==27 and len(report['joints'])==9
    assert all(np.isfinite(x['covariance']).all() for x in report['joints'].values())


def test_full_pose_replay_is_bitwise_deterministic():
    d=generate(seed=7,duration_s=2,noise=True,irregular=True,gaps=False,biases=True,transients=False,outliers=False)
    mapping=mapping_for(d);cal=calibration_from_known(mapping,d.q_I_S);initial={n:so3.inv(q) for n,q in d.q_I_S.items()}
    a,_=run_frontends(d.samples_by_node,initial_q_WI=initial);fa,_=run_coupled(a,mapping,cal)
    b,_=run_frontends(d.samples_by_node,initial_q_WI=initial);fb,_=run_coupled(b,mapping,cal)
    for x,y in zip(fa,fb):
        for segment in SEGMENTS:np.testing.assert_array_equal(x.segment_quaternions_W_S[segment],y.segment_quaternions_W_S[segment])
