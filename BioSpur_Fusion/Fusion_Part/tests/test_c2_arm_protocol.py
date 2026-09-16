"""Independent frame, information, ambiguity and derivative controls."""
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.arm_protocol import (
    ArmProtocolRow,ArmProtocolTape,cell_information,horizontal_residual,yaw_vectors,
    build_arm_protocol,
)


def fixture(yaw, *, sensor_error=0., multiple=2):
    yaw=np.asarray(yaw);n=len(yaw);axis=np.array([1.,0.,0.])
    torso=Rotation.from_euler('y',yaw[:,None]).as_matrix()
    direction=Rotation.from_euler('y',(yaw+sensor_error)[:,None]).apply(np.tile(axis,(n,1)))
    row=ArmProtocolRow('06_elbow_left',0,np.arange(n),direction,axis,np.ones(n)/n,multiple,{})
    pose=np.tile(np.eye(3),(n,24,1,1));pose[:,9]=torso
    return ArmProtocolTape([row],np.zeros(4)),torch.tensor(pose)


def test_torso_motion_is_not_sensor_heading_error_but_arm_only_error_is():
    yaw=np.linspace(-.8,.8,101)
    tape,pose=fixture(yaw);zero=torch.zeros(4,dtype=torch.float64)
    assert tape.energy_for_action('06_elbow_left',zero,pose).item()<1e-25
    assert tape.energy_for_action('06_elbow_left',zero,pose,reference='pelvis').item()>.2
    tape,pose=fixture(yaw,sensor_error=.3)
    assert tape.energy_for_action('06_elbow_left',zero,pose).item()==pytest.approx(.09)
    delta=zero.clone();delta[0]=-.3
    assert tape.energy_for_action('06_elbow_left',delta,pose).item()<1e-25


def test_common_torso_and_sensor_heading_ambiguity_is_preserved():
    tape,pose=fixture(np.array([.1,.2,.3]))
    delta=torch.zeros(4,dtype=torch.float64);delta[0]=.4
    pose[:,9]=torch.tensor(Rotation.from_euler('y',np.array([.5,.6,.7])[:,None]).as_matrix())
    assert tape.energy_for_action('06_elbow_left',delta,pose).item()<1e-25


def test_axial_sign_and_directed_left_right_are_distinct():
    d=torch.tensor([[1.,0.,0.]],dtype=torch.float64)
    v=yaw_vectors(d,torch.tensor(.2))
    torch.testing.assert_close(horizontal_residual(d,v,2),horizontal_residual(-d,v,2))
    assert abs(horizontal_residual(-d,v,1).item())>2.
    torch.testing.assert_close(horizontal_residual(d,v,1),horizontal_residual(-d,-v,1))
    with pytest.raises(ValueError,match='undefined'):
        horizontal_residual(d,torch.tensor([[0.,1.,0.]]),2)


def test_heading_and_torso_gradients_match_independent_finite_differences():
    tape,pose=fixture(np.array([.1,.2,.3]),sensor_error=.2)
    delta=torch.tensor([.03,0.,0.,0.],requires_grad=True,dtype=torch.float64)
    energy=tape.energy_for_action('06_elbow_left',delta,pose);energy.backward()
    eps=1e-6;plus=delta.detach().clone();minus=plus.clone();plus[0]+=eps;minus[0]-=eps
    fd=(tape.energy_for_action('06_elbow_left',plus,pose)-tape.energy_for_action('06_elbow_left',minus,pose))/(2*eps)
    assert delta.grad[0].item()==pytest.approx(fd.item(),abs=1e-8)
    angle=torch.tensor(.03,requires_grad=True,dtype=torch.float64)
    def evaluate(a):
        # Independent differentiable rotation, rather than the production exp.
        z=a*0.;o=z+1.;c=torch.cos(a);s=torch.sin(a)
        r=torch.stack((c,z,s,z,o,z,-s,z,c)).reshape(3,3)
        p=pose.clone();p[:,9]=r@pose[:,9]
        return tape.energy_for_action('06_elbow_left',delta.detach(),p)
    evaluate(angle).backward()
    fd=(evaluate(angle.detach()+eps)-evaluate(angle.detach()-eps))/(2*eps)
    assert abs(angle.grad.item())>.1
    assert angle.grad.item()==pytest.approx(fd.item(),abs=1e-8)


def test_duration_information_is_not_multiplied_by_sample_count_or_gaps():
    grid=np.arange(.025,1.,.05);raw=np.arange(.0025,1.,.005)
    a=cell_information(raw,np.ones(len(raw)),grid,0.,1.,3.)
    dense=np.arange(.00125,1.,.0025)
    b=cell_information(dense,np.ones(len(dense)),grid,0.,1.,3.)
    np.testing.assert_allclose(a,b,atol=1e-12)
    assert a.sum()==pytest.approx(3.)
    # Availability is applied AFTER allocation. Losing half of the cells
    # cannot silently double the weight on the remaining cells.
    assert a[:10].sum()==pytest.approx(1.5)


def test_scalar_mean_cannot_replace_time_correlated_torso_targets():
    tape,pose=fixture(np.array([-.7,.7]))
    zero=torch.zeros(4,dtype=torch.float64)
    assert tape.energy_for_action('06_elbow_left',zero,pose).item()<1e-25
    pose[:,9]=torch.eye(3)
    assert tape.energy_for_action('06_elbow_left',zero,pose).item()==pytest.approx(.49)


def test_raw_registration_builds_four_conditional_rows_without_tpose():
    from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    time=np.arange(300)/200;grid=time[::10]
    names=['02_t_pose','04_shoulder_left','05_shoulder_right','06_elbow_left','07_elbow_right']
    episodes={};factors={n:[] for n in NODES[1:]}
    for name in names:
        raw=np.zeros((300,11));raw[:,0]=time;raw[:,1]=1.;raw[:,7]=9.80665
        episodes[name]={n:dict(imu=raw.copy()) for n in NODES}
    for side,node in enumerate(NODES[1:3]):
        for name,kind,axis in [('02_t_pose','directed_side',None),
                (names[1+side],'axis_forward','x'),(names[3+side],'axis_lateral','y')]:
            factors[node].append(dict(action=name,source_role=kind,measurement_delta_rad=.1,
                quality=.8,base_sigma_deg=25.,used_for_frozen_heading=axis is None))
            if axis:
                rows=episodes[name][node]['imu']
                q=Rotation.from_euler(axis,time[:,None]).as_quat()
                rows[:,1:5]=q[:,[3,0,1,2]];rows[:,8 if axis=='x' else 9]=1.
    base={n:0. for n in NODES[1:]}
    c=dict(heading_factors=factors,functional_yaw_rad=[0.]*5,frozen_heading_correction_rad=base,
        pelvis_closure_rad=0.,initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist())
    actions={n:dict(time_s=grid,valid=np.ones(len(grid),bool)) for n in names}
    contracts={n:dict(lo=0.,hi=time[-1]) for n in names}
    registration=RegisteredHeadingPrior(factors,base)
    tape=build_arm_protocol(episodes,c,contracts,actions,registration.all_information,conditional_only=True)
    registration.bind_arm_protocol(tape,actions)
    assert {row.action for row in tape.rows}==set(names[1:]) and len(tape.rows)==4
    assert all(len(row.index)==len(grid) for row in tape.rows)
    assert all(set(registration.information[n])=={'02_t_pose'} for n in NODES[1:3])
