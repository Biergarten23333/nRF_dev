import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint, DOWN, _rotation, _wxyz
from biospur_fusion.c2_articulated_biomechanics.bend_plane import reconcile_hinge_bend_plane


JOINT=HingeJoint('knee','thigh','shank','09',(1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,0.,120.,1,1)


def test_preserves_natural_bend_direction_and_is_idempotent():
    p=_wxyz(Rotation.from_rotvec(np.zeros((4,3))))
    c=_wxyz(Rotation.from_rotvec(np.array([[0,.15,0],[0,.01,0],[0,0,0],[0,.16,0]])))
    a,b,m=reconcile_hinge_bend_plane(p,c,JOINT)
    np.testing.assert_allclose(_rotation(a).apply(DOWN),_rotation(p).apply(DOWN),atol=1e-12)
    np.testing.assert_allclose(b,c,atol=1e-12)
    aa,bb,_=reconcile_hinge_bend_plane(a,b,JOINT)
    np.testing.assert_allclose(_rotation(aa).as_matrix(),_rotation(a).as_matrix(),atol=1e-12)
    np.testing.assert_allclose(bb,b,atol=1e-12)
    assert m['physical_sensor_orientation_modified'] is False


def test_extension_noise_does_not_flip_axial_branch_or_zero_flexion():
    p=_wxyz(Rotation.from_rotvec(np.zeros((3,3))))
    c=_wxyz(Rotation.from_rotvec([[0,.1,0],[0,.001,0],[0,-.001,0]]))
    a,b,m=reconcile_hinge_bend_plane(p,c,JOINT)
    np.testing.assert_allclose(_rotation(a).as_matrix(),np.repeat(_rotation(a).as_matrix()[:1],3,axis=0),atol=1e-12)
    np.testing.assert_array_equal(b,c)
    assert m['near_extension_held_rows']==2


def test_upper_rom_still_applies_and_raw_inputs_unchanged():
    p=_wxyz(Rotation.from_rotvec(np.zeros((1,3))))
    c=_wxyz(Rotation.from_rotvec([[0,np.radians(140),0]]))
    original=c.copy()
    a,b,m=reconcile_hinge_bend_plane(p,c,JOINT)
    bend=np.degrees(np.arccos(np.sum(_rotation(a).apply(DOWN)*_rotation(b).apply(DOWN),axis=1)))
    np.testing.assert_allclose(bend,120,atol=1e-10)
    np.testing.assert_array_equal(c,original)
    assert m['upper_rom_capped_rows']==1


def test_invalid_input_rejected():
    with pytest.raises(ValueError):
        reconcile_hinge_bend_plane(np.zeros((0,4)),np.zeros((0,4)),JOINT)
