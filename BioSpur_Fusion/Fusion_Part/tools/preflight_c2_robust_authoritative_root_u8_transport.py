#!/usr/bin/env python3
"""One-shot, no-raw U8 typed-transport production-shape preflight."""
from __future__ import annotations

import argparse,hashlib,json,math,os,pickle,queue,resource,sys,threading,time,traceback
from dataclasses import replace
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"tests"))
import run_c2_owner_bound_async_worker_u7e4_action04 as legacy
import run_c2_robust_authoritative_root_u8_action04 as u8
from test_c2_owner_bound_async_worker import packets
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import group_epoch_times_ns
from biospur_fusion.c2_timing_contract import canonical_clock_global_ns
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import AsyncOwnerWorker,BShadowGeometryOwner,BShadowSnapshotOwner,BoundGroupPacket,DirectOwnerSequence
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import PoseTagLinkOwner
from biospur_fusion.root_r3.models import ImuSample

ACTION04=ROOT/"logs/c2_robust_authoritative_root_u8_action04_20260906T210000Z"
ACTION04_SEAL="0de3d18bbf9032087c49ec41ccf14e19a7dd1621b0975a5876ee6072308268fb"
GROUPS=16;PERIOD=.120048;RSS_CAP=300_000;SIZE_CAP=10_000_000
SOURCES=(ROOT/"src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py",ROOT/"tests/test_c2_owner_bound_async_worker.py",Path(__file__).resolve())

def _sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def _seal(path):
 rows="".join(f"{_sha(item)}  {item.name}\n" for item in sorted(path.iterdir()) if item.is_file() and item.name!="SHA256SUMS")
 (path/"SHA256SUMS").write_text(rows);return _sha(path/"SHA256SUMS")
def _stats(values):
 a=np.asarray(tuple(values),float);return {"count":len(a),"mean_ms":float(a.mean()),"p99_ms":float(np.quantile(a,.99)),"maximum_ms":float(a.max())}

def _fixture():
 owner,first,_=packets();base_pose={(x.node,x.anchor):x for x in first.pose_links};base_shadow={x.node:x for x in first.b_shadow_owner.snapshots}
 row_groups=[]
 for index in range(GROUPS):
  rows=[]
  for row_index,row in enumerate(first.event.payload):
   mask=row.valid_mask
   if index>=6 and row_index==0:mask=0x07
   if index>=11 and row_index==1:mask=0x07
   delta=index*120_048;rows.append(replace(row,sequence=index+1,sweep=index+1,strobe_us=row.strobe_us+delta,frame_us=row.frame_us+delta,valid_mask=mask))
  row_groups.append(tuple(rows))
 required={node:clock.last_timer_us for node,clock in owner.clocks.items()}
 for rows in row_groups:
  for row in rows:required[row.node]=max(required[row.node],row.frame_us,math.ceil(max(row.strobe_us+.5*x for x in row.t_round_us)))
 owner=replace(owner,clocks={node:replace(clock,last_timer_us=required[node]) for node,clock in owner.clocks.items()},digest="")
 groups=[]
 for index,rows in enumerate(row_groups):
  poses=[];snapshots=[]
  for row in rows:
   queries=[]
   for anchor in range(8):
    query=owner.clocks[row.node].link_time_ns(event_boot_epoch=row.boot,strobe_us=row.strobe_us,t_round_us=row.t_round_us[anchor]);queries.append(query)
    old=base_pose[(row.node,anchor)];pose_time=int(query//5_000_000*5_000_000);dt=(pose_time-old.pose_time_ns)*1e-9
    poses.append(PoseTagLinkOwner(row.node,anchor,query,pose_time,old.offset_world_m+dt*old.offset_velocity_world_mps,old.offset_velocity_world_mps,pose_time//5_000_000,old.source_revision+index,old.source_sha256))
   old=base_shadow[row.node];query=min(queries);pose_time=int(query//5_000_000*5_000_000)
   snapshots.append(BShadowSnapshotOwner(row.node,old.action,pose_time//5_000_000,pose_time,query,old.offsets_world_m,old.normals_world,old.joints_relative_world_m,old.source_sha256))
  _,_,availability=group_epoch_times_ns(rows,clocks=owner.clocks);availability=canonical_clock_global_ns(availability)
  event=RootWorkerEvent(20_000+index,availability*1e-9,"UWB",rows)
  groups.append(BoundGroupPacket(owner.digest,event,tuple(poses),(),first.a_sigma_owner,first.b_sigma_owner,BShadowGeometryOwner(first.b_shadow_owner.geometry,tuple(snapshots),first.b_shadow_owner.provenance),availability_global_ns=availability))
 timeline=[];value=owner.initial_state.time_s+.005;sequence=1000;stop=groups[-1].event.availability_time_s
 while value<=stop+1e-12:
  event=RootWorkerEvent(sequence,value,"IMU",ImuSample(value,value,np.array([0.,0.,9.80665]),np.eye(3),sequence));timeline.append((value,0,event));sequence+=1;value+=.005
 timeline.extend((group.event.availability_time_s,1,group) for group in groups);timeline.sort(key=lambda x:(x[0],x[1]))
 expected_counts=[sum(1 for row in group.event.payload if row.valid_mask.bit_count()>=4) for group in groups]
 if expected_counts!=[10]*6+[9]*5+[8]*5:raise RuntimeError("x/10 fixture inventory")
 return owner,timeline,expected_counts

def _drain(worker,timeline):
 results=[];final=[];errors=[]
 def run():
  while True:
   try:kind,value=worker._out.get(timeout=.1)
   except queue.Empty:continue
   if kind=="RESULT":results.append(pickle.loads(value))
   elif kind=="FINAL":final.append(value);return
   else:errors.append(RuntimeError(value));return
 thread=threading.Thread(target=run,daemon=False);thread.start();start=time.perf_counter();first=timeline[0][0]
 try:
  for when,_,item in timeline:
   wait=start+when-first-time.perf_counter()
   if wait>0:time.sleep(wait)
   worker.submit(item)
  worker._in.put(None,timeout=5);thread.join(30)
  if thread.is_alive() or errors or len(final)!=1:raise RuntimeError("async drain failure" if not errors else str(errors[0]))
  worker._p.join(2)
  if worker._p.is_alive() or worker._p.exitcode!=0 or len(results)!=len(timeline):raise RuntimeError("worker termination failure")
  final[0].update({"qsize":worker._in.qsize(),"exitcode":worker._p.exitcode,"alive":worker._p.is_alive()});worker._close_queues();worker._closed=True
  return results,final[0]
 except BaseException:worker.abort();thread.join(2);raise

def main():
 parser=argparse.ArgumentParser();parser.add_argument("--output",type=Path,required=True);args=parser.parse_args()
 if args.output.exists():raise FileExistsError(args.output)
 args.output.mkdir(parents=True);started=time.perf_counter();base={"schema":"biospur.c2.u8.typed-transport-preflight.v1","attempt":1,"no_retry":True,"raw_opened":False,"action04_opened":False,"HXX_opened":False,"bound_action04_seal":ACTION04_SEAL,"prior_status":"MECHANISM_QUALIFIED_DELIVERY_FAILED","calibrated_R":False,"scientific_pass":False,"product_ready":False}
 try:
  legacy.verify_seal(ACTION04,ACTION04_SEAL);before={str(x.relative_to(ROOT)):_sha(x) for x in SOURCES};owner,timeline,expected_x10=_fixture()
  direct=DirectOwnerSequence(owner);expected=[direct.process(item) for _,_,item in timeline]
  worker=AsyncOwnerWorker(owner,capacity=64);actual,final=_drain(worker,timeline)
  exact=len(actual)==len(expected) and all(u8._exact_result(a,b) for a,b in zip(actual,expected));groups=[x for x in actual if x.kind=="UWB"]
  x10=[len(x.authoritative_nodes) for x in groups];bias_ok,bias_rows=u8._bias_audit(groups,sorted(owner.clocks))
  publication=_stats(x.publication_lag_ms for x in actual);submit=_stats(worker.submit_ms);encode=_stats(worker.encode_ms);put=_stats(worker.put_ms)
  child=final["transport_timings_ms"];total=_stats(child["transport.total_service"]);imu_total=_stats(v for v,x in zip(child["transport.total_service"],actual) if x.kind=="IMU");uwb_total=_stats(v for v,x in zip(child["transport.total_service"],actual) if x.kind=="UWB")
  effective=(encode["mean_ms"]+put["mean_ms"]+imu_total["mean_ms"])/5.+(encode["mean_ms"]+put["mean_ms"]+uwb_total["mean_ms"])/(PERIOD*1000.)
  drain=final["count"]==len(timeline) and final["sentinel"] and final["qsize"]==0 and final["exitcode"]==0 and not final["alive"]
  gates={"exact_parity":exact,"u1":sum(x.guard_calls for x in groups)==GROUPS,"x10":x10==expected_x10,"bias_selected_only":bias_ok,"lossless_drain":drain,"queue_hwm":worker.hwm<64,"submit_p99":submit["p99_ms"]<5,"publication":publication["p99_ms"]<150 and publication["maximum_ms"]<200,"effective_utilization":effective<1,"wall":time.perf_counter()-started<120,"rss":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss<RSS_CAP and final["rss"]<RSS_CAP,"source_unchanged":before=={str(x.relative_to(ROOT)):_sha(x) for x in SOURCES}}
  status="U8_TYPED_TRANSPORT_PREFLIGHT_PASS" if all(gates.values()) else "BLOCKED_U8_TYPED_TRANSPORT_PREFLIGHT"
  result={**base,"status":status,"counts":{"events":len(actual),"groups":len(groups),"x10":x10},"gates":gates,"queue_hwm":worker.hwm,"parent":{"submit":submit,"encode":encode,"put":put},"child":{"total":total,"imu_total":imu_total,"uwb_total":uwb_total,"stages":{key:_stats(value) for key,value in child.items()}},"publication":publication,"effective_utilization":effective,"bias_audit":bias_rows,"source_sha256":before,"resources":{"parent_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,"worker_rss_kib":final["rss"]},"wall_s":time.perf_counter()-started}
  (args.output/"RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n");(args.output/"REPORT.md").write_text(f"# U8 typed transport preflight\n\nStatus: `{status}`. No raw/action/HXX data were opened. Exact direct/async semantic parity: {exact}. Effective transport-inclusive utilization: {effective:.6f}.\n")
  if sum(x.stat().st_size for x in args.output.iterdir())>=SIZE_CAP:raise RuntimeError("evidence cap")
  seal=_seal(args.output);print(json.dumps({"status":status,"seal_sha256":seal,"gates":gates,"wall_s":result["wall_s"]},sort_keys=True));return 0 if all(gates.values()) else 2
 except BaseException as exc:
  failure={**base,"status":"BLOCKED_U8_TYPED_TRANSPORT_PREFLIGHT","failure":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc(),"wall_s":time.perf_counter()-started,"rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss};(args.output/"FAILURE.json").write_text(json.dumps(failure,indent=2,sort_keys=True)+"\n");seal=_seal(args.output);print(json.dumps({"status":failure["status"],"failure":failure["failure"],"seal_sha256":seal},sort_keys=True));return 2

if __name__=="__main__":raise SystemExit(main())
