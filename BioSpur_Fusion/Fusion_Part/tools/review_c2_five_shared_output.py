#!/usr/bin/env python3
"""Post-inference shared-candidate comparison; no reference enters fitting."""
import argparse
import json
from pathlib import Path

from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_five_calibration.frontend import FIT
from assess_c2_five_calibration import angle_failures
from build_c2_five_calibration_review import main as build
from c2_five_frozen_review import verified_action_contracts


def review(source,out,*,previous_source=None):
    source,out=source.resolve(),out.resolve()
    if source==out or out.exists():raise ValueError('new evaluation directory required')
    if previous_source is not None:
        raise ValueError('shared global C2 replay uses the prior pane; legacy action grids may differ')
    h=json.loads((source/'H_REPLAY.json').read_text())
    c2=json.loads((source/'C2_FROZEN_REPLAY.json').read_text())
    if (h.get('status')!='H_DIAGNOSTIC_NOT_ACCEPTED'
            or h.get('calibration_kind')!='shared' or h.get('H_used_for_calibration') is not False
            or h.get('ten_node_reference_opened') is not False or c2.get('probe')
            or not h.get('diagnostic_only')):
        raise ValueError('completed frozen shared diagnostics required')
    for record in (c2,h):
        for name,expected in record['frozen_inputs'].items():
            if sha(source/name)!=expected:raise ValueError('frozen input changed: '+name)
    bindings={**c2['frozen_inputs'],**h['frozen_inputs']}
    for record,file in ((h,'H_REPLAY.npz'),(c2,'C2_FROZEN_REPLAY.npz')):
        if record['output_sha256']!=sha(source/file):raise ValueError('completed output changed')
    for name,expected in bindings.items():
        if sha(source/name)!=expected:raise ValueError('frozen input changed: '+name)
    for name in ('C2_FROZEN_REPLAY.json','C2_FROZEN_REPLAY.npz','H_REPLAY.json','H_REPLAY.npz'):
        bindings[name]=sha(source/name)
    c2_windows=verified_action_contracts(source)
    h_windows=verified_action_contracts(source,holdout=True)
    out.mkdir()
    for name in bindings:(out/name).symlink_to(source/name)
    write(out/'REVIEW_BOUNDARY.json',dict(source_run=str(source),compute_files_sha256=bindings,
        reference_scope='evaluation only after frozen C2 and H inference',
        no_fitting_or_parameter_selection=True,legacy_C2_validation_gates_claimed=False))
    build(out,with_h=True,shared_frozen=True)
    compared=json.loads((out/'C2_H_REFERENCE_COMPARISON.json').read_text())['metrics']
    c2_rows={n:v for n,v in compared.items() if n[:2] in FIT}
    h_rows={n:v for n,v in compared.items() if n.startswith('H')}
    if {n[:2] for n in c2_rows}!=FIT or set(h_rows)!={'H01_boxing','H02_golf'}:
        raise ValueError('all recorded C2 and both H comparisons required')
    limits=json.loads((source/'TASK_CONTRACT.json').read_text())['regression_gates']
    failures={}
    for label,rows,prefix in (('C2',c2_rows,'C2_check'),('H',h_rows,'H')):
        failures[label]=angle_failures(rows,[('physical_bend_mae_deg',limits[prefix+'_each_joint_MAE_deg_max']),
            ('physical_bend_p95_deg',limits[prefix+'_each_joint_P95_deg_max'])])
    if (verified_action_contracts(source)!=c2_windows
            or verified_action_contracts(source,holdout=True)!=h_windows
            or any(sha(source/name)!=expected for name,expected in bindings.items())):
        raise ValueError('evaluation input changed before assessment')
    write(out/'FROZEN_DIAGNOSTIC_ASSESSMENT.json',dict(
        source_sha256=sha(Path(__file__)),
        status='FROZEN_SHARED_DIAGNOSTIC',product_accepted=False,calibration_accepted=False,
        pose_regression_passed=not any(failures.values()),failures=failures,
        metrics=compared,legacy_C2_validation_gates_claimed=False,
        reference_is_external_ground_truth=False,H_used_for_calibration=False,
        limitation='Engineering comparison after parameter freeze; no legacy convergence/observability claim',
        bound_inputs={name:sha(source/name) for name in bindings}))
    if any(sha(source/name)!=expected for name,expected in bindings.items()):
        raise ValueError('evaluation changed computation artifacts')
    write(out/'COMPUTATION_UNCHANGED.json',dict(passed=True,compute_files_sha256=bindings))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--previous-source',type=Path)
    args=parser.parse_args()
    review(args.source,args.out,previous_source=args.previous_source)
