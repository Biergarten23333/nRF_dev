#!/usr/bin/env python3
import argparse,gzip,hashlib,io,json,os,pathlib
import numpy as np
from access import AccessGate
from common import dump,tool_identity
W={"D1":[("initial_still_verified_attempt_2",159482.243502601,159490.243569270),("t_pose",159515.194732572,159523.194799327),("arms",159561.888873564,159708.779882489),("left_elbow",159867.756239208,159907.639677120),("right_elbow_attempt_2",159990.890562082,160024.179884444),("left_knee",160047.905538995,160075.757280558),("right_knee",160098.641264983,160123.213609319),("left_heel",160162.842383158,160183.946344970),("right_heel",160208.416771782,160233.874604993),("squats",160257.591789967,160282.080835671),("trunk",160310.218076721,160350.787079520)],"D2":[("walk",160377.058329685,160406.128382753),("final_still",160548.786315034,160556.786379866)],"D3":[("golf_swing",160483.835979846,160521.487432413),("boxing",160617.304312351,160655.464412084)]}
def classify(t,b):
 hit=[(c,n) for c,rows in b.items() for n,lo,hi in rows if lo<=t<=hi]
 if len(hit)>1:raise RuntimeError("overlap")
 return hit[0] if hit else ("D0",None)
def main():
 p=argparse.ArgumentParser();p.add_argument("--allowlist",required=True);p.add_argument("--ledger",required=True);p.add_argument("--output-dir",required=True);p.add_argument("--report",required=True);a=p.parse_args();out=pathlib.Path(a.output_dir);out.mkdir(parents=True,exist_ok=True);gate=AccessGate(a.allowlist,a.ledger);cfg=json.loads(pathlib.Path(a.allowlist).read_text());paths={e["modality"]:e["path"] for e in cfg["entries"]}
 clock,_=gate.authorize(paths["TIME_METADATA"] if False else cfg["entries"][13]["path"],"CLOCK_METADATA",'sealed_extract.py');c=json.loads(pathlib.Path(clock).read_text());bridge=c["gates"]["action_annotation_bridge"]
 f=lambda s:int(round((bridge["listener_global_us_per_host_s"]*s+bridge["listener_global_us_intercept"])*1000));bounds={k:[(n,f(lo),f(hi)) for n,lo,hi in v] for k,v in W.items()}
 side,_=gate.authorize(cfg["entries"][2]["path"],"TIME_ONLY_SIDECAR",'sealed_extract.py');z=np.load(side,allow_pickle=False);arrays={k:z[k] for k in z.files};starts={}
 for k,x in arrays.items():u,i=np.unique(x["raw_record_index"],return_index=True);starts[k]={int(r):int(q) for r,q in zip(u,i)}
 tmp={};handles={}
 for cls in ('D1','D2'):
  t=out/f'{cls}_IMU_VIEW.tmp';raw=open(t,'wb');gz=gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0);tmp[cls]=t;handles[cls]=(raw,gz)
 canonical,_=gate.authorize(cfg["entries"][1]["path"],"SEALED_BLIND_STREAM",'sealed_extract.py');occ={};counts={"transport":0,"D1_IMU":0,"D2_IMU":0,"D3_rejected_before_value_decode":0};nodes={"D1":set(),"D2":set()}
 with gzip.open(canonical,'rb') as src:
  h=src.readline();suffix=b",split_class,selector_name,sidecar_common_time_ns,sidecar_clock_status\n"
  for _,gz in handles.values():gz.write(h.rstrip(b"\r\n")+suffix)
  for line in src:
   counts["transport"]+=1;prefix=line.split(b',',8)
   if len(prefix)!=9:raise RuntimeError("routing prefix")
   rec=int(prefix[1]);node=prefix[5].decode('ascii');measurement=prefix[7].decode('ascii');kind='imu' if measurement=='imu6_raw' else 'uwb';key=f'{kind}_{node}';ok=(key,rec);n=occ.get(ok,0);occ[ok]=n+1;start=starts[key].get(rec)
   if start is None:raise RuntimeError("sidecar unmapped")
   side_row=arrays[key][start+n];cls,name=classify(int(side_row["common_time_ns"]),bounds)
   if cls=='D3':counts["D3_rejected_before_value_decode"]+=1;continue
   if cls in ('D1','D2') and kind=='imu' and int(side_row["clock_status"])==1:
    handles[cls][1].write(line.rstrip(b"\r\n")+b','+cls.encode()+b','+name.encode()+b','+str(int(side_row["common_time_ns"])).encode()+b',1\n');counts[cls+'_IMU']+=1;nodes[cls].add(node)
 for raw,gz in handles.values():gz.close();raw.close()
 arts={}
 for cls,t in tmp.items():sha=hashlib.sha256(t.read_bytes()).hexdigest();dest=out/f'{cls}_IMU_VIEW_{sha}.csv.gz';os.rename(t,dest);arts[cls]={"realpath":str(dest.resolve()),"sha256":sha,"size":dest.stat().st_size,"rows":counts[cls+'_IMU'],"nodes":sorted(nodes[cls])}
 result={"tool":tool_identity(__file__),"selectors_monotonic_s":W,"selectors_common_time_ns":bounds,"counts":counts,"artifacts":arts,"D3_proof":"routing prefix and time-only sidecar classified before opaque value bytes were decoded","D3_measurement_values_decoded":False};dump(a.report,result);raise SystemExit(not(all(len(nodes[c])==10 for c in nodes) and counts['D3_rejected_before_value_decode']>0))
if __name__=="__main__":main()
