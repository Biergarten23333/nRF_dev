#!/usr/bin/env python3
"""Read-only physical diagnostics, separate from fitting and gate decisions."""
import argparse
import copy
import json
from pathlib import Path
import numpy as np
import torch
from biospur_fusion.c2_imucoco.workflow import write
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_five_calibration.frontend import FIT
from biospur_fusion.c2_five_calibration.anatomy import JointModel
from biospur_fusion.c2_five_calibration.solver import acceleration_residual,valid_support


def audit(out):
    g=json.loads((out/'GEOMETRY.json').read_text())
    fit=json.loads((out/'PHYSICAL_CALIBRATION.json').read_text())
    actions=action_data(out)
    changed=copy.deepcopy(g)
    for tip in (18,19,20,21,4,5,7,8):
        v=np.array(changed['rest_offsets_m'][tip])
        changed['rest_offsets_m'][tip]=(v*(1+.02/np.linalg.norm(v))).tolist()
    report={}
    for name,q in actions.items():
        if name[:2] not in FIT: continue
        prior=torch.tensor(q['prior'],dtype=torch.float64)
        model=JointModel(g)
        observed=torch.tensor(q['observed'])
        r=model.rotation(prior,observed,model.initial(prior,observed))
        a=torch.tensor(q['acceleration']);keep=valid_support(q['valid'])
        def residual(geometry,levers):
            return acceleration_residual(r,a,geometry,levers).numpy()[keep]
        nominal=residual(g,g['nominal_sensor_levers_m'])
        fitted=residual(g,fit['fitted_sensor_levers_m'])
        modified=residual(changed,fit['fitted_sensor_levers_m'])
        rms=lambda x:float(np.sqrt(np.mean(x*x)))
        report[name]=dict(nominal_rms_mps2=rms(nominal),fitted_rms_mps2=rms(fitted),
            fixed_pose_offset_rms_ratio=rms(fitted)/rms(nominal),
            length_plus_20mm_prediction_delta_rms_mps2=rms(modified-fitted))
    target=out/'FIXED_POSE_TRANSFER_AUDIT.json'
    if target.exists(): raise ValueError('audit exists')
    write(target,dict(actions=report,pose_fixed_across_comparison=True,
        rationale='all-C2 offset consistency compared at fixed poses',
        C2_check_is_in_sample=True,
        qualification='in-sample sensitivity and consistency; not holdout validation or pose accuracy truth'))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path)
    args=p.parse_args();torch.set_num_threads(2);audit(args.out.resolve())
