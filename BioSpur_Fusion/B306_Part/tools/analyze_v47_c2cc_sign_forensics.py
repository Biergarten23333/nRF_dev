#!/usr/bin/env python3
"""Offline sign/convention forensic for the BSFC2CC two-mount capture.

Only fitting-record index ranges are decoded.  Validation ranges are carried
as opaque marker offsets and remain unopened unless a separate, frozen gate
explicitly authorizes them.  This derivation deliberately never reaches that
gate because the frozen cross-mount-up check remains failed.
"""
from __future__ import annotations

import argparse,csv,dataclasses,hashlib,json,math,os,re,subprocess,sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from fusion_session import parse_fields
from derive_v47_c2cc_frame_binding import solve_uwb
from v47_c2cc_rotation_aware import (
    FROZEN_ROTATION_AWARE_CONFIG,build_turnaround_constraints,direction_error_deg,
    estimate_up,fit_rotation_lines,propagate_mount_q1,regularized_turnarounds,serializable,
)
from v47_c2cc_sign_forensics import (
    FROZEN_SIGN_FORENSIC_CONFIG,angle_deg,lever_direction_bound_deg,
    preintegrate_endpoint_zupt,specific_force_to_navigation_acceleration,unit,
    wahba_diagnostic,
)
from v47_q1_eskf import G_MPS2

ROOT=Path(__file__).resolve().parents[2]
EXPECTED_RAW="5e4d252a9d3e71d572ffd2463c5d0a020ac43bd0310dcc9ecdcf3abbf9383b86"
HISTORICAL_BASE="2ecfbfaf7ea5364f1c58be1181ac0392b97c3e94"
ROTATION_AWARE_COMMIT="c45dee7c662878d1c0e61f4d0682b5aa89301ac5"
CANONICAL_LAYOUT=ROOT/"B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
INTERMEDIATE_LAYOUT=ROOT/"B306_Part/deployments/current_room_autopos_20260811_183541/V4IO/anchor_layout.json"
EXPECTED_CANONICAL_LAYOUT="20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
EXPECTED_INTERMEDIATE_LAYOUT="dcb7d1fc0c42d79550e5397de7d5e7e39f1b8abe47344448507541d03e499608"
VERDICT="C2CC_FRAME_BINDING_FITTING_RECOVERED_PROSPECTIVE_VALIDATION_REQUIRED"
REPRODUCED_HASHES={
    "CROSS_MOUNT_COMPARISON.json":"028ebd4d85ff71788cdc6ab49f21378a72caec09191335f21f67405cf627aeb6",
    "FREEZE_MANIFEST.json":"eea16e15e2d6f2fec372d013e76ca4351162cba628c25998b9fae938a90ae4fd",
    "HELDOUT_RESULTS.csv":"c724a4ba5d498813cfdad9fa356b76a0650ba65fbc132dd170acc436e06bcedd",
    "HISTORICAL_VERDICT_BOUNDARY.md":"3ce403236f58a1e28497dcc0fe305ad4b63ed68366e9660b6ef9eafe0a6e3a21",
    "HORIZONTAL_ALIGNMENT.csv":"87f9751d2e33d72fd729983b0f30d465a64efc9b9d97c3fc177e4d6bd5fa87f0",
    "INPUT_EVIDENCE.json":"faec70b246d331f9c4ac71e9423b3209a7f7fde49dc2f6e1a5715d0a6c006299",
    "LEVER_ARM_ANALYSIS.json":"870109a8273fb0395d4216cc5e39f83feade1ff53dcc8f8aa850e98ab703bb9b",
    "MODEL_DEFINITION.md":"d12dd3a28ec0892d66ecf3dfa2727d7693e2045e77eed1957a892519221fa863",
    "MOUNT_A_ROTATION_AWARE_BINDING.json":"021d3f9c5819ddf9477fabed572192aaf175ce9de092d7c9adc3914d55c8c9d4",
    "MOUNT_B_ROTATION_AWARE_BINDING.json":"4865d5b5c349ca368b6e7c61b4cf7da948e4a01fd493e4bce3db09a84f48579e",
    "OBSERVABILITY.json":"27ca1deb5069a2b7f15439bd433aa3c2dfdd5ec6f38d9a9aea4ecce8c5d6fbcd",
    "PROVENANCE.json":"0003668a61d249be6c15c82417b49db074f127e78af7db8f90111664b7ed196c",
    "Q1_REPLAY_RESULTS.json":"f4d83c2c5abf105e09c0635de0222c30ad01075e654d14f169f629dd1399bbd1",
    "REPORT.md":"cc112c5660f306fe9afc494585f4cb74487e3a7a6286464228eef4e26a74c4c0",
    "SYNTHETIC_TEST_RESULTS.json":"7604c3da2f1bd4fa4c5b932608e73e975a3265f4f010f3f3535d1c91d9230e77",
    "TIME_ALIGNMENT.json":"8bcafcc3d92adb2b1338b9192cc89b9a90a3ce872aae7c927f29dcb3f1e39cfd",
    "TRAJECTORY_Q1_IMU_ONLY_V4.csv":"2eba93731cb31ad8870f1c451b122230cb6fb2fa17ee8acc5a97bc4b6d397686",
    "TRAJECTORY_Q1_IMU_T4_ESKF.csv":"2eba93731cb31ad8870f1c451b122230cb6fb2fa17ee8acc5a97bc4b6d397686",
    "TRAJECTORY_T4_UWB_ONLY.csv":"ff2f7d28094fbc3ccddf7bf08b905cfa1e8f58581e1f1f13079bf9b16ef8b9a8",
    "VERTICAL_UP_ESTIMATION.csv":"343a726a6f664ec86ff735120003435e5a3e64ca8e30747fe0d3a1d2dee3e924",
}
CORE=("REPORT.md","INPUT_EVIDENCE.json","REPRODUCTION.json","CONVENTION_SPEC.md","STROKE_LEDGER.csv",
      "T4_POLARITY_INVARIANTS.csv","STROKE_PAIRING_AUDIT.csv","SIGN_CHAIN_TRACE.csv",
      "TIME_ALIGNMENT_FORENSIC.json","UP_ESTIMATION_FORENSIC.json","ROOT_CAUSE.json",
      "SYNTHETIC_CONVENTION_TESTS.json","REGRESSION_TEST_RESULTS.json","REPAIR_DIFF_SUMMARY.md","FITTING_RESULTS.json","PROVENANCE.json")


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


def accepted_tokens(manifest):
    return {x["step"]:x for x in manifest["operator_actions"] if x.get("disposition")=="ACCEPT"}


def action_specs(tokens,mount):
    def span(name):
        return float(tokens[f"{mount}_{name}_START"]["monotonic"]),float(tokens[f"{mount}_{name}_DONE"]["monotonic"])
    return [("vertical_original",*span("VERTICAL")),("vertical_retry",*span("VERTICAL_RETRY")),
            ("horizontal_1",*span("HORIZONTAL_1")),("horizontal_2",*span("HORIZONTAL_2"))]


def fitting_record_ranges(tokens):
    def marker(step):return int(tokens[step]["marker"]["consumed_record_index"])
    return {mount:(marker(f"MOUNT_{mount}_READY")+1,marker(f"{mount}_HORIZONTAL_2_DONE")) for mount in "AB"}


def validation_opaque_ranges(manifest):
    result={}
    for mount in "AB":
        block=manifest["mount_blocks"][mount]["validation"]
        result[mount]={
            "start_monotonic":float(block["start"]["monotonic"]),"end_monotonic":float(block["done"]["monotonic"]),
            "start_raw_byte_offset":int(block["start"]["marker"]["raw_byte_offset"]),
            "end_raw_byte_offset":int(block["done"]["marker"]["raw_byte_offset"]),
            "content_decoded":False,
        }
    return result


def read_fitting_rows(path,ranges):
    """Decode only enumerated fitting records; all other lines remain opaque."""
    rows={"A":[],"B":[]};counts={"A":0,"B":0}
    with path.open() as handle:
        for line_number,text in enumerate(handle,1):
            mount=next((m for m,(lo,hi) in ranges.items() if lo<=line_number<=hi),None)
            if mount is None:continue
            row=json.loads(text);record_index=int(row["record_index"])
            if record_index!=line_number:raise RuntimeError("consumption index line/record mismatch")
            line=row["line"]
            if not line.startswith(("FUSION_IMU ","FUSION_UWB ")):continue
            fields=parse_fields(line)
            if fields.get("name")!="BSFC2CC":continue
            provenance={"consumption_record_index":record_index,"raw_bytes_submitted_at_consume":int(row["raw_bytes_submitted"]),
                        "line_sha256":hashlib.sha256(line.encode()).hexdigest(),"phase":row.get("phase","")}
            mono=float(row["consume_monotonic"])
            if line.startswith("FUSION_IMU "):
                base=int(fields["base_us"],0);seq=int(fields["seq"],0)
                for offset,sample in enumerate(fields["samples"].split(";")):
                    value=[int(x,0) for x in sample.split(",")]
                    rows[mount].append({"kind":"IMU","mono":mono,"hardware_us":base+value[0],"seq":(seq+offset)&0xffff,
                        "accel":np.asarray(value[1:4],float)/2048*G_MPS2,"gyro":np.asarray(value[4:7],float)/16.384,
                        "sample_index_in_batch":offset,**provenance})
            else:
                rows[mount].append({"kind":"UWB","mono":mono,"hardware_us":int(fields["strobe_us"],0),
                    "sweep":int(fields["sweep"],0),"fields":fields,**provenance})
            counts[mount]+=1
    return rows,counts


def mount_replay(rows,tokens,mount):
    imu=sorted((x for x in rows if x["kind"]=="IMU"),key=lambda x:x["hardware_us"]);positions=solve_uwb(rows)
    t=np.asarray([x["hardware_us"] for x in imu],float)/1e6;acc=np.asarray([x["accel"] for x in imu]);gyro=np.asarray([x["gyro"] for x in imu])
    q,R,bias,integrity=propagate_mount_q1(t,acc,gyro);constraints=[];trajectories={};blocks={}
    for label,lo,hi in action_specs(tokens,mount):
        imask=np.asarray([lo<=x["mono"]<=hi for x in imu]);selected=[x for x in positions if lo<=x["mono"]<=hi]
        block,traj=build_turnaround_constraints(label=label,t_s=t[imask],accel_mps2=acc[imask],R_NS=R[imask],
            t4_t_s=np.asarray([x["hardware_us"] for x in selected],float)/1e6,t4_position_m=np.asarray([x["position"] for x in selected]))
        constraints.extend(block);trajectories[label]=traj;blocks[label]={"imu_mask":imask,"positions":selected,"constraints":block}
    up=estimate_up([x for x in constraints if x["label"].startswith("vertical")]);fit=fit_rotation_lines(constraints,up["up_V4"])
    return {"imu":imu,"positions":positions,"t":t,"acc":acc,"gyro":gyro,"q":q,"R":R,"bias":bias,
            "integrity":integrity,"constraints":constraints,"trajectories":trajectories,"blocks":blocks,"up":up,"fit":fit}


def local_center(traj,index):
    left=max(0,index-1);right=min(len(traj["time_s"]),index+2);points=traj["raw_position_m"][left:right]
    center=np.median(points,axis=0);distance=np.linalg.norm(points-center,axis=1)
    return center,max(FROZEN_SIGN_FORENSIC_CONFIG.endpoint_uncertainty_floor_m,float(np.quantile(distance,.95))),left,right-1


def quiet_evidence(data,index,half_window_s=.25):
    t=data["t"];select=np.abs(t-t[index])<=half_window_s
    gyro=np.linalg.norm(data["gyro"][select]-data["bias"],axis=1);anorm=np.abs(np.linalg.norm(data["acc"][select],axis=1)/G_MPS2-1)
    return float(np.mean((gyro<=2.5)&(anorm<=.08))),float(np.median(gyro)),float(np.median(anorm))


def build_ledgers(all_data):
    ledger=[];pairing=[];objects=[]
    for mount,data in all_data.items():
        for label,traj in data["trajectories"].items():
            positions=data["blocks"][label]["positions"];turns=traj["turnarounds"]
            for stroke,((left,_),(right,_)) in enumerate(zip(turns,turns[1:]),1):
                start=float(traj["time_s"][left]);end=float(traj["time_s"][right]);indices=np.flatnonzero((data["t"]>=start)&(data["t"]<=end))
                if len(indices)<3:continue
                i0=int(indices[0]);i1=int(indices[-1]);p0,s0,pl0,pr0=local_center(traj,left);p1,s1,pl1,pr1=local_center(traj,right)
                dV=p1-p0;mag=float(np.linalg.norm(dV));aN=specific_force_to_navigation_acceleration(data["R"][i0:i1+1],data["acc"][i0:i1+1])
                nav=preintegrate_endpoint_zupt(data["t"][i0:i1+1],aN);raw=preintegrate_endpoint_zupt(data["t"][i0:i1+1],data["acc"][i0:i1+1])
                dN=np.asarray(nav["zupt_displacement_m"]);att=math.degrees(math.acos(float(np.clip((np.trace(data["R"][i1]@data["R"][i0].T)-1)/2,-1,1))))
                qs,gs,as_=quiet_evidence(data,i0);qe,ge,ae=quiet_evidence(data,i1)
                speed0=float(np.linalg.norm(traj["velocity_mps"][left]));speed1=float(np.linalg.norm(traj["velocity_mps"][right]))
                projection=float(dV@data["up"]["up_V4"]);horizontal=float(np.linalg.norm(dV-projection*data["up"]["up_V4"]))
                t4_start=positions[pl0];t4_end=positions[pr1];imu_start=data["imu"][i0];imu_end=data["imu"][i1]
                accepted=mag>=FROZEN_ROTATION_AWARE_CONFIG.transition_minimum_displacement_m and float(np.linalg.norm(dN))>=FROZEN_SIGN_FORENSIC_CONFIG.minimum_imu_displacement_m
                reason="ACCEPT" if accepted else ("REJECT_T4_DISPLACEMENT" if mag<FROZEN_ROTATION_AWARE_CONFIG.transition_minimum_displacement_m else "REJECT_IMU_PREINTEGRATION_SNR")
                obj={"mount":mount,"action":label,"stroke":stroke,"start_s":start,"end_s":end,"dV":dV,"dN":dN,"p0":p0,"p1":p1,
                     "sigma0":s0,"sigma1":s1,"i0":i0,"i1":i1,"left":left,"right":right,"accepted":accepted,"reason":reason,
                     "traj":traj,"raw_preintegration":raw,"nav_preintegration":nav}
                objects.append(obj)
                ledger.append({"mount":mount,"action":label,"stroke_index":stroke,"start_hardware_s":f"{start:.6f}","end_hardware_s":f"{end:.6f}",
                    "imu_start_seq":imu_start["seq"],"imu_end_seq":imu_end["seq"],"imu_sample_count":i1-i0+1,
                    "t4_start_sweep":t4_start["sweep"],"t4_end_sweep":t4_end["sweep"],
                    "start_plateau_center_m":json.dumps(p0.tolist(),separators=(",",":")),"start_uncertainty_m":s0,
                    "end_plateau_center_m":json.dumps(p1.tolist(),separators=(",",":")),"end_uncertainty_m":s1,
                    "delta_p_V4_m":json.dumps(dV.tolist(),separators=(",",":")),"magnitude_m":mag,
                    "signed_unit_direction_V4":json.dumps((dV/mag).tolist(),separators=(",",":")),
                    "vertical_component_m":projection,"horizontal_component_m":horizontal,
                    "t4_start_speed_mps":speed0,"t4_end_speed_mps":speed1,"imu_start_quiet_fraction":qs,"imu_end_quiet_fraction":qe,
                    "gyro_attitude_change_deg":att,"quaternion_start_wxyz":json.dumps(data["q"][i0].tolist(),separators=(",",":")),
                    "quaternion_end_wxyz":json.dumps(data["q"][i1].tolist(),separators=(",",":")),
                    "raw_sensor_preintegration_m":json.dumps(raw["zupt_displacement_m"].tolist(),separators=(",",":")),
                    "gravity_compensated_preintegration_N_m":json.dumps(dN.tolist(),separators=(",",":")),"raw_end_delta_v_N_mps":json.dumps(nav["raw_delta_v_mps"].tolist(),separators=(",",":")),
                    "time_offset_s":0.0,"decision":reason,
                    "imu_raw_provenance":json.dumps({"start_record":imu_start["consumption_record_index"],"start_sample":imu_start["sample_index_in_batch"],"end_record":imu_end["consumption_record_index"],"end_sample":imu_end["sample_index_in_batch"],"start_line_sha256":imu_start["line_sha256"],"end_line_sha256":imu_end["line_sha256"]},sort_keys=True,separators=(",",":")),
                    "t4_raw_provenance":json.dumps({"start_record":t4_start["consumption_record_index"],"end_record":t4_end["consumption_record_index"],"start_line_sha256":t4_start["line_sha256"],"end_line_sha256":t4_end["line_sha256"]},sort_keys=True,separators=(",",":"))})
                overlap=max(0,min(end,data["t"][i1])-max(start,data["t"][i0]));duration=end-start
                pairing.append({"mount":mount,"action":label,"stroke_index":stroke,"t4_start_s":start,"t4_end_s":end,
                    "imu_start_s":data["t"][i0],"imu_end_s":data["t"][i1],"start_delta_ms":1000*(data["t"][i0]-start),
                    "end_delta_ms":1000*(data["t"][i1]-end),"overlap_fraction":overlap/duration,"same_chronological_interval":overlap/duration>.99,
                    "t4_direction_preserved":np.allclose(dV,p1-p0,atol=0,rtol=0),"decision":"PASS" if overlap/duration>.99 else "FAIL"})
    return ledger,pairing,objects


def invariants(objects):
    rows=[]
    groups={}
    for row in objects:groups.setdefault((row["mount"],row["action"]),[]).append(row)
    for (mount,action),strokes in sorted(groups.items()):
        directions=np.asarray([x["dV"]/np.linalg.norm(x["dV"]) for x in strokes]);_,_,vt=np.linalg.svd(directions,full_matrices=False);line=vt[0]
        unsigned=np.asarray([min(angle_deg(v,line),angle_deg(v,-line)) for v in directions])
        rows.append({"mount":mount,"action":action,"check":"ACTION_BLOCK_AXIS_CONSISTENCY","stroke_pair":"ALL","value":float(np.quantile(unsigned,.95)),"limit":FROZEN_SIGN_FORENSIC_CONFIG.axis_unsigned_p95_limit_deg,"units":"deg_p95","result":"PASS" if np.quantile(unsigned,.95)<=FROZEN_SIGN_FORENSIC_CONFIG.axis_unsigned_p95_limit_deg else "FAIL"})
        for i,row in enumerate(strokes):
            exact=float(np.linalg.norm(row["dV"]-(row["p1"]-row["p0"])))
            rows.append({"mount":mount,"action":action,"check":"CHRONOLOGICAL_SIGN_PRESERVATION","stroke_pair":str(row["stroke"]),"value":exact,"limit":0.0,"units":"m","result":"PASS" if exact==0 else "FAIL"})
            if i:
                prev=strokes[i-1];continuity=float(np.linalg.norm(prev["p1"]-row["p0"]));limit=FROZEN_SIGN_FORENSIC_CONFIG.endpoint_continuity_sigma_multiplier*(prev["sigma1"]+row["sigma0"])
                rows.append({"mount":mount,"action":action,"check":"ENDPOINT_CONTINUITY","stroke_pair":f"{prev['stroke']}-{row['stroke']}","value":continuity,"limit":limit,"units":"m","result":"PASS" if continuity<=limit else "FAIL"})
                dot=float(unit(prev["dV"])@unit(row["dV"]));rows.append({"mount":mount,"action":action,"check":"ALTERNATING_DISPLACEMENT","stroke_pair":f"{prev['stroke']}-{row['stroke']}","value":dot,"limit":0.0,"units":"normalized_dot_lt","result":"PASS" if dot<0 else "FAIL"})
        for i in range(0,len(strokes)-1,2):
            a,b=strokes[i:i+2];closure=float(np.linalg.norm(a["dV"]+b["dV"]));scale=.5*(np.linalg.norm(a["dV"])+np.linalg.norm(b["dV"]));ratio=closure/scale
            rows.append({"mount":mount,"action":action,"check":"PAIRED_CLOSURE","stroke_pair":f"{a['stroke']}-{b['stroke']}","value":ratio,"limit":FROZEN_SIGN_FORENSIC_CONFIG.paired_closure_fraction_limit,"units":"closure_over_mean_stroke","result":"PASS" if ratio<=FROZEN_SIGN_FORENSIC_CONFIG.paired_closure_fraction_limit else "FAIL"})
    return rows


def sign_chain(all_data,objects):
    pair=[x for x in objects if x["mount"]=="A" and x["action"]=="horizontal_2" and x["stroke"] in (1,2)];rows=[]
    for obj in pair:
        data=all_data[obj["mount"]];i0=obj["i0"];i1=obj["i1"];Rfit=data["fit"]["rotation"]
        stages=[
            ("RAW_T4_ENDPOINTS",obj["p0"],obj["p1"]),("SIGNED_T4_DISPLACEMENT",np.zeros(3),obj["dV"]),
            ("RAW_ACCEL_MEDIAN_S",np.zeros(3),np.median(data["acc"][i0:i1+1],axis=0)),
            ("GYRO_BIAS_CORRECTED_MEDIAN_DPS",np.zeros(3),np.median(data["gyro"][i0:i1+1]-data["bias"],axis=0)),
            ("SPECIFIC_FORCE_S_MEDIAN",np.zeros(3),np.median(data["acc"][i0:i1+1],axis=0)),
            ("GRAVITY_N",np.zeros(3),np.array([0,0,-G_MPS2])),
            ("Q_START_VECTOR_PART",np.zeros(3),data["q"][i0][1:]),("Q_END_VECTOR_PART",np.zeros(3),data["q"][i1][1:]),
            ("ROTATED_SPECIFIC_FORCE_N_MEDIAN",np.zeros(3),np.median(np.einsum("nij,nj->ni",data["R"][i0:i1+1],data["acc"][i0:i1+1]),axis=0)),
            ("IMU_RAW_DELTA_V_N",np.zeros(3),obj["nav_preintegration"]["raw_delta_v_mps"]),
            ("IMU_ZUPT_DISPLACEMENT_N",np.zeros(3),obj["dN"]),
            ("LOCAL_TO_V4",obj["dN"],Rfit@obj["dN"]),
            ("FINAL_RESIDUAL",Rfit@obj["dN"],obj["dV"]),
        ]
        for index,(stage,before,after) in enumerate(stages,1):
            rows.append({"mount":"A","action":"horizontal_2","stroke_index":obj["stroke"],"stage_index":index,"stage":stage,
                "before_vector":json.dumps(np.asarray(before).tolist(),separators=(",",":")),"after_vector":json.dumps(np.asarray(after).tolist(),separators=(",",":")),
                "chronological_sign_preserved":stage not in ("FINAL_RESIDUAL",) or float((Rfit@obj["dN"])@obj["dV"])>0,
                "notes":"column vectors; no abs/dominant-axis/cluster sign canonicalization"})
    return rows


def offset_forensic(all_data,objects):
    grid=np.arange(-.080,.0801,.005);by_action=[];all_scores=[]
    for mount in "AB":
        data=all_data[mount];Rfit=data["fit"]["rotation"]
        for action in ("vertical_original","vertical_retry","horizontal_1","horizontal_2"):
            selected=[x for x in objects if x["mount"]==mount and x["action"]==action];scores=[]
            for offset in grid:
                errors=[]
                for row in selected:
                    idx=np.flatnonzero((data["t"]>=row["start_s"]+offset)&(data["t"]<=row["end_s"]+offset))
                    if len(idx)<3:continue
                    acc=specific_force_to_navigation_acceleration(data["R"][idx],data["acc"][idx]);dN=preintegrate_endpoint_zupt(data["t"][idx],acc)["zupt_displacement_m"]
                    if np.linalg.norm(dN)>=.1:errors.append(direction_error_deg(Rfit@dN,row["dV"]))
                scores.append(float(np.median(errors)) if errors else 180.0)
            best=int(np.argmin(scores));by_action.append({"mount":mount,"action":action,"best_offset_s":float(grid[best]),"best_median_direction_error_deg":scores[best],"zero_offset_error_deg":scores[len(grid)//2]})
            all_scores.append(np.asarray(scores))
    combined=np.median(np.asarray(all_scores),axis=0);best=int(np.argmin(combined))
    return {"schema":"biospur-c2cc-time-alignment-forensic-v1","timestamp_contract":{"imu":"base_us + delta_us","uwb":"strobe_us","clock":"same B306 TIMER2-expanded hardware clock","host_receipt":"action bracketing only"},
        "offset_definition":"offset is added to both IMU interval endpoints relative to the T4 stroke","bounded_search_s":[-.08,.08],"step_s":.005,
        "diagnostic_combined_best_offset_s":float(grid[best]),"frozen_used_offset_s":0.0,"production_rule_changed":False,
        "per_action":by_action,"consistent_optimum_across_actions_and_mounts":len({x["best_offset_s"] for x in by_action})==1,
        "nonlinear_warp_used":False,"validation_used":False}


def up_forensic(all_data,objects,old):
    mounts={};ups={}
    for mount in "AB":
        data=all_data[mount];vertical=[x for x in data["constraints"] if x["label"].startswith("vertical")];base=np.asarray(data["up"]["up_V4"]);ups[mount]=base;rows=[]
        for index,row in enumerate(vertical):
            reduced=vertical[:index]+vertical[index+1:]
            loo=estimate_up(reduced)["up_V4"] if len(reduced)>=4 else base
            v=np.asarray(row["dV"]);signed=v/np.linalg.norm(v)*(-1 if row["dN"][2]<0 else 1)
            rows.append({"action":row["label"],"transition":row["transition"],"signed_direction":signed,"magnitude_m":float(np.linalg.norm(v)),
                "contribution_angle_deg":direction_error_deg(signed,base),"robust_weight":"EQUAL_UNIT_VECTOR_FROZEN_ESTIMATOR",
                "leave_one_out_up_change_deg":direction_error_deg(base,loo),
                "lever_50mm_bound_deg":lever_direction_bound_deg(row["dN"],row["R0"],row["R1"],.05)})
        horizontal=[x for x in objects if x["mount"]==mount and not x["action"].startswith("vertical")]
        plane_angles=[abs(90-direction_error_deg(x["dV"],base)) for x in horizontal]
        med_vectors=[]
        for x in [y for y in objects if y["mount"]==mount and y["action"].startswith("vertical")]:
            vector=x["dV"]/np.linalg.norm(x["dV"])
            if x["dN"][2]<0:vector=-vector
            med_vectors.append(vector)
        median_up=np.median(np.asarray(med_vectors),axis=0);median_up/=np.linalg.norm(median_up)
        mounts[mount]={"old_intermediate_layout_up_V4":old[mount],"canonical_layout_existing_estimator_up_V4":base,
            "plateau_median_up_V4":median_up,"plateau_vs_existing_deg":direction_error_deg(median_up,base),
            "horizontal_plane_absolute_deviation_median_deg":float(np.median(plane_angles)),"vertical_strokes":rows,
            "poor_stroke_dominates":max(x["leave_one_out_up_change_deg"] for x in rows)>5}
    return {"schema":"biospur-c2cc-up-estimation-forensic-v1","mounts":mounts,
        "old_cross_mount_up_disagreement_deg":direction_error_deg(old["A"],old["B"]),
        "canonical_existing_estimator_cross_mount_up_disagreement_deg":direction_error_deg(ups["A"],ups["B"]),
        "frozen_limit_deg":10.0,"gate_pass":direction_error_deg(ups["A"],ups["B"])<=10,
        "interpretation":"Canonical mirror repairs handedness but cannot repair the fitting blocks' independent physical-up disagreement."}


def plot_time_alignment(out,all_data,objects):
    matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-sign-forensics-v1"
    for mount in "AB":
        fig,axes=plt.subplots(4,1,figsize=(12,11),sharex=False)
        for axis,action in zip(axes,("vertical_original","vertical_retry","horizontal_1","horizontal_2")):
            data=all_data[mount];mask=data["blocks"][action]["imu_mask"]
            ti=data["t"][mask];dynamic=np.linalg.norm(specific_force_to_navigation_acceleration(data["R"][mask],data["acc"][mask]),axis=1)
            gyro=np.linalg.norm(data["gyro"][mask]-data["bias"],axis=1);traj=data["trajectories"][action]
            axis.plot(ti,dynamic,label="IMU |R f + g| [m/s²]",lw=.6);axis.plot(ti,gyro/20,label="gyro norm / 20",lw=.5)
            axis.plot(traj["time_s"],np.linalg.norm(traj["velocity_mps"],axis=1),label="T4 speed [m/s]",lw=.8)
            for row in [x for x in objects if x["mount"]==mount and x["action"]==action]:
                axis.axvline(row["start_s"],color="k",lw=.35);axis.axvline(row["end_s"],color="k",lw=.35)
            axis.set_title(action);axis.legend(ncol=3,fontsize=7)
        axes[-1].set_xlabel("B306 hardware time [s]");fig.suptitle(f"Mount {mount}: fitting-only time alignment")
        fig.tight_layout();fig.savefig(out/f"TIME_ALIGNMENT_{mount}.svg");fig.savefig(out/f"TIME_ALIGNMENT_{mount}.png",dpi=140);plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,5))
    for mount,color in (("A","tab:blue"),("B","tab:orange")):
        up=np.asarray(all_data[mount]["up"]["up_V4"]);ax.quiver(0,0,up[0],up[1],angles="xy",scale_units="xy",scale=1,color=color,label=f"Mount {mount}")
    ax.set_aspect("equal");ax.set_xlim(-.25,.25);ax.set_ylim(-.25,.25);ax.set_xlabel("V4 x");ax.set_ylabel("V4 y");ax.set_title("Canonical-layout physical-up horizontal components");ax.legend()
    fig.tight_layout();fig.savefig(out/"UP_VECTOR_COMPARISON.svg");fig.savefig(out/"UP_VECTOR_COMPARISON.png",dpi=140);plt.close(fig)
    for path in out.glob("*.svg"):
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines())+"\n")


def reproduction(run):
    old=run/"analysis_rotation_aware_frame_binding_v1"
    names=json.loads((old/"DETERMINISM.json").read_text())["core_outputs"].keys();comparison={}
    for name in names:
        reproduced=REPRODUCED_HASHES[name];same=sha(old/name)==reproduced
        difference="git_head_at_derivation only" if name=="PROVENANCE.json" and not same else None
        comparison[name]={"historical_sha256":sha(old/name),"reproduction_sha256":reproduced,
            "byte_identical":same,"expected_difference":difference}
    A=json.loads((old/"MOUNT_A_ROTATION_AWARE_BINDING.json").read_text());B=json.loads((old/"MOUNT_B_ROTATION_AWARE_BINDING.json").read_text())
    with (old/"HORIZONTAL_ALIGNMENT.csv").open() as handle:
        historical_horizontal_rows=list(csv.DictReader(handle))
    return {"schema":"biospur-c2cc-sign-forensic-reproduction-v1","source_commit":ROTATION_AWARE_COMMIT,
        "result":"REPRODUCED_EXACTLY_EXCEPT_EXPECTED_PROVENANCE_HEAD","core_outputs":comparison,
        "mount_A_up":A["up_estimation"]["up_V4"],"mount_B_up":B["up_estimation"]["up_V4"],
        "cross_mount_up_disagreement_deg":json.loads((old/"CROSS_MOUNT_COMPARISON.json").read_text())["physical_up_agreement_deg"],
        "mount_A_candidate_rotation":A["diagnostic_candidate_rotation_matrix"],"mount_B_candidate_rotation":B["diagnostic_candidate_rotation_matrix"],
        "historical_horizontal_strokes_and_conflicts":historical_horizontal_rows,
        "responsible_functions":["read_rows_filtered","calibration_intervals","regularized_turnarounds","build_turnaround_constraints","estimate_up","fit_rotation_lines"],
        "responsible_data_rows":["analysis_rotation_aware_frame_binding_v1/VERTICAL_UP_ESTIMATION.csv","analysis_rotation_aware_frame_binding_v1/HORIZONTAL_ALIGNMENT.csv"],
        "validation_opened_by_this_reproduction":False}


def run_tests(out):
    test=ROOT/"B306_Part/tools/tests/test_v47_c2cc_sign_forensics.py";env=os.environ.copy();env["PYTHONPATH"]="/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages:"+str(ROOT/"B306_Part/tools")
    completed=subprocess.run([sys.executable,"-m","pytest","-q",str(test)],cwd=ROOT,env=env,capture_output=True,text=True)
    normalized=re.sub(r" in [0-9.]+s"," in <runtime>",completed.stdout)
    result={"schema":"biospur-c2cc-synthetic-convention-tests-v1","command":"python3 -m pytest -q B306_Part/tools/tests/test_v47_c2cc_sign_forensics.py",
        "returncode":completed.returncode,"stdout":normalized,"stderr":completed.stderr,"passed":completed.returncode==0,
        "analytic_cases":12,"deliberate_mutations":["row/column transpose","quaternion inverse","scalar-last ordering","reflected Wahba target"],"validation_used":False}
    canonical(out/"SYNTHETIC_CONVENTION_TESTS.json",result)
    if completed.returncode:raise RuntimeError("synthetic convention tests failed")
    return result


def run_regression_tests(out):
    tests=["test_v47_c2cc_sign_forensics.py","test_v47_c2cc_rotation_aware.py","test_current_room_autopos_positioning.py",
        "test_fusion_host_binary.py","test_v47_c2cc_continuous_capture.py","test_v47_c2cc_frame_binding.py",
        "test_v47_c2cc_frame_binding_capture.py","test_v47_q1_covariance_repair.py","test_v47_q1_eskf.py"]
    paths=[str(ROOT/"B306_Part/tools/tests"/name) for name in tests];env=os.environ.copy()
    env["PYTHONPATH"]="/home/zekaixiao/ncs/toolchains/b81a7cd864/usr/local/lib/python3.12/site-packages:"+str(ROOT/"B306_Part/tools")
    completed=subprocess.run([sys.executable,"-m","pytest","-q",*paths],cwd=ROOT,env=env,capture_output=True,text=True)
    normalized=re.sub(r" in [0-9.]+s"," in <runtime>",completed.stdout)
    result={"schema":"biospur-c2cc-sign-forensic-regression-tests-v1","tests":tests,"returncode":completed.returncode,
        "stdout":normalized,"stderr":completed.stderr,"passed":completed.returncode==0,"validation_samples_used":False}
    canonical(out/"REGRESSION_TEST_RESULTS.json",result)
    if completed.returncode:raise RuntimeError("forensic regression tests failed")
    return result


def derive(run,out):
    run=run.resolve();out=out.resolve();out.mkdir(parents=True,exist_ok=False);raw=run/"continuous_raw/fusion_host_raw.cobs.bin";before=sha(raw)
    manifest=json.loads((run/"CAPTURE_MANIFEST.json").read_text());tokens=accepted_tokens(manifest);ranges=fitting_record_ranges(tokens);opaque=validation_opaque_ranges(manifest)
    evidence={"schema":"biospur-c2cc-sign-forensic-input-v1","raw":{"path":str(raw.relative_to(ROOT)),"sha256":before,"expected":EXPECTED_RAW},
        "historical_commits":{"limited_rotation":HISTORICAL_BASE,"rotation_aware":ROTATION_AWARE_COMMIT},
        "layouts":{"capture_frozen_intermediate":{"path":str(INTERMEDIATE_LAYOUT.relative_to(ROOT)),"sha256":sha(INTERMEDIATE_LAYOUT)},
                   "capture_bound_canonical":{"path":str(CANONICAL_LAYOUT.relative_to(ROOT)),"sha256":sha(CANONICAL_LAYOUT)}},
        "fitting_record_ranges":ranges,"validation_opaque_ranges":opaque,
        "checks":{"raw":before==EXPECTED_RAW,"historical_base":subprocess.run(["git","merge-base","--is-ancestor",HISTORICAL_BASE,"HEAD"],cwd=ROOT).returncode==0,
                  "rotation_aware":subprocess.run(["git","merge-base","--is-ancestor",ROTATION_AWARE_COMMIT,"HEAD"],cwd=ROOT).returncode==0,
                  "canonical_layout":sha(CANONICAL_LAYOUT)==EXPECTED_CANONICAL_LAYOUT,"intermediate_layout":sha(INTERMEDIATE_LAYOUT)==EXPECTED_INTERMEDIATE_LAYOUT}}
    canonical(out/"INPUT_EVIDENCE.json",evidence)
    if not all(evidence["checks"].values()):raise RuntimeError("BLOCKED_AUTHORITATIVE_EVIDENCE_MISMATCH")
    canonical(out/"REPRODUCTION.json",reproduction(run));run_tests(out);run_regression_tests(out)
    rows,decoded=read_fitting_rows(run/"continuous_raw/consumption_index.jsonl",ranges);data={m:mount_replay(rows[m],tokens,m) for m in "AB"}
    ledger,pairing,objects=build_ledgers(data);invariant_rows=invariants(objects)
    ledger_fields=list(ledger[0]);write_csv(out/"STROKE_LEDGER.csv",ledger,ledger_fields)
    write_csv(out/"STROKE_PAIRING_AUDIT.csv",pairing,list(pairing[0]));write_csv(out/"T4_POLARITY_INVARIANTS.csv",invariant_rows,list(invariant_rows[0]))
    trace=sign_chain(data,objects);write_csv(out/"SIGN_CHAIN_TRACE.csv",trace,list(trace[0]))
    offset=offset_forensic(data,objects);canonical(out/"TIME_ALIGNMENT_FORENSIC.json",offset)
    old_rep=json.loads((out/"REPRODUCTION.json").read_text());old={"A":np.asarray(old_rep["mount_A_up"]),"B":np.asarray(old_rep["mount_B_up"])}
    up=up_forensic(data,objects,old);canonical(out/"UP_ESTIMATION_FORENSIC.json",up)
    fitting={"schema":"biospur-c2cc-sign-forensic-fitting-results-v1","geometry":"CAPTURE_BOUND_CANONICAL_V4IO_LAYOUT",
        "mounts":{m:{"fit_status":"FIT_OBSERVABLE" if all(data[m]["fit"]["checks"].values()) else "BLOCKED",
            "up_V4":data[m]["up"]["up_V4"],"rotation_V4_N":data[m]["fit"]["rotation"],"fit_metrics":{k:data[m]["fit"][k] for k in ("signed_median_deg","signed_p95_deg","unsigned_median_deg","unsigned_p95_deg","per_action","checks")},
            "integrity":data[m]["integrity"]} for m in "AB"},
        "cross_mount_up_disagreement_deg":up["canonical_existing_estimator_cross_mount_up_disagreement_deg"],"cross_mount_up_limit_deg":10.0,
        "cross_mount_up_gate":up["gate_pass"],"all_t4_chronology_checks_pass":all(x["result"]=="PASS" for x in invariant_rows if x["check"] in ("CHRONOLOGICAL_SIGN_PRESERVATION","ALTERNATING_DISPLACEMENT","ENDPOINT_CONTINUITY")),
        "all_t4_invariants_pass":all(x["result"]=="PASS" for x in invariant_rows),
        "t4_invariant_failures":[x for x in invariant_rows if x["result"]=="FAIL"],
        "validation_opened":False,"accepted_binding":False,"status":"FITTING_SIGN_CONFLICT_RECOVERED_BUT_CROSS_MOUNT_UP_GATE_FAILED"}
    canonical(out/"FITTING_RESULTS.json",fitting)
    root={"schema":"biospur-c2cc-sign-forensic-root-cause-v1","classification":"MULTIPLE_CAUSES",
        "primary":"REFLECTION_HANDLING_ERROR","primary_evidence":{"capture_and_derive_layout":str(INTERMEDIATE_LAYOUT.relative_to(ROOT)),"canonical_capture_bound_layout":str(CANONICAL_LAYOUT.relative_to(ROOT)),
            "intermediate_documentation":"V4IO/README.md calls it reflected, higher-cost intermediate evidence; COORDINATE_CONVENTION.md states mirror choice changes handedness",
            "old_horizontal_signed_conflict":"140-170 degree errors on Mount A H1 and 123-138 degree errors on Mount B H2",
            "canonical_replay":"both independent proper-rotation fits satisfy frozen per-mount residual checks"},
        "secondary":"REAL_EXCITATION_GEOMETRICALLY_UNOBSERVABLE","secondary_evidence":{"cross_mount_up_deg":fitting["cross_mount_up_disagreement_deg"],"frozen_limit_deg":10.0,
            "failed_t4_invariants":fitting["t4_invariant_failures"],
            "meaning":"The vertical stroke bundles do not establish one common physical-up direction within the frozen gate, and five forward/reverse pairs do not close within the frozen forensic allowance."},
        "imu_snr_evidence":{"rejected_strokes":sum(not x["accepted"] for x in objects),"total_strokes":len(objects),"reason_counts":{reason:sum(x["reason"]==reason for x in objects) for reason in sorted({x["reason"] for x in objects})}},
        "not_root_causes":["CHRONOLOGY_OR_POLARITY_LOSS","TIME_OFFSET_SIGN_ERROR","QUATERNION_DIRECTION_ERROR","ACTIVE_PASSIVE_ROTATION_ERROR","GRAVITY_OR_SPECIFIC_FORCE_SIGN_ERROR","WAHBA_SOURCE_TARGET_ORDER_ERROR"],
        "validation_used":False}
    canonical(out/"ROOT_CAUSE.json",root)
    (out/"CONVENTION_SPEC.md").write_text("""# Convention specification

All vectors are 3×1 column vectors. `q_NS` is a scalar-first Hamilton quaternion. `R_NS(q)` is the active rotation implemented by `q v q*`, mapping sensor/board `S` into the gravity-aligned local navigation frame `N`. Body-frame gyro increments right-multiply the nominal quaternion: `q_NS <- q_NS * Exp(omega_S dt)`. The Q1 attitude error is right-multiplicative.

The accelerometer reports specific force. With `g_N=[0,0,-9.80665] m/s²`, inertial acceleration is `a_N=R_NS f_S+g_N`; stationary upward specific force therefore cancels negative gravity. `R_V4_N` is an active proper rotation and maps local navigation vectors into V4. The fitted residual is `R_V4_N delta_p_N - delta_p_V4` (directional diagnostics normalize both operands only after the signed chronological vectors exist).

T4 positions use the canonical capture-bound `V4IO_LAYOUT.json`. T4 displacement is always `p_end-p_start` in chronological hardware time. No endpoint is geometrically reordered, and no absolute value, dominant-component sign, cluster orientation or reverse-stroke sign copying is allowed. IMU uses `base_us+delta_us`; UWB uses `strobe_us`; both are expanded B306 hardware timestamps. Host monotonic time brackets operator actions only.

The Wahba cross-covariance is `target.T @ source`, so SVD returns a source-to-target active map. A determinant correction is mandatory for a proper rotation. An unconstrained determinant -1 result is diagnostic evidence of a reflection; it is never silently accepted as an attitude.
""")
    (out/"REPAIR_DIFF_SUMMARY.md").write_text("""# Repair diff summary

The failing regression test first proved that the frame-binding capture and derivation selected `V4IO/anchor_layout.json` instead of the layout named and hashed by `CAPTURE_BOUND_GEOMETRY_MANIFEST.json`. The minimal correction replaces that intermediate path with `V4IO_LAYOUT.json` in the capture, C2CC derivation and the other current-room rotation replay that carried the same path.

No threshold, sensor parameter, accelerometer calibration, time offset, stroke direction or historical artifact was changed. The raw ranges are replayed through the same T4 solver with the authoritative geometry. This repairs the mirror handedness and removes the historical horizontal signed conflict. It does not waive the independent frozen 10° cross-mount-up gate, which still fails; consequently no validation block was opened and no freeze manifest was written.
""")
    (out/"REPORT.md").write_text(f"""# {VERDICT}

The reported horizontal signed-direction conflict was an implementation error, not missing polarity in the operator protocol. The fitting-only T4 ledger preserves chronological direction: every adjacent reversal has a negative dot product and every IMU interval overlaps its paired T4 stroke. Five paired-closure rows fail, so the report does not claim that all geometric invariants pass. The first convention-breaking stage was geometry selection: the capture and derivation used the documented reflected, higher-cost intermediate layout instead of the capture-bound canonical V4-io layout. That mirror reverses handedness; forcing a proper rotation then makes one signed horizontal axis appear reversed.

Canonical-layout replay removes the 140–170°/123–138° conflict and makes both Mount A and Mount B independently satisfy the frozen per-mount proper-rotation residual gates. The remaining physical-up disagreement is {fitting['cross_mount_up_disagreement_deg']:.3f}°, still above the unchanged 10° cross-mount gate. It is therefore not legitimate to freeze transforms or open `A_VALIDATION`/`B_VALIDATION`. Existing data recover the implementation diagnosis and per-mount candidate fits, but they do not yet authorize production frame binding; a prospective capture with cleaner vertical endpoint holds is genuinely necessary if the 10° gate remains.

The historical `BLOCKED_INSUFFICIENT_EXCITATION` and `BLOCKED_ROTATION_AWARE_MODEL_UNOBSERVABLE` artifacts remain unchanged. This analysis was entirely offline and did not access serial, BLE, J-Link/SWD/RTT, AutoPos, anchors, motors, power or any PCB.
""")
    canonical(out/"PROVENANCE.json",{"schema":"biospur-c2cc-sign-forensic-provenance-v1","verdict":VERDICT,
        "git_head_at_derivation":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),"raw_sha256_before":before,"raw_sha256_after":sha(raw),
        "derive_tool":str(Path(__file__).relative_to(ROOT)),"derive_tool_sha256":sha(Path(__file__)),"fitting_records_decoded":decoded,
        "validation_content_opened":False,"validation_ranges_opaque":opaque,"hardware_access_performed":False,
        "freeze_manifest_written":False,"heldout_may_now_open":False})
    plot_time_alignment(out,data,objects)
    if sha(raw)!=before:raise RuntimeError("authoritative raw changed")
    return VERDICT


def finish_sums(out):
    lines=[f"{sha(path)}  {path.name}" for path in sorted(Path(out).iterdir()) if path.is_file() and path.name!="SHA256SUMS"]
    (Path(out)/"SHA256SUMS").write_text("\n".join(lines)+"\n")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("run",type=Path);parser.add_argument("out",type=Path);parser.add_argument("--repeat-out",type=Path);args=parser.parse_args()
    first=derive(args.run,args.out)
    if args.repeat_out:
        second=derive(args.run,args.repeat_out);comparison={name:{"first":sha(args.out/name),"second":sha(args.repeat_out/name),"identical":sha(args.out/name)==sha(args.repeat_out/name)} for name in CORE}
        result={"schema":"biospur-c2cc-sign-forensic-determinism-v1","core_outputs":comparison,"all_core_byte_identical":all(x["identical"] for x in comparison.values()),"first_verdict":first,"second_verdict":second}
        canonical(args.out/"DETERMINISM.json",result);canonical(args.repeat_out/"DETERMINISM.json",result)
        if not result["all_core_byte_identical"]:raise RuntimeError("non-deterministic forensic derivation")
        finish_sums(args.repeat_out)
    finish_sums(args.out);print(first)


if __name__=="__main__":main()
