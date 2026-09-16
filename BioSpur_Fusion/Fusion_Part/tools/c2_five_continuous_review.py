"""Read-only comparison adapter for the continuous, in-sample C2 checkpoint.

This exports fitted C2 poses, not a label-free frozen C2 replay. H, when
present, is independently replayed after freezing. No output is refitted here.
"""
import json
import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN
from biospur_fusion.c2_five_calibration.replay import slice_actions


def load_continuous_calibration(out):
    report=json.loads((out/'SHARED_CALIBRATION.json').read_text())
    if not report['shared_fit'].get('continuous') or report.get('accepted') is not False:
        raise ValueError('continuous diagnostic calibration required')
    bindings={'CONTINUOUS_CALIBRATION.npz':report['continuous_output_sha256'],
        'SHARED_CALIBRATION.npz':report['output_sha256'],
        'C2_PRIOR.npz':report['prior_output_sha256'],
        'FRONTEND.json':report['frontend_sha256'],'GEOMETRY.json':report['geometry_sha256']}
    for name,h in bindings.items():
        if sha(out/name)!=h:raise ValueError('changed continuous calibration: '+name)
    path=INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json'
    if sha(path)!=report['provenance']['input_audit_sha256']:
        raise ValueError('changed continuous calibration windows')
    contracts=json.loads(path.read_text())['contracts']
    with np.load(out/'C2_PRIOR.npz',allow_pickle=False) as f:
        actions=slice_actions({k:f[k] for k in f.files},contracts,continuous_grid=True)
    with np.load(out/'SHARED_CALIBRATION.npz',allow_pickle=False) as f:
        outputs={k:f[k] for k in f.files}
    with np.load(out/'CONTINUOUS_CALIBRATION.npz',allow_pickle=False) as f:
        for name,q in actions.items():
            idx=np.searchsorted(f['time_s'],q['time_s'])
            if not np.array_equal(f['time_s'][idx],q['time_s']):
                raise ValueError('action comparison changed original time')
            if not np.array_equal(f['rotation'][idx],outputs[name+'/rotation']):
                raise ValueError('action export differs from continuous fit')
    return contracts,actions,outputs
