import numpy as np
import torch
from biospur_fusion.c2_five_calibration.mount_transport import shank_mount_proposal,transport_shank_observed
from biospur_fusion.c2_five_calibration.frontend import prepare
from biospur_fusion.c2_five_calibration.anatomy import JointModel
from biospur_fusion.c2_five_calibration.geometry import OBSERVED,sensor_positions
from test_c2_sensor_bias_transport import fixture
from test_c2_joint_kinematics import geometry


def test_raw_mount_transport_preserves_specific_force_and_functional_axis():
    episode,c=fixture();g=geometry();before=prepare(episode,c,g)
    changed,D=shank_mount_proposal(c,g,[.08,-.04]);after=prepare(episode,changed,g)
    np.testing.assert_allclose(after['orientation'][:,3:],before['orientation'][:,3:]@D,atol=1e-12)
    np.testing.assert_array_equal(after['orientation'][:,:3],before['orientation'][:,:3])
    np.testing.assert_allclose(after['acceleration_mps2'],before['acceleration_mps2'],atol=1e-12)
    np.testing.assert_array_equal(after['time_s'],before['time_s'])
    np.testing.assert_allclose(np.asarray(changed['segment_axes_in_sensor'])[:,:,1],np.asarray(c['segment_axes_in_sensor'])[:,:,1])
    angle=torch.tensor([.08,-.04],dtype=torch.float64,requires_grad=True)
    obs=torch.tensor(before['orientation'])
    actual=transport_shank_observed(obs,g,angle)
    np.testing.assert_allclose(actual.detach(),after['orientation'],atol=1e-12)
    assert torch.autograd.gradcheck(lambda x:transport_shank_observed(obs[:2],g,x),(angle,))


def test_joint_mount_bend_lever_ambiguity_preserves_sensor_positions():
    g=geometry();model=JointModel(g)
    prior=torch.eye(3,dtype=torch.float64).repeat(40,24,1,1)
    observed=prior[:,OBSERVED];p=model.initial(prior,observed);p[:,3:7]=.5
    r=model.rotation(prior,observed,p)
    c={'segment_axes_in_sensor':np.tile(np.eye(3),(5,1,1)).tolist()}
    angles=np.array([.06,-.04]);_,D=shank_mount_proposal(c,g,angles)
    new_obs=observed.clone();new_obs[:,3:]=new_obs[:,3:]@torch.tensor(D)
    pp=p.clone();pp[:,5:7]+=torch.tensor(angles)
    rr=model.rotation(prior,new_obs,pp)
    torch.testing.assert_close(rr[:,[1,2]],r[:,[1,2]],atol=1e-12,rtol=0)
    lever=torch.tensor(np.random.default_rng(4).normal(size=(5,3))*.1)
    new_lever=lever.clone();new_lever[3:]=(torch.tensor(D).transpose(-1,-2)@lever[3:,:,None]).squeeze(-1)
    torch.testing.assert_close(sensor_positions(rr,g,new_lever),sensor_positions(r,g,lever),atol=1e-12,rtol=0)


def test_shared_mount_path_requires_candidate_projection_and_has_gradient():
    import time
    import pytest
    from test_c2_shared_fit import fixture as shared_fixture
    from biospur_fusion.c2_five_calibration.shared_fit import _optimize,RegisteredHeadingPrior
    from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
    from biospur_fusion.c2_five_calibration.solver import PoseObjective
    torch.set_num_threads(1)
    actions,factors,heading,*_=shared_fixture()
    name=next(iter(actions));q=actions[name]
    o=SoftObservationObjective(PoseObjective(**q,geometry=geometry()))
    prior=RegisteredHeadingPrior(factors,heading)
    lever=torch.zeros(5,3,dtype=torch.float64)
    args=({name:o},{name:o.initial},lever,torch.zeros(4,dtype=torch.float64),lever,prior)
    with pytest.raises(ValueError,match='refreshed'):
        _optimize(*args,iterations=1,deadline=time.monotonic()+20,shared=True,fit_shank_mount=True)
    result=_optimize(*args,iterations=2,deadline=time.monotonic()+20,shared=True,
                     fit_shank_mount=True,freeze_heading=True,refresh_projection=True)
    assert result['mounting_fit_is_local_surrogate']
    assert result['shank_mount_angles_rad'].shape==(2,)
    assert np.isfinite(result['energy'])
