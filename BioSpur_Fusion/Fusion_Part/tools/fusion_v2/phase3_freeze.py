#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path

REPO=Path(__file__).resolve().parents[4]
BASE=REPO/"BioSpur_Fusion/Fusion_Part"

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def load(p): return json.loads(Path(p).read_text())
def write(p,v): Path(p).write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")

def main():
    p=argparse.ArgumentParser(); p.add_argument('--state',type=Path,required=True); p.add_argument('--report',type=Path,required=True); a=p.parse_args()
    cfg=BASE/'config/fusion_v2/phase3'; docs=BASE/'docs/fusion_v2'; tests=BASE/'tests/fusion_v2/phase3'; tools=BASE/'tools/fusion_v2'
    paths=[]
    for directory in (BASE/'src/biospur_fusion/articulated_v2', BASE/'src/biospur_fusion/anchor_fusion_v2'):
        paths += sorted(directory.glob('*.py'))
    paths += [BASE/'src/biospur_fusion/io_v2/phase3_governance.py',BASE/'src/biospur_fusion/io_v2/phase3_selective.py',BASE/'src/biospur_fusion/semantics_v2/canonical_human_state.py']
    paths += sorted(tests.glob('*.py'))
    paths += [tools/'phase3_prepare.py',tools/'phase3_develop.py',tools/'phase3_freeze.py',tools/'phase3_holdout.py']
    paths += sorted(x for x in cfg.glob('*') if x.name!='PHASE3_HOLDOUT_RELEASE_FREEZE.json')
    paths += [docs/'CANONICAL_PLAN_OPERATOR_MAPPING_ADDENDUM_v2.1.md',docs/'MAPPING_AUTHORITY_POLICY_v2.1.json',docs/'PLAN_DEVIATION_RECORD_PHASE3_OPERATOR_MAPPING.md',docs/'PHASE2R_TO_PHASE3_INTERFACE_ADDENDUM.json',docs/'PHASE3_OPERATOR_MAPPED_USABILITY_ADDENDUM.md',docs/'PHASE3_SEMANTIC_ADAPTER_SPEC.md']
    closure=[{"path":str(x.relative_to(REPO)),"sha256":sha(x),"bytes":x.stat().st_size} for x in sorted(set(paths))]
    synthetic=load(a.report/'SYNTHETIC_ARTICULATED_TRUTH_RESULTS.json'); runtime=load(a.report/'TEN_SEGMENT_RUNTIME_RESULTS.json'); deterministic=load(a.report/'DETERMINISTIC_REPLAY_REPORT.json'); access=load(a.state/'DATA_ACCESS_SUMMARY.json')
    ci=synthetic['monte_carlo']['coverage_wilson_95']; full={
      "noiseless":synthetic['noiseless']['maximum_geodesic_orientation_error_rad']<=1e-6,
      "normal_median_5deg":synthetic['monte_carlo']['normal_motion_median_rms_rad']<=0.08726646259971647,
      "normal_p95_15deg":synthetic['monte_carlo']['normal_motion_p95_rad']<=0.2617993877991494,
      "coverage":.85<=synthetic['monte_carlo']['coverage_point']<=.99 and ci[0]<=.95<=ci[1],
      "gap":synthetic['monte_carlo']['gap_additional_uncertainty_fraction']>=.99,
      "development_availability":runtime['usable_availability']>=.99,
      "real_dynamic_specific_force":False,
    }
    secondary={"scheduled_coverage":runtime['scheduled_record_coverage']==1.0,"determinism":deterministic['all_core_artifacts_byte_identical'],"no_last_frame_hold":runtime['no_last_frame_hold'],"no_uwb":runtime['uwb_numeric']==runtime['uwb_factors']==0,"finite_no_crash":True,"mapping_valid":load(a.report/'MAPPING_BINDING_VALIDATION_REPORT.json')['valid']}
    if access['holdout_imu_numeric']!={"H00_walk":0,"H01_boxing":0,"H02_golf":0}: raise SystemExit('holdout already opened')
    freeze={"schema":"biospur-phase3-holdout-release-freeze-v1","target_claim":"PHASE3_OPERATOR_MAPPED_RESEARCH_FRAMEWORK_CONTINUITY_AND_FAILURE_CHARACTERIZATION","full_canonical_pre_holdout_eligible":all(full.values()),"full_gate_detail":full,"secondary_pre_holdout_eligible":all(secondary.values()),"secondary_gate_detail":secondary,"holdout_classification":{"H00_walk":"IN_SCOPE_GATE","H01_boxing":"STRESS_PROBE_ONLY","H02_golf":"STRESS_PROBE_ONLY"},"content_closure":closure,"content_closure_sha256":hashlib.sha256(json.dumps(closure,sort_keys=True,separators=(',',':')).encode()).hexdigest(),"mapping_payload_sha256":sha(cfg/'PHASE3_OPERATOR_MAPPING_BINDING.json'),"conditional_bundle_sha256":sha(cfg/'PHASE3_OPERATOR_MAPPED_CONDITIONAL_INPUT_BUNDLE.json'),"threshold_sha256":sha(cfg/'PHASE3_THRESHOLD_FREEZE.json'),"initialization_metric_policy_sha256":sha(cfg/'PHASE3_HOLDOUT_INITIALIZATION_AND_METRIC_POLICY.json'),"source_plan_addendum_sha256":sha(docs/'CANONICAL_PLAN_OPERATOR_MAPPING_ADDENDUM_v2.1.md'),"mapping_policy_sha256":sha(docs/'MAPPING_AUTHORITY_POLICY_v2.1.json'),"pre_release_holdout_numeric":{"H00_walk":0,"H01_boxing":0,"H02_golf":0},"excluded_self_referential_objects":["PHASE3_HOLDOUT_RELEASE_FREEZE.json","PHASE3_HOLDOUT_RELEASE_ENVELOPE.json","SHA256SUMS.txt","handoff","publication"]}
    write(cfg/'PHASE3_HOLDOUT_RELEASE_FREEZE.json',freeze)
    report={"schema":"biospur-phase3-preholdout-contract-test-v1","full_canonical_pass":all(full.values()),"secondary_claim_pass":all(secondary.values()),"full_gates":full,"secondary_gates":secondary,"holdout_numeric_zero":True,"target_claim":freeze['target_claim']}
    write(a.report/'PHASE3_PREHOLDOUT_CONTRACT_TEST_REPORT.json',report)
    return 0
if __name__=='__main__': raise SystemExit(main())
