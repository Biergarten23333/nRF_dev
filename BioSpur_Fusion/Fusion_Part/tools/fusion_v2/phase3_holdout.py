#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json
from pathlib import Path
import sys

REPO=Path(__file__).resolve().parents[4]; sys.path.insert(0,str(REPO/'BioSpur_Fusion/Fusion_Part/src'))
from biospur_fusion.articulated_v2.binding import OperatorRecordedMappingProvider
from biospur_fusion.articulated_v2.evaluation import evaluate_cold_holdout
from biospur_fusion.io_v2.phase3_governance import Phase3DatasetBroker
from biospur_fusion.io_v2.phase3_selective import selective_imu_projection

def load(p): return json.loads(Path(p).read_text())
def write(p,v): Path(p).write_text(json.dumps(v,indent=2,sort_keys=True)+"\n")
def sha_bytes(b): return hashlib.sha256(b).hexdigest()

def main():
    p=argparse.ArgumentParser(); p.add_argument('--dataset',type=Path,required=True); p.add_argument('--state',type=Path,required=True); p.add_argument('--report',type=Path,required=True); p.add_argument('--envelope',type=Path,required=True); a=p.parse_args()
    cfg=REPO/'BioSpur_Fusion/Fusion_Part/config/fusion_v2/phase3'; selection=load(cfg/'PHASE3_DATA_SELECTION_ALLOWLIST.json'); config=load(cfg/'PHASE3_SOLVER_CONFIG.json'); mp=load(cfg/'PHASE3_OPERATOR_MAPPING_BINDING.json')
    binding=OperatorRecordedMappingProvider().load(mp,expected_capture=mp['capture_id'],expected_session=mp['session_id'],expected_donning=mp['donning_id'])
    broker=Phase3DatasetBroker.bootstrap(a.dataset,a.state/'DATA_ACCESS_LEDGER.jsonl','P3-05-one-shot-holdout'); broker.load_policy_addendum(a.dataset/'DATA_ACCESS_POLICY_ADDENDUM_003.json'); plan=broker.read_json(a.dataset/'CAPTURE_PLAN_FINAL.json',purpose='register frozen holdout routes'); _,routes=broker.register_phase3_routes(plan)
    selected={x['action_id']:x for x in selection['holdouts']}
    for route in routes:
        manifest=broker.read_json(Path(route['manifest']),purpose=f"verify frozen holdout metadata {route['action_id']}"); identity=broker.hash_allowed(Path(route['manifest']),purpose=f"rebind frozen holdout manifest {route['action_id']}"); broker.bind_holdout_manifest(route['action_id'],manifest,identity['sha256'])
        if identity['sha256']!=selected[route['action_id']]['manifest_sha256']: raise SystemExit('holdout manifest changed')
    broker.enable_one_shot_holdouts(a.envelope)
    results=[]; projections={}
    for action_id in ('H00_walk','H01_boxing','H02_golf'):
        route=selected[action_id]; payload=broker.read_holdout_once(action_id)
        if sha_bytes(payload)!=route['raw_opaque_sha256']: raise SystemExit('holdout payload changed')
        observations,audit=selective_imu_projection(payload,preparation_s=route['preparation_s'],formal_s=route['formal_s'],recovery_s=route['recovery_s'],include_context=True)
        result=evaluate_cold_holdout(observations,binding,config,route); results.append(result); projections[action_id]=audit.__dict__
        broker.record_consumption(Path(route['raw']),purpose=f"one-shot IMU-only factor accounting {action_id}",numeric_measurements=audit.imu_numeric_fields_decoded,arrays=audit.imu_arrays_materialized,factors=sum(result['factor_counts'].values()))
    overall={"schema":"biospur-phase3-holdout-result-v1","implementation_commit":load(a.envelope)['implementation_commit'],"target_claim":load(a.envelope)['target_claim'],"evaluations":results,"H00_secondary_continuity_gate_pass":results[0]['formal_scheduled_record_coverage']==1.0 and results[0]['universal_safety_pass'],"stress_safety_pass":all(x['universal_safety_pass'] for x in results[1:]),"holdout_informed_source_change":False,"full_canonical_pass":False,"external_accuracy_evaluation":False}
    write(a.report/'PHASE3_HOLDOUT_RESULT.json',overall)
    access={"schema":"biospur-phase3-holdout-access-attestation-v1","transaction":"ONE_ATOMIC_H00_H01_H02_SEQUENCE","implementation_commit":overall['implementation_commit'],"release_envelope_sha256":hashlib.sha256(a.envelope.read_bytes()).hexdigest(),"per_holdout_container_opens":{"H00_walk":1,"H01_boxing":1,"H02_golf":1},"imu_projection":projections,"uwb_numeric_fields_decoded":0,"uwb_arrays":0,"uwb_statistics":0,"uwb_factors":0,"repeat_holdout_evaluation":0,"ledger_sha256":hashlib.sha256((a.state/'DATA_ACCESS_LEDGER.jsonl').read_bytes()).hexdigest()}
    write(a.report/'PHASE3_HOLDOUT_ACCESS_ATTESTATION.json',access); write(a.state/'DATA_ACCESS_SUMMARY.json',broker.summary()|{"holdout_result":"ONE_SHOT_COMPLETE","holdout_IMU_opens":{"H00_walk":1,"H01_boxing":1,"H02_golf":1},"UWB_numeric":0,"UWB_arrays":0,"UWB_statistics":0,"UWB_factors":0})
    return 0
if __name__=='__main__': raise SystemExit(main())
