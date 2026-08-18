from __future__ import annotations

import json
from pathlib import Path

import pytest

from biospur_fusion.common_heading_v1.synthetic_oracle import run_independent_synthetic
from biospur_fusion.common_heading_v1.validator import ValidationError, compute_verdict


def test_independent_synthetic_all_gates(tmp_path):
    result=run_independent_synthetic(tmp_path/"result.json")
    assert result["synthetic_engineering_pass"]
    assert result["noiseless_max_error_rad"]<=1e-8
    assert result["cases"]["ten_heading_global_gauge"]=={"dimension":10,"rank":9,"nullity":1}


def fixtures():
    order=["torso","upper_arm_left","forearm_left","upper_arm_right","forearm_right","thigh_left","shank_left","thigh_right","shank_right"]
    candidate={"candidate_type":"FIT_ONLY","joint_mode_count":1,"joint_modes":[{"support_classification":"BEST"}],
               "joint_samples":{},"cross_heading_correlation":[],"parameter_order":order,"psi_GP":{"status":"FIXED_BY_EVIDENCE"},
               "candidate_payload_sha256":"x","validation_factor_rows_consumed":0,"h_numeric_consumption":0,"p_numeric_consumption":0,
               "b1_numeric_consumption":0,"opensense_numeric_consumption":0,"uwb_semantic_numeric_decode":0,"plus10_injection_factor_consumption":0}
    rank={"rank_by_relative_tolerance":{"1e-04":9,"1e-08":9},"nullity_by_relative_tolerance":{"1e-04":0,"1e-08":0}}
    info={"accepted_factor_count":1,"pass_matrix":"profiled_relative_heading.I2","pass_matrix_classification":"PROTOCOL_CONDITIONAL",
          "profiled_relative_heading":{name:rank for name in ("I0","I1","I2","biomechanics_conditional_increment","protocol_conditional_increment","process_drift_model","anatomy_prior","gauge_convention","combined")},
          "factor_family_block_counts":{"a":5}}
    split={"total_development_rows":1522793,"unique_uid_count":1522793,"uid_overlap":0,"forbidden":{"h_numeric_rows":0,"combined_h_array_materialized":False}}
    axis={"blocks":{"j":[{"line_symmetry":"+/-"}]},"aggregate":{"j":{"block_count":5,"qualification_status":"QUALIFIED"}}}
    boot={"intervals":{s:{"shortest_circular_arc_half_width_deg":1} for s in order},"resampling_unit":"action/cycle block","frame_samples_treated_independent":False}
    timing={"scenarios":[{"identifiability_verdict_flip":False}]}
    drift={"final_still_heading_factor_count":0,"validation_used_for_fit_or_mode_selection":False,
           "subtrees":{x:{"state":"STATIC_COMMON_HEADING_SUFFICIENT_FOR_THIS_SESSION_BASELINE"} for x in ("torso","left_arm","right_arm","left_leg","right_leg")},
           "semantic_residuals":{"a":{"pass":True}}}
    contract={"relative_heading_order":order,"qualification":{"required_axis_blocks_per_family":5,"required_heading_blocks_per_family":5},
              "opensense_full_input_pipeline_ready":False,"scope_qualifiers":[]}
    return candidate,info,split,axis,boot,timing,drift,contract


def test_validator_accepts_complete_machine_metrics():
    result=compute_verdict(candidate=fixtures()[0],information=fixtures()[1],split=fixtures()[2],axis=fixtures()[3],bootstrap=fixtures()[4],timing=fixtures()[5],drift=fixtures()[6],contract=fixtures()[7])
    assert result["opensense_common_heading_prerequisite_ready"] is True


@pytest.mark.parametrize("mutation", ["validation","h","uid","mode","final_still","full_pipeline","axis"])
def test_validator_rejects_governance_mutations(mutation):
    candidate,info,split,axis,boot,timing,drift,contract=fixtures()
    if mutation=="validation":candidate["validation_factor_rows_consumed"]=1
    elif mutation=="h":split["forbidden"]["h_numeric_rows"]=1
    elif mutation=="uid":split["uid_overlap"]=1
    elif mutation=="mode":candidate.pop("joint_samples")
    elif mutation=="final_still":drift["final_still_heading_factor_count"]=1
    elif mutation=="full_pipeline":contract["opensense_full_input_pipeline_ready"]=True
    elif mutation=="axis":axis["aggregate"]["j"]={"block_count":1,"qualification_status":"QUALIFIED"}
    with pytest.raises(ValidationError):
        compute_verdict(candidate=candidate,information=info,split=split,axis=axis,bootstrap=boot,timing=timing,drift=drift,contract=contract)
