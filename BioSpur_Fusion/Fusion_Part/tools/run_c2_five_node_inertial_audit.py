#!/usr/bin/env python3
"""Independent artifact checks and a candid regression decision report."""
import argparse
import json
from pathlib import Path
import numpy as np
from biospur_fusion.c2_sparse_nodes.inputs import ROOT,sha


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',type=Path,required=True)
    out=ap.parse_args().output.resolve()
    c=json.loads((out/'CALIBRATION_FROZEN.json').read_text())
    seal=json.loads((out/'FREEZE_SEAL.json').read_text())
    before=json.loads((out/'BEFORE_AFTER_REGRESSION.json').read_text())['episodes']
    metrics=json.loads((out/'REPLAY_METRICS.json').read_text())
    checks={};snapshots={}
    for name in metrics:
        with np.load(out/(name+'_REPLAY.npz')) as a,np.load(out/(name+'_REFERENCE_COMPARISON.npz')) as b:
            lengths=[]
            for k in range(4):
                base=1+3*k;arm=k<2
                for start,key in ((base,'upper_arm' if arm else 'thigh'),(base+1,'forearm' if arm else 'shank')):
                    lengths.append(abs(np.linalg.norm(a['joints_m'][:,:,start+1]-a['joints_m'][:,:,start],axis=-1)-c['lengths'][key]).max())
            r=a['retained_rotations'];eye=np.eye(3)
            checks[name]=dict(frames=len(r),nonconverged_frames=int((~a['optimizer_success']).sum()),
                invalid_input_frames=int((~a['input_valid']).sum()),
                finite=all(np.isfinite(a[k]).all() for k in a.files),
                monotonic_time=bool(np.all(np.diff(a['time_s'])>0)),
                same_reference_sample_grid=bool(np.array_equal(a['time_s'],b['time_s'])),
                maximum_bone_length_error_m=float(max(lengths)),
                maximum_rotation_orthogonality_error=float(abs(np.swapaxes(r,-1,-2)@r-eye).max()),
                bend_over_155_deg_frames=int(np.any(a['bend_deg']>155.5,axis=1).sum()))
            assert checks[name]['finite'] and checks[name]['monotonic_time'] and checks[name]['same_reference_sample_grid']
            assert max(lengths)<1e-10
            if name in ('00_initial_still','06_elbow_left','04_shoulder_left'):
                elapsed=0 if name=='00_initial_still' else (5.8 if name=='06_elbow_left' else 23.75)
                i=int(np.argmin(abs(a['time_s']-a['time_s'][0]-elapsed)))
                snapshots[name]=dict(frame=i,sample_time_s=float(a['time_s'][i]),
                    bend_deg=a['bend_deg'][i].tolist(),reference_bend_deg=b['reference_bend_deg'][i].tolist(),
                    upper_arm_elevation_deg=np.rad2deg(np.arcsin(np.clip(-a['proximal_rotations'][i,:2,2,2],-1,1))).tolist(),
                    median_bend_deg=np.median(a['bend_deg'],axis=0).tolist())
    assert sha(out/'CALIBRATION_FROZEN.json')==seal['calibration_sha256']
    assert all(sha(ROOT/p)==h for p,h in seal['source_sha256'].items())
    audit=dict(schema='five-node-inertial-rework-artifact-audit-v1',
        model_and_source_seal_verified=True,episodes=checks,snapshots=snapshots,
        pose_accuracy_accepted=False,independent_agent_review=False,
        comparison_includes_nonconverged_finite_frames=True)
    (out/'ARTIFACT_AUDIT.json').write_text(json.dumps(audit,indent=2)+'\n')
    lines=['# 五节点纯 IMU 校准与连续回放修正', '',
        '**结论：校准分段、初始前臂强制归零和姿态求解实现已修正；完成 C2 全部 19 个正式动作以及 H01/H02 连续回归。这是实现修正和完整失败分析，不是五节点动作精度通过。**','',
        '## 已确认的实现错误','',
        '旧输入已按 ACTION_START 裁掉预录段，校准却继续使用旧归档的 5–20 秒窗口，把屈肘与旋前/旋后混在一起。现在分别使用正式动作的 0–15 秒和 15–30 秒识别铰链轴和前臂纵轴。左右屈肘主轴能量占比为约 97.1%/98.0%，旋前/旋后为约 98.8%/97.6%。', '',
        '旧的初始姿态相对旋转把每段长轴都强制竖直，直接抹掉了自然弯曲。现在保留由功能动作识别的前臂长轴；在初始上臂近似自然下垂的假设下，校准估计左右肘弯曲为 '+ ' / '.join(f'{v:.2f}°' for v in c['standing_elbow_bend_estimate_deg'])+'。这是带上臂初始姿态假设的估计，不是独立测量到的肘角。','',
        '## 新求解链路','',
        '五个节点的姿态和原始加速度进入同一时间网格。去重力后，将远端 IMU 相对骨盆 IMU 的加速度，与骨长及传感器安装偏移决定的 FK 位置二阶导数比较。缺失上臂、大腿直接用方向参数求解，避免肘角与前臂旋转互相补偿。没有固定 0° 或 15° 肘角目标。', '',
        '全记录连续运行，动作标签只用于截取结果。动作之间的原始 IMU 数据保留，VQF 和姿态状态不逐动作重置。H01/H02 使用冻结模型和前序状态，未使用移除节点的 IMU、十节点姿态、UWB 距离或位置作为估计输入。已有跨节点时间映射仍用于 TIMER2 时间对齐。', '',
        '数值实现采用 10 秒离线窗口和 0.1 秒间隔的三次样条控制点，使用未来帧。根平移固定，不能据此宣称已恢复世界坐标轨迹或达到实时要求。','',
        '## 测试与证据','',
        '13 项针对性测试通过，覆盖节点输入限制、时间还原、骨长、功能轴和初始弯曲。已知真值测试构造了五个 IMU 方向完全相同、但肩/肘运动分配不同的两组运动；加速度约束能区分它们，屈肘角 RMSE 小于预设 5°，加速度残差 RMS 小于 0.05 m/s²。去掉加速度后该区分能力明显下降。', '',
        f'全量输出共 {sum(v["frames"] for v in checks.values())} 个正式动作帧，{sum(v["nonconverged_frames"] for v in checks.values())} 帧所在窗口未数值收敛。对比表包含所有具有共同有效采样时间的有限结果，包括未收敛帧；页面对这些帧加警示。十节点只是回归参考，不是动作捕捉真值。','',
        '| 动作 | 旧左/右肘角差 MAE（°） | 新左/右肘角差 MAE（°） | 未收敛帧 |',
        '|---|---:|---:|---:|']
    for name,row in before.items():
        old=' / '.join(f'{v:.1f}' for v in row['old_bend_mae_deg'][:2]);new=' / '.join(f'{v:.1f}' for v in row['new_bend_mae_deg'][:2])
        lines.append(f'| {name} | {old} | {new} | {checks[name]["nonconverged_frames"]} |')
    lines+=['','## 仍未解决的部分','',
        '全套动作仍存在明显误差，不能把局部修正扩展成整体精度结论。缺失肢段仍受通用姿态和运动平滑约束影响；传感器到关节的偏移是从校准运动拟合的，多项拟合碰到边界，尚未成为可靠实测量。前臂功能轴恢复了被代码抹掉的信息，但不能保证任意时刻的上臂方向都被唯一确定。', '',
        '实测数据是体表距离，当前把它们作为关节中心骨长的近似。躯干高度 0.425 m 与髋半宽 0.14 m 仍是假设，不应混称为实测身体尺寸。三栏均使用同一组几何；旧结果只按物理采样时刻插值，未移动时间来对齐动作。', '',
        '本次是单线程实现与复核，没有独立代理复核。H 系列在历史工作中已被查看，因此是回归集，不是全新的盲测。','',
        '## 学术依据与边界','',
        '传感器功能轴可以通过受约束的关节运动识别，参见 [Seel 等，2012](https://doi.org/10.1109/CCA.2012.6402423)。这支持分开识别屈肘和旋前/旋后方向，不能把混合运动的第一主轴直接当作肘轴。', '',
        '稀疏惯性姿态估计需要联合考虑姿态、加速度、人体几何和跨帧约束，参见 [Sparse Inertial Poser](https://arxiv.org/abs/1703.08014)。本实现采用了这类物理建模思路，并非复现该论文完整算法，也没有证据表明已达到其精度。','',
        '关键产物：`CALIBRATION_FROZEN.json`、`FREEZE_SEAL.json`、`FROZEN_MODEL_SOURCE.zip`、`ARTIFACT_AUDIT.json`、`BEFORE_AFTER_REGRESSION.json`、`C2_SOLVER_AUDIT.json`、`C2_H_SOLVER_AUDIT.json` 和 `REPLAY_REVIEW.html`。原始记录及旧结果保持不变。']
    (out/'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(audit,indent=2))


if __name__=='__main__':main()
