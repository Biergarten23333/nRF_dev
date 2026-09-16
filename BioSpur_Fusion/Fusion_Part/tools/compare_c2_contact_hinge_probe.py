"""Compare only newly solved frames against the previous contact solver."""
import argparse
import json
from pathlib import Path
import re
import subprocess

import numpy as np
from build_c2_full_continuous_ab_viewer import build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('previous', type=Path)
    parser.add_argument('candidate', type=Path)
    parser.add_argument('--previous-label')
    parser.add_argument('--candidate-label')
    parser.add_argument('--note')
    parser.add_argument('--include-saved-prefix', action='store_true',
                        help='Compare saved history too; disclose that the prefix was inherited at resume')
    args = parser.parse_args()
    result = json.loads((args.candidate/'RESULT.json').read_text())
    resumed_at = result['resumed_after_frame']
    start = 0 if args.include_saved_prefix else resumed_at
    with np.load(args.previous/'PROBE.npz') as a, np.load(args.candidate/'PROBE.npz') as b:
        end = min(len(a['time_s']), len(b['time_s']))
        if end <= start+1:
            raise ValueError('no new motion to compare')
        np.testing.assert_array_equal(a['time_s'][start:end], b['time_s'][start:end])
        t = b['time_s'][start:end].copy()
        names = b['joint_names'].tolist()
        stationary = b['stationary'][start:end].astype(bool)
        data = {label: (d['roots_candidate'][start:end].copy(), d['pose_candidate'][start:end].copy())
                for label, d in [('previous', a), ('candidate', b)]}
        offset = float(t[0]-b['time_s'][0])
    metrics = {}
    mask = stationary[:-1] & stationary[1:] & (np.diff(t)[:, None] <= .0075)
    for label, (root, pose) in data.items():
        step = np.linalg.norm(np.diff(root[:, None]+pose, axis=0), axis=2)
        metrics[label] = dict(root_peak_step_mm=float(1000*np.linalg.norm(np.diff(root, axis=0), axis=1).max()),
            joint_peak_step_mm={k: float(1000*step[:, names.index(k)].max()) for k in
                ('knee_left', 'knee_right', 'ankle_left', 'ankle_right')},
            joint_path_mm={k: float(1000*step[:, names.index(k)].sum()) for k in
                ('knee_left', 'knee_right', 'ankle_left', 'ankle_right')},
            stationary_foot_path_mm=[float(1000*step[:, names.index('ankle_'+s)][mask[:, j]].sum())
                                     for j, s in enumerate(('left', 'right'))])
    report = dict(scope=('NEW_SUFFIX_ONLY_NOT_CONTINUOUS_FULL_RUN' if start else 'CONTINUOUS_PREFIX_COMPARISON'), start_frame=start,
        end_frame=end-1, duration_s=float(t[-1]-t[0]), capture_start_offset_s=offset,
        candidate_status=result['status'], metrics=metrics, scientific_pass=False,
        inherited_prefix_included=bool(args.include_saved_prefix and resumed_at), resumed_at_frame=resumed_at)
    (args.candidate/'SUFFIX_COMPARISON.json').write_text(json.dumps(report, indent=2))
    with np.load(args.candidate/'VIEW.npz') as view:
        anchors = view['anchors_world_m'].copy()
    np.savez_compressed(args.candidate/'SUFFIX_VIEW.npz', time_s=t,
        roots_a=data['previous'][0], roots_b=data['candidate'][0],
        joints_relative=data['previous'][1], joints_relative_b=data['candidate'][1],
        joint_names=names, anchors_world_m=anchors)
    candidate_label = ('B：骨盆、双腿与接地共同求解' if result.get('pelvis_orientation')
                       else 'B：关节与接地共同求解')
    initialization = ('B从A已提交状态接续，非从头全程验证' if start
                      else '两侧同一起始状态、连续求解；仅展示两侧都有结果的时段，不代表全体校准完成')
    summary = dict(comparison_labels=['A：上一版接地求解', candidate_label],
        viewer_policy=f'仅比较 {offset:.3f}–{offset+t[-1]-t[0]:.3f} 秒；两侧同一十节点UWB/IMU输入。{initialization}；无显示滤波。',
        scientific_pass=False, full_calibration_pass=False)
    if result.get('calibrated_pose'):
        summary.update(comparison_labels=['旧连续标定 + 接地', '重新标定姿态 + 同一接地求解'],
            viewer_policy=f'仅比较连续前 {t[-1]-t[0]:.3f} 秒。修正姿态后重新求解接地；复用原UWB根先验与支撑分类，未重算上游全量融合。两侧同时间、同相机，无显示滤波。',
            comparison_note='T-pose姿态回归修复；不能据此宣称其余全部动作抗滑移通过。')
    if args.previous_label or args.candidate_label:
        if not (args.previous_label and args.candidate_label):
            raise ValueError('both comparison labels are required')
        summary['comparison_labels']=[args.previous_label,args.candidate_label]
    if args.note:
        summary['viewer_policy']=args.note
        summary['comparison_note']='当前70秒接地回归，不代表全量融合通过；原始传感器方向未改写。'
    (args.candidate/'SUFFIX_SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False))
    regions = json.loads(Path('logs/c2_support_position_guard_20260913/delivery/REGIONS.json').read_text())
    (args.candidate/'SUFFIX_REGIONS.json').write_text(json.dumps([r for r in regions if r['start_s']<=t[-1] and r['end_s']>t[0]]))
    output = args.candidate/'PREVIOUS_VS_HINGE.html'
    build(args.candidate/'SUFFIX_VIEW.npz', args.candidate/'SUFFIX_REGIONS.json', output,
          summary_path=args.candidate/'SUFFIX_SUMMARY.json')
    html = output.read_text().replace('C2 全程连续 · Pure IMU / IMU+UWB', 'C2 接地与关节一致性 · 短段对照')
    html = html.replace('C2 · 全程连续 A/B', 'C2 · 新求解片段 A/B')
    html = html.replace('同步的左右三维骨架；左纯 IMU，右 IMU+UWB', '同步三维骨架；左上一版接地求解，右关节与接地共同求解')
    output.write_text(html)
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.S)
    assert len(scripts) == 1
    subprocess.run(['node', '--check'], input=scripts[0], text=True, check=True, timeout=15)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    plt.style.use('dark_background')
    fig, axes = plt.subplots(3, 2, figsize=(13, 9), sharex=True)
    for label, color in [('previous', '#ffad63'), ('candidate', '#3cddd5')]:
        root, pose = data[label]
        for row, key in enumerate(('root', 'knee_left', 'knee_right')):
            xyz = root if key == 'root' else root+pose[:, names.index(key)]
            axes[row, 0].plot(t-t[0]+offset, 1000*np.linalg.norm(xyz-xyz[0], axis=1), color=color, label=label)
            axes[row, 1].plot(t[1:]-t[0]+offset, 1000*np.linalg.norm(np.diff(xyz, axis=0), axis=1), color=color)
            axes[row, 0].set_ylabel(key+' displacement (mm)')
            axes[row, 1].set_ylabel(key+' frame step (mm)')
    for ax in axes.flat:
        ax.grid(alpha=.2)
    axes[0, 0].legend()
    for ax in axes[-1]:
        ax.set_xlabel('Capture-relative time (s), native 200 Hz')
    fig.tight_layout()
    fig.savefig(args.candidate/'MOTION_COMPARISON.png', dpi=130)
    plt.close(fig)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
