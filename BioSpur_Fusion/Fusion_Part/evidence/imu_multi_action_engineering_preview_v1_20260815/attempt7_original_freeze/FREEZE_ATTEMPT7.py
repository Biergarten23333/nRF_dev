#!/usr/bin/env python3
"""Rebuild and freeze Attempt 7 numerics without invoking a solver."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np

FREEZE=Path(__file__).resolve().parent
REPO=next(parent for parent in FREEZE.parents if (parent/"Fusion_Part").is_dir())
SNAPSHOT=FREEZE/"SOURCE_SNAPSHOT"
sys.path.insert(0,str(SNAPSHOT/"Fusion_Part/src"))

from biospur_fusion.imu_preview_v0.io import dump_json,savez_deterministic,sha256
from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.model import (
    ACTIONS,Objective,finite_difference_jacobian,initial_parameters,parameter_steps,
)
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import (
    _derivative_audit,_soft_l1_cost,load_q2_cache,
)


def tree_manifest(root:Path)->list[dict]:
    return [{"path":str(path.relative_to(FREEZE)),"bytes":path.stat().st_size,"sha256":sha256(path)} for path in sorted(root.rglob("*")) if path.is_file()]


def main()->None:
    artifacts=FREEZE/"ORIGINAL_ARTIFACTS"
    capture=REPO/"Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601"
    phase=capture/"analysis_imu_multi_action_engineering_preview_v1_phase_a_final_20260815"
    gates_path=SNAPSHOT/"Fusion_Part/config/imu_multi_action_engineering_preview_v1/gates_v1.json"
    contract_path=SNAPSHOT/"Fusion_Part/config/imu_multi_action_engineering_preview_v1/ACTION_RELATIVE_EXCITATION_CONTRACT.json"
    template_path=SNAPSHOT/"Fusion_Part/config/GENERIC_ADULT_PROXY_V1.json"
    cache=phase/"Q2_HUMAN_QUASI_STATIC_CACHE.npz"
    gates=json.loads(gates_path.read_text());phase_result=json.loads((phase/"RESULT.json").read_text())
    q2=load_q2_cache(cache);windows={key:tuple(value) for key,value in phase_result["calibration_windows"].items()}
    timeline=build_common_timeline(q2,min(value[0] for value in windows.values()),max(value[1] for value in windows.values()),gates["common_time"])
    segmentation=json.loads((artifacts/"ACTION_SEGMENTATION.json").read_text())
    objective=Objective(timeline,segmentation,gates["node_to_segment"],gates["calibration_model"])
    x0=initial_parameters(objective);checkpoint=json.loads((artifacts/"START_0_CHECKPOINT.json").read_text());x_end=np.asarray(checkpoint["x"],float)
    residual_x0=objective.residual(x0);residual_end=objective.residual(x_end)
    jacobian=finite_difference_jacobian(objective.residual,x_end,parameter_steps(gates["production_jacobian"]))
    influence_weight=1./np.sqrt(1.+residual_end*residual_end)
    jacobian_row_scale=np.power(1.+residual_end*residual_end,-.75)
    robust_gradient=jacobian.T@(residual_end*influence_weight)
    structure=objective.structural_sparsity(x_end).tocsr();accounting,slices=objective.accounting(x_end)
    action_ordinal=np.empty(len(residual_end),np.int16);factor_ordinal=np.empty(len(residual_end),np.int16);factor_lookup=[]
    for ai,action in enumerate(ACTIONS):
        for factor in accounting["actions"][action]["residual_blocks"]:
            fi=len(factor_lookup);factor_lookup.append({"factor_ordinal":fi,"action":action,"residual_block":factor["residual_block"],"row_start":factor["row_start"],"row_stop":factor["row_stop"]});action_ordinal[factor["row_start"]:factor["row_stop"]]=ai;factor_ordinal[factor["row_start"]:factor["row_stop"]]=fi
    savez_deterministic(FREEZE/"ATTEMPT7_PRODUCTION_NUMERICS.npz",{
        "x0":x0,"x_end":x_end,"residual_x0":residual_x0,"residual_x_end":residual_end,
        "production_jacobian_x_end":jacobian,"soft_l1_influence_weight_x_end":influence_weight,
        "soft_l1_jacobian_row_scale_x_end":jacobian_row_scale,"robust_gradient_x_end":robust_gradient,
        "structural_sparsity_data":structure.data,"structural_sparsity_indices":structure.indices,
        "structural_sparsity_indptr":structure.indptr,"structural_sparsity_shape":np.asarray(structure.shape,np.int64),
        "row_action_ordinal":action_ordinal,"row_factor_ordinal":factor_ordinal,
    })
    dump_json(FREEZE/"ATTEMPT7_ROW_ACTION_METADATA.json",{"schema":"biospur-attempt7-row-action-metadata-v1","action_lookup":[{"ordinal":i,"action":name} for i,name in enumerate(ACTIONS)],"factor_lookup":factor_lookup,"accounting":accounting,"complete_row_count":len(residual_end)})
    reproduced=_derivative_audit(objective,x_end,jacobian,gates["production_jacobian"])
    dump_json(FREEZE/"ATTEMPT7_ORIGINAL_DERIVATIVE_REPRODUCTION.json",reproduced)
    original=json.loads((artifacts/"PRODUCTION_JACOBIAN_AUDIT.json").read_text())
    original_solver=json.loads((artifacts/"SOLVER_AUDIT.json").read_text())
    original_result=json.loads((artifacts/"RESULT.json").read_text())
    disposition={
        "schema":"biospur-attempt7-immutable-disposition-v1",
        "ATTEMPT7_ORIGINAL_GATE":"FAIL",
        "ATTEMPT7_JV":"PASS" if reproduced["maximum_jv_relative_error"]<=gates["production_jacobian"]["maximum_jv_relative_error"] else "FAIL",
        "ATTEMPT7_JV_MAX_RELATIVE_ERROR":reproduced["maximum_jv_relative_error"],
        "ATTEMPT7_SCALAR_COST_METRIC":"NUMERICALLY_ILL_CONDITIONED_NEAR_ZERO_DERIVATIVE",
        "ATTEMPT7_ADOPTABLE":False,
        "historical_result_verdict":original_result["verdict"],
        "historical_derivative_pass":original["pass"],
        "historical_scalar_cost_max_relative_error":original["maximum_soft_l1_cost_directional_relative_error"],
        "cost_checkpoint":checkpoint["cost"],"cost_recomputed":_soft_l1_cost(residual_end),
        "optimality":checkpoint["optimality"],"nfev":checkpoint["nfev"],"status":checkpoint["status"],
        "success_flag":checkpoint["success"],"message":checkpoint["message"],
        "first_start_wall_time_s":original_solver["first_start_wall_time_s"],"total_numeric_wall_time_s":original_solver["total_numeric_wall_time_s"],
        "robust_gradient_inf_norm":float(np.max(np.abs(robust_gradient))),"robust_gradient_l2_norm":float(np.linalg.norm(robust_gradient)),
        "residual_rows":len(residual_end),"parameter_count":len(x_end),"production_jacobian_shape":list(jacobian.shape),
        "no_retroactive_v2_metric_applied":True,"no_solver_invoked_by_freeze":True,
    }
    dump_json(FREEZE/"ATTEMPT7_DISPOSITION.json",disposition)
    binding={
        "schema":"biospur-attempt7-freeze-binding-v1",
        "audited_baseline_commit":"bc71745ffd7fc4c50afa61c3b7f604fc643dc0a1",
        "attempt7_original_absolute_path":str((capture/"analysis_imu_multi_action_engineering_preview_v1_calibration_attempt7_20260815").resolve()),
        "source_snapshot_status":"RECONSTRUCTED_FROM_WORKTREE_EDIT_CHRONOLOGY; GATES EXACTLY VERIFIED BY HISTORICAL SHA",
        "source_snapshot_files":tree_manifest(SNAPSHOT),
        "original_artifacts":tree_manifest(artifacts),
        "bindings":{
            "gates":{"path":str(gates_path.relative_to(FREEZE)),"sha256":sha256(gates_path),"historical_checkpoint_sha256":checkpoint["gates_sha256"]},
            "action_contract":{"path":str(contract_path.relative_to(FREEZE)),"sha256":sha256(contract_path)},
            "template":{"path":str(template_path.relative_to(FREEZE)),"sha256":sha256(template_path)},
            "phase_a_cache":{"absolute_path":str(cache.resolve()),"sha256":sha256(cache)},
            "phase_a_result":{"absolute_path":str((phase/"RESULT.json").resolve()),"sha256":sha256(phase/"RESULT.json")},
        },
        "data_firewall":{"golf":"SEALED","boxing":"SEALED","walk":"SEALED","final_still":"SEALED","uwb_t4_anchor":"SEALED","operator_measurements":"SEALED"},
    }
    dump_json(FREEZE/"ATTEMPT7_BINDING.json",binding)
    files=[path for path in sorted(FREEZE.rglob("*")) if path.is_file() and path.name!="SHA256_MANIFEST.json"]
    dump_json(FREEZE/"SHA256_MANIFEST.json",{"schema":"biospur-attempt7-freeze-sha256-manifest-v1","files":[{"path":str(path.relative_to(FREEZE)),"bytes":path.stat().st_size,"sha256":sha256(path)} for path in files]})


if __name__=="__main__":main()
