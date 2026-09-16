"""Synthetic shared calibration mechanics; no files, model assets, or recordings.

Predeclared recovery: [12,-9,8,-11] degree injected constant errors, broad
25-degree balanced protocol observations, <5 degree per-limb recovery. Static
pose/zero-force tape has no data-only yaw information: this is deliberately
conditional recovery, not a raw-sensor observability claim. All 19 actions,
9 pose DOFs/frame and 15 levers participate without fixed true proximal data.
"""
import copy
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.anatomy import PROXIMAL,TORSO
from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.operators import filtered
from biospur_fusion.c2_five_calibration.shared_fit import (
    RegisteredHeadingPrior,fit_shared_orientation,_objectives)
from biospur_fusion.c2_five_calibration.shared_orientation import transport_heading
from biospur_fusion.c2_five_calibration.solver import PoseObjective,acceleration_residual,solve_pose
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from test_c2_joint_kinematics import geometry
from biospur_fusion.c2_five_calibration.arm_protocol import ArmProtocolTape,build_arm_protocol

TRUTH=np.deg2rad([12.,-9.,8.,-11.])


@pytest.fixture(autouse=True)
def one_torch_thread():
    previous=torch.get_num_threads();torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def fixture():
    time=np.arange(30)/20;actions={}
    names=[f'{i:02d}_synthetic' for i in range(20) if i!=1]
    for index,name in enumerate(names):
        observed=np.tile(np.eye(3),(30,5,1,1))
        observed[:,1:]=Rotation.from_euler('y',-TRUTH[:,None]).as_matrix()
        prior=np.tile(np.eye(3),(30,24,1,1))
        # An imperfect prior has small balanced proximal rotations, not true
        # missing-node observations. Static poses need no specific forcing.
        prior[:,PROXIMAL]=Rotation.from_euler('z',.08*(-1)**index).as_matrix()
        actions[name]=dict(time_s=time.copy(),prior=prior,observed=observed,
            acceleration=np.zeros((30,5,3)),valid=np.ones(30,bool))
    factors={n:[] for n in NODES[1:]}
    for i,node in enumerate(NODES[1:]):
        for action,deviation in zip(names[1:4],[-.08,0.,.08]):
            # Independently generate a horizontal direction with a broad,
            # balanced intent departure, then corrupt its navigation gauge.
            direction=Rotation.from_euler('y',deviation-TRUTH[i]).apply([1.,0.,0.])
            measured=-np.arctan2(-direction[2],direction[0])
            factors[node].append(dict(action=action,source_role='axis_lateral',
                measurement_delta_rad=measured,quality=.9,base_sigma_deg=25.))
    calls=[]
    def replay(delta):
        calls.append(np.array(delta,copy=True)); result=copy.deepcopy(actions)
        for q in result.values():
            r,a=transport_heading(torch.tensor(q['observed']),torch.tensor(q['acceleration']),torch.tensor(delta))
            q['observed'],q['acceleration']=r.numpy(),a.numpy()
            # Deterministic input-dependent fake prior: no true latent pose
            # returned, and changed features change its proximal prediction.
            q['prior'][:,PROXIMAL]=Rotation.from_euler('y',.05*np.sum(delta)).as_matrix()@q['prior'][:,PROXIMAL]
        return dict(actions=result,binding=dict(total_delta=np.asarray(delta).tolist()),
            frontend=dict(synthetic=True,delta=np.asarray(delta).tolist()))
    return actions,factors,{n:0. for n in NODES[1:]},replay,calls


def test_extracted_objective_matches_original_equation_and_warm_start():
    actions,*_=fixture();q=next(iter(actions.values()));g=geometry()
    objective=PoseObjective(**q,geometry=g)
    p=objective.initial.clone();p[:,:3]+=.04
    lever=torch.zeros(5,3,dtype=torch.float64)
    r,terms=objective.evaluate(p,lever)
    residual=acceleration_residual(r,objective.acceleration,g,lever)[objective.good]
    angular=((r[:,PROXIMAL+TORSO]-objective.target[:,PROXIMAL+TORSO])/.5).square().mean()
    position,velocity,_=objective.tracking.losses(r,g,lambda x:filtered(x,1),objective.good)
    change=p-objective.initial
    expected=(residual/.75).square().mean()+.15*(angular+position+velocity)+.1*((change[1:]-change[:-1])/.12).square().mean()
    torch.testing.assert_close(terms['loss'],expected,atol=0,rtol=0)
    r,report=solve_pose(**q,geometry=g,levers=lever,iterations=3)
    r2,report2=solve_pose(**q,geometry=g,levers=lever,iterations=0,initial_rotation=r)
    np.testing.assert_allclose(r2,r,atol=1e-10)
    assert abs(report['history'][-1]['loss']-report2['history'][0]['loss'])<1e-10


def test_four_yaw_conditional_recovery_with_complete_checkpoint(record_property):
    actions,factors,base,replay,calls=fixture()
    checkpoint,audit=fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,replay,
        iterations=30,pose_iterations=2,outer_rounds=1,wall_limit_s=110)
    error=np.rad2deg(checkpoint['delta'].numpy()-TRUTH)
    record_property('heading_error_deg',error.tolist())
    record_property('full_energy_change',audit['rounds'][0]['replayed_energy']-audit['rounds'][0]['previous_energy'])
    assert np.max(np.abs(error))<5.,error
    assert audit['rounds'][0]['accepted']
    assert audit['rounds'][0]['replayed_energy']<audit['rounds'][0]['previous_energy']
    assert len(checkpoint['parameters'])==19 and all(p.shape==(30,9) for p in checkpoint['parameters'].values())
    assert checkpoint['levers'].shape==(5,3) and checkpoint['levers'].abs().max()<=.04
    assert len(calls)==2 and 'frontend' in checkpoint['replay']
    for name,r in checkpoint['rotations'].items():
        np.testing.assert_allclose(r[:,OBSERVED],checkpoint['replay']['actions'][name]['observed'],atol=1e-12)
    np.testing.assert_allclose(calls[-1],checkpoint['delta'],atol=0)
    # The caller's immutable tape was not changed.
    np.testing.assert_allclose(actions['00_synthetic']['observed'][0,1:],Rotation.from_euler('y',-TRUTH[:,None]).as_matrix())


def test_information_once_axial_wrap_and_zero_quality():
    _,factors,base,_,_=fixture();prior=RegisteredHeadingPrior(factors,base)
    delta=torch.tensor(TRUTH)
    torch.testing.assert_close(prior.energy(delta),prior.energy(delta+torch.pi),atol=1e-12,rtol=0)
    expected=3*.9/np.deg2rad(25.)**2
    assert all(abs(sum(v.values())-expected)<1e-12 for v in prior.information.values())
    bad=copy.deepcopy(factors);bad[NODES[1]].append(copy.deepcopy(bad[NODES[1]][0]))
    with pytest.raises(ValueError,match='once'): RegisteredHeadingPrior(bad,base)
    for rows in factors.values():
        for row in rows:row['quality']=0.
    assert RegisteredHeadingPrior(factors,base).energy(delta).item()==0.


def test_stale_surrogate_improvement_cannot_accept_changed_recurrent_prior():
    actions,factors,base,replay,calls=fixture()
    def hostile_replay(delta):
        result=replay(delta)
        if np.linalg.norm(delta)>1e-12:
            for q in result['actions'].values():
                # High-frequency torso output creates a large new tracking
                # target; this is an acceptance-path fault injection only.
                q['prior'][:,TORSO]=Rotation.from_euler('x',(1.4*(-1.)**np.arange(30))[:,None]).as_matrix()[:,None]
        return result
    checkpoint,audit=fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,hostile_replay,
        iterations=2,pose_iterations=0,outer_rounds=3,wall_limit_s=110)
    row=audit['rounds'][0]
    assert row['surrogate_energy']<row['previous_energy'] and row['replayed_energy']>row['previous_energy']
    assert not row['accepted']
    assert len(audit['rounds'])==1 and len(calls)==2
    assert audit['stop_reason']=='replayed_energy_did_not_improve'
    np.testing.assert_array_equal(checkpoint['delta'],np.zeros(4))
    assert checkpoint['replay']['binding']['total_delta']==[0.]*4
    for name,r in checkpoint['rotations'].items():
        np.testing.assert_allclose(r[:,OBSERVED],actions[name]['observed'])


def test_replay_must_preserve_support_and_rotate_acceleration():
    actions,factors,base,replay,_=fixture()
    def bad(delta):
        result=replay(delta);result['actions']['00_synthetic']['valid'][0]=False
        return result
    with pytest.raises(ValueError,match='time/support'):
        fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,bad,outer_rounds=0)
    def wrong_acceleration(delta):
        result=replay(delta);result['actions']['00_synthetic']['acceleration'][0,1,0]=1.
        return result
    with pytest.raises(ValueError,match='acceleration does not match'):
        fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,wrong_acceleration,outer_rounds=0)
    missing=copy.deepcopy(actions);missing.pop('19_synthetic')
    with pytest.raises(ValueError,match='all 19'):
        fit_shared_orientation(missing,geometry(),np.zeros((5,3)),factors,base,replay,outer_rounds=0)


def test_unregistered_action_contributes_and_static_data_has_no_yaw_term():
    actions,factors,base,_,_=fixture();g=geometry();obj=_objectives(actions,g)
    for o in obj.values():
        for delta in (torch.zeros(4,dtype=torch.float64),torch.tensor(TRUTH)):
            r,a=transport_heading(o.observed,o.acceleration,delta)
            _,terms=o.evaluate(o.initial,torch.zeros(5,3,dtype=torch.float64),observed=r,acceleration=a)
            assert terms['acceleration_loss']<1e-20
    q=copy.deepcopy(actions['19_synthetic']);q['acceleration'][:,1,0]=2.
    changed=PoseObjective(**q,geometry=g)
    lever=torch.zeros(5,3,dtype=torch.float64)
    _,before=obj['19_synthetic'].evaluate(obj['19_synthetic'].initial,lever)
    _,after=changed.evaluate(changed.initial,lever)
    assert after['loss']>before['loss']+.1


def conditional_fixture():
    actions,factors,base,replay,calls=fixture()
    name='06_synthetic';node=NODES[1]
    factors[node].append(dict(action=name,source_role='axis_lateral',
        measurement_delta_rad=1.2,quality=.9,base_sigma_deg=25.,used_for_frozen_heading=False))
    registration=RegisteredHeadingPrior(factors,base)
    # Exercise real raw-factor registration and tape construction, including
    # the previously missing information lookup. Independently generate a
    # sensor rotating about body Y; its world axis has a fixed .5 rad heading.
    raw_time=np.arange(300)/200
    rows=np.zeros((300,11));rows[:,0]=raw_time;rows[:,1]=1.;rows[:,7]=9.80665
    episodes={name:{n:dict(imu=rows.copy()) for n in NODES}}
    forearm=episodes[name][node]['imu']
    q=(Rotation.from_euler('z',.5)*Rotation.from_euler('y',raw_time[:,None])).as_quat()
    forearm[:,1:5]=q[:,[3,0,1,2]];forearm[:,9]=1.
    calibration=dict(heading_factors=factors,functional_yaw_rad=[0.]*5,
        frozen_heading_correction_rad=base,pelvis_closure_rad=0.,
        initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist())
    contracts={name:dict(lo=0.,hi=raw_time[-1])}
    tape=build_arm_protocol(episodes,calibration,contracts,actions,
        registration.all_information,conditional_only=True)
    return actions,factors,base,replay,calls,tape


def test_conditional_information_is_registered_but_not_scalar_heading_truth():
    _,factors,base,_,_,tape=conditional_fixture()
    prior=RegisteredHeadingPrior(factors,base)
    name='06_synthetic';node=NODES[1]
    assert name in prior.all_information[node] and name in prior.conditional_information[node]
    assert name not in prior.information[node]
    changed=copy.deepcopy(factors);changed[node][-1]['measurement_delta_rad']=-2.
    torch.testing.assert_close(prior.energy(torch.tensor(TRUTH)),
        RegisteredHeadingPrior(changed,base).energy(torch.tensor(TRUTH)),rtol=0,atol=0)
    assert tape.rows[0].information.sum()==pytest.approx(prior.all_information[node][name])


def test_shared_optimizer_consumes_conditional_plane_and_runtime_does_not():
    actions,factors,base,replay,_,tape=conditional_fixture();g=geometry()
    with pytest.raises(ValueError,match='require a calibration protocol tape'):
        fit_shared_orientation(actions,g,np.zeros((5,3)),factors,base,replay,outer_rounds=0)
    zero,audit0=fit_shared_orientation(actions,g,np.zeros((5,3)),factors,base,replay,
        pose_iterations=0,outer_rounds=0,arm_protocol=tape)
    result,audit=fit_shared_orientation(actions,g,np.zeros((5,3)),factors,base,replay,
        pose_iterations=15,outer_rounds=0,arm_protocol=tape)
    name='06_synthetic';delta=torch.zeros(4,dtype=torch.float64)
    before=tape.energy_for_action(name,delta,zero['rotations'][name])
    after=tape.energy_for_action(name,delta,result['rotations'][name])
    assert after<before*.5
    assert result['energy']<zero['energy']
    assert len(audit['conditional_arm_rows'])==1
    assert torch.max(abs(result['rotations'][name][:,9]-zero['rotations'][name][:,9]))>.1
    # Check the actual optimized checkpoint's complete energy, counting the
    # row prior once. Its target angle .5 is independently generated above.
    expected=float(RegisteredHeadingPrior(factors,base).energy(delta))
    for action,objective in _objectives(actions,g).items():
        _,terms=objective.evaluate(result['parameters'][action],result['levers'])
        expected+=float(terms['loss'])/19
    expected+=float((result['levers']/.025).square().sum())
    row=tape.rows[0];axis=result['rotations'][name][row.index,9]@torch.tensor([1.,0.,0.],dtype=torch.float64)
    angle=torch.atan2(-axis[:,2],axis[:,0])
    expected+=float((torch.tensor(row.information)*(.5-angle).square()).sum())
    assert result['energy']==pytest.approx(expected,abs=1e-10)
    def forbidden(*args,**kwargs):raise AssertionError('runtime consumed calibration action labels')
    tape.energy_for_action=forbidden
    rotation,_=solve_pose(**actions[name],geometry=g,levers=np.zeros((5,3)),iterations=0)
    assert np.isfinite(rotation).all()


def test_conditional_tape_cannot_duplicate_or_invent_information():
    actions,factors,base,_,_,tape=conditional_fixture()
    prior=RegisteredHeadingPrior(factors,base)
    with pytest.raises(ValueError,match='each conditional factor once'):
        prior.bind_arm_protocol(ArmProtocolTape([*tape.rows,*tape.rows],np.zeros(4)),actions)
    bad=copy.deepcopy(tape);bad.rows[0].information[:]*=2
    with pytest.raises(ValueError,match='changed registered information'):
        prior.bind_arm_protocol(bad,actions)


def test_pose_protocol_reaches_initial_control_proposal_and_fresh_replay(monkeypatch):
    import biospur_fusion.c2_five_calibration.shared_fit as owner
    actions,factors,base,replay,_=fixture()
    class Protocol:
        actions={'00_synthetic'}
        def energy_for_action(self, action, parameters):
            return (parameters[:,3]-1.).square().mean() if action in self.actions else parameters.sum()*0.
        def audit(self):return {'test_protocol':True}
    protocol=Protocol();seen=[];original=owner._optimize
    def record(*args,**kwargs):
        seen.append(kwargs.get('pose_protocol'))
        return original(*args,**kwargs)
    monkeypatch.setattr(owner,'_optimize',record)
    _,audit=owner.fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,replay,
        iterations=1,pose_iterations=1,outer_rounds=1,matched_control=True,
        pose_protocol=protocol,wall_limit_s=60)
    assert len(seen)==5 and all(p is protocol for p in seen)
    assert audit['pose_protocol']=={'test_protocol':True}
    protocol.actions={'H01_boxing'}
    with pytest.raises(ValueError,match='absent calibration'):
        owner.fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,replay,
            pose_protocol=protocol)
