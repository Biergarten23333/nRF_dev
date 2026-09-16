#!/usr/bin/env python3
"""Post-inference regression comparison; reference poses never enter inference."""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL
from biospur_fusion.c2_imucoco.workflow import INPUT_RUN
from biospur_fusion.c2_sparse_nodes.inputs import sha
from biospur_fusion.c2_sparse_nodes.evaluation import CAL_REFERENCE, H_REFERENCE, PAIRS, LINES, baseline_on_grid
from biospur_fusion.c2_sparse_nodes.viewer import write_viewer
from biospur_fusion.c2_coupled_progressive.contracts import load_effective_config
from biospur_fusion.c2_coupled_progressive.renderer import display_models


REFERENCE_TO_WORLD = Rotation.from_rotvec([0., 0., -np.pi/2]).as_matrix()


def reference_fk(rotations, config):
    """Vectorized authoritative C2 proxy geometry before its display reflection."""
    geometry = config['proxy_geometry']
    model = display_models(config)[1]
    points = np.zeros((len(rotations['pelvis']), 13, 3))
    for k, (parent, child) in enumerate(PAIRS):
        arm = k < 2
        side = -1 if k % 2 == 0 else 1
        span = geometry['acromion_proxy_span_m']['nominal'] if arm else model.hip_span_m
        base = rotations['torso' if arm else 'pelvis'] @ np.array([side*span/2, 0., model.torso_height_m if arm else 0.])
        middle = base - geometry[parent+'_m']['nominal']*rotations[parent][:, :, 2]
        tip = middle - geometry[child+'_m']['nominal']*rotations[child][:, :, 2]
        points[:, 1+3*k:4+3*k] = np.stack((base, middle, tip), axis=1)
    return points @ REFERENCE_TO_WORLD.T


def angle_between(a, b):
    dot = np.sum(a*b, axis=-1)/(np.linalg.norm(a, axis=-1)*np.linalg.norm(b, axis=-1))
    return np.degrees(np.arccos(np.clip(dot, -1., 1.)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    record = json.loads((out/'C2_H_REPLAY.json').read_text())
    if record['output_sha256'] != sha(out/'C2_H_REPLAY.npz'):
        raise ValueError('inference artifact hash mismatch')
    if (out/'REFERENCE_COMPARISON.json').exists():
        raise ValueError('comparison already exists; preserve earlier evidence')
    cal_contracts = json.loads((INPUT_RUN/'CALIBRATION_INPUT_AUDIT.json').read_text())['contracts']
    hold_contracts = json.loads((INPUT_RUN/'HOLDOUT_INPUT_AUDIT.json').read_text())['contracts']
    config = load_effective_config()
    episodes, metrics = {}, {}
    with np.load(out/'C2_H_REPLAY.npz') as data, np.load(CAL_REFERENCE) as calref, np.load(H_REFERENCE) as href:
        time = data['time_s']
        # Only a single fixed yaw gauge from C2 initial still, never per-frame
        # or H-derived alignment. Intrinsic bend metrics do not use this yaw.
        contract = cal_contracts['00_initial_still']
        indices = np.flatnonzero((time >= contract['lo']) & (time <= contract['hi']))[::3]
        ref, valid = baseline_on_grid(calref, '00', time[indices], contract, False)
        ref_left = (REFERENCE_TO_WORLD @ ref['pelvis']) @ np.array([-1., 0., 0.])
        our_root = WORLD_TO_SMPL.T @ data['retained_input_orientation'][indices, 0] @ WORLD_TO_SMPL
        our_left = our_root @ np.array([0., 1., 0.])
        a = np.mean(ref_left[valid], axis=0)
        b = np.mean(our_left[valid], axis=0)
        yaw = np.arctan2(b[1], b[0])-np.arctan2(a[1], a[0])
        alignment = Rotation.from_rotvec([0., 0., yaw]).as_matrix()
        for i, (name, contract) in enumerate({**cal_contracts, **hold_contracts}.items()):
            hold = name.startswith('H')
            ids = np.flatnonzero((time >= contract['lo']) & (time <= contract['hi']))[::3]
            times = time[ids]
            ref, valid = baseline_on_grid(href if hold else calref, name if hold else f'{i:02d}', times, contract, hold)
            valid &= data['input_valid'][ids]
            ten = reference_fk(ref, config) @ alignment.T
            measured = data['measured_joints_m'][ids]
            standard = data['standard_joints_m'][ids]
            bends = np.column_stack([angle_between(ref[p][:, :, 2], ref[c][:, :, 2]) for p, c in PAIRS])
            our_bends = data['bend_deg'][ids]
            errors = abs(our_bends-bends)
            # Check disagreement with retained IMU directions separately from
            # missing-node inference: a learned pose need not obey them exactly.
            sensor = data['retained_input_orientation'][ids]
            long_axes = [np.array([1.,0.,0.]), np.array([-1.,0.,0.]), np.array([0.,-1.,0.]), np.array([0.,-1.,0.])]
            residual = []
            for k, axis in enumerate(long_axes):
                observed = (sensor[:, k+1] @ axis) @ WORLD_TO_SMPL
                predicted = measured[:, 3+3*k]-measured[:, 2+3*k]
                residual.append(angle_between(observed, predicted))
            residual = np.stack(residual, axis=-1)
            metrics[name] = dict(frames=len(ids), compared_frames=int(valid.sum()),
                bend_mae_deg=errors[valid].mean(0).tolist() if valid.any() else None,
                bend_p95_deg=np.quantile(errors[valid], .95, axis=0).tolist() if valid.any() else None,
                retained_direction_mae_deg=residual[valid].mean(0).tolist() if valid.any() else None)
            repeat = lambda points: np.repeat(points[:, None], 3, axis=1).round(4).tolist()
            episodes[name] = dict(t=(times-contract['lo']).round(4).tolist(), sample_time_s=times.round(6).tolist(),
                five=repeat(measured), standard=repeat(standard), ten=repeat(ten), valid=valid.tolist(),
                bends=our_bends.round(1).tolist(), reference_bends=bends.round(1).tolist())
    comparison = dict(status='REGRESSION_DIAGNOSTIC_NOT_ACCEPTED', metrics=metrics,
        joint_order=['left_elbow','right_elbow','left_knee','right_knee'],
        reference_is_ground_truth=False, time_shift_fitted=False,
        reference_display='authoritative C2 internal quaternion FK; old improper display reflection excluded',
        reference_fixed_yaw_from_initial_still_deg=float(np.degrees(yaw)),
        reference_sha256={str(p):sha(p) for p in (CAL_REFERENCE,H_REFERENCE)},
        inference_output_sha256=record['output_sha256'], evaluation_source_sha256=sha(Path(__file__)))
    (out/'REFERENCE_COMPARISON.json').write_text(json.dumps(comparison,indent=2,allow_nan=False)+'\n')
    write_viewer(out/'REPLAY_REVIEW.html', dict(episodes=episodes, lines=LINES, geometry=[[0,0]]*3,
        panels=[dict(title='五节点 · 实测尺寸适配',caption='IMUCoCo 原权重 + 功能校准 + 尺寸拟合 FK',source='five',color='#39c5ff'),
                dict(title='五节点 · 标准 SMPL 身体',caption='同一份预测旋转；显示身体尺寸适配的影响',source='standard',color='#c39aff'),
                dict(title='十节点 · C2 参考',caption='原始旋转 FK；固定初始朝向对齐',source='ten',color='#ffb270')],
        status_text='真实 IMUCoCo 五节点网络输出，尚未通过动作精度验收。三窗共用原始采样时间和相机；没有拟合时间偏移。',
        geometry_text='左、中两窗共用网络预测，区别是身体尺寸。实测表面长度通过 SMPL 体型拟合进入 FK，尚非精确关节中心标定。',
        model_text='右窗按 C2 原始旋转重建，未套用旧页面的镜像；仅以初始静止设置一个固定朝向。网络未使用十节点或 H 标签拟合。前臂方向及肘角仍需检查；H 系列是已看过的回归数据。'))
    print(json.dumps({k:v for k,v in metrics.items() if k.startswith('H')},indent=2))


if __name__ == '__main__':
    main()
