#!/usr/bin/env python3
import argparse,json,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[5];sys.path.insert(0,str(ROOT/"BioSpur_Fusion/Fusion_Part/src"))
from biospur_fusion.phase1.ingress import NODES,iter_bundle
def main():
 p=argparse.ArgumentParser();p.add_argument("--input-contract",required=True);p.add_argument("--output",required=True);a=p.parse_args();c=json.load(open(a.input_contract));d=c["D1"];e={"value_sha256":d["value_sha256"],"context_sha256":d["context_sha256"],"models_sha256":c["time_models"]["sha256"],"rows":d["rows"]};conv=ROOT/c["conversion_contract"]
 accum={n:{"n":0,"sum":np.zeros(3),"outer":np.zeros((3,3))} for n in NODES}
 for o,_ in iter_bundle(d["value_realpath"],d["context_realpath"],conv,c["time_models"]["realpath"],e):
  if o.selector_name!="initial_still_verified_attempt_2":continue
  x=np.asarray(o.gyro_radps);z=accum[o.hardware_node_id];z["n"]+=1;z["sum"]+=x;z["outer"]+=np.outer(x,x)
 result={"schema":"biospur-phase1-d1-selected-parameters-v1","selection_partition":"D1","selector":"initial_still_verified_attempt_2","method":"per-node arithmetic mean and unbiased covariance of raw-converted gyro during verified initial still","nodes":{}}
 for n,z in accum.items():
  if z["n"]<1000:raise RuntimeError("insufficient initial still coverage")
  mean=z["sum"]/z["n"];cov=(z["outer"]-z["n"]*np.outer(mean,mean))/(z["n"]-1);result["nodes"][n]={"samples":z["n"],"gyro_bias_mean_radps":mean.tolist(),"gyro_covariance_radps2":cov.tolist(),"accelerometer_bias_state_prior_mean_mps2":[0,0,0],"accelerometer_bias_state_prior_covariance_mps4":[[1,0,0],[0,1,0],[0,0,1]],"accelerometer_bias_temporal_model":"SESSION_CONSTANT_WEAK_PRIOR_DOMINATED"}
 Path(a.output).write_text(json.dumps(result,sort_keys=True,indent=2)+"\n");print(json.dumps({n:x["samples"] for n,x in result["nodes"].items()},sort_keys=True))
if __name__=="__main__":main()
