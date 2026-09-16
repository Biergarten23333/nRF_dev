#!/usr/bin/env python3
"""Post-freeze comparison only; reuse the established time/FK/viewer owners."""
import argparse
import json
from pathlib import Path

import numpy as np
import torch
from scipy.spatial.transform import Rotation

from c2_progressive_candidate_io import resolve
from build_imucoco_review import reference_fk,REFERENCE_TO_WORLD,angle_between
from c2_five_geometry_review import freeze_five_output_coordinates,five_fk_to_output,compare_display_geometry,C2_TO_SMPL_BODY
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer
from biospur_fusion.c2_sparse_nodes.evaluation import LINES,PAIRS
from biospur_fusion.c2_sparse_nodes.articulated_reference import (
    CAL_REFERENCE,H_REFERENCE,verify_reference,baseline_on_grid,action_key,output_matrix)
from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config
from biospur_fusion.c2_five_calibration.geometry import DISPLAY,joints_from_global
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from biospur_fusion.c2_imucoco.body import bend_angles


def retained_direction_error(observed, geometry, reference):
    """Five calibrated distal directions versus ten v4 post-IK, not raw IMU error."""
    axes=np.asarray(geometry['rest_offsets_m'])[[20,21,7,8]]
    axes=axes/np.linalg.norm(axes,axis=-1,keepdims=True)
    measured=observed[:,1:]@axes[...,None]
    local=(observed[:,0].transpose(0,2,1)[:,None]@measured)[...,0]@C2_TO_SMPL_BODY
    reference_local=np.stack([-(reference['pelvis'].transpose(0,2,1)@reference[c])[:,:,2]
                              for _,c in PAIRS],axis=1)
    return angle_between(local,reference_local)


def main(candidate,holdout,out,kind="progressive"):
    if kind=="shared":
        from c2_shared_prefix_candidate_io import resolve
    else:
        from c2_progressive_candidate_io import resolve
    candidate=candidate.resolve();holdout=holdout.resolve();out=out.resolve()
    out.mkdir(parents=True,exist_ok=False)
    _,geometry,_,bindings=resolve(candidate)
    record=json.loads((holdout/'RESULT.json').read_text())
    if (not record['completed'] or record['bindings']!=bindings
            or sha(holdout/'H_REPLAY.npz')!=record['output_sha256']
            or sha(holdout/'INPUT_AUDIT.json')!=record['input_audit_sha256']):
        raise ValueError('verified frozen H replay required before comparison')
    audit=json.loads((holdout/'INPUT_AUDIT.json').read_text())
    contracts={**audit['calibration']['contracts'],**record['contracts']}
    final=candidate/'19_heel_to_butt_right'
    prior_path=candidate/'ACCEPTED_PRIOR.npz' if kind=='shared' else final/'C2_PREFIX_PRIOR.npz'
    physical_path=candidate/'CHECKPOINT.npz' if kind=='shared' else final/'PHYSICAL_PREFIX.npz'
    with np.load(prior_path,allow_pickle=False) as z:
        c2={k:z[k][::3] for k in z.files}
    with np.load(physical_path,allow_pickle=False) as z:
        if not np.array_equal(c2['time_s'],z['time_s']):raise ValueError('C2 physical time changed')
        c2['rotation']=z['rotation']
    with np.load(holdout/'H_REPLAY.npz',allow_pickle=False) as z:h={k:z[k] for k in z.files}
    actions={}
    for name,window in contracts.items():
        q=h if name.startswith('H') else c2
        index=np.flatnonzero((q['time_s']>=window['lo'])&(q['time_s']<window['hi']))
        if len(index)<2:raise ValueError('missing action support: '+name)
        actions[name]={k:v[index] for k,v in q.items()}
    own_frame=freeze_five_output_coordinates(actions['00_initial_still']['observed'][:,0],WORLD_TO_SMPL)
    own_matrix=np.asarray(own_frame['matrix_world_output_from_internal'])
    # Reference files are first opened after candidate and holdout checks.
    reference_binding=verify_reference();config=load_effective_config()
    metrics={};episodes={}
    with np.load(CAL_REFERENCE,allow_pickle=False) as ref_c,np.load(H_REFERENCE,allow_pickle=False) as ref_h:
        matrix=output_matrix(ref_c)
        if not np.allclose(matrix,output_matrix(ref_h),atol=1e-12):raise ValueError('reference C2/H output frames differ')
        zero=actions['00_initial_still']
        reference,valid=baseline_on_grid(ref_c,'00',zero['time_s'],contracts['00_initial_still'],False)
        left_ref=(REFERENCE_TO_WORLD@matrix@reference['pelvis'])@np.array([-1.,0.,0.])
        left_own=(own_matrix@WORLD_TO_SMPL.T@zero['observed'][:,0])@np.array([1.,0.,0.])
        if not valid.any():raise ValueError('no initial reference overlap')
        a,b=left_ref[valid].mean(0),left_own[valid].mean(0)
        if min(np.linalg.norm(a[:2]),np.linalg.norm(b[:2]))<1e-9:raise ValueError('undefined comparison yaw')
        yaw=np.arctan2(b[1],b[0])-np.arctan2(a[1],a[0])
        align=Rotation.from_rotvec([0.,0.,yaw]).as_matrix()
        for name,q in actions.items():
            t=q['time_s'];hold=name.startswith('H')
            ref,valid=baseline_on_grid(ref_h if hold else ref_c,action_key(name),t,contracts[name],hold)
            valid &= q['valid']
            if not valid.any():raise ValueError('no valid comparison: '+name)
            fixed=five_fk_to_output(joints_from_global(torch.as_tensor(q['rotation']),geometry).numpy()[:,DISPLAY],own_frame,source_space='SMPL_FK')
            prior=five_fk_to_output(joints_from_global(torch.as_tensor(q['prior']),geometry).numpy()[:,DISPLAY],own_frame,source_space='SMPL_FK')
            ten=reference_fk(ref,config)@REFERENCE_TO_WORLD@matrix.T@REFERENCE_TO_WORLD.T@align.T
            target=np.column_stack([angle_between(ref[p][:,:,2],ref[c][:,:,2]) for p,c in PAIRS])
            bend=bend_angles(fixed)
            # Ten v4 has already reconstructed distal directions using its
            # measured proximal joints. This is a post-IK reconstruction
            # discrepancy, not an audit of raw sensor or mounting correctness.
            retained_error=retained_direction_error(q['observed'],geometry,ref)
            metrics[name]=dict(frames=int(valid.sum()),bend_mae_deg=abs(bend-target)[valid].mean(0).tolist(),
                bend_p95_deg=np.quantile(abs(bend-target)[valid],.95,axis=0).tolist(),
                retained_direction_relative_pelvis_mean_deg=retained_error[valid].mean(0).tolist(),
                retained_direction_relative_pelvis_p95_deg=np.quantile(retained_error[valid],.95,axis=0).tolist(),
                geometry=compare_display_geometry(fixed,ten,valid))
            repeat=lambda x:np.repeat(x[:,None],3,axis=1).round(4).tolist()
            episodes[name]=dict(t=(t-contracts[name]['lo']).round(4).tolist(),sample_time_s=t.round(6).tolist(),
                five=repeat(fixed),standard=repeat(prior),ten=repeat(ten),valid=valid.tolist(),
                bends=bend.round(1).tolist(),reference_bends=target.round(1).tolist())
    gate_path=ROOT/'logs/c2_five_overnight_progressive_20260913_180146/REVIEW_GATES.json'
    gates=json.loads(gate_path.read_text());failures=[]
    for name,m in metrics.items():
        if name.startswith('H'):
            for field,limit in (('bend_mae_deg',gates['H_each_joint_MAE_deg_max']),('bend_p95_deg',gates['H_each_joint_P95_deg_max'])):
                failures.extend(dict(action=name,metric=field,joint=i,value=v,limit=limit) for i,v in enumerate(m[field]) if v>limit)
    body_policy=geometry.get('body_feasibility')
    body_audit=record['physical'].get('body_feasibility') if body_policy else None
    report=dict(metrics=metrics,failures=failures,engineering_H_angle_gates_passed=not failures,
        gates=gates,gates_sha256=sha(gate_path),reference=reference_binding,reference_is_ground_truth=False,
        reference_used_for_calibration=False,candidate_bindings=bindings,H_sha256=record['output_sha256'],
        retained_direction_comparison_scope='five calibrated distal observations versus ten v4 post-IK directions, each relative to own pelvis; not raw IMU error',
        fixed_comparison_yaw_deg=float(np.rad2deg(yaw)),time_shift_fitted=False,
        output_frame=own_frame,product_accepted=False,body_feasibility=body_audit)
    (out/'REVIEW.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    status=('H 动作误差检查未通过。本页为失败诊断回放，不能用于宣称五节点已成功。' if failures
            else 'H 角度检查通过，仍需检查完整姿态；未宣称产品通过。')
    if body_policy and (not body_audit or not body_audit.get('accepted')):
        status+=' 身体内核检查未通过。'
    write_viewer(out/'C2_H_REPLAY_REVIEW.html',dict(episodes=episodes,lines=LINES,geometry=[[0,0]]*3,
        camera_convention='C2_FROZEN',animation_focus=True,display_axis_matrix=own_matrix.tolist(),
        panels=[dict(title='五节点 · 身体约束候选' if body_policy else '五节点 · 渐进候选',
                     caption='C2 全量联合校准；身体约束参与求解；H 参数冻结' if body_policy else '实测尺寸 + 累计物理优化；H 参数冻结',source='five',color='#39c5ff'),
                dict(title='五节点 · 网络初值',caption='同一校准下的网络输出',source='standard',color='#c39aff'),
                dict(title='十节点 · 冻结 v4 参考',caption='已校验的纯 IMU 修正版；仅用于对照',source='ten',color='#ffb270')],
        status_text=status+' 三窗共用原始采样时刻和相机；H 未参与校准。',
        geometry_text=('躯干使用近似体积；内核排除不等于完整皮肤碰撞或肩关节活动域通过。' if body_policy else '')+'实测肢段尺寸进入物理方程；躯干及传感器位置仍含模型近似。',
        model_text='C2 为全量校准内结果；H 用冻结安装及传感器偏移参数，无动作标签求解。十节点仅作工程对照。'))
    if resolve(candidate)[3] != bindings or verify_reference() != reference_binding:
        raise ValueError('candidate or reference changed during review export')
    if sha(holdout/'H_REPLAY.npz') != record['output_sha256']:
        raise ValueError('holdout changed during review export')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--candidate',required=True,type=Path)
    p.add_argument('--holdout',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--candidate-kind',choices=['progressive','shared'],default='progressive')
    a=p.parse_args();torch.set_num_threads(1);main(a.candidate,a.holdout,a.out,a.candidate_kind)
