import ast
import copy
import json
from pathlib import Path

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_preview_v0.common_time import CommonTimeline,build_common_timeline
from biospur_fusion.imu_preview_v0.core import (
    EXPECTED_INITIAL,EXPECTED_TPOSE,SEGMENTS,continuous_replay,
    skeleton_from_directions,
)
from biospur_fusion.imu_preview_v0.io import canonical_json_bytes,load_calibration_ledger
from biospur_fusion.imu_preview_v0.q2 import Q2Result
from biospur_fusion.imu_preview_v0.revision_c import (
    CalibrationProblem, make_synthetic_problem, negative_oracle_harness_test,
    objective_oracle_compare, timestamp_shift_negative_control,
    write_checkpoint_atomic,
)

ROOT=Path(__file__).resolve().parents[3]
GATES_PATH=ROOT/"Fusion_Part/config/imu_relative_orientation_preview_v0/gates_v0.json"
TEMPLATE_PATH=ROOT/"Fusion_Part/config/generic_template_motion_demo_v1/GENERIC_ADULT_PROXY_V1.json"
GATES=json.loads(GATES_PATH.read_text());TEMPLATE=json.loads(TEMPLATE_PATH.read_text())


def _imu(times):
    dtype=np.dtype([("boot_epoch","<u2"),("global_time_ns","<i8"),("acc_raw","<i2",(3,)),("gyro_raw","<i2",(3,)),("status","u1")]);out=np.zeros(len(times),dtype=dtype);out["global_time_ns"]=times;out["acc_raw"][:,2]=2048;out["status"]=1;return out


def _q2(times,axis=(0,0,1),scale=1.0,boot=None):
    angle=np.linspace(0,.3*scale,len(times));quat=Rotation.from_rotvec(np.asarray(axis)[None]*angle[:,None]).as_quat();wxyz=quat[:,[3,0,1,2]];return Q2Result(times,np.zeros(len(times),int) if boot is None else boot,wxyz,np.tile(np.eye(3)[None]*1e-4,(len(times),1,1)),np.tile([0,0,9.80665],(len(times),1)),np.tile(np.asarray(axis)*.3,(len(times),1)),np.zeros(len(times),bool),np.ones(len(times),bool),np.zeros(len(times),bool),np.zeros(3),{})


def test_contract_has_exact_product_actions_and_only_allowed_verdicts():
    assert GATES["product"]=="IMU_RELATIVE_ORIENTATION_PREVIEW_V0"
    assert len(GATES["calibration_actions"])==11
    assert GATES["allowed_verdicts"]==["PASS_IMU_RELATIVE_ORIENTATION_PREVIEW_V0","FAIL_PREVIEW_CALIBRATION","BLOCKED_REAL_REPLAY_PIPELINE_MISSING"]
    assert set(GATES["always_sealed"])=={"walk","final_still","operator_measurements"}


def test_source_has_no_uwb_t4_anchor_or_label_triggered_state_reset():
    source_paths=list((ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0").glob("*.py"))
    for path in source_paths:
        tree=ast.parse(path.read_text());imports="\n".join(ast.unparse(n) for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom)))
        assert "uwb" not in imports.lower() and "t4" not in imports.lower()
    core=(ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py").read_text()
    assert "action_boundary_state_reset" not in core and "ankle_reanchor" not in core


def test_blank_or_forbidden_ledger_is_rejected(tmp_path):
    bad=tmp_path/"HELDOUT_TYPED_LEDGER.npz";np.savez(bad,action_windows=np.zeros(0,dtype=[("name","U32"),("start_ns","<i8"),("stop_ns","<i8")]))
    with pytest.raises(ValueError,match="calibration-only"):load_calibration_ledger(bad,GATES)


def test_allowlist_never_opens_uwb_array(tmp_path):
    names=GATES["calibration_actions"];rows=np.asarray([(name,i*2_000_000_000,(i+1)*2_000_000_000-1) for i,name in enumerate(names)],dtype=[("name","U32"),("start_ns","<i8"),("stop_ns","<i8")]);times=np.arange(0,len(names)*2_000_000_000,5_000_000,dtype=np.int64);arrays={"action_windows":rows,"uwb_forbidden":np.ones(4)};arrays.update({f"imu_{node}":_imu(times) for node in GATES["node_to_segment"]});path=tmp_path/"CALIBRATION_TYPED_LEDGER.npz";np.savez(path,**arrays);_,_,audit=load_calibration_ledger(path,GATES);assert "uwb_forbidden" not in audit["opened_npz_keys"] and audit["uwb"] is False


def test_per_node_slerp_does_not_reuse_another_node_index():
    a=np.arange(0,1_000_000_001,10_000_000,dtype=np.int64);b=np.arange(3_000_000,1_003_000_001,10_000_000,dtype=np.int64);q={"A":_q2(a,(1,0,0),1),"B":_q2(b,(0,1,0),2)};timeline=build_common_timeline(q,20_000_000,980_000_000,{"rate_hz":50,"maximum_bracket_gap_s":.03,"require_same_boot_epoch":True});rv_a=Rotation.from_matrix(timeline.rotation[:,0]).as_rotvec();rv_b=Rotation.from_matrix(timeline.rotation[:,1]).as_rotvec();assert np.max(np.abs(rv_a[:,1:]))<1e-8 and np.max(np.abs(rv_b[:,[0,2]]))<1e-8;assert timeline.accounting["nodes"]["A"]["grid_accounting_closed"] and timeline.accounting["nodes"]["B"]["grid_accounting_closed"]


def test_gap_and_boot_masks_propagate_and_close_accounting():
    times=np.r_[np.arange(0,101_000_000,10_000_000,dtype=np.int64),np.arange(200_000_000,301_000_000,10_000_000,dtype=np.int64)];boot=np.r_[np.zeros(11,int),np.ones(11,int)];q={"A":_q2(times,boot=boot)};timeline=build_common_timeline(q,0,300_000_000,{"rate_hz":100,"maximum_bracket_gap_s":.03,"require_same_boot_epoch":True});row=timeline.accounting["nodes"]["A"];assert row["bracket_gap_rejected"]>0 or row["clock_segment_rejected"]>0;assert row["grid_accounting_closed"]


def test_generic_lengths_are_exact_and_head_is_explicit_proxy():
    dirs=np.asarray([EXPECTED_INITIAL[s] for s in SEGMENTS]);s=skeleton_from_directions(dirs,TEMPLATE,.22);i={name:k for k,name in enumerate(("Pelvis","C7Proxy","HeadProxy","Shoulder_L","Shoulder_R","Elbow_L","Elbow_R","Wrist_L","Wrist_R","Hip_L","Hip_R","Knee_L","Knee_R","Ankle_L","Ankle_R"))};assert np.isclose(np.linalg.norm(s[i["C7Proxy"]]-s[i["Pelvis"]]),.5);assert np.isclose(np.linalg.norm(s[i["HeadProxy"]]-s[i["C7Proxy"]]),.22)


def test_label_blind_replay_has_single_continuous_state_and_fixed_root():
    times=np.arange(0,1_000_000_001,20_000_000,dtype=np.int64);nodes=tuple(sorted(GATES["node_to_segment"]));rotation=np.tile(np.eye(3),(len(times),len(nodes),1,1));timeline=CommonTimeline(times,nodes,rotation,np.zeros((len(times),len(nodes),3)),np.tile([0,0,9.8],(len(times),len(nodes),1)),np.zeros((len(times),len(nodes)),np.bool_),np.ones((len(times),len(nodes)),np.bool_),np.ones(len(times),np.bool_),{})
    calibration={"yaw_drift_knot_global_time_ns":[0,1_000_000_000],"segments":{s:{"board_frame_longitudinal_axis":EXPECTED_INITIAL[s].tolist(),"relative_heading_rad":0.,"yaw_drift_knot_rad":[0.,0.]} for s in SEGMENTS}}
    out=continuous_replay(timeline,GATES,calibration,TEMPLATE);assert np.isfinite(out["skeleton_m"]).all();assert np.all(out["skeleton_m"][:,0]==0)


def test_audit_writer_rejects_unexpected_nonfinite():
    with pytest.raises(ValueError,match="Out of range float values"):
        canonical_json_bytes({"diagnostic":float("nan")})


def test_revision_c_validity_masks_are_input_derived_and_factor_specific():
    problem,_=make_synthetic_problem(GATES);timeline=copy.deepcopy(problem.timeline)
    torso_node=timeline.node_order.index("BSF31CC");timeline.valid[5,torso_node]=False;timeline.all_nodes_valid=np.all(timeline.valid,axis=1)
    revised=CalibrationProblem(timeline,problem.windows,GATES);audit=revised.validity_audit()
    torso=next(x for x in audit["nodes"] if x["node"]=="BSF31CC")
    assert torso["invalid_rows"]==1 and audit["whole_skeleton"]["invalid_rows"]==1
    # The invalid torso row must not reduce an unrelated forearm factor.
    initial={x["factor_id"]:x for x in audit["factors"] if x["action"]=="initial_still_attempt2"}
    assert initial["static:forearm_L:initial_still_attempt2"]["valid_rows"]==100


def test_slow_oracle_and_negative_mutation_harness():
    problem,_=make_synthetic_problem(GATES);direction=np.ones(len(problem.x0));comparison=objective_oracle_compare(problem,problem.x0,[direction],1e-12,1e-12)
    assert comparison["pass"] and comparison["residual_max_abs_difference"]==0.0
    assert negative_oracle_harness_test(problem)["pass"]


def test_checkpoint_crash_before_rename_preserves_no_authoritative_target(tmp_path):
    target=tmp_path/"START_0_TERMINAL"
    with pytest.raises(RuntimeError,match="INJECTED_CHECKPOINT_CRASH"):
        write_checkpoint_atomic(target,{"fixture":True},{"x":np.arange(4.)},crash_inject=True)
    assert not target.exists() and list(tmp_path.glob("START_0_TERMINAL.tmp.*"))


def test_mask_preserving_timestamp_shift_uses_identical_support():
    problem,windows=make_synthetic_problem(GATES);axes=np.asarray([EXPECTED_INITIAL[s] for s in SEGMENTS]);calibration={"yaw_drift_knot_global_time_ns":problem.knot_times.tolist(),"segments":{segment:{"board_frame_longitudinal_axis":axes[k].tolist(),"relative_heading_rad":0.,"yaw_drift_knot_rad":[0.]*len(problem.knot_times)} for k,segment in enumerate(SEGMENTS)}}
    audit=timestamp_shift_negative_control(problem.timeline,windows,GATES,calibration)
    assert all(row.get("support_identical",True) for row in audit["lags"])
    assert all(row.get("source_to_target_mapping_sha256") for row in audit["lags"] if row["status"]=="EVALUATED")


@pytest.mark.parametrize("path,key",[("common_time","minimum_all_node_valid_fraction"),("calibration_solver","maximum_optimality"),("preview_gates","maximum_fixed_bone_length_error_m")])
def test_declared_gate_is_consumed_by_execution_source(path,key):
    sources="\n".join(p.read_text() for p in (ROOT/"Fusion_Part/src/biospur_fusion/imu_preview_v0").glob("*.py"));assert key in sources
