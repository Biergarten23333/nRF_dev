#!/usr/bin/env python3
"""C2 evaluation-only viewer of the frozen physical calibration candidate."""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import numpy as np
import torch
from scipy.spatial.transform import Rotation
from build_imucoco_review import reference_fk, REFERENCE_TO_WORLD, angle_between
from c2_five_geometry_review import (
    compare_display_geometry, freeze_five_output_coordinates, five_fk_to_output,
)
from biospur_fusion.c2_coupled_progressive import output_coordinates
from biospur_fusion.c2_sparse_nodes import viewer
from biospur_fusion.c2_five_calibration.workflow import action_data
from biospur_fusion.c2_five_calibration.geometry import joints_from_global, DISPLAY
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from biospur_fusion.c2_imucoco.body import bend_angles
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN,write
from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_sparse_nodes.evaluation import PAIRS, LINES
from biospur_fusion.c2_sparse_nodes.articulated_reference import (
    CAL_REFERENCE, H_REFERENCE, baseline_on_grid, verify_reference, action_key, output_matrix,
)
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer
from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config


def main(out, *, with_h=False, previous_source=None, shared_frozen=False, render=True, continuous_calibration=False):
    from c2_five_previous_output import PreviousOutput
    previous = PreviousOutput(previous_source, with_h=with_h) if previous_source else None
    comparison_name = 'C2_H_REFERENCE_COMPARISON.json' if with_h else 'REFERENCE_COMPARISON.json'
    page_name = 'C2_H_REPLAY_REVIEW.html' if with_h else 'REPLAY_REVIEW.html'
    if (out/comparison_name).exists(): raise ValueError('review already exists')
    if continuous_calibration and shared_frozen:
        raise ValueError('choose in-sample calibration or label-free frozen replay')
    if shared_frozen:
        from c2_five_frozen_review import load_shared_c2, verified_action_contracts
        contracts=verified_action_contracts(out)
    else:
        contracts=json.loads((INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
    if continuous_calibration:
        from c2_five_continuous_review import load_continuous_calibration
        contracts,actions,outputs=load_continuous_calibration(out)
        geometric_hinge=True
    elif shared_frozen:
        actions,outputs,geometric_hinge=load_shared_c2(out,contracts)
    else:
        validation=json.loads((out/'C2_VALIDATION.json').read_text())
        geometric_hinge=bool(validation['actions']) and all(
            'joint_angle_semantics' in row for row in validation['actions'].values())
    geometry=json.loads((out/'GEOMETRY.json').read_text())
    frontend=json.loads((out/'FRONTEND.json').read_text())
    directed_arm=any(row.get('calibration_role')=='conditional_arm_plane_diagnostic'
                     for rows in frontend.get('heading_factors',{}).values() for row in rows)
    config=load_effective_config()
    if not shared_frozen and not continuous_calibration:
        actions=action_data(out)
        outputs={}
        for stage in ('PHYSICAL_CALIBRATION','C2_VALIDATION'):
            record=json.loads((out/(stage+'.json')).read_text())
            if record['output_sha256']!=sha(out/(stage+'.npz')): raise ValueError('output changed')
            with np.load(out/(stage+'.npz')) as f: outputs.update({k:f[k] for k in f.files})
    if with_h:
        h_record=json.loads((out/'H_REPLAY.json').read_text())
        if h_record['output_sha256']!=sha(out/'H_REPLAY.npz'):
            raise ValueError('H output changed')
        for name,expected in h_record['frozen_inputs'].items():
            if sha(out/name)!=expected: raise ValueError('H frozen input changed: '+name)
        h_contracts=(verified_action_contracts(out,holdout=True) if shared_frozen else
                     json.loads((INPUT_RUN/'HOLDOUT_INPUT_AUDIT.json').read_text())['contracts'])
        with np.load(out/'H_REPLAY.npz') as f:
            h_data={k:f[k] for k in f.files}
        for name,window in h_contracts.items():
            ids=np.flatnonzero((h_data['time_s']>=window['lo'])&(h_data['time_s']<=window['hi']))
            if not len(ids): raise ValueError('H action has no replay frames: '+name)
            actions[name]={k:v[ids] for k,v in h_data.items() if k!='rotation'}
            for key in ('rotation','time_s','valid'):
                outputs[name+'/'+key]=h_data[key][ids]
        contracts={**contracts,**h_contracts}
    reference_provenance=verify_reference()
    # Freeze from ALL own finite initial pelvis frames before reference sampling.
    five_frame=freeze_five_output_coordinates(actions['00_initial_still']['observed'][:,0],WORLD_TO_SMPL)
    five_matrix=np.asarray(five_frame['matrix_world_output_from_internal'])
    episodes,metrics={},{}
    with ExitStack() as stack:
        reference=stack.enter_context(np.load(CAL_REFERENCE))
        hold_reference=stack.enter_context(np.load(H_REFERENCE)) if with_h else None
        output_frame=output_matrix(reference)
        if with_h and not np.allclose(output_matrix(hold_reference),output_frame,atol=1e-12):
            raise ValueError('C2 and H reference output-coordinate conventions differ')
        zero=actions['00_initial_still']; t=zero['time_s'];contract=contracts['00_initial_still']
        ref,valid=baseline_on_grid(reference,'00',t,contract,False)
        ref_left=(REFERENCE_TO_WORLD@output_frame@ref['pelvis'])@np.array([-1.,0.,0.])
        ours=(five_matrix@WORLD_TO_SMPL.T@zero['observed'][:,0])@np.array([1.,0.,0.])
        a,b=ref_left[valid].mean(0),ours[valid].mean(0)
        if not np.isfinite([a,b]).all() or min(np.linalg.norm(a[:2]),np.linalg.norm(b[:2]))<1e-9:
            raise ValueError('initial reflected left-axis yaw alignment is unobservable')
        yaw=np.arctan2(b[1],b[0])-np.arctan2(a[1],a[0])
        align=Rotation.from_rotvec([0.,0.,yaw]).as_matrix()
        for index,(name,contract) in enumerate(contracts.items()):
            q=actions[name];t=q['time_s'];r=outputs[name+'/rotation']
            if not np.array_equal(t,outputs[name+'/time_s']):raise ValueError('physical timeline changed')
            fixed=five_fk_to_output(joints_from_global(torch.tensor(r),geometry).numpy()[:,DISPLAY],
                                    five_frame,source_space='SMPL_FK')
            prior=five_fk_to_output(joints_from_global(torch.tensor(q['prior']),geometry).numpy()[:,DISPLAY],
                                    five_frame,source_space='SMPL_FK')
            old, old_valid = previous.on_grid(name, t, contract) if previous else (prior, q['valid'])
            hold=name.startswith('H')
            ref,valid=baseline_on_grid(hold_reference if hold else reference,
                action_key(name),t,contract,hold)
            valid &= q['valid'] & old_valid
            if not valid.any(): raise ValueError('no valid comparison samples: '+name)
            # reference_fk returns a proper world rotation of internal FK.
            # Restore internal Cartesian points, apply the frozen output
            # convention once, then the common display-world yaw alignment.
            ten=(reference_fk(ref,config)@REFERENCE_TO_WORLD@output_frame.T
                 @REFERENCE_TO_WORLD.T@align.T)
            target=np.column_stack([angle_between(ref[p][:,:,2],ref[c][:,:,2]) for p,c in PAIRS])
            bend=bend_angles(fixed);before=bend_angles(prior)
            metrics[name]=dict(compared_frames=int(valid.sum()),
                physical_bend_mae_deg=abs(bend-target)[valid].mean(0).tolist(),
                physical_bend_p95_deg=np.quantile(abs(bend-target)[valid],.95,axis=0).tolist(),
                prior_bend_mae_deg=abs(before-target)[valid].mean(0).tolist(),
                physical_display_geometry=compare_display_geometry(fixed,ten,valid))
            if previous:
                metrics[name]['previous_display_geometry'] = compare_display_geometry(old,ten,valid)
                metrics[name]['old_new_joint_change_rms_m'] = float(np.sqrt(np.mean((fixed[valid]-old[valid])**2)))
            if render:
                repeat=lambda p:np.repeat(p[:,None],3,axis=1).round(4).tolist()
                episodes[name]=dict(t=(t-contract['lo']).round(4).tolist(),sample_time_s=t.round(6).tolist(),
                    five=repeat(fixed),standard=repeat(old),ten=repeat(ten),valid=valid.tolist(),
                    bends=bend.round(1).tolist(),reference_bends=target.round(1).tolist())
    if shared_frozen:
        checked=verified_action_contracts(out)
        if with_h:checked={**checked,**verified_action_contracts(out,holdout=True)}
        if checked!=contracts:raise ValueError('evaluation windows changed during comparison')
    report=dict(metrics=metrics,reference_is_ground_truth=False,html_generated=render,
        previous_five_output=previous.provenance() if previous else None,
        reference_provenance=reference_provenance,
        reference_sha256=sha(CAL_REFERENCE),fixed_yaw_from_initial_still_deg=float(np.degrees(yaw)),
        time_shift_fitted=False,H_reference_opened=with_h,reference_used_in_calibration=False,shared_frozen_replay=shared_frozen,
        continuous_in_sample_calibration=continuous_calibration,
        evaluation_source_sha256=sha(Path(__file__)),
        evaluation_window_audit_sha256={name:sha(INPUT_RUN/name) for name in
            (('CALIBRATION_INPUT_AUDIT.json','HOLDOUT_INPUT_AUDIT.json') if with_h else
             ('CALIBRATION_INPUT_AUDIT.json',))},
        frozen_review_helper_sha256=sha(Path(__file__).with_name('c2_five_frozen_review.py')) if shared_frozen else None,
        evaluation_geometry_helper_sha256=sha(Path(__file__).with_name('c2_five_geometry_review.py')),
        output_coordinate_owner_sha256=sha(Path(output_coordinates.__file__)),
        viewer_template_sha256=sha(Path(viewer.__file__).with_name('viewer_template.html')),
        camera_convention='C2_FROZEN',
        camera_projection_source='tools/build_c2_avatar_interactive.py::project',
        five_output_coordinate_convention=five_frame,
        five_initial_pelvis_source=dict(file='C2_FROZEN_REPLAY.npz' if shared_frozen else 'C2_PRIOR.npz',
                                       sha256=sha(out/('C2_FROZEN_REPLAY.npz' if shared_frozen else 'C2_PRIOR.npz')),
                                       action='00_initial_still',field='observed[:,0]'),
        output_comparison=dict(reference_reflection_internal=output_frame.tolist(),
            reference_to_world=REFERENCE_TO_WORLD.tolist(),
            reflected_left_yaw_alignment=align.tolist(),yaw_alignment_determinant=float(np.linalg.det(align)),
            five_point_transform_determinant=float(np.linalg.det(five_matrix@WORLD_TO_SMPL.T)),
            reference_point_transform_determinant=float(np.linalg.det(align@REFERENCE_TO_WORLD@output_frame)),
            alignment_scope='ONE_INITIAL_STILL_YAW; REFLECTED_LEFT_TO_REFLECTED_LEFT',
            five_and_prior_share_reflection=True,per_action_transform_selection=False,
            output_only_not_algorithm_repair=True),
        angle_reference_convention='corrected native200 analytic hinge IK v4; five-node anatomical frame; engineering comparison, not external truth')
    if with_h:
        limits=json.loads((out/'TASK_CONTRACT.json').read_text())['regression_gates']
        failures=[]
        for name,row in metrics.items():
            if not name.startswith('H'): continue
            for key,limit in [('physical_bend_mae_deg',limits['H_each_joint_MAE_deg_max']),
                              ('physical_bend_p95_deg',limits['H_each_joint_P95_deg_max'])]:
                for joint,value in enumerate(row[key]):
                    if value>limit: failures.append(dict(action=name,joint=joint,metric=key,value=value,limit=limit))
        report.update(status='DIAGNOSTIC_NOT_ACCEPTED',H_regression_failures=failures,
            product_accepted=False,H_reference_sha256=sha(H_REFERENCE),
            H_output_sha256=h_record['output_sha256'],H_used_for_calibration=False)
    write(out/comparison_name,report)
    if not render:
        return
    if continuous_calibration:
        status='完整连续 C2 校准拟合（含动作间运动），不是无标签 C2 复现；尚未通过验收。'
        if with_h:status+='H 使用冻结参数，未参与校准。'
    elif shared_frozen:
        status='五节点冻结参数复现；校准尚未完成验收。'
        if with_h:status+='H 动作误差检查未通过。' if failures else 'H 动作误差检查通过，仍待整体评估。'
    elif with_h:
        assessment=json.loads((out/'ASSESSMENT.json').read_text())
        status=('C2 动作自检未通过。' if not assessment['C2_passed'] else 'C2 动作自检通过。')
        status+='H 使用冻结参数复现；'+('动作误差检查未通过。' if failures else '待完整验收。')
    else:
        status='全部 C2 参与校准；当前仅为校准内重放，尚未通过完整验收。'
    write_viewer(out/page_name,dict(episodes=episodes,lines=LINES,geometry=[[0,0]]*3,
        camera_convention='C2_FROZEN',
        animation_focus=bool(previous),
        display_axis_matrix=five_matrix.tolist(),
        panels=[dict(title='五节点 · 本次修改' if previous else '五节点 · 几何铰链候选' if geometric_hinge else '五节点 · 物理约束候选',
                    caption='完整连续录制联合拟合；C2 校准内结果，H 冻结复现' if continuous_calibration else ('肩肘动作约束进入校准；冻结参数后连续复现' if shared_frozen else '重做前臂方向校准 + 全动作重新求解' if directed_arm else '快慢动作约束 + 实测骨长；实际重新求解') if previous else '几何屈伸 + 五路姿态硬约束 + 实测骨长' if geometric_hinge else '实测尺寸进入求解；保留五段的六轴姿态估计',
                    source='five',color='#39c5ff'),
                dict(title='五节点 · 修改前' if previous else '五节点 · 网络初值',
                    caption='本次修正前的冻结五节点输出' if previous else '同一校准和身体尺寸，尚未施加物理优化',source='standard',color='#c39aff'),
                dict(title='十节点 · C2 修正版 v4',caption='2026-09-04 · 功能轴 + 解析 IK；仅用于对照',source='ten',color='#ffb270')],
        status_text=status+'三窗共用采样时刻和相机；未拟合时间偏移。',
        geometry_text='表面实测长度作为关节长度近似进入预测方程，保留 20 mm 映射不确定性；躯干采用 SMPL 均值。',
        model_text=('全部 19 个实际录制的 C2 动作及动作间运动参与连续校准，01 未采集；C2 使用已登记动作约束，H 使用冻结参数及无动作标签的关节求解。' if continuous_calibration else '全部 19 个实际录制的 C2 动作参与共享安装参数拟合，01 未采集；C2 复现是校准内自检。动作复现使用同一套无动作标签的关节求解。')+'肘含屈伸和轴向旋转，膝采用铰链近似。'+
            ('H 仅使用冻结参数，没有参与校准。' if with_h else 'H 系列尚未运行。')+
            ('本页 C2 是连续校准内拟合结果，并非独立无标签复现。' if continuous_calibration else '')+
            '十节点采用 2026-09-04 native200 解析铰链 IK 修正版，保留整个人体的共同时间轴；页面不修姿态。旧 2026-08-31 参考已替换。十节点仍是工程参考，不是外部动作真值。'))
    if with_h: print(json.dumps({k:v for k,v in metrics.items() if k.startswith('H')},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--with-h',action='store_true')
    parser.add_argument('--previous-source',type=Path)
    parser.add_argument('--continuous-calibration',action='store_true')
    args=parser.parse_args();main(args.out.resolve(),with_h=args.with_h,previous_source=args.previous_source,
                                continuous_calibration=args.continuous_calibration)
