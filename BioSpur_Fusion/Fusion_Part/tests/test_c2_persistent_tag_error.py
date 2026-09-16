from dataclasses import replace
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_uwb_root_world.persistent_tag_error import PersistentTagErrorFilter
from biospur_fusion.c2_uwb_root_world.tight_range import update_raw_ranges,RawRangeUpdateConfig
from biospur_fusion.root_r3.estimator import propagate_inertial,RootFilterConfig
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at

NODES=tuple('node'+str(i) for i in range(10))
CFG=RootFilterConfig()
TRUTH=np.array([2.,1.25,1.])


def owner():
    root=RootState(0.,np.r_[TRUTH,np.zeros(6)],np.diag([.01]*9))
    return PersistentTagErrorFilter(root,NODES)


def update(f,t,n,target,basis=np.eye(3),rate=np.zeros((3,3))):
    row=replace(row_at(t,target),node=NODES[n])
    decision=f.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,
        offset_world_m=np.zeros(3),offset_velocity_world_mps=np.zeros(3),
        basis_world_from_local=basis,basis_velocity_world_from_local=rate,
        reference_epoch_s=t)
    assert decision.accepted
    return row


def test_no_ranges_exact_inertial_and_zero_residual_mean():
    f=owner(); root=f.root
    for i in range(1,21):
        t=i*.005;force=np.array([.03,-.02,9.85665]);r=Rotation.from_rotvec([0,0,.01*i]).as_matrix()
        root,_=propagate_inertial(root,t,force,r,CFG);f.propagate(t,force,r,CFG)
        np.testing.assert_array_equal(f.root.vector,root.vector)
        np.testing.assert_array_equal(f.root.covariance,root.covariance)
    f=owner(); f.propagate(.1,[0,0,9.80665],np.eye(3),CFG)
    anchor=TRUTH+np.array([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],[0,0,1],[0,0,-1],[1,0,0],[0,1,0]])
    row=replace(row_at(.1,TRUTH),node=NODES[0],ranges_mm=(1000,)*8)
    before=f.root.vector.copy()
    d=f.update_ranges(row,anchors_m=anchor,clock=CLOCK,offset_world_m=np.zeros(3),
        offset_velocity_world_mps=np.zeros(3),basis_world_from_local=np.eye(3),
        basis_velocity_world_from_local=np.zeros((3,3)),reference_epoch_s=.1)
    assert d.accepted
    np.testing.assert_array_equal(f.root.vector,before)
    np.testing.assert_array_equal(f.error,np.zeros((10,3)))


def test_static_opposing_nodes_do_not_repeatedly_drive_common_velocity():
    f=owner(); baseline=f.root; dz=[]; old_dz=[]; velocities=[]; old_v=[]
    for i in range(1,401):
        t=i*.012;n=(i-1)%10
        f.propagate(t,[0,0,9.80665],np.eye(3),CFG)
        baseline,_=propagate_inertial(baseline,t,[0,0,9.80665],np.eye(3),CFG)
        before=f.root.position_m.copy();old=baseline.position_m.copy()
        row=update(f,t,n,TRUTH+[0,0,(n-4.5)*.04])
        baseline,d=update_raw_ranges(baseline,row,anchors_m=ANCHORS,clock=CLOCK)
        assert d.accepted
        dz.append(f.root.position_m[2]-before[2]);old_dz.append(baseline.position_m[2]-old[2])
        velocities.append(f.root.velocity_mps[2]);old_v.append(baseline.velocity_mps[2])
    assert np.sum(np.abs(dz[-100:])) < np.sum(np.abs(old_dz[-100:]))*.3
    assert np.std(velocities[-100:]) < np.std(old_v[-100:])*.3
    assert np.linalg.norm(f.covariance[:9,9:])>0
    assert np.linalg.eigvalsh(f.covariance).min()>0


@pytest.mark.parametrize('noise_density',[.30,.03])
def test_real_acceleration_and_rotating_local_offsets_preserve_common_motion(noise_density):
    f=owner();inertial=f.root
    config=replace(CFG,inertial_acceleration_noise_mps2_sqrt_hz=noise_density)
    acceleration=np.array([.02,0,.10]);bias=np.array([0,0,.03])
    errors=np.array([[.06*np.cos(i),.04*np.sin(i),(i-4.5)*.02] for i in range(10)])
    for i in range(1,401):
        t=i*.012;n=(i-1)%10;force=acceleration+bias+[0,0,9.80665]
        f.propagate(t,force,np.eye(3),config)
        inertial,_=propagate_inertial(inertial,t,force,np.eye(3),config)
        basis=Rotation.from_rotvec([0,.2*t,0]).as_matrix()
        skew=np.array([[0,0,.2],[0,0,0],[-.2,0,0]])
        target=TRUTH+.5*acceleration*t*t+basis@errors[n]
        update(f,t,n,target,basis,skew@basis)
    truth=TRUTH+.5*acceleration*t*t
    assert np.linalg.norm(f.root.position_m-truth)<.10
    assert np.linalg.norm(f.root.position_m-truth)<np.linalg.norm(inertial.position_m-truth)*.5
    assert np.linalg.norm(f.root.velocity_mps-acceleration*t)<.10
    assert f.root.position_m[2]-TRUTH[2]>.8
    assert np.linalg.eigvalsh(f.covariance).min()>0
