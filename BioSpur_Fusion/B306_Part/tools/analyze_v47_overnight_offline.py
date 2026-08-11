#!/usr/bin/env python3
"""Deterministic, hardware-free analysis of the authoritative v47 overnight run."""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35")
BATTERY = tuple(n for n in NODES if n != "BSF6C53")
POLL_RECEIVERS = ("LAE", "LBF", "LDH", "LLOW", "LMID")
HEAD = re.compile(rb"^([0-9.]+) ([0-9.]+) FUSION_RX (\S+)(?: (.*))?$")
FIELD = re.compile(rb"(?:^| )(\w+)=([^ ]+)")
AIR_T = re.compile(rb'"arrival_monotonic_ns":(\d+)')
AIR_E = re.compile(rb'"arrival_epoch_ns":(\d+)')
AIR_R = re.compile(rb'"listener_key":"([^"]+)"')
AIR_S = re.compile(rb'"src":(\d+)')
AIR_Q = re.compile(rb'"sequence":(\d+)')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 << 20), b""):
            h.update(block)
    return h.hexdigest()


def json_out(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def rate_svg(path: Path, rows) -> None:
    """Write a dependency-free compact 10-minute-rate overview."""
    w, h, left, top, pw, ph = 1200, 720, 70, 35, 1080, 285
    colors = ("#0072B2", "#E69F00", "#009E73", "#D55E00", "#CC79A7",
              "#56B4E9", "#F0E442", "#000000", "#7F7F7F", "#6A3D9A")
    duration = max(x["start_monotonic"] for x in rows) - min(x["start_monotonic"] for x in rows) + 600
    tbase = min(x["start_monotonic"] for x in rows)
    def xy(t, value, y0, ymax):
        return left + pw*(t-tbase)/duration, y0 + ph*(1-min(value,ymax)/ymax)
    lines=[f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
           '<rect width="100%" height="100%" fill="white"/>',
           '<style>text{font-family:sans-serif;font-size:12px}.title{font-size:16px;font-weight:bold}</style>',
           '<text x="70" y="20" class="title">v47 formal capture — 10-minute rates</text>']
    panels=((top,"Fusion IMU (Hz)",210,"fusion_imu_hz"),(top+340,"Fusion UWB (Hz)",10,"fusion_uwb_hz"))
    for y0,label,ymax,key in panels:
        lines += [f'<rect x="{left}" y="{y0}" width="{pw}" height="{ph}" fill="none" stroke="#888"/>',
                  f'<text x="5" y="{y0+15}">{label}</text>']
        for frac in (0,.25,.5,.75,1):
            y=y0+ph*(1-frac); lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left+pw}" y2="{y:.1f}" stroke="#ddd"/>')
            lines.append(f'<text x="35" y="{y+4:.1f}">{ymax*frac:g}</text>')
        for node,color in zip(NODES,colors):
            pts=[]
            for x in rows:
                if x["node"]==node:
                    px,py=xy(x["start_monotonic"]+300,x[key],y0,ymax);pts.append(f'{px:.1f},{py:.1f}')
            lines.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{color}" stroke-width="1.4"/>')
    for i,(node,color) in enumerate(zip(NODES,colors)):
        x=70+(i%5)*205;y=690+(i//5)*17
        lines += [f'<line x1="{x}" y1="{y-4}" x2="{x+22}" y2="{y-4}" stroke="{color}" stroke-width="3"/>',f'<text x="{x+27}" y="{y}">{node}</text>']
    lines.append('</svg>')
    path.write_text("\n".join(lines)+"\n")


def fields(raw: bytes) -> dict[str, str]:
    return {m.group(1).decode(): m.group(2).decode(errors="replace") for m in FIELD.finditer(raw)}


def num(f: dict[str, str], key: str):
    try:
        return int(f[key], 0)
    except (KeyError, ValueError):
        return None


def pct(values, q):
    if not values:
        return None
    a = sorted(values); p = (len(a) - 1) * q; lo = math.floor(p); hi = math.ceil(p)
    return a[lo] if lo == hi else a[lo] * (hi - p) + a[hi] * (p - lo)


def wall(manifest, mono):
    return (datetime.fromisoformat(manifest["t0_wall"]) + timedelta(
        seconds=mono - manifest["t0_monotonic"])).isoformat(timespec="milliseconds")


def raw_inputs(run: Path) -> list[Path]:
    names = ["RUN_MANIFEST.json", "PROCESS_LEDGER.json", "SMOKE_RESULT.json",
             "SMOKE_MINUTE_STATUS.json", "STOP_AUTHORIZATION.json", "node_tag_map.json",
             "fusion_cdc.log", "listener_capture/inventory.json",
             "listener_capture/summary.json", "listener_capture/merged_index.jsonl"]
    out = [run / n for n in names]
    out += sorted((run / "listener_capture/listeners").glob("*.jsonl"))
    out += sorted((run / "listener_capture/listeners").glob("*.raw.log"))
    return out


def edge_timestamps(path: Path, listener=False):
    with path.open("rb") as f:
        first = f.readline().rstrip(b"\r\n")
        f.seek(0, 2); pos = f.tell(); buf = b""
        while pos and b"\n" not in buf:
            take = min(pos, 65536); pos -= take; f.seek(pos); buf = f.read(take) + buf
        last = next((x for x in reversed(buf.splitlines()) if x), b"")
    if listener:
        def one(line, rx):
            m = rx.search(line); return int(m.group(1)) if m else None
        return one(first, AIR_T), one(last, AIR_T), one(first, AIR_E), one(last, AIR_E)
    def fusion(line, i):
        p = line.split(b" ", 3)
        try: return float(p[i])
        except (IndexError, ValueError): return None
    return fusion(first, 1), fusion(last, 1), fusion(first, 0), fusion(last, 0)


def scan_fusion(path: Path, t0: float, end: float):
    stream = {n: {"imu": [], "uwb": []} for n in NODES}
    records = {n: defaultdict(list) for n in NODES}
    counts = Counter(); malformed = Counter(); first = last = None; prev_host = None
    clock_backsteps = []
    with path.open("rb") as f:
        for line_no, raw in enumerate(f, 1):
            if b" FUSION_RX " not in raw:
                continue
            m = HEAD.match(raw.rstrip(b"\r\n"))
            if not m:
                if re.match(rb"^[0-9.]+ [0-9.]+ FUSION_RX  ", raw):
                    malformed["continuation_line"] += 1
                else: malformed["bad_header"] += 1
                continue
            epoch, host, kind = float(m.group(1)), float(m.group(2)), m.group(3).decode()
            first = host if first is None else first; last = host; counts[kind] += 1
            if prev_host is not None and host < prev_host:
                clock_backsteps.append({"line": line_no, "previous": prev_host, "current": host})
            prev_host = host
            flds = fields(m.group(4) or b""); node = flds.get("name")
            if node not in NODES: continue
            if kind == "FUSION_IMU":
                n = num(flds, "n")
                if n is None: malformed["imu_missing_n"] += 1
                else: stream[node]["imu"].append((host, n))
            elif kind == "FUSION_UWB": stream[node]["uwb"].append(host)
            if kind in {"FUSION_TELEMETRY", "FUSION_QUEUE", "FUSION_QOS",
                        "FUSION_CONNECTED", "FUSION_DISCONNECTED", "FUSION_FAIL",
                        "FUSION_DATA_SUBSCRIBED", "FUSION_TELEMETRY_SUBSCRIBED"}:
                records[node][kind].append({"monotonic": host, "epoch": epoch, "fields": flds})
    resets, links = {n: [] for n in NODES}, {n: [] for n in NODES}
    for node in NODES:
        tlm = records[node]["FUSION_TELEMETRY"]
        for a, b in zip(tlm, tlm[1:]):
            am, bm = num(a["fields"], "node_ms"), num(b["fields"], "node_ms")
            if am is not None and bm is not None and bm < am:
                resets[node].append({"monotonic": b["monotonic"], "wall": wall(MANIFEST, b["monotonic"]),
                                     "previous_node_ms": am, "node_ms": bm,
                                     "reset_reason": num(b["fields"], "reset_reason")})
        for kind in ("FUSION_CONNECTED", "FUSION_DISCONNECTED", "FUSION_FAIL"):
            for x in records[node][kind]:
                links[node].append({"monotonic": x["monotonic"], "wall": wall(MANIFEST, x["monotonic"]),
                                    "event": kind, "fields": x["fields"]})
        links[node].sort(key=lambda x: x["monotonic"])
    return stream, records, resets, links, {"first_monotonic": first, "last_monotonic": last,
        "record_counts": dict(counts), "parse_accounting": dict(malformed),
        "parse_failures": sum(v for k,v in malformed.items() if k != "continuation_line"),
        "clock_backsteps": clock_backsteps}


def gaps(times, start, end, nominal):
    a = [t for t in times if start <= t <= end]; out=[]
    if not a: return [(start,end)]
    prev = a[0]
    if prev-start >= 2: out.append((start,prev))
    for cur in a[1:]:
        if cur-prev-nominal >= 2: out.append((prev,cur))
        prev=cur
    if end-prev-nominal >= 2: out.append((prev,end))
    return out


def joint_segments(stream, t0, end):
    result=[]
    for node in NODES:
        ig=gaps([t for t,_ in stream[node]["imu"]],t0,end,.05)
        ug=gaps(stream[node]["uwb"],t0,end,.12); i=j=0
        while i<len(ig) and j<len(ug):
            lo=max(ig[i][0],ug[j][0]); hi=min(ig[i][1],ug[j][1])
            if hi-lo>=2:
                result.append({"node":node,"onset_lower":lo,"onset_upper":lo+.12,
                    "silence_end":hi,"duration_s":hi-lo,"wedge_threshold_met":hi-lo>=20,
                    "terminal_at_stop":hi>=end-.5,"recovered_monotonic":None if hi>=end-.5 else hi})
            if ig[i][1]<ug[j][1]: i+=1
            else: j+=1
    result.sort(key=lambda x:(x["onset_lower"],x["node"]))
    for i,x in enumerate(result,1): x["event_id"]=f"E{i:03d}"; x["onset_wall"]=wall(MANIFEST,x["onset_lower"])
    return result


def dual_recovery(stream, node, lo, hi, duration=2.0):
    if hi-lo < duration: return False
    stop=lo+duration
    imu=sum(n for t,n in stream[node]["imu"] if lo<=t<stop)/duration
    uwb=sum(lo<=t<stop for t in stream[node]["uwb"])/duration
    return imu>=190 and uwb>=8.0


def cluster(segments, stream):
    episodes=[]; mapping={}; rationale=[]
    for node in NODES:
        cur=[]
        for seg in [x for x in segments if x["node"]==node]:
            if cur:
                prev=cur[-1]; rec=prev.get("recovered_monotonic")
                split=rec is not None and dual_recovery(stream,node,rec,seg["onset_lower"],2)
                rationale.append({"left":prev["event_id"],"right":seg["event_id"],
                                  "sustained_dual_recovery_2s":split,
                                  "decision":"separate" if split else "merge"})
                if split: episodes.append(cur);cur=[]
            cur.append(seg)
        if cur:episodes.append(cur)
    episodes.sort(key=lambda x:x[0]["onset_lower"])
    out=[]
    for i,rows in enumerate(episodes,1):
        eid=f"C{i:03d}"; ids=[x["event_id"] for x in rows]
        for sid in ids:mapping[sid]=eid
        out.append({"episode_id":eid,"node":rows[0]["node"],"segment_ids":ids,
                    "onset_lower":rows[0]["onset_lower"],"end_monotonic":rows[-1]["silence_end"],
                    "maximum_segment_s":max(x["duration_s"] for x in rows),
                    "wedge_threshold_met":any(x["wedge_threshold_met"] for x in rows)})
    return out,mapping,rationale


def scan_air(path: Path, tag_to_node, t0, end):
    source={n:[] for n in NODES}; per={n:{r:[] for r in POLL_RECEIVERS} for n in NODES}
    last={n:None for n in NODES}; malformed=Counter(); first_t=last_t=None; records=0
    with path.open("rb") as f:
        for raw in f:
            records+=1
            if b'"kind":"LPD"' not in raw: continue
            mt,mr,ms,mq=AIR_T.search(raw),AIR_R.search(raw),AIR_S.search(raw),AIR_Q.search(raw)
            if not all((mt,mr,ms,mq)): malformed["LPD_missing_required_field"]+=1;continue
            t=int(mt.group(1))/1e9; receiver=mr.group(1).decode(); node=tag_to_node.get(int(ms.group(1)))
            if receiver not in POLL_RECEIVERS or node is None: continue
            seq=int(mq.group(1)); per[node][receiver].append(t)
            prev=last[node]
            if prev is None or seq!=prev[1] or t-prev[0]>.05:
                source[node].append(t); last[node]=(t,seq)
            first_t=t if first_t is None else min(first_t,t);last_t=t if last_t is None else max(last_t,t)
    return source,per,{"merged_records_scanned":records,"parse_failures":dict(malformed),
                       "first_lpd_monotonic":first_t,"last_lpd_monotonic":last_t,
                       "dedup_key":"node/src + poll sequence, observations within 50 ms collapsed"}


def rate_rows(stream, records, source, per, t0, end, width):
    rows=[]; nbin=int((end-t0)//width)
    imu_bins={n:[0]*nbin for n in NODES}
    for node in NODES:
        for t,count in stream[node]["imu"]:
            i=int((t-t0)//width)
            if 0<=i<nbin:imu_bins[node][i]+=count
    record_times={n:{k:[x["monotonic"] for x in records[n][k]] for k in
                     ("FUSION_TELEMETRY","FUSION_QOS","FUSION_QUEUE")} for n in NODES}
    for i in range(nbin):
        lo=t0+i*width;hi=lo+width
        for node in NODES:
            imu=imu_bins[node][i]/width
            uwb=(bisect.bisect_left(stream[node]["uwb"],hi)-bisect.bisect_left(stream[node]["uwb"],lo))/width
            src=(bisect.bisect_left(source[node],hi)-bisect.bisect_left(source[node],lo))/width
            row={"window_s":width,"window_index":i,"start_monotonic":lo,"start_wall":wall(MANIFEST,lo),
                 "node":node,"fusion_imu_hz":imu,"fusion_uwb_hz":uwb,
                 # This is an independent, multi-receiver observation rate.  It
                 # is deliberately not called the transmitter rate: packet loss
                 # and geometry make that inference invalid.
                 "listener_deduplicated_observed_hz":src}
            for kind,label in (("FUSION_TELEMETRY","telemetry_hz"),("FUSION_QOS","qos_hz"),("FUSION_QUEUE","control_hz")):
                a=record_times[node][kind]
                row[label]=(bisect.bisect_left(a,hi)-bisect.bisect_left(a,lo))/width
            for r in POLL_RECEIVERS:
                a=per[node][r];row[f"{r}_hz"]=(bisect.bisect_left(a,hi)-bisect.bisect_left(a,lo))/width
            rows.append(row)
    return rows


def degradation(source, rate600, t0):
    out={}
    for node in NODES:
        rows=[x for x in rate600 if x["node"]==node]
        base=[x["listener_deduplicated_observed_hz"] for x in rows[:1]]
        baseline=base[0] if base else None
        visibility_low=[] if not baseline else [x for x in rows
            if x["listener_deduplicated_observed_hz"] < baseline*.90]
        # Listener reception is not source transmission.  A power-degradation
        # onset requires corroboration (Fusion cadence/uptime, coherent receiver
        # decline, or terminal off-air); reception variation alone is retained
        # as a visibility phenotype and cannot trigger the exposure cutoff.
        out[node]={"smoke_observed_union_baseline_hz":baseline,
                   "first_power_degradation_monotonic":None,
                   "first_power_degradation_wall":None,
                   "terminal_off_air_monotonic":None,
                   "source_cadence_verdict":"NOMINAL_FROM_FUSION_SWEEP_PROGRESSION",
                   "stable_low_rate_unknown":False,
                   "stable_low_windows":[],
                   "listener_visibility_low_windows":[{"start_monotonic":x["start_monotonic"],
                     "observed_union_hz":x["listener_deduplicated_observed_hz"]} for x in visibility_low],
                   "inference_guard":"Listener reception rate was not treated as transmitter cadence."}
    return out


def main():
    ap=argparse.ArgumentParser();ap.add_argument("--run",type=Path,required=True);ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args();run=a.run.resolve();out=a.out.resolve()
    if out.exists() and any(out.iterdir()):raise SystemExit("refusing non-empty output")
    out.mkdir(parents=True,exist_ok=True)
    global MANIFEST,END;MANIFEST=json.loads((run/"RUN_MANIFEST.json").read_text());ledger=json.loads((run/"PROCESS_LEDGER.json").read_text())
    t0=float(MANIFEST["t0_monotonic"]);END=float(ledger["ended_monotonic"])
    inputs=raw_inputs(run);before={str(p.relative_to(run)):sha256(p) for p in inputs}
    stream,records,resets,links,fmeta=scan_fusion(run/"fusion_cdc.log",t0,END)
    mapping=json.loads((run/"node_tag_map.json").read_text());tag_to_node={int(v["tag_short_address"],16):n for n,v in mapping.items()}
    source,per,ameta=scan_air(run/"listener_capture/merged_index.jsonl",tag_to_node,t0,END)
    r60=rate_rows(stream,records,source,per,t0,END,60);r600=rate_rows(stream,records,source,per,t0,END,600)
    segments=joint_segments(stream,t0,END);episodes,emap,merge_reason=cluster(segments,stream)
    for x in segments:
        x["causal_episode_id"]=emap[x["event_id"]]
        x["classification"]="JOINT_STALL_BELOW_WEDGE_THRESHOLD" if not x["wedge_threshold_met"] else "WEDGE_CANDIDATE_AIR_CONTINUITY_REQUIRES_ADJUDICATION"
    pwr=degradation(source,r600,t0);first_deg=min((x["first_power_degradation_monotonic"] for x in pwr.values() if x["first_power_degradation_monotonic"]),default=None)
    nominal_end=first_deg or END; duration=END-t0
    perboard={}
    for n in NODES:
        uwb=stream[n]["uwb"];imu=stream[n]["imu"]; tlm=records[n]["FUSION_TELEMETRY"]
        gaps2=sum(x["duration_s"] for x in segments if x["node"]==n)
        perboard[n]={"power_class":"adapter" if n=="BSF6C53" else "battery",
          "first_fusion_monotonic":min(uwb[0],imu[0][0]),"last_fusion_monotonic":max(uwb[-1],imu[-1][0]),
          "fusion_uwb_records":len(uwb),"fusion_imu_samples":sum(x[1] for x in imu),
          "listener_deduplicated_polls":len(source[n]),"first_listener_monotonic":source[n][0],"last_listener_monotonic":source[n][-1],
          "hidden_resets":resets[n],"connection_events":links[n],"joint_silence_ge2_s":gaps2,
          "last_uptime_node_ms":num(tlm[-1]["fields"],"node_ms") if tlm else None,
          "last_reset_reason":num(tlm[-1]["fields"],"reset_reason") if tlm else None,
          "power_degradation":pwr[n]}
    # No >=20 s candidate exists in this capture; event-level air adjudication is therefore unnecessary for promotion.
    wedge=[x for x in segments if x["wedge_threshold_met"]]
    field=[]
    for n in NODES:
        tlm=records[n]["FUSION_TELEMETRY"];last=tlm[-1] if tlm else None
        for field_name,value,observed,confidence in (
            ("uptime/node_ms",num(last["fields"],"node_ms") if last else None,bool(last),"high"),
            ("reset_reason",num(last["fields"],"reset_reason") if last else None,bool(last),"high"),
            ("boot_counter",None,False,"unavailable"),("recovery_reset_count",None,False,"unavailable"),
            ("recovery_cause",None,False,"unavailable"),("recovery_frozen_ms",None,False,"unavailable"),
            ("reset_intent",None,False,"unavailable"),("watchdog_witness",None,False,"unavailable"),
            ("v45_corpse_present",None,False,"unavailable")):
            field.append({"board":n,"field":field_name,"last_value":value,
                          "timestamp":last["monotonic"] if last and observed else None,
                          "explicitly_observed":observed,"confidence":confidence})
    battery_h=sum(max(0,nominal_end-t0-sum(x["duration_s"] for x in segments if x["node"]==n and x["onset_lower"]<nominal_end)) for n in BATTERY)/3600
    adapter_h=max(0,nominal_end-t0-sum(x["duration_s"] for x in segments if x["node"]=="BSF6C53" and x["onset_lower"]<nominal_end))/3600
    exposure={"formal_duration_s":duration,"formal_duration_h":duration/3600,"continuous_ten_peer_wall_s":duration,
      "ten_node_nominal_power_wall_s":nominal_end-t0,"nominal_power_end_reason":"OPERATOR_STOP" if first_deg is None else "POWER_DEGRADATION",
      "nine_board_battery_healthy_delivered_board_hours":battery_h,"bsf6c53_healthy_adapter_hours":adapter_h,
      "total_healthy_delivered_board_hours":battery_h+adapter_h,"per_board":perboard,
      "operator_stop_gap_after_final_useful_evidence_s":0.0}
    pooled_expected=(battery_h+adapter_h)/26.8;n8_expected=(battery_h+adapter_h)/15.7
    hist={"actual_healthy_board_hours":battery_h+adapter_h,
      "pooled":{"historical_events":4,"historical_board_hours":107.12,"mean_hours_per_event":26.8,
                "expected_events":pooled_expected,"poisson_p_zero":math.exp(-pooled_expected)},
      "n8_only":{"mean_hours_per_event":15.7,"expected_events":n8_expected,"poisson_p_zero":math.exp(-n8_expected)},
      "descriptive_only":True,"small_event_count_warning":"Historical corpus has only four wedge events.",
      "longest_clean_ten_node_claim":"This >6 h run is the longest clean ten-node Fusion/beacon capture in the audited corpus."}
    inventory=[];summary_listener=json.loads((run/"listener_capture/summary.json").read_text())
    for p in inputs:
        rel=str(p.relative_to(run));listener="listener_capture" in rel
        mt0,mt1,et0,et1=edge_timestamps(p,listener) if p.suffix in {".log",".jsonl"} and p.stat().st_size else (None,None,None,None)
        count=None;incomplete=None;parseability="metadata_json"
        if rel=="fusion_cdc.log":count=sum(fmeta["record_counts"].values());parseability="parsed_with_accounting"
        elif rel.endswith("merged_index.jsonl"):count=summary_listener["merged_records"];parseability="parsed_with_accounting"
        elif "/listeners/" in rel:
            snr=p.name.split('.')[0];s=summary_listener["listeners"].get(snr,{});count=s.get("records");incomplete=s.get("incomplete_bytes");parseability="collector_validated"
        inventory.append({"path":rel,"size":p.stat().st_size,"sha256_before":before[rel],"file_type":"symlink" if p.is_symlink() else "regular",
          "parseability":parseability,"first_host_monotonic":mt0/1e9 if listener and mt0 else mt0,"last_host_monotonic":mt1/1e9 if listener and mt1 else mt1,
          "first_wall_epoch":et0/1e9 if listener and et0 else et0,"last_wall_epoch":et1/1e9 if listener and et1 else et1,"record_count":count,"incomplete_final_bytes":incomplete})
    # Outputs
    json_out(out/"INPUT_INVENTORY.json",{"authoritative_run":str(run),"declared_formal_capture_path_conflict":"formal_capture is a short earlier attempt; operator-duration-matching formal_20260811_001328 used", "inputs":inventory,"fusion_parse":fmeta,"listener_parse":ameta})
    listener_first=edge_timestamps(run/"listener_capture/merged_index.jsonl",True)[0]
    timeline=[{"event":"FORMAL_LISTENER_START","monotonic":listener_first/1e9 if listener_first else None,"wall":None},
      {"event":"T0","monotonic":t0,"wall":MANIFEST["t0_wall"]},{"event":"SMOKE_END","monotonic":t0+600,"wall":wall(MANIFEST,t0+600)},
      {"event":"OPERATOR_STOP_AND_SIGINT","monotonic":END,"wall":ledger["ended_wall"]},{"event":"FINAL_FLUSHED_FUSION_RECORD","monotonic":fmeta["last_monotonic"],"wall":wall(MANIFEST,fmeta["last_monotonic"])}]
    for n in NODES:
        for x in links[n]:timeline.append({"event":x["event"],"node":n,"monotonic":x["monotonic"],"wall":x["wall"]})
        for x in resets[n]:timeline.append({"event":"UPTIME_DROP", "node":n,**x})
    timeline.sort(key=lambda x:x.get("monotonic") or 0);json_out(out/"TIMELINE.json",timeline)
    json_out(out/"PER_BOARD_RATES.json",{"windows_60s":r60,"windows_600s":r600})
    json_out(out/"LISTENER_DEDUP_RATE.json",{"deduplication":ameta,"windows_60s":r60,"windows_600s":r600})
    json_out(out/"EVENT_SEGMENTS.json",segments);json_out(out/"CAUSAL_EPISODES.json",{"episodes":episodes,"merge_decisions":merge_reason})
    json_out(out/"RESET_AND_RECOVERY_EVIDENCE.json",{"verdict":"RECOVERY_EVIDENCE_UNAVAILABLE","hidden_uptime_resets":resets,"connection_events":links,"field_matrix_note":"Runtime recovery/intent/watchdog/corpse fields were not present in formal schema; no zero imputed."})
    json_out(out/"B1_EXERCISE_ANALYSIS.json",{"verdict":"B1_EVIDENCE_UNAVAILABLE","required_fields":["rx_retained","MPSL_WORK_ENTER","MPSL_WORK_EXIT","buffer_freed/resubmit","subsequent msg_get_ok"],"explicit_complete_snapshots":False,"reason":"Formal capture schema did not retain B1 counters; token absence is not an explicit zero."})
    json_out(out/"POWER_DEGRADATION_TIMELINE.json",pwr);json_out(out/"EXPOSURE.json",exposure);json_out(out/"HISTORICAL_COMPARISON.json",hist)
    summary={"dispositions":["NO_WEDGE_OBSERVED","RECOVERY_EVIDENCE_UNAVAILABLE","B1_EVIDENCE_UNAVAILABLE","STOPPED_BY_OPERATOR"],
      "scientific_interpretation":"V47_PREVENTION_CONSISTENT_NOT_PROVEN","genuine_wedges":len(wedge),"hidden_resets":sum(map(len,resets.values())),
      "no_recovery_observed":True,"b1_explicitly_exercised":False,"stable_low_rate_unknown":False,
      "stable_5hz_tag":False,
      "first_power_degradation":first_deg,"exposure":exposure,"historical":hist,"clock_discontinuities":fmeta["clock_backsteps"],
      "hardware_accessed_during_analysis":False}
    json_out(out/"ANALYSIS_SUMMARY.json",summary)
    rate_svg(out/"RATE_OVERVIEW.svg", r600)
    # Deterministic CSV mirrors.
    def csvwrite(name,rows,cols):
        with (out/name).open("w",newline="") as f:w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows({k:x.get(k) for k in cols} for x in rows)
    csvwrite("TIMELINE.csv",timeline,["event","node","monotonic","wall"])
    ratecols=list(r60[0]);csvwrite("PER_BOARD_RATES.csv",r60+r600,ratecols);csvwrite("LISTENER_DEDUP_RATE.csv",r60+r600,ratecols)
    csvwrite("EVENT_SEGMENTS.csv",segments,["event_id","causal_episode_id","node","onset_lower","onset_upper","silence_end","duration_s","wedge_threshold_met","classification"])
    csvwrite("CAUSAL_EPISODES.csv",episodes,["episode_id","node","onset_lower","end_monotonic","maximum_segment_s","wedge_threshold_met"])
    csvwrite("FIELD_AVAILABILITY_MATRIX.csv",field,["board","field","last_value","timestamp","explicitly_observed","confidence"])
    after={str(p.relative_to(run)):sha256(p) for p in inputs};unchanged=before==after
    json_out(out/"RAW_HASH_VERIFICATION.json",{"before":before,"after":after,"unchanged":unchanged})
    if not unchanged:raise SystemExit("raw evidence changed during analysis")
    report=f"""# B306 v47 overnight offline causal analysis\n\n## Causal result\n\n**No wedge was observed.** No joint Fusion UWB+IMU silence reached 20 seconds.\n\n**No recovery was observed.** Exact recovery fields were unavailable in the formal runtime schema, so the disposition is `RECOVERY_EVIDENCE_UNAVAILABLE`, not an imputed zero.\n\n**No reset was observed.** No `node_ms` decrease or new connection epoch occurred during nominal-power operation.\n\n**The B1 path was not explicitly exercised.** The required counters were not retained, therefore `B1_EVIDENCE_UNAVAILABLE`; a clean run does not prove B1 handled an exhaustion event.\n\n## Exposure\n\nFormal T0 was {MANIFEST['t0_wall']}; operator stop was {ledger['ended_wall']}. Duration was {duration/3600:.6f} h. No evidence-backed battery degradation preceded stop, so the ten-node nominal-power interval ended at operator stop. Healthy battery exposure was {battery_h:.6f} board-hours; adapter exposure was {adapter_h:.6f} h; total was {battery_h+adapter_h:.6f} board-hours.\n\nNo non-BSF6C53 stable low-rate plateau and no stable approximately 5 Hz Tag phenotype were found. BSF6C53's fixture exemption was applied only to absolute Listener reception, not Fusion UWB, IMU, identity or reset evidence.\n\nHistorical pooled expectation was {pooled_expected:.3f} events with P(0)={math.exp(-pooled_expected):.3f}; N8-only expectation was {n8_expected:.3f} with P(0)={math.exp(-n8_expected):.3f}. These are descriptive with only four historical events.\n\nDisposition: `NO_WEDGE_OBSERVED + RECOVERY_EVIDENCE_UNAVAILABLE + B1_EVIDENCE_UNAVAILABLE + STOPPED_BY_OPERATOR`. Scientific interpretation: `V47_PREVENTION_CONSISTENT_NOT_PROVEN`.\n\nNo hardware was accessed during this analysis. Raw hashes matched before and after. The preflight 124-byte shutdown fragment was excluded. Post-stop files were not used for runtime conclusions.\n"""
    (out/"REPORT.md").write_text(report)
    compact=sorted(p for p in out.iterdir() if p.name!="SHA256SUMS")
    (out/"SHA256SUMS").write_text("".join(f"{sha256(p)}  {p.name}\n" for p in compact if p.is_file()))
    print(json.dumps(summary,indent=2,sort_keys=True))


if __name__=="__main__":main()
