#!/usr/bin/env python3
"""Read-only publisher-counter analysis for O2."""
import json, math, re, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / "UWB_Part/logs/overnight_20260803/phase_r_capture/fusion_cdc.log"
OUT = ROOT / "UWB_Part/logs/n3_publisher_forensics_20260804/analysis.json"
START = 371236.285641365
CUTOFF = 11215.490
KV = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)=([^ ]+)")
FIELDS = ("node_ms publisher_count publisher_max_us enq_imu enq_uwb enq_ctl "
          "q_drop_imu q_drop_uwb q_drop_ctl delivered_imu delivered_uwb delivered_ctl "
          "q_hwm_imu q_hwm_uwb q_hwm_ctl").split()

def pct(xs, p):
    if not xs: return None
    ys=sorted(xs); return ys[min(len(ys)-1, math.ceil(p*len(ys))-1)]

def main():
    rows=defaultdict(list)
    with LOG.open(errors="replace") as f:
        for line in f:
            if "FUSION_QUEUE " not in line: continue
            parts=line.split()
            try: mono=float(parts[1])
            except (ValueError,IndexError): continue
            d=dict(KV.findall(line)); n=d.get("name")
            if not n: continue
            r={"relative_s":mono-START}
            for k in FIELDS:
                if k in d:r[k]=int(d[k],0)
            rows[n].append(r)
    result={"source":str(LOG.relative_to(ROOT)),"cutoff_relative_s":CUTOFF,"nodes":{}}
    for n,rs in sorted(rows.items()):
        steps=[]; intervals=[]; prev=None
        for r in rs:
            if prev is None or r["publisher_count"]<prev["publisher_count"] or r["node_ms"]<prev["node_ms"]:
                prev=r; continue
            if r["publisher_max_us"]>prev["publisher_max_us"]:
                steps.append({"relative_s":r["relative_s"],"from_us":prev["publisher_max_us"],"to_us":r["publisher_max_us"],"step_us":r["publisher_max_us"]-prev["publisher_max_us"]})
            dt=r["relative_s"]-prev["relative_s"]
            denq=sum(r[k]-prev[k] for k in ("enq_imu","enq_uwb","enq_ctl"))
            ddrop=sum(r[k]-prev[k] for k in ("q_drop_imu","q_drop_uwb","q_drop_ctl"))
            effective=denq-ddrop; pub=r["publisher_count"]-prev["publisher_count"]
            delivered=sum(r[k]-prev[k] for k in ("delivered_imu","delivered_uwb","delivered_ctl"))
            intervals.append({"relative_s":r["relative_s"],"dt_s":dt,"publisher_delta":pub,"effective_enqueue_delta":effective,"delivered_delta":delivered,"balance":pub-effective,"publisher_rate_hz":pub/dt if dt>0 else None})
            prev=r
        pre=[x for x in intervals if x["relative_s"]<=CUTOFF]
        def summarize(xs):
            dt=[x["dt_s"] for x in xs]; rate=[x["publisher_rate_hz"] for x in xs if x["publisher_rate_hz"] is not None]
            deficit=[x for x in xs if x["balance"]<0]
            longest=run=0
            for x in xs:
                run=run+1 if x["balance"]<0 else 0; longest=max(longest,run)
            return {"intervals":len(xs),"dt_median_s":statistics.median(dt) if dt else None,"dt_p99_s":pct(dt,.99),"dt_max_s":max(dt,default=None),"rate_median_hz":statistics.median(rate) if rate else None,"rate_p01_hz":pct(rate,.01),"rate_min_hz":min(rate,default=None),"deficit_intervals":len(deficit),"deficit_total":-sum(x["balance"] for x in deficit),"worst_balance":min((x["balance"] for x in xs),default=None),"longest_deficit_run":longest,"severe_deficit_intervals":sum(x["balance"]<=-5 for x in xs),"below_20hz_intervals":sum((x["publisher_rate_hz"] or 0)<20 for x in xs),"delivered_mismatch_intervals":sum(x["publisher_delta"]!=x["delivered_delta"] for x in xs)}
        initial=rs[0]
        result["nodes"][n]={"first_relative_s":initial["relative_s"],"last_relative_s":rs[-1]["relative_s"],"initial_publisher_count":initial["publisher_count"],"final_publisher_count":rs[-1]["publisher_count"],"initial_max_us":initial["publisher_max_us"],"final_max_us":rs[-1]["publisher_max_us"],"steps":steps,"whole":summarize(intervals),"pre_first_stall":summarize(pre),"pre_first_stall_hours":max(0,min(CUTOFF,rs[-1]["relative_s"])-max(0,rs[0]["relative_s"]))/3600,"first_over_1s":"before_window" if initial["publisher_max_us"]>1_000_000 else next((s["relative_s"] for s in steps if s["to_us"]>1_000_000),None)}
    OUT.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(OUT)
if __name__=="__main__":main()
