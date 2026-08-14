import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.calibration.frames import fit_proper_rotation


def test_arbitrary_reversed_mount_correspondences_recover_proper_shared_frame():
    rng = np.random.default_rng(47)
    source = rng.normal(size=(40, 3)); source /= np.linalg.norm(source, axis=1, keepdims=True)
    truth = Rotation.from_euler("zyx", [1.1, -.4, .7]).as_matrix()
    target = (truth @ source.T).T
    result = fit_proper_rotation(source, target, require_yaw=True)
    assert result.qualified and result.rank == 3 and result.yaw_observable
    assert np.linalg.det(np.asarray(result.R_N_from_V4)) > .999999
    assert np.max(abs(np.asarray(result.R_N_from_V4) - truth)) < 1e-10


def test_collinear_motion_reports_degeneracy():
    source = np.tile([0., 0., 1.], (20, 1)); target = source.copy()
    result = fit_proper_rotation(source, target, require_yaw=True)
    assert not result.qualified and result.rank == 1 and result.R_N_from_V4 is None
