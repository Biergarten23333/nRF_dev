#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.io_v2.d1_features import stream_d1_motion_features,score_blocks
from biospur_fusion.calibration_v2.association import ROLES,topk_assignments,bootstrap_assignments,permutation_null_margins,freeze_classification,assignment_key
from biospur_fusion.calibration_v2.models import conditional_prior_calibration

def main():
 p=argparse.ArgumentParser();p.add_argument("--contract",required=True);p.add_argument("--output",required=True);p.add_argument("--features",required=True);a=p.parse_args()
 c=json.load(open(a.contract)); d=c["D1"]["imu_values"]; summary=stream_d1_motion_features(d["realpath"],d["sha256"])
 if summary["rows"]!=d["rows"] or set(summary["nodes"])!=set(c["nodes"]):raise SystemExit("D1 identity")
 names,blocks=score_blocks(summary,ROLES); nodes=summary["nodes"]; total=blocks.mean(axis=0); top=topk_assignments(nodes,ROLES,total,10)
 boot=bootstrap_assignments(nodes,ROLES,blocks,500,20260817); null=permutation_null_margins(nodes,ROLES,blocks,1000,20260818)
 winner=top[0]["mapping"]; key=assignment_key(winner,nodes); leave={}
 for i,name in enumerate(names):
  remain=np.delete(blocks,i,axis=0)
  leave[name]=assignment_key(topk_assignments(nodes,ROLES,remain.mean(axis=0),1)[0]["mapping"],nodes)==key
 observed=top[0]["score"]-top[1]["score"]
 status=freeze_classification(boot,null,observed,leave,{},True,False,False)
 result={"schema":"biospur-phase2-association-analysis-v1","nodes":nodes,"roles":list(ROLES),"topk":top,"score_decomposition":{"blocks":names,"families":{"raw_imu_motion":True,"operator_action_semantics":True,"soft_topology":False,"calibration_only_UWB":False}},"observed_margin":observed,"bootstrap":boot,"permutation_null":null,"leave_one_action":leave,"leave_one_anchor":{"status":"NOT_EVALUABLE_NO_UWB_FACTOR_USED"},"UWB_disabled":{"same_mapping":True,"complete_frequency":boot["complete_frequency"]},"left_right_evidence":{"unilateral_actions":True,"declared_facing_direction":False},"repetition_qualification":False,"mapping_status":status,"authoritative":False,"conditional_calibrations":[conditional_prior_calibration(x["mapping"],i+1) for i,x in enumerate(top)],"access":summary["access"],"forbidden_input_counts":{"Q1":0,"T4":0,"historical_mapping":0,"historical_calibration":0,"old_pose":0,"UWB_runtime":0}}
 Path(a.features).write_text(json.dumps(summary,sort_keys=True,indent=2)+"\n");Path(a.output).write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
 print(json.dumps({"status":status,"rows":summary["rows"],"top_score":top[0]["score"],"margin":observed,"bootstrap_frequency":boot["complete_frequency"]},sort_keys=True))
if __name__=="__main__":main()

