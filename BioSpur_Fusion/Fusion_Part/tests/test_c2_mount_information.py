import numpy as np
import pytest
from biospur_fusion.c2_five_calibration.mount_information import axis_information


def test_repeated_hinge_axes_leave_longitudinal_frame_undetermined():
    result=axis_information([[0,1,0],[0,-1,0],[0,1,0]],[.9,.8,.95])
    assert result['structural_rank']==2
    np.testing.assert_allclose(np.abs(result['unresolved_body_rotation_axes']),[[0,1,0]])
    assert not result['is_posterior_covariance']


def test_two_distinct_axes_resolve_structural_rotation_and_ignore_zero_weight():
    assert axis_information([[0,1,0],[0,0,1]],[1,1])['structural_rank']==3
    assert axis_information([[0,1,0],[0,0,1]],[1,0])['structural_rank']==2
    assert axis_information([[0,1,0]],[0])['structural_rank']==0


def test_information_respects_coordinate_rotation():
    from scipy.spatial.transform import Rotation
    R=Rotation.from_rotvec([.3,.6,-.2]).as_matrix()
    a=np.array([[0.,1,0],[0,0,1]])
    np.testing.assert_allclose(axis_information(a,[1,.8])['eigenvalues'],axis_information(a@R.T,[1,.8])['eigenvalues'],atol=1e-12)
    with pytest.raises(ValueError):axis_information([[0,0,0]],[1])
