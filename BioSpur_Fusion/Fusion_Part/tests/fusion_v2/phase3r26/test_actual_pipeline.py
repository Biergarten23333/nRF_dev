import copy
import json
from pathlib import Path

import pytest

from biospur_fusion.heading_anchor_audit_v2.pipeline import (
    RUN_ID, run_science, validate_report_consistency,
)


@pytest.fixture(scope="session")
def result_dir(tmp_path_factory):
    repo=Path(__file__).resolve().parents[5]
    target=tmp_path_factory.mktemp("phase3r26")
    run_science(repo,target)
    return target


def load(root,name):
    return json.loads((root/name).read_text())


def test_real_R_EiI_loaded_and_no_synthetic_truth(result_dir):
    result=load(result_dir,"ACTUAL_REFERENCE_DIRECTION_EXTRACTION.json")
    assert result["real_R_EiI_loaded"] is True
    assert result["synthetic_truth_rows"]==0
    assert result["session_id"].startswith("phase2_targeted_calibration")
    assert result["action_boundary_resets"]==0
    assert len(result["devices"])==10


def test_all_actual_projections_nondegenerate(result_dir):
    result=load(result_dir,"ACTUAL_HORIZONTAL_PROJECTION_AUDIT.json")
    assert result["gates"]["all_ten_nondegenerate_and_stable"] is True
    assert all(x["horizontal_projection_norm"]["p05"]>.1 for x in result["devices"].values())


def test_all_512_candidates_evaluated_and_unique(result_dir):
    detail=load(result_dir,"ACTUAL_512_BRANCH_EVALUATION.json")
    selected=load(result_dir,"ACTUAL_BRANCH_SELECTION_RESULT.json")
    assert len(detail["candidates"])==512
    assert selected["exactly_one_branch_selected"] is True
    assert selected["selected_bit_vector"]==[0,1,1,1,1,0,1,0,0]


def test_all_mutations_execute_production_paths(result_dir):
    result=load(result_dir,"PRODUCTION_MUTATION_TEST_RESULTS.json")
    assert result["executed_count"]>=24
    assert result["toy_only_count"]==0 and result["literal_result_count"]==0
    assert result["all_passed"] is True
    assert all(x["production_path_exercised"] and x["input_hash"] for x in result["mutations"])


def test_gf2_is_derived_by_enumeration(result_dir):
    result=load(result_dir,"AUDIT_REPAIR_REPORT.json")
    base=result["baseline_reduced_objective"]
    assert base["candidate_count"]==base["invariant_count"]==512
    assert base["GF2_dimension_from_observed_invariant_subgroup"]==9
    assert result["directed_factor_symmetry"]["structure_symmetry_representations"]==1
    assert result["directed_factor_symmetry"]["remaining_GF2_dimension"]==0


def test_sigma_regression_does_not_claim_all_tolerances_close(result_dir):
    result=load(result_dir,"AUDIT_REPAIR_REPORT.json")
    rows=result["counterfactual_sigma_report_consistency_regression"]
    assert [x["sigma_deg"] for x in rows]==[45,60,89]
    assert all(x["profiled_rank"]==8 and x["profiled_nullity"]==1 and not x["closes_9D"] for x in rows)


def test_summary_detail_contradiction_fails_validator(result_dir):
    final=load(result_dir,"FINAL_RESULT.json")
    evaluation=load(result_dir,"ACTUAL_512_BRANCH_EVALUATION.json")
    selection=load(result_dir,"ACTUAL_BRANCH_SELECTION_RESULT.json")
    mutations=load(result_dir,"PRODUCTION_MUTATION_TEST_RESULTS.json")
    candidate=load(result_dir,"NINE_HEADING_CONDITIONAL_CANDIDATE.json")
    broken=copy.deepcopy(final);broken["selected_branch_count"]=2
    with pytest.raises(RuntimeError):
        validate_report_consistency(broken,evaluation,selection,mutations,candidate)


def test_sealed_consumer_count_zero(result_dir):
    summary=load(result_dir,"DATA_ACCESS_SUMMARY.json")
    assert summary["sealed_consumer_count"]==0
    assert summary["forbidden_consumer_count"]==0
    assert summary["UWB_measurement_consumer_count"]==0


def test_candidate_is_qualified_and_not_external_accuracy(result_dir):
    candidate=load(result_dir,"NINE_HEADING_CONDITIONAL_CANDIDATE.json")
    assert candidate is not None and len(candidate["nodes"])==9
    assert candidate["uncertainty"] is None
    assert "NO_EXTERNAL_ACCURACY" in candidate["qualifiers"]
    assert "NOT_OPENSENSE_READY" in candidate["qualifiers"]


def test_support_gate_remains_frozen(result_dir):
    result=load(result_dir,"SUPPORT_AND_BOOTSTRAP_GATE_AUDIT.json")
    assert result["bootstrap"]["single_heading_bootstrap_half_width_max_deg"]==15
    assert result["bootstrap"]["all_joint_bootstrap_half_width_le_15deg"] is False
    assert result["within_donning_block_support"]["all_families_at_least_5_blocks"] is False


def test_formal_verdict_is_partial_not_pass(result_dir):
    result=load(result_dir,"FINAL_RESULT.json")
    assert result["verdict"]=="PARTIAL_PHASE3R26_ACTUAL_BRANCH_RESOLVED_SUPPORT_GATES_FAIL"
