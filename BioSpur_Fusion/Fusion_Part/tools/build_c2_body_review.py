"""Render the fixed-policy body experiment with existing synchronized viewer."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer
from biospur_fusion.c2_sparse_nodes.articulated_reference import verify_reference
from biospur_fusion.c2_five_calibration.geometry import DISPLAY,joints_from_global
from biospur_fusion.c2_five_calibration.body_feasibility import BodyFeasibility
from biospur_fusion.c2_imucoco.body import bend_angles
from c2_five_geometry_review import five_fk_to_output


def main(out):
    # H is sealed before the original ten-node comparison is opened here.
    hresult=json.loads((out/'H_RESULT.json').read_text())
    if not hresult['completed']:raise ValueError('sealed H replay required')
    for name,value in hresult['outputs'].items():
        if sha(out/('H_'+name+'.npz'))!=value:raise ValueError('H result changed')
    for name,value in hresult['frozen']['C2_outputs'].items():
        if sha(out/('all_C2_'+name+'.npz'))!=value:raise ValueError('C2 result changed')
    if sha(out/'BODY_GEOMETRY.json')!=hresult['frozen']['geometry_sha256']:
        raise ValueError('body geometry changed')
    old=ROOT/'logs/c2_shared_full_20260914/review'
    reference=verify_reference();oldmeta=json.loads((old/'REVIEW.json').read_text())
    if reference!=oldmeta['reference']:raise ValueError('reference binding changed')
    text=(old/'C2_H_REPLAY_REVIEW.html').read_text()
    payload,_=json.JSONDecoder().raw_decode(text.split('const D=',1)[1])
    own=oldmeta['output_frame'];g=json.loads((out/'BODY_GEOMETRY.json').read_text())
    cache={}
    for phase in ('all_C2','H'):
        for branch in ('control','constrained'):
            with np.load(out/(phase+'_'+branch+'.npz'),allow_pickle=False) as z:
                cache[phase,branch]={k:z[k] for k in ('time_s','rotation','valid')}
    reports={}
    for name,episode in payload['episodes'].items():
        phase='H' if name.startswith('H') else 'all_C2';metrics={}
        for branch,key in (('constrained','five'),('control','standard')):
            q=cache[phase,branch];t=np.asarray(episode['sample_time_s'])
            ids=np.searchsorted(q['time_s'],t)
            ids=np.minimum(ids,len(q['time_s'])-1)
            previous=np.maximum(ids-1,0)
            ids=np.where(abs(q['time_s'][previous]-t)<abs(q['time_s'][ids]-t),previous,ids)
            if np.max(abs(q['time_s'][ids]-t))>1e-6:raise ValueError('different physical sample times')
            r=q['rotation'][ids]
            xyz=five_fk_to_output(joints_from_global(torch.as_tensor(r),g).numpy()[:,DISPLAY],own,source_space='SMPL_FK')
            episode[key]=np.repeat(xyz[:,None],3,axis=1).round(4).tolist()
            bends=bend_angles(xyz)
            error=abs(bends-np.asarray(episode['reference_bends']))[np.asarray(episode['valid'])]
            metrics[branch]=dict(bend_mae_deg=error.mean(0).tolist(),bend_p95_deg=np.quantile(error,.95,axis=0).tolist(),
                body=BodyFeasibility(g).audit(torch.as_tensor(r),torch.as_tensor(q['valid'][ids])))
            if key=='five':episode['bends']=bends.round(1).tolist()
        reports[name]=metrics
    payload['panels'][0].update(title='五节点 · 身体约束候选',caption='固定校准；保守体积＋身体相对姿态检查')
    payload['panels'][1].update(title='五节点 · 同预算对照',caption='相同输入、起点和步数；未加身体约束')
    payload['status_text']='身体约束实验回放；仍未通过整体动作验收。三窗同一采样时刻、相机联动；H 未用于参数拟合。'
    payload['model_text']='固定旧五节点安装与杆臂；只改变身体结构项和选解规则。身体体积含模型近似；十节点仅作工程对照。'
    review=out/'review';review.mkdir(exist_ok=True)
    write_viewer(review/'C2_H_REPLAY_REVIEW.html',payload)
    (review/'REVIEW.json').write_text(json.dumps(dict(metrics=reports,reference=reference,
        old_review_sha256=sha(old/'C2_H_REPLAY_REVIEW.html'),product_accepted=False),indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True)
    main(p.parse_args().out.resolve())
