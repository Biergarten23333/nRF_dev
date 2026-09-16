#!/usr/bin/env python3
"""Acceptance is a separate read-only assessment, not a solver output."""
import argparse
import json
import math
from pathlib import Path
from biospur_fusion.c2_five_calibration.frontend import FIT
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_sparse_nodes.inputs import sha


def angle_failures(rows, metric_limits):
    failures=[]
    for name,row in rows.items():
        if row['compared_frames']<=0:raise ValueError('comparison has no valid frames: '+name)
        for key,limit in metric_limits:
            values=row[key]
            if len(values)!=4 or not all(math.isfinite(v) and v>=0 for v in values):
                raise ValueError('four finite joint errors required: '+name+'/'+key)
            for joint,value in enumerate(values):
                if value>limit:failures.append(dict(action=name,joint=joint,metric=key,value=value,limit=limit))
    return failures


def assess(out):
    target=out/'ASSESSMENT.json'
    if target.exists():raise ValueError('assessment already exists')
    contract=json.loads((out/'TASK_CONTRACT.json').read_text())
    inertial=json.loads((out/'C2_VALIDATION.json').read_text())
    comparison=json.loads((out/'REFERENCE_COMPARISON.json').read_text())
    checked={k:v for k,v in comparison['metrics'].items() if k[:2] in FIT}
    limits=contract['regression_gates']
    failures=angle_failures(checked,[('physical_bend_mae_deg',limits['C2_check_each_joint_MAE_deg_max']),
                                   ('physical_bend_p95_deg',limits['C2_check_each_joint_P95_deg_max'])])
    passed=all(inertial['gates'].values()) and not failures and {k[:2] for k in checked}==FIT
    write(target,dict(status='C2_SELF_CHECK_PASSED_H_PENDING' if passed else 'C2_SELF_CHECK_FAILED',
        C2_passed=passed,H_allowed=passed,H_completed=False,product_accepted=False,
        C2_check_is_in_sample=True,calibration_protocol='ALL_RECORDED_C2',
        checked_actions=list(checked),
        inertial_gates=inertial['gates'],regression_failures=failures,
        joint_order=['left_elbow','right_elbow','left_knee','right_knee'],
        reference_is_external_ground_truth=False,
        bound_inputs={name:sha(out/name) for name in ('TASK_CONTRACT.json','PHYSICAL_CALIBRATION.json','C2_VALIDATION.json','REFERENCE_COMPARISON.json')}))
    print(json.dumps(dict(C2_passed=passed,failures=failures),indent=2))


def assess_h(out):
    """Close the frozen C2/H regression without rewriting the C2 assessment."""
    target=out/'FINAL_ASSESSMENT.json'
    if target.exists():raise ValueError('final assessment already exists')
    c2=json.loads((out/'ASSESSMENT.json').read_text())
    h=json.loads((out/'H_REPLAY.json').read_text())
    comparison=json.loads((out/'C2_H_REFERENCE_COMPARISON.json').read_text())
    for name,expected in {**c2['bound_inputs'],**h['frozen_inputs']}.items():
        if sha(out/name)!=expected:raise ValueError('frozen input changed: '+name)
    if h['output_sha256']!=sha(out/'H_REPLAY.npz') or comparison['H_output_sha256']!=h['output_sha256']:
        raise ValueError('H comparison is not bound to the replay')
    parameters=json.loads((out/'PHYSICAL_CALIBRATION.json').read_text())
    if {n[:2] for n in parameters['actions']}!=FIT:
        raise ValueError('full C2 calibration coverage is required')
    limits=json.loads((out/'TASK_CONTRACT.json').read_text())['regression_gates']
    checked={k:v for k,v in comparison['metrics'].items() if k.startswith('H')}
    if set(checked)!={'H01_boxing','H02_golf'}:
        raise ValueError('both H actions must be compared')
    failures=angle_failures(checked,[('physical_bend_mae_deg',limits['H_each_joint_MAE_deg_max']),
                                   ('physical_bend_p95_deg',limits['H_each_joint_P95_deg_max'])])
    if h['H_used_for_calibration'] or h['ten_node_reference_opened'] or h['UWB_measurements_used']:
        raise ValueError('H inference input boundary violated')
    passed=c2['C2_passed'] and not failures
    report=dict(status='FIVE_NODE_REGRESSION_PASSED' if passed else 'FIVE_NODE_REGRESSION_FAILED',
        regression_passed=passed,product_accepted=False,C2_check_is_in_sample=True,
        C2_check_passed=c2['C2_passed'],H_regression_passed=not failures,
        all_recorded_C2_actions_fitted=True,H_used_for_calibration=False,
        H_failures=failures,H_metrics=checked,
        limitation='Same-session ten-node comparison; H has been seen during development. Not blind external motion-capture validation.',
        source_sha256=sha(Path(__file__)),
        bound_inputs={name:sha(out/name) for name in ('ASSESSMENT.json','H_REPLAY.json','C2_H_REFERENCE_COMPARISON.json')})
    write(target,report)
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    p.add_argument('--with-h',action='store_true')
    args=p.parse_args();(assess_h if args.with_h else assess)(args.out.resolve())
