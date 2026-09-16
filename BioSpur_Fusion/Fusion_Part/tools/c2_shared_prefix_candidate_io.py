"""Frozen boundary for full-C2 shared-prefix candidates, before H access."""
import json
from pathlib import Path
import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_five_calibration.phase_contract import recorded_prefix


def resolve(candidate):
    candidate=Path(candidate).resolve()
    result=json.loads((candidate/'RESULT.json').read_text())
    if not result['completed'] or result['H_used'] or result['ten_used']:
        raise ValueError('completed five-only shared candidate required')
    for name,value in result['bindings'].items():
        if Path(name).name!=name or sha(candidate/name)!=value:
            raise ValueError('candidate binding changed: '+name)
    if sha(candidate/'CHECKPOINT.npz')!=result['output_sha256']:
        raise ValueError('physical checkpoint changed')
    contract=json.loads((candidate/'CONTRACT.json').read_text())
    recorded_prefix(contract['actions'],require_complete=True)
    if contract['H_used'] or contract['ten_used'] or not contract['matched_control']:
        raise ValueError('independent full candidate and matched control required')
    for name,value in contract['source_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError('candidate producer changed: '+name)
    c=json.loads((candidate/'FRONTEND.json').read_text());g=json.loads((candidate/'GEOMETRY.json').read_text())
    recorded_prefix(c['fit_actions'],require_complete=True)
    if not c['complete_recorded_C2']:raise ValueError('incomplete frontend')
    with np.load(candidate/'CHECKPOINT.npz',allow_pickle=False) as z:levers=z['levers']
    if levers.shape!=(5,3) or not np.isfinite(levers).all():raise ValueError('invalid levers')
    names=set(result['bindings'])|{'CHECKPOINT.npz','RESULT.json'}
    return c,g,levers,{str(candidate/n):sha(candidate/n) for n in sorted(names)}
