"""Animation must show frozen old poses at the actual new sample times."""
import numpy as np
import pytest

from c2_five_previous_output import PreviousOutput
from test_c2_five_calibration import geometry


def previous():
    result = PreviousOutput.__new__(PreviousOutput)
    result.geometry = geometry()
    result.frame = dict(matrix_world_output_from_internal=np.diag([1., -1., 1.]).tolist(),
                        world_to_smpl=np.eye(3).tolist())
    times = 123.+np.arange(10)/20
    rotation = np.tile(np.eye(3), (10, 24, 1, 1))
    valid = np.ones(10, dtype=bool)
    valid[4] = False
    result.calibration = {'06_elbow_left/'+key: value for key, value in
                          dict(time_s=times, rotation=rotation, valid=valid).items()}
    result.holdout = dict(time_s=times, rotation=rotation, valid=valid)
    return result, times


@pytest.mark.parametrize('action', ['06_elbow_left', 'H01_boxing'])
def test_same_timestamp_old_pose_and_invalid_samples_are_preserved(action):
    result, times = previous()
    points, valid = result.on_grid(action, times, dict(lo=times[0], hi=times[-1]))
    assert points.shape == (10, 13, 3)
    assert np.isfinite(points).all()
    assert valid.sum() == 9 and not valid[4]


@pytest.mark.parametrize('action', ['06_elbow_left', 'H01_boxing'])
def test_one_frame_shift_cannot_be_hidden_by_equal_frame_count(action):
    result, times = previous()
    with pytest.raises(ValueError, match='sample times differ'):
        result.on_grid(action, times+.05, dict(lo=times[0], hi=times[-1]))
