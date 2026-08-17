import numpy as np
import pytest

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.calibration import CalibrationBundle
from biospur_fusion.imu_pose_v1.mapping import FrozenOperatorMapping,H9
from conftest import mapping_for


def test_mapping_exact_scope_immutable_and_c2cc(synthetic_short):
    m=mapping_for(synthetic_short);assert len(m.node_to_segment)==10 and m.node_to_segment['BSFC2CC']=='pelvis'
    with pytest.raises(TypeError):m.node_to_segment['BSFC2CC']='torso'
    with pytest.raises(ValueError):m.assert_pooling(set(H9)|{'BSFC2CC'})


def test_mapping_rejects_stale_duplicate_and_typo(synthetic_short):
    p={'mapping':dict(synthetic_short.mapping),'binding_authority':'OPERATOR_RECORDED_POST_CAPTURE'}
    p['mapping']['BSFC22C']=p['mapping'].pop('BSFC2CC')
    with pytest.raises(ValueError):FrozenOperatorMapping.from_payload(p,capture_id='Capture_2_with_JOINT_LABEL',session_id='capture_2_with_joint_label',donning_id='capture_2_with_joint_label_donning_01')
    p={'mapping':synthetic_short.mapping,'binding_authority':'OPERATOR_RECORDED_POST_CAPTURE','session_id':'other'}
    with pytest.raises(ValueError):FrozenOperatorMapping.from_payload(p,capture_id='Capture_2_with_JOINT_LABEL',session_id='capture_2_with_joint_label',donning_id='capture_2_with_joint_label_donning_01')


def test_nonidentity_sensor_to_segment_enters_formula(synthetic_short):
    m=mapping_for(synthetic_short);neutral={n:so3.inv(q) for n,q in synthetic_short.q_I_S.items()}
    c=CalibrationBundle.from_neutral(m,neutral);assert c.assert_non_identity_exercised()
    for node in m.node_to_segment:
        np.testing.assert_allclose(c.apply(node,neutral[node]),[1,0,0,0],atol=1e-10)


def test_wrong_RSI_RIS_mutation_fails(synthetic_short):
    m=mapping_for(synthetic_short);node='BSFC2CC';qIS=synthetic_short.q_I_S[node];qWI=so3.inv(qIS)
    good=so3.mul(qWI,qIS);wrong=so3.mul(qWI,so3.inv(qIS))
    assert so3.geodesic(good,[1,0,0,0])<1e-10 and so3.geodesic(wrong,[1,0,0,0])>np.deg2rad(10)


def test_axis_permutation_sign_twist_and_layout_are_detectable():
    base=so3.exp([.3,-.2,.5]);mutations=[so3.exp([np.pi/2,0,0]),so3.exp([0,np.pi,0]),so3.exp([0,0,-.7])]
    assert all(so3.geodesic(base,so3.mul(base,m))>.5 for m in mutations)
