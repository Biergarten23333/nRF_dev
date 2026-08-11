#!/usr/bin/env python3
"""Offline closure analysis for the v47 ten-node Fusion dataset."""
from __future__ import annotations

import argparse,csv,hashlib,json,math,multiprocessing as mp,subprocess
from collections import Counter,defaultdict
from dataclasses import replace
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from analyze_v47_state_adaptive_fusion import (CALIBRATION,HELD_OUT,MOVES,NODES,
    StateAdaptiveFusion,adaptive_params,annotations,clean,read_positions,robust_scatter,
    rolling_features,sha256,write_csv,write_json)
from v47_real_data_adapter import load_capture,imu_physical,sequence_gap_count
from v47_static_fusion import fit_node_clock,local_to_t0_s
from v47_s2_fusion import S2Fusion,S2Parameters,corrected_range_m,range_jacobian

RAW_SHA="c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8"
MODES=("B0","I0","I1","H2","H3","H5","S1","S2P","S2R")
CTX={}

def head(): return subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()

def load_layout(path):
    obj=json.loads(path.read_text()); a=sorted(obj["anchors"],key=lambda x:x["id"])
    return obj,np.array([[x["x_mm"],x["y_mm"],x["z_mm"]] for x in a])/1000,np.array([x["d_anchor_mm"] for x in a])

def causal_extra(acc,gyro,times,idx,gravity_ref):
    gn=np.linalg.norm(gyro,axis=1); out_angle=np.zeros(len(idx)); out_gravity=np.zeros(len(idx))
    for j,ii in enumerate(idx):
        lo=np.searchsorted(times,times[ii]-1.,"left"); seg=slice(lo,ii+1)
        dt=np.diff(times[seg],prepend=times[lo]); out_angle[j]=np.sum(gn[seg]*dt)
        c0=np.searchsorted(times,times[ii]-.5,"left"); p0=np.searchsorted(times,times[ii]-1.,"left")
        current=np.mean(acc[c0:ii+1],axis=0)
        previous=np.mean(acc[p0:c0],axis=0) if c0>p0 else gravity_ref.copy()
        current/=max(np.linalg.norm(current),1e-12); previous/=max(np.linalg.norm(previous),1e-12)
        out_gravity[j]=math.degrees(math.acos(float(np.clip(current@previous,-1,1))))
    return out_angle,out_gravity

def build_inputs(imu,uwb,pos,base):
    clock=fit_node_clock(uwb); it=local_to_t0_s(imu["b306_us"],clock); ut=local_to_t0_s(uwb["strobe_us"],clock)
    acc,gyr,_=imu_physical(imu); bias=np.array(base["gyro_bias_dps"]); gravity=float(base["local_gravity_g"])
    idx,feat=rolling_features(acc,gyr-bias,gravity); ref=np.mean(acc[(it>=1)&(it<60)],axis=0); ref/=np.linalg.norm(ref)
    angle,gchange=causal_extra(acc,gyr-bias,it,idx,ref)
    feat={**feat,"gyro_angle_1s_deg":angle,"gravity_change_deg":gchange}
    return {"clock":clock,"imu_t":it,"uwb_t":ut,"acc":acc,"gyro":gyr,"idx":idx,"control_t":it[idx],"features":feat}

def derive(data,positions_path,prior,layout_path,geometry_path,s1_manifest_path):
    imu,uwb,audit=load_capture(data); pos=read_positions(positions_path); _,table=annotations(prior)
    layout,anchors,delays=load_layout(layout_path); s1=json.loads(s1_manifest_path.read_text())
    per={}
    for node in NODES:
        inp=build_inputs(imu[node],uwb[node],pos[node],s1["per_node"][node]); ct=inp["control_t"]
        q=(ct>=1)&(ct<240)
        for e in table: q &= ~((ct>=float(e["onset_s"]))&(ct<float(e["end_s"])))
        angle=max(.10,float(np.quantile(inp["features"]["gyro_angle_1s_deg"][q],.995)))
        gravity=max(.20,float(np.quantile(inp["features"]["gravity_change_deg"][q],.995)))
        ut=inp["uwb_t"]; cal=(ut>=1)&(ut<240); p=pos[node]["p"]
        range_sig=[]; range_med=[]
        for k in range(8):
            valid=cal&((uwb[node]["valid_mask"]&(1<<k))!=0)
            corrected=uwb[node]["range_mm"][valid,k]/1000-(delays[k]+layout.get("tag_delay_mm",0))/1000
            residual=corrected-np.linalg.norm(p[valid]-anchors[k],axis=1)
            med=float(np.median(residual)); sig=max(.03,float(1.4826*np.median(np.abs(residual-med))))
            range_sig.append(sig); range_med.append(med)
        base=s1["per_node"][node]
        per[node]={"position_r_m2":base["uwb_r_m2"],"range_sigma_m":range_sig,"range_residual_median_m":range_med,
            "gyro_bias_dps":base["gyro_bias_dps"],"local_gravity_g":base["local_gravity_g"],
            "gyro_rms_threshold_dps":base["gyro_rms_threshold_dps"],"accel_dev_rms_threshold_g":base["accel_dev_rms_threshold_g"],
            "gyro_std_threshold_dps":base["gyro_std_threshold_dps"],"accel_std_threshold_g":base["accel_std_threshold_g"],
            "gyro_angle_1s_threshold_deg":angle,"gravity_change_threshold_deg":gravity}
    global_pos=np.median(np.array([np.diag(per[n]["position_r_m2"]) for n in NODES]),axis=0)
    global_range=np.median(np.array([per[n]["range_sigma_m"] for n in NODES]),axis=0)
    return {"schema":"biospur-v47-s2-parameter-manifest-v1","frozen":True,"disposition":"DEVELOPMENT_DATASET_ONLY",
      "source_head":head(),"source_head_role":"pre-analysis repository baseline",
      "analysis_driver_sha256":sha256(Path(__file__).resolve()),
      "s2_implementation_sha256":sha256(Path(__file__).with_name("v47_s2_fusion.py").resolve()),
      "raw_sha256":audit.raw_sha256,"position_stream_sha256":sha256(positions_path),
      "geometry_manifest_sha256":sha256(geometry_path),"layout_sha256":sha256(layout_path),
      "coordinate_contract":"RELATIVE_GEOMETRY_ONLY","full_vector":"BLOCKED_FRAME_BINDING",
      "windows_half_open":{"development_static":[1,240],"held_out_static":[240,484],"post_move":[506,535]},
      "anchors_m":anchors.tolist(),"anchor_delay_mm":delays.tolist(),"tag_delay_mm":layout.get("tag_delay_mm",0),
      "delay_owner":"S2R_OBSERVATION_MODEL_EXACTLY_ONCE","transport_applies_v4_delay":False,
      "global_position_variance_m2":global_pos.tolist(),"global_range_sigma_m":global_range.tolist(),
      "state_machine":{"states":["INIT","STATIONARY","MOTION_SUSPECTED","MOVING","SETTLING","PLATFORM_CONFLICT"],
        "position_shift_normalized":1.35,"range_shift_normalized":2.5,"candidate_scatter_normalized":2.5,
        "suspected_confirm_dwell_s":.2,"suspected_clear_dwell_s":.5,"conflict_enter_dwell_s":.75,
        "conflict_resolve_dwell_s":2.0,"moving_quiet_dwell_s":.75,"settling_dwell_s":1.5,
        "candidate_window_s":1.5,"min_candidate_positions":6,"min_anchor_support":4,
        "nis_position_gate":16.266236,"nis_range_gate":10.827566,"zupt_sigma_mps":.02,
        "process_sigma_mps2":{"stationary":.03,"suspected":.25,"moving":1.,"settling":.2}},
      "parameter_provenance":{"motion_mechanism":"developed after inspected C2CC/AA61 failures; not held-out",
        "numeric_thresholds":"calibration [1,240), frozen annotations excluded; S1 node covariance retained",
        "movement_windows_used_for_numeric_optimization":False},"per_node":per}

def params(man,node,variant="main",mode="S2P"):
    n=man["per_node"][node]; s=man["state_machine"]
    pos=np.array(n["position_r_m2"]); rs=np.array(n["range_sigma_m"])
    kwargs={}
    if variant=="global_covariance": pos=np.diag(man["global_position_variance_m2"]); rs=np.array(man["global_range_sigma_m"])
    if variant=="no_conflict": kwargs["platform_conflict_enabled"]=False
    if variant=="instantaneous": kwargs["instantaneous_detector"]=True
    if variant=="fleet_context": kwargs["fleet_context_enabled"]=True
    if variant=="no_zupt": kwargs["zupt_enabled"]=False
    if variant=="fixed_settling": kwargs["fixed_candidate_scatter_m"]=.12
    return S2Parameters(pos,rs,np.array(man["anchors_m"]),np.array(man["anchor_delay_mm"]),man["tag_delay_mm"],
      n["gyro_rms_threshold_dps"],n["accel_dev_rms_threshold_g"],n["gyro_std_threshold_dps"],n["accel_std_threshold_g"],
      n["gyro_angle_1s_threshold_deg"],n["gravity_change_threshold_deg"],
      position_shift_normalized=s["position_shift_normalized"],range_shift_normalized=s["range_shift_normalized"],
      candidate_scatter_normalized=s["candidate_scatter_normalized"],suspected_confirm_dwell_s=s["suspected_confirm_dwell_s"],
      suspected_clear_dwell_s=s["suspected_clear_dwell_s"],conflict_enter_dwell_s=s["conflict_enter_dwell_s"],
      conflict_resolve_dwell_s=s["conflict_resolve_dwell_s"],moving_quiet_dwell_s=s["moving_quiet_dwell_s"],
      settling_dwell_s=s["settling_dwell_s"],candidate_window_s=s["candidate_window_s"],
      min_candidate_positions=s["min_candidate_positions"],min_anchor_support=s["min_anchor_support"],
      nis_position_gate=s["nis_position_gate"],nis_range_gate=s["nis_range_gate"],zupt_sigma_mps=s["zupt_sigma_mps"],**kwargs)

def replay_one(node,mode,variant="main",scale=1.,end_s=None):
    c=CTX; inp=c["inputs"][node]; uwb=c["uwb"][node]; pos=c["pos"][node]
    f=S2Fusion(params(c["manifest"],node,variant,mode),mode,scale); ci=ui=0
    while ci<len(inp["idx"]) or ui<len(uwb):
        next_c=inp["control_t"][ci] if ci<len(inp["idx"]) else math.inf
        next_u=inp["uwb_t"][ui] if ui<len(uwb) else math.inf
        if end_s is not None and min(next_c,next_u)>=end_s: break
        if ui<len(uwb) and (ci>=len(inp["idx"]) or inp["uwb_t"][ui]<inp["control_t"][ci]):
            f.process_uwb(inp["uwb_t"][ui],pos["p"][ui],uwb["range_mm"][ui],int(uwb["valid_mask"][ui]),ui); ui+=1
        else:
            feat={k:float(v[ci]) for k,v in inp["features"].items()}; bin_=int(math.floor(inp["control_t"][ci]*20+.5))
            f.process_control(inp["control_t"][ci],feat,True,c["common_bins"].get(bin_,0)>=5); ci+=1
    return f

def static_metrics(node,mode,f,pos,ut):
    q=np.array([240<=x["time_s"]<484 for x in f.snapshots]); p=np.array([x["published_m"] for x in f.snapshots]); v=np.array([x["velocity_mps"] for x in f.snapshots]); cand=np.array([x["candidate_m"] if x["candidate_m"] is not None else [np.nan]*3 for x in f.snapshots]); states=np.array([x["state"] for x in f.snapshots])
    _,_,rms,p95=robust_scatter(p[q]); _,_,crms,cp95=robust_scatter(cand[q]); speed=np.linalg.norm(v[q],axis=1)
    aq=[x for x in f.audit if 240<=x["time_s"]<484]; cats=Counter(x["category"] for x in aq)
    return {"node":node,"mode":mode,"status":"DEVELOPMENT_DATASET_ONLY","position_rms_m":rms,"position_p95_m":p95,
      "velocity_rms_mps":float(np.sqrt(np.mean(speed**2))),"velocity_p95_mps":float(np.quantile(speed,.95)),
      "stationary_occupancy":float(np.mean(states[q]=="STATIONARY")),"published_locked_max_step_m":f.published_motion_while_locked_max_m,
      "candidate_rms_m":crms,"candidate_p95_m":cp95,"zupt_updates":f.zupt_updates,
      "uwb_accepted":cats["accepted"],"uwb_rejected":cats["rejected"],"uwb_invalid":cats["invalid"],
      "cov_min_eigenvalue":f.covariance_min_eigenvalue,"cov_max_asymmetry":f.covariance_max_asymmetry,"reinitializations":f.reinitializations}

def movement(node,mode,f,pos,ut):
    start,end=MOVES[node]; tr=f.transitions
    def first(to,a,b): return next((x for x in tr if a<=x["time_s"]<b and x["to_state"]==to),None)
    suspect=first("MOTION_SUSPECTED",start-2,end+10); unlock=first("MOVING",start-2,end+10); settle=first("SETTLING",start,end+60); relock=first("STATIONARY",end,end+60)
    pre=np.median(pos[(ut>=450)&(ut<484)],axis=0); post=np.median(pos[(ut>=506)&(ut<535)],axis=0); delta=post-pre
    snaps=f.snapshots; st=np.array([x["time_s"] for x in snaps]); pub=np.array([x["published_m"] for x in snaps]); states=np.array([x["state"] for x in snaps])
    old=f.transitions[0] if f.transitions else None
    prepub=np.median(pub[(st>=450)&(st<484)],axis=0)
    if relock:
        locked_q=(st>=relock["time_s"])&(st<relock["time_s"]+1.)
        postpub=np.median(pub[locked_q],axis=0)
    else:
        postpub=np.full(3,np.nan)
    conflicts=[x for x in tr if start-2<=x["time_s"]<end+30 and x["to_state"]=="PLATFORM_CONFLICT"]
    old_to_new=float(np.linalg.norm(postpub-prepub)) if np.isfinite(postpub).all() else math.nan
    candidate=np.array([x["candidate_m"] if x["candidate_m"] is not None else [np.nan]*3 for x in snaps])
    candidate_delta=np.linalg.norm(candidate-prepub,axis=1)
    candidate_change_idx=np.flatnonzero((st>=start-2)&(st<end+30)&(candidate_delta>.25*max(old_to_new,.10)))
    candidate_change_s="" if not len(candidate_change_idx) else float(st[candidate_change_idx[0]])
    t4_distance=np.linalg.norm(pos-post,axis=1)
    reached_idx=np.flatnonzero((ut>=start)&(ut<end+60)&(t4_distance<=max(.10,.15*float(np.linalg.norm(delta)))))
    reached_s="" if not len(reached_idx) else float(ut[reached_idx[0]])
    movement_q=(st>=start)&(st<end+60)
    overshoot=(max(0.,float(np.nanmax(np.linalg.norm(pub[movement_q]-prepub,axis=1)))-old_to_new)
               if np.isfinite(old_to_new) else "")
    def drift_after(seconds):
        if not relock:return ""
        q=(st>=relock["time_s"]+seconds-1)&(st<relock["time_s"]+seconds+1)
        return "" if not np.any(q) else float(np.linalg.norm(np.median(pub[q],axis=0)-postpub))
    ambiguity_duration=0.
    for i,x in enumerate(tr):
        if x["to_state"]!="PLATFORM_CONFLICT" or not (start-2<=x["time_s"]<end+30):continue
        leave=next((y["time_s"] for y in tr[i+1:] if y["from_state"]=="PLATFORM_CONFLICT"),end+30)
        ambiguity_duration+=max(0.,min(leave,end+30)-x["time_s"])
    creep=float(np.max(np.linalg.norm(pub[(st>=start)&(st<end)]-prepub,axis=1))) if np.any((st>=start)&(st<end)) else 0
    return {"node":node,"mode":mode,"disposition":"DEVELOPMENT_REPLAY_NOT_GENERALIZATION_EVIDENCE",
      "motion_start_s":start,"motion_end_s":end,"suspected_s":"" if not suspect else suspect["time_s"],
      "unlock_s":"" if not unlock else unlock["time_s"],"unlock_latency_s":"" if not unlock else unlock["time_s"]-start,
      "unlock_evidence":"" if not unlock else unlock["evidence"],"settling_s":"" if not settle else settle["time_s"],
      "relock_s":"" if not relock else relock["time_s"],"relock_latency_s":"" if not relock else relock["time_s"]-end,
      "candidate_platform_change_s":candidate_change_s,"t4_reached_new_platform_s":reached_s,
      "old_lock_x_m":prepub[0],"old_lock_y_m":prepub[1],"old_lock_z_m":prepub[2],"new_lock_x_m":postpub[0],"new_lock_y_m":postpub[1],"new_lock_z_m":postpub[2],
      "t4_dx_m":delta[0],"t4_dy_m":delta[1],"t4_dz_m":delta[2],"t4_displacement_m":float(np.linalg.norm(delta)),
      "output_displacement_m":old_to_new,"overshoot_m":overshoot,"post_settle_drift_5s_m":drift_after(5),
      "post_settle_drift_30s_m":drift_after(30),"silent_creep_m":creep if not unlock else 0.,
      "ambiguity_entries":len(conflicts),"ambiguity_duration_s":ambiguity_duration,
      "false_return_old":"" if not np.isfinite(postpub).all() else int(np.linalg.norm(postpub-pre)<np.linalg.norm(postpub-post))}

def table_rows(node,mode,f,events):
    out=[]; st=np.array([x["time_s"] for x in f.snapshots]); p=np.array([x["published_m"] for x in f.snapshots]); v=np.array([x["velocity_mps"] for x in f.snapshots])
    for e in events:
        a,b=float(e["onset_s"]),float(e["end_s"]); before=(st>=a-2)&(st<a); during=(st>=a)&(st<b); base=np.median(p[before],axis=0)
        trans=[x for x in f.transitions if a<=x["time_s"]<b+1]; suspected=sum(x["to_state"]=="MOTION_SUSPECTED" for x in trans); moving=sum(x["to_state"]=="MOVING" for x in trans)
        future=[x for x in f.transitions if b<=x["time_s"]<b+30 and x["to_state"]=="STATIONARY"]
        settling_s=0. if not trans or f.snapshots[np.searchsorted(st,b,side="left")-1]["state"]=="STATIONARY" else (future[0]["time_s"]-b if future else "")
        out.append({"event_id":e["event_id"],"node":node,"mode":mode,"false_suspected":suspected,"temporary_unlock":moving,
          "persistent_false_platform":int(any(x["to_state"]=="STATIONARY" and x["reason"].endswith("NEW_STATIONARY_PLATFORM") for x in trans)),
          "max_excursion_m":float(np.max(np.linalg.norm(p[during]-base,axis=1))) if np.any(during) else "",
          "max_velocity_mps":float(np.max(np.linalg.norm(v[during],axis=1))) if np.any(during) else "",
          "fleet_context_classification":"COMMON_MODE" if len(e["nodes"].split(","))>=5 else "NOT_COMMON",
          "settling_time_s":settling_s,"filter_resets":f.reinitializations})
    return out

def summary_ablation(node,name,f,pos,ut,events):
    m=static_metrics(node,name,f,pos,ut); mv=[movement(node,name,f,pos,ut)] if node in MOVES else []
    tabs=table_rows(node,name,f,[e for e in events if float(e["end_s"])<=550])
    return {"node":node,"configuration":name,"static_rms_m":m["position_rms_m"],"static_p95_m":m["position_p95_m"],
      "false_suspected":sum(x["false_suspected"] for x in tabs),"temporary_unlock":sum(x["temporary_unlock"] for x in tabs),
      "persistent_false_transition":sum(x["persistent_false_platform"] for x in tabs),
      "movement_unlock_latency_s":mv[0]["unlock_latency_s"] if mv else "","movement_relock_latency_s":mv[0]["relock_latency_s"] if mv else "",
      "silent_creep_m":mv[0]["silent_creep_m"] if mv else 0,"published_locked_max_step_m":m["published_locked_max_step_m"]}

def worker(node):
    c=CTX; pos=c["pos"][node]["p"]; ut=c["inputs"][node]["uwb_t"]; events=c["table"]
    mains={mode:replay_one(node,mode) for mode in ("S2P","S2R")}
    abl=[]
    for name,mode,var,scale in [("no_stationary_lock_B0","S2P","main",1.),("lock_no_platform_conflict","S2P","no_conflict",1.),
      ("instantaneous_detector","S2P","instantaneous",1.),("global_position_covariance","S2P","global_covariance",1.),
      ("node_specific_position_covariance","S2P","main",1.),("raw_range_updates","S2R","main",1.),
      ("fleet_context_detector","S2P","fleet_context",1.),("no_zupt","S2P","no_zupt",1.),
      ("fixed_settling_scatter","S2P","fixed_settling",1.),("threshold_0.8","S2P","main",.8),("threshold_1.2","S2P","main",1.2)]:
        if name=="no_stationary_lock_B0":
            q=(ut>=240)&(ut<484); rms=robust_scatter(pos[q])[2]; abl.append({"node":node,"configuration":name,"static_rms_m":rms,"static_p95_m":robust_scatter(pos[q])[3],"false_suspected":"","temporary_unlock":"","persistent_false_transition":"","movement_unlock_latency_s":0 if node in MOVES else "","movement_relock_latency_s":0 if node in MOVES else "","silent_creep_m":"","published_locked_max_step_m":""}); continue
        f=mains[mode] if name in ("node_specific_position_covariance","raw_range_updates") and scale==1 else replay_one(node,mode,var,scale,end_s=550)
        abl.append(summary_ablation(node,name,f,pos,ut,events))
    # Explicit labels for the frozen main mechanisms avoid making the reader
    # infer that a control row implicitly represents the requested ablation.
    for name,mode in (("multi_timescale_detector","S2P"),("standalone_detector","S2P"),
                      ("zupt_enabled","S2P"),("t4_position_updates","S2P"),
                      ("covariance_normalized_settling","S2P"),("threshold_1.0","S2P")):
        abl.append(summary_ablation(node,name,mains[mode],pos,ut,events))
    return node,mains,abl

def link_characterization(node,uwb,pos,ut,man,s2r,events):
    out=[]; anchors=np.array(man["anchors_m"]); delays=np.array(man["anchor_delay_mm"]); sig=np.array(man["per_node"][node]["range_sigma_m"])
    accepted=Counter((x["anchor_id"],x["category"]) for x in s2r.audit if x["anchor_id"]!="")
    for k in range(8):
        valid=(uwb["valid_mask"]&(1<<k))!=0; raw=uwb["range_mm"][valid,k].astype(float); corr=raw/1000-(delays[k]+man["tag_delay_mm"])/1000
        pred=np.linalg.norm(pos[valid]-anchors[k],axis=1); res=corr-pred; med=np.median(res); rs=1.4826*np.median(np.abs(res-med)); centered=res-med
        ac=float(np.corrcoef(centered[:-1],centered[1:])[0,1]) if len(centered)>2 and np.std(centered)>0 else 0
        outlier=np.abs(centered)>3*sig[k]; best=cur=0
        for x in outlier: cur=cur+1 if x else 0; best=max(best,cur)
        h=np.array([range_jacobian(p,anchors[k]) for p in pos[valid]]); info=float(np.mean(np.sum(h*h,axis=1)/sig[k]**2))
        valid_t=ut[valid]
        cal=centered[(valid_t>=1)&(valid_t<240)]; held=centered[(valid_t>=240)&(valid_t<484)]
        blocks=[]
        for a in np.arange(240,1800,60):
            q=(valid_t>=a)&(valid_t<a+60)
            if np.any(q):blocks.append(float(np.median(centered[q])))
        regime_changes=sum(abs(b-a)>3*sig[k] for a,b in zip(blocks,blocks[1:]))
        move=np.zeros(len(valid_t),bool)
        for a,b in MOVES.values():move|=(valid_t>=a)&(valid_t<b)
        table=np.zeros(len(valid_t),bool)
        for e in events:table|=(valid_t>=float(e["onset_s"]))&(valid_t<float(e["end_s"]))
        move_delta="" if not np.any(move) else float(np.median(centered[move])-np.median(cal))
        table_ratio="" if not np.any(table) or not len(cal) else float((1.4826*np.median(np.abs(centered[table]-np.median(centered[table]))))/max(1.4826*np.median(np.abs(cal-np.median(cal))),1e-9))
        out.append({"node":node,"anchor_id":k,"anchor_label":chr(65+k),"anchor_delay_mm":delays[k],"valid_count":int(np.sum(valid)),
          "valid_rate":float(np.mean(valid)),"range_median_mm":float(np.median(raw)),"residual_median_m":float(med),"robust_sigma_m":float(rs),
          "autocorrelation_lag1":ac,"max_outlier_burst_records":best,"s2r_accepted":accepted[(k,"accepted")],"s2r_rejected":accepted[(k,"rejected")],
          "nis_acceptance_rate":accepted[(k,"accepted")]/max(1,accepted[(k,"accepted")]+accepted[(k,"rejected")]),
          "calibration_residual_median_m":"" if not len(cal) else float(np.median(cal)),
          "held_out_residual_median_m":"" if not len(held) else float(np.median(held)),
          "static_regime_change_count":regime_changes,"movement_residual_median_delta_m":move_delta,
          "table_vibration_robust_sigma_ratio":table_ratio,"position_information_trace_mean":info,
          "metal_facing_special_case":int(node=="BSF6C53"),"delay_at_positive_60mm_boundary":int(k==3)})
    return out

def savefig(path):
    plt.tight_layout();plt.savefig(path,metadata={"Date":None});plt.close(); text=path.read_text();path.write_text("\n".join(x.rstrip() for x in text.splitlines())+"\n")

def render_plots(out,results,pos,inputs,abl):
    matplotlib.rcParams["svg.hashsalt"]="v47-fusion-exhaustion-v1"
    x=np.arange(len(NODES)); b=[];p=[];r=[]
    for n in NODES:
        q=(inputs[n]["uwb_t"]>=240)&(inputs[n]["uwb_t"]<484);b.append(robust_scatter(pos[n]["p"][q])[2]*1000)
        for mode,target in [("S2P",p),("S2R",r)]:
            f=results[n][mode]; sq=np.array([240<=z["time_s"]<484 for z in f.snapshots]); pp=np.array([z["published_m"] for z in f.snapshots]);target.append(robust_scatter(pp[sq])[2]*1000)
    plt.figure(figsize=(12,5));plt.bar(x-.25,b,.25,label="B0");plt.bar(x,p,.25,label="S2P");plt.bar(x+.25,r,.25,label="S2R");plt.xticks(x,[n[3:] for n in NODES]);plt.ylabel("held-out RMS scatter (mm)");plt.legend();savefig(out/"static_mode_comparison.svg")
    fig,axes=plt.subplots(2,1,figsize=(12,8))
    for ax,n in zip(axes,("BSFC2CC","BSFAA61")):
        for mode in ("S2P","S2R"):
            f=results[n][mode];t=np.array([z["time_s"] for z in f.snapshots]);pp=np.array([z["published_m"] for z in f.snapshots]);q=(t>=485)&(t<540);ax.plot(t[q],np.linalg.norm(pp[q]-np.median(pp[(t>=450)&(t<484)],axis=0),axis=1),label=mode)
        ax.set_title(n);ax.set_ylabel("published displacement (m)");ax.legend()
    axes[-1].set_xlabel("T0 seconds");savefig(out/"movement_state_response.svg")
    configs=sorted(set(x["configuration"] for x in abl)); med=[];lat=[]
    for c in configs:
        q=[x for x in abl if x["configuration"]==c];med.append(np.median([float(x["static_rms_m"]) for x in q])); ll=[float(x["movement_unlock_latency_s"]) for x in q if x["movement_unlock_latency_s"]!=""];lat.append(np.median(ll) if ll else np.nan)
    plt.figure(figsize=(8,6));plt.scatter(np.array(med)*1000,lat);[plt.annotate(c,(med[i]*1000,lat[i]),fontsize=6) for i,c in enumerate(configs) if np.isfinite(lat[i])];plt.xlabel("median static RMS (mm)");plt.ylabel("median development unlock latency (s)");savefig(out/"pareto_static_vs_release.svg")

def write_autopsy(out,data,imu_all,uwb_all,pos_all):
    """Reconstruct S1 causally from authoritative inputs; no temporary files."""
    old_manifest=json.loads((data/"analysis_state_adaptive_fusion_v1/PARAMETER_MANIFEST.json").read_text())
    rows=[]; causes={}
    for node,(m0,m1) in MOVES.items():
        imu,uwb,pos=imu_all[node],uwb_all[node],pos_all[node]
        clock=fit_node_clock(uwb); it=local_to_t0_s(imu["b306_us"],clock); ut=local_to_t0_s(uwb["strobe_us"],clock)
        acc,gyr,_=imu_physical(imu); bias=np.array(old_manifest["per_node"][node]["gyro_bias_dps"]); gres=gyr-bias
        idx,feat=rolling_features(acc,gres,old_manifest["per_node"][node]["local_gravity_g"]); ct=it[idx]
        gn=np.linalg.norm(gres,axis=1); an=np.linalg.norm(acc,axis=1)
        gravity_ref=np.mean(acc[(it>=1)&(it<60)],axis=0); gravity_ref/=np.linalg.norm(gravity_ref)
        par=adaptive_params(old_manifest,node); est=StateAdaptiveFusion(par); ci=ui=0; node_rows=[]
        last_uwb=np.full(3,np.nan); last_ranges=np.full(8,np.nan); last_ut=np.nan; last_nis=np.nan
        pre=(ut>=450)&(ut<484); range_base=np.median(uwb["range_mm"][pre],axis=0).astype(float)
        pos_prev=None; ut_prev=None; vlike=np.nan; first={}
        while ci<len(idx) or ui<len(ut):
            if ui<len(ut) and (ci>=len(idx) or ut[ui]<ct[ci]):
                p=pos["p"][ui]; est.process_uwb(float(ut[ui]),p,status="ok",record_index=ui)
                last_uwb=p.copy(); last_ranges=uwb["range_mm"][ui].astype(float); last_ut=float(ut[ui])
                last_nis=est.audit[-1]["nis"] if est.audit[-1]["nis"]!="" else math.nan
                vlike=(math.nan if pos_prev is None else
                       float(np.linalg.norm(p-pos_prev)/max(ut[ui]-ut_prev,1e-9)))
                pos_prev=p.copy(); ut_prev=float(ut[ui]); ui+=1; continue
            t=float(ct[ci]); ii=int(idx[ci]); before=est.state; center,scatter=est._platform(t)
            shift=(math.inf if center is None or est.lock_position is None else
                   float(np.linalg.norm(center-est.lock_position)))
            vals=[float(feat[k][ci]) for k in ("gyro_rms_dps","accel_dev_rms_g","gyro_std_dps","accel_std_g")]
            th=[par.gyro_rms_threshold_dps,par.accel_dev_rms_threshold_g,par.gyro_std_threshold_dps,par.accel_std_threshold_g]
            votes=sum(v>x for v,x in zip(vals,th)); quiet=votes<2
            stable=center is not None and scatter<=par.platform_stability_threshold_m
            motion=(not quiet) and center is not None and shift>par.platform_shift_threshold_m
            speed=float(np.linalg.norm(est.x[3:])); speed_ok=speed<=par.stationary_speed_threshold_mps
            angles={}
            for window in (.25,1.,3.):
                j=np.searchsorted(it,t-window,"left"); seg=np.arange(j,ii+1)
                delta=np.diff(it[seg],prepend=it[seg[0]]); angles[window]=float(np.sum(gn[seg]*delta))
            gvec=np.mean(acc[max(0,ii-99):ii+1],axis=0); gvec/=np.linalg.norm(gvec)
            gangle=math.degrees(math.acos(float(np.clip(gvec@gravity_ref,-1,1))))
            longj=np.searchsorted(it,t-2.,"left")
            dwell_before=est.motion_evidence_elapsed; quiet_before=est.quiet_elapsed; settle_before=est.settling_elapsed
            est.process_control(t,{k:float(v[ci]) for k,v in feat.items()},sequence_advancing=True)
            row={"node":node,"t0_s":t,"imu_index":ii,"imu_seq":int(imu["seq"][ii]),"state_before":before,"state_after":est.state,
              "acc_x_g":acc[ii,0],"acc_y_g":acc[ii,1],"acc_z_g":acc[ii,2],"acc_norm_g":an[ii],
              "gyro_x_dps":gyr[ii,0],"gyro_y_dps":gyr[ii,1],"gyro_z_dps":gyr[ii,2],"gyro_norm_dps":np.linalg.norm(gyr[ii]),
              "gyro_bias_corrected_norm_dps":gn[ii],"gyro_angle_025_deg":angles[.25],"gyro_angle_1_deg":angles[1.],"gyro_angle_3_deg":angles[3.],
              "gravity_direction_change_deg":gangle,"gyro_rms_short_dps":vals[0],"acc_dev_rms_short_g":vals[1],
              "gyro_std_short_dps":vals[2],"acc_std_short_g":vals[3],"gyro_std_long_dps":float(np.std(gn[longj:ii+1])),
              "acc_std_long_g":float(np.std(an[longj:ii+1])),"active_votes":votes,"imu_quiet":int(quiet),
              "platform_center_available":int(center is not None),"platform_scatter_m":scatter,"platform_stable":int(stable),
              "candidate_shift_m":shift,"motion_predicate":int(motion),"speed_mps":speed,"speed_ok":int(speed_ok),
              "motion_dwell_before_s":dwell_before,"motion_dwell_after_s":est.motion_evidence_elapsed,
              "quiet_dwell_before_s":quiet_before,"quiet_dwell_after_s":est.quiet_elapsed,
              "settling_dwell_before_s":settle_before,"settling_dwell_after_s":est.settling_elapsed,
              "published_x_m":est.x[0],"published_y_m":est.x[1],"published_z_m":est.x[2],
              "locked_x_m":est.lock_position[0] if est.lock_position is not None else math.nan,
              "locked_y_m":est.lock_position[1] if est.lock_position is not None else math.nan,
              "locked_z_m":est.lock_position[2] if est.lock_position is not None else math.nan,
              "candidate_x_m":center[0] if center is not None else math.nan,
              "candidate_y_m":center[1] if center is not None else math.nan,
              "candidate_z_m":center[2] if center is not None else math.nan,"last_uwb_t0_s":last_ut,
              "t4_x_m":last_uwb[0],"t4_y_m":last_uwb[1],"t4_z_m":last_uwb[2],"t4_velocity_like_mps":vlike,
              "last_uwb_nis":last_nis,"range_change_l2_mm":float(np.linalg.norm(last_ranges-range_base)),
              **{f"range_{k}_mm":last_ranges[k] for k in range(8)}}
            if 480<=t<550:node_rows.append(row)
            for key,value in (("imu_active",not quiet),("candidate_shift",center is not None and shift>par.platform_shift_threshold_m),
                              ("motion_predicate",motion),("platform_stable",stable),("speed_ok",speed_ok)):
                if value and key not in first and t>=m0-5:first[key]=t
            ci+=1
        rows.extend(node_rows[::2])
        causes[node]={"movement_window":[m0,m1],"thresholds":{"gyro_rms":th[0],"accel_dev_rms":th[1],
          "gyro_std":th[2],"acc_std":th[3],"shift_m":par.platform_shift_threshold_m,
          "scatter_m":par.platform_stability_threshold_m,"exit_dwell_s":par.exit_dwell_s,
          "speed_mps":par.stationary_speed_threshold_mps},"first_predicate_true":first,
          "transitions_480_800":[x for x in est.transitions if 480<=x["time_s"]<800]}
    write_csv(out/"S1_FAILURE_TIMELINE.csv",rows,list(rows[0]))
    causes["causal_conclusions"]={"BSFC2CC":"candidate shift crossed 0.18 m at 496.836446 s; 0.75 s persistent evidence plus cadence produced unlock at 497.986363 s",
      "BSFAA61":"0.306714 m maximum candidate shift remained below node-specific 0.312501 m threshold; motion predicate never true despite strong IMU",
      "silent_creep":"stationary consensus updated both x and lock_position whenever each incremental candidate shift remained below threshold",
      "relock":"C2CC velocity reached 3.082959 m/s; quiet/stable overlap was intermittent and three settling entries were interrupted; AA61 never unlocked"}
    write_json(out/"S1_FAILURE_CAUSES.json",causes)
    (out/"S1_FAILURE_AUTOPSY.md").write_text("""# S1 failure autopsy

The audit was completed before S2 was implemented. BSFC2CC first crossed its 0.18 m candidate-shift threshold at T0+496.836446 s. The required persistent predicate then accumulated until the unlock at 497.986363 s; candidate formation and shift crossing, not the nominal 0.75 s dwell alone, dominated the 5.986363 s latency.

BSFAA61 contained strong discarded motion evidence: 0.5 s gyro RMS reached 94.490886 dps, one-second integrated absolute gyro angle 40.337822 degrees, and gravity-direction change 8.391075 degrees. Its candidate shift peaked at 0.306714 m, just below the high-scatter-derived 0.312501 m threshold, so the conjunctive predicate never became true.

S1's stationary consensus changed both the estimator position and `lock_position` whenever each incremental shift remained under the threshold. This let AA61 creep approximately [-81.8,-48.9,-401.6] mm without an unlock. C2CC's inferred velocity reached 3.082959 m/s. It entered SETTLING three times much later, but quiet/stability did not persist and each attempt was interrupted. No timestamp reversal, sequence gap, unit error, or half-open-window error contributed. The root causes were conjunctive evidence loss, absolute scatter-dependent thresholds, mutable lock semantics, and a relock contract that coupled noisy platform stability to an inflated velocity state.
""")

def docs(out,man,metrics,moves,tables,links,abl,events,transitions,integrity):
    s2p=[x for x in metrics if x["mode"]=="S2P"];s2r=[x for x in metrics if x["mode"]=="S2R"]
    mp=np.median([x["position_rms_m"] for x in s2p]);mr=np.median([x["position_rms_m"] for x in s2r])
    mv=[x for x in moves if x["mode"] in ("S2P","S2R")]; good=all(x["unlock_s"]!="" and x["relock_s"]!="" for x in mv)
    persistent=sum(x["persistent_false_platform"] for x in tables)
    creep_free=all(x["published_locked_max_step_m"]<=1e-12 for x in s2p+s2r)
    ambiguous_moving=sum(x["classification"]=="AMBIGUOUS" and x["to_state"]=="MOVING" for x in events)
    ambiguous_conflicts=sum(x["classification"]=="RF_PLATFORM_CHANGE" for x in events)
    settling_interruptions=sum(x["reason"]=="SETTLING_INTERRUPTED_BY_MOTION_EVIDENCE" for x in transitions)
    unverified_relocks=sum(x["to_state"]=="STATIONARY" and x["reason"] in
                           ("SETTLED_ROBUST_PLATFORM","CONFLICT_RESOLVED_NEW_STATIONARY_PLATFORM") and
                           not (x["node"] in MOVES and MOVES[x["node"]][0]-2<=x["time_s"]<MOVES[x["node"]][1]+60)
                           for x in transitions)
    selected="S2P_AND_S2R_BOTH_REQUIRED" if good and creep_free and persistent==0 else "NO_ARCHITECTURE_READY"
    decision={"verdict":"DATASET_EXHAUSTED_S2_READY_FOR_NEW_VALIDATION" if good else "DATASET_EXHAUSTED_CONDITIONAL",
      "architecture_disposition":selected,"full_vector_disposition":"FULL_VECTOR_MODE_BLOCKED_FRAME_BINDING",
      "s2p_static_median_rms_m":mp,"s2r_static_median_rms_m":mr,"s2r_materially_outperforms_s2p":bool(mr<.9*mp),
      "silent_platform_creep_eliminated":creep_free,
      "all_development_moves_unlock_and_relock":good,"persistent_false_table_transitions":persistent,
      "ambiguous_unverified_moving_transitions":ambiguous_moving,
      "rf_platform_conflict_entries":ambiguous_conflicts,"settling_interruptions":settling_interruptions,
      "unverified_new_platform_relocks":unverified_relocks,
      "ui_experimental_ready":bool(good and creep_free and persistent==0),
      "ui_readiness_scope":"CONTROLLED_EXPERIMENT_ONLY_WITH_VISIBLE_AMBIGUITY_STATE",
      "human_ik_fk_accuracy_ready":False,"development_dataset_only":True}
    write_json(out/"EXECUTIVE_DECISION.json",decision)
    (out/"S2_ARCHITECTURE.md").write_text("""# S2 architecture

S2 separates the internal Kalman state, immutable published lock, and causal background platform candidate. Its states are INIT, STATIONARY, MOTION_SUSPECTED, MOVING, SETTLING and PLATFORM_CONFLICT. Stationary UWB never moves the published lock. IMU fast activity, one-second cumulative absolute gyro angle, gravity-direction change, short variance, covariance-normalized T4 shift and per-link range-vector shift produce causal evidence. PLATFORM_CONFLICT preserves ambiguity when UWB shifts without adequate IMU confirmation.

S2P applies asynchronous gated T4 position updates to [p,v] only in MOVING/SETTLING. S2R processes each sweep sequentially in ascending Anchor ID, subtracts the frozen V4 anchor residual delay exactly once, and performs scalar nonlinear range EKF updates with per-node/per-link calibration variance. Both apply ZUPT while stationary/settling and relock only after quietness, a covariance-normalized stable candidate, sufficient Anchor support and a bounded dwell. A relock is an explicit recovery where T4 may initialize the new platform, as allowed by the S2R contract.
""")
    (out/"FRAME_AND_IDENTIFIABILITY_CLOSURE.md").write_text("""# Frame and identifiability closure

The B306 parser preserves the JY61P signed int16 X/Y/Z register order and source-proved scale. Raw sensor-axis labels and signs are therefore `PROVEN_FROM_SOURCE` as register-frame quantities, but their physical PCB directions are not survey-bound. Each board's gravity direction in sensor coordinates is `OBSERVABLE_FROM_STATIC_GRAVITY`. V4 +Z alignment with physical gravity is `REQUIRES_SURVEYED_GRAVITY_REFERENCE`. Sensor yaw relative to V4, complete signed sensor-to-V4 rotation and lever arm are `UNIDENTIFIABLE` from this capture. The two development moves lack controlled excitation and external attitude truth; fitting them would be circular and not independently testable. Full vector propagation remains `BLOCKED_FRAME_BINDING`; S2V_DEV was not implemented.
""")
    comps={"raw_axis_order_and_signedness":"PROVEN_FROM_SOURCE","gravity_in_sensor_frame":"OBSERVABLE_FROM_STATIC_GRAVITY",
      "v4_plus_z_is_physical_up":"REQUIRES_SURVEYED_GRAVITY_REFERENCE","sensor_yaw_to_v4":"UNIDENTIFIABLE",
      "complete_signed_sensor_to_v4_rotation":"REQUIRES_NEW_CONTROLLED_MOTION","movement_fitted_transform":"WEAKLY_ESTIMABLE_DEVELOPMENT_ONLY_NOT_INDEPENDENTLY_TESTABLE",
      "lever_arm":"UNIDENTIFIABLE","full_vector_inertial_propagation":"BLOCKED_FRAME_BINDING"};write_json(out/"FRAME_COMPONENTS.json",comps)
    (out/"DATASET_CAPABILITY_MATRIX.md").write_text("""# Dataset capability matrix

| Question | Disposition |
|---|---|
| Static node-specific IMU/UWB noise | Supported, calibration plus held-out static window |
| S1 failure mechanism | Timestamp-level causal closure |
| S2 lock/conflict mechanics | Internal consistency, development dataset only |
| S2 motion generalization | Requires new held-out trajectory |
| Solved-position versus raw-range plumbing | Supported internally with fixed geometry/delay |
| Absolute localization accuracy | Unavailable; no ground truth |
| Full vector inertial propagation | Blocked by sensor-to-V4 binding |
| Node-to-body assignment / IK/FK | NOT_APPLICABLE_TO_CURRENT_CAPTURE |
""")
    (out/"LIMITATIONS_AND_UNIDENTIFIABLES.md").write_text(f"""# Limitations and unidentifiables

The two movements are development evidence because they were inspected before S2. T4 is an internal relative reference, not truth. The room frame is not surveyed gravity-up. No sensor-to-segment extrinsic, lever arm, external trajectory, absolute position error, yaw observability or human-body model can be inferred. Static improvements may be smoothing/locking rather than spatial inertial propagation. S2's bidirectionality is asymmetric: IMU controls state and process-noise mode; UWB corrects p/v during movement and supplies platform integrity. It is not full vector inertial Fusion.

The complete run contains {ambiguous_moving} `AMBIGUOUS` transitions into MOVING, {ambiguous_conflicts} RF-platform conflict entries, {settling_interruptions} settling interruptions, and {unverified_relocks} new-platform relocks outside the two development windows, counted across S2P and S2R. They are not called false motion because this tabletop capture has no independent physical truth there. They do prevent interpreting the successful two-window replay as motion validation. BSFC2CC's repeated settling interruptions are a visible state-chattering limitation, not hidden by the headline result.
""")
    (out/"NEXT_CONTROLLED_EXPERIMENT.md").write_text("""# Minimal next controlled experiment

Before capture, survey the physical gravity/up relation of V4, verify signed sensor axes, fix one module in a measured-orientation fixture, record lever arm and exact start pose, and freeze S2 code, covariance, thresholds and pass/fail metrics. Use three disjoint repetitions: calibration, development, and an untouched validation repetition whose labels remain hidden until the code and manifest are frozen.

Each repetition must contain: 60 s stationary; measured slow and fast translations along each surveyed V4 axis; pure positive/negative rotations about every usable sensor axis; combined translation/rotation; at least six stop-and-hold transitions; a table/common-mode vibration negative control; and the complete trajectory repeated in the opposite direction. Use an external surveyed distance/trajectory reference and timestamp it on a verified common clock; require <1 ms synchronization uncertainty. Pre-register release latency, false-release count, relock latency, static scatter, trajectory displacement consistency and numerical/accounting gates.

Freeze these pass/fail thresholds before the hidden validation replay: zero sequence gaps; closed UWB accounting; finite symmetric PSD covariance; exactly zero published movement while a lock remains STATIONARY; zero transitions to MOVING and zero new-platform relocks during the common-mode negative control; fast-motion release <=1.0 s; slow-motion release <=2.0 s; relock <=5.0 s after the measured stop; no return to the old platform; and dynamic position RMSE <102.6 mm against the external reference. S2R may be called materially better than S2P only if its hidden-validation RMSE is at least 10% lower without worsening any state-machine gate. Any parameter change after viewing validation labels invalidates that repetition as held out.

Later product work assigns arbitrary BSF IDs to ten fixed body locations, estimates per-session sensor-to-segment extrinsics, freezes body topology, then validates IK/FK and the stick-figure UI against external motion truth. Node-to-body assignment and human IK/FK are `NOT_APPLICABLE_TO_CURRENT_CAPTURE`.
""")
    modes={"B0":"canonical T4 input","S1":"frozen failed predecessor","S2P":"solved-position S2","S2R":"raw-range S2 sequential A-H updates","I0/I1":"local inertial controls","H2/H3/H5":"BLOCKED_FRAME_BINDING","S2V_DEV":"NOT_IMPLEMENTED_BLOCKED_FRAME_BINDING"};write_json(out/"MODE_DEFINITIONS.json",modes)
    write_json(out/"PARETO_SUMMARY.json",{"configurations":sorted(set(x["configuration"] for x in abl)),"selection_rule":"causal mechanism and sensitivity stability; known moves are development-only","static_s2p_median_rms_m":mp,"static_s2r_median_rms_m":mr})
    cp=np.median([x["candidate_rms_m"] for x in s2p]); cr=np.median([x["candidate_rms_m"] for x in s2r])
    report=f"""# v47 Fusion dataset exhaustion

Verdict: `{decision['verdict']}`; architecture: `{selected}`. S1's exact failure was a conjunctive candidate-shift gate plus mutable lock semantics. S2 removes silent creep by construction and adds auditable suspicion/conflict states. All motion results are `DEVELOPMENT_REPLAY_NOT_GENERALIZATION_EVIDENCE`.

Held-out published-lock RMS is {mp*1000:.3f} mm for S2P and {mr*1000:.3f} mm for S2R; this zero is lock semantics, not absolute accuracy. Background-candidate median RMS is {cp*1000:.3f} mm and {cr*1000:.3f} mm respectively. Both use actual hardware timestamps and complete observation accounting. Persistent false table transitions across both modes: {persistent}. The remainder contains {ambiguous_moving} unverified MOVING entries and {settling_interruptions} settling interruptions across both modes; without physical truth these remain ambiguous rather than being called false. Full vector inertial propagation remains blocked. The dataset now supports implementation/internal-consistency closure and a frozen next experiment, not motion generalization or human IK/FK accuracy.
"""; (out/"REPORT.md").write_text(report)

def replay_all(data,positions_path,prior,layout_path,manifest_path,out):
    out.mkdir(parents=True,exist_ok=True); man=json.loads(manifest_path.read_text()); imu,uwb,audit=load_capture(data);pos=read_positions(positions_path);static,table=annotations(prior)
    inputs={n:build_inputs(imu[n],uwb[n],pos[n],man["per_node"][n]) for n in NODES}
    common=Counter()
    for n in NODES:
        p=params(man,n); feat=inputs[n]["features"]
        for i,t in enumerate(inputs[n]["control_t"]):
            vals=[feat[k][i] for k in ("gyro_rms_dps","accel_dev_rms_g","gyro_std_dps","accel_std_g")]; th=[p.gyro_rms_threshold_dps,p.accel_dev_rms_threshold_g,p.gyro_std_threshold_dps,p.accel_std_threshold_g]
            if sum(v>x for v,x in zip(vals,th))>=2: common[int(math.floor(t*20+.5))]+=1
    global CTX;CTX={"manifest":man,"imu":imu,"uwb":uwb,"pos":pos,"inputs":inputs,"table":table,"common_bins":common}
    with mp.get_context("fork").Pool(5) as pool: got=dict((n,(m,a)) for n,m,a in pool.map(worker,NODES))
    results={n:got[n][0] for n in NODES};abl=[x for n in NODES for x in got[n][1]]
    metrics=[];moves=[];tabs=[];trans=[];links=[];account={}
    old=list(csv.DictReader((data/"analysis_state_adaptive_fusion_v1/PER_NODE_MODE_METRICS.csv").open()))
    metrics.extend(old)
    for n in NODES:
        for mode in ("S2P","S2R"):
            f=results[n][mode];metrics.append(static_metrics(n,mode,f,pos[n]["p"],inputs[n]["uwb_t"]));tabs+=table_rows(n,mode,f,table)
            trans += [{"node":n,"mode":mode,**x} for x in f.transitions];account[f"{n}:{mode}"]=f.accounting()
            if n in MOVES:moves.append(movement(n,mode,f,pos[n]["p"],inputs[n]["uwb_t"]))
        links+=link_characterization(n,uwb[n],pos[n]["p"],inputs[n]["uwb_t"],man,results[n]["S2R"],table)
    # Full-run event mining from causal transitions; no new event is called known motion.
    bins=Counter(round(x["time_s"]) for x in trans if x["to_state"] in ("MOTION_SUSPECTED","MOVING","PLATFORM_CONFLICT"))
    mined=[]
    for x in trans:
        if x["to_state"] not in ("MOTION_SUSPECTED","MOVING","PLATFORM_CONFLICT"):continue
        known=x["node"] in MOVES and MOVES[x["node"]][0]-2<=x["time_s"]<MOVES[x["node"]][1]+10
        cls="SUPPORTED_NODE_MOTION" if known else "COMMON_MODE_DISTURBANCE" if bins[round(x["time_s"]) ]>=5 else "RF_PLATFORM_CHANGE" if x["to_state"]=="PLATFORM_CONFLICT" else "AMBIGUOUS"
        mined.append({**x,"classification":cls,"independent_physical_truth":int(known),"note":"development annotation" if known else "not physical-motion truth"})
    integrity={"raw_sha256":audit.raw_sha256,"imu_sequence_gaps":{n:sequence_gap_count(imu[n]["seq"],1<<16) for n in NODES},
      "uwb_sequence_gaps":{n:sequence_gap_count(uwb[n]["sweep"],1<<32) for n in NODES},
      "all_covariance_finite_psd_symmetric":all(f.covariance_min_eigenvalue>=-1e-10 and f.covariance_max_asymmetry<=1e-10 for r in results.values() for f in r.values()),
      "observation_accounting_closed":all(x["closed"] for x in account.values()),"deterministic_replay_equality":"PENDING_SECOND_RUN"}
    write_autopsy(out,data,imu,uwb,pos);write_json(out/"S2_PARAMETER_MANIFEST.json",man)
    fields=list(metrics[0]);
    for r in metrics:
        for k in r:
            if k not in fields:fields.append(k)
    write_csv(out/"PER_NODE_MODE_METRICS.csv",metrics,fields)
    write_csv(out/"MOVEMENT_RESPONSE.csv",moves,list(moves[0]));write_csv(out/"TABLE_VIBRATION_RESPONSE.csv",tabs,list(tabs[0]));write_csv(out/"STATE_TRANSITIONS.csv",trans,list(trans[0]));write_csv(out/"UWB_LINK_CHARACTERIZATION.csv",links,list(links[0]));write_json(out/"UWB_UPDATE_ACCOUNTING.json",account)
    write_csv(out/"FULL_RUN_EVENT_MINING.csv",mined,list(mined[0]) if mined else ["node"]);write_csv(out/"ABLATION_RESULTS.csv",abl,list(abl[0]));write_json(out/"NUMERICAL_INTEGRITY.json",integrity)
    render_plots(out,results,pos,inputs,abl);docs(out,man,metrics,moves,tabs,links,abl,mined,trans,integrity)
    names=sorted(p.name for p in out.iterdir() if p.is_file() and p.name!="SHA256SUMS");(out/"SHA256SUMS").write_text("".join(f"{sha256(out/n)}  {n}\n" for n in names))

def main():
    ap=argparse.ArgumentParser();sub=ap.add_subparsers(dest="cmd",required=True)
    d=sub.add_parser("derive");r=sub.add_parser("replay")
    for p in (d,r):p.add_argument("--data",type=Path,required=True);p.add_argument("--positions",type=Path,required=True);p.add_argument("--prior",type=Path,required=True);p.add_argument("--layout",type=Path,required=True)
    d.add_argument("--geometry",type=Path,required=True);d.add_argument("--s1-manifest",type=Path,required=True);d.add_argument("--out",type=Path,required=True)
    r.add_argument("--manifest",type=Path,required=True);r.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    if a.cmd=="derive":write_json(a.out,derive(a.data,a.positions,a.prior,a.layout,a.geometry,a.s1_manifest))
    else:replay_all(a.data,a.positions,a.prior,a.layout,a.manifest,a.out)
if __name__=="__main__":main()
