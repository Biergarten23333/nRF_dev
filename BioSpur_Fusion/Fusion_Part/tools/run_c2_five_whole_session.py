"""Bounded full-session C2 calibration; never loads H or ten-node replay."""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_imucoco.workflow import INPUT_RUN, load_input
from biospur_fusion.c2_imucoco.backend import load_pose, ChunkedPoseStream
from biospur_fusion.c2_five_calibration.frontend import prepare, initial_state
from biospur_fusion.c2_five_calibration.workflow import SMPL
from biospur_fusion.c2_five_calibration.whole_session import fit_whole_session
from biospur_fusion.c2_five_calibration.temporal_transport_check import check_raw_temporal_transport
from biospur_fusion.c2_five_calibration.session_heading import SessionHeadingParameters
from biospur_fusion.c2_sparse_nodes.inputs import ROOT, sha


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--final-iterations',type=int)
    parser.add_argument('--shared-reference-metric',action='store_true')
    parser.add_argument('--spatial-arm-axes',action='store_true',
                        help='experimental 3D soft functional axes; directed limb cues stay horizontal')
    parser.add_argument('--iterations',type=int,default=8)
    parser.add_argument('--wall-limit',type=float,default=1800.)
    args=parser.parse_args()
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    torch.set_num_threads(1)
    previous=ROOT/'logs/c2_joint_chain_20260914/joint_heading'
    frontend_path=previous/'BASELINE_FRONTEND.json'
    geometry_path=previous/'GEOMETRY.json'
    contracts_path=ROOT/'logs/c2_direction_chain_20260914_133819/candidate/INPUT_AUDIT.json'
    cache_path=ROOT/'logs/c2_overlap_repair_20260914/temporal_selected/C2_PRIOR.npz'
    baseline=json.loads(frontend_path.read_text());geometry=json.loads(geometry_path.read_text())
    if args.shared_reference_metric:
        from biospur_fusion.c2_five_calibration.acceleration_metric import MODEL
        geometry['acceleration_observation_model']=MODEL
    contracts=json.loads(contracts_path.read_text())['contracts']
    raw=load_input(INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz')
    prepared=prepare(raw['_continuous'],baseline,geometry)
    with np.load(cache_path) as cache:
        data={key:cache[key][:len(prepared['time_s'][::3])] for key in cache.files}
    for key,value in (('time_s',prepared['time_s']),('observed',prepared['orientation']),
                      ('acceleration',prepared['acceleration_mps2']),('valid',prepared['input_valid'])):
        np.testing.assert_array_equal(data[key],value[::3])
    resume=None
    resume_bindings={}
    if args.resume is not None:
        source=args.resume.resolve()
        if json.loads((source/'GEOMETRY.json').read_text())!=geometry:
            raise ValueError('resume geometry must be identical')
        old=json.loads((source/'RUN.json').read_text())
        resume={}
        for label,stage in [('control','full_C2_fixed_heading_control'),('proposal','full_C2_shared_calibration')]:
            file=source/(stage+'.npz')
            with np.load(file) as z:
                for key in ('time_s','valid'):np.testing.assert_array_equal(z[key],data[key])
                resume[label]={key:z[key] for key in ('parameters','coefficients','levers')}
            resume[label]['energy']=next(row['energy'] for row in old['stages'] if row['stage']==stage)
            resume_bindings[str(file)]=sha(file)
        resume_bindings[str(source/'RUN.json')]=sha(source/'RUN.json')
    manifest=dict(resume_bindings=resume_bindings,status='running',completed=False,H_used=False,reference_used=False,
        weekly_usage_stop_percent=50,iterations_per_stage=args.iterations,final_iterations=args.final_iterations,
        spatial_arm_axes=args.spatial_arm_axes,
        source_bindings={str(p):sha(p) for p in (frontend_path,geometry_path,contracts_path,cache_path,
            INPUT_RUN/'CALIBRATION_CONTINUOUS_INPUT.npz',Path(__file__).resolve())},
        implementation_bindings={str(p):sha(p) for p in
            list((ROOT/'src/biospur_fusion/c2_five_calibration').rglob('*.py')) +
            [ROOT/'src/biospur_fusion/c2_sparse_nodes/heading_transport.py']},
        calibration_input_policy='all recorded 00-19, five IMUs, measured geometry; 01 absent in capture',
        stages=[])
    def write():
        (out/'RUN.json').write_text(json.dumps(manifest,indent=2))
    write()
    def progress(audit,result):
        name=audit['stage']
        np.savez_compressed(out/(name+'.npz'),time_s=data['time_s'],valid=data['valid'],
            rotation=result['rotations']['_continuous'].numpy(),
            parameters=result['parameters']['_continuous'].numpy(),levers=result['levers'].numpy(),
            delta=result['delta'].numpy(),coefficients=result['heading_coefficients'].numpy())
        manifest['stages'].append(audit);write();print(json.dumps(audit),flush=True)
    def replay(frontend,coefficients):
        (out/'FRONTEND_PROPOSAL.json').write_text(json.dumps(frontend,indent=2))
        fresh=prepare(raw['_continuous'],frontend,geometry)
        initial=initial_state(prepare(raw['00_initial_still'],frontend,geometry),geometry)
        # Upstream loader temporarily changes cwd; contain that side effect.
        cwd=Path.cwd()
        try:poser,_=load_pose(SMPL,out)
        finally:os.chdir(cwd)
        stream=ChunkedPoseStream(poser,initial_global_rotation=initial,sensor_vertices=geometry['sensor_vertices'])
        prediction,neural=stream.run(fresh['features'],input_valid=fresh['input_valid'],
            wall_limit_s=min(args.wall_limit,700.),progress=lambda row:print(json.dumps(row),flush=True))
        q=dict(time_s=fresh['time_s'][::3],valid=fresh['input_valid'][::3],
            observed=fresh['orientation'][::3],acceleration=fresh['acceleration_mps2'][::3],
            prior=prediction['global_rotation'][::3])
        model=SessionHeadingParameters(baseline,data['time_s'],contracts)
        transport=check_raw_temporal_transport(raw['_continuous'],baseline,geometry,coefficients,data,q,heading_model=model)
        np.savez_compressed(out/'C2_FRESH_PRIOR.npz',**q)
        return dict(data=q,transport=transport,binding=dict(neural=neural,
            prior_sha256=sha(out/'C2_FRESH_PRIOR.npz'),frontend_sha256=sha(out/'FRONTEND_PROPOSAL.json')))
    try:
        result,audit=fit_whole_session(data,geometry,baseline,contracts,
            {name:raw[name] for name in contracts},replay,
            iterations=args.iterations,wall_limit_s=args.wall_limit,progress=progress,
            resume=resume,final_iterations=args.final_iterations,
            spatial_arm_axes=args.spatial_arm_axes,
            iteration_progress=lambda row:print(json.dumps(row),flush=True))
        (out/'RESULT.json').write_text(json.dumps(audit,indent=2))
        (out/'GEOMETRY.json').write_text(json.dumps(geometry,indent=2))
        manifest.update(status='completed',completed=True,tracking_accepted=False,
                        calibration_energy_improved=audit['calibration_energy_improved'])
        write()
    except Exception as error:
        manifest.update(status='failed',error=repr(error));write();raise


if __name__=='__main__':main()
