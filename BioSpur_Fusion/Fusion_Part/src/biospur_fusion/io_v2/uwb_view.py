from __future__ import annotations
import csv,gzip,hashlib,json,os
from pathlib import Path
import numpy as np

WINDOWS={"D1":[("initial_still_verified_attempt_2",159482.243502601,159490.243569270),("t_pose",159515.194732572,159523.194799327),("arms",159561.888873564,159708.779882489),("left_elbow",159867.756239208,159907.639677120),("right_elbow_attempt_2",159990.890562082,160024.179884444),("left_knee",160047.905538995,160075.757280558),("right_knee",160098.641264983,160123.213609319),("left_heel",160162.842383158,160183.946344970),("right_heel",160208.416771782,160233.874604993),("squats",160257.591789967,160282.080835671),("trunk",160310.218076721,160350.787079520)],"D2":[("walk",160377.058329685,160406.128382753),("final_still",160548.786315034,160556.786379866)],"D3":[("golf_swing",160483.835979846,160521.487432413),("boxing",160617.304312351,160655.464412084)]}
def sha(path):
 h=hashlib.sha256()
 with open(path,"rb") as f:
  for b in iter(lambda:f.read(1<<20),b""):h.update(b)
 return h.hexdigest()
def classify(t,bounds):
 hit=[(c,n) for c,rows in bounds.items() for n,lo,hi in rows if lo<=t<=hi]
 if len(hit)>1:raise ValueError("overlapping windows")
 return hit[0] if hit else ("D0",None)
def generate(canonical,sidecar,clock_config,output,expected_canonical,expected_sidecar):
 if sha(canonical)!=expected_canonical or sha(sidecar)!=expected_sidecar:raise ValueError("source hash")
 c=json.load(open(clock_config)); bridge=c["gates"]["action_annotation_bridge"]
 conv=lambda s:int(round((bridge["listener_global_us_per_host_s"]*s+bridge["listener_global_us_intercept"])*1000))
 bounds={k:[(n,conv(lo),conv(hi)) for n,lo,hi in v] for k,v in WINDOWS.items()}
 z=np.load(sidecar,allow_pickle=False); starts={}
 for k in z.files:
  if not k.startswith("uwb_"):continue
  x=z[k];u,i=np.unique(x["raw_record_index"],return_index=True);starts[k]={int(r):int(q) for r,q in zip(u,i)}
 occ={};counts={"canonical_rows":0,"D1_uwb":0,"D2_uwb_skipped":0,"D3_rejected_before_value_decode":0,"measurement_arrays":0};nodes=set();anchors=set()
 out=Path(output);tmp=out.with_suffix(out.suffix+".tmp")
 with gzip.open(canonical,"rt",newline="") as src,gzip.GzipFile(filename="",mode="wb",fileobj=open(tmp,"wb"),mtime=0) as gz:
  reader=csv.DictReader(src); header="source_record,node,anchor,range_value,units,common_time_ns,selector_name,raw_record_occurrence\n";gz.write(header.encode())
  for r in reader:
   counts["canonical_rows"]+=1
   if r["measurement"]!="uwb_range":continue
   rec=int(r["source_record"]);node=r["node"];key="uwb_"+node;ok=(key,rec);n=occ.get(ok,0);occ[ok]=n+1
   start=starts.get(key,{}).get(rec)
   if start is None:raise ValueError("unmapped UWB record")
   side=z[key][start+n]; cls,name=classify(int(side["common_time_ns"]),bounds)
   if cls=="D3":counts["D3_rejected_before_value_decode"]+=1;continue
   if cls=="D2":counts["D2_uwb_skipped"]+=1;continue
   if cls!="D1" or int(side["clock_status"])!=1:continue
   value=float(r["value_0"]); gz.write(f'{rec},{node},{r["anchor"]},{value},{r["units"]},{int(side["common_time_ns"])},{name},{n}\n'.encode());counts["D1_uwb"]+=1;nodes.add(node);anchors.add(r["anchor"])
 os.replace(tmp,out)
 return {"schema":"biospur-D1-UWB-calibration-view-v1","realpath":str(out.resolve()),"sha256":sha(out),"rows":counts["D1_uwb"],"nodes":sorted(nodes),"anchors":sorted(anchors),"counts":counts,"D3_measurement_numeric_decode":0,"D3_measurement_arrays":0,"selector_frozen_before_decode":True}
