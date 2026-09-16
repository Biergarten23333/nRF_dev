import numpy as np
import torch
from biospur_fusion.c2_five_calibration.temporal_parameters import linear_basis,TemporalHeadingParameters
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def test_piecewise_basis_endpoint_hold_and_large_grid():
    knots=np.arange(27000.)
    b=linear_basis([-2.,100.25,30000.],knots)
    np.testing.assert_allclose(b@knots,[0.,100.25,26999.])
    np.testing.assert_array_equal(b.sum(1),np.ones(3))


def test_curve_parameter_transport_equals_fresh_replay_and_is_differentiable():
    curves={n:dict(time_s=[0.,2.,5.],correction_rad=[.1,.2,-.1]) for n in NODES[1:3]}
    base=dict(temporal_heading_curves=curves,frozen_heading_correction_rad={n:.1 for n in NODES[1:]})
    times=np.array([-1.,0.,1.,3.,6.]);model=TemporalHeadingParameters(base,times)
    coefficients=torch.linspace(-.2,.3,model.size,dtype=torch.float64,requires_grad=True)
    delta=model.increments(coefficients);fresh=model.frontend(coefficients.detach().numpy())
    for i,n in enumerate(NODES[1:]):
        np.testing.assert_allclose(temporal_heading(times,n,fresh)-temporal_heading(times,n,base),delta[:,i].detach(),atol=1e-12)
    obs=torch.eye(3,dtype=torch.float64).expand(len(times),5,3,3)
    acc=torch.ones(len(times),5,3,dtype=torch.float64)
    r,a=transport_heading(obs,acc,delta)
    for i in range(len(times)):
        rr,aa=transport_heading(obs[i:i+1],acc[i:i+1],delta[i])
        torch.testing.assert_close(r[i],rr[0]);torch.testing.assert_close(a[i],aa[0])
    (r.sum()+a.sum()).backward();assert torch.isfinite(coefficients.grad).all()
    assert all('joint_increment_rad' not in c for c in base['temporal_heading_curves'].values())
    torch.testing.assert_close(r[:,0],obs[:,0]);torch.testing.assert_close(a[:,0],acc[:,0])


def test_temporal_registered_factors_and_protocol_reduce_to_constant_case():
    from test_c2_phase_registration import raw_fixture
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalRegisteredHeadingPrior
    from biospur_fusion.c2_five_calibration.arm_protocol import build_arm_protocol
    episodes,base,actions,contracts=raw_fixture()
    grid=next(iter(actions.values()))['time_s']
    prior=TemporalRegisteredHeadingPrior(base,grid)
    tape=build_arm_protocol(episodes,base,contracts,actions,prior.all_information,conditional_only=True)
    prior.bind_arm_protocol(tape,actions)
    delta=torch.tensor([.1,-.2,.3,-.4],dtype=torch.float64)
    repeated=delta.expand(len(grid),4)
    torch.testing.assert_close(prior.energy(delta),prior.energy(repeated))
    rotations=torch.eye(3,dtype=torch.float64).expand(len(grid),24,3,3)
    for name in actions:
        torch.testing.assert_close(prior.energy_for_action(name,delta,rotations),prior.energy_for_action(name,repeated,rotations))


def test_joint_optimizer_accumulates_curve_pose_and_regularizer_gradients():
    import time
    from test_c2_shared_fit import fixture,geometry
    from biospur_fusion.c2_five_calibration.solver import PoseObjective
    from biospur_fusion.c2_five_calibration.shared_fit import _optimize
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalRegisteredHeadingPrior
    torch.set_num_threads(1)
    actions,factors,heading,*_=fixture();q=next(iter(actions.values()))
    for rows in factors.values():
        for f in rows:f['measurement_time_s']=.7
    base=dict(heading_factors=factors,frozen_heading_correction_rad=heading,
              temporal_heading_curves={n:dict(time_s=[0.,1.45],correction_rad=[0.,0.]) for n in NODES[1:3]})
    model=TemporalHeadingParameters(base,q['time_s']);prior=TemporalRegisteredHeadingPrior(base,q['time_s'])
    o=PoseObjective(**q,geometry=geometry());lever=torch.zeros(5,3,dtype=torch.float64)
    result=_optimize({'_continuous':o},{'_continuous':o.initial},lever,torch.zeros(30,4,dtype=torch.float64),lever,prior,
                     iterations=2,deadline=time.monotonic()+30,shared=True,heading_model=model)
    assert np.isfinite(result['energy']);assert torch.isfinite(result['heading_coefficients']).all()
    assert result['heading_coefficients'].abs().max()>0


def test_raw_time_check_handles_noncommuting_filter_without_looser_tolerance():
    from test_c2_phase_registration import raw_fixture
    from biospur_fusion.c2_five_calibration.frontend import prepare
    from biospur_fusion.c2_five_calibration.temporal_transport_check import check_raw_temporal_transport
    episodes,base,*_=raw_fixture();episode=episodes['06_elbow_left']
    base['temporal_heading_curves']={n:dict(time_s=[0.,15.,30.],correction_rad=[0.,0.,0.]) for n in NODES[1:3]}
    geometry=dict(bone_frame_correction=np.tile(np.eye(3),(5,1,1)).tolist())
    def pack(prepared):
        return dict(time_s=prepared['time_s'][::3],observed=prepared['orientation'][::3],acceleration=prepared['acceleration_mps2'][::3])
    old=pack(prepare(episode,base,geometry));model=TemporalHeadingParameters(base,old['time_s'])
    coeff=np.linspace(-.3,.4,model.size);new=pack(prepare(episode,model.frontend(coeff),geometry))
    result=check_raw_temporal_transport(episode,base,geometry,coeff,old,new)
    assert result['raw_time_transport_verified']
    assert result['acceleration_max_error_mps2']<1e-9
    assert result['surrogate_filter_commutator_max_mps2']>1e-6


def test_resumed_heading_checkpoint_reproduces_original_objective():
    import time
    import pytest
    from test_c2_shared_fit import fixture,geometry
    from biospur_fusion.c2_five_calibration.solver import PoseObjective
    from biospur_fusion.c2_five_calibration.shared_fit import _optimize
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalRegisteredHeadingPrior
    torch.set_num_threads(1)
    actions,factors,heading,*_=fixture();q=next(iter(actions.values()))
    for rows in factors.values():
        for factor in rows:factor['measurement_time_s']=.7
    baseline=dict(heading_factors=factors,frozen_heading_correction_rad=heading)
    model=TemporalHeadingParameters(baseline,q['time_s'])
    prior=TemporalRegisteredHeadingPrior(baseline,q['time_s'])
    objective=PoseObjective(**q,geometry=geometry());nominal=torch.zeros(5,3,dtype=torch.float64)
    delta=torch.zeros(30,4,dtype=torch.float64)
    kwargs=dict(deadline=time.monotonic()+30,shared=True,heading_model=model)
    first=_optimize({'_continuous':objective},{'_continuous':objective.initial},nominal,delta,nominal,prior,iterations=2,**kwargs)
    rows=[]
    resumed=_optimize({'_continuous':objective},first['parameters'],first['levers'],delta,nominal,prior,
        iterations=0,initial_heading_coefficients=first['heading_coefficients'],expected_initial_energy=first['energy'],progress=rows.append,**kwargs)
    assert resumed['energy']==pytest.approx(first['energy'],abs=1e-12)
    torch.testing.assert_close(resumed['rotations']['_continuous'],first['rotations']['_continuous'])
    assert rows[0]['step']==0
    with pytest.raises(ValueError,match='original objective'):
        _optimize({'_continuous':objective},first['parameters'],first['levers'],delta,nominal,prior,
            iterations=0,initial_heading_coefficients=first['heading_coefficients'],expected_initial_energy=first['energy']+1,**kwargs)
