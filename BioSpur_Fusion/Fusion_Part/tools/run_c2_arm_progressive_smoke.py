#!/usr/bin/env python3
"""Bounded chronological synthetic run, with separate post-update truth scoring."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
from scipy.spatial.transform import Rotation
from c2_arm_progressive_fixture import phase_stream,deliver
from biospur_fusion.c2_five_calibration.progressive import ArmProgressiveSession
from biospur_fusion.c2_five_calibration.progressive.session import digest


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def score(snapshot, truth):
    result=[]
    for limb,state in enumerate(snapshot['arms']):
        row=state['selected']
        if row is None:result.append(None);continue
        error=Rotation.from_matrix(np.asarray(row['mount'])@truth['mounts'][limb].T).magnitude()
        delta=row['heading']-truth['heading'][limb]
        result.append(dict(mount_error_deg=float(np.rad2deg(error)),
            heading_error_deg=float(np.rad2deg(np.arctan2(np.sin(delta),np.cos(delta))))))
    return result


def main(out):
    out.mkdir(parents=True,exist_ok=False)
    package=Path('src/biospur_fusion/c2_five_calibration/progressive')
    sources=list(package.glob('*.py'))+[Path('tools')/name for name in (
        'run_c2_arm_progressive_smoke.py','c2_arm_progressive_fixture.py','c2_arm_smoke_fixture.py')]
    sources += [Path('src/biospur_fusion/c2_sparse_nodes/calibration.py'),Path('tests/test_c2_arm_progressive.py')]
    contract=dict(status='PREDECLARED_SYNTHETIC_GATE',source_sha256={str(p):sha(p) for p in sources},
        cases=['ideal','coupled','early_tpose_bias_12deg','swapped_elbow_phases'],
        full_C2_calibration=False,raw6_navigation_tested=False,real_payload_opened=False,
        ten_node_data_used=False,H_opened=False,wall_limit_s=120,
        ideal_mount_heading_limit_deg=3.,coupled_mount_heading_limit_deg=5.,
        correction_gate='absolute final heading error smaller than after T-pose for both arms',
        batch_cost_tolerance=1e-7,batch_mount_tolerance_deg=.001,
        negative_gate='both final arms report CONFLICT',
        selected_state_confidence='conditional composite objective; not a probability')
    (out/'CONTRACT.json').write_text(json.dumps(contract,indent=2))
    for p in sources:
        target=out/'source_snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,target)
    start=time.monotonic();results=[]
    for case in contract['cases']:
        events,truth=phase_stream(case=='coupled',early_tpose_bias_deg=12. if case=='early_tpose_bias_12deg' else 0.)
        if case=='swapped_elbow_phases':
            for first in (5,7):
                left,right=copy.deepcopy(events[first]['rows']),copy.deepcopy(events[first+1]['rows'])
                for node in left:
                    events[first]['rows'][node][:,1:]=right[node][:,1:]
                    events[first+1]['rows'][node][:,1:]=left[node][:,1:]
        session=ArmProgressiveSession(source_kind='synthetic_orientation_and_gyro')
        rows=[]
        for event in events:
            if time.monotonic()-start>120:raise TimeoutError('smoke total wall budget reached')
            snapshot=deliver(session,event,parts=3)
            # Scoring happens only after the immutable update; these values
            # never enter the session, its readiness or subsequent updates.
            rows.append(dict(snapshot=snapshot,truth_assessment=score(snapshot,truth)))
            (out/(case+'_TRACE.json')).write_text(json.dumps(rows,indent=2))
        frozen_hash=session.state_digest
        batch=session.batch_check()
        assert frozen_hash==session.state_digest
        differences=[]
        for a,b in zip(session.snapshot()['arms'],batch):
            if a['selected'] is None or b['selected'] is None:
                differences.append(dict(comparable=False));continue
            ra,rb=a['selected'],b['selected']
            differences.append(dict(comparable=True,cost_delta=abs(ra['cost']-rb['cost']),
                mount_delta_deg=float(np.rad2deg(Rotation.from_matrix(
                    np.asarray(ra['mount'])@np.asarray(rb['mount']).T).magnitude()))))
        consistent=all(d.get('comparable') and d['cost_delta']<=1e-7 and d['mount_delta_deg']<=.001 for d in differences)
        if case in ('ideal','coupled'):
            limit=3. if case=='ideal' else 5.
            passed=all(s is not None and s['mount_error_deg']<=limit and abs(s['heading_error_deg'])<=limit
                       for s in rows[-1]['truth_assessment'])
            passed &= all(a['status']=='CONDITIONAL_SUPPORT' for a in session.snapshot()['arms'])
        elif case=='early_tpose_bias_12deg':
            passed=all(abs(b['heading_error_deg'])<abs(a['heading_error_deg'])
                       for a,b in zip(rows[1]['truth_assessment'],rows[-1]['truth_assessment']) if a is not None and b is not None)
            passed &= all(a is not None for a in rows[1]['truth_assessment']+rows[-1]['truth_assessment'])
        else:
            passed=all(a['status']=='CONFLICT' for a in session.snapshot()['arms'])
        result=dict(case=case,passed=bool(passed and consistent),batch_consistent=consistent,
            batch_differences=differences,initial_tpose_errors=rows[1]['truth_assessment'],
            final_errors=rows[-1]['truth_assessment'],final_states=[a['status'] for a in session.snapshot()['arms']],
            snapshot_sha256=digest(session.snapshots),trace_sha256=sha(out/(case+'_TRACE.json')))
        session.save(out/(case+'_CHECKPOINT.json'))
        results.append(result);print(json.dumps(result),flush=True)
    if any(sha(Path(p))!=v for p,v in contract['source_sha256'].items()):
        raise ValueError('source changed during run')
    report=dict(complete=True,cases=results,all_gates_passed=all(r['passed'] for r in results),
        wall_s=time.monotonic()-start,product_accepted=False,independent_review=False)
    (out/'RESULT.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    main(parser.parse_args().out)
