"""Measure where a calibration replay changes observed segment motion.

This is attribution, not anatomical acceptance: unsigned inter-segment bends
cannot determine whether a knee is on the correct side of its hinge plane.
"""
import numpy as np
from biospur_fusion.c2_coupled_progressive.math_utils import qmt_wxyz_to_rotation


def summarize_pose_stage(trajectory):
    result = {}
    for episode, segments in trajectory['trajectory'].items():
        rotations = {
            name: qmt_wxyz_to_rotation(row['quat_world_segment_wxyz']).as_matrix()
            for name, row in segments.items()
            if isinstance(row, dict) and 'quat_world_segment_wxyz' in row
        }
        joints = {}
        for side in ('left', 'right'):
            for joint, parent, child in (
                ('elbow', 'upper_arm', 'forearm'), ('knee', 'thigh', 'shank')
            ):
                a, b = parent+'_'+side, child+'_'+side
                valid = np.asarray(segments[a]['mask'], bool) & np.asarray(segments[b]['mask'], bool)
                dot = np.sum(rotations[a][..., :, 2]*rotations[b][..., :, 2], axis=-1)
                bends = np.degrees(np.arccos(np.clip(dot[valid], -1, 1)))
                joints[joint+'_'+side] = {
                    'valid_rows': len(bends),
                    'min_median_max_deg': np.quantile(bends, [0, .5, 1]).tolist() if len(bends) else None,
                }
        result[episode] = joints
    return result
