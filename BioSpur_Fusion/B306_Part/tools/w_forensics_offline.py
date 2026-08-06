#!/usr/bin/env python3
"""Offline-only reconstruction for W_forensics_20260802."""
from __future__ import annotations

import csv, hashlib, json, shutil, statistics, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARC = ROOT / "UWB_Part/logs/relay8_2_20260802/hardware_arc"
S7 = ARC / "s6_s7_20260802_143622/s7"
OUT = ROOT / "UWB_Part/logs/W_forensics_20260802"
sys.path.insert(0, str(ROOT / "B306_Part/tools"))
import analyze_relay8_1_overnight as ana
from fusion_session import parse_fields

NODES = ("BSF3C79","BSFEC35","BSF44AD","BSF6C53","BSF8BC4","BSF1120","BSF31CC","BSFAA61","BSFB165","BSFC2CC")
PICKUP=(247939.533,247948.371); LOOP=(248550.028,248569.783)

def sha(p: Path) -> str:
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(8<<20),b""): h.update(b)
    return h.hexdigest()

def fields_in_window(path: Path, start: float, end: float):
    for host,line in ana.iter_fusion(path,start,end):
        yield host,line,parse_fields(line)

def main() -> int:
    cap=json.loads((S7/"s7_capture.json").read_text()); analysis=json.loads((S7/"FULL_LOAD_ANALYSIS.json").read_text())
    start,end=float(cap["started_monotonic"]),float(cap["ended_monotonic"])
    imu=defaultdict(list); uwb=defaultdict(list); qos=defaultdict(list); tele=defaultdict(list); queue=defaultdict(list); dis=[]
    for host,line,f in fields_in_window(S7/"fusion_cdc.log",start,end):
        n=f.get("name")
        if n not in NODES: continue
        if "FUSION_IMU " in line: imu[n].append((host,int(f["seq"],0),int(f["n"],0),int(f["base_us"],0)))
        elif "FUSION_UWB " in line:
            s=int(f["sweep"],0); uwb[n].append((host,s))
            if len(uwb[n])>1 and s<uwb[n][-2][1]: dis.append((n,uwb[n][-2],uwb[n][-1]))
        elif "FUSION_QOS " in line: qos[n].append((host,{k:int(f.get(k,"0"),0) for k in ("reports","crc_ok","crc_error","rx_timeout","event_gaps","first_event","last_event")}))
        elif "FUSION_TELEMETRY " in line: tele[n].append((host,{k:int(f.get(k,"0"),0) for k in ("node_ms","crc","header","reorder","last_sweep","uart_restarts","relay_timeout","drop_err","uart_err")}))
        elif "FUSION_QUEUE " in line: queue[n].append((host,{k:int(f.get(k,"0"),0) for k in ("q_drop_imu","q_drop_uwb","abort_imu","abort_uwb")}))

    events=[]
    for n in NODES:
        for a,b in zip(imu[n],imu[n][1:]):
            missing=(b[1]-((a[1]+a[2])&0xffff))&0xffff
            if not 0<missing<0x8000: continue
            expected_records=(missing + max(1,a[2])-1)//max(1,a[2])
            first_missing_host=a[0]+.05
            gap=max(0.0,(b[3]-a[3])/1e6-a[2]*.005)
            def off(interval):
                return first_missing_host-interval[1] if first_missing_host>interval[1] else first_missing_host-interval[0] if first_missing_host<interval[0] else 0.0
            events.append(dict(node=n,first_missing_host_monotonic=f"{first_missing_host:.6f}",last_missing_host_monotonic=f"{b[0]:.6f}",missing_records=expected_records,missing_samples=missing,gap_duration_s=f"{gap:.6f}",pickup_offset_s=f"{off(PICKUP):.6f}",arm_loop_offset_s=f"{off(LOOP):.6f}"))
    with (OUT/"loss_events.csv").open("w",newline="") as f:
        w=csv.DictWriter(f,fieldnames=events[0]); w.writeheader(); w.writerows(events)

    # Independent observer periods for slot/tag 10 (BSF44AD, on-air 0xb10a).
    reset=next(x for x in dis if x[0]=="BSF44AD"); rt=reset[2][0]
    observer={}
    for p in sorted((S7/"continuous_listener_capture/listeners").glob("*.jsonl")):
        ts=[]
        with p.open(errors="replace") as f:
            for line in f:
                j=json.loads(line)
                if j.get("kind")=="LPD":
                    tag=j.get("fields",{}).get("tag_id")
                    host=j["arrival_monotonic_ns"]/1e9
                    # Before reset the configured on-air tag is 3.  After the
                    # reset the observer parser reports the default tag 10;
                    # retain that independently observed identity rather than
                    # substituting the downstream B306 logical field (58).
                    if (host < rt and tag==3) or (host >= rt and tag==10):
                        ts.append((host,j.get("rx_unwrapped_ticks")))
        def period(xs):
            d=[(b[1]-a[1])/63_897_600_000 for a,b in zip(xs,xs[1:]) if a[1] is not None and b[1] is not None and .05<(b[1]-a[1])/63_897_600_000<.2]
            return statistics.median(d) if d else None
        before=[x for x in ts if rt-120<x[0]<rt-2]; after=[x for x in ts if rt+2<x[0]<rt+105]
        observer[p.name]={"before_s":period(before),"after_s":period(after),"before_n":len(before),"after_n":len(after)}
    (OUT/"observer_periods.json").write_text(json.dumps(observer,indent=2)+"\n")

    # Compact machine-readable facts used by the report.
    summaries={n:{"events":sum(e["node"]==n for e in events),"missing_samples":sum(int(e["missing_samples"]) for e in events if e["node"]==n)} for n in NODES}
    status=analysis["nodes"]["BSF44AD"]["status"]
    facts={"window":{"start":start,"end":end,"duration_s":end-start},"loss_summary":summaries,"counter_discontinuities":[{"node":n,"before_host":a[0],"before":a[1],"after_host":b[0],"after":b[1]} for n,a,b in dis],"bsf44ad_beacon":{"first":status["series"][0]["fields"],"last":status["series"][-1]["fields"],"reported_rx_delta":status["rx_delta"],"reported_miss_delta":status["miss_delta"]},"observer_periods":observer,"free_bytes":shutil.disk_usage("/mnt/nrf_ssd").free,"projected_8h_bytes":36_000_000_000}
    (OUT/"facts.json").write_text(json.dumps(facts,indent=2)+"\n")

    # Full arc inventory; hashes are required for every file actually read here.
    read=[S7/"s7_capture.json",S7/"FULL_LOAD_ANALYSIS.json",S7/"fusion_cdc.log",*sorted((S7/"continuous_listener_capture/listeners").glob("*.jsonl")),ROOT/"UWB_Part/reports/relay8_1_overnight_20260802/OVERNIGHT_REPORT.md",ARC/"s3_fix_verification_20260802_132357/S3_REPORT.md",ROOT/"B306_Part/host/fusion_master/src/main.c",ROOT/"UWB_Part/relay8_2-workspace/src/src/ss_twr_init.c",ROOT/"UWB_Part/relay8_2-workspace/src/apps/tag/src/uwb_tag_ble.c"]
    readset={p.resolve() for p in read if p.is_file()}
    with (OUT/"evidence_index.csv").open("w",newline="") as f:
        w=csv.writer(f); w.writerow(("path","bytes","sha256","read_this_round"))
        for p in sorted(ARC.rglob("*")):
            if p.is_file(): w.writerow((str(p.relative_to(ROOT)),p.stat().st_size,sha(p) if p.resolve() in readset else "",str(p.resolve() in readset).lower()))
        for p in read:
            if p.is_file() and not p.is_relative_to(ARC): w.writerow((str(p.relative_to(ROOT)),p.stat().st_size,sha(p),"true"))
    return 0
if __name__=="__main__": raise SystemExit(main())
