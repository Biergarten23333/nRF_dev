import ast
import copy
import hashlib
import inspect
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_mocap.baseline_v1 import (
    EXPECTED_TPOSE,
    FROZEN_NODE_TO_SEGMENT,
    LANDMARK_INDEX,
    LANDMARKS,
    SEGMENT_ORDER,
    _bone_errors,
    _fit_dual_static_axis,
    _signed_angle_about,
    _pose_reference_render_indices,
    estimate_temporal_skeleton,
    evaluate_static_stability,
    load_imu_only_ledger,
    run_analysis,
    skeleton_from_state,
    validate_frozen_mapping,
)
from biospur_fusion.imu_mocap.q2_frontend import Q2Result, run_q2_frontend


ROOT=Path(__file__).resolve().parents[3]
CFG=ROOT/"Fusion_Part/config/imu_only_mocap_baseline_v1/gates_v1.json"
TEMPLATE_PATH=ROOT/"Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json"
GATES=json.loads(CFG.read_text())
TEMPLATE=json.loads(TEMPLATE_PATH.read_text())


def _imu(times_ns, gyro=(0,0,0), accel=(0,0,2048)):
    dtype=np.dtype([
        ("global_time_ns","<i8"),("acc_raw","<i2",(3,)),
        ("gyro_raw","<i2",(3,)),("status","u1")
    ])
    result=np.zeros(len(times_ns),dtype=dtype)
    result["global_time_ns"]=times_ns
    result["acc_raw"]=accel
    result["gyro_raw"]=gyro
    result["status"]=1
    return result


def _q2_series(times, phase):
    seconds=(times-times[0])/1e9
    angle=.35*np.sin(2*np.pi*.35*seconds+phase)
    xyzw=Rotation.from_rotvec(np.c_[np.zeros(len(times)),angle,np.zeros(len(times))]).as_quat()
    q=np.c_[xyzw[:,3],xyzw[:,:3]]
    omega=np.c_[np.zeros(len(times)),np.gradient(angle,seconds),np.zeros(len(times))]
    return Q2Result(
        times,q,np.tile(np.eye(3)[None]*1e-4,(len(times),1,1)),omega,
        np.ones(len(times),bool),np.ones(len(times),bool),np.zeros(len(times),bool),
        np.zeros(3),{"initial_still_attitude_drift_rad":0.0},
    )


def _synthetic_temporal_inputs():
    times=np.arange(0,8_000_000_001,5_000_000,dtype=np.int64)
    windows={
        "initial_still_attempt2":(0,1_500_000_000),"t_pose":(2_000_000_000,3_500_000_000),
        "arms":(4_000_000_000,5_500_000_000),"squats":(6_000_000_000,7_500_000_000),
    }
    q2={node:_q2_series(times,.11*k) for k,node in enumerate(sorted(FROZEN_NODE_TO_SEGMENT))}
    local={segment:EXPECTED_TPOSE[segment].copy() for segment in SEGMENT_ORDER}
    functional={name:{"pass":True,"parent_axis_sensor_frame":[1.,0.,0.],"child_axis_sensor_frame":[1.,0.,0.]} for name in
                ("left_elbow","right_elbow_attempt2","left_knee","right_knee")}
    segments={segment:{"per_sensor_gravity_frame_yaw_correction_rad":0.} for segment in SEGMENT_ORDER}
    elbows={side:{"hinge_axis_parent_sensor_frame":[1.,0.,0.],"zero_extension_offset_rad":0.,"flexion_sign":1} for side in ("left","right")}
    calibration={"functional_axes":functional,"segments":segments,"elbow_zero_and_sign":elbows}
    return q2,windows,local,calibration


def _compact_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_config_is_predeclared_strict_imu_only_and_sealed():
    assert GATES["declared_before_real_capture_execution"] is True
    assert GATES["sealed"]=={"walk":True,"final_still":True,"operator_measurements":True}
    assert set(GATES["allowed_npz_keys"])=={"action_windows",*(f"imu_{n}" for n in FROZEN_NODE_TO_SEGMENT)}
    assert GATES["forbidden_modalities"]==["UWB","UWB_TAG_T4","UWB_COVARIANCE","ANCHOR_GEOMETRY"]


def test_source_has_no_uwb_frontend_or_t4_call():
    paths=[ROOT/"Fusion_Part/src/biospur_fusion/imu_mocap/q2_frontend.py",ROOT/"Fusion_Part/src/biospur_fusion/imu_mocap/baseline_v1.py"]
    for path in paths:
        tree=ast.parse(path.read_text())
        imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        calls=[n.func.id for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        assert not any("uwb" in ast.unparse(n).lower() for n in imports)
        assert "_solve_t4" not in calls


def test_allowlist_does_not_open_extra_uwb_key(tmp_path):
    windows=np.array([("initial_still_attempt2",0,1_000_000_000)],dtype=[("name","U32"),("start_ns","<i8"),("stop_ns","<i8")])
    arrays={"action_windows":windows,"uwb_forbidden":np.arange(10)}
    times=np.arange(0,1_000_000_001,5_000_000,dtype=np.int64)
    arrays.update({f"imu_{node}":_imu(times) for node in FROZEN_NODE_TO_SEGMENT})
    path=tmp_path/"mixed.npz";np.savez(path,**arrays)
    _,_,audit=load_imu_only_ledger(path,GATES)
    assert "uwb_forbidden" not in audit["opened_inputs"][0]["npz_keys_opened"]
    assert audit["uwb_arrays_accessed"] is False


def test_stationarity_bootstraps_large_zero_rate_offset_and_updates_bias():
    times=np.arange(0,6_000_000_001,5_000_000,dtype=np.int64)
    # 590 LSB is about 36 dps, matching the capture-day scale of the observed
    # zero-rate offset; the stationary test must operate on residual gyro.
    imus={node:_imu(times,gyro=(590,-165,-48)) for node in FROZEN_NODE_TO_SEGMENT}
    result,audit=run_q2_frontend(imus,{"initial_still_attempt2":(0,6_000_000_000)},GATES["q2_frontend"])
    assert audit["verdict"]=="PASS"
    assert all(r.audit["confirmed_initial_still_fraction"]>=.99 for r in result.values())
    assert all(r.audit["stationary_bias_updates"]>0 for r in result.values())
    assert all(r.audit["gravity_updates_accepted"]>0 for r in result.values())


def test_unbounded_gap_is_not_silently_propagated():
    times=np.r_[np.arange(0,1_000_000_001,5_000_000,dtype=np.int64),2_000_000_000]
    imus={node:_imu(times) for node in FROZEN_NODE_TO_SEGMENT}
    result,_=run_q2_frontend(imus,{"initial_still_attempt2":(0,1_000_000_000)},GATES["q2_frontend"])
    assert all(r.audit["gap_boundaries"]==1 and r.gap_boundary[-1] for r in result.values())


def test_frozen_identity_rejects_left_right_swap():
    altered=copy.deepcopy(GATES)
    altered["node_to_segment"]["BSFAA61"],altered["node_to_segment"]["BSF1120"]=(
        altered["node_to_segment"]["BSF1120"],altered["node_to_segment"]["BSFAA61"])
    with pytest.raises(ValueError,match="identity mismatch"):
        validate_frozen_mapping(altered)


def test_known_single_segment_rotation_moves_only_expected_subtree_and_lengths_stay_fixed():
    base=np.array([EXPECTED_TPOSE[s] for s in SEGMENT_ORDER])
    s0=skeleton_from_state(np.zeros(3),base,TEMPLATE)
    moved=base.copy();moved[SEGMENT_ORDER.index("forearm_L")]=Rotation.from_euler("z",35,degrees=True).apply(moved[SEGMENT_ORDER.index("forearm_L")])
    s1=skeleton_from_state(np.zeros(3),moved,TEMPLATE)
    changed={LANDMARKS[i] for i in np.flatnonzero(np.linalg.norm(s1-s0,axis=1)>1e-9)}
    assert changed=={"Wrist_L"}
    _,error=_bone_errors(np.stack([s0,s1]),TEMPLATE)
    assert error<=1e-12


def test_dual_static_fit_keeps_initial_and_tpose_distinct_and_is_initial_reference_sensitive():
    initial=np.tile(np.eye(3)[None],(40,1,1))
    tpose=np.tile(Rotation.from_euler("y",-90,degrees=True).as_matrix()[None],(40,1,1))
    down=np.array([0.,0.,-1.]);left=np.array([-1.,0.,0.])
    local,yaw,initial_error,tpose_error=_fit_dual_static_axis(initial,tpose,down,left,.25)
    assert initial_error<1e-8 and tpose_error<1e-8
    changed=np.array([0.,-1.,0.])
    local_changed,yaw_changed,_,_=_fit_dual_static_axis(initial,tpose,changed,left,.25)
    reconstructed=np.array([np.cos(yaw),np.sin(yaw),0.])  # explicit gauge witness
    reconstructed_changed=np.array([np.cos(yaw_changed),np.sin(yaw_changed),0.])
    assert not np.allclose(local,local_changed) or not np.allclose(reconstructed,reconstructed_changed)


def test_hinge_zero_changes_absolute_forearm_pose_without_changing_axis():
    parent=np.array([0.,0.,-1.]);forearm=np.array([0.,0.,-1.]);axis=np.array([0.,1.,0.]);zero=np.deg2rad(12.)
    corrected=Rotation.from_rotvec(-zero*axis).apply(forearm)
    assert not np.allclose(corrected,forearm)
    assert np.allclose(axis,np.array([0.,1.,0.]))
    assert np.isclose(_signed_angle_about(parent[None],corrected[None],axis[None])[0],-zero)


def test_continuous_estimator_does_not_reset_at_action_boundaries():
    q2,windows,local,calibration=_synthetic_temporal_inputs()
    arrays,audit=estimate_temporal_skeleton(q2,windows,local,calibration,GATES,TEMPLATE)
    assert audit["timeline"]["initialization_count"]==1
    assert audit["timeline"]["action_boundary_state_resets"]==0
    assert np.count_nonzero(arrays["state_status"]=="INITIALIZED_ONCE")==1
    for action,(start,_) in windows.items():
        index=np.searchsorted(arrays["time_ns"],start)
        if index:assert arrays["state_status"][index]=="CONTINUOUS",action


def test_arms_pose_reference_clip_includes_three_second_lead_in_without_reset():
    start=10_000_000_000;times=np.arange(0,20_000_000_001,10_000_000,dtype=np.int64);arrays={"time_ns":times};windows={"initial_still_attempt2":(0,2_000_000_000),"t_pose":(3_000_000_000,5_000_000_000),"arms":(start,18_000_000_000)}
    indices=_pose_reference_render_indices("ARMS_WITH_LEAD_IN",arrays,windows,GATES)
    assert times[indices[0]]==start-3_000_000_000
    assert times[indices[-1]]==start+8_000_000_000


def test_pose_reference_renderer_requires_requested_overlays_and_no_uwb():
    source=(ROOT/"Fusion_Part/src/biospur_fusion/imu_mocap/baseline_v1.py").read_text()
    for token in ("global_time_ns=","action=","state=","elbow consistency:","IMU-only non-clinical preview"):
        assert token in source
    assert '"uwb_dots":False' in source


def test_each_temporal_parameter_changes_state_or_tested_decision():
    q2,windows,local,calibration=_synthetic_temporal_inputs()
    base,_=estimate_temporal_skeleton(q2,windows,local,calibration,GATES,TEMPLATE)
    base_digest=hashlib.sha256(base["skeleton_m"].tobytes()+base["segment_angular_velocity_rad_s"].tobytes()).hexdigest()
    replacements={
        "state_rate_hz":80.,"orientation_innovation_gain":.65,"angular_velocity_innovation_gain":.5,
        "angular_velocity_damping":.5,"stationary_angular_velocity_gain":.9,
        "angular_acceleration_limit_rad_s2":1.,"hinge_axis_regularization_gain":.8,
        "root_contact_position_gain":.8,"root_velocity_gain":.7,"root_velocity_damping":.4,
        "maximum_adjacent_segment_rotation_deg":.001,"broad_elbow_angle_deg":[100.,110.],
        "broad_knee_angle_deg":[100.,110.],"broad_ball_joint_cone_deg":20.,
    }
    source=inspect.getsource(estimate_temporal_skeleton)
    for key,value in replacements.items():
        assert f'cfg["{key}"]' in source
        altered=copy.deepcopy(GATES);altered["temporal_estimator"][key]=value
        candidate,audit=estimate_temporal_skeleton(q2,windows,local,calibration,altered,TEMPLATE)
        digest=hashlib.sha256(candidate["skeleton_m"].tobytes()+candidate["segment_angular_velocity_rad_s"].tobytes()).hexdigest()
        assert digest!=base_digest or audit!=estimate_temporal_skeleton(q2,windows,local,calibration,GATES,TEMPLATE)[1],key


def _stable_static_arrays():
    hz=100;n0=601;n1=401
    action=np.array(["initial_still_attempt2"]*n0+["t_pose"]*n1,dtype="U32")
    times=np.r_[np.arange(n0,dtype=np.int64)*10_000_000,7_000_000_000+np.arange(n1,dtype=np.int64)*10_000_000]
    dirs=np.tile(np.array([EXPECTED_TPOSE[s] for s in SEGMENT_ORDER])[None],(len(action),1,1))
    root=np.zeros((len(action),3));s=np.stack([skeleton_from_state(r,d,TEMPLATE) for r,d in zip(root,dirs)])
    return {"action":action,"time_ns":times,"segment_direction":dirs,"root_m":root,"skeleton_m":s,"stationary_fraction":np.ones(len(action))}


@pytest.mark.parametrize("key",[
    "initial_still_minimum_continuous_stationary_s","initial_still_root_p95_displacement_m",
    "initial_still_joint_relative_p95_displacement_m","initial_still_segment_axis_p95_deviation_deg",
    "tpose_joint_relative_p95_displacement_m","tpose_segment_axis_p95_deviation_deg",
    "tpose_joint_velocity_p95_mps","maximum_bone_length_error_m",
])
def test_every_static_gate_can_change_decision(key):
    arrays=_stable_static_arrays();g=copy.deepcopy(GATES)
    if key=="initial_still_minimum_continuous_stationary_s":g["stability_gates"][key]=6.1
    elif key=="maximum_bone_length_error_m":g["stability_gates"][key]=-1e-12
    else:g["stability_gates"][key]=-1e-12
    assert evaluate_static_stability(arrays,g,TEMPLATE)["pass"] is False


def test_uwb_delete_or_replace_leaves_compact_phase_a_result_identical(tmp_path):
    names=["initial_still_attempt2","t_pose","arms","squats","left_elbow","right_elbow_attempt2","left_knee","right_knee"]
    lengths=[6,4,3,3,2,2,2,2];rows=[];cursor=0
    for name,length in zip(names,lengths):
        rows.append((name,cursor*1_000_000_000,(cursor+length)*1_000_000_000));cursor+=length+1
    windows=np.array(rows,dtype=[("name","U32"),("start_ns","<i8"),("stop_ns","<i8")])
    times=np.arange(0,(cursor+1)*1_000_000_000,5_000_000,dtype=np.int64)
    common={"action_windows":windows,**{f"imu_{node}":_imu(times) for node in FROZEN_NODE_TO_SEGMENT}}
    p1=tmp_path/"with_uwb_a.npz";p2=tmp_path/"with_uwb_b.npz"
    np.savez(p1,**common,uwb_positions=np.ones((3,3)))
    np.savez(p2,**common,uwb_positions=np.full((3,3),999.))
    o1=tmp_path/"a";o2=tmp_path/"b"
    run_analysis(p1,TEMPLATE_PATH,CFG,o1);run_analysis(p2,TEMPLATE_PATH,CFG,o2)
    names=["IMU_FRONTEND_AUDIT.json","SENSOR_TO_SEGMENT_CALIBRATION.json","STATIC_STABILITY.json","ACTION_SMOKE_GATES.json","TEMPORAL_ESTIMATOR_AUDIT.json","ABLATION_RESULTS.json","IMU_ONLY_STATE_TIMELINE.npz"]
    assert {name:_compact_sha(o1/name) for name in names}=={name:_compact_sha(o2/name) for name in names}
