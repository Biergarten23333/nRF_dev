import json
from pathlib import Path

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_engineering_v1.phase_a import (
    EXPECTED_MAPPING,_best_two_pose_axis,add_pose_dispersion_gate,compatibility,
)
from biospur_fusion.imu_multi_action_engineering_v1.q2 import (
    estimate_bias_and_gravity,prepare_quasi_static,
)
from biospur_fusion.imu_multi_action_engineering_v1.model import (
    SEGMENTS,_decode,angles_to_axis,axis_to_tangent,migrate_spherical_latent_checkpoint,s2_log,tangent_to_axis,
)
from biospur_fusion.imu_multi_action_engineering_v1.segmentation import _returns,_smooth

ROOT=Path(__file__).resolve().parents[3]
GATES=json.loads((ROOT/"Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json").read_text())


def _imu(seconds=12.,bias_dps=(.5,-.2,.1),motion=None,gravity=(0.,0.,1.),offset_ns=0):
    n=int(seconds*200);t=np.arange(n)/200.;dtype=np.dtype([("status","u1"),("global_time_ns","i8"),("boot_epoch","i4"),("acc_raw","i2",(3,)),("gyro_raw","i2",(3,))]);out=np.zeros(n,dtype=dtype);out["status"]=1;out["global_time_ns"]=offset_ns+(t*1e9).astype(np.int64);out["boot_epoch"]=1
    breathing=.015*np.sin(2*np.pi*.25*t);accel=np.tile(np.asarray(gravity,float),(n,1));accel[:,0]+=.01*np.sin(2*np.pi*.18*t);accel[:,1]+=breathing;gyro=np.tile(np.asarray(bias_dps,float),(n,1));gyro[:,0]+=1.5*np.sin(2*np.pi*.25*t);gyro[:,1]+=.4*np.sin(2*np.pi*.18*t)
    if motion is not None:motion(gyro,t)
    out["acc_raw"]=np.rint(accel*2048).astype(np.int16);out["gyro_raw"]=np.rint(gyro*16.384).astype(np.int16);return out


def _windows(offset=0):return {"initial_still_attempt2":(offset,offset+5_000_000_000),"t_pose":(offset+6_000_000_000,offset+11_000_000_000)}


def test_product_has_exact_eleven_actions_and_frozen_mapping():
    assert len(GATES["calibration_actions"])==11
    assert GATES["node_to_segment"]==EXPECTED_MAPPING
    assert set(GATES["allowed_npz_keys"])=={"action_windows",*(f"imu_{node}" for node in EXPECTED_MAPPING)}


def test_solver_cannot_stop_on_ftol_before_outer_optimality_gate():
    solver=GATES["calibration_solver"]
    assert solver["ftol"] is None and solver["xtol"] is None
    assert solver["gtol"]<=solver["maximum_optimality"]
    assert solver["lsmr_atol"]<=1e-12 and solver["lsmr_btol"]<=1e-12


def test_natural_human_quasi_static_motion_retains_continuous_evidence():
    p=prepare_quasi_static({"A":_imu()},GATES["q2"],_windows()["initial_still_attempt2"])["A"]
    assert 0.<p.quasi_static_weight.min()<1.
    assert p.quasi_static_weight.mean()>.45


def test_one_moving_node_cannot_zero_another_nodes_weight():
    def sustained(gyro,t):gyro[(t>2)&(t<5),2]+=60.
    prepared=prepare_quasi_static({"quiet":_imu(),"moving":_imu(motion=sustained)},GATES["q2"],_windows()["initial_still_attempt2"])
    active=(prepared["quiet"].time_ns>2_000_000_000)&(prepared["quiet"].time_ns<5_000_000_000)
    assert np.median(prepared["quiet"].quasi_static_weight[active])>.35
    assert np.median(prepared["moving"].quasi_static_weight[active])<np.median(prepared["quiet"].quasi_static_weight[active])


def test_sustained_action_has_lower_confidence_than_natural_sway():
    def sustained(gyro,t):gyro[(t>2)&(t<5),0]+=80.
    quiet=prepare_quasi_static({"n":_imu()},GATES["q2"],_windows()["initial_still_attempt2"])["n"]
    moving=prepare_quasi_static({"n":_imu(motion=sustained)},GATES["q2"],_windows()["initial_still_attempt2"])["n"]
    active=(moving.time_ns>2_000_000_000)&(moving.time_ns<5_000_000_000)
    assert np.median(moving.quasi_static_weight[active])<.5*np.median(quiet.quasi_static_weight[active])


def test_bias_and_gravity_are_robust_with_explicit_uncertainty():
    prepared=prepare_quasi_static({"n":_imu()},GATES["q2"],_windows()["initial_still_attempt2"]);estimate=estimate_bias_and_gravity(prepared,_windows(),GATES["q2"])["n"]
    assert np.max(np.abs(np.degrees(estimate["gyro_bias_rad_s"])-np.array([.5,-.2,.1])))<.35
    assert estimate["gyro_bias_effective_sample_size"]>=GATES["q2"]["minimum_effective_sample_size"]
    assert np.isfinite(estimate["gyro_bias_covariance_rad2_s2"]).all()
    for pose in estimate["gravity"].values():
        assert pose["effective_sample_size"]>=GATES["q2"]["minimum_effective_sample_size"]
        assert pose["angular_standard_uncertainty_deg"]<GATES["q2"]["maximum_gravity_angular_standard_uncertainty_deg"]


def test_neutral_and_tpose_gravity_references_are_not_averaged():
    imu=_imu();split=(imu["global_time_ns"]>=6_000_000_000)&(imu["global_time_ns"]<=11_000_000_000);imu["acc_raw"][split]=np.array([0,2048,0],np.int16);prepared=prepare_quasi_static({"n":imu},GATES["q2"],_windows()["initial_still_attempt2"]);estimate=estimate_bias_and_gravity(prepared,_windows(),GATES["q2"])["n"];a=np.asarray(estimate["gravity"]["initial_still_attempt2"]["gravity_direction_board"]);b=np.asarray(estimate["gravity"]["t_pose"]["gravity_direction_board"])
    assert np.degrees(np.arccos(np.clip(a@b,-1,1)))>80.


def test_two_pose_axis_recovers_consistent_static_and_tpose_geometry():
    initial=np.eye(3);tpose=np.array([[0.,0.,-1.],[0.,1.,0.],[1.,0.,0.]])
    result=_best_two_pose_axis(initial,tpose,np.array([0.,0.,-1.]),np.array([-1.,0.,0.]))
    assert result["maximum_direction_mismatch_deg"]<1e-5


def test_two_pose_axis_rejects_incompatible_observations():
    result=_best_two_pose_axis(np.eye(3),np.eye(3),np.array([0.,0.,-1.]),np.array([-1.,0.,0.]))
    assert result["maximum_direction_mismatch_deg"]>40.


def test_q2_failure_is_declared_before_any_solver_configuration():
    source=(ROOT/"Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/phase_a.py").read_text()
    assert '"nonlinear_solver_started":False' in source
    assert "least_squares" not in source


def test_golf_boxing_and_other_modalities_are_sealed_in_phase_a():
    assert GATES["data_firewall"]["golf"].startswith("SEALED")
    assert GATES["data_firewall"]["boxing"].startswith("SEALED")
    assert GATES["data_firewall"]["walk"]=="SEALED"
    assert GATES["data_firewall"]["uwb_t4_anchor"]=="SEALED"


def test_declared_pose_dispersion_gate_changes_phase_a_decision():
    class Result:
        time_ns=np.arange(20,dtype=np.int64)
        q_wxyz=np.tile(np.array([1.,0.,0.,0.]),(20,1))
    subset={"initial_still_attempt2":{},"t_pose":{},"pass":True}
    assert add_pose_dispersion_gate(subset,{"node":Result()},(0,19),(0,19),1.)["pass"]
    result=Result();result.q_wxyz=result.q_wxyz.copy();result.q_wxyz[-5:]=np.array([.99904822,.04361939,0.,0.]);subset={"initial_still_attempt2":{},"t_pose":{},"pass":True}
    assert not add_pose_dispersion_gate(subset,{"node":result},(0,19),(0,19),1.)["pass"]


def test_q2_raw_gyro_failure_has_specific_terminal_blocker(monkeypatch):
    class Result:
        time_ns=np.arange(20,dtype=np.int64)
        q_wxyz=np.tile(np.array([1.,0.,0.,0.]),(20,1))
        gyro_corrected_rad_s=np.zeros((20,3))
    monkeypatch.setattr("biospur_fusion.imu_multi_action_engineering_v1.phase_a._integrate_gyro",lambda *args:Rotation.from_rotvec([.3,0,0]).as_matrix())
    cfg={"minimum_arm_board_relative_rotation_deg":0.,"maximum_arm_board_relative_rotation_deg":180.,"maximum_non_arm_board_relative_rotation_deg":180.,"maximum_non_arm_direction_mismatch_deg":180.,"maximum_arm_direction_mismatch_deg":180.,"maximum_q2_vs_raw_gyro_relative_rotation_deg":10.}
    audit=compatibility({"node":Result()},{"node":"torso"},(0,9),(10,19),cfg)
    assert audit["verdict"]=="BLOCKED_Q2_RELATIVE_ROTATION_INCONSISTENT"


def test_no_hard_global_stationarity_veto_or_whole_window_fallback_remains():
    source=(ROOT/"Fusion_Part/src/biospur_fusion/imu_multi_action_engineering_v1/q2.py").read_text()
    assert "multi_node_agreement_fraction" not in source
    assert "minimum_initial_still_eligible_fraction" not in source
    assert "eligible.sum()<20" not in source.replace(" ","")
    assert "fallback=initial" not in source.replace(" ","")


def test_phase_row_selection_uses_global_timeline_index_domain():
    energy=np.arange(1000,dtype=float);rows=np.arange(400,500);cfg={"return_energy_quantile":.2}
    selected=_returns(rows,energy,cfg)
    assert selected.min()>=400 and selected.max()<500


def test_single_invalid_sample_does_not_poison_segmentation_smoothing_window():
    values=np.ones(100);values[50]=np.nan;smoothed=_smooth(values,10)
    assert np.isfinite(smoothed).all() and np.allclose(smoothed,1.)


def test_reference_centered_s2_chart_has_two_physical_directions_at_downward_pose():
    reference=np.array([0.,0.,-1.]);step=1e-6
    base=tangent_to_axis(reference,np.zeros(2));du=(tangent_to_axis(reference,[step,0.])-tangent_to_axis(reference,[-step,0.]))/(2*step);dv=(tangent_to_axis(reference,[0.,step])-tangent_to_axis(reference,[0.,-step]))/(2*step)
    assert np.allclose(base,reference)
    assert np.linalg.norm(du)>.999 and np.linalg.norm(dv)>.999
    assert abs(float(du@dv))<1e-10


def test_s2_chart_round_trip_preserves_pose_reference_direction():
    reference=np.array([0.,0.,-1.]);axis=np.array([.2,-.3,-.9327379053]);axis/=np.linalg.norm(axis)
    coordinates=axis_to_tangent(reference,axis)
    assert np.allclose(tangent_to_axis(reference,coordinates),axis,atol=1e-12)


def test_old_spherical_latent_checkpoint_migration_preserves_all_latent_axes():
    rng=np.random.default_rng(47);old=np.zeros(96);old[56::2]=rng.uniform(-np.pi,np.pi,20);old[57::2]=rng.uniform(-1.2,1.2,20)
    expected=[]
    for cursor in range(56,96,2):expected.append(angles_to_axis(old[cursor],old[cursor+1]))
    migrated=migrate_spherical_latent_checkpoint(old);decoded=_decode(migrated);actual=[decoded[4][pose][segment] for pose in ("initial_still_attempt2","t_pose") for segment in SEGMENTS]
    assert np.allclose(actual,expected,atol=1e-12)


def test_s2_static_residual_magnitude_is_the_geodesic_angle():
    reference=np.array([0.,0.,-1.]);direction=Rotation.from_rotvec([0.,np.deg2rad(37.),0.]).apply(reference)
    residual=s2_log(reference,direction)
    assert np.isclose(np.linalg.norm(residual),np.deg2rad(37.),atol=1e-12)
    assert np.allclose(s2_log(reference,reference),0.)


def test_s2_log_remains_resolved_at_sub_microradian_human_hold_steps():
    reference=np.array([0.,0.,-1.]);angle=1e-9;direction=Rotation.from_rotvec([0.,angle,0.]).apply(reference)
    assert np.isclose(np.linalg.norm(s2_log(reference,direction)),angle,rtol=1e-8)
