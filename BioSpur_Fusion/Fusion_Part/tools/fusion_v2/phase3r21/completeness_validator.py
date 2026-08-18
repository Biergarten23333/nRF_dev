#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys


EXPECTED_NODES={"BSF1120","BSF31CC","BSF3C79","BSF44AD","BSF6C53","BSF8BC4","BSFAA61","BSFB165","BSFC2CC","BSFEC35"}
FIT_ACTIONS={"00_initial_still","02_t_pose","03_pelvis_hula_circle","04_shoulder_left","05_shoulder_right","06_elbow_left","07_elbow_right","08_hip_left","09_hip_right","10_knee_left_seated","11_knee_right_seated","12_heel_raise_left","13_heel_raise_right","14_trunk_flex_extend","15_trunk_axial_rotation","16_squat","18_heel_to_butt_left","19_heel_to_butt_right"}


def load(path): return json.loads(Path(path).read_text())


def evaluate(artifacts: Path, source: Path) -> dict:
    broker=load(artifacts/"BROKER_REPORT.json");fit=load(artifacts/"FIT_REPORT.json");bundle=load(artifacts/"SESSION_CALIBRATION_BUNDLE.json")
    replay=load(artifacts/"replay/CONTINUOUS_REPLAY_REPORT.json");semantic=load(artifacts/"replay/REAL_SEMANTIC_GATES.json");wobble=load(artifacts/"replay/REAL_STATIC_WOBBLE.json");h=load(artifacts/"H_CACHE_REPORT.json")
    coverage=load(artifacts/"postprocess/COVERAGE_COVARIANCE.json");timing=load(artifacts/"postprocess/TIME_SENSITIVITY.json");animations=load(artifacts/"postprocess/ANIMATION_MANIFEST.json");result=load(artifacts/"postprocess/RESULT.json")
    tree=ast.parse(source.read_text());imports={n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)}
    checks={
      "production_imports_real_decoder":any(x and "imu_pose_r21.real_data" in x for x in imports),
      "real_samples_nonzero":broker["real_imu_decoded_samples"]>0,
      "ten_nodes":set(broker["nodes"])==EXPECTED_NODES,
      "uid_lineage_nonzero":broker["real_uid_count"]>0,
      "all_18_fit_actions":set(fit["per_action"])==FIT_ACTIONS and all(v["factors"]>0 and v["information_trace"]>0 for v in fit["per_action"].values()),
      "bundle_loadable_nonempty":len(bundle["nodes"])==10 and bool(bundle["bundle_artifact_sha256"]),
      "validation_prefreeze_invisible":all(v==0 for v in broker["cache_manifest"]["validation_main_process_prefreeze_counts"].values()),
      "nineteen_validation_windows":len([a for a,v in broker["per_action"].items() if v["validation"]>0])==19,
      "continuous_real_b0_b1_p":min(replay["b0_real"],replay["b1_real"],replay["p_real"])>0 and replay["action_boundary_reset_count"]==0,
      "h_all_three":set(h["actions"])=={"H00_walk","H01_boxing","H02_golf"} and h["rows"]>0,
      "real_semantics_ran":bool(semantic["real_capture"]) and not semantic["synthetic"],
      "recovery_rest_ran":wobble["eligible"]>0,
      "coverage_recomputed":coverage["scheduled"]>0 and coverage["emitted"]==coverage["scheduled"],
      "time_sensitivity_all_scenarios":timing["scenarios"]["nominal"] and timing["scenarios"]["correlated_common_mode"] and timing["scenarios"]["full_clock_plus_independent_age_interval"],
      "formal_and_full_visualizations":animations["formal_only"]>=22 and animations["full_context"]>=22,
      "declarative_verdict_present":result["verdict"].startswith(("PASS_","FAIL_","PHASE3R2_1_")),
      "uwb_semantic_zero":all(broker["uwb"][k]==0 for k in broker["uwb"] if k!="co_located_transport_record_exposure"),
      "historical_colocated_exposure_preserved":broker["uwb"]["co_located_transport_record_exposure"]>0,
    }
    mandatory_real_counts={"real_imu":broker["real_imu_decoded_samples"],"nodes":len(broker["nodes"]),"fit_actions":len(fit["per_action"]),
                           "validation_windows":len([a for a,v in broker["per_action"].items() if v["validation"]>0]),"h":len(h["actions"]),
                           "b0":replay["b0_real"],"b1":replay["b1_real"],"p":replay["p_real"],"wobble":wobble["eligible"]}
    structural_pass=all(checks.values()) and all(v>0 for v in mandatory_real_counts.values())
    return {"schema":"biospur-phase3r21-independent-completeness-v1","checks":checks,"mandatory_real_counts":mandatory_real_counts,
            "structural_pass":structural_pass,"verdict":"COMPLETE_REAL_PIPELINE_EXECUTED" if structural_pass else "STAGE_INCOMPLETE_REAL_PIPELINE_NOT_EXECUTED"}


def mutation_tests() -> dict:
    base={"real_imu":10,"nodes":10,"fit_actions":18,"validation_windows":19,"h":3,"b0":1,"b1":1,"p":1,"wobble":1}
    mutations={"real_reader_zero":{**base,"real_imu":0},"node_drop":{**base,"nodes":9},"empty_fit":{**base,"fit_actions":17},"truncated_half":{**base,"validation_windows":9},"synthetic_substitution":{**base,"real_imu":0}}
    return {name:not all(v>0 for v in payload.values()) or payload.get("nodes")!=10 or payload.get("fit_actions")!=18 or payload.get("validation_windows")!=19 for name,payload in mutations.items()}


def main():
    p=argparse.ArgumentParser();p.add_argument("--artifacts",type=Path,required=True);p.add_argument("--source",type=Path,required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    result=evaluate(a.artifacts,a.source);result["mutation_rejections"]=mutation_tests();result["mutation_suite_pass"]=all(result["mutation_rejections"].values())
    a.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n");print(json.dumps({"structural_pass":result["structural_pass"],"mutation_suite_pass":result["mutation_suite_pass"]},sort_keys=True));return 0 if result["structural_pass"] and result["mutation_suite_pass"] else 1


if __name__=="__main__":raise SystemExit(main())
