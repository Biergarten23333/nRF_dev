#!/usr/bin/env python3
"""Expose reset-reference versus anatomical-pose comparison semantics.

The alternative reference uses a previously frozen five-node initial pose only
as a declared evaluation convention. It cannot validate that initial pose.
"""
import argparse,json
from pathlib import Path
import numpy as np
import torch
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_five_calibration.solver import bend_cosines
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN,write
from biospur_fusion.c2_sparse_nodes.evaluation import CAL_REFERENCE,PAIRS,baseline_on_grid
from biospur_fusion.c2_sparse_nodes.inputs import sha


def main(out):
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    initial=np.array(json.loads((out/'INITIAL_STATE.json').read_text())['global_rotation'])
    actions=action_data(out)
    contracts=json.loads((INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
    change=WORLD_TO_SMPL@Rotation.from_rotvec([0.,0.,-np.pi/2]).as_matrix()
    mapping=dict(pelvis=[0],torso=[3,6,9,12,13,14,15],upper_arm_left=[16],upper_arm_right=[17],
        forearm_left=[18],forearm_right=[19],thigh_left=[1],thigh_right=[2],shank_left=[4],shank_right=[5])
    report={}
    with np.load(CAL_REFERENCE) as reference,np.load(out/'C2_VALIDATION.npz') as fitted:
        for index,(name,w) in enumerate(contracts.items()):
            q=actions[name];ref,valid=baseline_on_grid(reference,f'{index:02d}',q['time_s'],w,False)
            valid &= q['valid']
            pose=np.repeat(initial[None],len(q['time_s']),axis=0)
            for segment,joints in mapping.items():
                for j in joints:pose[:,j]=change@ref[segment]@change.T@initial[j]
            bend=lambda r:np.rad2deg(np.arccos(np.clip(bend_cosines(torch.tensor(r),geometry).numpy(),-1,1)))
            ours=bend(fitted[name+'/rotation']); aligned=bend(pose)
            reset=np.rad2deg(np.arccos(np.clip(np.stack([(ref[a][:,:,2]*ref[b][:,:,2]).sum(-1) for a,b in PAIRS],-1),-1,1)))
            report[name]=dict(our_median_bend_deg=np.median(ours[valid],0).tolist(),
                original_reset_median_bend_deg=np.median(reset[valid],0).tolist(),
                common_initial_median_bend_deg=np.median(aligned[valid],0).tolist(),
                common_initial_MAE_deg=np.mean(abs(ours-aligned)[valid],0).tolist(),
                common_initial_P95_deg=np.quantile(abs(ours-aligned)[valid],.95,axis=0).tolist())
    write(out/'REFERENCE_GAUGE_AUDIT.json',dict(actions=report,
        original_reference_modified=False,alternative_is_motion_consistency_only=True,
        validates_absolute_initial_bend=False,calibration_parameters_changed=False,
        initial_pose_sha256=sha(out/'INITIAL_STATE.json'),reference_sha256=sha(CAL_REFERENCE),
        source='pose_reset_avatar.py build_pose_reset_trajectory resets each segment independently to initial orientation'))
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    args=p.parse_args();main(args.out.resolve())
