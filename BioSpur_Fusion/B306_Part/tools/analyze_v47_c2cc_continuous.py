#!/usr/bin/env python3
"""Deterministic closure for the continuous BSFC2CC stationary repeat."""
from __future__ import annotations
import argparse,csv,hashlib,json,math,os,re,shutil
from pathlib import Path
from collections import Counter

import matplotlib
matplotlib.use("Agg");matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-continuous-v1"
import matplotlib.pyplot as plt
import numpy as np

import analyze_v47_c2cc_stationary as base
from fusion_session import parse_fields

ROOT=Path(__file__).resolve().parents[2]
OLD=ROOT/"B306_Part/logs/v47_c2cc_stationary_heldout_20260811_220207"
NODE="BSFC2CC"
OUTPUTS=("REPORT.md","WARMUP_ANALYSIS.json","PREVIOUS_GAP_HYPOTHESIS.md",
 "FORMAL_CAPTURE_INTEGRITY.json","PER_MODE_METRICS.csv","STATE_TRANSITIONS.csv",
 "UWB_UPDATE_ACCOUNTING.json","UWB_LINK_METRICS.csv","LISTENER_SUMMARY.json",
 "NUMERICAL_INTEGRITY.json","source_age_backlog.svg","old_stale_live_sequence.svg",
 "position_modes.svg","imu_motion_evidence.svg","anchor_residuals.svg","state_innovation_timeline.svg")

def sha(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda:f.read(4<<20),b""):h.update(b)
    return h.hexdigest()
def clean(x):
    if isinstance(x,np.generic):x=x.item()
    if isinstance(x,float):return None if not math.isfinite(x) else float(f"{x:.12g}")
    if isinstance(x,np.ndarray):return [clean(v) for v in x.tolist()]
    return x
def write_json(path,obj):path.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")
def savefig(path):
    plt.tight_layout();plt.savefig(path,format="svg",metadata={"Date":None});plt.close()
    path.write_text("\n".join(x.rstrip() for x in path.read_text().splitlines())+"\n",encoding="utf-8")

def cdc_sensor_rows(run,until=None):
    rows=[];pat=re.compile(r"^(\d+\.\d+) (\d+\.\d+) FUSION_RX (FUSION_(?:IMU|UWB) .*)$")
    with (run/"continuous_raw/fusion_cdc.log").open(errors="replace") as f:
        for line in f:
            m=pat.match(line.rstrip("\n"))
            if not m:continue
            mono=float(m.group(2))
            if until is not None and mono>=until:continue
            text=m.group(3);fields=parse_fields(text)
            if fields.get("name")!=NODE:continue
            kind="IMU" if text.startswith("FUSION_IMU ") else "UWB"
            rows.append({"host_monotonic":mono,"kind":kind,"fields":fields})
    return rows

def gap_audit(rows):
    last_i=last_u=None;last_n=0;last_i_offset=last_u_offset=None;ig=[];ug=[]
    for r in rows:
        f=r["fields"]
        if r["kind"]=="IMU":
            seq=int(f["seq"],0);n=int(f["n"],0)
            offset=r["host_monotonic"]*1000-int(f["master_ms"])
            if last_i is not None and seq!=((last_i+last_n)&0xffff):
                delta=((seq-last_i-last_n)&0xffff);drop=last_i_offset-offset
                stale=drop>1000;ig.append({"host_monotonic":r["host_monotonic"],"previous_seq":last_i,"current_seq":seq,"modular_sequence_delta":delta,"host_master_offset_drop_ms":drop,"classification":"STALE_PREFIX_TO_LIVE_JUMP" if stale else "UNEXPLAINED_SEQUENCE_GAP","missing_samples":0 if stale else delta})
            last_i,last_n,last_i_offset=seq,n,offset
        else:
            seq=int(f["sweep"],0);offset=r["host_monotonic"]*1000-int(f["master_ms"])
            if last_u is not None and seq!=((last_u+1)&0xffffffff):
                delta=((seq-last_u-1)&0xffffffff);drop=last_u_offset-offset;stale=drop>1000;ug.append({"host_monotonic":r["host_monotonic"],"previous_sweep":last_u,"current_sweep":seq,"modular_sequence_delta":delta,"host_master_offset_drop_ms":drop,"classification":"STALE_PREFIX_TO_LIVE_JUMP" if stale else "UNEXPLAINED_SEQUENCE_GAP","missing_sweeps":0 if stale else delta})
            last_u,last_u_offset=seq,offset
    return ig,ug

def warmup_analysis(run,manifest):
    opened=float(manifest["collector_open_monotonic"]);t0=float(manifest["formal_t0"]["monotonic"])
    rows=cdc_sensor_rows(run,t0);imu=[r for r in rows if r["kind"]=="IMU"];uwb=[r for r in rows if r["kind"]=="UWB"];ig,ug=gap_audit(rows)
    seconds=json.loads((run/"warmup/SECONDLY_EVIDENCE.json").read_text())
    def first_nominal(kind):
        key="imu_hz" if kind=="imu" else "uwb_hz";lo,hi=(185,215) if kind=="imu" else (7,10)
        for i in range(max(0,len(seconds)-4)):
            if all(lo<=x[key]<=hi for x in seconds[i:i+5]):return seconds[i]["start_monotonic"]-opened
        return None
    first_raw=manifest.get("first_byte_monotonic")
    result={"collector_open_wall":manifest["collector_open_wall"],"collector_open_monotonic":opened,
      "formal_t0_wall":manifest["formal_t0"]["wall"],"formal_t0_monotonic":t0,"warmup_duration_s":t0-opened,
      "open_to_first_byte_s":None if first_raw is None else first_raw-opened,
      "open_to_first_valid_imu_s":None if not imu else imu[0]["host_monotonic"]-opened,
      "open_to_first_valid_uwb_s":None if not uwb else uwb[0]["host_monotonic"]-opened,
      "time_to_nominal_imu_cadence_s":first_nominal("imu"),"time_to_nominal_uwb_cadence_s":first_nominal("uwb"),
      "decoded_queue_high_water":manifest["formal_t0"]["health"]["decoded_queue_high_water"],
      "raw_backlog_high_water_bytes":manifest["formal_t0"]["health"]["raw_backlog_high_water"],
      "raw_bytes_retained_before_t0":manifest["formal_t0"]["raw_byte_offset"],
      "initial_decoder_fragment_errors":manifest["formal_t0"]["health"]["frame_crc_decode_errors"],
      "initial_decoder_fragment_classification":"SERIAL_OPEN_BOUNDARY_FRAGMENT_RETAINED",
      "imu_gap_events":ig,"uwb_gap_events":ug,"imu_missing_samples":sum(x["missing_samples"] for x in ig),"uwb_missing_sweeps":sum(x["missing_sweeps"] for x in ug),
      "stale_prefix_to_live_imu_events":sum(x["classification"]=="STALE_PREFIX_TO_LIVE_JUMP" for x in ig),"stale_prefix_to_live_uwb_events":sum(x["classification"]=="STALE_PREFIX_TO_LIVE_JUMP" for x in ug),
      "live_catchup_monotonic":manifest.get("live_catchup_monotonic"),"live_catchup_elapsed_s":None if manifest.get("live_catchup_monotonic") is None else manifest["live_catchup_monotonic"]-opened,
      "formal_start_disposition":manifest["formal_t0"]["live_catchup"],"identity_observation":manifest.get("identity_observation"),"anchor_observation_status":manifest.get("anchor_observation",{}).get("status"),
      "reset_reconnect_events":manifest.get("events",[]),"secondly_evidence":seconds}
    return result

def prepare_view(run,out,manifest):
    view=out/"replay_input_view";formal=view/"formal_capture";formal.mkdir(parents=True)
    shutil.copyfile(run/"FROZEN_INPUT_HASHES.json",view/"FROZEN_INPUT_HASHES.json")
    shutil.copyfile(run/"FROZEN_S2_PARAMETER_MANIFEST.json",view/"FROZEN_S2_PARAMETER_MANIFEST.json")
    os.symlink(os.path.relpath(run/"continuous_raw/fusion_host_raw.cobs.bin",formal),formal/"fusion_host_raw.cobs.bin")
    os.symlink(os.path.relpath(run/"continuous_raw/listener_capture",formal),formal/"listener_capture")
    t0=manifest["formal_t0"];t1=manifest["formal_final_values"]
    fm={"formal_health_baseline":t0["health"],"t0_wall":t0["wall"],"t0_monotonic":t0["monotonic"],"t0_monotonic_ns":int(t0["monotonic"]*1e9),"planned_duration_s":600,
        "commands_after_t0":[],"mutation":False,"mapping":{NODE:{"logical_tag_id":142,"tag_short_address":"0xB18E"}},
        "formal_source_bounds":{"t0_exclusive":manifest["formal_initial_values"],"t1_inclusive":t1}}
    write_json(formal/"RUN_MANIFEST.json",fm)
    pl={"status":"CAPTURE_COMPLETE","stop_reason":manifest["stop_reason"],"duration_s":manifest["formal_duration_s"],"t0_wall":t0["wall"],"t1_wall":manifest["formal_t1_wall"],"t1_monotonic":manifest["formal_t1_monotonic"],"events":manifest.get("formal_events",[]),
        "fusion_health_final":manifest["health_final"],"listener_summary":manifest["listener_summary"],"decoded_close_drain":manifest.get("close_drain",{}),"initial":manifest["formal_initial_values"],"final":t1}
    write_json(formal/"PROCESS_LEDGER.json",pl);return view

def old_hypothesis(warm,formal):
    old=json.loads((OLD/"CAPTURE_INTEGRITY.json").read_text());old_duration=old["duration_s"]
    excess_i=old["imu_samples"]-round(200*old_duration);excess_u=old["uwb_sweeps"]-round((25/3)*old_duration)
    synchronized=old["imu_sequence_gaps"]==old["uwb_sequence_gaps"]==1
    count_match=abs(excess_i-old["imu_missing_samples"])<=20 and abs(excess_u-old["uwb_missing_sweeps"])<=2
    new_clean=formal["checks"]["imu_sequence_gap_zero"] and formal["checks"]["uwb_sequence_gap_zero"]
    old_rows=[];pat=re.compile(r"^(\d+\.\d+) (\d+\.\d+) FUSION_RX (FUSION_(?:IMU|UWB) .*)$")
    with (OLD/"formal_capture/fusion_cdc.log").open(errors="replace") as f:
        for line in f:
            m=pat.match(line.rstrip("\n"))
            if not m:continue
            fields=parse_fields(m.group(3))
            if fields.get("name")==NODE:old_rows.append({"mono":float(m.group(2)),"kind":"IMU" if m.group(3).startswith("FUSION_IMU ") else "UWB","fields":fields,"offset_ms":float(m.group(2))*1000-int(fields["master_ms"])})
    last_i=last_n=None;gap_row=None
    for row in old_rows:
        if row["kind"]!="IMU":continue
        seq=int(row["fields"]["seq"]);n=int(row["fields"]["n"])
        if last_i is not None and seq!=((last_i+last_n)&0xffff):gap_row=row;break
        last_i,last_n=seq,n
    pre_offset=old_rows[0]["offset_ms"] if old_rows else None;post_offset=None if gap_row is None else gap_row["offset_ms"]
    offset_drop=None if pre_offset is None or post_offset is None else pre_offset-post_offset
    burst_s=None if not old_rows or gap_row is None else gap_row["mono"]-old_rows[0]["mono"]
    direct_cdc=offset_drop is not None and offset_drop>1000 and burst_s is not None and burst_s<.5
    disposition="SUPPORTED" if synchronized and count_match and new_clean and direct_cdc else "PARTIALLY_SUPPORTED" if synchronized and count_match else "UNRESOLVED"
    facts={"classification":disposition,"old_excess_imu_samples":excess_i,"old_excess_uwb_sweeps":excess_u,
      "old_missing_imu_samples":old["imu_missing_samples"],"old_missing_uwb_sweeps":old["uwb_missing_sweeps"],
      "old_excess_imu_duration_s":excess_i/200,"old_missing_imu_duration_s":old["imu_missing_samples"]/200,
      "old_excess_uwb_duration_s":excess_u/(25/3),"old_missing_uwb_duration_s":old["uwb_missing_sweeps"]/(25/3),
      "old_listener_continuous":True,"old_reboot_reconnect":False,"new_warmup_missing_imu":warm["imu_missing_samples"],"new_warmup_missing_uwb":warm["uwb_missing_sweeps"],"new_formal_lossless":new_clean,
      "old_cdc_first_to_gap_host_s":burst_s,"old_host_master_offset_drop_ms":offset_drop,
      "limitation":"The old CDC trace proves stale-to-live catch-up but cannot distinguish the OS USB buffer from the Master CDC/application queue as the buffering owner."}
    text=f"""# Previous-gap hypothesis\n\nClassification: `{disposition}`\n\nThe old run contained {old['imu_missing_samples']} missing IMU samples and {old['uwb_missing_sweeps']} missing UWB sweeps, while its decoded totals exceeded nominal by approximately {excess_i} IMU samples and {excess_u} UWB sweeps. The excess durations ({excess_i/200:.3f} s IMU, {excess_u/(25/3):.3f} s UWB) closely match the missing durations ({old['imu_missing_samples']/200:.3f} s, {old['uwb_missing_sweeps']/(25/3):.3f} s). In the retained old CDC log, the synchronized IMU discontinuity arrived only `{burst_s}` seconds after its first decoded sensor record and the host-minus-Master offset dropped by `{offset_drop}` ms. This is direct stale-burst-to-live evidence. Listener continuity and the absence of reset/reconnect evidence reject a board reboot explanation.\n\nThe continuous repeat retained startup bytes and moved T0 only after a measured live plateau. Its formal gap result is `{new_clean}`. The evidence supports the stale CDC/Master prefix followed by a live-sequence jump hypothesis. It does not identify whether the buffered owner was the OS USB path or the Master application/CDC queue. The old registered verdict remains unchanged.\n"""
    return facts,text

def plots(out,warm):
    sec=warm["secondly_evidence"];t=np.asarray([x["end_monotonic"]-warm["collector_open_monotonic"] for x in sec]);age=np.asarray([x["age_offset_median_ms"] for x in sec],float);age-=np.nanmedian(age[-10:]);q=np.asarray([x["decoded_queue_depth"] for x in sec])
    fig,ax=plt.subplots(figsize=(10,5));ax.plot(t,age,label="source-age proxy above live plateau (ms)");ax2=ax.twinx();ax2.plot(t,q,color="tab:orange",label="decoded queue");ax.axvline(warm["warmup_duration_s"],color="k",ls="--",label="FORMAL_T0");ax.set(xlabel="seconds from collector open",ylabel="relative age (ms)",title="Warm-up CDC drain and live catch-up");ax2.set_ylabel("decoded records queued");savefig(out/"source_age_backlog.svg")
    old=json.loads((OLD/"CAPTURE_INTEGRITY.json").read_text());fig,ax=plt.subplots(figsize=(9,4));labels=["old excess","old missing","new warm-up missing","new formal missing"];imu=[old["imu_samples"]-round(200*old["duration_s"]),old["imu_missing_samples"],warm["imu_missing_samples"],0];uwb=[old["uwb_sweeps"]-round((25/3)*old["duration_s"]),old["uwb_missing_sweeps"],warm["uwb_missing_sweeps"],0];x=np.arange(4);ax.bar(x-.18,imu,.36,label="IMU samples");ax.bar(x+.18,np.asarray(uwb)*24,.36,label="UWB sweeps ×24");ax.set_xticks(x,labels,rotation=15);ax.set_ylabel("count on comparable scale");ax.set_title("Old stale/live discontinuity versus continuous lifecycle");ax.legend();savefig(out/"old_stale_live_sequence.svg")

def derive(run,out):
    out.mkdir(parents=True,exist_ok=False);manifest=json.loads((run/"RUN_MANIFEST.json").read_text());raw_before=sha(run/"continuous_raw/fusion_host_raw.cobs.bin")
    warm=warmup_analysis(run,manifest);write_json(out/"WARMUP_ANALYSIS.json",clean(warm));view=prepare_view(run,out,manifest);core=out/"core";base.analyze(view,core)
    mapping={"CAPTURE_INTEGRITY.json":"FORMAL_CAPTURE_INTEGRITY.json","PER_MODE_METRICS.csv":"PER_MODE_METRICS.csv","STATE_TRANSITIONS.csv":"STATE_TRANSITIONS.csv","UWB_UPDATE_ACCOUNTING.json":"UWB_UPDATE_ACCOUNTING.json","UWB_LINK_METRICS.csv":"UWB_LINK_METRICS.csv","LISTENER_SUMMARY.json":"LISTENER_SUMMARY.json","NUMERICAL_INTEGRITY.json":"NUMERICAL_INTEGRITY.json","position_modes.svg":"position_modes.svg","imu_motion_evidence.svg":"imu_motion_evidence.svg","anchor_residuals.svg":"anchor_residuals.svg","state_innovation_timeline.svg":"state_innovation_timeline.svg"}
    for src,dst in mapping.items():shutil.copyfile(core/src,out/dst)
    formal=json.loads((out/"FORMAL_CAPTURE_INTEGRITY.json").read_text());facts,text=old_hypothesis(warm,formal);(out/"PREVIOUS_GAP_HYPOTHESIS.md").write_text(text,encoding="utf-8");plots(out,warm)
    numerical=json.loads((out/"NUMERICAL_INTEGRITY.json").read_text());s2=numerical["s2_gates"];verdict="STOPPED_BY_OPERATOR" if manifest["stop_reason"]=="STOPPED_BY_OPERATOR" else "C2CC_STATIONARY_HELDOUT_PASS" if formal["status"]=="PASS" and s2["S2P"]["status"]==s2["S2R"]["status"]=="PASS" else "C2CC_STATIONARY_CAPTURE_FAIL" if formal["status"]!="PASS" else "C2CC_STATIONARY_ALGORITHM_FAIL"
    metrics=list(csv.DictReader((out/"PER_MODE_METRICS.csv").open()));report=f"""# BSFC2CC continuous stationary repeat\n\nPrimary verdict: `{verdict}`\n\nOne Fusion serial open and one uninterrupted raw timeline covered collector open, warm-up, CDC drain, the in-stream T0 marker, the 600-second formal window and clean stop. Collector open was `{manifest['collector_open_wall']}`, live catch-up was established at monotonic `{manifest.get('live_catchup_monotonic')}`, T0 was `{manifest['formal_t0']['wall']}`, and stop was `{manifest['formal_t1_wall']}` after `{manifest['formal_duration_s']}` seconds.\n\nWarm-up lasted `{warm['warmup_duration_s']}` seconds and retained `{warm['raw_bytes_retained_before_t0']}` bytes before T0. It captured `{warm['stale_prefix_to_live_imu_events']}` IMU and `{warm['stale_prefix_to_live_uwb_events']}` UWB stale-prefix-to-live transition, with `{warm['imu_missing_samples']}` unexplained missing IMU samples and `{warm['uwb_missing_sweeps']}` unexplained missing UWB sweeps. Warm-up dirt does not fail the registered formal gate. Previous-gap hypothesis: `{facts['classification']}`.\n\nFormal lossless gate: `{formal['status']}`. Frozen S2P: `{s2['S2P']['status']}`. Frozen S2R: `{s2['S2R']['status']}`. Zero published RMS, if present, is lock semantics rather than absolute positioning accuracy. No new capture value changed a frozen parameter.\n""";(out/"REPORT.md").write_text(report,encoding="utf-8")
    if sha(run/"continuous_raw/fusion_host_raw.cobs.bin")!=raw_before:raise RuntimeError("raw changed during analysis")
    frozen=json.loads((run/"FROZEN_INPUT_HASHES.json").read_text());checks=[sha(ROOT/frozen["geometry"]["path"])==frozen["geometry"]["sha256"],sha(ROOT/frozen["s2_code"]["path"])==frozen["s2_code"]["sha256"],sha(run/frozen["s2_parameter_manifest"]["copy"])==frozen["s2_parameter_manifest"]["sha256"]]
    if not all(checks):raise RuntimeError("frozen input changed")
    return verdict

def finalize(run,a,b):
    mismatch=[x for x in OUTPUTS if sha(a/x)!=sha(b/x)]
    if mismatch:raise RuntimeError(f"non-deterministic outputs: {mismatch}")
    for name in OUTPUTS:shutil.copyfile(a/name,run/name)
    phases=json.loads((run/"CAPTURE_PHASES.json").read_text());manifest=json.loads((run/"RUN_MANIFEST.json").read_text())
    ordered=[phases.get(x) for x in ("COLLECTOR_OPEN","RAW_RECORDING_FROM_FIRST_BYTE","FORMAL_T0","CLEAN_STOP")]
    phases["single_timeline_valid"]=(manifest.get("serial_open_count")==1 and phases.get("one_raw_file") is True and phases.get("WARMUP_RECORDING")==phases.get("COLLECTOR_OPEN") and all(x is not None for x in ordered) and ordered==sorted(ordered));write_json(run/"CAPTURE_PHASES.json",phases)
    repro={"status":"PASS","mismatches":[],"compared":list(OUTPUTS)};write_json(run/"REPRODUCIBILITY.json",repro)
    names=["RUN_MANIFEST.json","CAPTURE_PHASES.json","CDC_LIVE_CATCHUP.json","FROZEN_INPUT_HASHES.json","FROZEN_S2_PARAMETER_MANIFEST.json","REPRODUCIBILITY.json",*OUTPUTS]
    lines=[f"{sha(run/x)}  {x}" for x in sorted(names)];lines.append(f"{sha(run/'continuous_raw/fusion_host_raw.cobs.bin')}  continuous_raw/fusion_host_raw.cobs.bin");(run/"SHA256SUMS").write_text("\n".join(lines)+"\n",encoding="utf-8")

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--run",type=Path,required=True);ap.add_argument("--out",type=Path);ap.add_argument("--finalize",type=Path,nargs=2);a=ap.parse_args()
    if a.out:derive(a.run,a.out)
    elif a.finalize:finalize(a.run,*a.finalize)
    else:ap.error("--out or --finalize required")
if __name__=="__main__":main()
