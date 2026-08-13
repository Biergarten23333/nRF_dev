#!/usr/bin/env python3
"""One-open interactive BSFC2CC black-box frame-binding capture."""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, resolve_fusion_port
from v47_c2cc_continuous_capture import LiveCatchupDetector, formal_start_disposition
from v47_c2cc_frame_binding import FROZEN_CONFIG, G_MPS2, principal_direction
from v47_uwb_position_replay import load_solver, validate_anchor_slot_identity

ROOT=Path(__file__).resolve().parents[2]
NODE="BSFC2CC"
LAYOUT=ROOT/"B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
ABORTS={"ABORT_CAPTURE","停止"}


def wall(): return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(4<<20),b""): h.update(chunk)
    return h.hexdigest()


def atomic(path,value):
    temporary=path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(value,indent=2,sort_keys=True)+"\n")
    os.replace(temporary,path)


class TokenInbox:
    def __init__(self):
        self.q=queue.Queue();threading.Thread(target=self._run,daemon=True).start()
    def _run(self):
        for line in sys.stdin:
            self.q.put((line.rstrip("\n"),time.monotonic(),wall()))


class Recorder:
    def __init__(self,root):
        self.root=root;self.rawdir=root/"continuous_raw";self.rawdir.mkdir(parents=True)
        self.cdc=(self.rawdir/"fusion_cdc.log").open("x",buffering=1)
        self.raw=(self.rawdir/"fusion_host_raw.cobs.bin").open("xb",buffering=0)
        self.index=(self.rawdir/"consumption_index.jsonl").open("x",buffering=1)
        models,layout_io,c_solver=load_solver("UWB_TAG_T4")
        self.models=models;self.solver=c_solver.TagPositionSolver(layout_io.load_layout_json(LAYOUT),models.SolverConfig(method="T4"))
        self.channel=None;self.open_count=0;self.record_index=0;self.phase="STARTUP_PREFIX"
        self.imu=[];self.uwb=[];self.positions=[];self.events=[];self.phase_counts={}
        self.last_imu=None;self.last_uwb=None;self.last_imu_us=None;self.last_uwb_us=None
        self.aborted=False;self.close_drain={};self.final_health={}
    def open(self):
        self.open_wall=wall();self.open_mono=time.monotonic();port=resolve_fusion_port(None)
        self.channel=ThreadedLineChannel(port,self.cdc,"FUSION",decoded_queue_records=1048576,
            backlog_red_records=131072,raw_backlog_red_bytes=131072,stall_red_s=2,raw_file=self.raw)
        self.channel.transport_mode="binary";self.channel.text_pending.clear();self.open_count=1;self.port=str(port)
    def marker(self,name,extra=None):
        health=self.channel.health_snapshot()
        return {"name":name,"wall":wall(),"monotonic":time.monotonic(),
                "raw_byte_offset":health["raw_bytes_submitted"],"decoded_record_index":health["decoded_records"],
                "consumed_record_index":self.record_index,"health":health,**(extra or {})}
    def consume(self,deadline):
        line=self.channel.read(deadline)
        if not line:return None
        mono=time.monotonic();self.record_index+=1;fields=parse_fields(line);health=self.channel.health_snapshot()
        self.index.write(json.dumps({"record_index":self.record_index,"consume_monotonic":mono,
            "raw_bytes_submitted":health["raw_bytes_submitted"],"phase":self.phase,"line":line},separators=(",",":"))+"\n")
        counts=self.phase_counts.setdefault(self.phase,{"imu_samples":0,"imu_records":0,"uwb_sweeps":0,
            "imu_gap_events":0,"uwb_gap_events":0,"timestamp_reversals":0,"duplicates":0})
        if fields.get("name")==NODE and line.startswith("FUSION_IMU "):
            seq=int(fields["seq"],0);n=int(fields["n"],0);base=int(fields["base_us"],0)
            counts["imu_records"]+=1;counts["imu_samples"]+=n
            if self.last_imu is not None:
                expected=(self.last_imu[0]+self.last_imu[1])&0xffff
                if seq==self.last_imu[0]:counts["duplicates"]+=1
                elif seq!=expected:counts["imu_gap_events"]+=1
            if self.last_imu_us is not None and base<=self.last_imu_us:counts["timestamp_reversals"]+=1
            for sample in fields["samples"].split(";"):
                value=[int(x,0) for x in sample.split(",")]
                if len(value)!=7:continue
                self.imu.append({"consume_mono":mono,"hardware_us":base+value[0],"seq":seq,
                    "accel_mps2":np.asarray(value[1:4],float)/2048.*G_MPS2,
                    "gyro_dps":np.asarray(value[4:7],float)/16.384})
            self.last_imu=(seq,n);self.last_imu_us=base
        elif fields.get("name")==NODE and line.startswith("FUSION_UWB "):
            sweep=int(fields["sweep"],0);strobe=int(fields["strobe_us"],0);counts["uwb_sweeps"]+=1
            if self.last_uwb is not None:
                if sweep==self.last_uwb:counts["duplicates"]+=1
                elif sweep!=((self.last_uwb+1)&0xffffffff):counts["uwb_gap_events"]+=1
            if self.last_uwb_us is not None and strobe<=self.last_uwb_us:counts["timestamp_reversals"]+=1
            record={"consume_mono":mono,"hardware_us":strobe,"sweep":sweep,"fields":fields};self.uwb.append(record)
            position=self._solve(fields,mono,strobe,sweep)
            if position is not None:self.positions.append(position)
            self.last_uwb=sweep;self.last_uwb_us=strobe
        if line.startswith(("FUSION_CONNECTED ","FUSION_DISCONNECTED ")) or "RESET" in line:
            self.events.append({"wall":wall(),"monotonic":mono,"line":line})
        return line
    def _solve(self,f,mono,strobe,sweep):
        try:
            aids=tuple(int(x,0) for x in f["anchor_id"].split(","));validate_anchor_slot_identity(aids)
            ranges=[int(x,0) for x in f["range_mm"].split(",")]
            quality=[float(x) for x in f["quality"].split(",")];mask=int(f.get("valid_mask",f["valid"]),0)
            observations=tuple(self.models.Observation(anchor_id=aids[i],range_mm=float(ranges[i]),
                quality_percent=quality[i],status="O") for i in range(8)
                if mask&(1<<i) and 0<ranges[i]<0xffff)
            frame=self.models.Frame(tag=NODE,sweep=sweep,host_elapsed_s=mono-self.open_mono,
                host_epoch_s=time.time(),observations=observations,imu=None)
            result=self.solver.solve_frame(frame)
            if result is None:return None
            return {"consume_mono":mono,"hardware_us":strobe,"sweep":sweep,
                    "position_m":np.array([result.x_mm,result.y_mm,result.z_mm],float)/1000.,
                    "residual_rms_mm":float(result.residual_rms_mm),"anchors_used":int(result.anchors_used)}
        except Exception:return None
    def drain_for(self,seconds,phase):
        self.phase=phase;end=time.monotonic()+seconds
        while not self.aborted and time.monotonic()<end:self.consume(min(end,time.monotonic()+.1))
    def close(self):
        if self.channel:
            self.close_drain=self.channel.quiesce_reader_and_drain("frame_binding_close")
            self.channel.close();self.final_health=self.channel.health_snapshot()
        self.index.close();self.raw.close();self.cdc.close()


class Protocol:
    def __init__(self,recorder,inbox,root):
        self.r=recorder;self.inbox=inbox;self.actions=[];self.instructions=[]
        self.file=(root/"OPERATOR_ACTIONS.jsonl").open("x",buffering=1)
    def wait(self,instruction,expected,step):
        issued=self.r.marker("INSTRUCTION_"+step,{"instruction":instruction,"expected_token":expected})
        self.instructions.append(issued);print(instruction,flush=True);print("精确 token："+expected,flush=True)
        while not self.r.aborted:
            self.r.consume(time.monotonic()+.1)
            try:token,mono,token_wall=self.inbox.q.get_nowait()
            except queue.Empty:continue
            disposition="ABORT" if token in ABORTS else "ACCEPT" if token==expected else "REJECT"
            row={"step":step,"token":token,"expected":expected,"disposition":disposition,
                 "wall":token_wall,"monotonic":mono,"marker":self.r.marker("TOKEN_"+step)}
            self.actions.append(row);self.file.write(json.dumps(row,separators=(",",":"))+"\n")
            if disposition=="ABORT":self.r.aborted=True;return row
            if disposition=="REJECT":print("token 不匹配，仍等待精确 token："+expected,flush=True);continue
            return row
    def close(self):self.file.close()


def segment(rec,start,end):
    imu=[x for x in rec.imu if start<=x["consume_mono"]<=end]
    pos=[x for x in rec.positions if start<=x["consume_mono"]<=end]
    return imu,pos


def action_quality(rec,start,end,kind,reference_direction=None):
    imu,pos=segment(rec,start,end);duration=end-start
    out={"duration_s":duration,"imu_samples":len(imu),"t4_solutions":len(pos),"kind":kind}
    checks={"duration":duration>=FROZEN_CONFIG.minimum_action_duration_s,
            "imu_samples":len(imu)>=FROZEN_CONFIG.minimum_imu_samples,
            "t4_solutions":len(pos)>=FROZEN_CONFIG.minimum_t4_solutions}
    if imu:
        accel=np.asarray([x["accel_mps2"] for x in imu]);gyro=np.linalg.norm([x["gyro_dps"] for x in imu],axis=1)
        baseline=np.median(accel,axis=0);out["dynamic_accel_p95_mps2"]=float(np.quantile(np.linalg.norm(accel-baseline,axis=1),.95))
        out["gyro_p95_dps"]=float(np.quantile(gyro,.95));checks["dynamic_acceleration"]=out["dynamic_accel_p95_mps2"]>=FROZEN_CONFIG.minimum_dynamic_acceleration_mps2
        checks["limited_rotation"]=out["gyro_p95_dps"]<=FROZEN_CONFIG.maximum_translation_gyro_p95_dps
    else:checks.update(dynamic_acceleration=False,limited_rotation=False)
    if pos:
        direction,explained,span=principal_direction(np.asarray([x["position_m"] for x in pos]))
        out.update(direction_v4=direction.tolist(),direction_explained=explained,span_m=span)
        checks["displacement"]=span>=FROZEN_CONFIG.minimum_displacement_m
        checks["direction_explained"]=explained>=FROZEN_CONFIG.minimum_direction_explained
        if reference_direction is not None:
            angle=math.degrees(math.acos(float(np.clip(abs(direction@reference_direction),-1,1))))
            out["noncollinear_angle_deg"]=angle;checks["noncollinear"]=angle>=FROZEN_CONFIG.minimum_horizontal_angle_deg
    else:checks.update(displacement=False,direction_explained=False)
    out["checks"]=checks;out["accepted"]=all(checks.values());return out


INSTRUCTIONS={
"MOUNT_A_READY":"把 BSFC2CC 牢固固定在非金属便携载体上，方向随意；整块载体放稳、保持静止。完成后发送：",
"A_VERTICAL_START":"保持载体姿态尽量不变，准备把整个载体明显抬高、短暂停、放低、短暂停，约重复六次。开始动作前发送：",
"A_HORIZONTAL_1_START":"保持姿态，沿一条水平直线往返约六次，距离尽量明显并在两端短暂停。开始前发送：",
"A_HORIZONTAL_2_START":"换一条与上一条明显不同的水平直线，保持姿态往返约六次。开始前发送：",
"A_VALIDATION_START":"做一条新的混合平移路径：包含水平、垂直、启停，并至少有一次斜线或 L 形；尽量不转动载体。开始前发送：",
"A_ROTATION_START":"把载体保持在近似同一位置，缓慢向多个任意方向倾斜和旋转，中间可短暂停，最后放稳。开始前发送：",
"REMOUNT_B_START":"保持采集不断开。现在准备拆松 BSFC2CC，并用明显不同的任意方向重新牢固安装。动手前发送：",
"MOUNT_B_READY":"重新安装完成后，把载体放稳并保持静止。发送：",
"B_VERTICAL_START":"保持新安装姿态，明显抬高/放低并短暂停，约重复六次。开始前发送：",
"B_HORIZONTAL_1_START":"保持姿态，沿第一条水平直线往返约六次。开始前发送：",
"B_HORIZONTAL_2_START":"换一条明显不同的水平直线往返约六次。开始前发送：",
"B_VALIDATION_START":"做新的水平+垂直+斜线或 L 形混合平移，尽量不转动载体。开始前发送：",
"B_ROTATION_START":"在近似同一位置缓慢做多方向倾斜和旋转，最后放稳。开始前发送：",
"STOP_CAPTURE":"所需证据已经记录完毕。保持硬件不变；确认可以结束这一条连续采集后发送：",
}


def run_translation(proto,prefix,label,reference=None):
    start_token=f"{prefix}_{label}_START";done_token=f"{prefix}_{label}_DONE"
    start=proto.wait(INSTRUCTIONS[start_token],start_token,start_token)
    if proto.r.aborted:return None
    done=proto.wait("完成动作并把载体放稳后发送：",done_token,done_token)
    proto.r.drain_for(2,"POST_"+done_token)
    quality=action_quality(proto.r,start["monotonic"],done["monotonic"],label,reference)
    attempt={"start":start,"done":done,"quality":quality,"fit_eligible":quality["accepted"]}
    proto.actions.append({"step":done_token+"_QUALITY","quality":quality})
    if quality["accepted"]:
        print(f"{done_token} 客观检查通过。",flush=True);return attempt
    retry_start=f"{prefix}_{label}_RETRY_START";retry_done=f"{prefix}_{label}_RETRY_DONE"
    print(f"{done_token} 证据不足，原尝试已完整保留；原因："+
          ", ".join(k for k,v in quality["checks"].items() if not v),flush=True)
    rs=proto.wait("请按同一动作重做；开始前发送：",retry_start,retry_start)
    if proto.r.aborted:return attempt
    rd=proto.wait("完成重做并放稳后发送：",retry_done,retry_done);proto.r.drain_for(2,"POST_"+retry_done)
    rq=action_quality(proto.r,rs["monotonic"],rd["monotonic"],label,reference)
    retry={"start":rs,"done":rd,"quality":rq,"fit_eligible":rq["accepted"]}
    proto.actions.append({"step":retry_done+"_QUALITY","quality":rq})
    return retry


def require_fit_eligible(attempt, label):
    if attempt is None or not attempt.get("fit_eligible",False):
        raise RuntimeError(f"PROTOCOL_BLOCKED_INSUFFICIENT_EXCITATION:{label}")


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--out-dir",type=Path,required=True);args=parser.parse_args()
    root=args.out_dir;root.mkdir(parents=True,exist_ok=False)
    code=[Path(__file__),Path(__file__).with_name("v47_c2cc_frame_binding.py")]
    frozen={"schema":"biospur-c2cc-frame-binding-pretoken-freeze-v1","frozen_wall":wall(),
        "git_head":subprocess.check_output(["git","rev-parse","HEAD"],cwd=ROOT,text=True).strip(),
        "config":dataclasses.asdict(FROZEN_CONFIG),"code_sha256":{str(p.relative_to(ROOT)):sha(p) for p in code},
        "geometry":{"path":str(LAYOUT.relative_to(ROOT)),"sha256":sha(LAYOUT)},
        "time_alignment":"COMMON_B306_HARDWARE_CLOCK_NO_OFFSET_NO_WARP"}
    atomic(root/"PRETOKEN_FROZEN_CONFIG.json",frozen)
    rec=Recorder(root);inbox=TokenInbox();proto=Protocol(rec,inbox,root);manifest={"schema":"biospur-c2cc-frame-binding-capture-v1"}
    def stop(_s,_f):rec.aborted=True
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    try:
        rec.open();manifest.update(serial_open_count=1,port=rec.port,collector_open_wall=rec.open_wall);print("CAPTURE_OPENED — STARTUP_PREFIX_RETAINED",flush=True)
        detector=LiveCatchupDetector();seconds=[];next_second=math.floor(time.monotonic())+1;bucket=[]
        while not rec.aborted:
            line=rec.consume(min(next_second,time.monotonic()+.1));now=time.monotonic()
            if line:
                f=parse_fields(line)
                if f.get("name")==NODE and line.startswith("FUSION_IMU "):bucket.append(("i",int(f["n"]),now*1000-int(f["master_ms"]),0,0))
                elif f.get("name")==NODE and line.startswith("FUSION_UWB "):bucket.append(("u",1,now*1000-int(f["master_ms"]),0,0))
            if now>=next_second:
                health=rec.channel.health_snapshot();offsets=[x[2] for x in bucket]
                serial_pending=health["serial_input_bytes"]
                row={"start_monotonic":next_second-1,"end_monotonic":next_second,
                    "imu_hz":sum(x[1] for x in bucket if x[0]=="i"),"uwb_hz":sum(x[1] for x in bucket if x[0]=="u"),
                    "imu_gap_events":0,"uwb_gap_events":0,"age_offset_median_ms":np.median(offsets).item() if offsets else None,
                    "decoded_queue_depth":health["decoded_queue_depth"],"raw_queue_depth":health["raw_queue_depth"],
                    # -1 is the channel's explicit FIONREAD-unavailable sentinel,
                    # not evidence of pending bytes.  Both decoded/raw queues
                    # remain mandatory zero/backlog gates.
                    "serial_input_bytes":0 if serial_pending<0 else serial_pending,
                    "serial_input_bytes_observed":serial_pending,
                    "serial_input_bytes_disposition":"UNAVAILABLE_NOT_BACKLOG" if serial_pending<0 else "MEASURED",
                    "timestamp_jump":False}
                ok,evidence=detector.update(row);row["live_evidence"]=evidence;seconds.append(row);bucket=[]
                disposition=formal_start_disposition(next_second-rec.open_mono,detector.stable_seconds,30,180)
                if next_second-rec.open_mono>=60 and disposition:
                    if disposition!="LIVE_CATCHUP_OBSERVED":
                        raise RuntimeError("LIVE_CDC_CATCHUP_NOT_OBSERVED_BEFORE_PRETOKEN_DEADLINE")
                    manifest["formal_t0"]=rec.marker("FORMAL_T0",{"catchup":disposition});manifest["live_catchup_seconds"]=seconds
                    atomic(root/"LIVE_CATCHUP_EVIDENCE.json",seconds);rec.phase="FORMAL_WAIT_MOUNT_A";print("FORMAL_T0 — PASSIVE_CAPTURE_ONLY",flush=True);break
                next_second+=1
        mounts={}
        if not rec.aborted:
            ready=proto.wait(INSTRUCTIONS["MOUNT_A_READY"],"MOUNT_A_READY","MOUNT_A_READY");rec.drain_for(5,"A_INITIAL_STATIONARY")
            mounts["A"]={"stationary_start":ready["monotonic"],"stationary_end":time.monotonic()}
        for prefix in ("A","B"):
            if rec.aborted:break
            if prefix=="B":
                proto.wait(INSTRUCTIONS["REMOUNT_B_START"],"REMOUNT_B_START","REMOUNT_B_START")
                ready=proto.wait(INSTRUCTIONS["MOUNT_B_READY"],"MOUNT_B_READY","MOUNT_B_READY");rec.drain_for(5,"B_INITIAL_STATIONARY")
                mounts["B"]={"stationary_start":ready["monotonic"],"stationary_end":time.monotonic()}
                ai,_=segment(rec,mounts["A"]["stationary_start"],mounts["A"]["stationary_end"]);bi,_=segment(rec,mounts["B"]["stationary_start"],mounts["B"]["stationary_end"])
                if ai and bi:
                    ga=np.median([x["accel_mps2"] for x in ai],axis=0);gb=np.median([x["accel_mps2"] for x in bi],axis=0)
                    mounts["gravity_change_deg"]=math.degrees(math.acos(float(np.clip(ga@gb/(np.linalg.norm(ga)*np.linalg.norm(gb)),-1,1))))
                    print(f"Mount B 原始重力方向相对 A 改变 {mounts['gravity_change_deg']:.1f}°（仅作可辨识度记录）。",flush=True)
            mounts[prefix]["vertical"]=run_translation(proto,prefix,"VERTICAL")
            if rec.aborted:break
            require_fit_eligible(mounts[prefix]["vertical"],f"{prefix}_VERTICAL")
            mounts[prefix]["horizontal_1"]=run_translation(proto,prefix,"HORIZONTAL_1")
            if rec.aborted:break
            require_fit_eligible(mounts[prefix]["horizontal_1"],f"{prefix}_HORIZONTAL_1")
            ref=np.asarray(mounts[prefix]["horizontal_1"]["quality"].get("direction_v4",[1,0,0]))
            mounts[prefix]["horizontal_2"]=run_translation(proto,prefix,"HORIZONTAL_2",ref)
            if rec.aborted:break
            require_fit_eligible(mounts[prefix]["horizontal_2"],f"{prefix}_HORIZONTAL_2")
            st=proto.wait(INSTRUCTIONS[f"{prefix}_VALIDATION_START"],f"{prefix}_VALIDATION_START",f"{prefix}_VALIDATION_START")
            if rec.aborted:break
            dn=proto.wait("完成混合路径并放稳后发送：",f"{prefix}_VALIDATION_DONE",f"{prefix}_VALIDATION_DONE");rec.drain_for(2,f"{prefix}_POST_VALIDATION")
            if rec.aborted:break
            mounts[prefix]["validation"]={"start":st,"done":dn,"fit_eligible":False,
                "quality":action_quality(rec,st["monotonic"],dn["monotonic"],"VALIDATION")}
            rs=proto.wait(INSTRUCTIONS[f"{prefix}_ROTATION_START"],f"{prefix}_ROTATION_START",f"{prefix}_ROTATION_START")
            if rec.aborted:break
            rd=proto.wait("完成多方向缓慢旋转并放稳后发送：",f"{prefix}_ROTATION_DONE",f"{prefix}_ROTATION_DONE")
            if rec.aborted:break
            rec.drain_for(6,f"{prefix}_FINAL_STATIONARY");mounts[prefix]["rotation"]={"start":rs,"done":rd,"final_stationary_end":time.monotonic(),"fit_eligible":False}
        if not rec.aborted:proto.wait(INSTRUCTIONS["STOP_CAPTURE"],"STOP_CAPTURE","STOP_CAPTURE")
        manifest["stop_reason"]="OPERATOR_STOP_CAPTURE" if not rec.aborted else "OPERATOR_ABORT"
    except Exception as error:
        manifest.update(stop_reason="INFRASTRUCTURE_STOP",error=f"{type(error).__name__}: {error}")
    finally:
        proto.close();rec.close();manifest.update(finalized_wall=wall(),serial_open_count=rec.open_count,
            mount_blocks=mounts if "mounts" in locals() else {},operator_actions=proto.actions,instructions=proto.instructions,
            live_catchup_seconds=seconds if "seconds" in locals() else [],
            phase_counts=rec.phase_counts,events=rec.events,close_drain=rec.close_drain,final_health=rec.final_health,
            raw_sha256=sha(rec.rawdir/"fusion_host_raw.cobs.bin"));atomic(root/"CAPTURE_MANIFEST.json",manifest)
        print(manifest["stop_reason"],flush=True)
    return 0 if manifest.get("stop_reason")=="OPERATOR_STOP_CAPTURE" else 2


if __name__=="__main__":raise SystemExit(main())
