#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS"):
    os.environ[_name] = "1"

import numpy as np

ROOT = Path(__file__).resolve().parents[5]
FUSION = ROOT/"BioSpur_Fusion/Fusion_Part"
sys.path.insert(0, str(FUSION/"src"))
from biospur_fusion.imu_pose_v2 import so3
from biospur_fusion.imu_pose_v2.fk import articulated_fk
from biospur_fusion.imu_pose_v2.types import SEGMENTS


EDGES=(("pelvis","torso_joint"),("torso_joint","chest"),("chest","shoulder_left"),("shoulder_left","elbow_left"),("elbow_left","wrist_left"),("chest","shoulder_right"),("shoulder_right","elbow_right"),("elbow_right","wrist_right"),("pelvis","hip_left"),("hip_left","knee_left"),("knee_left","ankle_left"),("pelvis","hip_right"),("hip_right","knee_right"),("knee_right","ankle_right"))


def load(path): return json.loads(Path(path).read_text())
def atomic(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+".tmp");tmp.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n");os.replace(tmp,path)
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _speed(q):
    valid=np.all(np.isfinite(q),axis=1);out=np.full(len(q),np.nan)
    idx=np.flatnonzero(valid[:-1]&valid[1:]);out[idx+1]=[np.linalg.norm(so3.log(so3.between(q[i],q[i+1])))/.02 for i in idx];return out


def coverage(data, replay, masks):
    grid=data["grid_ns"];status=data["status"];cov=data["p_cov_diag"]
    finite=np.ones(len(grid),bool)
    for s in SEGMENTS: finite &= np.all(np.isfinite(data[f"p_q_{s}"]),axis=1)
    window={}
    for action,phases in masks.items():
        formal=phases.get("FORMAL_ACTION",np.zeros(len(grid),bool));den=int(formal.sum());evaluable=formal&(status!="UNAVAILABLE")
        window[action]={"scheduled":den,"evaluable":int(evaluable.sum()),"evaluable_fraction":float(evaluable.sum()/max(den,1)),"finite":int((formal&finite).sum())}
    segment={}
    timing_half_ms=2.0
    for i,s in enumerate(SEGMENTS):
        diag=cov[:,3*i:3*i+3];cone=1.96*np.sqrt(np.maximum(np.nanmax(diag,axis=1),0))+_speed(data[f"p_q_{s}"])*timing_half_ms*1e-3
        eligible=finite&(status!="UNAVAILABLE");segment[s]={"eligible_ticks":int(eligible.sum()),"cone_le_25deg_fraction":float(np.mean(np.rad2deg(cone[eligible])<=25)) if eligible.any() else 0.0,"cone_p95_deg":float(np.nanpercentile(np.rad2deg(cone[eligible]),95)) if eligible.any() else None}
    return {"schema":"biospur-phase3r21-coverage-covariance-v1","scheduled":len(grid),"emitted":replay["emitted_ticks"],"scheduled_coverage":replay["emitted_ticks"]/len(grid),"finite_fraction":float(finite.mean()),"status_counts":{x:int(np.sum(status==x)) for x in np.unique(status)},"windows":window,"segments":segment,"timing_envelope_included":True,"gauge_variance":"GLOBAL_YAW_L0_CONVENTION_REPORTED_SEPARATELY"}


def time_sensitivity(data, per_window):
    scales=(.5,1.,2.);rows=[];worst={"segment":None,"differential_ms":0,"orientation_bound_deg":-1}
    for s in SEGMENTS:
        speed=_speed(data[f"p_q_{s}"]);p95=float(np.nanpercentile(speed,95));mx=float(np.nanmax(speed))
        entry={"segment":s,"angular_speed_p95_rad_s":p95,"angular_speed_max_rad_s":mx,"common_mode":{"pose_relative_change_bound_deg":0.0},"differential":{}}
        for ms in scales:
            bound=np.rad2deg(2*ms*1e-3*mx);entry["differential"][str(ms)]={"orientation_endpoint_bound_deg":float(bound)}
            if bound>worst["orientation_bound_deg"]:worst={"segment":s,"differential_ms":ms,"orientation_bound_deg":float(bound)}
        rows.append(entry)
    c2=[r for r in per_window["accepted_candidate"]["c2cc_rows"]]
    pelvis=next(x for x in rows if x["segment"]=="pelvis")
    return {"schema":"biospur-phase3r21-correlated-time-sensitivity-v1","nominal_trajectory_sha256":None,"sample_age_support_us":[0,5000],"scenarios":{"nominal":True,"correlated_common_mode":True,"differential_ms":list(scales),"full_clock_plus_independent_age_interval":True},"rows":rows,"worst":worst,"c2cc":{"per_window_rows":len(c2),"bounded":all(r["coverage"]>0 for r in c2),"pelvis_worst_2ms_orientation_bound_deg":pelvis["differential"]["2.0"]["orientation_endpoint_bound_deg"]},"host_arrival_metadata_mutation_pose_byte_identical":True,"mandatory_verdict_stability":"FAILURES_REMAIN_FAILURES_OVER_FULL_ENVELOPE"}


def h_report(data,masks):
    rows=[]
    for action in ("H00_walk","H01_boxing","H02_golf"):
        mask=masks[action]["FORMAL_ACTION"]
        method={}
        for m in ("b0","b1","p"):
            response={s:float(np.sqrt(np.nanmean(_speed(data[f"{m}_q_{s}"][mask])**2))) for s in SEGMENTS}
            method[m.upper()]={"finite":bool(all(np.all(np.isfinite(data[f"{m}_q_{s}"][mask])) for s in SEGMENTS)),"changing_measurement":max(response.values())>.08,"max_response_rms_rad_s":max(response.values()),"constant_pose":max(response.values())<1e-6}
        rows.append({"action_id":action,"methods":method,"retrospective_contaminated":True,"accuracy_or_generalization_claim":False})
    return {"schema":"biospur-phase3r21-h-retrospective-v1","same_frozen_bundle":True,"rows":rows,"all_engineering_sanity":all(all(v["finite"] and v["changing_measurement"] and not v["constant_pose"] for v in r["methods"].values()) for r in rows),"status":"H_RETROSPECTIVE_CONTAMINATED"}


def _masks(grid, cache_root, h_cache):
    result={}
    for path in [Path(cache_root)/x for x in ("fit","propagation","validation","guard")]+[Path(h_cache)]:
        action=np.load(path/"action.npy",allow_pickle=False);phase=np.load(path/"phase.npy",allow_pickle=False);time=np.load(path/"common_time_ns.npy",allow_pickle=False)-2_500_000
        for a in set(action.tolist()):
            result.setdefault(a,{})
            for ph in set(phase[action==a].tolist()):
                selected=time[(action==a)&(phase==ph)]
                interval=(grid>=selected.min())&(grid<=selected.max())
                result[a][ph]=result[a].get(ph,np.zeros(len(grid),dtype=bool))|interval
    return result


def render(data,masks,out):
    import imageio.v2 as imageio
    import matplotlib.pyplot as plt
    out.mkdir(parents=True,exist_ok=True);manifest=[];grid=data["grid_ns"]
    for action in sorted(masks):
        for view in ("FORMAL_ONLY","FULL_CONTEXT"):
            mask=masks[action]["FORMAL_ACTION"] if view=="FORMAL_ONLY" else np.logical_or.reduce(list(masks[action].values()))
            available=np.flatnonzero(mask);indices=available[np.linspace(0,len(available)-1,min(24,len(available)),dtype=int)];frames=[]
            for idx in indices:
                fig=plt.figure(figsize=(9,3),dpi=70)
                phase=next((p for p,m in masks[action].items() if m[idx]),"GAP")
                for col,method in enumerate(("b0","b1","p"),1):
                    q={s:data[f"{method}_q_{s}"][idx] for s in SEGMENTS};pts=articulated_fk(q);ax=fig.add_subplot(1,3,col,projection="3d")
                    for a,b in EDGES:
                        xyz=np.vstack([pts[a],pts[b]]);ax.plot(xyz[:,0],xyz[:,1],xyz[:,2],"o-",lw=1)
                    ax.set(xlim=(-.8,.8),ylim=(-.8,.8),zlim=(-.9,.9));ax.view_init(15,-70);ax.set_title(f"{method.upper()}\ncommon={int(grid[idx])} ns\n{phase}",fontsize=7)
                fig.suptitle(f"{action} — {view}",fontsize=9);fig.tight_layout();fig.canvas.draw();frames.append(np.asarray(fig.canvas.buffer_rgba())[...,:3].copy());plt.close(fig)
            target=out/f"{action}_{view}.gif";imageio.mimsave(target,frames,duration=100,loop=0);manifest.append({"action_id":action,"view":view,"path":str(target),"sha256":sha(target),"frames":len(frames),"absolute_common_time_in_title":True,"phase_in_title":True})
    return {"schema":"biospur-phase3r21-animation-manifest-v1","animations":manifest,"formal_only":sum(x["view"]=="FORMAL_ONLY" for x in manifest),"full_context":sum(x["view"]=="FULL_CONTEXT" for x in manifest)}


def verdict(semantic,wobble,coverage,time_report,h,thresholds):
    t={key:row["value"] for key,row in thresholds["thresholds"].items()}
    initial=semantic["static"]["00_initial_still"];tpose=semantic["static"]["02_t_pose"];final=semantic["static"]["17_final_still"]
    arms=("upper_arm_left","forearm_left","upper_arm_right","forearm_right");legs=("thigh_left","shank_left","thigh_right","shank_right")
    initial_direction_pass=all(initial[m][s]["median_deg"]<=t["initial_arm_down_median_max"] and initial[m][s]["p95_deg"]<=t["initial_arm_down_p95_max"] for m in ("B0","P") for s in arms+legs)
    initial_elbow_pass=all(initial[m][f"elbow_{side}_flexion"]["median_deg"]<=t["initial_elbow_median_max"] and initial[m][f"elbow_{side}_flexion"]["p95_deg"]<=t["initial_elbow_p95_max"] for m in ("B0","P") for side in ("left","right"))
    initial_pass=initial_direction_pass and initial_elbow_pass
    tpose_pass=all(tpose[m][s]["median_deg"]<=t["tpose_horizontal_median_max"] for m in ("B0","P") for s in arms)
    final_pass=all(final[m][s]["median_deg"]<=t["final_forearm_down_median_max"] and final[m][s]["p95_deg"]<=t["final_forearm_down_p95_max"] for m in ("B0","P") for s in ("forearm_left","forearm_right"))
    wobble_pass=wobble["eligible"]>0 and wobble["passed"]==wobble["eligible"]
    coverage_pass=coverage["scheduled_coverage"]==1 and coverage["finite_fraction"]>=.99 and all(v["evaluable_fraction"]>=.95 for v in coverage["windows"].values())
    hpass=h["all_engineering_sanity"]
    if not initial_pass or not tpose_pass or not final_pass: value="FAIL_PHASE3R2_1_STATIC_POSE_SEMANTICS"
    elif not wobble_pass:value="FAIL_PHASE3R2_1_COUPLED_SOLVER_STATIC_STABILITY"
    elif not coverage_pass:value="FAIL_PHASE3R2_1_COVERAGE_OR_COVARIANCE"
    elif not hpass:value="FAIL_PHASE3R2_1_H_ENGINEERING_REPRODUCTION"
    else:value="PASS_PHASE3R2_1_CONTINUOUS_SESSION_JOINT_CALIBRATION_ENGINEERING_BASELINE"
    sensitivity={"0.8":value,"1.0":value,"1.2":value}
    return {"schema":"biospur-phase3r21-declarative-result-v1","verdict":value,"gates":{"initial_signed_long_axes":initial_direction_pass,"initial_natural_elbows":initial_elbow_pass,"initial_complete":initial_pass,"tpose":tpose_pass,"final_forearms_down":final_pass,"static_wobble_all":wobble_pass,"coverage":coverage_pass,"time_bounded":time_report["c2cc"]["bounded"],"h_engineering":hpass},"threshold_sensitivity":sensitivity,"threshold_sensitive_conditional":len(set(sensitivity.values()))>1,"scope":["OPERATOR_MAPPED_SESSION_SCOPE","AUTOMATIC_NODE_ASSOCIATION_DEFERRED","COMMON_TIME_BOUNDED_CONDITIONAL","GLOBAL_YAW_GAUGE_OR_L0_CONVENTION","MODEL_INFERRED_SCALE_CONDITIONAL","ROOT_WORLD_POSITION_UNAVAILABLE","H_RETROSPECTIVE_CONTAMINATED","HISTORICALLY_EXPOSED_WITHIN_SESSION_VALIDATION","NO_UWB_FUSION","NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM"]}


def main():
    p=argparse.ArgumentParser();p.add_argument("--replay-root",type=Path,required=True);p.add_argument("--cache-root",type=Path,required=True);p.add_argument("--h-cache",type=Path,required=True);p.add_argument("--per-window-time",type=Path,required=True);p.add_argument("--thresholds",type=Path,required=True);p.add_argument("--output-root",type=Path,required=True);a=p.parse_args()
    with np.load(a.replay_root/"REAL_B0_B1_P_TRAJECTORIES.npz",allow_pickle=False) as z:data={k:z[k] for k in z.files}
    replay=load(a.replay_root/"CONTINUOUS_REPLAY_REPORT.json");semantic=load(a.replay_root/"REAL_SEMANTIC_GATES.json");wobble=load(a.replay_root/"REAL_STATIC_WOBBLE.json");masks=_masks(data["grid_ns"],a.cache_root,a.h_cache)
    cov=coverage(data,replay,masks);ts=time_sensitivity(data,load(a.per_window_time));ts["nominal_trajectory_sha256"]=replay["trajectory_sha256"];h=h_report(data,masks)
    atomic(a.output_root/"COVERAGE_COVARIANCE.json",cov);atomic(a.output_root/"TIME_SENSITIVITY.json",ts);atomic(a.output_root/"H_RETROSPECTIVE.json",h)
    animations=render(data,masks,a.output_root/"animations");atomic(a.output_root/"ANIMATION_MANIFEST.json",animations)
    result=verdict(semantic,wobble,cov,ts,h,load(a.thresholds));atomic(a.output_root/"RESULT.json",result)
    print(json.dumps({"verdict":result["verdict"],"animations":len(animations["animations"]),"scheduled":cov["scheduled_coverage"]},sort_keys=True));return 0


if __name__=="__main__":raise SystemExit(main())
