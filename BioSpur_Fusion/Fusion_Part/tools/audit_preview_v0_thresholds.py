#!/usr/bin/env python3
"""Inventory every V0 gate leaf and source-only assumption at an exact commit."""
from __future__ import annotations

import argparse,hashlib,json,subprocess
from pathlib import Path

CATEGORIES={"HARD_NUMERICAL_INVARIANT","DATA_VALIDITY_GATE","DISPLAY_GAUGE","HUMAN_PROBABILISTIC_LIKELIHOOD","MODEL_MISMATCH_DIAGNOSTIC","INVALID_ROBOTIC_ASSUMPTION","DECLARED_BUT_UNUSED","TAUTOLOGICAL_TEST"}
RUNTIME_PATHS=(
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/io.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/q2.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/common_time.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/pipeline.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/renderer.py",
 "Fusion_Part/src/biospur_fusion/imu_preview_v0/revision_c.py",
 "Fusion_Part/tools/run_imu_relative_orientation_preview_v0.py",
)


def git_show(commit:str,path:str)->str:
    return subprocess.check_output(["git","show",f"{commit}:BioSpur_Fusion/{path}"],text=True)


def flatten(value,pointer=""):
    if isinstance(value,dict):
        for key,item in value.items():yield from flatten(item,f"{pointer}/{key}")
    else:yield pointer,value


def classify(pointer:str,refs:int)->tuple[str,str]:
    if refs==0:return "DECLARED_BUT_UNUSED","No runtime source reference at the audited commit."
    if pointer.startswith(("/allowed_","/forbidden_","/sealed_","/always_sealed","/validity_contract")) or pointer in ("/common_time/maximum_bracket_gap_s","/common_time/require_same_boot_epoch","/common_time/minimum_all_node_valid_fraction","/q2/maximum_propagated_gap_s"):return "DATA_VALIDITY_GATE","Transport/modality/timestamp validity, not a human-pose requirement."
    if pointer.startswith("/node_to_segment"):return "HARD_NUMERICAL_INVARIANT","Frozen left/right node identity."
    if pointer.startswith("/template") or pointer in ("/preview_gates/maximum_fixed_bone_length_error_m","/determinism/isolated_reload_required"):return "HARD_NUMERICAL_INVARIANT","Serialization/template/program integrity."
    if pointer in ("/q2/yaw_sigma_deg","/calibration_solver/first_yaw_drift_knot_fixed_rad"):return "DISPLAY_GAUGE","Unobserved global/display heading convention."
    if pointer.startswith("/q2/"):
        invalid=("stationary_gyro_limit","stationary_accel_norm","stationary_filtered_jerk","multi_node_agreement","minimum_stationary_duration","minimum_initial_still_eligible","stationary_gravity_correction","dynamic_gravity","stationary_bias_update")
        if any(token in pointer for token in invalid):return "INVALID_ROBOTIC_ASSUMPTION","Binary robotic-still/dynamic classification is invalid for natural wearable motion."
        if any(token in pointer for token in ("lsb_per","gravity_mps2")):return "HARD_NUMERICAL_INVARIANT","Sensor conversion or physical unit constant."
        return "HUMAN_PROBABILISTIC_LIKELIHOOD","Noise/filter/uncertainty model parameter."
    if pointer=="/preview_gates/minimum_relevant_segment_motion_deg":return "INVALID_ROBOTIC_ASSUMPTION","Takes a minimum over related absolute segment motions instead of relative joint excitation."
    if pointer=="/preview_gates/maximum_boundary_segment_step_deg":return "INVALID_ROBOTIC_ASSUMPTION","Uniform absolute step gate ignores gyro-predicted human motion and covariance."
    if pointer=="/preview_gates/left_right_swap_must_fail_identity_gate":return "TAUTOLOGICAL_TEST","Runtime test compares mapping dictionaries rather than replaying swapped identity."
    if pointer in ("/preview_gates/maximum_return_to_neutral_axis_error_deg","/preview_gates/maximum_pronation_centerline_displacement_m"):return "DECLARED_BUT_UNUSED","Named gate has no effective runtime decision."
    if pointer.startswith("/preview_gates/"):return "MODEL_MISMATCH_DIAGNOSTIC","Output/model mismatch diagnostic; must not require robotic symmetry."
    if pointer.startswith("/rendering/"):return "DISPLAY_GAUGE","Display/rendering convention or media encoding."
    if pointer.startswith("/common_time/"):return "DATA_VALIDITY_GATE","Common timestamp association contract."
    if pointer.startswith("/solver_qualification/") or pointer.startswith("/determinism/"):return "HARD_NUMERICAL_INVARIANT","Numerical equivalence/determinism qualification."
    if pointer.startswith("/calibration_solver/"):
        if any(token in pointer for token in ("sigma","loss","f_scale","bias_bound","knot_spacing")):return "HUMAN_PROBABILISTIC_LIKELIHOOD","Optimization likelihood/prior/model scale."
        if any(token in pointer for token in ("multistart","restart","optimality")):return "MODEL_MISMATCH_DIAGNOSTIC","Optimizer/output repeatability diagnostic."
        return "HARD_NUMERICAL_INVARIANT","Solver execution or parameterization contract."
    if pointer.startswith(("/claims_forbidden","/calibration_actions")):return "HARD_NUMERICAL_INVARIANT","Product boundary/action inventory."
    return "HARD_NUMERICAL_INVARIANT","Product schema or frozen execution contract."


SOURCE_ASSUMPTIONS=(
 ("EXPECTED_INITIAL_AS_FRAMEWISE_TRUTH","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","EXPECTED_INITIAL","INVALID_ROBOTIC_ASSUMPTION","Ideal initial vectors are applied to uniformly sampled frames instead of a latent pose with covariance."),
 ("EXPECTED_TPOSE_AS_FRAMEWISE_TRUTH","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","EXPECTED_TPOSE","INVALID_ROBOTIC_ASSUMPTION","Ideal T-pose vectors are applied to uniformly sampled frames instead of a latent pose with covariance."),
 ("SQUAT_LEFT_RIGHT_Z_EQUALITY","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","pair[0][:,2]-pair[1][:,2]","INVALID_ROBOTIC_ASSUMPTION","Framewise mirrored Z is not human bilateral phase consistency."),
 ("FUNCTIONAL_ACTIONS_POSTHOC_PCA_ONLY","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","derive_functional_parameters","DECLARED_BUT_UNUSED","Functional parameters are derived after fitting and are not consumed by continuous replay."),
 ("ELBOW_PHASE_SPLIT_AT_HALF_WINDOW","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","rows[:max(1,len(rows)//2)]","INVALID_ROBOTIC_ASSUMPTION","Curl/pronation is split by time half rather than signal content."),
 ("FUNCTIONAL_PCA_EIGEN_RATIO_UNGATED","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","eigen_ratio","DECLARED_BUT_UNUSED","Reported PCA diagnostic does not execute a calibration decision."),
 ("UNBOUNDED_LABEL_BLIND_INTERPOLATION","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","np.interp","INVALID_ROBOTIC_ASSUMPTION","Interpolation crosses arbitrary invalid spans without a maximum time gap."),
 ("ABSOLUTE_MINIMUM_SEGMENT_MOTION_GATE","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","minimum_relevant_segment_motion_deg","INVALID_ROBOTIC_ASSUMPTION","Minimum absolute motion across proximal/distal segments rejects valid isolated joint excitation."),
 ("BOUNDARY_ABSOLUTE_EIGHT_DEG_GATE","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","maximum_boundary_segment_step_deg","INVALID_ROBOTIC_ASSUMPTION","Observed step is not compared with gyro-predicted step/covariance."),
 ("LEFT_RIGHT_SWAP_DICTIONARY_INEQUALITY","Fusion_Part/src/biospur_fusion/imu_preview_v0/core.py","identity_failed=swapped!=gates","TAUTOLOGICAL_TEST","Dictionary inequality does not prove swapped replay identity failure."),
)


def main()->int:
    ap=argparse.ArgumentParser();ap.add_argument("--commit",required=True);ap.add_argument("--output",type=Path,required=True);args=ap.parse_args();commit=subprocess.check_output(["git","rev-parse",f"{args.commit}^{{commit}}"],text=True).strip();config_path="Fusion_Part/config/imu_relative_orientation_preview_v0/gates_v0.json";config_text=git_show(commit,config_path);config=json.loads(config_text);runtime={path:git_show(commit,path) for path in RUNTIME_PATHS};items=[]
    for pointer,value in flatten(config):
        key=pointer.rsplit("/",1)[-1];locations=[]
        for path,text in runtime.items():
            for line_no,line in enumerate(text.splitlines(),1):
                count=line.count(f'"{key}"')
                locations.extend({"path":path,"line":line_no,"occurrence":ordinal+1} for ordinal in range(count))
        category,rationale=classify(pointer,len(locations));config_lines=[i for i,line in enumerate(config_text.splitlines(),1) if f'"{key}"' in line]
        items.append({"id":pointer,"origin":"GATE_JSON_LEAF","value":value,"classification":category,"rationale":rationale,"declaration":{"path":config_path,"lines":config_lines},"runtime_reference_count":len(locations),"runtime_references":locations,"actual_execution_status":"DECLARED_NOT_EXECUTED" if not locations else "EXECUTED_IN_BASELINE_RUNTIME"})
    for identifier,path,needle,category,rationale in SOURCE_ASSUMPTIONS:
        text=runtime[path];locations=[{"path":path,"line":i} for i,line in enumerate(text.splitlines(),1) if needle in line];items.append({"id":identifier,"origin":"SOURCE_MODEL_ASSUMPTION","value":needle,"classification":category,"rationale":rationale,"declaration":locations[0] if locations else {"path":path,"line":None},"runtime_reference_count":len(locations),"runtime_references":locations,"actual_execution_status":"EXECUTED_IN_BASELINE_RUNTIME" if locations else "SOURCE_PATTERN_NOT_FOUND"})
    assert all(item["classification"] in CATEGORIES for item in items);counts={category:sum(item["classification"]==category for item in items) for category in sorted(CATEGORIES)};result={"schema":"biospur-threshold-and-human-assumption-inventory-v1","audited_commit":commit,"audited_config_path":config_path,"audited_config_sha256":hashlib.sha256(config_text.encode()).hexdigest(),"runtime_paths":list(RUNTIME_PATHS),"item_count":len(items),"classification_counts":counts,"items":items};args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,sort_keys=True,separators=(",",":"),ensure_ascii=False)+"\n");return 0


if __name__=="__main__":raise SystemExit(main())
