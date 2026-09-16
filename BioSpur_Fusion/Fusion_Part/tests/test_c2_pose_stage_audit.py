import numpy as np
from scipy.spatial.transform import Rotation
from tools.c2_pose_stage_audit import summarize_pose_stage


def test_stage_audit_keeps_natural_bends_and_excludes_invalid_rows():
    rows = {}
    for side in ('left', 'right'):
        for segment, degrees in [('upper_arm', 0), ('forearm', 5), ('thigh', 0), ('shank', 8)]:
            q = Rotation.from_euler('x', [[degrees], [90]], degrees=True).as_quat()[:, [3, 0, 1, 2]]
            rows[segment+'_'+side] = {'quat_world_segment_wxyz': q, 'mask': np.array([True, False])}
    result = summarize_pose_stage({'trajectory': {'00': rows}})['00']
    for side in ('left', 'right'):
        for joint, angle in [('elbow', 5), ('knee', 8)]:
            assert result[joint+'_'+side]['valid_rows'] == 1
            np.testing.assert_allclose(result[joint+'_'+side]['min_median_max_deg'], angle, atol=1e-10)
