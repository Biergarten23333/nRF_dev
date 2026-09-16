"""Reference-version and common-body timeline regression checks."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes.articulated_reference import (
    SEGMENTS, action_key, corrected_sample_times, sample_body,
)


def body():
    archive={}
    for i,segment in enumerate(SEGMENTS):
        base='trajectory/05/'+segment
        archive[base+'/time_root_s']=np.array([10.,10.005,10.01])
        rotation=Rotation.from_rotvec(np.array([[0.,0.,v] for v in np.deg2rad([0.,20.,40.])]))*Rotation.from_rotvec([np.deg2rad(i*5.),0.,0.])
        archive[base+'/quat_world_segment_wxyz']=rotation.as_quat()[:,[3,0,1,2]]
        archive[base+'/mask']=np.array([True,True,True])
    return archive


def test_action_identity_does_not_shift_after_unrecorded_01():
    assert action_key('06_elbow_left')=='05'
    assert action_key('07_elbow_right')=='06'
    assert action_key('19_heel_to_butt_right')=='18'
    assert action_key('H01_boxing')=='H01_boxing'


def test_common_clock_roundtrip_and_native_h_epoch():
    target=dict(a_ns_per_us=1000.2,b_ns=321.)
    timer=np.array([10e6,10e6+5000])
    expected=(timer*target['a_ns_per_us']+target['b_ns'])*1e-9
    np.testing.assert_allclose(corrected_sample_times(timer*1e-6,target),expected,atol=1e-12)
    source=dict(a_ns_per_us=999.8,b_ns=754.)
    grid_start=timer[0]*source['a_ns_per_us']+source['b_ns']
    elapsed=(timer-timer[0])*source['a_ns_per_us']*1e-9
    np.testing.assert_allclose(corrected_sample_times(elapsed,target,holdout=True,
        sync={'actual_common_interval_ns':[grid_start,0]},source_clock=source),expected,atol=1e-12)


def test_common_rotation_preserves_relative_joint_and_exact_sample():
    archive=body();t=archive['trajectory/05/pelvis/time_root_s']
    r,valid=sample_body(archive,'05',np.array([10.,10.0025,10.01]),t)
    assert valid.all()
    relative=np.swapaxes(r['upper_arm_left'],1,2)@r['forearm_left']
    np.testing.assert_allclose(relative,np.repeat(relative[:1],3,axis=0),atol=1e-12)
    archive['trajectory/05/forearm_left/mask'][1]=False
    _,valid=sample_body(archive,'05',np.array([10.,10.0025,10.01]),t)
    assert valid.tolist()==[True,False,True]


def test_mismatched_body_grid_is_rejected():
    archive=body();t=archive['trajectory/05/pelvis/time_root_s']
    archive['trajectory/05/forearm_left/time_root_s']+=.001
    with pytest.raises(ValueError,match='share a timeline'):
        sample_body(archive,'05',np.array([10.0025]),t)


def test_review_fk_matches_authoritative_post_fk_output():
    from tools.build_imucoco_review import reference_fk, REFERENCE_TO_WORLD
    from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config
    from biospur_fusion.c2_coupled_progressive.renderer import joints_for_frame, display_models
    from biospur_fusion.c2_coupled_progressive.output_coordinates import OUTPUT_COORDINATE_SCHEMA
    archive=body();config=load_effective_config()
    convention=np.diag([-1.,1.,1.])
    trajectory={'trajectory':{'05':{}},'output_coordinate_convention':{
        'schema':OUTPUT_COORDINATE_SCHEMA,'matrix_world_output_from_internal':convention}}
    for segment in SEGMENTS:
        base='trajectory/05/'+segment
        trajectory['trajectory']['05'][segment]={name:archive[base+'/'+name]
            for name in ('time_root_s','quat_world_segment_wxyz','mask')}
    rotations,_=sample_body(archive,'05',np.array([10.005]),np.array([10.,10.005,10.01]))
    actual=reference_fk(rotations,config)@REFERENCE_TO_WORLD@convention.T
    expected=joints_for_frame(trajectory,'05',1,display_models(config)[1],config)
    names=['pelvis_center','shoulder_left','elbow_left','wrist_left','shoulder_right',
           'elbow_right','wrist_right','hip_left','knee_left','ankle_left',
           'hip_right','knee_right','ankle_right']
    np.testing.assert_allclose(actual[0],np.array([expected[name] for name in names]),atol=1e-12)
