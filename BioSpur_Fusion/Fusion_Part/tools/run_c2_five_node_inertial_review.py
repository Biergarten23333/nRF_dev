#!/usr/bin/env python3
"""Compare frozen inferred trajectories only after estimation has finished."""
import argparse
import json
from pathlib import Path
import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha
from biospur_fusion.c2_sparse_nodes.evaluation import report,LINES
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer

OLD=ROOT/'logs/c2_five_node_pure_imu_v3_20260906_103000'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args();out=args.output.resolve()
    comparison=report(out)
    c=json.loads((out/'CALIBRATION_FROZEN.json').read_text())
    contracts={}
    for name in ('CALIBRATION_INPUT_AUDIT.json','HOLDOUT_INPUT_AUDIT.json'):
        contracts.update(json.loads((out/name).read_text())['contracts'])
    viewer={};regression={}
    for name in comparison['comparison']:
        with np.load(out/(name+'_REPLAY.npz')) as a,np.load(out/(name+'_REFERENCE_COMPARISON.npz')) as b,np.load(OLD/(name+'_REPLAY.npz')) as old:
            t=a['time_s'];oldtime=old['time_s'];oldj=old['joints_m'].reshape(len(oldtime),-1)
            previous=np.column_stack([np.interp(t,oldtime,oldj[:,j]) for j in range(oldj.shape[1])]).reshape((-1,3,13,3))
            oldb=np.column_stack([np.interp(t,oldtime,old['bend_deg'][:,j]) for j in range(4)])
            valid=b['source_comparison_valid']&(t>=oldtime[0])&(t<=oldtime[-1])
            if not valid.any():raise ValueError('no comparable frames: '+name)
            ref=b['reference_joints_m'][:,1]
            newerr=np.linalg.norm(a['joints_m'][:,1]-ref,axis=2)
            olderr=np.linalg.norm(previous[:,1]-ref,axis=2)
            regression[name]=dict(frames=int(valid.sum()),
                nonconverged_frames_in_comparison=int(np.sum(valid&~a['optimizer_success'])),
                new_elbow_position_mae_mm=(np.mean(newerr[valid][:,[2,5]],axis=0)*1000).tolist(),
                old_elbow_position_mae_mm=(np.mean(olderr[valid][:,[2,5]],axis=0)*1000).tolist(),
                new_bend_mae_deg=np.mean(abs(a['bend_deg'][valid]-b['reference_bend_deg'][valid]),axis=0).tolist(),
                old_bend_mae_deg=np.mean(abs(oldb[valid]-b['reference_bend_deg'][valid]),axis=0).tolist(),
                reference_is_ground_truth=False)
            viewer[name]=dict(t=np.round(t-contracts[name]['lo'],3).tolist(),sample_time_s=np.round(t,6).tolist(),
                five=np.round(a['joints_m'],4).tolist(),ten=np.round(b['reference_joints_m'],4).tolist(),
                previous=np.round(previous,4).tolist(),valid=(valid&a['optimizer_success']).tolist(),bends=np.round(a['bend_deg'],1).tolist(),
                reference_bends=np.round(b['reference_bend_deg'],1).tolist())
    payload=dict(episodes=viewer,lines=LINES,geometry=list(zip(c['torso_display_models_m'],c['hip_display_half_width_models_m'])),
        panels=[dict(title='修正后 · 五节点',source='five',color='#39c5ff',caption='功能轴校准＋实测骨长＋加速度约束'),
                dict(title='原结果 · 五节点',source='previous',color='#c59aff',caption='原姿态先验结果；按相同采样时间插值'),
                dict(title='十节点 · C2 参考',source='ten',color='#ffb270',caption='逐节点恢复原始采样时刻；仅用于对比')],
        status_text='已修正校准分段和初始前臂方向，加入跨帧加速度约束。以下是全量回归结果，仍有动作误差，不能视为五节点方案已通过。',
        geometry_text='三栏使用同一组骨长、躯干高度 0.425 m 和髋半宽 0.14 m，以及同一时间和相机。肢段长度来自实测表面距离；躯干和髋宽是未实测假设。根位置固定。',
        model_text='新结果由五个 IMU 连续计算，动作之间不重设姿态；未使用 UWB 距离或位置。前臂功能轴由屈肘和旋前/旋后分别校准，骨长参与加速度残差。十节点仅作回归参考，不是真值。离线窗口使用未来帧；不是实时验证。')
    write_viewer(out/'REPLAY_REVIEW.html',payload)
    (out/'BEFORE_AFTER_REGRESSION.json').write_text(json.dumps(dict(episodes=regression,old_source=str(OLD),
        old_calibration_sha256=sha(OLD/'CALIBRATION_FROZEN.json')),indent=2)+'\n')
    print(json.dumps(regression,indent=2))


if __name__=='__main__':main()
