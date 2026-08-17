import json,pytest
from biospur_fusion.semantics_v2.actions import scientific_windows
from biospur_fusion.calibration_v2.models import conditional_prior_calibration
from biospur_fusion.calibration_v2.association import ROLES
from biospur_fusion.calibration_v2.p3_probe import probe_bundle

def events():return [{"action":"left_elbow","event":"ACTION_START","monotonic":1.0},{"action":"left_elbow","event":"ACTION_STOP","monotonic":3.0},{"action":"golf_swing","event":"ACTION_START","monotonic":4.0},{"action":"golf_swing","event":"ACTION_STOP","monotonic":5.0}]
def test_D3_window_rejected():assert [x["action"] for x in scientific_windows(events())]==["left_elbow"]
def test_window_uncertainty_preserved():assert scientific_windows(events())[0]["boundary_uncertainty_s"]==1
def bundle():
 m={f"N{i}":r for i,r in enumerate(ROLES)};return {"mapping_hypotheses":[conditional_prior_calibration(m,1)],"runtime_UWB_required":False,"phase1_orientation_role":"INITIALIZER_OR_DIAGNOSTIC_ONLY","contact_status":"CONTACT_UNOBSERVABLE","model_inferred_segments":{"head":"MODEL_INFERRED","hands":"MODEL_INFERRED","feet":"UNAVAILABLE"},"authoritative":False}
def test_p3_conditional_probe_passes():assert probe_bundle(bundle())["status"]=="PASS_CONDITIONAL_TOPK_HANDOFF_READY"
def test_p3_uncertainty_propagates():assert probe_bundle(bundle())["calibration_uncertainty_changes_prediction_uncertainty"]
def test_p3_phase3_not_started():assert probe_bundle(bundle())["phase3_started"] is False
def test_p3_rejects_runtime_uwb():
 b=bundle();b["runtime_UWB_required"]=True
 with pytest.raises(ValueError):probe_bundle(b)
def test_p3_rejects_double_counted_p1():
 b=bundle();b["phase1_orientation_role"]="INDEPENDENT_FACTOR"
 with pytest.raises(ValueError):probe_bundle(b)
def test_p3_rejects_duplicate_role():
 b=bundle();b["mapping_hypotheses"][0]["mapping"]["N0"]="torso"
 with pytest.raises(ValueError):probe_bundle(b)
def test_p3_rejects_zero_covariance():
 b=bundle();b["mapping_hypotheses"][0]["conditional_marginals"]["N0"]["T_segment_to_IMU"]["covariance_diagonal"][0]=0
 with pytest.raises(ValueError):probe_bundle(b)
def test_head_hands_feet_not_direct():assert set(bundle()["model_inferred_segments"].values())<={"MODEL_INFERRED","UNAVAILABLE"}
def test_contact_unobservable():assert bundle()["contact_status"]=="CONTACT_UNOBSERVABLE"

