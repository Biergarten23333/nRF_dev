import numpy as np
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective,observation_rotation,solve_soft_observations


def test_zero_residual_keeps_original_observation_equation():
    torch.set_num_threads(1);actions,*_=fixture();q=next(iter(actions.values()));base=PoseObjective(**q,geometry=geometry())
    soft=SoftObservationObjective(base);lever=torch.zeros(5,3,dtype=torch.float64)
    r,t=base.evaluate(base.initial,lever);rr,tt=soft.evaluate(soft.initial,lever)
    torch.testing.assert_close(r,rr);torch.testing.assert_close(t['loss'],tt['loss'])
    p=soft.initial.clone().requires_grad_();_,terms=soft.evaluate(p,lever);terms['loss'].backward();assert torch.isfinite(p.grad).all()


def test_soft_observations_keep_pelvis_gauge_and_raw_data_immutable():
    torch.set_num_threads(1);actions,*_=fixture();q=next(iter(actions.values()));raw=q['observed'].copy();acc=q['acceleration'].copy()
    r,audit=solve_soft_observations(q,geometry(),torch.zeros(5,3,dtype=torch.float64),iterations=4,wall_limit_s=30)
    np.testing.assert_allclose(r[:,0],raw[:,0],atol=1e-12)
    np.testing.assert_array_equal(q['observed'],raw);np.testing.assert_array_equal(q['acceleration'],acc)
    assert np.isfinite(r).all();assert not audit['calibration_parameters_changed']
    correction=torch.zeros(len(raw),4,3,dtype=torch.float64);correction[:,0,1]=.1
    adjusted=observation_rotation(torch.tensor(raw),correction)
    torch.testing.assert_close(adjusted[:,0],torch.tensor(raw[:,0]))
    assert not torch.allclose(adjusted[:,1],torch.tensor(raw[:,1]))
    frozen=SoftObservationObjective(PoseObjective(**q,geometry=geometry()),freeze_observation=True)
    guess=frozen.initial.clone();guess[:,9:]=.1
    locked,_=frozen.evaluate(guess,torch.zeros(5,3,dtype=torch.float64))
    np.testing.assert_allclose(locked.detach().numpy()[:,[0,18,19,4,5]],raw,atol=1e-12)


def test_soft_pose_roundtrip_and_joint_calibration_gradient():
    import time
    from biospur_fusion.c2_five_calibration.shared_fit import _optimize
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalHeadingParameters,TemporalRegisteredHeadingPrior
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    torch.set_num_threads(1);actions,factors,heading,*_=fixture();q=next(iter(actions.values()))
    for rows in factors.values():
        for f in rows:f['measurement_time_s']=.7
    base=dict(heading_factors=factors,frozen_heading_correction_rad=heading,
              temporal_heading_curves={n:dict(time_s=[0.,1.45],correction_rad=[0.,0.]) for n in NODES[1:3]})
    model=TemporalHeadingParameters(base,q['time_s']);prior=TemporalRegisteredHeadingPrior(base,q['time_s'])
    o=SoftObservationObjective(PoseObjective(**q,geometry=geometry()));lever=torch.zeros(5,3,dtype=torch.float64)
    p=o.initial.clone();p[:,9:]=.04
    r,_=o.evaluate(p,lever);roundtrip=o.parameters_from_rotation(r)
    rr,_=o.evaluate(roundtrip,lever);torch.testing.assert_close(rr,r,atol=1e-6,rtol=0)
    result=_optimize({'_continuous':o},{'_continuous':o.initial},lever,torch.zeros(30,4,dtype=torch.float64),lever,prior,
                     iterations=2,deadline=time.monotonic()+30,shared=True,heading_model=model)
    assert np.isfinite(result['energy']);assert result['parameters']['_continuous'].shape==(30,21)


def test_locked_warm_start_rejects_unreported_sensor_changes():
    import pytest
    actions,*_=fixture();q=next(iter(actions.values()));base=PoseObjective(**q,geometry=geometry())
    free=SoftObservationObjective(base);p=free.initial.clone();p[:,9:]=.04
    r,_=free.evaluate(p,torch.zeros(5,3,dtype=torch.float64))
    with pytest.raises(ValueError,match='preserve all five'):
        SoftObservationObjective(base,freeze_observation=True).parameters_from_rotation(r)


def test_energy_audit_accounts_for_every_term_with_nonzero_corrections():
    from biospur_fusion.c2_five_calibration.optimization import _snapshot
    actions,*_=fixture(); q=next(iter(actions.values()))
    base=PoseObjective(**q,geometry=geometry())
    objective=SoftObservationObjective(base,protocol=lambda p,r: p[:,0].square().mean())
    p=objective.initial.clone()
    p[:,0]=torch.linspace(.01,.08,len(p))
    p[:,9]=torch.linspace(.02,.04,len(p))
    _,terms=objective.evaluate(p,torch.zeros(5,3,dtype=torch.float64))
    snapshot=_snapshot(terms,0,'combined',float(terms['loss']))
    assert snapshot['energy_components']['weighted_pose_smoothness_loss']>0
    assert snapshot['energy_components']['protocol_loss']>0
    np.testing.assert_allclose(sum(snapshot['energy_components'].values()),float(terms['loss']),rtol=1e-12)
