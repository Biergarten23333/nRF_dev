import numpy as np

from tools.summarize_c2_root_jitter import summarize


def test_gap_step_is_not_reported_as_native_jump():
    data = dict(time_s=np.array([0., .005, 1.005]),
                roots_b=np.array([[0., 0., 0.], [0., 0., .001], [0., 0., 1.]]),
                root_state_b=np.zeros((3, 9)), uwb_time_s=np.array([.002]),
                uwb_node=np.array(['node']), uwb_state_delta=np.zeros((1, 9)),
                uwb_accepted=np.array([True]))
    result = summarize(data, 0., 2.)
    assert result['gap_count'] == 1
    assert result['native_z_step_max_m'] == .001
    assert result['gap_step_max_m'] == .999


def test_window_without_frames_is_explicit():
    result = summarize({'time_s': np.array([0.]), 'roots_b': np.zeros((1, 3))}, 1., 2.)
    assert result == {'frames': 0}
