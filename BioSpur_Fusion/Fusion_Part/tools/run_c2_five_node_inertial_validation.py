#!/usr/bin/env python3
"""Frozen five-IMU continuous C2/H regression, with bounded solver stages."""
import argparse
import json
from pathlib import Path
import time
import zipfile
import numpy as np

from biospur_fusion.c2_sparse_nodes.inputs import (
    ROOT,NODES,FiveNodeFrontend,episode_contracts,save_input,sha,
)
from biospur_fusion.c2_sparse_nodes.calibration import calibrate
from biospur_fusion.c2_sparse_nodes.protocol import calibration_ledger
from biospur_fusion.c2_sparse_nodes.inertial import calibrate_inertial,synchronized_inertial
from biospur_fusion.c2_sparse_nodes.inertial_replay import solve_stream
from biospur_fusion.c2_sparse_nodes.inertial_window import WINDOW_CONFIG


def write(path,value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')


def load(path):
    with np.load(path,allow_pickle=False) as data:
        names=list(dict.fromkeys(k.split('/')[0] for k in data.files))
        return {e:{n:{'imu':data[f'{e}/{n}/imu']} for n in NODES} for e in names}


def summarize(arr):
    return dict(frames=len(arr['time_s']),solved_frames=int(arr['optimizer_success'].sum()),
        invalid_input_frames=int((~arr['input_valid']).sum()),
        bend_p95_deg=np.percentile(arr['bend_deg'],95,axis=0).tolist())


def run_stream(out,prefix,ep,calibration,contracts,metrics,initial=None,prepared=None,prefix_arrays=None):
    t,r,a,valid=synchronized_inertial(ep,calibration) if prepared is None else prepared
    arr,audit=solve_stream(t,r,a,valid,calibration,initial_state=initial,config=calibration['inertial_solver'],
        progress=lambda w:print(prefix,w['start'],w['stop'],w['success'],round(w['wall_s'],2),flush=True),
        wall_limit_s=1200.)
    if prefix_arrays is not None:
        arr={k:np.concatenate((prefix_arrays[k],v)) for k,v in arr.items()}
        audit['reused_prefix_frames']=len(prefix_arrays['time_s'])
        t=arr['time_s']
    np.savez_compressed(out/(prefix+'_CONTINUOUS_REPLAY.npz'),**arr)
    write(out/(prefix+'_SOLVER_AUDIT.json'),audit)
    for name,contract in contracts.items():
        keep=(t>=contract['lo'])&(t<contract['hi'])
        if not keep.any():raise RuntimeError('empty episode '+name)
        subset={k:v[keep] for k,v in arr.items()}
        np.savez_compressed(out/(name+'_REPLAY.npz'),**subset)
        metrics[name]=summarize(subset)
    write(out/'REPLAY_METRICS.json',metrics)
    return arr


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    ap.add_argument('--stage',choices=['calibration','holdout'],required=True)
    args=ap.parse_args();out=args.output.resolve();started=time.monotonic()
    if not out.is_relative_to(ROOT/'logs'):raise ValueError('logs output required')
    if args.stage=='calibration':
        if (out/'FREEZE_SEAL.json').exists():raise ValueError('cannot overwrite frozen model')
        data=load(out/'CALIBRATION_CONTINUOUS_INPUT.npz')
        surface=ROOT/'config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json'
        c=calibrate_inertial(data,calibrate(data,json.loads(surface.read_text())))
        files=[ROOT/'src/biospur_fusion/c2_sparse_nodes'/name for name in
            ('inputs.py','calibration.py','model.py','inertial.py','inertial_model.py',
             'inertial_window.py','inertial_optimizer.py','inertial_replay.py','functional_frames.py','protocol.py')]+[Path(__file__).resolve()]
        code={str(p.relative_to(ROOT)):sha(p) for p in files}
        c.update(inertial_solver=WINDOW_CONFIG,source_sha256=code,hz=20,
            surface_sha256=sha(surface),input_sha256=sha(out/'CALIBRATION_CONTINUOUS_INPUT.npz'),
            reconstruction='offline missing-segment swing fitted to sensor acceleration; calibrated distal rotations immutable',
            geometry_used_in_orientation_fit=True,physical_geometry_index=1,
            heldout_used_for_fit=False,code_frozen_before_holdout=True,
            validation_scope='same-session regression; ten-node reference is not ground truth')
        c['assumptions']=[a for a in c['assumptions'] if 'elbow flexion plus' not in a]+[
            'missing proximal axial rotation is a display convention; inferred swing uses dynamic and generic posture constraints',
            'sensor-to-joint offsets fitted from calibration motion; active bounds mark uncertain fits']
        write(out/'FIVE_NODE_CALIBRATION_PROTOCOL.json',calibration_ledger(data,c))
        write(out/'CALIBRATION_FROZEN.json',c)
        write(out/'FREEZE_SEAL.json',dict(calibration_sha256=sha(out/'CALIBRATION_FROZEN.json'),
            source_sha256=code,holdout_opened=False,
            status='GO_FOR_REGRESSION; NOT_POSE_ACCURACY_ACCEPTANCE'))
        with zipfile.ZipFile(out/'FROZEN_MODEL_SOURCE.zip','x',compression=zipfile.ZIP_DEFLATED) as z:
            for p in files:z.write(p,str(p.relative_to(ROOT)))
        contracts=json.loads((out/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
        run_stream(out,'C2',data['_continuous'],c,contracts,{})
        write(out/'CALIBRATION_REPLAY_COMPLETE.json',dict(wall_s=time.monotonic()-started))
    else:
        c=json.loads((out/'CALIBRATION_FROZEN.json').read_text())
        seal=json.loads((out/'FREEZE_SEAL.json').read_text())
        if sha(out/'CALIBRATION_FROZEN.json')!=seal['calibration_sha256']:raise RuntimeError('model changed')
        if any(sha(ROOT/p)!=h for p,h in seal['source_sha256'].items()):raise RuntimeError('source changed')
        if (out/'RUN_COMPLETE.json').exists():raise ValueError('completed output cannot be overwritten')
        # Restore the actual VQF states by a deterministic raw C2 pass. This
        # consumes no missing-node payload and does not re-fit any parameter.
        cached=out/'HOLDOUT_CONTINUOUS_INPUT.npz'
        if cached.exists():
            hold=load(cached)
            haudit=json.loads((out/'HOLDOUT_INPUT_AUDIT.json').read_text())
            if not haudit.get('inter_action_motion_retained') or haudit['reset_count']!=0:
                raise ValueError('cached holdout does not preserve continuous IMU states')
            if haudit['consumed_nodes']!=list(NODES):raise ValueError('cached holdout node firewall mismatch')
        else:
            frontend=FiveNodeFrontend();_,audit=frontend.read(episode_contracts())
            hold,haudit=frontend.read(episode_contracts(True),start=frontend.cursor,keep_continuous=True)
            save_input(cached,hold);write(out/'HOLDOUT_INPUT_AUDIT.json',haudit)
        cal=load(out/'CALIBRATION_CONTINUOUS_INPUT.npz')['_continuous']
        continuous={n:{'imu':np.concatenate((cal[n]['imu'],hold['_continuous'][n]['imu']))} for n in NODES}
        # Re-open only the final C2 window using its original incoming state.
        # All earlier solved windows are identical to a combined-stream pass.
        prepared=synchronized_inertial(continuous,c)
        previous=json.loads((out/'C2_SOLVER_AUDIT.json').read_text())['windows'][-1]
        cut=previous['start']
        with np.load(out/'C2_CONTINUOUS_REPLAY.npz',allow_pickle=False) as d:
            prefix_arrays={k:d[k][:cut] for k in d.files}
        if not np.array_equal(prefix_arrays['time_s'],prepared[0][:cut]):
            raise RuntimeError('combined sample grid changed')
        metrics=json.loads((out/'REPLAY_METRICS.json').read_text())
        run_stream(out,'C2_H',continuous,c,haudit['contracts'],metrics,
            initial=np.asarray(previous['initial_states']),
            prepared=tuple(v[cut:] for v in prepared),prefix_arrays=prefix_arrays)
        if sha(out/'CALIBRATION_FROZEN.json')!=seal['calibration_sha256']:raise RuntimeError('model changed')
        write(out/'RUN_COMPLETE.json',dict(status='REGRESSION_COMPLETE_NOT_VALIDATED_PRODUCT',
            episodes=len(metrics),calibration_sha256=seal['calibration_sha256'],
            continuous_pose_state=True,uwb_ranges_or_positions_used=False,
            wall_s=time.monotonic()-started))


if __name__=='__main__':main()
