import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_five_calibration.pelvis_frame import frame_from_axes


def test_recovers_tilted_frame_without_forcing_gravity_vertical():
    r=Rotation.from_rotvec([.2,-.3,.6]).as_matrix()
    got=frame_from_axes(r[:,1],r[:,2])
    np.testing.assert_allclose(got,r,atol=1e-12)
    assert abs(got[2,2]-1)>.01


def test_common_sensor_rotation_equivariance_and_no_reflection():
    y=np.array([.1,1.,.1]);z=np.array([0.,.2,1.])
    r=Rotation.from_rotvec([-.3,.4,.1]).as_matrix()
    got=frame_from_axes(y,z)
    np.testing.assert_allclose(frame_from_axes(r@y,r@z),r@got,atol=1e-12)
    np.testing.assert_allclose(got.T@got,np.eye(3),atol=1e-12)
    assert np.linalg.det(got)>0
    with pytest.raises(ValueError,match='independent'):frame_from_axes(y,y)
    with pytest.raises(ValueError,match='nonzero'):frame_from_axes(y,np.zeros(3))


def test_proposal_updates_authoritative_metadata_without_mutating_input():
    import copy
    from biospur_fusion.c2_sparse_nodes.inputs import NODES
    from biospur_fusion.c2_five_calibration.pelvis_frame import pelvis_two_axis_proposal
    t=np.arange(6000)/200
    episodes={}
    for name,axis in [('14_trunk_flex_extend',np.array([0.,1.,0.])),('15_trunk_axial_rotation',np.array([.1,0.,1.]))]:
        axis=axis/np.linalg.norm(axis);theta=.5*np.sin(t)
        xyzw=Rotation.from_rotvec(theta[:,None]*axis).as_quat()
        rows=np.column_stack((t,xyzw[:,[3,0,1,2]],np.tile([0,0,9.80665],(len(t),1)),.5*np.cos(t)[:,None]*axis))
        episodes[name]={n:dict(imu=rows.copy()) for n in NODES}
    calibration=dict(initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),functional_yaw_rad=[0.]*5,five_node_functional_frames={NODES[0]:dict(old=True)})
    saved=copy.deepcopy(calibration)
    candidate,audit=pelvis_two_axis_proposal(episodes,calibration)
    assert calibration==saved
    assert candidate['five_node_functional_frames'][NODES[0]]['owner']=='pelvis_two_axis_proposal'
    assert candidate['previous_pelvis_functional_frame']==dict(old=True)
    assert not candidate['calibration_accepted'] and audit['standing_long_axis_tilt_deg']>1
    with pytest.raises(ValueError,match='C2'):
        pelvis_two_axis_proposal({**episodes,'H01_boxing':episodes['14_trunk_flex_extend']},calibration)
