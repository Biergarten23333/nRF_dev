from __future__ import annotations
import hashlib,json,sys
from pathlib import Path
import numpy as np
from .filter import FrontendConfig,ImuFrontend
ROOT=Path(__file__).resolve().parents[5]
sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase1.ingress import NODES,iter_bundle
def run_partition(input_contract_path,partition,config=None):
 c=json.load(open(input_contract_path));d=c[partition];expected={"value_sha256":d["value_sha256"],"context_sha256":d["context_sha256"],"models_sha256":c["time_models"]["sha256"],"rows":d["rows"]};conversion=ROOT/c["conversion_contract"]
 priors=json.load(open(ROOT/c["D1_selected_parameters"]))["nodes"]
 states={};outputs_hash=hashlib.sha256();last_counters=None
 for obs,counters in iter_bundle(d["value_realpath"],d["context_realpath"],conversion,c["time_models"]["realpath"],expected):
  key=(obs.hardware_node_id,obs.boot_epoch)
  if key not in states:
   states[key]=ImuFrontend(*key,cfg=config or FrontendConfig());states[key].bg=np.asarray(priors[obs.hardware_node_id]["gyro_bias_mean_radps"],float)
  out=states[key].step(obs.accel_mps2,obs.gyro_radps,obs.node_timer_us,obs.common_time_ns,(0,5000),True);last_counters=counters;counters["estimator_consumption"]+=1
  if out["valid"]:outputs_hash.update(obs.hardware_node_id.encode()+obs.node_timer_us.to_bytes(8,"little")+np.asarray(out["q"],dtype="<f8").tobytes())
 summaries=[states[k].summary() for k in sorted(states)]
 return {"schema":"biospur-phase1-run-result-v1","partition":partition,"rows":d["rows"],"states":summaries,"output_stream_sha256":outputs_hash.hexdigest(),"access_counters":last_counters,"forbidden_input_counts":{"Q1":0,"T4":0,"UWB":0,"historical_pose":0,"historical_mapping":0,"anatomical_role":0},"yaw_gauges":len(summaries),"node_to_body_mapping":"UNKNOWN","logical_role":None,"mapping_status":"UNASSIGNED"}
