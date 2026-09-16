"""Mixed sensor exports and reference-dependent runtime must fail closed."""
import numpy as np
import pytest

from biospur_fusion.c2_imucoco.workflow import load_input
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def streams():
    rows = np.zeros((8, 11))
    rows[:, 0] = np.arange(8) * .005
    rows[:, 1] = 1.
    return {f'00_initial_still/{node}/imu': rows.copy() for node in NODES}


def test_exact_five_archive_round_trip(tmp_path):
    original = streams()
    path = tmp_path / 'five.npz'
    np.savez(path, **original)
    result = load_input(path)
    assert set(result['00_initial_still']) == set(NODES)
    for node in NODES:
        np.testing.assert_array_equal(result['00_initial_still'][node]['imu'],
                                      original[f'00_initial_still/{node}/imu'])


@pytest.mark.parametrize('extra', ['00_initial_still/BSF_REMOVED/imu',
                                  '00_initial_still/BSFC2CC/uwb', 'reference/rotation'])
def test_extra_payload_rejected_before_decoding(tmp_path, extra):
    archive = streams()
    archive[extra] = np.array([object()], dtype=object)
    path = tmp_path / 'mixed.npz'
    np.savez(path, **archive)
    with pytest.raises(ValueError, match='forbidden payload'):
        load_input(path)


def test_missing_node_rejected(tmp_path):
    archive = streams()
    archive.pop(next(iter(archive)))
    path = tmp_path / 'missing.npz'
    np.savez(path, **archive)
    with pytest.raises(ValueError, match='exactly the five'):
        load_input(path)


@pytest.mark.parametrize('fault', ['duplicate_time', 'nan', 'wrong_shape'])
def test_invalid_sensor_stream_rejected(tmp_path, fault):
    archive = streams()
    key = next(iter(archive))
    if fault == 'duplicate_time':
        archive[key][1, 0] = archive[key][0, 0]
    elif fault == 'nan':
        archive[key][1, 6] = np.nan
    else:
        archive[key] = archive[key][:, :10]
    path = tmp_path / 'bad.npz'
    np.savez(path, **archive)
    with pytest.raises(ValueError, match='invalid five-node'):
        load_input(path)
