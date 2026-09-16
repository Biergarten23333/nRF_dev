"""Verify one explicitly reviewed, first-order-equivalent replay primitive.

This owns source compatibility only. It does not grant scientific acceptance
or permit changed calibration parameters, observations, losses or budgets.
"""
import ast
import hashlib
import json

from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha

OWNER = 'src/biospur_fusion/c2_five_calibration/anatomy.py'
SYMBOL = 'exp_rotation'


def source_parts(text):
    tree = ast.parse(text)
    selected = [node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == SYMBOL]
    if len(selected) != 1:
        raise ValueError('exactly one rotation primitive required')
    other = ast.Module(body=[node for node in tree.body if node is not selected[0]], type_ignores=[])
    digest = lambda node: hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest()
    return digest(other), digest(selected[0])


def verify_equivalent_rotation(out, record, producer, current):
    if out is None or set(record) != {'schema','owner','symbol','before','after','proof_sha256'}:
        raise ValueError('explicit bound rotation equivalence required')
    if (record['schema'] != 'biospur-rotation-equivalence-v1'
            or record['owner'] != OWNER or record['symbol'] != SYMBOL
            or record['before'] != producer.get(OWNER) or record['after'] != current.get(OWNER)):
        raise ValueError('rotation equivalence source differs')
    proof_path = out/'SO3_EXP_EQUIVALENCE.json'
    if sha(proof_path) != record['proof_sha256']:
        raise ValueError('rotation equivalence proof changed')
    proof = json.loads(proof_path.read_text())
    snapshot = json.loads((out/'PRODUCER_SOURCE_SNAPSHOT.json').read_text())[OWNER]
    old = snapshot['text']; new = (ROOT/OWNER).read_text()
    if (hashlib.sha256(old.encode()).hexdigest() != record['before']
            or hashlib.sha256(new.encode()).hexdigest() != record['after']):
        raise ValueError('rotation equivalence source bytes changed')
    before_rest,before_function = source_parts(old)
    after_rest,after_function = source_parts(new)
    if before_rest != after_rest:
        raise ValueError('numerical changes outside the rotation primitive are forbidden')
    if (proof.get('status') != 'FIRST_ORDER_EQUIVALENCE_TESTED_NOT_MOTION_ACCEPTANCE'
            or proof.get('before_source_sha256') != record['before']
            or proof.get('after_source_sha256') != record['after']
            or proof.get('unchanged_ast_sha256') != before_rest
            or proof.get('before_function_ast_sha256') != before_function
            or proof.get('after_function_ast_sha256') != after_function
            or proof.get('primitive_output_and_gradient_passed') is not True
            or proof.get('joint_objective_and_gradient_passed') is not True
            or proof.get('existing_hinge_solver_regressions_passed') is not True):
        raise ValueError('incomplete first-order numerical equivalence evidence')
    evidence = proof.get('evidence_sha256', {})
    if not evidence or any('/' in name or sha(out/name) != expected for name,expected in evidence.items()):
        raise ValueError('rotation equivalence evidence changed')
    return OWNER
