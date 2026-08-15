#!/usr/bin/env python3
"""D-1 R3A instrumentation and one-shot R3B topology qualification."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT/"Fusion_Part/src"))
from biospur_fusion.imu_multi_action_engineering_v1.common_time import build_common_timeline
from biospur_fusion.imu_multi_action_engineering_v1.pipeline import load_q2_cache
from biospur_fusion.imu_multi_action_revision_d.r3_cycle import relative_rate_signal as old_relative_rate_signal, select_pre_reference as old_select_pre_reference
from biospur_fusion.imu_multi_action_revision_d.r3b_topology import (
 build_chain_signal,classify_reference_quality,detect_active_bouts,detect_cycles,huber_so3_reference,
 legacy_reference_diagnostic,phase_groups_from_cycle_vectors,quantiles,relative_activity,relative_orientation,
 runs,sha256,synthetic_qualification,
)

BASELINE="7c659b24b714b1ef4d9143658d1a6ee49ffb92ce"
MODULE=ROOT/"Fusion_Part/src/biospur_fusion/imu_multi_action_revision_d/r3b_topology.py"
RUNNER=Path(__file__).resolve()
ACTIONS=("initial_still_attempt2","t_pose","arms","left_elbow","right_elbow_attempt2","left_knee","right_knee","left_heel","right_heel","squats","trunk")

def sanitize(x:Any)->Any:
 if isinstance(x,np.ndarray):return sanitize(x.tolist())
 if isinstance(x,(np.integer,)):return int(x)
 if isinstance(x,(np.floating,)):return None if not np.isfinite(x) else float(x)
 if isinstance(x,float):return None if not math.isfinite(x) else x
 if isinstance(x,dict):return {str(k):sanitize(v) for k,v in x.items()}
 if isinstance(x,(list,tuple)):return [sanitize(v) for v in x]
 return x

def dump(path:Path,value:Any)->None:path.write_text(json.dumps(sanitize(value),sort_keys=True,separators=(",",":"),allow_nan=False)+"\n")
def head()->str:return subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip()
def manifest(out:Path)->None:dump(out/"SHA256_MANIFEST.json",{str(p.relative_to(out)):sha256(p) for p in sorted(out.rglob("*")) if p.is_file() and p.name!="SHA256_MANIFEST.json"})

def config_paths(config:Path)->dict[str,Path]:
 return {name:config/name for name in ("R3B_SIGNAL_DERIVED_ACTION_CONTRACT.json","R3B_REFERENCE_AND_NEUTRAL_SEMANTICS.json","R3B_ACTION_CHAIN_MAP.json","R3B_GATE_DERIVATION_RULES.json")}

def freeze(args:argparse.Namespace)->dict[str,Any]:
 if args.output.exists():raise FileExistsError(args.output)
 if head()!=BASELINE:raise RuntimeError("baseline HEAD changed")
 paths=config_paths(args.config);loaded={k:json.loads(p.read_text()) for k,p in paths.items()};args.output.mkdir(parents=True)
 for name,p in paths.items():shutil.copyfile(p,args.output/name)
 original=args.original_r3;provenance={
  "schema":"biospur-r3a-r3b-pre-real-freeze-v1","baseline_commit":BASELINE,"contracts":{k:sha256(p) for k,p in paths.items()},
  "source_sha256":sha256(MODULE),"runner_sha256":sha256(RUNNER),
  "R2_timeline_sha256":sha256(args.r2/"ACTION_PHASE_TIMELINE.json"),
  "R2_decomposition_sha256":sha256(args.r2_audit/"D_MINUS_1_R2_FAILURE_DECOMPOSITION.json"),
  "R3_original_manifest_sha256":sha256(original/"SHA256_MANIFEST.json"),"R3_original_exception_sha256":sha256(original/"EXECUTION_FAILURE.json"),
  "R2_DISPOSITION":"FAIL_IMMUTABLE_UNDER_ORIGINAL_CONTRACT","R3_ORIGINAL_DISPOSITION":"FAIL_REQUIRED_ACTIVE_BOUT_AMBIGUOUS","R3_ORIGINAL_ADOPTABLE":False,
  "corrected_interpretation":{"OBSERVED_R3_FAILURE":"PRE_REFERENCE_ROBUST_RETENTION_BELOW_FROZEN_MINIMUM","ACTIVE_BOUT_AMBIGUITY":"NOT_ESTABLISHED_BECAUSE_ACTIVE_BOUT_WAS_NOT_EVALUATED","CYCLE_TOPOLOGY":"NOT_EVALUATED","ROOT_CAUSE":"NOT_CLASSIFIED"},
  "actual_r3a_retained_fraction_read":False,"real_capture_accessed":False,"FINAL_STILL_STATUS":"SEALED","D0":"NOT_STARTED"
 }
 dump(args.output/"R3A_R3B_FREEZE.json",provenance);manifest(args.output);return provenance

def verify_freeze(freeze_dir:Path)->tuple[dict[str,Any],dict[str,Any],dict[str,Any],dict[str,Any],dict[str,Any]]:
 f=json.loads((freeze_dir/"R3A_R3B_FREEZE.json").read_text());names=("R3B_SIGNAL_DERIVED_ACTION_CONTRACT.json","R3B_REFERENCE_AND_NEUTRAL_SEMANTICS.json","R3B_ACTION_CHAIN_MAP.json","R3B_GATE_DERIVATION_RULES.json")
 for name in names:
  if sha256(freeze_dir/name)!=f["contracts"][name]:raise RuntimeError(f"frozen contract changed: {name}")
 if sha256(MODULE)!=f["source_sha256"] or sha256(RUNNER)!=f["runner_sha256"]:raise RuntimeError("source changed after freeze")
 return f,*[json.loads((freeze_dir/name).read_text()) for name in names]

def context(args:argparse.Namespace):
 phase=json.loads((args.phase_a/"RESULT.json").read_text());cache=args.phase_a/"Q2_HUMAN_QUASI_STATIC_CACHE.npz"
 if not phase["pass"] or sha256(cache)!=phase["q2_cache_sha256"]:raise RuntimeError("Phase-A cache binding failed")
 if phase["final_still"]!="SEALED" or phase["data_access"]["final_still"]!="SEALED_NOT_OPENED":raise RuntimeError("final_still firewall failed")
 r2=json.loads((args.r2/"ACTION_PHASE_TIMELINE.json").read_text());r2_result=json.loads((args.r2/"RESULT.json").read_text())
 if sha256(args.r2/"ACTION_PHASE_TIMELINE.json")!=r2_result["action_phase_timeline_sha256"]:raise RuntimeError("R2 immutable binding failed")
 gates=json.loads(args.legacy_gates.read_text());q2=load_q2_cache(cache);start=min(x["search_domain_ns"][0] for x in r2["actions"].values());stop=max(x["search_domain_ns"][1] for x in r2["actions"].values());timeline=build_common_timeline(q2,start,stop,gates["common_time"]);nodes={n:i for i,n in enumerate(timeline.node_order)};segments={s:nodes[n] for n,s in gates["node_to_segment"].items()};segment_node={s:n for n,s in gates["node_to_segment"].items()};domains={a:tuple(r2["actions"][a]["search_domain_ns"]) for a in ACTIONS};return phase,cache,r2,gates,q2,timeline,segments,segment_node,domains

def chains(chain_map:Mapping[str,Any])->list[dict[str,Any]]:
 out=[]
 for action,item in chain_map["actions"].items():
  for kind in ("primary_chains","diagnostic_chains"):
   for name,pair in item.get(kind,{}).items():out.append({"key":f"{action}:{name}","action":action,"name":name,"parent":pair[0],"child":pair[1],"role":"PRIMARY" if kind=="primary_chains" else "DIAGNOSTIC"})
 return out

def domain_rows(timeline:Any,domain:tuple[int,int])->np.ndarray:return np.flatnonzero((timeline.time_ns>=domain[0])&(timeline.time_ns<=domain[1]))

def max_invalid_gap_s(mask:np.ndarray,hz:float)->float:return max([b-a for a,b in runs(~mask)] or [0])/hz

def classify_r3a(diag:Mapping[str,Any],contract:Mapping[str,Any])->str:
 if diag["pre_reference_candidate"] is None or diag["pair_valid_fraction"]<float(contract["baseline"]["minimum_valid_fraction"]):return "INSUFFICIENT_VALID_DATA"
 if diag["maximum_invalid_gap_s"]>float(contract["active_bout"]["maximum_bridgeable_invalid_gap_s"]):return "TIMESTAMP_OR_VALIDITY_ERROR"
 if (diag["candidate_activity_snr"].get("p90") or 0)>float(contract["active_bout"]["onset_activity_z"]):return "WINDOW_CONTAMINATION"
 retained=diag["legacy_reference"].get("retained_fraction")
 if retained is not None and retained<float(contract["baseline"]["legacy_retained_fraction_threshold"]):return "ROBUST_CUTOFF_TOO_NARROW"
 return "NATURAL_POSTURE_VARIATION"

def plot_r3a(path:Path,title:str,timeline:Any,rows:np.ndarray,activity:Mapping[str,np.ndarray],legacy:Mapping[str,Any],reference:Mapping[str,Any]|None)->None:
 t=(timeline.time_ns[rows]-timeline.time_ns[rows[0]])/1e9;fig,axes=plt.subplots(4,1,figsize=(14,12),sharex=True,constrained_layout=True);rate=activity["rate_rad_s"][rows];inc=activity["increment_rad"][rows]
 axes[0].plot(t,rate,label="relative angular activity");axes[0].set_ylabel("rad/s");axes[1].plot(t,inc,label="relative increment",color="#9467bd");axes[1].set_ylabel("rad")
 if reference is not None and legacy.get("status")=="AVAILABLE":
  rel=reference["relative"];centre=np.asarray(legacy["robust_centre_matrix"]);from biospur_fusion.imu_multi_action_revision_d.r3b_topology import residual_rotvec
  q=np.linalg.norm(residual_rotvec(centre,rel),axis=1);axes[2].plot(t,q[rows],label="geodesic excursion");candidate=set(legacy["candidate_rows"]);inlier=set(legacy["inlier_rows"]);mask=np.array([r in candidate for r in rows]);axes[2].scatter(t[mask],q[rows][mask],s=8,c=["#2ca02c" if r in inlier else "#d62728" for r in rows[mask]])
  peaks,_=__import__("scipy.signal",fromlist=["find_peaks"]).find_peaks(np.nan_to_num(q[rows]),prominence=.06);axes[3].plot(t,q[rows]);axes[3].scatter(t[peaks],q[rows][peaks],c="#ff7f0e",s=18,label="extrema candidates")
 axes[0].set_title(f"R3A old-reference instrumentation — {title}\nlegacy retention={legacy.get('retained_fraction')}");axes[3].set_xlabel("seconds from broad envelope start")
 for ax in axes:ax.grid(alpha=.2);ax.legend(loc="upper right",fontsize=7)
 fig.savefig(path,dpi=130);plt.close(fig)

def run_r3a(args:argparse.Namespace)->dict[str,Any]:
 if args.output.exists():raise FileExistsError(args.output)
 freeze_record,contract,reference_semantics,chain_map,derivation=verify_freeze(args.freeze)
 phase,cache,r2,gates,q2,timeline,segments,segment_node,domains=context(args);old_contract=json.loads(args.original_r3_contract.read_text());args.output.mkdir(parents=True);plot_dir=args.output/"r3a_diagnostic_plots";plot_dir.mkdir();records=[]
 for spec in chains(chain_map):
  rows=domain_rows(timeline,domains[spec["action"]]);pi,ci=segments[spec["parent"]],segments[spec["child"]];rel=relative_orientation(timeline.rotation[:,pi],timeline.rotation[:,ci]);valid=timeline.valid[:,pi]&timeline.valid[:,ci]&np.isfinite(rel).all((1,2));cov=timeline.covariance_rad2[:,pi]+timeline.covariance_rad2[:,ci];activity=relative_activity(timeline.time_ns,rel,cov,valid,contract);old_rate=old_relative_rate_signal(timeline.time_ns,rel,cov,valid,old_contract);candidate=old_select_pre_reference(old_rate["snr"],old_rate["valid"],rows,old_contract);legacy=legacy_reference_diagnostic(rel,[] if candidate is None else candidate["row_indices"],float(contract["coordinate"]["relative_orientation_uncertainty_floor_rad"]));pnode,cnode=segment_node[spec["parent"]],segment_node[spec["child"]]
  raw_counts={node:int(np.sum((q2[node].time_ns>=domains[spec["action"]][0])&(q2[node].time_ns<=domains[spec["action"]][1]))) for node in (pnode,cnode)};valid_counts={pnode:int(np.sum(timeline.valid[rows,pi])),cnode:int(np.sum(timeline.valid[rows,ci]))};candidate_rows=np.asarray([] if candidate is None else candidate["row_indices"],int);candidate_snr=old_rate["snr"][candidate_rows] if len(candidate_rows) else np.asarray([]);q2_sigma=np.sqrt(np.maximum(np.trace(cov[rows],axis1=1,axis2=2)/3,0));diag={"chain_key":spec["key"],**spec,"broad_search_envelope":{"start_global_time_ns":domains[spec["action"]][0],"stop_global_time_ns":domains[spec["action"]][1],"common_grid_start_row":int(rows[0]),"common_grid_stop_row_exclusive":int(rows[-1]+1),"global_time_ns":timeline.time_ns[rows].tolist()},"raw_sample_count_by_node":raw_counts,"common_valid_sample_count_by_node":valid_counts,"pair_valid_count":int(np.sum(valid[rows])),"pair_valid_fraction":float(np.mean(valid[rows])),"pre_reference_candidate":candidate,"legacy_reference":legacy,"candidate_activity_snr":quantiles(candidate_snr),"relative_activity_rad_s":quantiles(activity["rate_rad_s"][rows]),"relative_increment_rad":quantiles(activity["increment_rad"][rows]),"q2_orientation_sigma_rad":quantiles(q2_sigma),"maximum_invalid_gap_s":max_invalid_gap_s(valid[rows],float(contract["common_time"]["rate_hz"])),"timestamp_interpolation":"PER_NODE_SO3_SLERP_ON_COMMON_GLOBAL_TIME","validity_mask":"PAIRWISE_PARENT_AND_CHILD_REQUIRED","transition_contamination":bool((quantiles(candidate_snr).get("p90") or 0)>float(contract["active_bout"]["onset_activity_z"])),"natural_posture_variation":"POSSIBLE_NOT_FORCED_TO_ZERO","chain_identity_anomaly":"NOT_ESTABLISHED_BY_REFERENCE_ONLY","Q2_confidence":"PROPAGATED_COVARIANCE_RECORDED"};diag["root_cause_classification"]=classify_r3a(diag,contract);records.append(diag);plot_r3a(plot_dir/f"{spec['action']}__{spec['name']}.png",spec["key"],timeline,rows,activity,legacy,{"relative":rel} if candidate is not None else None)
 dump(args.output/"R3A_PRE_REFERENCE_DIAGNOSTIC.json",{"schema":"biospur-r3a-pre-reference-diagnostic-v1","chains":records,"original_r3_failure_interpretation":{"OBSERVED_R3_FAILURE":"PRE_REFERENCE_ROBUST_RETENTION_BELOW_FROZEN_MINIMUM","ACTIVE_BOUT_AMBIGUITY":"NOT_ESTABLISHED_BECAUSE_ACTIVE_BOUT_WAS_NOT_EVALUATED","CYCLE_TOPOLOGY":"NOT_EVALUATED","ROOT_CAUSE":"NOT_CLASSIFIED"}})
 with (args.output/"R3A_ALL_CHAIN_REFERENCE_TABLE.csv").open("w",newline="") as f:
  fields=["chain_key","action","name","role","parent","child","pair_valid_fraction","candidate_count","inlier_count","retained_fraction","residual_p50_rad","residual_p95_rad","activity_p50_rad_s","activity_p95_rad_s","root_cause_classification"]
  w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
  for d in records:
   l=d["legacy_reference"];w.writerow({"chain_key":d["chain_key"],"action":d["action"],"name":d["name"],"role":d["role"],"parent":d["parent"],"child":d["child"],"pair_valid_fraction":d["pair_valid_fraction"],"candidate_count":l.get("candidate_count"),"inlier_count":l.get("inlier_count"),"retained_fraction":l.get("retained_fraction"),"residual_p50_rad":l.get("residual_quantiles_rad",{}).get("p50"),"residual_p95_rad":l.get("residual_quantiles_rad",{}).get("p95"),"activity_p50_rad_s":d["relative_activity_rad_s"].get("p50"),"activity_p95_rad_s":d["relative_activity_rad_s"].get("p95"),"root_cause_classification":d["root_cause_classification"]})
 classification={"schema":"biospur-r3a-failure-classification-v1","chains":{d["chain_key"]:d["root_cause_classification"] for d in records},"counts":{name:sum(d["root_cause_classification"]==name for d in records) for name in ("WINDOW_CONTAMINATION","NATURAL_POSTURE_VARIATION","Q2_ORIENTATION_NOISE","TIMESTAMP_OR_VALIDITY_ERROR","ROBUST_CUTOFF_TOO_NARROW","WRONG_PRIMARY_CHAIN","INSUFFICIENT_VALID_DATA","MIXED")},"no_first_failure_abort":len(records)==len(chains(chain_map)),"active_bout_ambiguity_not_inferred_from_retention":True};dump(args.output/"R3A_FAILURE_CLASSIFICATION.json",classification)
 access={"opened":["CALIBRATION_ONLY_Q2_CACHE","ELEVEN_BROAD_WINDOWS","R2_R3_DERIVED_EVIDENCE"],"forbidden_opened":[],"FINAL_STILL_STATUS":"SEALED","final_still_samples_accessed":False,"D0":"NOT_STARTED","JACOBIAN":"NOT_STARTED","SOLVER":"NOT_STARTED"};dump(args.output/"R3A_DATA_ACCESS_AUDIT.json",access);result={"pass":True,"terminal_outcome":"R3A_INSTRUMENTATION_COMPLETE","all_chain_count":len(records),"all_chains_evaluated":len(records)==len(chains(chain_map)),"real_calibration_only_cache_accessed":True,"FINAL_STILL_STATUS":"SEALED","D0":"NOT_STARTED"};dump(args.output/"RESULT.json",result);(args.output/"REPORT.md").write_text(f"# R3A all-chain old-reference instrumentation\n\n`R3A_INSTRUMENTATION_COMPLETE`\n\nAll {len(records)} declared chains were instrumented without first-failure abort. Retention is diagnostic only; no active-bout or cycle verdict was inferred. See `R3A_FAILURE_CLASSIFICATION.json`.\n\n`FINAL_STILL_STATUS = SEALED`\n");manifest(args.output);return result

def qualify(args:argparse.Namespace)->dict[str,Any]:
 if args.output.exists():raise FileExistsError(args.output)
 f,contract,reference,chains_cfg,derivation=verify_freeze(args.freeze);args.output.mkdir(parents=True);result=synthetic_qualification(contract);dump(args.output/"R3B_SYNTHETIC_QUALIFICATION.json",result);dump(args.output/"R3B_QUALIFICATION_BINDING.json",{"contract_shas":f["contracts"],"source_sha256":f["source_sha256"],"runner_sha256":f["runner_sha256"],"r3a_result_sha256":sha256(args.r3a/"RESULT.json"),"real_r3b_accessed":False,"pass":result["pass"],"FINAL_STILL_STATUS":"SEALED"});manifest(args.output);return result

def chain_summary(signal:Mapping[str,Any],bouts:list[dict[str,Any]],cycle:Mapping[str,Any],legacy:Mapping[str,Any],rows:np.ndarray,timeline:Any,contract:Mapping[str,Any])->dict[str,Any]:
 quality=classify_reference_quality(legacy,signal.get("baseline"),contract);last=max([x["stop_row_exclusive"] for x in bouts] or [signal["baseline"]["stop_row_exclusive"]]);tail=rows[rows>=last];z=signal["activity_z"];quiet=[]
 for a,b in runs(signal["valid"][tail]&np.isfinite(z[tail])&(z[tail]<=float(contract["active_bout"]["offset_activity_z"]))):
  if b-a>=round(.4*float(contract["common_time"]["rate_hz"])):quiet=tail[a:b].tolist();break
 if quiet:
  q=float(np.nanmedian(signal["smoothed_excursion_rad"][quiet]));u=float(np.nanmedian(signal["excursion_uncertainty_rad"][quiet]));post="POST_NEUTRAL_AVAILABLE" if q<=4*u else "POST_BOUT_NON_NEUTRAL_HOLD"
 else:post="NOT_OBSERVED"
 active_rows=sorted({r for b in bouts for r in range(b["start_row"],b["stop_row_exclusive"])});maximum=float(np.nanmax(signal["smoothed_excursion_rad"][rows]));sigma=float(np.nanmedian(signal["excursion_uncertainty_rad"][rows]));functional=bool(active_rows) and maximum>=float(contract["factor_evidence"]["minimum_excursion_rad"]) and maximum/max(sigma,1e-12)>=float(contract["factor_evidence"]["minimum_excursion_sigma"])
 return {"status":signal["status"],"baseline":signal["baseline"],"reference":{"quality":quality,"huber_centre_matrix":signal["reference"]["centre_matrix"],"tangent_covariance_rad2":signal["reference"]["tangent_covariance_rad2"],"effective_sample_count":signal["reference"]["effective_sample_count"],"legacy_retained_fraction":legacy.get("retained_fraction"),"legacy_cutoff_rad":legacy.get("inlier_cutoff_rad")},"activity":{"rad_s":quantiles(signal["activity"]["rate_rad_s"][rows]),"z":quantiles(signal["activity_z"][rows]),"valid_fraction":float(np.mean(signal["valid"][rows]))},"active_bouts":bouts,"active_rows":active_rows,"ACTIVE_MOTION_EVIDENCE":"PASS" if bouts else "FAIL","cycle":cycle,"CYCLE_TOPOLOGY_EVIDENCE":"PASS" if cycle["complete_cycles"] else "FAIL","REFERENCE_ZERO_RETURN_EVIDENCE":"PASS" if quality=="HIGH" else "LOW_OR_UNAVAILABLE","POST_NEUTRAL_STATUS":post,"post_quiet_rows":quiet,"FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY":"ELIGIBLE" if functional else "NOT_ELIGIBLE","SIGN_ELIGIBLE":bool(cycle["complete_cycles"]),"ZERO_ELIGIBLE":quality=="HIGH","RETURN_FACTOR_ELIGIBLE":post=="POST_NEUTRAL_AVAILABLE","maximum_excursion_rad":maximum,"maximum_excursion_sigma":maximum/max(sigma,1e-12)}

def associate_arms(left:Mapping[str,Any],right:Mapping[str,Any],timeline:Any,contract:Mapping[str,Any])->dict[str,Any]:
 lc=left["cycle"]["complete_cycles"];rc=right["cycle"]["complete_cycles"];pairs=[];ul=set();ur=set();limit=float(contract["phase_association"]["maximum_bilateral_peak_offset_s"]);minover=float(contract["phase_association"]["minimum_bilateral_cycle_overlap_fraction"])
 candidates=[]
 for i,a in enumerate(lc):
  for j,b in enumerate(rc):
   overlap=max(0,min(a["stop_row_exclusive"],b["stop_row_exclusive"])-max(a["start_row"],b["start_row"]));den=max(1,min(a["stop_row_exclusive"]-a["start_row"],b["stop_row_exclusive"]-b["start_row"]));offset=abs(int(timeline.time_ns[a["peak_row"]])-int(timeline.time_ns[b["peak_row"]]))/1e9
   if offset<=limit and overlap/den>=minover:candidates.append((offset,-overlap,i,j))
 for off,neg,i,j in sorted(candidates):
  if i not in ul and j not in ur:ul.add(i);ur.add(j);pairs.append({"left":i,"right":j,"peak_offset_s":off,"overlap_rows":-neg})
 events=[{"phase":"bilateral","start_row":min(lc[p["left"]]["start_row"],rc[p["right"]]["start_row"]),"stop_row_exclusive":max(lc[p["left"]]["stop_row_exclusive"],rc[p["right"]]["stop_row_exclusive"]),**p} for p in pairs];events += [{"phase":"left_dominant","start_row":x["start_row"],"stop_row_exclusive":x["stop_row_exclusive"],"left":i} for i,x in enumerate(lc) if i not in ul];events += [{"phase":"right_dominant","start_row":x["start_row"],"stop_row_exclusive":x["stop_row_exclusive"],"right":i} for i,x in enumerate(rc) if i not in ur];events.sort(key=lambda x:x["start_row"]);classes=[]
 for e in events:
  if not classes or classes[-1]!=e["phase"]:classes.append(e["phase"])
 return {"pairs":pairs,"events":events,"chronological_classes":classes,"left_present":"left_dominant" in classes,"right_present":"right_dominant" in classes,"bilateral_present":"bilateral" in classes,"weak_expected_order":["left_dominant","right_dominant","bilateral"],"order_qc_match":classes==["left_dominant","right_dominant","bilateral"]}

def plot_r3b(path:Path,action:str,items:list[tuple[str,Mapping[str,Any],np.ndarray]],timeline:Any)->None:
 fig,(ax,dx)=plt.subplots(2,1,figsize=(14,8),sharex=True,constrained_layout=True);origin=min(int(timeline.time_ns[rows[0]]) for _,_,rows in items);colors=["#1f77b4","#d62728","#2ca02c","#9467bd"]
 for idx,(name,s,rows) in enumerate(items):
  c=colors[idx%len(colors)];t=(timeline.time_ns[rows]-origin)/1e9;q=s["smoothed_excursion_rad"][rows];u=s["excursion_uncertainty_rad"][rows];ax.plot(t,q,color=c,label=name);ax.fill_between(t,np.maximum(0,q-u),q+u,color=c,alpha=.13);dx.plot(t,s["activity_z"][rows],color=c,label=name)
  for cyc in s["_cycle"]["complete_cycles"]:ax.axvspan((timeline.time_ns[cyc["start_row"]]-origin)/1e9,(timeline.time_ns[cyc["stop_row_exclusive"]-1]-origin)/1e9,color=c,alpha=.07);ax.plot((timeline.time_ns[cyc["peak_row"]]-origin)/1e9,s["smoothed_excursion_rad"][cyc["peak_row"]],"o",color=c)
  for bout in s["_bouts"]:dx.axvspan((timeline.time_ns[bout["start_row"]]-origin)/1e9,(timeline.time_ns[bout["stop_row_exclusive"]-1]-origin)/1e9,color=c,alpha=.08)
 ax.set(title=f"R3B signal-derived topology — {action}",ylabel="geodesic excursion (rad)");dx.axhline(4,color="black",ls="--",lw=.7);dx.axhline(2,color="gray",ls=":",lw=.7);dx.set(xlabel="seconds",ylabel="activity change z");ax.legend(fontsize=7);dx.legend(fontsize=7)
 for a in (ax,dx):a.grid(alpha=.2)
 fig.savefig(path,dpi=140);plt.close(fig)

def analyze_static(timeline:Any,rows:np.ndarray,contract:Mapping[str,Any])->dict[str,Any]:
 valid=timeline.all_nodes_valid[rows];gyro=np.linalg.norm(timeline.gyro_rad_s[rows],axis=2);score=np.nanmedian(gyro,axis=1);length=round(.4*float(contract["common_time"]["rate_hz"]));candidates=[]
 for a in range(0,max(1,len(rows)-length+1),5):
  block=np.arange(a,min(a+length,len(rows)));fraction=float(np.mean(valid[block]));
  if len(block)==length and fraction>=.8:candidates.append((float(np.nanmedian(score[block])),a,a+length,fraction))
 if not candidates:return {"ACTIVE_MOTION_EVIDENCE":"NOT_APPLICABLE_STATIC","REFERENCE_ZERO_RETURN_EVIDENCE":"FAIL","plateau":None}
 _,a,b,f=min(candidates);selected=rows[a:b];return {"ACTIVE_MOTION_EVIDENCE":"NOT_APPLICABLE_STATIC","CYCLE_TOPOLOGY_EVIDENCE":"NOT_APPLICABLE_STATIC","REFERENCE_ZERO_RETURN_EVIDENCE":"PASS","plateau":{"start_row":int(selected[0]),"stop_row_exclusive":int(selected[-1]+1),"global_time_ns":timeline.time_ns[selected].tolist(),"duration_s":len(selected)/float(contract["common_time"]["rate_hz"]),"valid_fraction":f,"gyro_median_rad_s":float(np.nanmedian(score[a:b])),"gyro_p95_rad_s":float(np.nanpercentile(score[a:b],95))}}

def run_r3b(args:argparse.Namespace)->dict[str,Any]:
 if args.output.exists():raise FileExistsError(args.output)
 freeze_record,contract,reference_semantics,chain_map,derivation=verify_freeze(args.freeze);qual=json.loads((args.qualification/"R3B_SYNTHETIC_QUALIFICATION.json").read_text());binding=json.loads((args.qualification/"R3B_QUALIFICATION_BINDING.json").read_text())
 if not qual["pass"] or binding["source_sha256"]!=sha256(MODULE):raise RuntimeError("synthetic/source binding failed")
 phase,cache,r2,gates,q2,timeline,segments,segment_node,domains=context(args);specs=chains(chain_map);analyses={};raw_signals={};plot_items={a:[] for a in ACTIONS}
 for spec in specs:
  rows=domain_rows(timeline,domains[spec["action"]]);s=build_chain_signal(timeline,segments[spec["parent"]],segments[spec["child"]],rows,contract)
  if s["status"]!="AVAILABLE":analyses[spec["key"]]={**spec,"status":s["status"],"ACTIVE_MOTION_EVIDENCE":"FAIL","CYCLE_TOPOLOGY_EVIDENCE":"FAIL","REFERENCE_ZERO_RETURN_EVIDENCE":"FAIL"};continue
  bouts=detect_active_bouts(s,rows,contract);cyc=detect_cycles(s,rows,contract);legacy=legacy_reference_diagnostic(s["relative"],s["baseline"]["row_indices"],float(contract["coordinate"]["relative_orientation_uncertainty_floor_rad"]));summary=chain_summary(s,bouts,cyc,legacy,rows,timeline,contract);analyses[spec["key"]]={**spec,**summary};s["_cycle"]=cyc;s["_bouts"]=bouts;raw_signals[spec["key"]]=s;plot_items[spec["action"]].append((spec["name"],s,rows))
 static={a:analyze_static(timeline,domain_rows(timeline,domains[a]),contract) for a in ("initial_still_attempt2","t_pose")}
 arms=associate_arms(analyses["arms:left"],analyses["arms:right"],timeline,contract) if "arms:left" in analyses and "arms:right" in analyses else {"left_present":False,"right_present":False,"bilateral_present":False,"events":[]}
 elbow_groups={side:phase_groups_from_cycle_vectors(analyses[f"{action}:{name}"]["cycle"]["complete_cycles"],float(contract["phase_association"]["minimum_axis_cluster_separation_deg"])) for side,action,name in (("left","left_elbow","elbow_L"),("right","right_elbow_attempt2","elbow_R"))}
 trunk_groups=phase_groups_from_cycle_vectors(analyses["trunk:trunk"]["cycle"]["complete_cycles"],float(contract["phase_association"]["minimum_axis_cluster_separation_deg"])) if "trunk:trunk" in analyses else {"groups":[]}
 phases={"arms":arms,"elbows":elbow_groups,"trunk":trunk_groups}
 factor_rows=[]
 for key,d in analyses.items():
  factor_rows.append({"ACTION":d["action"],"PHASE":"SIGNAL_DERIVED_PRIMARY" if d["role"]=="PRIMARY" else "DIAGNOSTIC_NUISANCE","PRIMARY_CHAIN":f"{d['parent']}->{d['child']}","ACTIVE_ROWS":len(d.get("active_rows",[])),"CYCLE_COUNT":len(d.get("cycle",{}).get("complete_cycles",[])),"REFERENCE_QUALITY":d.get("reference",{}).get("quality","UNAVAILABLE"),"FUNCTIONAL_AXIS_ELIGIBLE":d.get("FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY")=="ELIGIBLE","SIGN_ELIGIBLE":bool(d.get("SIGN_ELIGIBLE",False)),"ZERO_ELIGIBLE":bool(d.get("ZERO_ELIGIBLE",False)),"RETURN_FACTOR_ELIGIBLE":bool(d.get("RETURN_FACTOR_ELIGIBLE",False)),"FAIL_REASON":None if d.get("FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY")=="ELIGIBLE" else d.get("status","MOTION_OR_REFERENCE_EVIDENCE_MISSING")})
 required_primary=[d for d in analyses.values() if d["role"]=="PRIMARY"];valid_support=all(d.get("status")=="AVAILABLE" for d in required_primary);motion_ok=all(d.get("ACTIVE_MOTION_EVIDENCE")=="PASS" for d in required_primary) and all(x["REFERENCE_ZERO_RETURN_EVIDENCE"]=="PASS" for x in static.values());association_ok=arms.get("left_present") and arms.get("right_present") and arms.get("bilateral_present") and all(len(x.get("groups",[]))>=2 for x in elbow_groups.values()) and len(trunk_groups.get("groups",[]))>=3;cycle_ok=all(d.get("CYCLE_TOPOLOGY_EVIDENCE")=="PASS" for d in required_primary)
 if timeline.accounting["all_nodes_valid_fraction"]<.95:terminal="FAIL_Q2_OR_TIMESTAMP_INPUT_QUALITY"
 elif not valid_support:terminal="FAIL_VALID_TIME_SUPPORT"
 elif not motion_ok:terminal="FAIL_REQUIRED_MOTION_EVIDENCE_MISSING"
 elif not association_ok:terminal="FAIL_ACTION_CHAIN_ASSOCIATION_AMBIGUOUS"
 elif not cycle_ok:terminal="FAIL_REQUIRED_CYCLE_OR_REVERSAL_EVIDENCE_MISSING"
 else:terminal="PASS_R3B_SIGNAL_DERIVED_ACTION_TOPOLOGY"
 args.output.mkdir(parents=True);plot_dir=args.output/"r3b_timeline_plots";plot_dir.mkdir()
 for action,items in plot_items.items():
  if items:plot_r3b(plot_dir/f"{action}.png",action,items,timeline)
 for action in ("initial_still_attempt2","t_pose"):
  fig,ax=plt.subplots(figsize=(12,4),constrained_layout=True);rows=domain_rows(timeline,domains[action]);t=(timeline.time_ns[rows]-timeline.time_ns[rows[0]])/1e9;ax.plot(t,np.nanmedian(np.linalg.norm(timeline.gyro_rad_s[rows],axis=2),axis=1));p=static[action]["plateau"];
  if p:ax.axvspan((timeline.time_ns[p["start_row"]]-timeline.time_ns[rows[0]])/1e9,(timeline.time_ns[p["stop_row_exclusive"]-1]-timeline.time_ns[rows[0]])/1e9,color="green",alpha=.15)
  ax.set(title=f"R3B independent static latent — {action}",xlabel="seconds",ylabel="median node gyro rad/s");fig.savefig(plot_dir/f"{action}.png",dpi=140);plt.close(fig)
 timeline_out={"schema":"biospur-r3b-action-phase-timeline-v1","static":static,"chains":analyses,"phase_association":phases,"terminal_outcome":terminal};dump(args.output/"R3B_ACTION_PHASE_TIMELINE.json",timeline_out);dump(args.output/"R3B_ACTION_BOUT_CYCLE_REFERENCE_MATRIX.json",{"schema":"biospur-r3b-action-bout-cycle-reference-matrix-v1","static":static,"chains":{k:{x:v for x,v in d.items() if x in ("action","name","role","parent","child","ACTIVE_MOTION_EVIDENCE","CYCLE_TOPOLOGY_EVIDENCE","REFERENCE_ZERO_RETURN_EVIDENCE","POST_NEUTRAL_STATUS","FUNCTIONAL_AXIS_FACTOR_ELIGIBILITY","SIGN_ELIGIBLE","ZERO_ELIGIBLE","RETURN_FACTOR_ELIGIBLE")} for k,d in analyses.items()},"phase_association":phases});dump(args.output/"R3B_FACTOR_ELIGIBILITY_MATRIX.json",{"schema":"biospur-r3b-factor-eligibility-matrix-v1","rows":factor_rows,"D0_READY_FOR_SEPARATE_AUTHORIZATION":terminal.startswith("PASS_")});dump(args.output/"R3B_ALL_CHAIN_DIAGNOSTICS.json",{"schema":"biospur-r3b-all-chain-diagnostics-v1","common_time_accounting":timeline.accounting,"chains":analyses})
 old_new={"schema":"biospur-r3b-old-vs-new-membership-v1","R2_timeline_sha256":sha256(args.r2/"ACTION_PHASE_TIMELINE.json"),"actions":{a:{"r2_phase_count":len(r2["actions"][a]["phases"]),"r2_complete_cycles":sum(int(p.get("complete_cycle_count") or 0) for p in r2["actions"][a]["phases"]),"r3b_chain_keys":[k for k,d in analyses.items() if d["action"]==a],"r3b_active_rows":sum(len(d.get("active_rows",[])) for d in analyses.values() if d["action"]==a),"r3b_complete_cycles":sum(len(d.get("cycle",{}).get("complete_cycles",[])) for d in analyses.values() if d["action"]==a)} for a in ACTIONS}};dump(args.output/"R3B_OLD_VS_NEW_MEMBERSHIP.json",old_new)
 shutil.copyfile(args.qualification/"R3B_SYNTHETIC_QUALIFICATION.json",args.output/"R3B_SYNTHETIC_QUALIFICATION.json");access={"schema":"biospur-r3b-data-access-audit-v1","opened":["CALIBRATION_ONLY_Q2_CACHE","ELEVEN_CALIBRATION_BROAD_WINDOWS","R2_R3_DERIVED_EVIDENCE","FROZEN_NODE_SEGMENT_MAPPING"],"forbidden_opened":[],"q2_cache_sha256":sha256(cache),"FINAL_STILL":"SEALED","final_still_samples_accessed":False,"WALK":"SEALED","GOLF":"SEALED","BOXING":"SEALED","UWB":"SEALED","operator_measurements":"SEALED","D0":"NOT_STARTED","JACOBIAN":"NOT_STARTED","SOLVER":"NOT_STARTED","FREEZE":"NOT_CREATED","REPLAY":"NOT_STARTED","RENDER":"NOT_STARTED","COMMIT_PUSH":"NOT_PERFORMED"};dump(args.output/"R3B_DATA_ACCESS_AUDIT.json",access)
 result={"schema":"biospur-r3b-result-v1","terminal_outcome":terminal,"pass":terminal.startswith("PASS_"),"D0_READY_FOR_SEPARATE_AUTHORIZATION":terminal.startswith("PASS_"),**{k:v for k,v in access.items() if k in ("FINAL_STILL","WALK","GOLF","BOXING","UWB","D0","JACOBIAN","SOLVER","FREEZE","REPLAY","RENDER","COMMIT_PUSH")}};dump(args.output/"RESULT.json",result);(args.output/"REPORT.md").write_text(f"# Revision D D−1 R3B\n\n`{terminal}`\n\nAll declared chains were evaluated without first-failure abort. Active motion, cycle topology, and reference/zero-return evidence are reported independently. Low legacy retention never directly invalidates active motion.\n\n`D0_READY_FOR_SEPARATE_AUTHORIZATION = {str(result['D0_READY_FOR_SEPARATE_AUTHORIZATION']).lower()}`\n\n`FINAL_STILL = SEALED`\n\nNo Jacobian, objective optimization, solver, freeze, replay, render, commit, or push was performed.\n");manifest(args.output);return result

def main()->int:
 p=argparse.ArgumentParser();sub=p.add_subparsers(dest="command",required=True);f=sub.add_parser("freeze");f.add_argument("--config",type=Path,required=True);f.add_argument("--r2",type=Path,required=True);f.add_argument("--r2-audit",type=Path,required=True);f.add_argument("--original-r3",type=Path,required=True);f.add_argument("--output",type=Path,required=True)
 for name in ("r3a","r3b"):
  q=sub.add_parser(name);q.add_argument("--freeze",type=Path,required=True);q.add_argument("--phase-a",type=Path,required=True);q.add_argument("--legacy-gates",type=Path,required=True);q.add_argument("--r2",type=Path,required=True);q.add_argument("--output",type=Path,required=True)
  if name=="r3a":q.add_argument("--original-r3-contract",type=Path,required=True)
  else:q.add_argument("--qualification",type=Path,required=True)
 q=sub.add_parser("qualify");q.add_argument("--freeze",type=Path,required=True);q.add_argument("--r3a",type=Path,required=True);q.add_argument("--output",type=Path,required=True)
 a=p.parse_args();result=freeze(a) if a.command=="freeze" else run_r3a(a) if a.command=="r3a" else qualify(a) if a.command=="qualify" else run_r3b(a);print(json.dumps(sanitize(result),sort_keys=True));return 0 if result.get("pass",True) else 2
if __name__=="__main__":raise SystemExit(main())
