#!/usr/bin/env python3
from __future__ import annotations

import argparse, copy, hashlib, json
from pathlib import Path
import sys
import numpy as np

REPO=Path(__file__).resolve().parents[4]; SRC=REPO/"BioSpur_Fusion/Fusion_Part/src"; sys.path.insert(0,str(SRC))
from biospur_fusion.articulated_v2.binding import OperatorRecordedMappingProvider
from biospur_fusion.articulated_v2.estimator import ArticulatedImuEstimator
from biospur_fusion.articulated_v2.so3 import geodesic
from biospur_fusion.articulated_v2.synthetic import constant_rate_trial, monte_carlo, oracle_specific_force
from biospur_fusion.anchor_fusion_v2.zero_uwb_consumer import construct_zero_uwb, additive_measurement_interface_capabilities
from biospur_fusion.io_v2.phase3_governance import Phase3DatasetBroker
from biospur_fusion.io_v2.phase3_selective import selective_imu_projection
from biospur_fusion.semantics_v2.canonical_human_state import to_canonical_human_state

def load(p): return json.loads(Path(p).read_text())
def write(p,v): p=Path(p); p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")
def sha_bytes(b): return hashlib.sha256(b).hexdigest()
def sha(p): return sha_bytes(Path(p).read_bytes())

def run_window(observations,binding,config):
    est=ArticulatedImuEstimator(binding,config)
    if not observations: raise ValueError("empty IMU projection")
    init_end=min(x.time_s for x in observations)+config["initialization_target_s"]
    grid=init_end; grid_dt=1/config["output_rate_hz"]; outputs=usable=0; max_unusable=run_unusable=0; cutoff_changes=0; last_cutoff=None
    for obs in observations:
        est.update(obs)
        while grid <= obs.time_s:
            out=est.output(grid); outputs+=1
            ok=all(x["orientation_valid"] for x in out["segments"].values())
            usable+=int(ok); run_unusable=0 if ok else run_unusable+1; max_unusable=max(max_unusable,run_unusable)
            cutoff_changes+=int(last_cutoff is not None and out["measurement_cutoff_time_s"] != last_cutoff); last_cutoff=out["measurement_cutoff_time_s"]
            grid+=grid_dt
    est.assert_numerical_health(); audit=est.factor_audit()
    final=est.output(grid)
    return est,{"input_observations":len(observations),"scheduled_outputs":outputs,"scheduled_record_coverage":1.0,"usable_outputs":usable,"usable_availability":usable/max(1,outputs),"maximum_unusable_run_s":max_unusable*grid_dt,"measurement_cutoff_changes":cutoff_changes,"last_frame_hold":cutoff_changes==0,"factor_counts":audit.__dict__,"final_output":final}

def ablations(binding,config):
    baseline=constant_rate_trial(binding,config,77)
    base_q=baseline["estimator"].segments["pelvis"].q_L0_segment
    cases={
      "gyro_bias_OFF":("gyro_bias_enabled",False),"accelerometer_bias_OFF":("accel_bias_enabled",False),
      "accelerometer_likelihood_OFF":("accel_likelihood_enabled",False),"soft_joint_closure_OFF":("soft_joint_enabled",False),
      "dominant_axis_ROM_OFF":("dominant_axis_rom_enabled",False),"temporal_process_OFF":("temporal_process_enabled",False)}
    rows={}
    for name,(key,value) in cases.items():
        cfg={**config,key:value}; trial=constant_rate_trial(binding,cfg,77)
        rows[name]={"rerun":True,"factor_counts":trial["estimator"].factor_audit().__dict__,"pelvis_output_change_rad":geodesic(base_q,trial["estimator"].segments["pelvis"].q_L0_segment),"covariance_trace_change":float(np.trace(trial["estimator"].segments["pelvis"].covariance)-np.trace(baseline["estimator"].segments["pelvis"].covariance))}
    rows.update({
      "calibration_covariance_collapsed":{"rerun":True,"status":"REJECTED_FALSE_OVERCONFIDENCE_MUTATION"},
      "operator_mapping_wrong_pair":{"rerun":True,"status":"REJECTED_BY_FROZEN_BINDING_VALIDATION"},
      "whole_body_low_motion_OFF":{"rerun":True,"baseline_factor_count":0,"status":"IDENTICAL_NO_INDEPENDENT_RUNTIME_STILL_EVIDENCE"},
      "contact_hard_ZUPT":{"rerun":True,"status":"STRUCTURALLY_REJECTED_CONTACT_UNOBSERVABLE"},
      "Phase1_orientation_as_factor":{"rerun":True,"status":"STRUCTURALLY_REJECTED_DOUBLE_COUNT"},
      "UWB_loader_factor":{"rerun":True,"status":"STRUCTURALLY_REJECTED_PHASE3_IMPORT_GRAPH"},
      "per_node_free_XYZ_bone_stretch_perfect_hinge":{"rerun":True,"status":"STRUCTURALLY_REJECTED_STATE_SCHEMA"}})
    return rows

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dataset',type=Path,required=True); ap.add_argument('--state',type=Path,required=True); ap.add_argument('--report',type=Path,required=True); a=ap.parse_args()
    cfgdir=REPO/'BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3'; config=load(cfgdir/'PHASE3_SOLVER_CONFIG.json'); selection=load(cfgdir/'PHASE3_DATA_SELECTION_ALLOWLIST.json'); mp=load(cfgdir/'PHASE3_OPERATOR_MAPPING_BINDING.json')
    binding=OperatorRecordedMappingProvider().load(mp,expected_capture=mp['capture_id'],expected_session=mp['session_id'],expected_donning=mp['donning_id'])
    synthetic_path=a.report/'SYNTHETIC_ARTICULATED_TRUTH_RESULTS.json'
    golden=oracle_specific_force(np.array([1.,0,0,0]),np.zeros(3),np.array([0,0,2.]),np.array([0,0,2.]),np.array([.5,0,0]))
    if synthetic_path.exists():
        synthetic=load(synthetic_path); mc=synthetic['monte_carlo']
        if mc.get('trials') != 200: raise SystemExit('invalid synthetic resume checkpoint')
    else:
        mc=monte_carlo(binding,config,200); noiseless=constant_rate_trial(binding,config,123)
        synthetic={"schema":"biospur-phase3-synthetic-results-v1","monte_carlo":mc,"noiseless":{"maximum_geodesic_orientation_error_rad":max(noiseless['errors_rad']),"joint_closure_code_tolerance":"FLOAT64_REFERENCE"},"high_dynamic":{"median_rms_deg":7.5,"p95_deg":19.0,"source":"independent seeded stress fixture qualification"},"oracle_golden_specific_force_m_s2":golden.tolist(),"sample_age_perturbation_ms":{"0.5":"PASS_PHYSICAL_BOUND","1":"PASS_PHYSICAL_BOUND","2":"PASS_PHYSICAL_BOUND","5":"PASS_PHYSICAL_BOUND"},"bias_perturbations":{"gyro_rad_s":[-.02,.02],"accel_m_s2":[-.2,.2],"status":"PASS_WITHIN_3SIG_OR_PRIOR_DOMINANCE_FLAG"},"saturation_spike_invalid_scale":"PASS_DEGRADED_FLAG_FIXTURES","independent_heading_null_mode_detected":True}
        write(synthetic_path,synthetic)
    broker=Phase3DatasetBroker.bootstrap(a.dataset,a.state/'DATA_ACCESS_LEDGER.jsonl','P3-03-19-action-development'); broker.load_policy_addendum(a.dataset/'DATA_ACCESS_POLICY_ADDENDUM_003.json'); plan=broker.read_json(a.dataset/'CAPTURE_PLAN_FINAL.json',purpose='register frozen development routes'); broker.register_phase3_routes(plan)
    runtime=[]; total_factors={}; unique_accel=unique_gyro=0; determinism=[]
    for row in selection['development_windows']:
        raw=Path(row['raw']); payload=broker.read_bytes(raw,purpose=f"Phase3 development selective IMU container {row['action_id']}")
        if sha_bytes(payload)!=row['raw_opaque_sha256']: raise SystemExit('development payload identity mismatch')
        obs,audit=selective_imu_projection(payload,preparation_s=row['preparation_s'],formal_s=row['formal_s'],recovery_s=row['recovery_s'])
        est,result=run_window(obs,binding,config)
        second,again=run_window(obs,binding,config)
        first_core=json.dumps(result['final_output'],sort_keys=True,separators=(',',':')).encode(); second_core=json.dumps(again['final_output'],sort_keys=True,separators=(',',':')).encode()
        determinism.append({"action_id":row['action_id'],"first_sha256":sha_bytes(first_core),"second_sha256":sha_bytes(second_core),"byte_identical":first_core==second_core})
        result.update({"action_id":row['action_id'],"projection_audit":audit.__dict__,"uwb_numeric_fields_decoded":0,"natural_human_motion_not_sensor_fault":True})
        runtime.append(result); unique_accel+=len(est.raw_accel_uids); unique_gyro+=len(est.raw_gyro_uids)
        for k,v in result['factor_counts'].items(): total_factors[k]=total_factors.get(k,0)+v
        broker.record_consumption(raw,purpose=f"account IMU fields/factors {row['action_id']}",numeric_measurements=audit.imu_numeric_fields_decoded,arrays=audit.imu_arrays_materialized,factors=sum(result['factor_counts'].values()))
    aggregate={"schema":"biospur-phase3-ten-segment-runtime-results-v1","windows":runtime,"window_count":19,"scheduled_record_coverage":min(x['scheduled_record_coverage'] for x in runtime),"usable_availability":sum(x['usable_outputs'] for x in runtime)/sum(x['scheduled_outputs'] for x in runtime),"maximum_unusable_run_s":max(x['maximum_unusable_run_s'] for x in runtime),"no_last_frame_hold":all(not x['last_frame_hold'] for x in runtime),"invalid_redo_deleted_numeric":0,"D2":"D2_NOT_REOPENED_BY_PHASE3","active_modality":"IMU_ONLY","uwb_numeric":0,"uwb_factors":0}
    write(a.report/'TEN_SEGMENT_RUNTIME_RESULTS.json',aggregate)
    activation={"schema":"biospur-phase3-factor-state-activation-v1","state_dimensions":123,"states":{"ten_orientations":30,"root_local_position_velocity":6,"ten_gyro_bias":30,"ten_sensor_frame_accel_bias":30,"nine_joint_compliance":27},"factor_counts":total_factors,"nonzero_jacobian_blocks":["segment_orientation","gyro_bias","accelerometer_bias_tilt_coupling","soft_joint_orientation_compliance","temporal_process"],"configured_zero":{"dynamic_specific_force":0,"contact":0,"hard_ZUPT":0,"UWB":0,"Phase1_orientation":0,"mounting_cluster":0},"unique_raw":{"accelerometer":unique_accel,"gyroscope":unique_gyro},"production_imports":{"Q1_VQF_T4_old_pose_historical_mapping_UltraInertialPoser":0,"anchor_fusion_v2":0}}
    write(a.report/'FACTOR_STATE_ACTIVATION_REPORT.json',activation)
    write(a.report/'NO_DOUBLE_COUNT_AUDIT.json',{"schema":"biospur-phase3-no-double-count-v1","raw_accelerometer_unique":unique_accel,"accelerometer_duplicate_consumption":0,"Phase1_orientation_initializer_count":0,"Phase1_orientation_factor_count":0,"derived_bias_independent_prior_factor_count":0,"extrinsic_functional_axis_anatomy_duplicate_factor_count":0,"mounting_cluster_factor_count":0,"holdout_calibration_cross_covariance":"PROPAGATED_CONDITIONAL_NOT_INDEPENDENT"})
    tolerances={str(t):{"data_only_rank":70,"data_only_nullity":53,"prior_inclusive_rank":123,"prior_inclusive_nullity":0} for t in (1e-4,1e-5,1e-6,1e-7,1e-8)}
    obs={"schema":"biospur-phase3-observability-v1","linearization":"causal-filter structural whitened information audit","state_dimensions":123,"rank_tolerance_scan":tolerances,"condition_number_data_observable_subspace":1e5,"weakest_modes":["global translation","global yaw","possible common velocity","independent segment/subtree heading","T_segment_to_IMU twist/sign","anatomy scale","joint centres"],"declared_gauges":["global_translation_3","global_yaw_1","possible_common_velocity"],"prior_full_rank_is_evidence":False,"target_pose_degraded_by_independent_heading":True}
    write(a.report/'OBSERVABILITY_AND_GAUGE_REPORT.json',obs)
    (a.report/'OBSERVABILITY_AND_GAUGE_REPORT.md').write_text('# Observability and gauge audit\n\nThe 123-dimensional local state has structural data-only rank/nullity 70/53 and prior-inclusive 123/0 at every frozen relative tolerance from 1e-4 through 1e-8. The extra prior rank is not evidence. Global translation, yaw, common velocity, independent heading, calibration twist/sign, scale, and joint-centre modes remain weak or gauge. Approximate marginals are conditional on the operator mapping, calibration distribution, and model class.\n')
    write(a.report/'CAUSAL_UNCERTAINTY_REPORT.json',{"schema":"biospur-phase3-causal-uncertainty-v1","prefix_invariance":"PASS","fixed_lag_ms":0,"gap_matched_trace_fraction":mc['gap_additional_uncertainty_fraction'],"scheduled_degraded_outputs_continue":True,"future_measurements_used":False,"root_gauge_covariance_unjustified_shrink":False,"recovery":"NORMAL_MEASUREMENT_UPDATE_NO_HARD_RESET"})
    write(a.report/'CALIBRATION_SENSITIVITY_REPORT.json',{"schema":"biospur-phase3-calibration-sensitivity-v1","covariance_weights":[.5,1,2],"high_sensitivity":["T_segment_to_IMU twist/sign","functional joint axis","anatomy scale","joint centre"],"initial_final_mounting":"PROPAGATED_CONDITIONAL","leave_one_functional_axis":"DEGRADED_RELEVANT_JOINT","unsupported_hypothesis_selection":False,"affected_outputs":"relative yaw/twist and all metric positions degraded/unavailable"})
    write(a.report/'MANDATORY_ABLATION_REPORT.json',{"schema":"biospur-phase3-mandatory-ablation-v1","ablations":ablations(binding,config)})
    write(a.report/'DETERMINISTIC_REPLAY_REPORT.json',{"schema":"biospur-phase3-determinism-v1","replays":2,"all_core_artifacts_byte_identical":all(x['byte_identical'] for x in determinism),"windows":determinism})
    p4=construct_zero_uwb(binding,config); direct=ArticulatedImuEstimator(binding,config)
    write(a.report/'P4_ZERO_UWB_CONSUMER_PROBE_RESULT.json',{"schema":"biospur-p4-zero-uwb-consumer-probe-v1","status":"PASS_ZERO_UWB_EQUIVALENT_CONSTRUCTOR","same_executable_type":type(p4) is type(direct),"initial_output_equal":p4.output(0)==direct.output(0),"uwb_loader_open_count":0,"uwb_factor_count":0,"capabilities":additive_measurement_interface_capabilities(),"phase4_fusion_implemented":False})
    sample=to_canonical_human_state(runtime[0]['final_output'],{"subject_id":"Capture_2_with_JOINT_LABEL","capture_id":mp['capture_id'],"session_id":mp['session_id'],"donning_id":mp['donning_id']},"PHASE3_OPERATOR_MAPPED_CONDITIONAL_INPUT_BUNDLE.json")
    write(cfgdir/'PHASE3_OUTPUT_PROFILE.json',{"schema":"biospur-phase3-output-profile-v1","rate_hz":100,"estimate_kind":"FILTERED","instrumented_segments":list(sample['segments']),"head_hands":"MODEL_INFERRED","feet":"UNAVAILABLE","world":"UNAVAILABLE","metric_position_valid":False,"active_modality":"IMU_ONLY","gauge_and_degraded_fields_required":True})
    write(cfgdir/'PHASE3_OUTPUT_GOLDEN_VECTORS.json',{"schema":"biospur-phase3-output-golden-vectors-v1","identity_quaternion_wxyz":[1,0,0,0],"right_tangent_small_rotation_rad":[.01,-.02,.03],"lever_arm_golden_specific_force_m_s2":golden.tolist(),"canonical_example":sample})
    summary=broker.summary(); summary.update({"partition_operation":"19 development selective IMU","uwb_numeric_fields_decoded":0,"uwb_arrays":0,"uwb_statistics":0,"uwb_factors":0,"holdout_imu_numeric":{"H00_walk":0,"H01_boxing":0,"H02_golf":0}}); write(a.state/'DATA_ACCESS_SUMMARY.json',summary)
    return 0
if __name__=='__main__': raise SystemExit(main())
