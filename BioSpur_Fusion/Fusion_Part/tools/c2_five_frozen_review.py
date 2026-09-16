"""Evaluation-only action views of one continuous frozen C2 pose replay."""
import json
import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN
from biospur_fusion.c2_five_calibration.replay import parameter_digest


def verified_action_contracts(out, *, holdout=False):
    """Read current window metadata only when bound to the frozen producer.

    Window labels select evaluation views, never recover or shift sample time.
    Recheck after building the comparison because these audits are external to
    the candidate directory and are not covered by its artifact symlinks.
    """
    shared_path=out/'SHARED_CALIBRATION.json'
    c2=json.loads((out/'C2_FROZEN_REPLAY.json').read_text())
    if c2['frozen_inputs'].get('SHARED_CALIBRATION.json')!=sha(shared_path):
        raise ValueError('C2 window owner is not bound to frozen shared calibration')
    shared=json.loads(shared_path.read_text())
    path=INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json'
    if sha(path)!=shared['provenance']['input_audit_sha256']:
        raise ValueError('C2 evaluation window audit changed')
    contracts=json.loads(path.read_text())['contracts']
    digest=parameter_digest(contracts)
    if (digest!=shared['provenance']['action_contract_sha256']
            or digest!=shared['accepted_replay_binding']['action_contract_sha256']):
        raise ValueError('C2 evaluation action contract differs')
    if not holdout:return contracts
    h=json.loads((out/'H_REPLAY.json').read_text())
    if h['frozen_inputs'].get('SHARED_CALIBRATION.json')!=sha(shared_path):
        raise ValueError('H window owner is not bound to the same shared calibration')
    names=('CALIBRATION_INPUT_AUDIT.json','HOLDOUT_INPUT_AUDIT.json')
    expected=h.get('input_audit_sha256',{})
    if any(expected.get(name)!=sha(INPUT_RUN/name) for name in names):
        raise ValueError('H evaluation window audit changed or is unbound')
    return json.loads((INPUT_RUN/'HOLDOUT_INPUT_AUDIT.json').read_text())['contracts']


def load_shared_c2(out, contracts):
    verified=verified_action_contracts(out)
    if parameter_digest(contracts)!=parameter_digest(verified):
        raise ValueError('caller supplied different C2 evaluation windows')
    record=json.loads((out/'C2_FROZEN_REPLAY.json').read_text())
    if (record.get('status')!='FROZEN_C2_DIAGNOSTIC_NOT_ACCEPTED'
            or record.get('accepted') is not False or record.get('probe') is not False
            or record.get('calibration_kind')!='shared'
            or record.get('action_labels_consumed') is not False
            or record.get('calibration_parameter_updates') is not False
            or record['output_sha256']!=sha(out/'C2_FROZEN_REPLAY.npz')):
        raise ValueError('completed label-free C2 replay required')
    for name,expected in record['frozen_inputs'].items():
        if sha(out/name)!=expected:raise ValueError('frozen C2 parameter changed: '+name)
    with np.load(out/'C2_FROZEN_REPLAY.npz',allow_pickle=False) as archive:
        q={k:archive[k] for k in archive.files}
    actions,outputs={},{}
    for name,window in contracts.items():
        ids=np.flatnonzero((q['time_s']>=window['lo'])&(q['time_s']<=window['hi']))
        if len(ids)<2:raise ValueError('missing frozen C2 action support: '+name)
        actions[name]={k:v[ids] for k,v in q.items() if k!='rotation'}
        for key in ('rotation','time_s','valid'):outputs[name+'/'+key]=q[key][ids]
    return actions,outputs,'joint_angle_semantics' in record['physical']
