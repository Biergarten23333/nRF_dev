import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.geometry import OBSERVED
from biospur_fusion.c2_five_calibration.progressive.physical_prefix import warm_start_rotation


def fixture():
    prior=np.tile(np.eye(3),(10,24,1,1));observed=np.tile(np.eye(3),(10,5,1,1))
    observed[:,1]=Rotation.from_euler('z',.3).as_matrix()
    data=dict(prior=prior,observed=observed,time_s=np.arange(10)*.05)
    old=dict(rotation=prior[:5].copy(),time_s=data['time_s'][:5].copy())
    old['rotation'][:,9]=Rotation.from_euler('x',.2).as_matrix()
    return data,old


def test_warm_start_keeps_past_guess_but_rebinds_observed_and_preserves_inputs():
    data,old=fixture();before=data['prior'].copy();past=old['rotation'].copy()
    warm=warm_start_rotation(data,old)
    np.testing.assert_array_equal(warm[:,OBSERVED],data['observed'])
    np.testing.assert_array_equal(warm[:5,9],old['rotation'][:,9])
    np.testing.assert_array_equal(warm[5:,9],data['prior'][5:,9])
    np.testing.assert_array_equal(data['prior'],before)
    np.testing.assert_array_equal(old['rotation'],past)


def test_future_or_shifted_guess_is_rejected():
    data,old=fixture();old['time_s']+=.05
    with pytest.raises(ValueError,match='exact past'):warm_start_rotation(data,old)


def test_reflected_guess_is_rejected():
    data,old=fixture();old['rotation'][0,0,0,0]=-1.
    with pytest.raises(ValueError,match='proper'):warm_start_rotation(data,old)
