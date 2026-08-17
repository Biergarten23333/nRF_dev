#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
BASE = REPO/"BioSpur_Fusion/Fusion_Part"
sys.path.insert(0, str(BASE/"src"))

from biospur_fusion.imu_pose_v1.governance import Phase3RDatasetBroker
from biospur_fusion.imu_pose_v1.mapping import FrozenOperatorMapping, H9


def sha(p: Path) -> str: return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p: Path): return json.loads(p.read_text())
def write(p: Path, x): p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(x, indent=2, sort_keys=True)+"\n")


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--dataset",type=Path,required=True); ap.add_argument("--state",type=Path,required=True)
    a=ap.parse_args(); cfg=BASE/"config/fusion_v2/phase3r"; p3cfg=BASE/"config/fusion_v2/phase3"
    p2=BASE/"reports/fusion_v2/phase2r/phase2r_20260817T154655Z"
    # This bootstrap is intentionally the first semantic dataset read.
    broker=Phase3RDatasetBroker.bootstrap(a.dataset,a.state/"DATA_ACCESS_LEDGER.jsonl","P3R-00-bootstrap-routing")
    broker.load_policy_addendum(a.dataset/"DATA_ACCESS_POLICY_ADDENDUM_003.json")
    plan_payload=broker.read_bytes(a.dataset/"CAPTURE_PLAN_FINAL.json",purpose="bind exact capture plan")
    selection=load(p3cfg/"PHASE3_DATA_SELECTION_ALLOWLIST.json")
    development, retrospective=broker.register_literal_selection(selection,plan_payload)
    for row in (*development,*retrospective):
        broker.read_json(Path(row["manifest"]),purpose=f"verify promoted literal manifest {row['action_id']}")
    mapping_source=p2/"OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json"; payload=load(mapping_source)
    binding=FrozenOperatorMapping.from_payload(payload,capture_id="Capture_2_with_JOINT_LABEL",
        session_id="capture_2_with_joint_label",donning_id="capture_2_with_joint_label_donning_01")
    write(cfg/"PHASE3R_OPERATOR_MAPPING.json",{
        "schema":"biospur-phase3r-operator-mapping-v1","source":str(mapping_source.relative_to(REPO)),
        "source_sha256":sha(mapping_source),"authority":binding.authority,
        "capture_id":binding.capture_id,"session_id":binding.session_id,"donning_id":binding.donning_id,
        "mapping":dict(binding.node_to_segment),"immutable_runtime":True,"automapping":"DEFERRED_OPTIONAL_R_AND_D",
        "H9":sorted(H9),"distinct_layout":["BSFC2CC"],"BSFC22C_rejected":True,
    })
    write(cfg/"PHASE3R_DATA_SELECTION.json",{
        "schema":"biospur-phase3r-data-selection-v1","source_plan_sha256":hashlib.sha256(plan_payload).hexdigest(),
        "development_windows":development,"development_count":len(development),
        "retrospective_diagnostics":retrospective,"retrospective_count":len(retrospective),
        "invalid_redo_rejected_deleted_numeric":0,"recursive_glob":False,"latest_file_inference":False,
        "uwb_decode_policy":"REJECT_AFTER_COMMON_HEADER_BEFORE_PAYLOAD_VALUES",
    })
    write(a.state/"DATA_ACCESS_SUMMARY_BOOTSTRAP.json",broker.summary())
    return 0

if __name__=="__main__": raise SystemExit(main())
