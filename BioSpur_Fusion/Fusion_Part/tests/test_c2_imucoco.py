"""Coordinate and real pretrained model integration gates."""
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL, TPOSE_SEGMENTS, encode


def test_frame_and_column_encoding():
    b = WORLD_TO_SMPL
    np.testing.assert_allclose(b @ [1, 0, 0], [0, 0, 1])
    np.testing.assert_allclose(b @ [0, 1, 0], [1, 0, 0])
    np.testing.assert_allclose(b @ [0, 0, 1], [0, 1, 0])
    assert np.linalg.det(b) == 1
    r = Rotation.from_rotvec([.3, .5, -.8]).as_matrix()[None, None]
    f = encode(r, np.array([[[1., 2., 3.]]]))
    np.testing.assert_allclose(f[0, 0, :3], r[0, 0, :, 0])
    np.testing.assert_allclose(f[0, 0, 3:6], r[0, 0, :, 1])
    np.testing.assert_allclose(f[0, 0, 6:], [1, 2, 3])
    with pytest.raises(ValueError, match='reflection'):
        encode(np.diag([-1., 1., 1.])[None], np.zeros((1, 3)))


def test_functional_tpose_and_natural_bend_are_different():
    tpose = TPOSE_SEGMENTS @ TPOSE_SEGMENTS.transpose(0, 2, 1)
    np.testing.assert_allclose(tpose, np.tile(np.eye(3), (5, 1, 1)), atol=1e-12)
    standing = Rotation.from_rotvec([0., -.2, 0.]).as_matrix()
    calibrated = standing @ TPOSE_SEGMENTS[1].T
    # The mounting/frame conversion preserves a real .2 rad difference.
    straight = TPOSE_SEGMENTS[1].T
    assert Rotation.from_matrix(calibrated @ straight.T).magnitude() == pytest.approx(.2)


def test_input_clock_gaps_preserve_time_and_mark_invalid(monkeypatch):
    from biospur_fusion.c2_imucoco import preprocessing as p
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    monkeypatch.setattr(p, 'relative', lambda rows, node, c: np.tile(np.eye(3), (len(rows), 1, 1)))
    monkeypatch.setattr(p, 'acceleration', lambda rows, node, c: np.zeros((len(rows), 3)))
    data = {}
    for i, node in enumerate(NODES):
        times = 100. + np.arange(201)*.005 + i*.001
        if i == 2:
            times = times[(times < 100.4) | (times > 100.5)]
        rows = np.zeros((len(times), 11))
        rows[:, 0] = times
        data[node] = {'imu': rows}
    prepared = p.prepare_stream(data, {})
    assert prepared['time_s'][0] == pytest.approx(100.004)
    np.testing.assert_allclose(np.diff(prepared['time_s']), 1/60)
    gap = (prepared['time_s'] > 100.4) & (prepared['time_s'] < 100.5)
    assert gap.any() and not prepared['input_valid'][gap].any()
    assert prepared['features'].shape[1:] == (5, 9)
    data[NODES[0]]['imu'][1, 0] = data[NODES[0]]['imu'][0, 0]
    with pytest.raises(ValueError, match='non-monotonic'):
        p.prepare_stream(data, {})


def test_calibration_rejects_holdout_and_removed_nodes():
    from biospur_fusion.c2_imucoco.calibration import fit_calibration
    with pytest.raises(ValueError, match='H-series'):
        fit_calibration({'H01_boxing': {}}, {})
    with pytest.raises(ValueError, match='five retained'):
        fit_calibration({'00_initial_still': {'removed_node': {}}}, {})


def test_released_feature_model_online_matches_offline():
    import torch
    from biospur_fusion.c2_imucoco.upstream import DEFAULT_UPSTREAM, load_features, set_placements
    if not (DEFAULT_UPSTREAM / 'UPSTREAM_MANIFEST.json').exists():
        pytest.skip('run tools/prepare_imucoco.py to enable pretrained integration gate')
    torch.set_num_threads(2)
    torch.manual_seed(42)
    offline = load_features(online=False)
    online = load_features(online=True)
    # Published vertex placements: pelvis, L/R forearm, L/R lower leg.
    vertices = [3021, 1962, 5431, 1096, 4583]
    positions = offline.mesh_positions[vertices].clone()
    mapping = set_placements(offline, positions)
    np.testing.assert_array_equal(set_placements(online, positions), mapping)
    assert mapping.max() < 5
    x = torch.randn(1, 4, 5, 9) * .1
    x[..., :6] += torch.tensor([1., 0., 0., 0., 1., 0.])
    with torch.inference_mode():
        expected = offline.inference_time_forward_mesh(x)
        h1 = h2 = None
        result = []
        for i in range(4):
            feat, h1, h2 = online.inference_time_forward_mesh_online(x[:, i:i+1], h1, h2)
            result.append(feat)
        actual = torch.cat(result, 1)
    torch.testing.assert_close(actual, expected, atol=3e-5, rtol=3e-4)
    from biospur_fusion.c2_imucoco.chunked import ChunkedFeatures
    chunks = ChunkedFeatures()
    chunks.set_placements(positions)
    with torch.inference_mode():
        first = chunks.forward(x[:, :2])
        second = chunks.forward(x[:, 2:])
    torch.testing.assert_close(torch.cat([first, second], 1), actual, atol=3e-5, rtol=3e-4)
    # A second actual sensor count is supported without synthesizing inputs.
    set_placements(online, positions[1:])
    assert online.current_device_2_joint_mapping.max().item() < 4
