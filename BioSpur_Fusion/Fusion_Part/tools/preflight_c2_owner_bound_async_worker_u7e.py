#!/usr/bin/env python3
"""One-shot fixture gate for U7E owner-bound async integration."""
from __future__ import annotations
import argparse,hashlib,json,os,resource,subprocess,sys,time,traceback
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"tests"))
from test_c2_owner_bound_async_worker import PublicReference,sequence
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent,THREAD_ENV
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import AsyncOwnerWorker

CAP=300_000;ECAP=20_000_000
SEALS={ROOT/"logs/c2_async_root_worker_u7d_20260906T175000Z/SHA256SUMS":"9a574d5d0d0fdd5aedc071154e721b76e0af3ebaa8c5c88faf8ff1205256bb28",
 ROOT/"logs/c2_root_worker_owner_wiring_correctness_u7d1_aliasfix_20260906T190000Z/SHA256SUMS":"56e823a6f1c2d2d7736d8b370345fb5da0f4408c545baf1affae624d1deb668f",
 ROOT/"logs/c2_root_worker_owner_wiring_resource_u7d1_20260906T193000Z/SHA256SUMS":"53b4f3d5aa7e57ca1cde14c50f82b43e8cc5f61b88ba63332f3e7b3b8237501b",
 ROOT/"logs/c2_owner_bound_async_worker_u7e_20260906T200000Z/SHA256SUMS":"8e9e97be38bf1ddf86e1ae8ae7e447bb4afb458acc5a8693c1e2e1ea290274e4"}
SEALS[ROOT/"logs/c2_owner_bound_async_worker_u7e1_correction_20260906T205500Z/SHA256SUMS"]="bfad074b1de92930b115f238efe423bb072c314b7698cc2fa656996b06b17f28"
SEALS.update({
 ROOT/"logs/c2_offline_unified_u3_20260906T160900Z/SHA256SUMS":"938862a8c8d6daacd2c3c894865e3373eec2ae159a2a1f8f27e5c8d5559b2640",
 ROOT/"logs/c2_offline_unified_u3_20260906T160900Z_provenance_correction/SHA256SUMS":"3faa2fe70872818877a2545a828a55580d3276a53de908d19cb337bdaca9035b",
 ROOT/"logs/c2_tight_range_u5b_revision_002_action04_first5s_20260906T144646Z/SHA256SUMS":"cb0da6312eefb1d249d216c1560282d1fa221f4132fd9a9f2fa64e56ec5a5574",
 ROOT/"logs/c2_offline_uncertainty_u6_action04_first5s_20260906T151100Z/SHA256SUMS":"64730cd279f46d6c7ec6e03114c7a96471a66bf5eb0a98e4ead7fe44627452ab"})
SEALS[ROOT/"logs/c2_owner_bound_async_worker_u7e3_action04_predecode_blocked_20260906T171000Z/SHA256SUMS"]="c4332ae7e5c82efb9033af8f5a0b02d047506636d0e885b6ba4689be778279a8"
SEALS[ROOT/"logs/c2_owner_bound_async_worker_u7e4_20260906T171600Z/SHA256SUMS"]="8ab5db9acd663f38df6a62e417467250250409afbc46ffec18bf458e5e90ed1b"
SEALS[ROOT/"logs/c2_owner_bound_async_worker_u7e4_action04_20260906T173000Z/SHA256SUMS"]="42eb85f98274f0d4a372bdf43b851ac9e83241cf3618c87f9c2b6d28f375e8c3"
SEALS[ROOT/"logs/c2_owner_bound_async_worker_u7e5_revision_002_20260906T194500Z/SHA256SUMS"]="3d3a5d8b70de957b31826c7b6ade8049f3d207896f8054249471dc1873b32db6"
FILES=(ROOT/"src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py",
 ROOT/"src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",ROOT/"tests/test_c2_owner_bound_async_worker.py",
 ROOT/"tests/test_c2_direct_body_shadow_ab.py",Path(__file__).resolve())
TESTS=("tests/test_c2_owner_bound_async_worker.py","tests/test_c2_direct_body_shadow_ab.py","tests/test_c2_root_worker_event_codec.py",
 "tests/test_c2_async_root_worker.py","tests/test_c2_root_worker_owner_wiring.py",
 "tests/test_c2_causal_update_guard.py","tests/test_c2_causal_update_transaction.py")


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,v):Path(p).write_text(json.dumps(v,indent=2,sort_keys=True,allow_nan=False)+"\n")
def stats(x):
 x=np.asarray(x,float);return {"count":len(x),"p50_ms":float(np.quantile(x,.5)),"p99_ms":float(np.quantile(x,.99)),"maximum_ms":float(np.max(x))}
def ext(p):
 for line in p.read_text().splitlines():
  if "Maximum resident set size (kbytes):" in line:return int(line.rsplit(":",1)[1])
 raise RuntimeError("test RSS missing")
def seal(out,result,before,after,command):
 write(out/"RESULT.json",result);write(out/"HASHES.json",{"before":before,"after":after});(out/"COMMAND.txt").write_text(command+"\n")
 (out/"REPORT.md").write_text("# U7E6 exact batch-shadow fixture\n\n"+f"Status: `{result['status']}`. {result['summary']}\n\n"
  "Channel A is the authoritative U3/U1/U2 root. Channel B is observation-only U5 diagnostics and never feeds A. "
  "Online/action/scientific/calibrated-R/production claims remain false.\n")
 members=sorted(x for x in out.iterdir() if x.name!="SHA256SUMS");(out/"SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in members))
 if sum(x.stat().st_size for x in out.iterdir())>=ECAP:raise RuntimeError("evidence cap")
 return sha(out/"SHA256SUMS")


def main():
 p=argparse.ArgumentParser();p.add_argument("--output",type=Path,required=True);a=p.parse_args()
 if a.output.exists():raise FileExistsError(a.output)
 a.output.mkdir(parents=True);start=time.perf_counter();before={str(x.relative_to(ROOT)):sha(x) for x in FILES}
 command=("timeout --signal=TERM --kill-after=5s 600s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
  "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
  f"tools/preflight_c2_owner_bound_async_worker_u7e.py --output {a.output}")
 base={"schema":"biospur.c2.u7e6.fixture.v1","execution_class":"EXACT_NODE_LOCAL_BATCH_SHADOW_FIXTURE_ONLY",
  "scientific_pass":False,"calibrated_R":False,"online_ready":False,"action_ready":False,"production_ready":False,
  "raw_opened":False,"action04_opened":False,"HXX_opened":False,"bound_seals":list(SEALS.values())}
 try:
  if any(sha(path)!=digest for path,digest in SEALS.items()):raise RuntimeError("seal binding")
  env=dict(os.environ);env.update(THREAD_ENV);env["PYTHONPATH"]="src:tools:.";tt=a.output/"TEST_TIME.txt"
  with (a.output/"TEST_STDOUT.txt").open("w") as out,(a.output/"TEST_STDERR.txt").open("w") as err:
   test=subprocess.run(["/usr/bin/time","-v","-o",str(tt),sys.executable,"-m","pytest","-q",*TESTS],cwd=ROOT,env=env,stdout=out,stderr=err,timeout=180)
  if test.returncode:raise RuntimeError(f"tests failed rc={test.returncode}")
  owner,items=sequence();direct=PublicReference(owner);expected=[direct.process(x) for x in items]
  worker=AsyncOwnerWorker(owner);cold=worker.cold_start_ms;started=time.perf_counter();first=min(x.availability_time_s if isinstance(x,RootWorkerEvent) else x.event.availability_time_s for x in items)
  for item in items:
   when=item.availability_time_s if isinstance(item,RootWorkerEvent) else item.event.availability_time_s
   wait=started+when-first-time.perf_counter()
   if wait>0:time.sleep(wait)
   worker.submit(item)
  hwm=worker.hwm;submit=tuple(worker.submit_ms);actual,final=worker.close_and_collect(len(items))
  errors={"state":0.,"covariance":0.,"h":0.,"sensor_r":0.,"total_r":0.,"s":0.,"nis":0.,"condition":0.};exact=True
  for x,y in zip(actual,expected):
   exact &= (x.kind,x.sequence,x.decision,x.root_reason,x.link_identities,x.link_count,x.guard_calls,x.diagnostic_decisions)==(y.kind,y.sequence,y.decision,y.root_reason,y.link_identities,y.link_count,y.guard_calls,y.diagnostic_decisions)
   for name in ("state","covariance"):errors[name]=max(errors[name],float(np.max(np.abs(getattr(x,name)-getattr(y,name)))))
   for name in ("diagnostic_state","diagnostic_covariance"):
    if getattr(x,name) is not None:errors[name]=max(errors.get(name,0.),float(np.max(np.abs(getattr(x,name)-getattr(y,name)))))
   for name in ("diagnostic_committed_state","diagnostic_committed_covariance"):
    if getattr(x,name) is not None:errors[name]=max(errors.get(name,0.),float(np.max(np.abs(getattr(x,name)-getattr(y,name)))))
   exact &= x.diagnostic_committed_time_s==y.diagnostic_committed_time_s
   for name in ("h","sensor_r","total_r","s"):
    for u,v in zip(getattr(x,name),getattr(y,name)):errors[name]=max(errors[name],float(np.max(np.abs(u-v))))
   for u,v in zip(x.diagnostic_predicted,y.diagnostic_predicted):errors["prediction"]=max(errors.get("prediction",0.),float(np.max(np.abs(u-v))))
   exact &= x.authoritative_weights==y.authoritative_weights
   if x.nis:errors["nis"]=max(errors["nis"],float(np.max(np.abs(np.asarray(x.nis)-np.asarray(y.nis)))))
   if x.condition:errors["condition"]=max(errors["condition"],float(np.max(np.abs(np.asarray(x.condition)-np.asarray(y.condition)))))
   exact &= x.rank==y.rank
   for (_,u),(_,v) in zip(x.diagnostic_weights,y.diagnostic_weights):errors["weights"]=max(errors.get("weights",0.),float(np.max(np.abs(u-v))))
   for (_,um,uv,ut),(_,vm,vv,vt) in zip(x.bias_snapshots,y.bias_snapshots):
    for key,u,v in (("bias_mean",um,vm),("bias_variance",uv,vv)):errors[key]=max(errors.get(key,0.),float(np.max(np.abs(u-v))))
    exact &= np.array_equal(ut,vt,equal_nan=True)
   for left,right in zip(x.diagnostic_node_states,y.diagnostic_node_states):
    exact &= left[0]==right[0]
    for u,v in zip(left[1:],right[1:]):errors["node_states"]=max(errors.get("node_states",0.),float(np.max(np.abs(u-v))))
  uwb=[x for x in actual if x.kind=="UWB"];latency=stats([x.publication_lag_ms for x in actual]);uwb_latency=stats([x.publication_lag_ms for x in uwb])
  imu_service=[x.service_ms for x in actual if x.kind=="IMU"];uwb_service=[x.service_ms for x in uwb]
  utilization=float(np.mean(imu_service)/5.0+np.mean(uwb_service)/120.048)
  parent=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss);test_rss=ext(tt);after={str(x.relative_to(ROOT)):sha(x) for x in FILES}
  drain=final["sentinel"] and final["count"]==len(items) and final["qsize"]==0 and final["exitcode"]==0 and not final["alive"]
  bias_evolved=bool(uwb and any(np.any(item[1]>0) and np.any(np.isfinite(item[3])) for item in uwb[-1].bias_snapshots))
  weights_changed=bool(len(uwb)==2 and any(not np.array_equal(x[1],y[1]) for x,y in zip(uwb[0].diagnostic_weights,uwb[1].diagnostic_weights)))
  ready=exact and all(v<=1e-12 for v in errors.values()) and len(uwb)==2 and all(x.link_count==80 and x.guard_calls==1 for x in uwb)
  ready &= bias_evolved and weights_changed
  ready &= utilization<1 and all({value for _,_,value in x.authoritative_weights}=={1.0} for x in uwb)
  ready &= drain and hwm<=64 and parent<CAP and final["rss"]<CAP and test_rss<CAP and before==after
  sigma_qualities=sorted({int(q) for item in items if not isinstance(item,RootWorkerEvent) for row in item.event.payload for q in row.quality})
  ready &= 25 in sigma_qualities and 100 in sigma_qualities and len(sigma_qualities)>2
  status="READY_FOR_MONITOR_U7E6_FIXTURE_REVIEW" if ready else "BLOCKED_U7E6_FIXTURE"
  result={**base,"status":status,"summary":f"two groups; parity {exact}; UWB latency max {uwb_latency['maximum_ms']:.3f} ms.",
   "cold_start_ms":cold,"events":len(items),"uwb_groups":len(uwb),"parity_exact":exact,"maximum_absolute_errors":errors,
   "same_links_order_no_deletion":all(x.link_count==80 and len(x.link_identities)==80 and len(set(x.link_identities))==80 for x in uwb),"u1_calls_per_group":[x.guard_calls for x in uwb],
   "dynamic_weights_changed":weights_changed,"persistent_bias_evolved":bias_evolved,
   "channel_a_weight_policy":"UNIT_INFORMATION_WEIGHT_EXACT_U3","channel_a_sigma_policy":"U3_LAYOUT_PLUS_FLOOR",
   "channel_b_sigma_policy":"U5B_QUALITY_ONLY","fixture_quality_values":sigma_qualities,"service_utilization":utilization,
   "channel_b_weights_owner":"RECONSTRUCTED_INSIDE_CHRONOLOGICAL_OBSERVATION_JOURNAL_NOT_PACKET_INPUT",
   "channel_b_shadow_evaluator":"ONE_NODE_EIGHT_ANCHOR_EXACT_BATCH_NO_CROSS_TOKEN_CACHE",
   "channel_b_reference_time_owner":"ClockModel.seconds(strobe_us+t_round_us/2)",
   "service_utilization_formula":"mean(IMU service ms)/5ms + mean(UWB service ms)/120.048ms",
   "imu_service_ms":stats(imu_service),"uwb_service_ms":stats(uwb_service),
   "diagnostic_channel_feeds_authoritative_root":False,"diagnostic_uncertainty_status":"PROVISIONAL_UNCALIBRATED_OBSERVATION_ONLY",
   "latency_ms":latency,"uwb_latency_ms":uwb_latency,"submit_ms":stats(submit),"queue_hwm":hwm,"queue_capacity":64,
   "literal_drain":drain,"future_access":False,"resources":{"parent_kib":parent,"worker_kib":final["rss"],"test_kib":test_rss},
   "source_hashes_unchanged":before==after,"wall_s":time.perf_counter()-start}
  digest=seal(a.output,result,before,after,command);print(json.dumps({"status":status,"seal_sha256":digest,"uwb_max_ms":uwb_latency["maximum_ms"]},sort_keys=True));return 0 if ready else 2
 except BaseException as exc:
  after={str(x.relative_to(ROOT)):sha(x) for x in FILES};result={**base,"status":"BLOCKED_U7E6_FIXTURE","summary":f"First gate: {type(exc).__name__}: {exc}","failure":traceback.format_exc(),"wall_s":time.perf_counter()-start}
  digest=seal(a.output,result,before,after,command);print(json.dumps({"status":result["status"],"seal_sha256":digest,"failure":str(exc)},sort_keys=True));return 2


if __name__=="__main__":raise SystemExit(main())
