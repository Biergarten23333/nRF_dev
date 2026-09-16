"""Synthetic review-window bindings; no reference, inference, or model assets."""
import json

import numpy as np
import pytest

import c2_five_frozen_review as review
from biospur_fusion.c2_five_calibration.replay import parameter_digest
from biospur_fusion.c2_sparse_nodes.inputs import sha


def write(path, value):
    path.write_text(json.dumps(value))
    return sha(path)


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    inputs=tmp_path/'inputs';inputs.mkdir()
    out=tmp_path/'out';out.mkdir()
    monkeypatch.setattr(review,'INPUT_RUN',inputs)
    contracts={'00_initial_still':{'lo':.01,'hi':.11}}
    h_contracts={'H01_boxing':{'lo':1.,'hi':2.}}
    write(inputs/'CALIBRATION_INPUT_AUDIT.json',{'contracts':contracts})
    write(inputs/'HOLDOUT_INPUT_AUDIT.json',{'contracts':h_contracts})
    shared=dict(provenance=dict(input_audit_sha256=sha(inputs/'CALIBRATION_INPUT_AUDIT.json'),
                               action_contract_sha256=parameter_digest(contracts)),
                accepted_replay_binding=dict(action_contract_sha256=parameter_digest(contracts)))
    write(out/'SHARED_CALIBRATION.json',shared)
    binding={'SHARED_CALIBRATION.json':sha(out/'SHARED_CALIBRATION.json')}
    time=np.arange(4)*.05
    arrays=dict(time_s=time,prior=np.tile(np.eye(3),(4,24,1,1)),
                observed=np.tile(np.eye(3),(4,5,1,1)),acceleration=np.zeros((4,5,3)),
                valid=np.array([True,False,True,True]),rotation=np.tile(np.eye(3),(4,24,1,1)))
    np.savez(out/'C2_FROZEN_REPLAY.npz',**arrays)
    write(out/'C2_FROZEN_REPLAY.json',dict(status='FROZEN_C2_DIAGNOSTIC_NOT_ACCEPTED',
        accepted=False,probe=False,calibration_kind='shared',
        action_labels_consumed=False,calibration_parameter_updates=False,
        frozen_inputs=binding,output_sha256=sha(out/'C2_FROZEN_REPLAY.npz'),
        physical={'joint_angle_semantics':'synthetic'}))
    write(out/'H_REPLAY.json',dict(frozen_inputs=binding,
        input_audit_sha256={name:sha(inputs/name) for name in
            ('CALIBRATION_INPUT_AUDIT.json','HOLDOUT_INPUT_AUDIT.json')}))
    return out,inputs,contracts,h_contracts


def test_window_views_preserve_global_times_and_support(candidate):
    out,_,contracts,h_contracts=candidate
    actions,outputs,geometric=review.load_shared_c2(out,contracts)
    assert geometric
    np.testing.assert_array_equal(actions['00_initial_still']['time_s'],np.array([.05,.1]))
    np.testing.assert_array_equal(outputs['00_initial_still/valid'],np.array([False,True]))
    assert review.verified_action_contracts(out,holdout=True)==h_contracts


@pytest.mark.parametrize('name,holdout',[
    ('CALIBRATION_INPUT_AUDIT.json',False),('HOLDOUT_INPUT_AUDIT.json',True)])
def test_changed_external_audit_is_rejected(candidate,name,holdout):
    out,inputs,_,_=candidate
    with (inputs/name).open('a') as stream:stream.write(' ')
    with pytest.raises(ValueError,match='audit changed'):
        review.verified_action_contracts(out,holdout=holdout)


def test_caller_cannot_supply_different_windows(candidate):
    out,_,_,_=candidate
    with pytest.raises(ValueError,match='caller supplied different'):
        review.load_shared_c2(out,{'00_initial_still':{'lo':.0,'hi':.15}})


def test_h_must_bind_both_original_input_audits(candidate):
    out,_,_,_=candidate
    path=out/'H_REPLAY.json';record=json.loads(path.read_text())
    record['input_audit_sha256'].pop('CALIBRATION_INPUT_AUDIT.json')
    write(path,record)
    with pytest.raises(ValueError,match='unbound'):
        review.verified_action_contracts(out,holdout=True)


def test_action_digest_cannot_be_replaced_by_a_file_hash_only(candidate):
    out,inputs,_,_=candidate
    shared_path=out/'SHARED_CALIBRATION.json';shared=json.loads(shared_path.read_text())
    shared['accepted_replay_binding']['action_contract_sha256']='wrong'
    write(shared_path,shared)
    path=out/'C2_FROZEN_REPLAY.json';record=json.loads(path.read_text())
    record['frozen_inputs']['SHARED_CALIBRATION.json']=sha(shared_path)
    write(path,record)
    with pytest.raises(ValueError,match='action contract differs'):
        review.verified_action_contracts(out)


def test_h_cannot_belong_to_a_different_shared_candidate(candidate):
    out,_,_,_=candidate
    path=out/'H_REPLAY.json';record=json.loads(path.read_text())
    record['frozen_inputs']['SHARED_CALIBRATION.json']='different'
    write(path,record)
    with pytest.raises(ValueError,match='same shared calibration'):
        review.verified_action_contracts(out,holdout=True)


@pytest.mark.parametrize('field,value',[('status','running'),('accepted',True),('probe',None)])
def test_incomplete_or_mislabelled_replay_is_rejected(candidate,field,value):
    out,_,contracts,_=candidate
    path=out/'C2_FROZEN_REPLAY.json';record=json.loads(path.read_text())
    record[field]=value;write(path,record)
    with pytest.raises(ValueError,match='completed label-free'):
        review.load_shared_c2(out,contracts)
