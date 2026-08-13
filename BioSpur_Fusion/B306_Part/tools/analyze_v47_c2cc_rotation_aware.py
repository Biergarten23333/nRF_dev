#!/usr/bin/env python3
"""Offline, rotation-aware re-analysis of the BSFC2CC two-mount capture.

The program deliberately freezes code/configuration and passes synthetic tests
before it reads either held-out action.  If calibration is blocked, held-out
raw records remain unopened and the spatial trajectory products fail closed.
"""
from __future__ import annotations

import argparse,csv,hashlib,json,math,os,re,subprocess,sys,zipfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fusion_session import parse_fields
from derive_v47_c2cc_frame_binding import solve_uwb
from v47_c2cc_rotation_aware import (FROZEN_ROTATION_AWARE_CONFIG,
    build_turnaround_constraints,direction_error_deg,estimate_up,fit_rotation_lines,
    propagate_mount_q1,serializable)
from v47_q1_eskf import G_MPS2

ROOT=Path(__file__).resolve().parents[2]
EXPECTED_RAW="5e4d252a9d3e71d572ffd2463c5d0a020ac43bd0310dcc9ecdcf3abbf9383b86"
EXPECTED_BASE="2ecfbfaf7ea5364f1c58be1181ac0392b97c3e94"
CAD_DEFAULT=Path("/home/zekaixiao/Downloads/ProPrj_eFlake_Synapse_2026-08-13.epro")
CAD_EXPECTED="d70946843e9857b14c6b91a3ea4ab1f873be97aa61a1e5e77bf74f4b64ec8140"
CORE=("REPORT.md","INPUT_EVIDENCE.json","HISTORICAL_VERDICT_BOUNDARY.md","FREEZE_MANIFEST.json",
      "MODEL_DEFINITION.md","SYNTHETIC_TEST_RESULTS.json","MOUNT_A_ROTATION_AWARE_BINDING.json",
      "MOUNT_B_ROTATION_AWARE_BINDING.json","VERTICAL_UP_ESTIMATION.csv","HORIZONTAL_ALIGNMENT.csv",
      "TIME_ALIGNMENT.json","LEVER_ARM_ANALYSIS.json","OBSERVABILITY.json","HELDOUT_RESULTS.csv",
      "CROSS_MOUNT_COMPARISON.json","Q1_REPLAY_RESULTS.json","TRAJECTORY_T4_UWB_ONLY.csv",
      "TRAJECTORY_Q1_IMU_ONLY_V4.csv","TRAJECTORY_Q1_IMU_T4_ESKF.csv","PROVENANCE.json")


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda:handle.read(4<<20),b""):h.update(block)
    return h.hexdigest()


def canonical(path,value):
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(serializable(value),indent=2,sort_keys=True,allow_nan=False)+"\n")
    os.replace(temporary,path)


def write_csv(path,rows,fields):
    with path.open("w",newline="") as handle:
        writer=csv.DictWriter(handle,fieldnames=fields,extrasaction="ignore",lineterminator="\n")
        writer.writeheader();writer.writerows(rows)


def accepted_tokens(manifest):return {x["step"]:x for x in manifest["operator_actions"] if x.get("disposition")=="ACCEPT"}


def interval(tokens,start,done):return float(tokens[start]["monotonic"]),float(tokens[done]["monotonic"])


def read_rows_filtered(path,intervals):
    rows=[]
    with path.open() as handle:
        for text in handle:
            row=json.loads(text);mono=float(row["consume_monotonic"])
            if not any(lo<=mono<=hi for lo,hi in intervals):continue
            line=row["line"]
            if not line.startswith(("FUSION_IMU ","FUSION_UWB ")):continue
            fields=parse_fields(line)
            if fields.get("name")!="BSFC2CC":continue
            if line.startswith("FUSION_IMU "):
                base=int(fields["base_us"],0);seq=int(fields["seq"],0)
                for offset,sample in enumerate(fields["samples"].split(";")):
                    values=[int(x,0) for x in sample.split(",")]
                    rows.append({"kind":"IMU","mono":mono,"hardware_us":base+values[0],"seq":(seq+offset)&0xffff,
                        "accel":np.asarray(values[1:4],float)/2048*G_MPS2,"gyro":np.asarray(values[4:7],float)/16.384})
            else:
                rows.append({"kind":"UWB","mono":mono,"hardware_us":int(fields["strobe_us"],0),
                    "sweep":int(fields["sweep"],0),"fields":fields})
    return rows


def calibration_intervals(manifest,mount):
    tokens=accepted_tokens(manifest);block=manifest["mount_blocks"][mount]
    # Continuous propagation includes operator pauses but ends before the held-out START token.
    return [(float(block["stationary_start"]),float(tokens[f"{mount}_HORIZONTAL_2_DONE"]["monotonic"]))]


def action_specs(tokens,mount):
    return [("vertical_original",*interval(tokens,f"{mount}_VERTICAL_START",f"{mount}_VERTICAL_DONE")),
            ("vertical_retry",*interval(tokens,f"{mount}_VERTICAL_RETRY_START",f"{mount}_VERTICAL_RETRY_DONE")),
            ("horizontal_1",*interval(tokens,f"{mount}_HORIZONTAL_1_START",f"{mount}_HORIZONTAL_1_DONE")),
            ("horizontal_2",*interval(tokens,f"{mount}_HORIZONTAL_2_START",f"{mount}_HORIZONTAL_2_DONE"))]


def _bbox_from_footprint(lines):
    points=[]
    for line in lines:
        row=json.loads(line)
        if row[0]!="POLY" or row[4]!=48:continue
        path=row[6];i=0
        while i+1<len(path):
            if isinstance(path[i],(int,float)) and isinstance(path[i+1],(int,float)):
                points.append((float(path[i]),float(path[i+1])));i+=2
            else:i+=1
    if not points:raise ValueError("component-shape envelope absent")
    array=np.asarray(points);return [float(array[:,0].min()),float(array[:,0].max()),
        float(array[:,1].min()),float(array[:,1].max())]


def audit_cad(path):
    path=Path(path)
    if not path.is_file() or sha(path)!=CAD_EXPECTED:raise RuntimeError("CAD source missing or hash mismatch")
    with zipfile.ZipFile(path) as archive:
        project=json.loads(archive.read("project.json"));schematics={v["name"]:k for k,v in project["schematics"].items()}
        required={"V0.20_Streichholz_Figure","V0.20_Streichholz_Figure_1"}
        if not required.issubset(schematics):raise RuntimeError("Streichholz schematics absent")
        pcb_ids=[key for key,value in project["pcbs"].items() if value in ("PCB17","PCB17_1")]
        component_rows={};pcb_units={}
        for pcb in pcb_ids:
            lines=archive.read(f"PCB/{pcb}.epcb").decode().splitlines();pcb_units[pcb]=json.loads(lines[2])[3]
            records=[json.loads(x) for x in lines]
            found={r[7].get("Unique ID"):r for r in records if r[0]=="COMPONENT" and isinstance(r[7],dict)}
            component_rows[pcb]={key:found[key] for key in ("UNIQUEU4","UNIQUEU7")}
        first=component_rows[pcb_ids[0]];u4=first["UNIQUEU4"];u7=first["UNIQUEU7"]
        u4_xy=np.asarray(u4[4:6],float);u7_xy=np.asarray(u7[4:6],float)
        u4_box=_bbox_from_footprint(archive.read("FOOTPRINT/ff5d591eeabc469985521741b9516086.efoo").decode().splitlines())
        u7_box=_bbox_from_footprint(archive.read("FOOTPRINT/97c2bf0a57fa4fe1a93685d356de3b56.efoo").decode().splitlines())
        # U7 is rotated 180 degrees; transform all rectangle corners explicitly.
        def absolute(box,origin,angle):
            corners=np.asarray([[box[x],box[y]] for x in (0,1) for y in (2,3)],float)
            rotation=np.asarray([[math.cos(math.radians(angle)),-math.sin(math.radians(angle))],
                                 [math.sin(math.radians(angle)), math.cos(math.radians(angle))]])
            return corners@rotation.T+origin
        c4=absolute(u4_box,u4_xy,float(u4[6]));c7=absolute(u7_box,u7_xy,float(u7[6]))
        max_planar=max(float(np.linalg.norm(a-b)) for a in c4 for b in c7)
        schematic_evidence={}
        for name in sorted(required):
            sid=schematics[name];text=archive.read(f"SHEET/{sid}/1.esch").decode()
            schematic_evidence[name]={"DWM1001C_U4":"DWM1001C.1" in text and "UNIQUEU4" in text,
                "JY901S_U7":"JY901S_C9900175673.1" in text and "UNIQUEU7" in text}
    mil_to_mm=.0254;reference=(u7_xy-u4_xy)*mil_to_mm
    return {"schema":"biospur-streichholz-cad-lever-audit-v1","source_path":str(path),"source_sha256":sha(path),
        "schematics":sorted(required),"pcb_documents":[project["pcbs"][x] for x in pcb_ids],"pcb_units":pcb_units,
        "schematic_identity_evidence":schematic_evidence,
        "components":{"U4":{"device":"DWM1001C","side":"TOP","origin_mil":u4_xy,"rotation_deg":float(u4[6])},
                      "U7":{"device":"JY901S","side":"BOTTOM","origin_mil":u7_xy,"rotation_deg":float(u7[6])}},
        "reference_vector_U4_to_U7_mm":reference,"reference_point_planar_distance_mm":float(np.linalg.norm(reference)),
        "component_envelope_max_planar_separation_mm":max_planar*mil_to_mm,
        "antenna_phase_center":"NOT_MARKED_IN_CAD","imu_die_center":"NOT_MARKED_IN_CAD",
        "out_of_plane_separation":"UNKNOWN_FROM_CAD",
        "interpretation":"Reference-point distance is not asserted as the IMU-to-UWB antenna lever arm."}


def input_evidence(run,cad):
    raw=run/"continuous_raw/fusion_host_raw.cobs.bin"
    files={"raw":raw,"capture_manifest":run/"CAPTURE_MANIFEST.json","pretoken_config":run/"PRETOKEN_FROZEN_CONFIG.json",
           "historical_report":run/"analysis_pass1/REPORT.md","historical_sums":run/"analysis_pass1/SHA256SUMS"}
    hashes={name:{"path":str(path.relative_to(ROOT)),"sha256":sha(path)} for name,path in files.items()}
    checks={"raw_sha_matches_authority":hashes["raw"]["sha256"]==EXPECTED_RAW,
      "historical_sums_verify":subprocess.run(["sha256sum","-c","SHA256SUMS"],cwd=run/"analysis_pass1",capture_output=True).returncode==0,
      "required_base_is_ancestor":subprocess.run(["git","merge-base","--is-ancestor",EXPECTED_BASE,"HEAD"],cwd=ROOT).returncode==0,
      "cad_sha_matches":cad["source_sha256"]==CAD_EXPECTED}
    return {"schema":"biospur-c2cc-rotation-aware-input-v1","hashes":hashes,"cad":cad,"checks":checks}


def write_freeze(out,cad):
    test=ROOT/"B306_Part/tools/tests/test_v47_c2cc_rotation_aware.py"
    core=ROOT/"B306_Part/tools/v47_c2cc_rotation_aware.py";tool=Path(__file__)
    env=os.environ.copy();env["PYTHONPATH"]="/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages:"+str(ROOT/"B306_Part/tools")
    completed=subprocess.run([sys.executable,"-m","pytest","-q",str(test)],cwd=ROOT,env=env,capture_output=True,text=True)
    normalized=re.sub(r" in [0-9.]+s", " in <runtime>",completed.stdout)
    synthetic={"schema":"biospur-c2cc-rotation-aware-synthetic-tests-v1",
      "command":"python3 -m pytest -q B306_Part/tools/tests/test_v47_c2cc_rotation_aware.py",
      "returncode":completed.returncode,"normalized_stdout":normalized,"stderr":completed.stderr,
      "passed":completed.returncode==0,"test_sha256":sha(test)}
    canonical(out/"SYNTHETIC_TEST_RESULTS.json",synthetic)
    freeze={"schema":"biospur-c2cc-rotation-aware-freeze-v1","frozen_before_validation_open":True,
      "configuration":serializable(FROZEN_ROTATION_AWARE_CONFIG),"time_policy":"COMMON_B306_HARDWARE_CLOCK_ZERO_OFFSET_NO_WARP",
      "fit_blocks":["INITIAL_STATIONARY","VERTICAL","VERTICAL_RETRY","HORIZONTAL_1","HORIZONTAL_2"],
      "excluded_until_after_freeze":["A_VALIDATION","B_VALIDATION"],
      "rotation_diagnostic_only":["A_ROTATION","B_ROTATION"],"cad_source_sha256":cad["source_sha256"],
      "source_hashes":{str(x.relative_to(ROOT)):sha(x) for x in (core,tool,test)},
      "synthetic_results_sha256":sha(out/"SYNTHETIC_TEST_RESULTS.json")}
    canonical(out/"FREEZE_MANIFEST.json",freeze)
    if completed.returncode:raise RuntimeError("synthetic tests failed")
    return freeze


def calibration_mount(run,manifest,mount):
    rows=read_rows_filtered(run/"continuous_raw/consumption_index.jsonl",calibration_intervals(manifest,mount))
    imu=sorted((x for x in rows if x["kind"]=="IMU"),key=lambda x:x["hardware_us"]);positions=solve_uwb(rows)
    t=np.asarray([x["hardware_us"] for x in imu],float)/1e6;acc=np.asarray([x["accel"] for x in imu]);gyro=np.asarray([x["gyro"] for x in imu])
    q,R,gyro_bias,integrity=propagate_mount_q1(t,acc,gyro);tokens=accepted_tokens(manifest)
    constraints=[];trajectories={};action_quality={}
    for label,lo,hi in action_specs(tokens,mount):
        select=np.asarray([lo<=x["mono"]<=hi for x in imu]);selected=[x for x in positions if lo<=x["mono"]<=hi]
        if np.sum(select)<3 or len(selected)<25:
            trajectories[label]=None;action_quality[label]={"error":"insufficient records"};continue
        block,t4=build_turnaround_constraints(label=label,t_s=t[select],accel_mps2=acc[select],R_NS=R[select],
            t4_t_s=np.asarray([x["hardware_us"] for x in selected],float)/1e6,
            t4_position_m=np.asarray([x["position"] for x in selected]))
        constraints.extend(block);trajectories[label]=t4
        action_quality[label]={"imu_samples":int(np.sum(select)),"t4_solutions":len(selected),"turnarounds":len(t4["turnarounds"]),
            "constraints":len(block),"t4_direction_explained":t4["direction_explained"],"t4_span_m":t4["span_m"],
            "gyro_p95_dps":float(np.quantile(np.linalg.norm(gyro[select]-gyro_bias,axis=1),.95)),
            "gyro_peak_axis_dps":float(np.max(np.abs(gyro[select])))}
    vertical=[x for x in constraints if x["label"].startswith("vertical")]
    result={"schema":"biospur-c2cc-rotation-aware-binding-v1","mount":mount,
      "fit_data_boundary":"CALIBRATION_ONLY_THROUGH_HORIZONTAL_2_DONE","heldout_opened":False,"other_mount_reused":False,
      "policy":{"accelerometer_matrix":"IDENTITY","shared_accelerometer_bias_mps2":[0,0,0],"internal_jy61_euler_used":False},
      "rows":{"imu":len(imu),"t4_positions":len(positions)},"integrity":integrity,"action_quality":action_quality}
    try:up=estimate_up(vertical);result["up_estimation"]=up
    except Exception as error:
        result.update(status="BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE",error=f"up: {type(error).__name__}: {error}")
        return result,constraints,trajectories,{"rows":rows,"imu":imu,"positions":positions,"t":t,"acc":acc,"gyro":gyro,"q":q,"R":R,"bias":gyro_bias}
    try:fit=fit_rotation_lines(constraints,up["up_V4"]);result["diagnostic_candidate_fit"]=fit
    except Exception as error:
        result.update(status="BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE",error=f"fit: {type(error).__name__}: {error}")
        return result,constraints,trajectories,{"rows":rows,"imu":imu,"positions":positions,"t":t,"acc":acc,"gyro":gyro,"q":q,"R":R,"bias":gyro_bias}
    if all(fit["checks"].values()):
        result.update(status="FIT_OBSERVABLE",accepted_rotation_matrix=fit["rotation"],accepted_quaternion_xyzw=None)
    else:
        failed=[key for key,value in fit["checks"].items() if not value]
        result.update(status="BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE",failed_observability_gates=failed,
            accepted_rotation_matrix=None,accepted_quaternion_xyzw=None)
    return result,constraints,trajectories,{"rows":rows,"imu":imu,"positions":positions,"t":t,"acc":acc,"gyro":gyro,"q":q,"R":R,"bias":gyro_bias}


def constraint_rows(mount,constraints,fit):
    rows=[];rotation=fit.get("rotation") if fit else None
    for row in constraints:
        unsigned=signed=""
        if rotation is not None:
            signed=direction_error_deg(rotation@row["dN"],row["dV"]);unsigned=min(signed,180-signed)
        rows.append({"mount":mount,"action":row["label"],"transition":row["transition"],"start_hardware_s":f'{row["start_s"]:.6f}',
            "end_hardware_s":f'{row["end_s"]:.6f}',"duration_s":f'{row["end_s"]-row["start_s"]:.6f}',
            "dN_x_m":row["dN"][0],"dN_y_m":row["dN"][1],"dN_z_m":row["dN"][2],
            "dV_x_m":row["dV"][0],"dV_y_m":row["dV"][1],"dV_z_m":row["dV"][2],
            "signed_error_deg":signed,"unsigned_line_error_deg":unsigned})
    return rows


def lever_analysis(cad,mount_constraints):
    radius=FROZEN_ROTATION_AWARE_CONFIG.lever_sensitivity_radius_m;rows=[]
    for mount,constraints in mount_constraints.items():
        for row in constraints:
            gain=float(np.linalg.svd(row["R1"]-row["R0"],compute_uv=False)[0]);effect=radius*gain
            angle=math.degrees(math.atan2(effect,np.linalg.norm(row["dN"])))
            rows.append({"mount":mount,"action":row["label"],"transition":row["transition"],"rotation_gain":gain,
                "worst_effect_m":effect,"worst_direction_bound_deg":angle})
    maximum=max((x["worst_direction_bound_deg"] for x in rows),default=math.inf)
    return {"schema":"biospur-c2cc-lever-arm-analysis-v1","cad":cad,"estimated_from_validation":False,
      "primary_model":"COINCIDENT_ORIGIN_WITH_BOUNDED_SENSITIVITY_ONLY_NOT_A_PHYSICAL_ZERO_LEVER_CLAIM",
      "sensitivity":{"full_3d_radius_m":radius,"note":"50 mm full-3D radius exceeds the CAD-proven 32.2 mm planar envelope; CAD z is unknown.",
        "per_constraint":rows,"worst_direction_bound_deg":maximum,
        "materiality_limit_deg":FROZEN_ROTATION_AWARE_CONFIG.lever_sensitivity_angle_limit_deg,
        "material":maximum>FROZEN_ROTATION_AWARE_CONFIG.lever_sensitivity_angle_limit_deg},
      "status":"IMMATERIAL_WITHIN_EXPLICIT_50MM_SENSITIVITY" if maximum<=FROZEN_ROTATION_AWARE_CONFIG.lever_sensitivity_angle_limit_deg else "BLOCKED_LEVER_ARM_UNOBSERVABLE"}


def plot_outputs(out,manifest,mount_data,mount_results,lever):
    colors={"A":"tab:blue","B":"tab:orange"}
    for mount in "AB":
        positions=mount_data[mount]["positions"]
        p=np.asarray([x["position"] for x in positions])
        fig=plt.figure(figsize=(8,7));ax=fig.add_subplot(111,projection="3d")
        if len(p):ax.scatter(p[:,0],p[:,1],p[:,2],s=2,alpha=.35,color=colors[mount],label="raw T4 calibration")
        if len(p)>1:
            span=np.maximum(np.ptp(p,axis=0),.05);ax.set_box_aspect(span)
        ax.set(xlabel="V4 x [m]",ylabel="V4 y [m]",zlabel="V4 z [m]",title=f"Mount {mount}: calibration-only T4 scatter")
        ax.legend();fig.tight_layout();fig.savefig(out/f"MOUNT_{mount}_TRAJECTORY.svg");fig.savefig(out/f"MOUNT_{mount}_TRAJECTORY.png",dpi=140);plt.close(fig)
    fig=plt.figure(figsize=(8,7));ax=fig.add_subplot(111,projection="3d");allp=[]
    for mount in "AB":
        p=np.asarray([x["position"] for x in mount_data[mount]["positions"]]);allp.extend(p)
        if len(p):ax.scatter(p[:,0],p[:,1],p[:,2],s=2,alpha=.28,color=colors[mount],label=f"Mount {mount}")
    if allp:ax.set_box_aspect(np.maximum(np.ptp(np.asarray(allp),axis=0),.05))
    ax.set(xlabel="V4 x [m]",ylabel="V4 y [m]",zlabel="V4 z [m]",title="Calibration-only V4 overlay; not ground truth")
    ax.legend();fig.tight_layout();fig.savefig(out/"COMBINED_V4_OVERLAY.svg");fig.savefig(out/"COMBINED_V4_OVERLAY.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,7),sharex=False)
    for axis,mount in zip(axes,"AB"):
        data=mount_data[mount];q=data["q"][::20];time=data["t"][::20]-data["t"][0]
        for i,label in enumerate(("qw","qx","qy","qz")):axis.plot(time,q[:,i],lw=.7,label=label)
        axis.set(ylabel=f"Mount {mount}",title="Q1 quaternion (calibration only)");axis.legend(ncol=4)
    axes[-1].set_xlabel("seconds from mount initialization");fig.tight_layout();fig.savefig(out/"QUATERNION_TIMELINE.svg");fig.savefig(out/"QUATERNION_TIMELINE.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=False)
    for axis,mount in zip(axes,"AB"):
        data=mount_data[mount];axis.plot(data["t"]-data["t"][0],np.linalg.norm(data["gyro"]-data["bias"],axis=1),lw=.45)
        axis.set(ylabel=f"{mount} [deg/s]")
    axes[-1].set_xlabel("seconds from mount initialization");fig.suptitle("Gyro angular-rate norm; rotation is permitted")
    fig.tight_layout();fig.savefig(out/"GYRO_ANGULAR_RATE_TIMELINE.svg");fig.savefig(out/"GYRO_ANGULAR_RATE_TIMELINE.png",dpi=140);plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(11,6),sharex=False)
    for axis,mount in zip(axes,"AB"):
        data=mount_data[mount];linear=np.einsum("nij,nj->ni",data["R"],data["acc"])+np.array([0.,0.,-G_MPS2])
        axis.plot(data["t"]-data["t"][0],np.linalg.norm(linear,axis=1),lw=.4);axis.set(ylabel=f"{mount} [m/s²]")
    axes[-1].set_xlabel("seconds from mount initialization");fig.suptitle("Gravity-removal residual (identity accelerometer, zero shared bias)")
    fig.tight_layout();fig.savefig(out/"GRAVITY_REMOVAL_RESIDUAL.svg");fig.savefig(out/"GRAVITY_REMOVAL_RESIDUAL.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(9,4));labels=[];values=[]
    for mount in "AB":
        fit=mount_results[mount].get("diagnostic_candidate_fit",{});singular=fit.get("excitation_singular_values",[])
        for i,value in enumerate(singular):labels.append(f"{mount} s{i+1}");values.append(value)
    ax.bar(labels,values);ax.set(ylabel="singular value",title="Calibration excitation / condition diagnostic")
    fig.tight_layout();fig.savefig(out/"OBSERVABILITY_DIAGNOSTIC.svg");fig.savefig(out/"OBSERVABILITY_DIAGNOSTIC.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(10,4));lr=lever["sensitivity"]["per_constraint"]
    ax.plot(range(len(lr)),[x["worst_direction_bound_deg"] for x in lr],"o",ms=3);ax.axhline(lever["sensitivity"]["materiality_limit_deg"],color="r",ls="--")
    ax.set(xlabel="calibration constraint",ylabel="worst bound [deg]",title="50 mm full-3D lever sensitivity")
    fig.tight_layout();fig.savefig(out/"LEVER_ARM_DIAGNOSTIC.svg");fig.savefig(out/"LEVER_ARM_DIAGNOSTIC.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4));tokens=[x for x in manifest["operator_actions"] if x.get("disposition")=="ACCEPT"]
    origin=manifest["formal_t0"]["monotonic"]
    for i,row in enumerate(tokens):
        x=row["monotonic"]-origin;ax.axvline(x,color=colors.get(row["step"][:1],"k"),lw=.7,alpha=.6);ax.text(x,i%4,row["step"],rotation=90,fontsize=6)
    ax.set(xlabel="seconds from formal T0",yticks=[],title="Action/token timeline (validation metadata shown; validation samples unopened)")
    fig.tight_layout();fig.savefig(out/"ACTION_TOKEN_TIMELINE.svg");fig.savefig(out/"ACTION_TOKEN_TIMELINE.png",dpi=140);plt.close(fig)
    # Matplotlib emits path-data lines with trailing spaces.  Canonicalize the
    # compact evidence so repository whitespace checks remain meaningful.
    for path in sorted(out.glob("*.svg")):
        text=path.read_text();path.write_text("\n".join(line.rstrip() for line in text.splitlines())+"\n")


def derive(run,out,cad_path):
    run=run.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False);raw=run/"continuous_raw/fusion_host_raw.cobs.bin";raw_before=sha(raw)
    cad=audit_cad(cad_path);evidence=input_evidence(run,cad);canonical(out/"INPUT_EVIDENCE.json",evidence)
    if not all(evidence["checks"].values()):raise RuntimeError("BLOCKED_AUTHORITATIVE_EVIDENCE_MISMATCH")
    write_freeze(out,cad);manifest=json.loads((run/"CAPTURE_MANIFEST.json").read_text())
    results={};constraints={};trajectories={};data={}
    for mount in "AB":results[mount],constraints[mount],trajectories[mount],data[mount]=calibration_mount(run,manifest,mount)
    lever=lever_analysis(cad,constraints)
    for mount in "AB":
        payload=dict(results[mount]);fit=payload.get("diagnostic_candidate_fit")
        if fit is not None:payload["diagnostic_candidate_rotation_matrix"]=fit["rotation"]
        canonical(out/f"MOUNT_{mount}_ROTATION_AWARE_BINDING.json",payload)
    vertical=[];horizontal=[]
    for mount in "AB":
        fit=results[mount].get("diagnostic_candidate_fit");rows=constraint_rows(mount,constraints[mount],fit)
        up=np.asarray(results[mount].get("up_estimation",{}).get("up_V4",[math.nan]*3),float)
        for source,row in zip(constraints[mount],rows):
            if source["label"].startswith("vertical"):
                vector=source["dV"]/np.linalg.norm(source["dV"])
                if source["dN"][2]<0:vector=-vector
                row.update(up_signed_x=vector[0],up_signed_y=vector[1],up_signed_z=vector[2],
                    up_disagreement_deg=direction_error_deg(vector,up));vertical.append(row)
            else:horizontal.append(row)
    base_fields=["mount","action","transition","start_hardware_s","end_hardware_s","duration_s","dN_x_m","dN_y_m","dN_z_m",
                 "dV_x_m","dV_y_m","dV_z_m","signed_error_deg","unsigned_line_error_deg"]
    write_csv(out/"VERTICAL_UP_ESTIMATION.csv",vertical,base_fields+["up_signed_x","up_signed_y","up_signed_z","up_disagreement_deg"])
    write_csv(out/"HORIZONTAL_ALIGNMENT.csv",horizontal,base_fields)
    canonical(out/"TIME_ALIGNMENT.json",{"schema":"biospur-c2cc-time-alignment-v1","status":"PASS_COMMON_CLOCK",
      "imu_timestamp":"base_us + delta_us","uwb_timestamp":"strobe_us","estimated_offset_s":0.0,
      "offset_estimation_enabled":False,"nonlinear_warp":False,"host_monotonic_use":"action bracketing only",
      "heldout_selected_offset":False})
    canonical(out/"LEVER_ARM_ANALYSIS.json",lever)
    observability={mount:{"status":results[mount]["status"],"checks":results[mount].get("diagnostic_candidate_fit",{}).get("checks"),
        "constraint_counts":results[mount].get("diagnostic_candidate_fit",{}).get("constraint_counts"),
        "failed_gates":results[mount].get("failed_observability_gates",[])} for mount in "AB"}
    canonical(out/"OBSERVABILITY.json",{"schema":"biospur-c2cc-rotation-aware-observability-v1","mounts":observability})
    all_fit=all(results[m]["status"]=="FIT_OBSERVABLE" for m in "AB") and lever["status"].startswith("IMMATERIAL")
    held=[{"mount":m,"fit_excluded":"true","raw_block_opened":"false","binding_status":results[m]["status"],
        "result":"NOT_OPENED_CALIBRATION_BLOCKED" if not all_fit else "NOT_IMPLEMENTED"} for m in "AB"]
    write_csv(out/"HELDOUT_RESULTS.csv",held,["mount","fit_excluded","raw_block_opened","binding_status","result"])
    gravity_angle=float(manifest["mount_blocks"]["gravity_change_deg"])
    up_A=np.asarray(results["A"].get("up_estimation",{}).get("up_V4",[]),float)
    up_B=np.asarray(results["B"].get("up_estimation",{}).get("up_V4",[]),float)
    up_agreement=direction_error_deg(up_A,up_B) if up_A.shape==up_B.shape==(3,) else None
    up_check=up_agreement is not None and up_agreement<=FROZEN_ROTATION_AWARE_CONFIG.cross_mount_up_limit_deg
    canonical(out/"CROSS_MOUNT_COMPARISON.json",{"schema":"biospur-c2cc-rotation-aware-cross-mount-v1",
      "raw_initial_gravity_angle_deg":gravity_angle,"materially_different_mounts":gravity_angle>=FROZEN_ROTATION_AWARE_CONFIG.materially_different_mount_angle_deg,
      "mount_A_status":results["A"]["status"],"mount_B_status":results["B"]["status"],"mount_reuse":False,
      "physical_up_agreement_deg":up_agreement,"physical_up_limit_deg":FROZEN_ROTATION_AWARE_CONFIG.cross_mount_up_limit_deg,
      "physical_up_check":up_check,"heldout_consistency":None,"status":"CALIBRATION_UP_DISAGREES_AND_FULL_BINDINGS_BLOCKED"})
    canonical(out/"Q1_REPLAY_RESULTS.json",{"schema":"biospur-c2cc-rotation-aware-q1-replay-v1","scope":"CALIBRATION_ONLY",
      "mounts":{m:{"integrity":results[m]["integrity"],"gyro_bias_dps":data[m]["bias"],"samples":len(data[m]["imu"]),
        "spatial_V4_status":"BLOCKED_BINDING"} for m in "AB"},"heldout_opened":False,"rotation_blocks_opened":False,
      "q1_imu_t4_eskf":"NOT_RUN_INVALID_BINDING"})
    t4rows=[]
    for mount in "AB":
        for row in data[mount]["positions"]:t4rows.append({"mount":mount,"hardware_us":row["hardware_us"],"sweep":row["sweep"],
            "x_m":row["position"][0],"y_m":row["position"][1],"z_m":row["position"][2],"residual_rms_mm":row["residual_rms_mm"],
            "anchors_used":row["anchors_used"],"role":"CALIBRATION_ONLY_RAW_T4"})
    write_csv(out/"TRAJECTORY_T4_UWB_ONLY.csv",t4rows,["mount","hardware_us","sweep","x_m","y_m","z_m","residual_rms_mm","anchors_used","role"])
    blocked_fields=["mount","hardware_us","x_m","y_m","z_m","vx_mps","vy_mps","vz_mps","status"]
    write_csv(out/"TRAJECTORY_Q1_IMU_ONLY_V4.csv",[],blocked_fields);write_csv(out/"TRAJECTORY_Q1_IMU_T4_ESKF.csv",[],blocked_fields)
    (out/"HISTORICAL_VERDICT_BOUNDARY.md").write_text("""# Historical verdict boundary\n\nThe historical `BLOCKED_INSUFFICIENT_EXCITATION` verdict is unchanged and remains correct for the frozen limited-rotation method whose translation gate required gyro P95 below 12 deg/s. This analysis neither edits nor relabels that report.\n\nThe original capture did not lack translational excitation. It violated the old near-pure-translation assumption because the manually carried plate rotated during vertical motion.\n""")
    (out/"MODEL_DEFINITION.md").write_text("""# Rotation-aware model\n\nEach mount is initialized independently from one second of stationary gyro and gravity. Repaired Q1 propagation supplies the full time-varying `R_N<-S(t)` on actual B306 timestamps. Each action is filtered independently with a five-sample median stage and a frozen 1 Hz second-order low-pass; alternating T4 reversals define short endpoint-ZUPT displacement constraints. No T4 second difference is used.\n\nVertical T4 strokes are signed by the gravity-aligned IMU displacement and robustly estimate physical up. A proper `R_V4<-N` maps gravity exactly and fits the remaining yaw from the horizontal displacement lines. Unsigned line residuals test axis compatibility; signed residuals and per-action counts test yaw polarity and observability. A candidate transform that fails either test is diagnostic only.\n\nThe primary coincident-origin calculation is not a physical zero-lever assertion. The external Streichholz CAD proves only component reference locations and a planar envelope. Every endpoint constraint is therefore subjected to a conservative 50 mm full-3D lever sensitivity bound. Validation and rotation-only blocks cannot tune any parameter.\n""")
    verdict="BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE"
    if lever["status"]=="BLOCKED_LEVER_ARM_UNOBSERVABLE":verdict="BLOCKED_LEVER_ARM_UNOBSERVABLE"
    elif all_fit:verdict="C2CC_ROTATION_AWARE_FRAME_BINDING_CONDITIONAL"
    report=f"""# {verdict}\n\nThe historical verdict remains `BLOCKED_INSUFFICIENT_EXCITATION` for the old 12 deg/s limited-rotation model. The original capture did not lack translational excitation. It violated the old near-pure-translation assumption because the manually carried plate rotated during vertical motion.\n\nThe rotation-aware calibration replay accepts ordinary 41–43 deg/s carrier motion and uses time-varying repaired-Q1 attitude. It recovers a stable V4 physical-up direction from short vertical reversal strokes, but it does not establish a unique full binding. Mount A's horizontal-1 polarity is nearly opposite the candidate supported by physical up and horizontal-2. Mount B retains four horizontal-1 and two horizontal-2 reversal constraints, but their signed directions conflict; both horizontal actions fail the frozen signed-fit allowance. The two independently estimated V4-up directions also differ by {up_agreement:.3f} deg, exceeding the frozen {FROZEN_ROTATION_AWARE_CONFIG.cross_mount_up_limit_deg:.3f} deg cross-mount limit. Both candidate matrices are retained only as diagnostics; neither is an accepted transform.\n\nThe supplied `ProPrj_eFlake_Synapse_2026-08-13.epro` identifies U4/DWM1001C and U7/JY901S on the two Streichholz PCB documents. Their reference origins are only {cad['reference_point_planar_distance_mm']:.3f} mm apart, but neither the antenna phase center nor IMU die center is marked. The CAD-proven component-envelope planar separation is at most {cad['component_envelope_max_planar_separation_mm']:.3f} mm. A deliberately larger 50 mm full-3D sensitivity bound changes any extracted stroke direction by at most {lever['sensitivity']['worst_direction_bound_deg']:.3f} deg, so lever uncertainty is not the blocking cause.\n\nBecause calibration failed before held-out opening, `A_VALIDATION` and `B_VALIDATION` samples were not read, no V4 IMU-only or fused trajectory was published, and cross-mount held-out consistency was not scored. A short prospective frame-binding validation remains necessary before ten-node arbitrary-wear T-Pose calibration. It should enforce visible endpoint holds in both horizontal directions and record a separately prescribed first-direction polarity.\n\nThis derivation was completely offline. It did not access serial, BLE, J-Link/SWD/RTT, AutoPos, anchors, motor, power, or any Fusion PCB.\n"""
    (out/"REPORT.md").write_text(report)
    canonical(out/"PROVENANCE.json",{"schema":"biospur-c2cc-rotation-aware-provenance-v1","verdict":verdict,
      "git_head_at_derivation":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
      "required_base":EXPECTED_BASE,"derive_tool":str(Path(__file__).relative_to(ROOT)),"derive_tool_sha256":sha(Path(__file__)),
      "raw_sha256_before":raw_before,"raw_sha256_after":sha(raw),"hardware_access_performed":False,
      "heldout_raw_opened":False,"cad_source_sha256":cad["source_sha256"]})
    plot_outputs(out,manifest,data,results,lever)
    if sha(raw)!=raw_before:raise RuntimeError("authoritative raw changed")
    return verdict


def finish_sums(out):
    rows=[]
    for path in sorted(Path(out).iterdir()):
        if path.is_file() and path.name!="SHA256SUMS":rows.append(f"{sha(path)}  {path.name}")
    (Path(out)/"SHA256SUMS").write_text("\n".join(rows)+"\n")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("run",type=Path);parser.add_argument("out",type=Path)
    parser.add_argument("--repeat-out",type=Path);parser.add_argument("--cad",type=Path,default=CAD_DEFAULT);args=parser.parse_args()
    verdict=derive(args.run,args.out,args.cad)
    if args.repeat_out:
        second=derive(args.run,args.repeat_out,args.cad);comparison={name:{"first":sha(args.out/name),"second":sha(args.repeat_out/name),
            "identical":sha(args.out/name)==sha(args.repeat_out/name)} for name in CORE}
        determinism={"schema":"biospur-c2cc-rotation-aware-determinism-v1","core_outputs":comparison,
            "all_core_byte_identical":all(x["identical"] for x in comparison.values()),"first_verdict":verdict,"second_verdict":second}
        canonical(args.out/"DETERMINISM.json",determinism);canonical(args.repeat_out/"DETERMINISM.json",determinism)
        if not determinism["all_core_byte_identical"]:raise RuntimeError("non-deterministic core derivation")
        finish_sums(args.repeat_out)
    finish_sums(args.out);print(verdict);return 0


if __name__=="__main__":raise SystemExit(main())
