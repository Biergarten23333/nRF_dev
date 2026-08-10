#!/usr/bin/env python3
"""Deterministic offline causal analysis for the B306 v47 afternoon run."""

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

NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
)
BATTERY = set(NODES) - {"BSF6C53"}
KINDS = {
    "FUSION_UWB", "FUSION_IMU", "FUSION_TELEMETRY", "FUSION_QUEUE",
    "FUSION_POOL", "FUSION_QOS", "FUSION_CONNECTED",
    "FUSION_DISCONNECTED", "FUSION_FAIL", "FUSION_MASTER_POOL",
    "FUSION_CONTROL_CHARACTERISTIC", "FUSION_DATA_SUBSCRIBED",
    "FUSION_TELEMETRY_SUBSCRIBED", "FUSION_STALL_CHARACTERISTIC",
    "FUSION_BRIDGE_READY", "FUSION_PHY_UPDATED", "FUSION_CI_CURRENT",
    "FUSION_CI_REQUEST", "FUSION_IMU_WAIT_EPOCH", "FUSION_TARGET",
}
COUNTERS = (
    "node_ms", "frames", "bytes", "last_sweep", "imu_records", "imu_pulls",
    "notify_ok", "watchdog_feeds", "drop_unsub", "drop_err", "notify_errno",
    "q_drop_imu", "q_drop_uwb", "q_drop_ctl", "q_hwm_imu", "q_hwm_uwb",
    "q_hwm_ctl", "publisher_count", "enq_imu", "enq_uwb", "enq_ctl",
    "abort_imu", "abort_uwb", "abort_ctl", "delivered_imu", "delivered_uwb",
    "delivered_ctl", "usage", "sent_cb", "reports", "event_gaps", "crc_ok",
    "crc_error", "nak", "rx_timeout", "handle", "master_ms", "subscribed",
)
HEAD = re.compile(r"^([0-9.]+) ([0-9.]+) FUSION_RX (\S+)(?: (.*))?$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def percentile(values, q: float):
    if not values:
        return None
    x = sorted(values)
    p = (len(x) - 1) * q
    lo, hi = math.floor(p), math.ceil(p)
    return x[lo] if lo == hi else x[lo] * (hi - p) + x[hi] * (p - lo)


def parse_fields(text: str) -> dict[str, object]:
    out = {}
    for token in text.split():
        if "=" not in token:
            continue
        k, v = token.split("=", 1)
        try:
            out[k] = int(v, 0)
        except ValueError:
            out[k] = v
    return out


def wrap_delta(before: int, after: int, bits: int = 32) -> int:
    """Unsigned wrap-safe delta. Resets must be identified independently."""
    return (after - before) % (1 << bits)


def bins(times, start: float, end: float, width: float, weights=None):
    n = max(0, int(math.floor((end - start) / width)))
    out = [0] * n
    if weights is None:
        weights = [1] * len(times)
    for t, w in zip(times, weights):
        i = int((t - start) // width)
        if 0 <= i < n:
            out[i] += w
    return out


def derive_gate(counts, width: float) -> dict:
    rates = [x / width for x in counts]
    med = statistics.median(rates) if rates else 0.0
    p10 = percentile(rates, 0.10) or 0.0
    gate = min(p10, med * 0.80) if p10 else med * 0.80
    return {"median_hz": med, "p10_hz": p10, "gate_hz": gate}


def sustained_dual_recovery(imu, uwb, start: float, end: float, duration: float,
                            imu_gate: float, uwb_gate: float) -> dict:
    """Prove a dual-stream recovery using a prefix window of requested length."""
    available = max(0.0, end - start)
    if available + 1e-9 < duration:
        return {"pass": False, "reason": "interval_too_short", "available_s": available}
    stop = start + duration
    it = [t for t, _ in imu if start <= t < stop]
    iw = [n for t, n in imu if start <= t < stop]
    ut = [t for t in uwb if start <= t < stop]
    imu_samples, uwb_records = sum(iw), len(ut)
    imu_rate, uwb_rate = imu_samples / duration, uwb_records / duration
    # Require presence near both edges, preventing a short burst from passing
    # merely on aggregate count.
    imu_edge = bool(it) and it[0] - start <= 0.20 and stop - it[-1] <= 0.20
    uwb_edge = bool(ut) and ut[0] - start <= 0.30 and stop - ut[-1] <= 0.30
    passed = imu_rate >= imu_gate and uwb_rate >= uwb_gate and imu_edge and uwb_edge
    return {
        "pass": passed, "available_s": available, "duration_s": duration,
        "imu_samples": imu_samples, "uwb_records": uwb_records,
        "imu_rate_hz": imu_rate, "uwb_rate_hz": uwb_rate,
        "imu_edge_covered": imu_edge, "uwb_edge_covered": uwb_edge,
    }


def cluster_segments(segments, streams, gates, primary_s=20.0):
    by_node = defaultdict(list)
    for seg in segments:
        by_node[seg["node"]].append(seg)
    episodes, mapping, sensitivity = [], {}, []
    for node in NODES:
        rows = sorted(by_node[node], key=lambda x: x["onset_lower"])
        current = None
        for seg in rows:
            if current is None:
                current = {"node": node, "segments": [seg]}
                continue
            prev = current["segments"][-1]
            rec = prev.get("recovered_monotonic")
            checks = {}
            if rec is not None:
                for d in (2.0, 5.0, 10.0, 20.0):
                    checks[str(int(d))] = sustained_dual_recovery(
                        streams[node]["imu"], streams[node]["uwb"], rec,
                        seg["onset_lower"], d, gates[node]["imu_gate_hz"],
                        gates[node]["uwb_gate_hz"])
            else:
                checks = {str(x): {"pass": False, "reason": "no_recovery"} for x in (2, 5, 10, 20)}
            sensitivity.append({"node": node, "left": prev["event_id"],
                                "right": seg["event_id"], "checks": checks})
            if checks[str(int(primary_s))]["pass"]:
                episodes.append(current); current = {"node": node, "segments": [seg]}
            else:
                current["segments"].append(seg)
        if current:
            episodes.append(current)
    episodes.sort(key=lambda x: x["segments"][0]["onset_lower"])
    for i, ep in enumerate(episodes, 1):
        ep["episode_id"] = f"C{i:03d}"
        ep["segment_ids"] = [x["event_id"] for x in ep.pop("segments")]
        ss = [next(s for s in segments if s["event_id"] == x) for x in ep["segment_ids"]]
        ep["onset_lower"] = ss[0]["onset_lower"]
        ep["end_monotonic"] = ss[-1].get("recovered_monotonic")
        ep["terminal_at_stop"] = ss[-1]["terminal_at_stop"]
        ep["historical_wedge_segments"] = sum(s["classification"] == "STEADY_STATE_HOST_WEDGE" for s in ss)
        for sid in ep["segment_ids"]:
            mapping[sid] = ep["episode_id"]
    return episodes, mapping, sensitivity


def scan_fusion(path: Path, t0: float, end: float, micro_start: float, micro_end: float):
    streams = {n: {"imu": [], "uwb": []} for n in NODES}
    records = {n: defaultdict(list) for n in NODES}
    micro = []
    malformed = Counter()
    observed_kinds = Counter()
    with path.open(errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if " FUSION_RX " not in line:
                continue
            m = HEAD.match(line)
            if not m:
                # The capture deliberately preserves wrapped Master pool/status
                # continuation lines.  They are valid evidence, but not standalone
                # records consumable by this parser.
                if re.match(r"^\d+(?:\.\d+)?\s+\d+(?:\.\d+)?\s+FUSION_RX\s{2,}\S", line):
                    malformed["continuation_line"] += 1
                else:
                    malformed["bad_header"] += 1
                continue
            epoch, mono, kind, rest = float(m.group(1)), float(m.group(2)), m.group(3), m.group(4) or ""
            observed_kinds[kind] += 1
            f = parse_fields(rest); node = f.get("name")
            if kind in KINDS and kind != "FUSION_MASTER_POOL" and node not in NODES:
                malformed[f"{kind}:missing_or_bad_name"] += 1
            if node in NODES:
                rec = {"epoch": epoch, "monotonic": mono, "kind": kind, "fields": f}
                if kind == "FUSION_IMU":
                    n = f.get("n")
                    if not isinstance(n, int): malformed["FUSION_IMU:missing_n"] += 1
                    else: streams[node]["imu"].append((mono, n))
                elif kind == "FUSION_UWB":
                    streams[node]["uwb"].append(mono)
                if kind in KINDS:
                    records[node][kind].append(rec)
                if micro_start <= mono <= micro_end and kind in KINDS:
                    micro.append(rec)
            elif kind == "FUSION_MASTER_POOL" and micro_start <= mono <= micro_end:
                micro.append({"epoch": epoch, "monotonic": mono, "kind": kind, "fields": f})
    return streams, records, micro, {"counts": dict(observed_kinds), "accounting": dict(malformed),
                                     "malformed_total": sum(v for k, v in malformed.items()
                                                            if k != "continuation_line")}


def baseline_gates(streams, t0: float):
    out = {}
    for node in NODES:
        imu_t = [t for t, _ in streams[node]["imu"]]
        imu_n = [n for _, n in streams[node]["imu"]]
        ic = bins(imu_t, t0, t0 + 600, 5.0, imu_n)
        uc = bins(streams[node]["uwb"], t0, t0 + 600, 5.0)
        ig, ug = derive_gate(ic, 5.0), derive_gate(uc, 5.0)
        out[node] = {"imu": ig, "uwb": ug, "imu_gate_hz": ig["gate_hz"],
                     "uwb_gate_hz": ug["gate_hz"]}
    return out


def scan_air(path: Path, tag_to_node, receiver_keys):
    times = {n: {r: [] for r in receiver_keys} for n in NODES}
    malformed = Counter()
    with path.open(errors="replace") as fh:
        for line in fh:
            if '"kind":"LPD"' not in line:
                continue
            try:
                d = json.loads(line)
                node = tag_to_node.get(d.get("src")); receiver = d.get("listener_key")
                t = d.get("arrival_monotonic_ns")
                if node and receiver in receiver_keys and isinstance(t, int):
                    times[node][receiver].append(t / 1e9)
                elif node and receiver in receiver_keys:
                    malformed["missing_monotonic"] += 1
            except (ValueError, TypeError):
                malformed["json"] += 1
    return times, dict(malformed)


def count_range(a, lo, hi):
    return bisect.bisect_left(a, hi) - bisect.bisect_left(a, lo)


def air_metrics(times_by_receiver, onset, recovery, capture_start, capture_end):
    windows = {}
    for w in (5, 10, 20, 60, 120, 600):
        row = {"window_s": w, "status": "COMPLETE" if onset-w >= capture_start and onset+w <= capture_end else "INCOMPLETE",
               "receivers": {}}
        for r, a in times_by_receiver.items():
            pre, post = count_range(a, onset-w, onset), count_range(a, onset, onset+w)
            row["receivers"][r] = {"pre": pre, "post": post, "ratio": post/pre if pre else None}
        row["sum_pre"] = sum(x["pre"] for x in row["receivers"].values())
        row["sum_post"] = sum(x["post"] for x in row["receivers"].values())
        row["sum_ratio"] = row["sum_post"] / row["sum_pre"] if row["sum_pre"] else None
        windows[str(w)] = row
    dur = max(0.0, (recovery or capture_end) - onset)
    intervals = {}
    for label, lo, hi in (
        ("event", onset, onset+dur), ("equal_pre", onset-dur, onset),
        ("equal_post", onset+dur, onset+2*dur),
    ):
        rr = {r: count_range(a, lo, min(hi, capture_end)) for r, a in times_by_receiver.items()}
        intervals[label] = {"lo": lo, "hi": hi, "status": "COMPLETE" if lo >= capture_start and hi <= capture_end else "INCOMPLETE",
                            "per_receiver": rr, "sum": sum(rr.values()),
                            "rate_hz": sum(rr.values()) / max(1e-12, min(hi,capture_end)-lo) if min(hi,capture_end)>lo else None}
    local = sorted(t for a in times_by_receiver.values() for t in a if onset-120 <= t <= onset+120)
    gaps = [b-a for a,b in zip(local,local[1:])]
    active_seconds = len({int(t) for t in local})
    rolling = {}
    for width in (1, 5):
        samples = []
        lo = max(capture_start, math.floor(onset-120))
        hi = min(capture_end, math.ceil(onset+120))
        t = lo
        while t + width <= hi:
            per = {r: count_range(a, t, t+width) / width
                   for r, a in times_by_receiver.items()}
            samples.append({"start": t, "per_receiver_hz": per,
                            "sum_hz": sum(per.values())})
            t += 1.0
        rolling[str(width)] = samples
    receiver_gaps = {}
    for r, a in times_by_receiver.items():
        aa = [t for t in a if onset-120 <= t <= onset+120]
        receiver_gaps[r] = max((b-a for a,b in zip(aa,aa[1:])), default=None)
    return {"windows": windows, "intervals": intervals,
            "rolling_rates": rolling,
            "local_interarrival_s": {"count": len(gaps), "p50": percentile(gaps,.5), "p95": percentile(gaps,.95),
                                     "p99": percentile(gaps,.99), "max": max(gaps) if gaps else None},
            "longest_air_gap_s_per_receiver": receiver_gaps,
            "local_active_seconds": active_seconds,
            "receiver_agreement": sum(bool(count_range(a,onset-20,onset+20)) for a in times_by_receiver.values())}


def first_air_degradation(times_by_receiver, start, end, anchor):
    rows = []
    for k in range(int(start//5), int(end//5)+1):
        lo = k*5.0; per = {r: count_range(a,lo,lo+5) for r,a in times_by_receiver.items()}
        rows.append({"start":lo,"sum":sum(per.values()),"per_receiver":per})
    # Listener cadence changed earlier in the run for an unrelated responder
    # transition, so a run-start median is not a valid control for this event.
    # Use the last five complete bins immediately before the target onset.  This
    # is a pre-declared local matched control (25 seconds), not a post-event fit.
    pre = [x for x in rows if x["start"]+5 <= anchor]
    baseline = pre[-5:]
    med = statistics.median(x["sum"] for x in baseline)
    threshold = med*0.70
    first = None
    scan_start = baseline[-1]["start"]+5 if baseline else anchor
    for i in range(len(rows)-2):
        block=rows[i:i+3]
        if block[0]["start"] < scan_start:
            continue
        if all(x["sum"] < threshold and sum(v>0 for v in x["per_receiver"].values()) == len(times_by_receiver) for x in block):
            first=block[0]["start"]; break
    return {"bin_s":5,"baseline_window":[baseline[0]["start"],baseline[-1]["start"]+5] if baseline else None,
            "baseline_rows":baseline,"baseline_median_sum":med,"threshold_sum":threshold,
            "criterion":"three consecutive 5 s bins below 70% of the immediately preceding 25 s matched-control median, with all receivers present",
            "first_degradation_monotonic":first,"rows":rows}


def nearest(rows, t, side):
    if side == "before":
        a=[x for x in rows if x["monotonic"] < t]; return a[-1] if a else None
    a=[x for x in rows if x["monotonic"] >= t]; return a[0] if a else None


def counter_delta(before, after, elapsed, rates):
    out=[]
    for key in COUNTERS:
        a = before["fields"].get(key) if before else None
        b = after["fields"].get(key) if after else None
        if not isinstance(a,int) or not isinstance(b,int):
            state="unavailable"; delta=None
        elif key == "node_ms" and b < a:
            state="reset"; delta=None
        elif b < a and key not in {"master_ms"}:
            state="reset_or_new_connection"; delta=None
        else:
            delta=wrap_delta(a,b); state="advanced" if delta else "froze"
        out.append({"field":key,"before":a,"after":b,"elapsed_s":elapsed,"delta":delta,
                    "expected_healthy_delta":rates.get(key,0)*elapsed if key in rates else None,"state":state})
    return out


def counter_rates(records, node, t0):
    rates={}
    for kind in ("FUSION_TELEMETRY","FUSION_QUEUE","FUSION_QOS"):
        a=[x for x in records[node][kind] if t0<=x["monotonic"]<t0+600]
        if len(a)<2: continue
        elapsed=a[-1]["monotonic"]-a[0]["monotonic"]
        for key in COUNTERS:
            x,y=a[0]["fields"].get(key),a[-1]["fields"].get(key)
            if isinstance(x,int) and isinstance(y,int) and y>=x and elapsed>0:
                rates[key]=(y-x)/elapsed
    return rates


def exposure_v2(streams, t0, end, cutoff):
    result={"definition":"complete bins before LED-off observation; both stream rates must meet initial-stable-period-derived gates",
            "led_off_cutoff_monotonic":cutoff,"widths":{}}
    for width in (1.0,5.0,10.0):
        wr={"nodes":{}}
        for node in NODES:
            it=[t for t,_ in streams[node]["imu"]]; iw=[n for _,n in streams[node]["imu"]]
            ub=bins(streams[node]["uwb"],t0,min(end,cutoff),width)
            ib=bins(it,t0,min(end,cutoff),width,iw)
            base_u=bins(streams[node]["uwb"],t0,t0+600,width)
            base_i=bins(it,t0,t0+600,width,iw)
            ug,ig=derive_gate(base_u,width),derive_gate(base_i,width)
            good=[i for i,(u,m) in enumerate(zip(ub,ib)) if u/width>=ug["gate_hz"] and m/width>=ig["gate_hz"]]
            wr["nodes"][node]={"exposure_s":len(good)*width,"imu_gate":ig,"uwb_gate":ug,
                               "complete_bins":len(ub),"healthy_bins":len(good)}
        wr["battery_board_hours"]=sum(wr["nodes"][n]["exposure_s"] for n in BATTERY)/3600
        wr["adapter_hours"]=wr["nodes"]["BSF6C53"]["exposure_s"]/3600
        result["widths"][str(int(width))]=wr
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--archive",type=Path,required=True); ap.add_argument("--out",type=Path,required=True)
    args=ap.parse_args(); archive=args.archive.resolve(); out=args.out.resolve()
    if out.exists() and any(out.iterdir()): raise SystemExit("refusing non-empty output directory")
    out.mkdir(parents=True,exist_ok=True)
    manifest=json.loads((archive/"RUN_MANIFEST.json").read_text()); ledger=json.loads((archive/"PROCESS_LEDGER.json").read_text())
    t0=float(manifest["t0_monotonic"]); end=float(ledger["ended_monotonic"])
    micro_start=t0+(datetime.fromisoformat("2026-08-10T18:05:00+02:00")-datetime.fromisoformat(manifest["t0_wall"])).total_seconds()
    micro_end=t0+(datetime.fromisoformat("2026-08-10T18:12:00+02:00")-datetime.fromisoformat(manifest["t0_wall"])).total_seconds()
    streams,records,micro,parse_audit=scan_fusion(archive/"fusion_cdc.log",t0,end,micro_start,micro_end)
    gates=baseline_gates(streams,t0)
    segments=json.loads((archive/"analysis/EVENT_TIMELINE.json").read_text())
    episodes,mapping,sensitivity=cluster_segments(segments,streams,gates)
    inv=json.loads((archive/"listener_capture/summary.json").read_text())
    receiver_keys=sorted(x["listener_key"] for x in inv["listeners"].values() if x["kinds"].get("LPD",0)>0)
    nm=json.loads((archive/"node_tag_map.json").read_text()); tag_to_node={int(v["tag_short_address"],16):n for n,v in nm.items()}
    air_times,air_malformed=scan_air(archive/"listener_capture/merged_index.jsonl",tag_to_node,receiver_keys)
    multiscale=[]
    for seg in segments:
        multiscale.append({"level":"segment","id":seg["event_id"],"node":seg["node"],"onset":seg["onset_lower"],
                           **air_metrics(air_times[seg["node"]],seg["onset_lower"],seg.get("recovered_monotonic"),t0,end)})
    for ep in episodes:
        multiscale.append({"level":"episode","id":ep["episode_id"],"node":ep["node"],"onset":ep["onset_lower"],
                           **air_metrics(air_times[ep["node"]],ep["onset_lower"],ep.get("end_monotonic"),t0,end)})
    target_onset=min(x["onset_lower"] for x in segments if x["node"]=="BSFEC35")
    degradation=first_air_degradation(air_times["BSFEC35"],t0,end,target_onset)
    # Counter transitions for the two frozen phenotype segments.
    deltas=[]; rates=counter_rates(records,"BSFEC35",t0)
    for sid in ("E044","E045"):
        seg=next(x for x in segments if x["event_id"]==sid); rec=seg.get("recovered_monotonic") or end
        for kind in ("FUSION_TELEMETRY","FUSION_QUEUE","FUSION_POOL","FUSION_QOS"):
            before=nearest(records["BSFEC35"][kind],seg["onset_lower"],"before")
            after=nearest(records["BSFEC35"][kind],rec,"after")
            deltas.append({"segment":sid,"kind":kind,"before_t":before["monotonic"] if before else None,
                           "after_t":after["monotonic"] if after else None,
                           "rows":counter_delta(before,after,(after["monotonic"]-before["monotonic"]) if before and after else 0,rates)})
    # Add evidence-based episode labels without changing historical segments.
    lifecycle=records["BSFEC35"]["FUSION_CONNECTED"]+records["BSFEC35"]["FUSION_DISCONNECTED"]
    for ep in episodes:
        if ep["node"]=="BSFEC35" and "E044" in ep["segment_ids"]:
            lo=ep["onset_lower"]; hi=ep.get("end_monotonic") or end
            lc=sum(lo-30<=x["monotonic"]<=hi+60 for x in lifecycle)
            ep["mechanism_class"]="LOW_VOLTAGE_TRANSITION"
            ep["mechanism_evidence"]={"lifecycle_records":lc,"historical_wedge_segments":ep["historical_wedge_segments"],
                                      "air_degradation_first":degradation["first_degradation_monotonic"]}
            ep["independent_causal_wedge"] = False
        else:
            ep["mechanism_class"]="UNADJUDICATED_NON_TARGET_EPISODE"
            ep["independent_causal_wedge"] = False
    led=json.loads((archive/"OPERATOR_OBSERVATION_ALL_LEDS_OFF.json").read_text())["host_monotonic_at_recording"]
    exp=exposure_v2(streams,t0,end,led)
    write_json(out/"SEGMENT_TO_EPISODE.json",{"primary_sustained_recovery_s":20,"mapping":mapping,"sensitivity":sensitivity})
    write_json(out/"EPISODES.json",episodes)
    with (out/"EPISODES.csv").open("w",newline="") as fh:
        fields=("episode_id","node","onset_lower","end_monotonic","terminal_at_stop","historical_wedge_segments","mechanism_class","independent_causal_wedge")
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader();w.writerows({k:x.get(k) for k in fields} for x in episodes)
    write_json(out/"MULTISCALE_AIR.json",{"receivers":receiver_keys,"malformed":air_malformed,"rows":multiscale,"bsfec35_transition":degradation})
    with (out/"MULTISCALE_AIR.csv").open("w",newline="") as fh:
        fields=("level","id","node","window_s","status","sum_pre","sum_post","sum_ratio")
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
        for x in multiscale:
            for z in x["windows"].values(): w.writerow({"level":x["level"],"id":x["id"],"node":x["node"],**{k:z[k] for k in fields[3:]}})
    write_json(out/"E044_E045_MICROTIMELINE.json",{"window":{"start":micro_start,"end":micro_end},"records":micro,"counter_deltas":deltas})
    with (out/"E044_E045_MICROTIMELINE.csv").open("w",newline="") as fh:
        fields=("epoch","monotonic","kind","node","node_ms","handle","reports","event_gaps","crc_ok","crc_error","nak","rx_timeout","frames","bytes","last_sweep","imu_records","imu_pulls","notify_ok","watchdog_feeds","publisher_count","enq_imu","enq_uwb","delivered_imu","delivered_uwb","delivered_ctl")
        w=csv.DictWriter(fh,fieldnames=fields);w.writeheader()
        for x in micro:
            f=x["fields"];w.writerow({"epoch":x["epoch"],"monotonic":x["monotonic"],"kind":x["kind"],**{k:f.get(k) for k in fields[3:]}})
    write_json(out/"EXPOSURE_V2.json",exp)
    write_json(out/"PARSE_AUDIT.json",parse_audit)
    summary={"schema":"biospur-v47-causal-analysis-v2","historical_segments":len(segments),
             "historical_wedge_segments":sum(x["classification"]=="STEADY_STATE_HOST_WEDGE" for x in segments),
             "causal_episodes":len(episodes),"independent_causal_wedge_episodes":sum(x["independent_causal_wedge"] for x in episodes),
             "target_episode":next(x for x in episodes if "E044" in x["segment_ids"]),
             "causal_verdict":"LOW_VOLTAGE_TRANSITION","parse_audit":parse_audit,"baseline_gates":gates,
             "exposure_v2_battery_board_hours":{w:x["battery_board_hours"] for w,x in exp["widths"].items()}}
    write_json(out/"CAUSAL_SUMMARY.json",summary)
    print(json.dumps(summary,indent=2,sort_keys=True))


if __name__=="__main__": main()
