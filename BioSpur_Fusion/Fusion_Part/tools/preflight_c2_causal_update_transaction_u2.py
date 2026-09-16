#!/usr/bin/env python3
"""Synthetic-only U2 focused gates and bounded transaction benchmark."""
from __future__ import annotations
import argparse,hashlib,json,os,pathlib,resource,subprocess,sys,time
from functools import partial
import numpy as np

from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter,RootFilterConfig
from biospur_fusion.root_r3.models import ImuSample,PositionObservation,RootState
from biospur_fusion.c2_uwb_root_world.causal_update_guard import CandidateKind,ReachabilityClass,ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.causal_update_transaction import execute_causal_update_transaction
from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint,HINGE_SPECS
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose

FILES=(
 "src/biospur_fusion/root_r3/estimator.py","tests/root_r3/test_estimator.py",
 "src/biospur_fusion/c2_articulated_biomechanics/hinge_temporal.py","tests/test_c2_hinge_temporal.py",
 "src/biospur_fusion/c2_uwb_calibration/causal_articulated_pose.py","tests/test_c2_causal_articulated_pose.py",
 "src/biospur_fusion/c2_uwb_root_world/causal_update_guard.py","tests/test_c2_causal_update_guard.py",
 "src/biospur_fusion/c2_uwb_root_world/causal_update_transaction.py","tests/test_c2_causal_update_transaction.py",
 "tools/preflight_c2_causal_update_transaction_u2.py")
TEST_COMMAND=[sys.executable,"-m","pytest","-q","tests/root_r3/test_estimator.py",
 "tests/test_c2_causal_articulated_pose.py","tests/test_c2_hinge_temporal.py",
 "tests/test_c2_causal_update_guard.py","tests/test_c2_causal_update_transaction.py"]
COMPILE_COMMAND=[sys.executable,"-m","py_compile",*[path for path in FILES if path.endswith(".py")]]

def digest(path): return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()
def make_root():
 root=CausalDelayedRootFilter(RootState(0.,np.zeros(9),np.eye(9)*.1),RootFilterConfig(fixed_lag_s=1.,nis_limit_3d=1e9),inertial=False)
 for i in range(1,21): root.add_imu(ImuSample(i*.005,i*.005,np.array([0.,0.,9.80665]),np.eye(3),i))
 return root
def envelope(): return ReachabilityEnvelope(ReachabilityClass.NOMINAL,1.,10.,100.,10.,100.,1000.,1.,1.,1.,.01,2,10.,1e8,"synthetic U2 benchmark limits")
def make_pose():
 model={name:HingeJoint(name,parent,child,episode,(1.,0.,0.),(1.,0.,0.),(0.,0.,0.,1.),1.,minimum,maximum,1,1)
        for name,(parent,child,episode,minimum,maximum) in HINGE_SPECS.items()}
 geometry=DisplayProxyGeometry(.5,.3,.4,{segment:.3 for segment in SEGMENTS})
 pose=CausalArticulatedPose(action_start_s=0.,action_stop_s=1.,
  rotations_at_fraction=lambda _:{segment:np.eye(3) for segment in SEGMENTS},geometry=geometry,
  hinge_projector=partial(project_hinge_corrections,model=model))
 for value in (0.,.005,.01): pose.sample(value)
 return pose

def main():
 parser=argparse.ArgumentParser(); parser.add_argument("--output",required=True); args=parser.parse_args()
 output=pathlib.Path(args.output); output.mkdir(parents=True,exist_ok=False); start=time.perf_counter()
 before={path:digest(path) for path in FILES}
 compile_result=subprocess.run(COMPILE_COMMAND,text=True,capture_output=True,timeout=30)
 tests=subprocess.run(TEST_COMMAND,text=True,capture_output=True,timeout=60)
 (output/"TESTS.txt").write_text("COMPILE: "+" ".join(COMPILE_COMMAND)+"\n"+compile_result.stdout+compile_result.stderr+"\nCOMMAND: "+" ".join(TEST_COMMAND)+"\n"+tests.stdout+tests.stderr)
 root_timings=[]; articulated_timings=[]
 if tests.returncode==0:
  for _ in range(500):
   root=make_root(); observation=PositionObservation(.095,.101,np.array([.001,0,0]),np.eye(3)*.1,"tag",(0,1,2,3))
   tick=time.perf_counter_ns(); execute_causal_update_transaction(root=root,observation=observation,kind=CandidateKind.ROOT_POSITION,nominal_envelope=envelope()); root_timings.append((time.perf_counter_ns()-tick)/1e6)
  pose=make_pose(); correction={segment:np.zeros(3) for segment in SEGMENTS}
  for _ in range(500):
   root=make_root(); observation=PositionObservation(.095,.101,np.array([.001,0,0]),np.eye(3)*.1,"tag",(0,1,2,3))
   tick=time.perf_counter_ns(); execute_causal_update_transaction(root=root,observation=observation,
    kind=CandidateKind.ARTICULATED_IK,nominal_envelope=envelope(),pose=pose,
    correction_at_measurement=correction); articulated_timings.append((time.perf_counter_ns()-tick)/1e6)
 root_p99=float(np.percentile(root_timings,99)) if root_timings else float("inf")
 articulated_p99=float(np.percentile(articulated_timings,99)) if articulated_timings else float("inf")
 rss=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss); after={path:digest(path) for path in FILES}
 gates={"py_compile":compile_result.returncode==0,"tests":tests.returncode==0,"hashes_stable":before==after,
        "root_transaction_p99_below_5ms":root_p99<5.,"articulated_transaction_p99_below_5ms":articulated_p99<5.,"rss_below_300mb":rss<300000,
        "raw_hxx_not_opened":True,"scientific_pass":False}
 passed=all(value for key,value in gates.items() if key!="scientific_pass")
 result={"status":"READY_FOR_INDEPENDENT_U2_AUDIT" if passed else "BLOCKED_U2_PREFLIGHT",
  "scientific_pass":False,"gates":gates,"tests_exit_code":tests.returncode,
  "root_benchmark_calls":len(root_timings),"articulated_benchmark_calls":len(articulated_timings),
  "root_p99_ms":root_p99,"root_max_ms":max(root_timings,default=float("inf")),
  "articulated_p99_ms":articulated_p99,"articulated_max_ms":max(articulated_timings,default=float("inf")),"rss_kib":rss,"wall_s":time.perf_counter()-start,
  "before_hashes":before,"after_hashes":after,"real_data_opened":False,"raw_or_hxx_opened":False,"u3_started":False}
 (output/"RESULT.json").write_text(json.dumps(result,sort_keys=True,indent=2)+"\n")
 (output/"REPORT.md").write_text(f"# U2-P synthetic preflight\n\nStatus: `{result['status']}`. Tests exit {tests.returncode}; ROOT p99 {root_p99:.6f} ms; ARTICULATED p99 {articulated_p99:.6f} ms; RSS {rss} KiB. Scientific pass is false. No raw/HXX/U3.\n")
 members=[]
 for name in ("REPORT.md","RESULT.json","TESTS.txt"): members.append(f"{digest(output/name)}  {name}")
 (output/"SHA256SUMS").write_text("\n".join(members)+"\n")
 print(json.dumps(result,sort_keys=True)); return 0 if passed else 1
if __name__=="__main__": raise SystemExit(main())
