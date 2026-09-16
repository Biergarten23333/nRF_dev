#!/usr/bin/env python3
"""Read-only C2 gyro/quaternion consistency probe; never a heading fit."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation
from vqf import VQF

from biospur_fusion.c2_sparse_nodes.inputs import NODES, sha
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input, write
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL, TPOSE_SEGMENTS
from biospur_fusion.c2_five_calibration.frontend import prepare
from biospur_fusion.c2_five_calibration.navigation import preintegrate_gyro


def vqf_bias_history(episodes):
    """Reproduce the initializer, recording the bias used for each gyro step."""
    history={};audit={}
    for node in NODES[1:3]:
        rows=episodes['_continuous'][node]['imu']
        state=VQF(.005);bias=[];maximum=0.
        for row in rows:
            # VQF integrates the gyro with the preceding bias, then updates
            # its bias estimate from acceleration. Record that causal order.
            bias.append(state.getBiasEstimate()[0])
            state.update(row[8:11],row[5:8])
            q=state.getQuat6D();stored=row[1:5]
            maximum=max(maximum,min(np.max(abs(q-stored)),np.max(abs(q+stored))))
        if maximum>1e-8:raise ValueError('continuous VQF reproduction differs: '+node)
        history[node]=(rows[:,0],np.asarray(bias))
        audit[node]=dict(frames=len(rows),quaternion_max_element_error=maximum,
            bias_sample_order='read before update, matching gyro integration',
            independent_heading_evidence=False)
    return history,audit


def probe(out, *, vqf_control=False):
    report_name='GYRO_INCREMENT_VQF_CONTROL.json' if vqf_control else 'GYRO_INCREMENT_CHECK.json'
    if (out/report_name).exists():
        raise ValueError('preserve existing navigation evidence')
    contract=json.loads((out/'TASK_CONTRACT.json').read_text())
    bindings=contract['probe_inputs_sha256']
    if any(sha(out/name)!=value for name,value in bindings.items()):
        raise ValueError('five-only frozen probe input changed')
    calibration=json.loads((out/'FRONTEND.json').read_text())
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    input_path=INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz'
    before=sha(input_path)
    episodes=load_input(input_path)
    if any(name.startswith('H') or set(ep)!=set(NODES) for name,ep in episodes.items()):
        raise ValueError('only retained five-node C2 is authorized')
    history,vqf_audit=vqf_bias_history(episodes) if vqf_control else ({},{})
    bias={};bias_audit={}
    for node in NODES[1:3]:
        rows=episodes['00_initial_still'][node]['imu']
        gyro=rows[:,8:11];speed=np.linalg.norm(gyro,axis=1)
        quiet=gyro[speed<=np.quantile(speed,.6)]
        bias[node]=np.median(quiet,axis=0)
        bias_audit[node]=dict(apparent_bias_rad_s=bias[node].tolist(),
            selected_rows=len(quiet),component_mad_rad_s=np.median(abs(quiet-bias[node]),axis=0).tolist(),
            interpretation='natural standing apparent rate; may contain real motion; not a certified bias or long-term covariance')
    reports={}
    for name,episode in episodes.items():
        if name=='_continuous':continue
        data=prepare(episode,calibration,geometry)
        grid=data['time_s'][::3];observed=data['orientation'][::3]
        valid_pose=data['input_valid'][::3]
        reports[name]={}
        for i,node in enumerate(NODES[1:3],1):
            rows=episode[node]['imu']
            mounting=(np.asarray(calibration['segment_axes_in_sensor'])[i]
                @TPOSE_SEGMENTS[i].T@WORLD_TO_SMPL.T
                @np.asarray(geometry['bone_frame_correction'])[i])
            q=observed[:,i];delta=q[:-1].transpose(0,2,1)@q[1:]
            up_in_previous=q[:-1].transpose(0,2,1)@np.array([0.,1.,0.])
            reports[name][node]={}
            rates=rows[:,8:11]
            hypotheses=[('zero_bias',np.zeros(3)),('initial_apparent_bias',bias[node])]
            if vqf_control:
                time,bias_history=history[node];index=np.searchsorted(time,rows[:,0])
                if not np.array_equal(time[index],rows[:,0]):raise ValueError('VQF bias time mismatch')
                rates=rates-bias_history[index]
                hypotheses=[('reproduced_vqf_bias',np.zeros(3))]
            for label,b in hypotheses:
                gyro,valid=preintegrate_gyro(rows[:,0],rates,grid,mounting,b,maximum_gap_s=.0075)
                finer,fine_valid=preintegrate_gyro(rows[:,0],rates,grid,mounting,b,
                    maximum_gap_s=.0075,subdivisions=2)
                if not np.array_equal(valid,fine_valid):raise ValueError('integration subdivision changed support')
                valid&=valid_pose[:-1]&valid_pose[1:]
                if not valid.any():raise ValueError('no supported increments: '+name+'/'+node)
                residual=Rotation.from_matrix(delta[valid]@gyro[valid].transpose(0,2,1)).as_rotvec()
                rate=residual/np.diff(grid)[valid,None]
                yaw_rate=(rate*up_in_previous[valid]).sum(-1)
                transverse=rate-yaw_rate[:,None]*up_in_previous[valid]
                reports[name][node][label]=dict(valid_intervals=int(valid.sum()),
                    rejected_intervals=int((~valid).sum()),
                    full_rotation_residual_p95_deg=float(np.degrees(np.quantile(np.linalg.norm(residual,axis=1),.95))),
                    integration_subdivision_difference_max_deg=float(np.degrees(Rotation.from_matrix(
                        gyro[valid]@finer[valid].transpose(0,2,1)).magnitude().max())),
                    yaw_rate_median_deg_s=float(np.degrees(np.median(yaw_rate))),
                    yaw_rate_p95_absolute_deg_s=float(np.degrees(np.quantile(abs(yaw_rate),.95))),
                    transverse_rate_p95_deg_s=float(np.degrees(np.quantile(np.linalg.norm(transverse,axis=1),.95))),
                    yaw_integral_supported_deg=float(np.degrees(np.sum(yaw_rate*np.diff(grid)[valid]))),
                    rate_residual_frame='preceding segment frame; projection on world up in that frame')
        print(json.dumps(dict(action=name,status='checked')),flush=True)
    if before!=sha(input_path) or any(sha(out/n)!=h for n,h in bindings.items()):
        raise ValueError('read-only input changed')
    write(out/report_name,dict(actions=reports,bias_sensitivity=bias_audit,vqf_reproduction=vqf_audit,
        input_sha256=before,source_sha256=sha(Path(__file__)),
        owner_sha256=sha(Path(preintegrate_gyro.__code__.co_filename)),
        maximum_raw_bracket_s=.0075,action_count=len(reports),
        heading_fit_performed=False,independent_information_claimed=False,
        covariance_identified=False,H_used=False,ten_node_used=False,
        status='READ_ONLY_BIAS_SENSITIVITY; NOT_A_CORRECTION_OR_POSE_PASS'))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--vqf-control',action='store_true')
    args=parser.parse_args();probe(args.out.resolve(),vqf_control=args.vqf_control)
