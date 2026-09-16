"""Numerical-source compatibility cannot authorize unrelated anatomy changes."""
import json
from pathlib import Path

import pytest

from biospur_fusion.c2_five_calibration import rotation_equivalence as gate
from biospur_fusion.c2_sparse_nodes.inputs import sha


def write(path,record):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record))
    return sha(path)


@pytest.fixture
def pair(tmp_path,monkeypatch):
    monkeypatch.setattr(gate,'ROOT',tmp_path)
    out=tmp_path/'out';out.mkdir()
    old='import torch\n\ndef exp_rotation(x):\n    return x\n\ndef other(x):\n    return x+1\n'
    new=old.replace('return x\n','return x.clone()\n')
    source=tmp_path/gate.OWNER;source.parent.mkdir(parents=True);source.write_text(old);before=sha(source)
    write(out/'PRODUCER_SOURCE_SNAPSHOT.json',{gate.OWNER:{'text':old,'sha256':before}})
    source.write_text(new);after=sha(source)
    common,old_fn=gate.source_parts(old);_,new_fn=gate.source_parts(new)
    proof=dict(status='FIRST_ORDER_EQUIVALENCE_TESTED_NOT_MOTION_ACCEPTANCE',before_source_sha256=before,
        after_source_sha256=after,unchanged_ast_sha256=common,before_function_ast_sha256=old_fn,
        after_function_ast_sha256=new_fn,primitive_output_and_gradient_passed=True,
        joint_objective_and_gradient_passed=True,existing_hinge_solver_regressions_passed=True,
        evidence_sha256={'tests.json':write(out/'tests.json',{'synthetic_fixture':True})})
    record=dict(schema='biospur-rotation-equivalence-v1',owner=gate.OWNER,symbol=gate.SYMBOL,before=before,after=after,
                proof_sha256=write(out/'SO3_EXP_EQUIVALENCE.json',proof))
    return out,source,record,{gate.OWNER:before},{gate.OWNER:after}


def test_exact_reviewed_symbol_exception(pair):
    out,_,record,producer,current=pair
    assert gate.verify_equivalent_rotation(out,record,producer,current)==gate.OWNER


def test_other_anatomy_change_rejected_even_with_new_hash(pair):
    out,source,record,producer,current=pair
    source.write_text(source.read_text().replace('x+1','x+2'))
    record['after']=current[gate.OWNER]=sha(source)
    with pytest.raises(ValueError,match='outside the rotation primitive'):
        gate.verify_equivalent_rotation(out,record,producer,current)


def test_missing_or_changed_proof_rejected(pair):
    out,_,record,producer,current=pair
    (out/'SO3_EXP_EQUIVALENCE.json').write_text('{}')
    with pytest.raises(ValueError,match='proof changed'):
        gate.verify_equivalent_rotation(out,record,producer,current)


def test_missing_gradient_evidence_rejected(pair):
    out,_,record,producer,current=pair
    path=out/'SO3_EXP_EQUIVALENCE.json';proof=json.loads(path.read_text());proof['joint_objective_and_gradient_passed']=False
    record['proof_sha256']=write(path,proof)
    with pytest.raises(ValueError,match='incomplete first-order'):
        gate.verify_equivalent_rotation(out,record,producer,current)


def test_external_or_changed_evidence_rejected(pair):
    out,_,record,producer,current=pair
    (out/'tests.json').write_text('changed')
    with pytest.raises(ValueError,match='evidence changed'):
        gate.verify_equivalent_rotation(out,record,producer,current)
