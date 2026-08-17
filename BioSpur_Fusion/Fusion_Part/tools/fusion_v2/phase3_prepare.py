#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "BioSpur_Fusion/Fusion_Part/src"))

from biospur_fusion.articulated_v2.binding import OperatorRecordedMappingProvider
from biospur_fusion.io_v2.phase3_governance import Phase3DatasetBroker


def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path: Path): return json.loads(path.read_text())
def write(path: Path, value): path.parent.mkdir(parents=True, exist_ok=True); path.write_text(json.dumps(value, indent=2, sort_keys=True)+"\n")


def main() -> int:
    p=argparse.ArgumentParser(); p.add_argument("--dataset",type=Path,required=True); p.add_argument("--state",type=Path,required=True); p.add_argument("--report",type=Path,required=True); a=p.parse_args()
    config=REPO/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3"
    p2=REPO/"BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase2r/phase2r_20260817T154655Z"
    broker=Phase3DatasetBroker.bootstrap(a.dataset,a.state/"DATA_ACCESS_LEDGER.jsonl","P3-01-contract-and-routing")
    broker.load_policy_addendum(a.dataset/"DATA_ACCESS_POLICY_ADDENDUM_003.json")
    plan=broker.read_json(a.dataset/"CAPTURE_PLAN_FINAL.json",purpose="derive literal Phase3 routes")
    dev,holdouts=broker.register_phase3_routes(plan)
    p2selection=load(p2/"PHASE2R_DATA_SELECTION_ALLOWLIST.json")
    p2rows={x["action_id"]:x for x in p2selection["phase2_windows"]}
    development=[]
    for row in dev:
        manifest=broker.read_json(Path(row["manifest"]),purpose=f"verify promoted metadata {row['action_id']}")
        identity=broker.hash_allowed(Path(row["manifest"]),purpose=f"bind promoted manifest {row['action_id']}")
        old=p2rows[row["action_id"]]
        if identity["sha256"] != old["manifest_sha256"] or row["raw"] != old["raw"]:
            raise SystemExit("Phase2-R development route identity mismatch")
        development.append({**row,"manifest_sha256":identity["sha256"],"raw_opaque_sha256":old["raw_slice_opaque_sha256"],"preparation_s":manifest["preparation_buffer_s"],"formal_s":manifest["actual_action_duration_s"],"recovery_s":manifest["post_action_buffer_s"]})
    bound_holdouts=[]
    for row in holdouts:
        manifest=broker.read_json(Path(row["manifest"]),purpose=f"read sealed holdout routing metadata {row['action_id']}")
        identity=broker.hash_allowed(Path(row["manifest"]),purpose=f"bind sealed holdout manifest {row['action_id']}")
        bound_holdouts.append(broker.bind_holdout_manifest(row["action_id"],manifest,identity["sha256"]))
    selection={"schema":"biospur-phase3-data-selection-allowlist-v1","source_plan_sha256":sha(a.dataset/"CAPTURE_PLAN_FINAL.json"),"development_windows":development,"development_count":len(development),"holdouts":bound_holdouts,"holdout_count":len(bound_holdouts),"invalid_redo_deleted_numeric_count":0,"recursive_glob":False,"d2":"D2_NOT_REOPENED_BY_PHASE3"}
    write(config/"PHASE3_DATA_SELECTION_ALLOWLIST.json",selection)
    mapping_payload=load(config/"PHASE3_OPERATOR_MAPPING_BINDING.json")
    binding=OperatorRecordedMappingProvider().load(mapping_payload,expected_capture="Capture_2_with_JOINT_LABEL",expected_session="capture_2_with_joint_label",expected_donning="capture_2_with_joint_label_donning_01")
    source=load(p2/"OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json")
    validation={"schema":"biospur-phase3-mapping-validation-v1","valid":dict(binding.node_to_role)==source["mapping"],"node_count":len(binding.node_to_role),"role_count":len(set(binding.node_to_role.values())),"source_authority":source["binding_authority"],"runtime_authority":binding.authority_source,"capture_id":binding.capture_id,"session_id":binding.session_id,"donning_id":binding.donning_id,"operator_confirmed":binding.operator_confirmed,"automatic_top1_used":False,"historical_mapping_fallback":False,"BSFC2CC_present":True,"BSFC22C_present":False}
    write(a.report/"MAPPING_BINDING_VALIDATION_REPORT.json",validation)
    policy=REPO/"BioSpur_Fusion/Fusion_Part/docs/fusion_v2/MAPPING_AUTHORITY_POLICY_v2.1.json"
    freeze={"schema":"biospur-phase3-operator-session-binding-freeze-v1","binding_payload":"PHASE3_OPERATOR_MAPPING_BINDING.json","binding_payload_sha256":sha(config/"PHASE3_OPERATOR_MAPPING_BINDING.json"),"source_operator_artifact_sha256":sha(p2/"OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json"),"scope":{"capture_id":binding.capture_id,"session_id":binding.session_id,"donning_id":binding.donning_id},"mapping_authority_policy_sha256":sha(policy),"validation_report_sha256":sha(a.report/"MAPPING_BINDING_VALIDATION_REPORT.json"),"immutable_runtime":True,"automatic_association":"FAILED_DEFERRED"}
    write(a.report/"OPERATOR_SESSION_NODE_BINDING_FREEZE.json",freeze)
    components={x:sha(p2/x) for x in load(p2/"CALIBRATION_BUNDLE_CONDITIONAL_MANIFEST.json")["components"]}
    bundle={"schema":"biospur-phase3-operator-mapped-conditional-input-bundle-v1","authoritative":False,"mapping_payload_sha256":freeze["binding_payload_sha256"],"mapping_authority":"OPERATOR_RECORDED","conditional_calibration_status":"RESEARCH_CALIBRATION_LIMITED","components":components,"cross_covariance_sha256":sha(p2/"CALIBRATION_CROSS_COVARIANCE.npz"),"observability":{"data_only_rank_nullity":[50,70],"prior_inclusive_rank_nullity":[120,0],"local_marginals":"mapping-conditional approximate local Gaussian"},"unidentified_states":["full T_segment_to_IMU","accelerometer bias","joint centres","bone lengths","metric translation","world transform"],"H9":["BSF6C53","BSF8BC4","BSF1120","BSF3C79","BSF44AD","BSF31CC","BSFAA61","BSFB165","BSFEC35"],"distinct_layout":["BSFC2CC"],"mounting_cluster_production_factor_count":0,"accidental_BSFC2CC_pooling":"STRUCTURALLY_FORBIDDEN"}
    write(config/"PHASE3_OPERATOR_MAPPED_CONDITIONAL_INPUT_BUNDLE.json",bundle)
    audit={"schema":"biospur-phase3-usability-source-audit-v1","source_path":"config/fusion_v2/imu_frontend/POSE_USABILITY_CONTRACT.md","source_sha256":sha(REPO/"BioSpur_Fusion/Fusion_Part/config/fusion_v2/imu_frontend/POSE_USABILITY_CONTRACT.md"),"accepted_phase1_bound":True,"operator_addendum":"docs/fusion_v2/PHASE3_OPERATOR_MAPPED_USABILITY_ADDENDUM.md","external_accuracy_added":False}
    write(a.report/"PHASE3_USABILITY_CONTRACT_SOURCE_AUDIT.json",audit)
    summary=broker.summary(); summary["phase3_holdout_imu_numeric"]={x["action_id"]:0 for x in bound_holdouts}; summary["phase3_holdout_uwb_numeric"]={x["action_id"]:0 for x in bound_holdouts}
    write(a.state/"DATA_ACCESS_SUMMARY.json",summary)
    return 0

if __name__=="__main__": raise SystemExit(main())
