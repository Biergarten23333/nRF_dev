import copy
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation
from test_c2_body_feasibility import rig,poses
from biospur_fusion.c2_five_calibration.body_feasibility import BodyFeasibility,segment_core_depth
from biospur_fusion.c2_five_calibration.geometry import joints_from_global,OBSERVED
from biospur_fusion.c2_five_calibration.anatomy import JointModel,TORSO


def candidate():
    g=rig();g['torso_pose_model']='relative_prior';g['body_feasibility']['frame_model']='skeletal_chord'
    return g


def test_incompatible_body_frame_is_rejected():
    g=candidate();g['body_feasibility'].pop('frame_model')
    with pytest.raises(ValueError,match='compatible'):BodyFeasibility(g)


def test_rigid_neutral_parity_and_global_rotation_invariance():
    g=candidate();b=BodyFeasibility(g);r=poses();valid=torch.ones(2,dtype=torch.bool)
    legacy=BodyFeasibility(rig()).evaluate(r,valid);new=b.evaluate(r,valid)
    torch.testing.assert_close(legacy['inner_depth_proxy_m'],new['inner_depth_proxy_m'])
    turn=torch.tensor(Rotation.from_rotvec([.5,-.3,.2]).as_matrix())
    rotated=b.evaluate(turn@r,valid)
    torch.testing.assert_close(rotated['inner_depth_proxy_m'],new['inner_depth_proxy_m'])


def test_bent_chord_crossing_and_front_clearance_with_fixed_radii():
    g=candidate();b=BodyFeasibility(g);r=poses()[:1]
    r[:,3]=torch.tensor(Rotation.from_euler('x',.6).as_matrix())
    r[:,6]=torch.tensor(Rotation.from_euler('x',.9).as_matrix())
    j=joints_from_global(r,g);frame,centre=b.envelope_frame(r,j)
    radius=torch.tensor(b.spec['inner_radii_m'],dtype=r.dtype)
    a=centre-frame[:,:,0];z=centre+frame[:,:,0]
    local=lambda p:(frame.transpose(-1,-2)@(p-centre)[...,None]).squeeze(-1)
    assert segment_core_depth(local(a),local(z),torch.zeros(3),radius).item()>0
    offset=frame[:,:,2]*(radius[2]+.02)
    assert segment_core_depth(local(a+offset),local(z+offset),torch.zeros(3),radius).item()==0
    assert b.spec['inner_radii_m']==g['body_feasibility']['inner_radii_m']
    assert torch.linalg.det(frame).item()==pytest.approx(1.)


def test_relative_model_keeps_configuration_and_observations():
    g=candidate();m=JointModel(g);prior=poses();prior[:,13]=torch.tensor(Rotation.from_euler('z',.1).as_matrix())
    observed=prior[:,OBSERVED].clone();p=m.initial(prior,observed);p[:,:3]=torch.tensor([.1,.2,-.1])
    r=m.rotation(prior,observed,p)
    torch.testing.assert_close(r[:,OBSERVED],observed)
    before=joints_from_global(prior,g);after=joints_from_global(r,g)
    torch.testing.assert_close((before[:,16]-before[:,17]).norm(dim=-1),(after[:,16]-after[:,17]).norm(dim=-1))
    p=p.requires_grad_();value=BodyFeasibility(g).evaluate(m.rotation(prior,observed,p),torch.ones(2,dtype=torch.bool))['body_loss']
    value.backward();assert torch.isfinite(p.grad).all()
