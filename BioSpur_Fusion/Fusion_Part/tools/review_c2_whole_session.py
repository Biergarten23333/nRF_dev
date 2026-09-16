"""Export complete C2/H diagnostics with the established synchronized viewer."""
import argparse
import copy
import json
from pathlib import Path

import numpy as np
import torch

from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_sparse_nodes.articulated_reference import verify_reference
from biospur_fusion.c2_five_calibration.geometry import DISPLAY,joints_from_global
from c2_five_geometry_review import five_fk_to_output,LIMBS


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate',type=Path,required=True)
    args=parser.parse_args();out=args.candidate.resolve()
    evaluation=json.loads((out/'H_EVALUATION.json').read_text())
    producer=json.loads((out/'H_PRODUCER.json').read_text())
    if not producer['completed'] or producer['H_used_for_fit'] or producer['reference_used']:
        raise ValueError('completed frozen H producer required')
    for name,binding in producer['bindings'].items():
        if sha(out/name)!=binding:raise ValueError('frozen candidate changed')
    if sha(out/'H_REPLAY.npz')!=producer['output_sha256']:raise ValueError('H changed')
    old=ROOT/'logs/c2_direction_chain_20260914_133819/review'
    metadata=json.loads((old/'REVIEW.json').read_text())
    if evaluation['reference']!=verify_reference() or metadata['reference']!=evaluation['reference']:
        raise ValueError('frozen reference mismatch')
    template=old/'C2_H_HEADING_REPAIR_REVIEW.html';text=template.read_text()
    prefix,payload=text.split('const D=',1);data,end=json.JSONDecoder().raw_decode(payload)
    geometry=json.loads((out/'GEOMETRY.json').read_text());bindings={}
    torch.set_num_threads(1)
    for filename,is_h in [('full_C2_proposal_final.npz',False),('H_REPLAY.npz',True)]:
        bindings[filename]=sha(out/filename)
        with np.load(out/filename) as z:
            for name,e in data['episodes'].items():
                if name.startswith('H')!=is_h:continue
                time=np.asarray(e['sample_time_s']);ids=np.searchsorted(z['time_s'],time).clip(1,len(z['time_s'])-1)
                ids-=abs(z['time_s'][ids-1]-time)<abs(z['time_s'][ids]-time)
                if np.max(abs(z['time_s'][ids]-time))>1e-5:raise ValueError('unaligned action '+name)
                positions=five_fk_to_output(joints_from_global(torch.tensor(z['rotation'][ids]),geometry).numpy()[:,DISPLAY],metadata['output_frame'],source_space='SMPL_FK')
                e['standard']=copy.deepcopy(e['five'])
                e['five']=np.repeat(positions[:,None],3,axis=1).tolist()
                e['valid']=(np.asarray(e['valid'])&z['valid'][ids]).tolist()
                bends=[]
                for a,b,c in LIMBS.values():
                    u,v=positions[:,b]-positions[:,a],positions[:,c]-positions[:,b]
                    bends.append(np.rad2deg(np.arctan2(np.linalg.norm(np.cross(u,v),axis=1),(u*v).sum(1))))
                e['bends']=np.stack(bends,1).tolist()
    data['panels'][0].update(title='五节点 · 本轮联合求解候选',caption=('整套 C2；共享参考加速度相关性修正；H 参数冻结' if geometry.get('acceleration_observation_model') else '整套 C2 航向修正；H 参数冻结'))
    data['panels'][1].update(title='五节点 · 原展示结果',caption='保留修改前输出，便于检查改善与退步')
    data['status_text']='完整 C2/H 诊断回放；五节点尚未验收。四窗共享时间和相机，十节点仅用于对照。'
    data['model_text']='整套实录 C2 联合求解；H 冻结安装、尺寸与校准参数。统一航向仅用于展示整个人体，无肢段修正、缩放或时间拟合。'
    target=out/'C2_H_REVIEW.html'
    if target.exists():raise ValueError('preserve existing review')
    target.write_text(prefix+'const D='+json.dumps(data,separators=(',',':'))+payload[end:].replace('骨盆原点重合，保留骨长和身体倾斜差异','各自模型根点重合（定义不同），保留骨长和倾斜差异'))
    (out/'REVIEW_BINDINGS.json').write_text(json.dumps(dict(compute=bindings,template_sha256=sha(template),output_sha256=sha(target),reference=evaluation['reference'],tracking_accepted=False),indent=2))
    print(target)


if __name__=='__main__':main()
