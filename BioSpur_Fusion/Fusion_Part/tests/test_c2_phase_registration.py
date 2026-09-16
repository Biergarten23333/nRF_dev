"""Late directed pronation must survive registration on its own time support."""
import copy

import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_five_calibration.heading import fit_registered_heading
from biospur_fusion.c2_five_calibration.arm_protocol import build_arm_protocol
from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
from biospur_fusion.c2_five_calibration.phase_contract import phase_bounds,phase_key


def raw_fixture(late_yaw=0.):
    t=np.arange(6000)/200
    still=np.zeros((len(t),11));still[:,0]=t;still[:,1]=1.;still[:,7]=9.80665
    swing=still.copy();swing[:,1:5]=Rotation.from_euler('y',t[:,None]).as_quat()[:,[3,0,1,2]]
    swing[:,9]=1.
    episodes={name:{node:dict(imu=still if node==NODES[0] else swing)
                    for node in NODES} for name in EPISODES}
    for i,name in enumerate(('06_elbow_left','07_elbow_right'),1):
        rows=swing.copy();late=t>=15
        q=(Rotation.from_euler('z',late_yaw)*Rotation.from_euler('y',-np.pi/2)
           *Rotation.from_euler('z',(t[late]-15)[:,None])).as_quat()
        rows[late,1:5]=q[:,[3,0,1,2]];rows[late,9]=0.;rows[late,10]=1.
        episodes[name][NODES[i]]=dict(imu=rows)
        rows=still.copy();rows[:,1:5]=Rotation.from_euler('x',np.pi/2 if i==1 else -np.pi/2).as_quat()[[3,0,1,2]]
        episodes['02_t_pose'][NODES[i]]=dict(imu=rows)
        name='04_shoulder_left' if i==1 else '05_shoulder_right'
        rows=still.copy();rows[:,1:5]=Rotation.from_euler('x',t[:,None]).as_quat()[:,[3,0,1,2]];rows[:,8]=1.
        episodes[name][NODES[i]]=dict(imu=rows)
    c=dict(functional_yaw_rad=[0.]*5,pelvis_closure_rad=0.,
        initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),
        forearm_mount_calibration={node:dict(axis_sensor_long=[0.,0.,1.]) for node in NODES[1:3]})
    h=fit_registered_heading(episodes,c)
    c.update(heading_factors=h['factors'],frozen_heading_correction_rad=h['frozen_correction_rad'])
    grid=t[::10]
    actions={name:dict(time_s=grid,valid=np.ones(len(grid),bool)) for name in EPISODES}
    contracts={name:dict(lo=0.,hi=30.) for name in EPISODES}
    return episodes,c,actions,contracts


def test_full_raw_registration_preserves_both_elbow_phases_and_scalar_boundary():
    episodes,c,actions,contracts=raw_fixture()
    prior=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    tape=build_arm_protocol(episodes,c,contracts,actions,prior.all_information,conditional_only=True)
    prior.bind_arm_protocol(tape,actions)
    assert len(actions)==19 and len(tape.rows)==6
    for side,node in enumerate(NODES[1:3]):
        action='06_elbow_left' if side==0 else '07_elbow_right'
        early,late=[r for r in tape.rows if r.action==action]
        assert early.factor_key==action+':flexion' and early.multiple==2
        assert late.factor_key==action+':pronation' and late.multiple==1
        assert early.phase_id=='flexion' and late.phase_id=='pronation'
        assert np.all(actions[action]['time_s'][early.index]<15.)
        assert np.all(actions[action]['time_s'][late.index]>=15.)
        assert not np.intersect1d(early.index,late.index).size
        assert set(prior.information[node])=={'02_t_pose'}
        for row in (early,late):
            expected=prior.all_information[node][row.factor_key]
            # Raw quadrature near the phase boundary can map to a cell
            # outside that phase. Its mass is lost, never redistributed.
            assert 0<row.information.sum()<=expected+1e-12
            assert row.information.sum()+row.audit['lost_information']==pytest.approx(expected)
        # An axial plane cannot distinguish the opposite direction; the late
        # forward phase must. This is an independent exact rotation control.
        pose=torch.eye(3,dtype=torch.float64).repeat(600,24,1,1)
        zero=torch.zeros(4,dtype=torch.float64)
        assert tape.energy_for_action(action,zero,pose).item()<1e-20
        pose[:,9]=torch.tensor(Rotation.from_euler('y',np.pi).as_matrix())
        opposite=tape.energy_for_action(action,zero,pose).item()
        assert opposite==pytest.approx(np.pi**2*late.information.sum())
    altered=copy.deepcopy(c['heading_factors'])
    for node in NODES[1:3]:altered[node][-1]['measurement_delta_rad']=2.3
    torch.testing.assert_close(prior.energy(torch.ones(4,dtype=torch.float64)),
        RegisteredHeadingPrior(altered,c['frozen_heading_correction_rad']).energy(torch.ones(4,dtype=torch.float64)))


def test_phase_metadata_rejects_ambiguous_or_duplicate_evidence():
    with pytest.raises(ValueError,match='identifier'):phase_key('06_elbow_left','first:second')
    with pytest.raises(ValueError,match='explicit phase'):
        phase_bounds(dict(action='06_elbow_left',phase_id='pronation',source_role='directed_forward'))
    for interval in ([15,15],[-1,15],[15,31],[np.nan,30]):
        with pytest.raises(ValueError,match='phase bounds'):
            phase_bounds(dict(action='06_elbow_left',source_role='axis_lateral',formal_interval_s=interval))
    _,c,_,_=raw_fixture();node=NODES[1]
    duplicate=copy.deepcopy(c['heading_factors']);duplicate[node].append(copy.deepcopy(duplicate[node][-1]))
    with pytest.raises(ValueError,match='once per limb'):
        RegisteredHeadingPrior(duplicate,c['frozen_heading_correction_rad'])
    overlap=copy.deepcopy(c['heading_factors']);overlap[node][-1]['formal_interval_s']=[14.,30.]
    with pytest.raises(ValueError,match='overlapping phases'):
        RegisteredHeadingPrior(overlap,c['frozen_heading_correction_rad'])
    promoted=copy.deepcopy(c['heading_factors']);promoted[node][-1]['used_for_frozen_heading']=True
    with pytest.raises(ValueError,match='scalar heading truth'):
        RegisteredHeadingPrior(promoted,c['frozen_heading_correction_rad'])


def test_phase_binding_rejects_changed_direction_periodicity_and_support():
    episodes,c,actions,contracts=raw_fixture()
    prior=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    tape=build_arm_protocol(episodes,c,contracts,actions,prior.all_information,conditional_only=True)
    prior.bind_arm_protocol(tape,actions)
    late=next(r for r in tape.rows if r.phase_id=='pronation')
    from dataclasses import replace
    bad=copy.deepcopy(tape)
    bad.rows=tuple(replace(r,multiple=2) if r.factor_key==late.factor_key else r for r in bad.rows)
    with pytest.raises(ValueError,match='phase semantics'):prior.bind_arm_protocol(bad,actions)
    bad=copy.deepcopy(tape)
    next(r for r in bad.rows if r.phase_id=='pronation').audit['registered_phase_interval_s']=[0.,15.]
    with pytest.raises(ValueError,match='phase semantics'):prior.bind_arm_protocol(bad,actions)
    bad=copy.deepcopy(tape)
    late_row=next(r for r in bad.rows if r.phase_id=='pronation')
    late_row.index[:]=late_row.index-300
    with pytest.raises(ValueError,match='phase support'):prior.bind_arm_protocol(bad,actions)


def test_phase_keys_reach_shared_checkpoint_without_becoming_action_names():
    from biospur_fusion.c2_five_calibration.shared_fit import fit_shared_orientation,_objectives
    from test_c2_joint_kinematics import geometry
    episodes,c,actions,contracts=raw_fixture(late_yaw=.4)
    for q in actions.values():
        count=len(q['time_s'])
        q.update(prior=np.tile(np.eye(3),(count,24,1,1)),
            observed=np.tile(np.eye(3),(count,5,1,1)),acceleration=np.zeros((count,5,3)))
    prior=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    tape=build_arm_protocol(episodes,c,contracts,actions,prior.all_information,conditional_only=True)
    def replay(delta):
        assert np.array_equal(delta,np.zeros(4))
        return dict(actions=actions,binding=dict(synthetic=True))
    g=geometry()
    checkpoint,audit=fit_shared_orientation(actions,g,np.zeros((5,3)),c['heading_factors'],
        c['frozen_heading_correction_rad'],replay,pose_iterations=0,outer_rounds=0,arm_protocol=tape)
    assert len(checkpoint['parameters'])==19 and len(audit['conditional_arm_rows'])==6
    zero=torch.zeros(4,dtype=torch.float64)
    expected=prior.energy(zero).item()
    for name,objective in _objectives(actions,g).items():
        _,terms=objective.evaluate(checkpoint['parameters'][name],checkpoint['levers'])
        expected+=terms['loss'].item()/19
    # The independent construction injected .4 rad only in the late phase.
    # Two signed phase energies must survive the actual shared-fit owner.
    late_energy=sum(row.information.sum()*.4**2 for row in tape.rows if row.phase_id=='pronation')
    assert late_energy>.5
    assert checkpoint['energy']==pytest.approx(expected+late_energy,abs=1e-10)


def test_temporal_curve_reaches_protocol_without_changing_evidence_mass():
    from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
    episodes,c,actions,contracts=raw_fixture()
    prior=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    old=build_arm_protocol(episodes,c,contracts,actions,prior.all_information,conditional_only=True)
    c['temporal_heading_curves']={n:dict(time_s=[0.,30.],correction_rad=[.1,.7]) for n in NODES[1:3]}
    new=build_arm_protocol(episodes,c,contracts,actions,prior.all_information,conditional_only=True)
    for a,b in zip(old.rows,new.rows):
        node=NODES[a.limb+1];t=actions[a.action]['time_s'][a.index]
        shift=temporal_heading(t,node,c)-c['frozen_heading_correction_rad'][node]
        np.testing.assert_allclose(b.direction,Rotation.from_euler('y',shift[:,None]).apply(a.direction),atol=1e-12)
        np.testing.assert_array_equal(a.information,b.information)
        np.testing.assert_array_equal(a.index,b.index)


def test_scalar_heading_factor_uses_curve_at_its_original_measurement_time():
    _,c,_,_=raw_fixture()
    c['temporal_heading_curves']={n:dict(time_s=[0.,30.],correction_rad=[.1,.7]) for n in NODES[1:3]}
    for node in NODES[1:3]:
        for f in c['heading_factors'][node]:f['measurement_time_s']=15.
    updated=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'],baseline_calibration=c)
    base=dict(c['frozen_heading_correction_rad']);base.update({n:.4 for n in NODES[1:3]})
    expected=RegisteredHeadingPrior(c['heading_factors'],base)
    delta=torch.tensor([.1,-.2,.3,-.4],dtype=torch.float64)
    torch.testing.assert_close(updated.energy(delta),expected.energy(delta),atol=1e-12,rtol=0)
