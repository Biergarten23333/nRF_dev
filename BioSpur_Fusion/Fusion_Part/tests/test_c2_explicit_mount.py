from dataclasses import dataclass
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_native200_calibration.functional_mount import calibration_with_mounts


@dataclass
class Calibration:
    initial_world_sensor: dict
    yaw_closure_rad: dict


def test_preserves_natural_flexion_instead_of_zeroing():
    mount = Rotation.from_rotvec([.4, -.2, .1]).as_matrix()
    natural = Rotation.from_rotvec([.12, 0, 0]).as_matrix()
    measured = natural@mount.T
    original = Calibration({'forearm': measured.copy()}, {'pelvis': .01})
    adapted = calibration_with_mounts(original, {'forearm': mount})
    np.testing.assert_allclose(measured@adapted.initial_world_sensor['forearm'].T, natural, atol=1e-12)
    np.testing.assert_allclose(original.initial_world_sensor['forearm'], measured)
    assert adapted.yaw_closure_rad == original.yaw_closure_rad
    assert not np.allclose(natural, np.eye(3))


@pytest.mark.parametrize('bad', [np.zeros((3,3)), -np.eye(3), np.full((3,3),np.nan)])
def test_rejects_invalid_mount(bad):
    with pytest.raises(ValueError):
        calibration_with_mounts(Calibration({'x':np.eye(3)},{}), {'x':bad})


def test_missing_mount_is_not_replaced_by_standing_zero():
    with pytest.raises(ValueError):
        calibration_with_mounts(Calibration({'x':np.eye(3)},{}), {})
