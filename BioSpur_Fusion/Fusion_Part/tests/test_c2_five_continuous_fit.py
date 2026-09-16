"""Regression: transitions remain in one physical objective, exports are exact."""
import copy
import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.continuous import action_checkpoints,check_continuous_transport
from biospur_fusion.c2_five_calibration.replay import slice_actions
from biospur_fusion.c2_five_calibration.shared_fit import _objectives
from test_c2_shared_fit import fixture
from test_c2_joint_kinematics import geometry


def test_transition_force_changes_full_objective_but_not_action_windows():
    actions,*_=fixture()
    q=copy.deepcopy(next(iter(actions.values())))
    q={k:np.tile(v,(4,)+(1,)*(v.ndim-1)) for k,v in q.items()}
    q['time_s']=np.arange(120)/20
    full={n:q for n in actions}
    obj=_objectives(full,geometry(),continuous=True)['_continuous']
    lever=torch.zeros(5,3,dtype=torch.float64)
    _,before=obj.evaluate(obj.initial,lever)
    # This middle interval is absent from a per-action-only representation.
    changed=copy.deepcopy(q);changed['acceleration'][45:75,1,0]=2.
    obj2=_objectives({n:changed for n in actions},geometry(),continuous=True)['_continuous']
    _,after=obj2.evaluate(obj2.initial,lever)
    assert after['loss']>before['loss']+.01
    assert len(obj.initial)==120
    assert torch.all(obj.valid_pairs)


def test_global_sampling_phase_and_exact_export():
    names=[f'{i:02d}_synthetic' for i in range(20) if i!=1]
    data={'time_s':np.arange(1000)/60,'valid':np.ones(1000,bool)}
    contracts={n:dict(lo=.017+i*.6,hi=.49+i*.6) for i,n in enumerate(names)}
    actions=slice_actions(data,contracts,continuous_grid=True)
    full={k:v[::3] for k,v in data.items()}
    p=torch.arange(len(full['time_s']))[:,None]
    checkpoint=dict(parameters={'_continuous':p},rotations={'_continuous':p+1})
    sliced=action_checkpoints(checkpoint,full,actions)
    for n,q in actions.items():
        idx=np.searchsorted(full['time_s'],q['time_s'])
        np.testing.assert_array_equal(sliced['parameters'][n],idx[:,None])
    actions[names[0]]['time_s'][0]+=.001
    with pytest.raises(ValueError,match='exact continuous subset'):
        action_checkpoints(checkpoint,full,actions)


def test_transition_gap_or_force_cannot_be_hidden_in_replay():
    actions,*_=fixture();q=next(iter(actions.values()))
    same=copy.deepcopy(q)
    check_continuous_transport(q,same,torch.zeros(4,dtype=torch.float64))
    same['valid'][15]=False
    with pytest.raises(ValueError,match='time/support'):
        check_continuous_transport(q,same,torch.zeros(4,dtype=torch.float64))
    same=copy.deepcopy(q);same['acceleration'][15,2,0]=1.
    with pytest.raises(ValueError,match='acceleration'):
        check_continuous_transport(q,same,torch.zeros(4,dtype=torch.float64))


def test_continuous_checkpoint_lifecycle_keeps_one_state_and_all_actions():
    from biospur_fusion.c2_five_calibration.shared_fit import fit_shared_orientation
    actions,factors,base,_,_=fixture()
    q=copy.deepcopy(next(iter(actions.values())))
    # Fake full neural tape at 60 Hz; action maps use its original 20 Hz phase.
    full={k:np.repeat(v,3,axis=0) for k,v in q.items()}
    full['time_s']=np.arange(90)/60
    actions={n:{k:v[::3] for k,v in full.items()} for n in actions}
    def replay(delta):
        return dict(actions=copy.deepcopy(actions),continuous=copy.deepcopy(full),binding={'fresh':True})
    result,audit=fit_shared_orientation(actions,geometry(),np.zeros((5,3)),factors,base,replay,
        pose_iterations=1,outer_rounds=0,continuous=True)
    assert set(result['parameters'])=={'_continuous'}
    assert result['parameters']['_continuous'].shape==(30,9)
    assert audit['continuous'] and audit['action_count']==19
    assert audit['action_weight'] is None
