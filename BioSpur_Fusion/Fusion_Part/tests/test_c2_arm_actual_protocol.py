import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_five_calibration.progressive.arm_factors import build_phase
from biospur_fusion.c2_five_calibration.progressive.arm_model import residual_terms
from biospur_fusion.c2_five_calibration.progressive.neutral_frame import initial_neutral_correction
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def test_azimuth_condition_does_not_force_horizontal_pose():
    tilt=np.deg2rad(35.)
    f=dict(id='tpose',kind='direction',direction_mode='azimuth',axial=False,
           observed=[np.eye(3).tolist()],axis=[np.cos(tilt),0.,np.sin(tilt)],target=[[1.,0.,0.]])
    assert np.linalg.norm(residual_terms(np.zeros(4),[f])['tpose'])<1e-12
    f['direction_mode']='spatial'
    assert np.linalg.norm(residual_terms(np.zeros(4),[f])['tpose'])>.5


def test_wrong_azimuth_cannot_reduce_cost_by_tilting_toward_vertical():
    f=dict(id='direction',kind='direction',direction_mode='azimuth',axial=False,
           observed=[np.eye(3).tolist()],axis=[0.,1.,0.],target=[[1.,0.,0.]])
    first=residual_terms(np.zeros(4),[f])['direction']
    f['axis']=[0.,.01,np.sqrt(1.-.01**2)]
    np.testing.assert_allclose(residual_terms(np.zeros(4),[f])['direction'],first)
    f['axis']=[0.,0.,1.]
    with pytest.raises(ValueError,match='undefined horizontal'):
        residual_terms(np.zeros(4),[f])


def test_no_hanging_arm_constraint_from_natural_standing():
    a=np.zeros((6000,11));a[:,0]=np.arange(6000)*.005;a[:,1]=1.
    rows={n:a.copy() for n in NODES}
    factors,diagnostics=build_phase('00_initial_still:full',0.,30.,rows,policy='actual_c2_conditional')
    assert not factors
    assert diagnostics[0]['status']=='NO_EXACT_INITIAL_ARM_DIRECTION'
    assert diagnostics[0]['max_evidence_time']==a[-1,0]


def test_neutral_correction_removes_initial_tilt_and_is_yaw_covariant():
    initial=Rotation.from_euler('xyz',[[.2,-.1,.7]]*3)
    correction=initial_neutral_correction(initial)
    corrected=(initial*correction).as_matrix()
    np.testing.assert_allclose(corrected[:,:,2],np.tile([0.,0.,1.],(3,1)),atol=1e-12)
    yaw=Rotation.from_euler('z',.9)
    other=initial_neutral_correction(yaw*initial)
    np.testing.assert_allclose(correction.as_matrix(),other.as_matrix(),atol=1e-12)
