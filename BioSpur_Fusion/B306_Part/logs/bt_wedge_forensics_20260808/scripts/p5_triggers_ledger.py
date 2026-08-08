#!/usr/bin/env python3
"""§5 path-activity census, §7.1 master triggers with base rates,
§7.2 node-internal triggers with base rates, §12 downtime ledger."""
import json
import os
import sys
import datetime as dt
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow.parquet as pq         # noqa: E402


def T(x):
    return dt.datetime.fromtimestamp(x).strftime("%H:%M:%S.%f")[:-3]


def load_ctl(run):
    return [json.loads(l) for l in open(os.path.join(CACHE, f"ctl_{run}.jsonl"))]


def cmd_text(c):
    raw = c.get("raw", "")
    i = raw.find("line=")
    return raw[i + 5:].strip() if i >= 0 else raw.strip()


def main():
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    wedges = [r for r in reg["wedge_candidates"]
              if r["classification"] == "STEADY_STATE_HOST_WEDGE"]
    det = json.load(open(os.path.join(CACHE, "detect_raw.json")))
    out = {}

    # ---------------- §7.1 master-originated triggers ----------------
    print("=" * 96)
    print("7.1  MASTER-ORIGINATED TRIGGERS -- exact operations near each onset")
    print("=" * 96)
    ctl_by_run = {r: load_ctl(r) for r in ("N5", "N7", "N8")}
    # per-node inbound operation stream: a GATT write or read addressed here
    INBOUND = {"FUSION_COMMAND_TX", "FUSION_STALL_READ_START"}
    per_node_ops = defaultdict(list)
    for run, rows in ctl_by_run.items():
        for c in rows:
            if c["type"] not in INBOUND:
                continue
            tgt = c.get("target") or c.get("name")
            if not tgt or not tgt.startswith("BSF"):
                continue
            per_node_ops[(run, tgt)].append((c["t_host"], c["type"], cmd_text(c)))
    for k in per_node_ops:
        per_node_ops[k].sort()

    # base rate: operations per node-hour, per run
    base = {}
    for run in ("N5", "N7", "N8"):
        dur = det[run]["t_run1"] - det[run]["t_run0"]
        n_nodes = len(det[run]["nodes"])
        tot = sum(len(v) for (r, _), v in per_node_ops.items() if r == run)
        base[run] = {"ops_per_node_hour": round(tot / (dur / 3600) / n_nodes, 1),
                     "mean_gap_s": round(dur / (tot / n_nodes), 1) if tot else None}
        print(f"  {run}: {tot} inbound ops over {dur/3600:.2f} h x {n_nodes} nodes "
              f"= {base[run]['ops_per_node_hour']}/node-hour, "
              f"mean gap {base[run]['mean_gap_s']} s per node")

    trig = {}
    for w in wedges:
        run, nd, onset = w["run"], w["node"], w["onset_lower_epoch"]
        ops = per_node_ops[(run, nd)]
        pre = [(round(onset - t, 3), ty, tx) for t, ty, tx in ops if t <= onset]
        g = base[run]["mean_gap_s"]
        row = {"last_op_lead_s": pre[-1][0] if pre else None,
               "last_op": pre[-1][1:] if pre else None,
               "n_within": {f"{wd}s": sum(1 for L, _, _ in pre if L <= wd)
                            for wd in (0.05, 0.1, 1, 5, 10, 60)},
               "base_mean_gap_s": g,
               "p_chance_within_10s": round(1 - np.exp(-10.0 / g), 4) if g else None,
               "p_chance_within_1s": round(1 - np.exp(-1.0 / g), 4) if g else None}
        trig[f"{run}_{nd}"] = row
        print(f"\n  {run} {nd} onset {T(onset)}")
        print(f"    last inbound op {row['last_op_lead_s']} s before onset: {row['last_op']}")
        print(f"    counts {row['n_within']}   chance(<=10 s)={row['p_chance_within_10s']} "
              f"chance(<=1 s)={row['p_chance_within_1s']}")
        for L, ty, tx in pre[-6:]:
            print(f"      -{L:8.3f}s  {ty:26s} {tx[:80]}")
    out["triggers_master"] = {"base": base, "events": trig}

    # command-class enrichment across the whole campaign
    print("\n  --- command-class base rates vs wedge coincidences (all runs)")
    cls_tot = Counter()
    for k, v in per_node_ops.items():
        for _, ty, tx in v:
            cls_tot[(ty, tx.split()[-1] if ty == "FUSION_COMMAND_TX" and tx else ty)] += 1
    cls_hit = Counter()
    for w in wedges:
        ops = per_node_ops[(w["run"], w["node"])]
        for t, ty, tx in ops:
            if 0 <= w["onset_lower_epoch"] - t <= 5.0:
                cls_hit[(ty, tx.split()[-1] if ty == "FUSION_COMMAND_TX" and tx else ty)] += 1
    for k, n in cls_tot.most_common(14):
        print(f"    {str(k):50s} total={n:6d}  followed-by-wedge<=5s={cls_hit.get(k,0)}")
    out["command_classes"] = {str(k): {"total": v, "wedge_within_5s": cls_hit.get(k, 0)}
                              for k, v in cls_tot.items()}

    # ---------------- §7.2 node-internal triggers ----------------
    print("\n" + "=" * 96)
    print("7.2  NODE-INTERNAL TRIGGERS -- IMU recovery, I2C, UART restart, timer wrap")
    print("=" * 96)
    COUNTERS = ["imu_hreset", "imu_hrecover_ok", "imu_hrecover_fail", "imu_i2c_err",
                "imu_hfrozen", "imu_hrate", "imu_hcanary", "imu_hplaus", "imu_hdead",
                "imu_hident", "imu_hi2c", "uart_restarts", "uart_err", "timer_wraps"]
    ni = {}
    for run in ("N5", "N7", "N8"):
        tlm = pq.read_table(os.path.join(CACHE, f"tlm_{run}.parquet"))
        nm = np.array(tlm.column("name").to_pylist())
        th = tlm.column("t_host").to_numpy()
        dur_h = (det[run]["t_run1"] - det[run]["t_run0"]) / 3600.0
        for c in COUNTERS:
            if c not in tlm.column_names:
                continue
            v = tlm.column(c).to_numpy(zero_copy_only=False)
            ev = defaultdict(list)
            for nd in sorted(set(nm.tolist())):
                m = nm == nd
                vv, tt = np.asarray(v[m], float), th[m]
                d = np.diff(vv)
                for i in np.nonzero(d > 0)[0]:
                    ev[nd].append(float(tt[i + 1]))
            tot = sum(len(x) for x in ev.values())
            if tot == 0:
                continue
            rate = tot / (dur_h * len(ev)) if len(ev) else 0
            ni.setdefault(run, {})[c] = {"total": tot, "per_node_hour": round(rate, 2),
                                         "times": {k: v2 for k, v2 in ev.items()}}
    prox = {}
    for w in wedges:
        run, nd, onset = w["run"], w["node"], w["onset_lower_epoch"]
        row = {}
        for c, d in ni.get(run, {}).items():
            ts = d["times"].get(nd, [])
            lead = [round(onset - t, 2) for t in ts if 0 <= onset - t <= 600]
            r = d["per_node_hour"]
            row[c] = {"per_node_hour": r,
                      "n_within_10s": sum(1 for L in lead if L <= 10),
                      "n_within_60s": sum(1 for L in lead if L <= 60),
                      "nearest_lead_s": min(lead) if lead else None,
                      "p_chance_within_10s": round(1 - np.exp(-r * 10 / 3600), 4)}
        prox[f"{run}_{nd}"] = row
        print(f"\n  {run} {nd}")
        for c, v in sorted(row.items(), key=lambda kv: (kv[1]["nearest_lead_s"] is None,
                                                        kv[1]["nearest_lead_s"] or 9e9)):
            if v["nearest_lead_s"] is None and v["n_within_60s"] == 0:
                continue
            print(f"    {c:20s} rate={v['per_node_hour']:6.2f}/node-h  "
                  f"nearest {v['nearest_lead_s']}s before onset  "
                  f"<=10s:{v['n_within_10s']} <=60s:{v['n_within_60s']}  "
                  f"P(chance<=10s)={v['p_chance_within_10s']}")
    print("\n  fleet-wide episode rates (per node-hour):")
    for run in ni:
        print(f"    {run}: " + ", ".join(f"{c}={d['per_node_hour']}"
                                         for c, d in sorted(ni[run].items())))
    out["triggers_node_internal"] = {
        "rates": {r: {c: {"total": d["total"], "per_node_hour": d["per_node_hour"]}
                      for c, d in v.items()} for r, v in ni.items()},
        "proximity": prox}

    # clustering: do IMU recovery episodes coincide fleet-wide?
    print("\n  IMU-recovery clustering test (N8): episodes within 5 s across nodes")
    if "N8" in ni and "imu_hreset" in ni["N8"]:
        allt = sorted((t, nd) for nd, ts in ni["N8"]["imu_hreset"]["times"].items() for t in ts)
        clusters, cur = [], [allt[0]] if allt else []
        for t, nd in allt[1:]:
            if t - cur[-1][0] <= 5:
                cur.append((t, nd))
            else:
                clusters.append(cur); cur = [(t, nd)]
        if cur:
            clusters.append(cur)
        sizes = Counter(len(c) for c in clusters)
        print(f"    {len(allt)} episodes -> {len(clusters)} clusters, sizes {dict(sorted(sizes.items()))}")
        out["imu_reset_clusters"] = {"n_episodes": len(allt), "n_clusters": len(clusters),
                                     "size_hist": dict(sorted(sizes.items()))}

    json.dump(out, open(os.path.join(CACHE, "p5.json"), "w"), indent=1, default=str)
    print("\nwrote cache/p5.json")


if __name__ == "__main__":
    main()
