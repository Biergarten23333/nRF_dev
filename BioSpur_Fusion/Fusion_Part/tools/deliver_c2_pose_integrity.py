"""Build an explicit posture-only comparison, without claiming fusion rerun."""
import argparse
import json
from pathlib import Path
import numpy as np
from tools.build_c2_full_continuous_ab_viewer import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pose', type=Path, required=True)
    parser.add_argument('--previous-pose', type=Path)
    parser.add_argument('--previous-label', default='旧连续姿态（有错）')
    parser.add_argument('--candidate-label', default='连续 IMU 重新标定 + IK')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    with np.load('logs/c2_passive_seated_repair_20260914_070933/full/CONTINUOUS_AB.npz') as old, np.load(args.pose) as new:
        np.testing.assert_array_equal(old['time_s'], new['time_s'])
        np.testing.assert_array_equal(old['joint_names'], new['joint_names'])
        previous = old['joints_relative']
        if args.previous_pose is not None:
            with np.load(args.previous_pose) as source:
                np.testing.assert_array_equal(new['time_s'], source['time_s'])
                np.testing.assert_array_equal(new['joint_names'], source['joint_names'])
                previous = source['joints_relative'].copy()
        np.savez_compressed(args.output/'VIEW.npz', time_s=new['time_s'],
            roots_a=old['roots_b_posterior'], roots_b=old['roots_b_posterior'],
            joints_relative=previous, joints_relative_b=new['joints_relative'],
            joint_names=new['joint_names'], anchors_world_m=old['anchors_world_m'])
    summary = dict(comparison_labels=[args.previous_label, args.candidate_label],
        viewer_policy='完整连续输入上的姿态修复对照；两侧复用同一根位置，仅比较姿态。不是全量融合重算或抗滑移验收。',
        comparison_note='19个已采动作全部保留；01未采。膝关节仍有旧120度上限，深蹲/勾腿受限，未宣称全部姿态正确。',
        scientific_pass=False, full_calibration_pass=False, root_fusion_recomputed=False)
    (args.output/'SUMMARY.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    build(args.output/'VIEW.npz', Path('logs/c2_support_position_guard_20260913/delivery/REGIONS.json'),
          args.output/'FULL_POSE_AB.html', summary_path=args.output/'SUMMARY.json')


if __name__ == '__main__':
    main()
