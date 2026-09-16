#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, resource, subprocess, sys, time
from pathlib import Path
import numpy as np
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import _CausalHingeTemporalOwner

ROOT=Path(__file__).resolve().parents[1]; COUNT=20_000
FILES=(ROOT/"src/biospur_fusion/c2_articulated_biomechanics/hinge_temporal.py",ROOT/"src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py",ROOT/"tests/test_c2_hinge_temporal.py",ROOT/"tests/test_c2_causal_articulated_pose.py",Path(__file__).resolve())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def projection(v): return {"joint":{n:{"post_projection_signed_deg":v} for n in ("elbow_left","elbow_right","knee_left","knee_right")}}
def main():
 p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if a.output.exists(): raise FileExistsError(a.output)
 start=time.time();before={str(x.relative_to(ROOT)):sha(x) for x in FILES}
 command=[sys.executable,"-m","pytest","-q",str(ROOT/"tests/test_c2_hinge_temporal.py"),str(ROOT/"tests/test_c2_causal_articulated_pose.py")]
 test=subprocess.run(command,cwd=ROOT,text=True,capture_output=True,timeout=60)
 owner=_CausalHingeTemporalOwner(); timings=np.empty(COUNT,dtype=np.int64)
 for i in range(COUNT):
  t=time.perf_counter_ns();plan=owner._prepare_from_pose(pose_revision=0,continuity_generation=0,publication_sequence=i,time_s=i*.005,correction_hash="a"*64,projection_hash="b"*64,projection=projection(float(i%100)),rom_valid=True,fk_valid=True);owner._commit_from_pose(plan,pose_revision=0);timings[i]=time.perf_counter_ns()-t
 p99=float(np.percentile(timings,99)/1e6);rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss);after={str(x.relative_to(ROOT)):sha(x) for x in FILES}
 gates={"tests":test.returncode==0,"p99_below_1ms":p99<1.,"rss_below_300mb":rss<300000,"hashes_stable":before==after,"raw_hxx_not_opened":True,"scientific_pass":False}
 status="READY_FOR_INDEPENDENT_U15_AUDIT" if all(v for k,v in gates.items() if k!="scientific_pass") else "BLOCKED_U15_PREFLIGHT"
 a.output.mkdir(parents=True);(a.output/"TESTS.txt").write_text("$ "+" ".join(command)+"\n"+test.stdout+test.stderr)
 result={"status":status,"scientific_pass":False,"gates":gates,"p99_ms":p99,"max_ms":float(timings.max()/1e6),"rss_kib":rss,"wall_s":time.time()-start,"before_hashes":before,"after_hashes":after}
 (a.output/"RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n");(a.output/"REPORT.md").write_text(f"# U1.5 preflight\n\n{status}; synthetic only; scientific_pass=false; p99={p99:.6f} ms; RSS={rss} KiB.\n")
 rows=[]
 for x in sorted(a.output.iterdir()):
  if x.name!="SHA256SUMS":rows.append(f"{sha(x)}  {x.name}")
 (a.output/"SHA256SUMS").write_text("\n".join(rows)+"\n");print(json.dumps(result,sort_keys=True));return 0 if status.startswith("READY") else 1
if __name__=="__main__": raise SystemExit(main())
