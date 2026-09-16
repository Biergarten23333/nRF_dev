"""Read-only boundary for a completed progressive physical C2 candidate."""
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_five_calibration.phase_contract import recorded_prefix


def resolve(candidate):
    candidate=Path(candidate).resolve()
    result=json.loads((candidate/'RESULT.json').read_text())
    contract=json.loads((candidate/'CONTRACT.json').read_text())
    recorded_prefix(result['actions'],require_complete=True)
    if not result['completed'] or contract['H_used'] or contract['ten_node_used']:
        raise ValueError('completed independent C2 candidate required')
    if result['frozen_final_prefix']!='19_heel_to_butt_right':
        raise ValueError('candidate was frozen before last recorded action')
    for name,value in contract['source_sha256'].items():
        if sha(ROOT/name)!=value:raise ValueError('candidate source changed: '+name)
    final=candidate/result['frozen_final_prefix'];history=result['history'][-1]
    paths=dict(frontend=final/'FRONTEND.json',geometry=candidate/'GEOMETRY.json',
               physical=final/'PHYSICAL_PREFIX.npz',prior=final/'C2_PREFIX_PRIOR.npz',
               result=candidate/'RESULT.json',contract=candidate/'CONTRACT.json')
    for key,field in (('frontend','frontend_sha256'),('physical','physical_sha256'),('prior','prior_sha256')):
        if sha(paths[key])!=history[field]:raise ValueError('candidate artifact changed: '+key)
    c=json.loads(paths['frontend'].read_text());g=json.loads(paths['geometry'].read_text())
    seed=Path(contract['seed'])
    seed_geometry=seed/'arrived_neural_prefix14/GEOMETRY.json'
    physical_contract=json.loads((seed/'physical_prefix14/CONTRACT.json').read_text())
    if (sha(seed_geometry)!=physical_contract['geometry_sha256']
            or g!=json.loads(seed_geometry.read_text())):
        raise ValueError('candidate geometry differs from bound measured-geometry seed')
    recorded_prefix(c['fit_actions'],require_complete=True)
    if not c['complete_recorded_C2']:raise ValueError('frontend is an incomplete prefix')
    with np.load(paths['physical'],allow_pickle=False) as z:
        levers=z['levers']
    if levers.shape!=(5,3) or not np.isfinite(levers).all():raise ValueError('invalid frozen levers')
    return c,g,levers,{str(path):sha(path) for path in paths.values()}
