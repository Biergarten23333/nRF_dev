"""Wearing/back prior remains geometry-only, positive, and rotation covariant."""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.antenna_los import outward_facing_information_weights


def test_direct_front_and_back_use_existing_information_mapping():
    anchors=np.array([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],
        [0,0,1],[0,0,-1],[1,1,0],[-1,-1,0]],float)
    scores,weights=outward_facing_information_weights(np.zeros(3),anchors,np.array([1.,0,0]))
    np.testing.assert_array_equal(scores[:6],[1,-1,0,0,0,0])
    np.testing.assert_array_equal(weights[:6],[.75,.25,.5,.5,.5,.5])
    assert np.all((weights>=.25)&(weights<=.75))
    np.testing.assert_allclose(weights,.5+.25*scores,atol=0,rtol=0)


def test_rigid_rotation_and_translation_preserve_facing_prior():
    tag=np.array([.2,.4,.6])
    anchors=np.array([[1,0,0],[-1,0,0],[0,1,0],[0,-1,0],
        [0,0,1],[0,0,-1],[1,1,0],[-1,-1,0]],float)
    normal=np.array([.8,.2,-.1])
    r=Rotation.from_rotvec([.2,-.3,.5]).as_matrix()
    shift=np.array([3.,-2.,1.])
    first=outward_facing_information_weights(tag,anchors,normal)
    second=outward_facing_information_weights(r@tag+shift,anchors@r.T+shift,r@normal)
    np.testing.assert_allclose(first,second,atol=1e-15,rtol=1e-15)


def test_no_range_or_other_body_geometry_enters_prior():
    # The API accepts only the tag, anchors and its outward normal; no joint
    # geometry, range residuals or future target positions can be supplied.
    import inspect
    assert tuple(inspect.signature(outward_facing_information_weights).parameters)==(
        'tag_position_world_m','anchor_positions_world_m','outward_normal_world_vector')
