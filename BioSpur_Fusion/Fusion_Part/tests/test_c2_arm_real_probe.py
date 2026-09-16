import numpy as np
from scipy.spatial.transform import Rotation

from run_c2_arm_progressive_real_probe import align_phase
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def stream():
    result = {}
    for index, node in enumerate(NODES):
        t = np.arange(0., 2., .005) + index*.0001
        a = np.zeros((len(t), 11))
        a[:, 0] = t
        a[:, 1] = 1.
        result[node] = a
    return result


def test_future_samples_do_not_change_prefix_and_mount_is_proper():
    data = stream()
    before, _ = align_phase(data, 0., 1.)
    for a in data.values():
        a[a[:, 0] >= 1., 5:] = 12345.
    after, _ = align_phase(data, 0., 1.)
    for node in NODES:
        np.testing.assert_array_equal(before[node], after[node])
    pelvis = Rotation.from_quat(before[NODES[0]][0, [2, 3, 4, 1]]).as_matrix()
    np.testing.assert_allclose(pelvis[:, 0], [0., 0., -1.], atol=1e-12)
    np.testing.assert_allclose(pelvis[:, 2], [0., 1., 0.], atol=1e-12)
    assert np.isclose(np.linalg.det(pelvis), 1.)


def test_large_gap_is_not_interpolated():
    data = stream()
    a = data[NODES[1]]
    data[NODES[1]] = a[(a[:, 0] < .4) | (a[:, 0] > .6)]
    result, audit = align_phase(data, 0., 1.)
    assert audit['rejected_gap_grid_rows'] > 30
    t = result[NODES[0]][:, 0]
    assert not np.any((t > .4) & (t < .6))


def test_direction_elevation_diagnostic_is_invariant_to_heading():
    from probe_c2_arm_phase_heading import direction_diagnostics
    factors = [dict(kind='direction', id='example', observed=[np.eye(3).tolist()],
                    axis=[1., 0., 0.], target=[[0., 0., 1.]], axial=False)]
    a = direction_diagnostics(np.zeros(4), factors)[0]
    b = direction_diagnostics(np.array([0., 0., 0., 1.3]), factors)[0]
    assert np.isclose(a['yaw_invariant_elevation_rms_deg'], 90.)
    assert a == b
