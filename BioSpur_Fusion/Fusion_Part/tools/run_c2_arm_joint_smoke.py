#!/usr/bin/env python3
"""Seal and run the bounded upper-arm mechanism smoke test."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
from scipy.spatial.transform import Rotation
from c2_arm_smoke_fixture import fixture
from c2_arm_joint_smoke import fit, wear_branch


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(out):
    out.mkdir(parents=True,exist_ok=False)
    sources = [Path('tools')/name for name in ('c2_arm_smoke_fixture.py','c2_arm_joint_smoke.py',
                                               'run_c2_arm_joint_smoke.py')]
    sources += [Path('src/biospur_fusion/c2_sparse_nodes/calibration.py')]
    sources += [Path('src/biospur_fusion/c2_five_calibration/progressive/arm_model.py')]
    wear_source=Path('config/biospur_fusion_v0_c2_main_contract_20260829/REVIEW_CHECKLIST_ZH.md')
    wear_text=wear_source.read_text()
    if '全部 sensor `-Y` 约朝地面' not in wear_text or '左、右、左后' not in wear_text:
        raise ValueError('wear-direction source changed')
    contract = dict(scope='conditional mounting/constant-heading mechanism only',
        raw_six_axis_navigation_tested=False,full_body_or_H_tested=False,
        real_recording_opened=False,ten_node_observations_used=False,
        all_C2_calibration_claimed=False,removed_pose_prior_used=False,
        source_sha256={str(p):sha(p) for p in sources},
        starts_deg=[[0,0,0,0],[20,-20,15,25],[-20,20,-15,-25],[0,0,180,0]],
        max_evaluations_per_fit=100,wall_limit_s=120,
        ideal_mount_and_heading_max_deg=3.,coupled_mount_and_heading_max_deg=5.,
        across_starts_mount_spread_max_deg=5.,negative_residual_cost_min=2.,
        anatomical_branch_guard_implemented=True,
        wear_source_sha256=sha(wear_source),wear_source=str(wear_source),
        branch_policy='retain all outcomes; only positive qualitative hemisphere margins eligible',
        directional_assumptions='broad pelvis-relative movement directions; chest is unobserved')
    (out/'CONTRACT.json').write_text(json.dumps(contract,indent=2))
    for p in sources:
        target=out/'source_snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,target)
    rows=[];start=time.monotonic()
    for case,coupled,swap in [('ideal',False,False),('coupled',True,False),('swapped_phases',False,True)]:
        actions,truth=fixture(coupled)
        for limb in range(2):
            fits=[]
            for initial in contract['starts_deg']:
                if time.monotonic()-start>120:raise TimeoutError('smoke wall limit reached')
                result=fit(actions,limb,np.deg2rad(initial),swap)
                result['mount_error_deg']=float(np.rad2deg(Rotation.from_matrix(result['mount']@truth['mounts'][limb].T).magnitude()))
                difference=result['heading']-truth['heading'][limb]
                result['heading_error_deg']=float(np.rad2deg(np.arctan2(np.sin(difference),np.cos(difference))))
                result['wear_branch_eligible']=wear_branch(result['mount'],limb)
                result['start_deg']=initial
                result['parameters']=result['parameters'].tolist();result['mount']=result['mount'].tolist()
                fits.append(result)
            eligible=[r for r in fits if r['wear_branch_eligible']]
            spread=max((float(np.rad2deg(Rotation.from_matrix(np.asarray(a['mount'])@np.asarray(b['mount']).T).magnitude()))
                       for a in eligible for b in eligible),default=180.)
            limit=3. if case=='ideal' else 5.
            positive=len(eligible)>=3 and all(r['success'] and r['mount_error_deg']<=limit and abs(r['heading_error_deg'])<=limit for r in eligible) and spread<=5.
            row=dict(case=case,limb=limb,fits=fits,mount_spread_deg=spread,
                     eligible_count=len(eligible),recovery_pass=positive if not swap else None,
                     negative_detected=all(r['cost']>=2. for r in fits) if swap else None)
            rows.append(row)
            print(json.dumps({k:v for k,v in row.items() if k!='fits'}),flush=True)
            (out/'RESULT.json').write_text(json.dumps(dict(complete=False,cases=rows),indent=2))
    actions,_=fixture()
    for episode in actions.values():
        for node in episode.values():node['imu'][:,8:11]=0
    try:
        fit(actions,0,np.zeros(4))
        unexcited_rejected=False
    except ValueError as exc:
        unexcited_rejected='insufficient' in str(exc)
    if any(sha(Path(p))!=v for p,v in contract['source_sha256'].items()):
        raise ValueError('source changed during smoke')
    result=dict(complete=True,cases=rows,zero_excitation_rejected=unexcited_rejected,
        all_gates_passed=unexcited_rejected and all(r['negative_detected'] if r['case']=='swapped_phases' else r['recovery_pass'] for r in rows),
        wall_s=time.monotonic()-start,product_accepted=False)
    (out/'RESULT.json').write_text(json.dumps(result,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='cases'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    main(parser.parse_args().out)
