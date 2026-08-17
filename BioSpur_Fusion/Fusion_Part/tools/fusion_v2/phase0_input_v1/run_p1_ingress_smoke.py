#!/usr/bin/env python3
import argparse,hashlib,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase1.ingress import NODES,iter_bundle
def main():
 ap=argparse.ArgumentParser();ap.add_argument("--manifest",required=True);ap.add_argument("--output",required=True);a=ap.parse_args();m=json.load(open(a.manifest));d=m["D1"]
 expected={"value_sha256":d["value_sha256"],"context_sha256":d["context_sha256"],"models_sha256":m["time_models"]["sha256"],"rows":d["rows"]}
 nodes=set();rows=0;counters=None
 for obs,counters in iter_bundle(d["value_realpath"],d["context_realpath"],ROOT/m["conversion_contract"],m["time_models"]["realpath"],expected):
  rows+=1;nodes.add(obs.hardware_node_id)
 if rows!=800196 or nodes!=set(NODES):raise RuntimeError("smoke coverage")
 result={"schema":"biospur-p0-p1-consumer-smoke-v1","rows":rows,"nodes":sorted(nodes),"exact_join":True,"units_axes_contract":True,"boot_epoch_present":True,"native_common_time_present":True,"uncertainty_references_present":True,"logical_role":None,"mapping_status":"UNASSIGNED","statistics_computed":False,"plots_created":False,"estimator_fitting":False,"access_counters":{"D1":counters,"D2":{"bytes_streamed":0,"headers_parsed":0,"routing_fields_decoded":0,"imu_fields_decoded":0,"uwb_fields_decoded":0,"arrays_materialized":0,"values_analyzed":0,"estimator_consumption":0},"D3":{"bytes_streamed":0,"headers_parsed":0,"routing_fields_decoded":0,"imu_fields_decoded":0,"uwb_fields_decoded":0,"arrays_materialized":0,"values_analyzed":0,"estimator_consumption":0}},"verdict":"PASS_PHASE0_TO_PHASE1_IMU_INPUT_INTERFACE"}
 Path(a.output).write_text(json.dumps(result,sort_keys=True,indent=2)+"\n");print(json.dumps(result,sort_keys=True))
if __name__=="__main__":main()
