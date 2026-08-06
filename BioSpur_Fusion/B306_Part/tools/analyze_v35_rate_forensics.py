#!/usr/bin/env python3
from __future__ import annotations

import hashlib, json, math, re, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "logs/b306_v35_20260804/P8_VERIFY_600S/fusion_cdc.log"
RESULT = ROOT / "logs/b306_v35_20260804/P8_VERIFY_600S/result.json"
REPORT = ROOT / "logs/b306_v35_20260804/V35_REPORT.md"
OUT = ROOT / "logs/v35_rate_forensics_20260804"
WINDOW_S = 600.0


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def pct(xs, q):
    ys = sorted(xs)
    if not ys: return None
    x = (len(ys) - 1) * q; lo = int(x); hi = min(lo + 1, len(ys))
    return ys[lo] + (ys[hi] - ys[lo]) * (x - lo)


def dist(ds, nominal):
    return {"n":len(ds), "min":min(ds), "p01":pct(ds,.01), "p50":pct(ds,.5),
            "p95":pct(ds,.95), "p99":pct(ds,.99), "p999":pct(ds,.999), "max":max(ds),
            "gt_2x_nominal":sum(x > 2*nominal for x in ds),
            "gt_10x_nominal":sum(x > 10*nominal for x in ds)}


def main():
    imu, uwb = defaultdict(list), defaultdict(list)
    line_re = re.compile(r"\S+\s+(\d+\.\d+)\s+FUSION_RX\s+(FUSION_(?:IMU|UWB))\s+(.*)")
    with RAW.open(errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            m = line_re.match(line)
            if not m: continue
            host = float(m.group(1)); typ=m.group(2)
            d = dict(re.findall(r"(\w+)=([^ ]+)",m.group(3))); name=d.get("name")
            try:
                if typ == "FUSION_UWB":
                    uwb[name].append({"line":line_no,"host":host,"ts":int(d["frame_us"]),"seq":int(d["sweep"])})
                else:
                    imu[name].append({"line":line_no,"host":host,"ts":int(d["base_us"]),"seq":int(d["seq"]),"n":int(d["n"])})
            except (KeyError, ValueError): pass
    out={"method":{"formal_window_s":WINDOW_S,"uwb_nominal_us":120000,"imu_nominal_us":5000,
                   "timestamp_fields":{"uwb":"B306 TIMER2 frame_us","imu":"B306 TIMER2 base_us plus sample offsets"}},"nodes":{}}
    for name in sorted(uwb):
        U,I=uwb[name],imu[name]
        uds=[b["ts"]-a["ts"] for a,b in zip(U,U[1:])]
        ids=[]
        for r in I:
            ids.extend([5000]*(max(0,r["n"]-1)))
        ids.extend(b["ts"]-(a["ts"]+(a["n"]-1)*5000) for a,b in zip(I,I[1:]))
        ug=[]
        for a,b in zip(U,U[1:]):
            delta=(b["seq"]-a["seq"]) & 0xffffffff
            if delta != 1: ug.append({"missing":delta-1,"before":a,"after":b,"timestamp_delta_us":b["ts"]-a["ts"]})
        ig=[]
        for a,b in zip(I,I[1:]):
            delta=(b["seq"]-a["seq"]) & 0xffff; missing=delta-a["n"]
            if missing: ig.append({"missing":missing,"before":a,"after":b,"timestamp_delta_us":b["ts"]-a["ts"]})
        # Every node has one attachment-boundary discontinuity in the first 0.12 s.
        boundary_u=[g for g in ug if g["after"]["host"]-U[0]["host"] < .2]
        boundary_i=[g for g in ig if g["after"]["host"]-I[0]["host"] < .2]
        live_u=[g for g in ug if g not in boundary_u]
        live_i=[g for g in ig if g not in boundary_i]
        samples=sum(r["n"] for r in I)
        out["nodes"][name]={
          "uwb":{"records":len(U),"host_window_rate_hz":len(U)/WINDOW_S,
                 "endpoint_span_s":(U[-1]["ts"]-U[0]["ts"])/1e6,
                 "endpoint_rate_hz":(len(U)-1)*1e6/(U[-1]["ts"]-U[0]["ts"]),
                 "median_interval_rate_hz":1e6/statistics.median(uds),"delta_us":dist(uds,120000),
                 "all_gap_pairs":len(ug),"all_missing":sum(g["missing"] for g in ug),
                 "boundary_gaps":boundary_u,"live_gap_pairs":len(live_u),"live_missing":sum(g["missing"] for g in live_u)},
          "imu":{"samples":samples,"host_window_rate_hz":samples/WINDOW_S,
                 "delivery_fraction":{"numerator":samples,"denominator":120000,"value":samples/120000},
                 "endpoint_span_s":(I[-1]["ts"]+(I[-1]["n"]-1)*5000-I[0]["ts"])/1e6,
                 "endpoint_rate_hz":(samples-1)*1e6/(I[-1]["ts"]+(I[-1]["n"]-1)*5000-I[0]["ts"]),
                 "median_interval_rate_hz":1e6/statistics.median(ids),"delta_us":dist(ids,5000),
                 "all_gap_pairs":len(ig),"all_missing":sum(max(0,g["missing"]) for g in ig),
                 "boundary_gaps":boundary_i,"live_gap_pairs":len(live_i),"live_missing":sum(max(0,g["missing"]) for g in live_i)}
        }
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/"RATE_FORENSICS.json").write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    inputs=[RAW,RESULT,REPORT,Path(__file__),ROOT/"tools/v35_verify.py",ROOT/"tools/delivered_rate.py"]
    (OUT/"EVIDENCE_SHA256.txt").write_text("".join(f"{sha(p)}  {p}\n" for p in inputs))

if __name__ == "__main__": main()
