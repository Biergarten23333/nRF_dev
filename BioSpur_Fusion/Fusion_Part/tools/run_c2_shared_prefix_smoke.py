"""Real arrived-prefix wiring test; no H and no accuracy acceptance claim."""
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,NODES,sha,FiveNodeFrontend,episode_contracts
from biospur_fusion.c2_five_calibration.frontend import fit_frontend
from biospur_fusion.c2_five_calibration.geometry import subject_geometry
from biospur_fusion.c2_five_calibration.placement import C2_VERTICES,PLACEMENT_EVIDENCE
from biospur_fusion.c2_five_calibration.replay import C2ReplayEvaluator
from biospur_fusion.c2_five_calibration.workflow import fingerprint,SMPL
from biospur_fusion.c2_five_calibration.shared_fit import fit_shared_orientation
from biospur_fusion.c2_five_calibration.calibration_prior import RegisteredHeadingPrior
from biospur_fusion.c2_five_calibration.arm_protocol import build_arm_protocol
from biospur_fusion.c2_imucoco.workflow import SURFACE
from biospur_fusion.c2_imucoco.backend import load_pose
from biospur_fusion.c2_imucoco.upstream import verify_assets


def main(out, matched_control=False, *, last_action='14_trunk_flex_extend', iterations=8, pose_iterations=3, outer_rounds=1, wall_limit_s=1500, bend_protocol=False):
    out=out.resolve()
    out.mkdir(exist_ok=False);start=time.monotonic();torch.set_num_threads(1)
    def write(name,data): (out/name).write_text(json.dumps(data,indent=2,allow_nan=False))
    contracts=episode_contracts();names=list(contracts);names=names[:names.index(last_action)+1]
    contracts={n:contracts[n] for n in names};sources=fingerprint();sources[str(Path(__file__).resolve())]=sha(Path(__file__).resolve())
    assets=verify_assets()
    write('CONTRACT.json',dict(actions=names,H_used=False,ten_used=False,source_sha256=sources,
        model_assets=assets,matched_control=matched_control,total_wall_limit_s=wall_limit_s,iterations=iterations,pose_iterations=pose_iterations,outer_rounds=outer_rounds,bend_protocol=bend_protocol,
        scope='real shared-prefix feedback wiring; constant-heading model unchanged; no accuracy proof'))
    episodes,audit=FiveNodeFrontend().read(contracts,keep_continuous=True);write('INPUT_AUDIT.json',audit)
    for n in NODES:
        a=episodes['_continuous'][n]['imu'];episodes['_continuous'][n]['imu']=a[a[:,0]<=episodes[names[-1]][n]['imu'][-1,0]]
    surface=json.loads(SURFACE.read_text());c=fit_frontend(episodes,surface,prefix=True)
    poser,body=load_pose(SMPL,out);g=subject_geometry(body,surface,sensor_vertices=C2_VERTICES);g['placement_evidence']=PLACEMENT_EVIDENCE
    write('BASELINE_FRONTEND.json',c);write('GEOMETRY.json',g)
    evaluator=C2ReplayEvaluator(episodes,c,g,poser,contracts,dict(input_sha256=sha(out/'INPUT_AUDIT.json'),source_sha256=sources,model_sha256=assets),
        prefix=True,continuous_grid=True,wall_limit_s=600,progress=lambda x:print(json.dumps(x),flush=True))
    baseline=evaluator(np.zeros(4));full={k:v[::3] for k,v in baseline['continuous'].items()}
    prior=RegisteredHeadingPrior(c['heading_factors'],c['frozen_heading_correction_rad'])
    protocol=build_arm_protocol(episodes,c,contracts,{n:full for n in names},prior.all_information,conditional_only=True)
    bends=None
    if bend_protocol:
        from biospur_fusion.c2_five_calibration.protocol_pose import build_bend_protocol
        bends=build_bend_protocol(contracts,{n:full for n in names})
    checkpoint,report=fit_shared_orientation(baseline['actions'],g,g['nominal_sensor_levers_m'],c['heading_factors'],c['frozen_heading_correction_rad'],evaluator,
        prefix=True,continuous=True,arm_protocol=protocol,pose_protocol=bends,matched_control=matched_control,iterations=iterations,pose_iterations=pose_iterations,outer_rounds=outer_rounds,wall_limit_s=wall_limit_s-(time.monotonic()-start))
    write('AUDIT.json',report);write('FRONTEND.json',checkpoint['replay']['frontend'])
    np.savez_compressed(out/'CHECKPOINT.npz',delta_rad=checkpoint['delta'].numpy(),levers=checkpoint['levers'].numpy(),rotation=checkpoint['rotations']['_continuous'].numpy(),time_s=full['time_s'])
    accepted_data=checkpoint['replay']['continuous']
    np.savez_compressed(out/'ACCEPTED_PRIOR.npz',**accepted_data)
    write('REPLAY_BINDING.json',checkpoint['replay']['binding'])
    for p,v in sources.items():
        if sha(ROOT/p)!=v:raise ValueError('producer changed during test')
    write('RESULT.json',dict(completed=True,calibration_accepted=False,heading_increment_deg=np.rad2deg(checkpoint['delta'].numpy()).tolist(),
        rounds=report['rounds'],elapsed_seconds=time.monotonic()-start,output_sha256=sha(out/'CHECKPOINT.npz'),H_used=False,ten_used=False,
        bindings={name:sha(out/name) for name in ('FRONTEND.json','GEOMETRY.json','ACCEPTED_PRIOR.npz','REPLAY_BINDING.json','INPUT_AUDIT.json','CONTRACT.json')}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',required=True,type=Path);p.add_argument('--matched-control',action='store_true')
    p.add_argument('--last-action',default='14_trunk_flex_extend');p.add_argument('--iterations',type=int,default=8)
    p.add_argument('--pose-iterations',type=int,default=3);p.add_argument('--outer-rounds',type=int,default=1);p.add_argument('--wall-limit-s',type=float,default=1500)
    p.add_argument('--bend-protocol',action='store_true')
    a=p.parse_args();main(a.out,a.matched_control,last_action=a.last_action,iterations=a.iterations,pose_iterations=a.pose_iterations,outer_rounds=a.outer_rounds,wall_limit_s=a.wall_limit_s,bend_protocol=a.bend_protocol)
