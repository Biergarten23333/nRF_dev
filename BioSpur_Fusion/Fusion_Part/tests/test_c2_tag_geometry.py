import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_uwb_calibration.tag_geometry import (
    bind_frozen_parity, engineering_tag_points, SensorBindingEvidence,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import NODE_TO_PROXY_POINT


def test_parity_then_heading_binds_anatomical_right_and_preserves_z():
    a = Rotation.from_euler('z', .43).as_matrix()
    m = np.diag([-1.,1.,1.])
    geometry = bind_frozen_parity(m,a,a@np.array([.23,0,0]))
    np.testing.assert_allclose(geometry.vectors(a@np.array([.23,0,0])),[-.23,0,0],atol=1e-14)
    np.testing.assert_allclose(geometry.vectors([0,0,1]),[0,0,1],atol=1e-14)
    assert np.linalg.det(geometry.matrix) == pytest.approx(-1)
    assert np.linalg.det(geometry.heading_after_reflection) == pytest.approx(1)
    # Physical registered right normal stays -X, without reflection again.
    assert geometry.vectors(a@np.array([.23,0,0])) @ np.array([-1.,0,0]) > 0


def test_geometry_jacobian_and_no_input_mutation():
    a=Rotation.from_euler('z',.19).as_matrix()
    old=a.copy()
    geometry=bind_frozen_parity(np.diag([-1.,1.,1.]),a,a[:,0])
    np.testing.assert_array_equal(a,old)
    j=np.arange(18,dtype=float).reshape(3,6)/20
    delta=np.arange(6,dtype=float)*1e-7
    np.testing.assert_allclose(geometry.vectors(j@delta),geometry.jacobian(j)@delta,atol=1e-15)
    with pytest.raises(ValueError):
        bind_frozen_parity(np.eye(3),a,a[:,0])


def test_chest_is_separate_source_bound_candidate_not_drawing_joint():
    joints={key:np.array([[0.,0.,.425]]) for key in NODE_TO_PROXY_POINT.values()}
    tags=engineering_tag_points(joints,np.array([[0.,0.,1.]]),np.array([.14,.15]))
    np.testing.assert_array_equal(joints['shoulder_mid'],[[0,0,.425]])
    np.testing.assert_allclose(tags['BSF31CC'],[[0,0,.28]])
    assert not np.array_equal(tags['BSF31CC'],joints['shoulder_mid'])
    # A nonvertical torso demonstrates this is not a hardcoded .280 Z point.
    tags=engineering_tag_points(joints,np.array([[1.,0.,0.]]),np.array([.14,.15]))
    np.testing.assert_allclose(tags['BSF31CC'],[[-.145,0,.425]])
    with pytest.raises(ValueError):
        SensorBindingEvidence('BSF31CC','rib triangle',None,None,'sealed').require_measured_offset()


def test_runner_rejects_unadapted_articulated_geometry(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'tools'))
    from run_c2_continuous_archive_ab import run
    pose=tmp_path/'pose.npz'
    np.savez(pose,geometry_embedding_from_previous=np.diag([-1.,1.,1.]))
    with pytest.raises(ValueError,match='not yet supported'):
        run(tmp_path,pose,tmp_path/'output',duration_s=1,
            feedback_mode='full-state',articulated_rotations=tmp_path/'old_rotations.npz')
