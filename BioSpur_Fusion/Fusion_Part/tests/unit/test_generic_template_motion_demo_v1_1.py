import json
from pathlib import Path
import numpy as np
from biospur_fusion.visualization.generic_motion_demo_v1_1 import _baseline_and_reject,_state_skeleton
from biospur_fusion.visualization.generic_motion_demo_v1 import LANDMARK_INDEX,NODE_ORDER

ROOT=Path(__file__).resolve().parents[3];CFG=ROOT/"Fusion_Part/config/generic_template_motion_demo_v1_1";TEMPLATE=json.loads((ROOT/"Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json").read_text());G=json.loads((CFG/"demo_gates_v1_1.json").read_text())

def test_v1_rejection_is_historical_addendum_not_artifact_rewrite():
 r=json.loads((CFG/"V1_REJECTION_AND_HISTORICAL_INTERPRETATION.json").read_text());assert r["historical_interpretation"]=={"SOFTWARE_INTEGRITY":"PASS","MOTION_FIDELITY":"NOT_TESTED","PREVIEW":"REJECTED_BY_OPERATOR"};assert r["root_cause"]["maximum_nominal_limb_uwb_direction_weight"]==.2;assert r["v1_artifacts_modified"] is False

def test_every_requested_action_including_golf_and_boxing_is_previewed():
 assert G["preview_actions"]==G["calibration_actions"] and len(G["preview_actions"])==13
 assert G["preview_actions"][-2:]==["golf_swing","boxing"]

def test_walk_final_and_operator_measurements_remain_sealed():
 assert G["heldout"]=={"walk":"SEALED","final_still":"SEALED"};assert G["operator_measurements"]=="SEALED_AND_FORBIDDEN";assert not ({"walk","final_still"}&set(G["calibration_actions"]))
 assert G["custom_action_frontends"]["walk_rows_selected"] is False and G["custom_action_frontends"]["final_still_rows_selected"] is False

def test_displacement_baseline_is_robust_initial_still_median():
 raw=np.zeros((7,len(NODE_ORDER),3));raw[:3,:,0]=np.array([1,1,100])[:,None];raw[3:,:,0]=3;actions=np.array(["initial_still_attempt2"]*3+["arms"]*4);base,delta,accepted,log=_baseline_and_reject(raw,actions,G);assert np.allclose(base[:,0],1);assert np.allclose(delta[3:,:,0],2)

def test_pre_estimation_rejection_assigns_zero_weight_log():
 raw=np.zeros((6,len(NODE_ORDER),3));raw[4,:,0]=10;actions=np.array(["initial_still_attempt2"]*3+["arms"]*3);_,_,accepted,log=_baseline_and_reject(raw,actions,G);assert not accepted[4].any();assert log and all(x["estimator_weight"]==0 for x in log)

def test_state_parameterization_preserves_all_generic_lengths():
 x=np.r_[np.zeros(3),np.tile([0,0,1.],10)];s=_state_skeleton(x,TEMPLATE);i=LANDMARK_INDEX;d=TEMPLATE["dimensions"]
 assert np.isclose(np.linalg.norm(s[i["Pelvis"]]-s[i["C7Proxy"]]),d["C7Proxy_to_PelvisProxy_m"])
 assert np.isclose(np.linalg.norm(s[i["Elbow_L"]]-s[i["Wrist_L"]]),d["rendering_forearm_length_L_m"])
 assert np.isclose(np.linalg.norm(s[i["Knee_R"]]-s[i["Ankle_R"]]),d["rendering_shank_length_R_m"])

def test_pass_requires_motion_ablation_and_preview_not_integrity_only():
 assert G["pass_requires"]==["NUMERICAL_MOTION_RESPONSIVENESS_PASS","ALL_ABLATIONS_DATA_DRIVEN","PREVIEWS_GENERATED"]
 assert G["motion_gates"]["disclosed_placement_bound_hits_allowed_to_pass"] is False
 assert G["motion_gates"]["large_residuals_allowed_to_pass"] is False

def test_source_joint_solver_contains_direct_uwb_displacement_residual():
 source=(ROOT/"Fusion_Part/src/biospur_fusion/visualization/generic_motion_demo_v1_1.py").read_text();assert "joint_trajectories_directly_constrained_by_uwb_displacements" in source;assert "target=base[LANDMARK_INDEX[landmark]]+delta" in source;assert "entire_skeleton_forward_propagated_from_imu_only\":False" in source

def test_custom_actions_use_sha_bound_t4_q1_frontends_not_empty_timeline():
 binding=G["custom_action_frontends"]
 assert binding["scope"]=="ONLY_GOLF_SWING_AND_BOXING_ROWS_SELECTED_BY_FROZEN_ACTION_METADATA"
 assert len(binding["t4_sha256"])==64 and len(binding["q1_sha256"])==64
