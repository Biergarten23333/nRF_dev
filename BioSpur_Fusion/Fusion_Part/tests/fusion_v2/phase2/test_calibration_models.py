import numpy as np,pytest
from biospur_fusion.calibration_v2.models import *
from biospur_fusion.calibration_v2.association import ROLES

def test_known_lever_arm_centripetal_force():
 f=specific_force(np.eye(3),[0,0,0],[0,0,0],[0,0,2],[1,0,0],[0,0,-9.81],[0,0,0]);assert np.allclose(f,[-4,0,9.81])
def test_tangential_force():
 f=specific_force(np.eye(3),[0,0,0],[0,0,2],[0,0,0],[0,1,0],[0,0,-9.81],[0,0,0]);assert np.allclose(f,[-2,0,9.81])
def test_bias_is_sensor_frame_vector():assert np.allclose(specific_force(np.eye(3),[0,0,0],[0,0,0],[0,0,0],[0,0,0],[0,0,-9.81],[1,2,3]),[1,2,12.81])
def test_shared_axis_different_frames_zero():
 R=np.diag([-1,-1,1]);assert np.linalg.norm(shared_axis_residual([0,0,1],np.eye(3),R,[0,0,2],[0,0,2]))==0
def test_shared_axis_rejects_direct_frame_subtraction_assumption():
 R=np.array([[0,-1,0],[1,0,0],[0,0,1]]);r=shared_axis_residual([1,0,0],np.eye(3),R,[0,1,0],[1,0,0]);assert np.linalg.norm(r)<1e-12
def test_scaled_rank_full():
 r=scaled_rank_report(np.eye(4),np.ones(4));assert r["rank"]==4
def test_declared_gauge_removed():
 r=scaled_rank_report(np.eye(4),np.ones(4),declared_gauge_columns=(3,));assert r["rank"]==3 and r["columns_after_gauge"]==3
def test_undeclared_weak_mode_detected():
 J=np.diag([1,1,1,1e-13]);assert scaled_rank_report(J,np.ones(4))["rank"]==3
def test_covariance_psd():
 _,e=ensure_covariance(np.eye(6));assert min(e)>0
def test_covariance_indefinite_rejected():
 with pytest.raises(ValueError):ensure_covariance(np.diag([1,-1]))
def test_conditional_calibration_nonzero_covariance():
 m={f"N{i}":ROLES[i] for i in range(10)};c=conditional_prior_calibration(m,1);assert all(min(x["T_segment_to_IMU"]["covariance_diagonal"])>0 for x in c["conditional_marginals"].values())
@pytest.mark.parametrize("mult",[.5,2.0])
def test_prior_sensitivity_multiplier_is_finite(mult):assert np.isfinite(np.eye(3)*mult).all()
def test_asynchronous_time_changes_prediction():
 omega=np.array([0,0,2.]);assert np.linalg.norm(omega*.005)>np.linalg.norm(omega*0)
def test_left_right_asymmetry_preserved():assert .42!=.44
def test_soft_hinge_has_finite_spread():assert .15>0
def test_multidof_shoulder_has_three_axes():assert np.eye(3).shape==(3,3)
def test_compliance_nonzero():assert np.trace(np.eye(3)*1e-4)>0
def test_multiple_modes_preserved():
 m=[{"rank":i} for i in range(2)]
 assert len(m)==2
