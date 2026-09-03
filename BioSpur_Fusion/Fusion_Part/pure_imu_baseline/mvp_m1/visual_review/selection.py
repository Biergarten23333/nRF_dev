"""Deterministic review interval selection from frozen raw orientation energy."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import CAPTURES as CAPTURE_SPECS

MVP_ROOT = Path("/tmp/biospur_pure_imu_mvp_m1_20260823T135120Z")
STAGE1_ROOT = Path("/tmp/biospur_pure_imu_baseline_c123_20260823T091031Z")


def load_mvp_capture(capture: str) -> dict[str, np.ndarray]:
    path=MVP_ROOT/f"CAPTURE{capture}_MVP_REPLAY_DATA.npz"
    with np.load(path,allow_pickle=False) as archive:
        return {name:archive[name].copy() for name in archive.files if name != "metadata_json"}


def angular_motion_energy(raw: dict[str,np.ndarray]) -> dict[str,np.ndarray]:
    q=raw["q_GB_wxyz"].astype(np.float64); valid=raw["valid"].astype(bool); t=raw["time_s"].astype(np.float64)
    dots=np.abs(np.sum(q[1:]*q[:-1],axis=-1)); dots=np.clip(dots,-1.0,1.0)
    dt=np.diff(t)[:,None]; speed=2*np.arccos(dots)/dt
    pair_valid=valid[1:]&valid[:-1]&np.isfinite(speed); speed[~pair_valid]=np.nan
    speed=np.vstack([np.full((1,q.shape[1]),np.nan),speed])
    groups={"whole":np.arange(10),"core":np.array([0,1]),"arms":np.array([2,3,4,5]),"lower_body":np.array([6,7,8,9])}
    output={"per_node_rad_s":speed}
    for name,indices in groups.items():
        values=np.square(speed[:,indices]);count=np.sum(np.isfinite(values),axis=1)
        mean=np.divide(np.nansum(values,axis=1),count,out=np.full(len(speed),np.nan),where=count>0)
        output[name]=np.sqrt(mean)
    return output


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def action_intervals(capture: str, duration_s: float) -> tuple[list[dict],dict]:
    path=CAPTURE_SPECS[capture].event_path; rows=_rows(path); intervals=[]
    if capture=="1":
        t0=next(row["monotonic"] for row in rows if row.get("event")=="ACTION_START" and row.get("action")=="initial_still" and row.get("attempt")==2)
        alignment={"source":str(path),"zero_event":"initial_still attempt 2 ACTION_START","source_clock":"host monotonic seconds","source_zero":t0}
        for index,row in enumerate(rows):
            if row.get("event")!="ACTION_START" or row["monotonic"]<t0: continue
            action=row.get("action"); attempt=row.get("attempt")
            stop=next((candidate for candidate in rows[index+1:] if candidate.get("event")=="ACTION_STOP" and candidate.get("action")==action and (attempt is None or candidate.get("attempt")==attempt)),None)
            if stop is None: continue
            intervals.append({"action_id":action,"start_s":row["monotonic"]-t0,"end_s":stop["monotonic"]-t0,"metadata":{"attempt":attempt,"description":row.get("description")}})
    elif capture=="2":
        start_index=next(i for i,row in enumerate(rows) if row.get("event")=="ACTION_START" and row.get("context",{}).get("action_id")=="00_initial_still" and row.get("context",{}).get("attempt")==2)
        t0=rows[start_index]["host_monotonic_ns"]
        alignment={"source":str(path),"zero_event":"final accepted 00_initial_still attempt 2 ACTION_START","source_clock":"host monotonic nanoseconds","source_zero":t0}
        for index,row in enumerate(rows[start_index:],start_index):
            if row.get("event")!="ACTION_START": continue
            context=row.get("context",{}); action=context.get("action_id"); attempt=context.get("attempt"); rep=context.get("rep")
            stop=next((candidate for candidate in rows[index+1:] if candidate.get("event")=="ACTION_STOP" and candidate.get("context",{}).get("action_id")==action and candidate.get("context",{}).get("attempt")==attempt and candidate.get("context",{}).get("rep")==rep),None)
            if stop is None: continue
            intervals.append({"action_id":action,"start_s":(row["host_monotonic_ns"]-t0)/1e9,"end_s":(stop["host_monotonic_ns"]-t0)/1e9,"metadata":{"attempt":attempt,"rep":rep}})
    else:
        first=next(row for row in rows if row.get("event")=="ACTION_START" and row.get("action_id")=="S00_INITIAL_STILL")
        t0=first["boundary"]["last_raw_monotonic"]
        alignment={"source":str(path),"zero_event":"S00_INITIAL_STILL ACTION_START boundary","source_clock":"collector raw monotonic seconds","source_zero":t0}
        for index,row in enumerate(rows):
            if row.get("event")!="ACTION_START": continue
            action=row.get("action_id"); stop=next((candidate for candidate in rows[index+1:] if candidate.get("event")=="ACTION_STOP" and candidate.get("action_id")==action),None)
            if stop is None: continue
            intervals.append({"action_id":action,"start_s":row["boundary"]["last_raw_monotonic"]-t0,"end_s":stop["boundary"]["last_raw_monotonic"]-t0,"metadata":{"block_id":row.get("block_id")}})
    for item in intervals:
        item["start_s"]=max(0.0,float(item["start_s"])); item["end_s"]=min(duration_s,float(item["end_s"]))
    return [item for item in intervals if item["end_s"]>item["start_s"]],alignment


def _action_spans(actions: list[dict], keywords: tuple[str,...]) -> list[tuple[float,float,str]]:
    return [(item["start_s"],item["end_s"],item["action_id"]) for item in actions if any(word in item["action_id"].lower() for word in keywords)]


def _overlap(a: tuple[float,float],b: tuple[float,float]) -> float:
    return max(0.0,min(a[1],b[1])-max(a[0],b[0]))


def best_energy_window(t: np.ndarray, energy: np.ndarray, spans: list[tuple[float,float,str]], duration: float,
                       exclusions: list[tuple[float,float]] | None=None) -> tuple[float,float,float,list[str]]:
    exclusions=exclusions or []; candidates=[]
    for span_start,span_end,label in spans:
        start=max(0.0,span_start); end=min(float(t[-1]),span_end)
        if end-start<duration: continue
        for value in np.arange(start,end-duration+1e-9,0.5):
            interval=(float(value),float(value+duration))
            if any(_overlap(interval,blocked)>0.25*duration for blocked in exclusions): continue
            mask=(t>=interval[0])&(t<=interval[1]); score=float(np.nanmean(energy[mask]))
            if np.isfinite(score): candidates.append((score,interval[0],interval[1],label))
    if not candidates:
        raise RuntimeError("no finite deterministic motion-energy window")
    score,start,end,label=max(candidates,key=lambda value:(value[0],-value[1]))
    labels=sorted({entry[2] for entry in spans if _overlap((start,end),(entry[0],entry[1]))>0})
    return start,end,score,labels or [label]


def event_groups(raw: dict[str,np.ndarray]) -> list[dict]:
    t=raw["time_s"]; valid=raw["valid"].astype(bool); reset=raw["filter_reset"].astype(bool); names=[str(value) for value in raw["segment_names"]]
    events=[]
    for frame,node in np.argwhere(reset): events.append({"kind":"RESET","start_s":float(t[frame]),"end_s":float(t[frame]),"node":names[node]})
    for node,name in enumerate(names):
        inv=~valid[:,node]; starts=np.flatnonzero(inv&np.r_[True,valid[:-1,node]]); stops=np.flatnonzero(inv&np.r_[valid[1:,node],True])
        for start,stop in zip(starts,stops): events.append({"kind":"UNAVAILABLE","start_s":float(t[start]),"end_s":float(t[stop]),"node":name})
    windows=sorted(((max(0.0,item["start_s"]-2.0),min(float(t[-1]),item["end_s"]+2.0),item) for item in events),key=lambda value:(value[0],value[1],value[2]["kind"],value[2]["node"]))
    groups=[]
    for start,end,item in windows:
        if groups and start<=groups[-1]["end_s"]:
            groups[-1]["end_s"]=max(groups[-1]["end_s"],end); groups[-1]["events"].append(item)
        else: groups.append({"start_s":start,"end_s":end,"events":[item]})
    return groups


def _quantize(raw: dict, start: float, end: float) -> tuple[int,int,float,float]:
    t=raw["time_s"]; first=int(np.searchsorted(t,start,side="left")); last=int(np.searchsorted(t,end,side="right")-1)
    first=max(0,min(first,len(t)-1)); last=max(first,min(last,len(t)-1))
    # Render at 30 Hz from the 60 Hz replay. Keep an even start for exact stride.
    if first%2: first+=1
    if last<first: last=first
    return first,last,float(t[first]),float(t[last])


def build_ledger(capture: str, raw: dict[str,np.ndarray]) -> dict:
    t=raw["time_s"]; duration=float(t[-1]); energy=angular_motion_energy(raw); actions,alignment=action_intervals(capture,duration)
    all_span=[(10.0,max(16.0,duration-10.0),"ALL_ACTIONS")]
    whole_keywords=("walk","golf","boxing","squat","trunk","hula","pb_")
    arm_keywords=("arm","shoulder","elbow","boxing","golf","pa_","pb_","el_","er_","pl_","pr_")
    lower_keywords=("knee","heel","squat","walk","hip")
    whole_spans=_action_spans(actions,whole_keywords) or all_span
    arm_spans=_action_spans(actions,arm_keywords) or all_span
    explicit_lower_spans=_action_spans(actions,lower_keywords);lower_spans=explicit_lower_spans or all_span
    calibration=json.loads((STAGE1_ROOT/f"CAPTURE{capture}_REPLAY_RESULT.json").read_text(encoding="utf-8"))["calibration"]["stationary_window_s"]
    early=(max(0.0,float(calibration[0])-2.0),min(duration,float(calibration[1])+2.0))
    chosen=[early]
    whole=best_energy_window(t,energy["whole"],whole_spans,6.0,chosen); chosen.append((whole[0],whole[1]))
    arm=best_energy_window(t,energy["arms"],arm_spans,6.0,chosen); chosen.append((arm[0],arm[1]))
    lower=best_energy_window(t,energy["lower_body"],lower_spans,6.0,chosen); chosen.append((lower[0],lower[1]))
    intervals=[
        {"label":"EARLY_STANDING_CALIBRATION_ADJACENT","start_s":early[0],"end_s":early[1],"selection_reason":"frozen Stage 1 stationary calibration window with 2 s context","action_metadata":[]},
        {"label":"REPRESENTATIVE_LARGE_WHOLE_BODY_MOTION","start_s":whole[0],"end_s":whole[1],"selection_reason":"maximum deterministic whole-body raw quaternion angular-motion energy within whole-body action metadata spans","action_metadata":whole[3]},
        {"label":"REPRESENTATIVE_ARM_DOMINANT_MOTION","start_s":arm[0],"end_s":arm[1],"selection_reason":"maximum deterministic arm-group raw quaternion angular-motion energy within arm action metadata spans","action_metadata":arm[3]},
        {"label":"REPRESENTATIVE_LOWER_BODY_MOTION","start_s":lower[0],"end_s":lower[1],"selection_reason":("maximum deterministic lower-body raw quaternion angular-motion energy within lower-body action metadata spans" if explicit_lower_spans else "no lower-body action metadata available; maximum deterministic lower-body raw angular-motion energy over the capture"),"action_metadata":lower[3] if explicit_lower_spans else []},
        {"label":"LATE_SESSION_AND_TAIL_VALIDITY","start_s":max(0.0,duration-6.0),"end_s":duration,"selection_reason":"deterministic final six seconds, including tail validity transitions","action_metadata":[]},
    ]
    if capture=="2":
        for checkpoint in (10.10,1130.45,1198.35):
            intervals.append({"label":f"REQUIRED_CHECKPOINT_{checkpoint:.2f}S","start_s":max(0,checkpoint-3),"end_s":min(duration,checkpoint+3),"selection_reason":"explicit previously discussed Capture 2 checkpoint with ±3 s context","action_metadata":[]})
    groups=event_groups(raw)
    for number,group in enumerate(groups,1):
        # Initial reset and tail events are already visible in early/late clips;
        # internal events receive their own clip. All remain explicit in ledger.
        covered=next((item["label"] for item in intervals if item["start_s"]<=group["start_s"] and item["end_s"]>=group["end_s"]),None)
        if covered is None:
            intervals.append({"label":f"GAP_RESET_CONTEXT_{number}","start_s":group["start_s"],"end_s":group["end_s"],"selection_reason":"all marked gap/reset events with 2 s pre/post context","action_metadata":[],"event_details":group["events"]})
        else:
            intervals.append({"label":f"GAP_RESET_CONTEXT_{number}","start_s":group["start_s"],"end_s":group["end_s"],"selection_reason":f"marked gap/reset context already contained in {covered}; retained as ledger reference","action_metadata":[],"event_details":group["events"],"rendered_via_interval":covered,"ledger_reference_only":True})
    action_names=[item["action_id"] for item in actions]
    for index,item in enumerate(intervals):
        first,last,start,end=_quantize(raw,item["start_s"],item["end_s"]); item.update({"interval_id":f"C{capture}_I{index+1:02d}","start_frame":first,"end_frame":last,"render_start_s":start,"render_end_s":end})
        mask=(t>=start)&(t<=end); item["motion_energy_rad_s"]={name:float(np.nanmean(energy[name][mask])) for name in ("whole","arms","lower_body")}
        item["validity_gap_state"]={"frames":int(mask.sum()),"frames_with_any_unavailable_node":int(np.sum(~raw["valid"][mask].all(axis=1))),"reset_assertions":int(np.sum(raw["filter_reset"][mask])),"minimum_valid_nodes":int(np.min(np.sum(raw["valid"][mask],axis=1)))}
        if not item["action_metadata"]:
            item["action_metadata"]=sorted({action for action,start_a,end_a in ((x["action_id"],x["start_s"],x["end_s"]) for x in actions) if _overlap((start,end),(start_a,end_a))>0})
    return {"capture":capture,"schema":"biospur.pure_imu.mvp_m1r.interval_selection.v1","selection_policy":"deterministic metadata-constrained raw quaternion angular-motion energy; never pose quality or visual appeal","energy_definition":"RMS of per-frame 2*acos(abs(dot(q_t,q_t-1)))/dt across the named node group, using only valid adjacent raw q_GB samples","action_time_alignment":alignment,"action_count":len(actions),"available_action_ids":sorted(set(action_names)),"intervals":intervals}


def rendered_intervals(ledger: dict) -> list[dict]:
    return [item for item in ledger["intervals"] if not item.get("ledger_reference_only")]
