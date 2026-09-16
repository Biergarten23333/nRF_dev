"""Independent kinematic invariants and a synthetic inverse round trip."""
import inspect
import json
from pathlib import Path
import numpy as np
import torch
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.anatomy import JointModel, MAX_BEND, PROXIMAL
from biospur_fusion.c2_five_calibration.geometry import OBSERVED,joints_from_global
from biospur_fusion.c2_five_calibration.solver import solve_pose,bend_cosines


def geometry():
    parent=[None,0,0,0,1,2,3,4,5,6,7,8,9,9,9,12,13,14,16,17,18,19,20,21]
    offsets=np.tile([0.,.1,0.],(24,1))
    offsets[0]=0
    offsets[[18,20]]=[[.3175,0.,0.],[.245,0.,0.]]
    offsets[[19,21]]=[[-.3175,0.,0.],[-.245,0.,0.]]
    offsets[[4,5,7,8]]=[[0.,-.48,0.],[0.,-.48,0.],[0.,-.43,0.],[0.,-.43,0.]]
    return dict(parent=parent,rest_offsets_m=offsets.tolist(),bone_frame_correction=np.tile(np.eye(3),(5,1,1)).tolist())


def test_known_joint_angles_recover_and_observed_rotations_stay_exact():
    g=geometry(); model=JointModel(g)
    observed=torch.tensor(Rotation.random(100,random_state=6).as_matrix().reshape(20,5,3,3))
    prior=torch.eye(3,dtype=torch.float64).repeat(20,24,1,1)
    parameters=torch.zeros(20,9,dtype=torch.float64)
    parameters[:,3:7]=torch.linspace(.1,2.,20)[:,None]
    parameters[:,7:9]=torch.linspace(-1.,1.,20)[:,None]
    actual=model.rotation(prior,observed,parameters)
    recovered=model.initial(actual,observed)
    np.testing.assert_allclose(recovered[:,3:9],parameters[:,3:9],atol=1e-6)
    reconstructed=model.rotation(actual,observed,recovered)
    torch.testing.assert_close(reconstructed[:,PROXIMAL],actual[:,PROXIMAL],atol=1e-6,rtol=1e-6)
    assert torch.equal(actual[:,OBSERVED],observed)
    torch.testing.assert_close(torch.acos(bend_cosines(actual,g)),parameters[:,3:7],atol=1e-6,rtol=1e-6)
    torch.testing.assert_close(torch.linalg.det(actual),torch.ones(20,24,dtype=torch.float64))


def test_joint_model_gradients_and_measured_lengths():
    g=geometry();model=JointModel(g)
    prior=torch.eye(3,dtype=torch.float64).repeat(20,24,1,1)
    observed=prior[:,OBSERVED].clone()
    parameters=torch.full((20,9),.1,dtype=torch.float64,requires_grad=True)
    r=model.rotation(prior,observed,parameters);j=joints_from_global(r,g)
    j.square().sum().backward()
    assert torch.isfinite(parameters.grad).all() and parameters.grad.abs().sum()>0
    for i in range(1,24):
        expected=np.linalg.norm(g['rest_offsets_m'][i])
        np.testing.assert_allclose((j[:,i]-j[:,g['parent'][i]]).norm(dim=-1).detach(),expected,atol=1e-7)


def test_no_action_name_can_select_a_joint_angle():
    assert 'calibration_action' not in inspect.signature(solve_pose).parameters


def test_warm_start_preserves_objective_and_observed_measurements():
    g=geometry()
    t=np.arange(60)/20
    prior=np.tile(np.eye(3),(len(t),24,1,1))
    observed=prior[:,OBSERVED].copy()
    observed[:,1]=Rotation.from_euler('y',(.4+.1*np.sin(t))[:,None]).as_matrix()
    acceleration=np.zeros((len(t),5,3))
    args=(prior,observed,acceleration,np.ones(len(t),bool),t,g,np.zeros((5,3)))
    previous,first=solve_pose(*args,iterations=10)
    resumed,second=solve_pose(*args,iterations=0,initial_rotation=previous)
    np.testing.assert_allclose(resumed,previous,atol=1e-10)
    np.testing.assert_allclose(second['history'][0]['loss'],first['history'][-1]['loss'],atol=1e-10)
    np.testing.assert_array_equal(resumed[:,OBSERVED],observed)


def test_warm_start_cannot_modify_retained_imu_rotations():
    import pytest
    t=np.arange(60)/20
    prior=np.tile(np.eye(3),(len(t),24,1,1))
    previous=prior.copy()
    previous[:,18]=Rotation.from_euler('z',.2).as_matrix()
    with pytest.raises(ValueError,match='preserve the five'):
        solve_pose(prior,prior[:,OBSERVED],np.zeros((len(t),5,3)),np.ones(len(t),bool),
            t,geometry(),np.zeros((5,3)),initial_rotation=previous)


def test_old_independent_torso_parameter_layout_is_rejected():
    import pytest
    prior=torch.eye(3,dtype=torch.float64).repeat(2,24,1,1)
    with pytest.raises(ValueError,match='requires 9 parameters'):
        JointModel(geometry()).rotation(prior,prior[:,OBSERVED],torch.zeros(2,21,dtype=torch.float64))


def test_torso_correction_cannot_shrink_shoulder_span():
    g=geometry()
    g['rest_offsets_m'][13]=[.08,.06,0.]
    g['rest_offsets_m'][14]=[-.08,.06,0.]
    g['rest_offsets_m'][16]=[.12,.01,0.]
    g['rest_offsets_m'][17]=[-.12,.01,0.]
    prior=torch.tensor(Rotation.random(20*24,random_state=51).as_matrix().reshape(20,24,3,3))
    observed=prior[:,OBSERVED].clone()
    model=JointModel(g)
    parameters=model.initial(prior,observed)
    parameters[:,:3]=torch.tensor(np.random.default_rng(6).normal(size=(20,3)))
    pose=model.rotation(prior,observed,parameters)
    joints=joints_from_global(pose,g)
    np.testing.assert_allclose((joints[:,16]-joints[:,17]).norm(dim=-1),.4,atol=1e-12)
    assert torch.equal(pose[:,OBSERVED],observed)


def test_geometric_zero_and_seed_angle_preserve_noncollinear_bone_lengths():
    g=geometry()
    # Real SMPL rest bones are not exactly collinear, particularly at knees.
    for joint,offset in {4:[.07,-.48,.04],5:[-.07,-.48,.03],
                         7:[-.01,-.43,-.01],8:[.01,-.43,-.01],
                         18:[.3175,.03,.02],19:[-.3175,.03,.02]}.items():
        g['rest_offsets_m'][joint]=offset
    prior=torch.eye(3,dtype=torch.float64).repeat(10,24,1,1)
    observed=prior[:,OBSERVED].clone()
    zero=torch.zeros(10,9,dtype=torch.float64)
    model=JointModel(g)
    actual=model.rotation(prior,observed,zero)
    # The new parameter is geometric bend; zero means straight physical
    # directions, while the measured lengths/rest offsets are untouched.
    torch.testing.assert_close(bend_cosines(actual,g),torch.ones(10,4,dtype=prior.dtype),atol=1e-12,rtol=0)
    seed=model.initial(prior,observed)
    expected=torch.acos(bend_cosines(prior,g).clamp(-1.,1.))
    torch.testing.assert_close(seed[:,3:7],expected,atol=1e-12,rtol=0)
    j=joints_from_global(actual,g)
    for i in range(1,24):
        np.testing.assert_allclose((j[:,i]-j[:,g['parent'][i]]).norm(dim=-1),np.linalg.norm(g['rest_offsets_m'][i]),atol=1e-12)
