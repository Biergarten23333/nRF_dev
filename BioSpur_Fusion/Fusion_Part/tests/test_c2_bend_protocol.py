import copy
import numpy as np
import pytest
import torch

from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol, INTENTS


def inputs():
    names={f'{i:02d}_fixture' for i in range(20) if i!=1}
    for name, *_ in INTENTS:
        names.discard(name[:2]+'_fixture'); names.add(name)
    q=dict(time_s=np.arange(601)/20, valid=np.ones(601,bool),
           observed=np.tile(np.eye(3),(601,5,1,1)))
    return {n:dict(lo=0.,hi=30.) for n in names}, {n:copy.deepcopy(q) for n in names}


def test_only_recorded_late_elbow_phase_and_correct_limb_receive_prior():
    contracts, actions=inputs(); tape=build_bend_protocol(contracts,actions)
    p=torch.ones(601,9,dtype=torch.float64,requires_grad=True)
    tape.energy_for_action('06_elbow_left',p).backward()
    assert p.grad[:300].abs().sum()==0
    assert p.grad[300:600,3].abs().sum()>0
    assert p.grad[:,[0,1,2,4,5,6,7,8]].abs().sum()==0
    assert len(tape.rows)==4 and tape.audit()['exact_angle_target'] is False


def test_approximate_bend_is_soft_and_missing_support_loses_information():
    contracts,actions=inputs(); tape=build_bend_protocol(contracts,actions)
    p=torch.zeros(601,9,dtype=torch.float64)
    p[:,3:7]=torch.pi/2
    assert tape.energy_for_action('08_hip_left',p)==0
    p[:,5]+=np.deg2rad(10.)
    loss=float(tape.energy_for_action('08_hip_left',p))
    assert 0<loss<1.
    actions['08_hip_left']['valid'][200:400]=False
    missing=build_bend_protocol(contracts,actions)
    assert missing.energy_for_action('08_hip_left',p)<loss


def test_H_and_partial_calibration_cannot_enter_protocol():
    contracts,actions=inputs(); tape=build_bend_protocol(contracts,actions)
    with pytest.raises(ValueError,match='forbidden'):
        tape.energy_for_action('H01_boxing',torch.zeros(601,9))
    actions.pop('00_fixture')
    with pytest.raises(ValueError,match='all recorded'):
        build_bend_protocol(contracts,actions)


def test_changed_clock_or_extra_node_is_rejected():
    contracts,actions=inputs()
    actions['06_elbow_left']['time_s'][1]+=.01
    with pytest.raises(ValueError,match='original five-node'):
        build_bend_protocol(contracts,actions)
