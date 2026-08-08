#!/usr/bin/env python3
"""§12 downtime attribution ledger + §11 necessity/rate, and the control
distribution for the §5 channel-shift proxy."""
import json
import os
import sys
import datetime as dt
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow.parquet as pq         # noqa: E402


def T(x):
    return dt.datetime.fromtimestamp(x).strftime("%H:%M:%S")


def channel_shift_controls():
    """L1 distance between 5-min channel histograms, for every healthy node
    at every wedge onset -- the null distribution the event value needs."""
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    wedges = [r for r in reg["wedge_candidates"]
              if r["classification"] == "STEADY_STATE_HOST_WEDGE"]
    res = {}
    for run in sorted({w["run"] for w in wedges}):
        q = pq.read_table(os.path.join(CACHE, f"qos_{run}.parquet"))
        nm = np.array(q.column("name").to_pylist())
        th = q.column("t_host").to_numpy()
        chs = sorted(c for c in q.column_names if c.startswith("ch"))
        M = np.column_stack([q.column(c).to_numpy(zero_copy_only=False).astype(float)
                             for c in chs])
        for w in wedges:
            if w["run"] != run:
                continue
            on = w["onset_lower_epoch"]
            vals = {}
            for nd in sorted(set(nm.tolist())):
                m = nm == nd
                t, X = th[m], M[m]
                a, b = (t > on - 300) & (t <= on), (t > on) & (t <= on + 300)
                if a.sum() < 60 or b.sum() < 60:
                    continue
                ha, hb = np.nansum(X[a], 0), np.nansum(X[b], 0)
                if ha.sum() == 0 or hb.sum() == 0:
                    continue
                vals[nd] = float(np.abs(ha / ha.sum() - hb / hb.sum()).sum())
            ev = vals.pop(w["node"], None)
            res[f"{run}_{w['node']}"] = {
                "event_l1": round(ev, 4) if ev else None,
                "control_median": round(float(np.median(list(vals.values()))), 4),
                "control_min": round(min(vals.values()), 4),
                "control_max": round(max(vals.values()), 4),
                "n_controls": len(vals)}
    return res


def ledger():
    det = json.load(open(os.path.join(CACHE, "detect_raw.json")))
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    byev = {(r["run"], r["node"], round(r["onset_lower_epoch"], 1)): r
            for r in reg["wedge_candidates"] + reg["near_misses"]}
    rows = []
    for run in ("N5", "N6", "N7", "N8"):
        r = det[run]
        tend = r["t_run1"]
        for nd, n in sorted(r["nodes"].items()):
            # pre-roll: node joined late?
            late = n["t_first"] - r["t_run0"]
            if late > 5:
                rows.append({"run": run, "node": nd, "cause": "OPERATIONAL",
                             "detail": "joined late / not delivering at run open",
                             "start": T(r["t_run0"]), "minutes": round(late / 60, 2)})
            for s in n["joint_stalls"]:
                k = (run, nd, round(s["onset_lower"], 1))
                e = byev.get(k)
                cls = e["classification"] if e else "UNKNOWN"
                cause = {"STEADY_STATE_HOST_WEDGE": "HOST_WEDGE",
                         "DEPLETION_OR_BROWNOUT": "BATTERY_DEPLETION",
                         "RF_OR_DISCONNECT": "UNKNOWN"}.get(cls, "UNKNOWN")
                if cause == "BATTERY_DEPLETION" and (e and e["reset_at_onset"]):
                    cause = "BROWNOUT_CYCLE"
                dur = s["dur_s"] if not s["terminal"] else (tend - s["onset_lower"])
                rows.append({"run": run, "node": nd, "cause": cause,
                             "detail": (e["reason"] if e else "unclassified") +
                                       (" [terminal]" if s["terminal"] else ""),
                             "start": T(s["onset_lower"]), "minutes": round(dur / 60, 2)})
    # RECONNECT: operator action, N8 BSFEC35 17:28:20
    return rows


def main():
    out = {}
    print("=" * 92)
    print("5.2  CHANNEL-MAP-UPDATE PROXY -- event vs same-window controls")
    print("=" * 92)
    cs = channel_shift_controls()
    for k, v in cs.items():
        print(f"  {k:16s} event L1={v['event_l1']}  controls median={v['control_median']} "
              f"range [{v['control_min']}, {v['control_max']}] n={v['n_controls']}")
    out["channel_shift"] = cs

    print("\n" + "=" * 92)
    print("12  DOWNTIME ATTRIBUTION LEDGER")
    print("=" * 92)
    rows = ledger()
    agg = defaultdict(float)
    for r in rows:
        agg[(r["run"], r["cause"])] += r["minutes"]
    runs = sorted({r for r, _ in agg})
    causes = sorted({c for _, c in agg})
    print(f"{'cause':22}" + "".join(f"{r:>12}" for r in runs) + f"{'TOTAL':>12}")
    for c in causes:
        tot = sum(agg[(r, c)] for r in runs)
        print(f"{c:22}" + "".join(f"{agg[(r,c)]:12.1f}" for r in runs) + f"{tot:12.1f}")
    tot_all = sum(agg.values())
    print(f"{'ALL (node-minutes)':22}" +
          "".join(f"{sum(agg[(r,c)] for c in causes):12.1f}" for r in runs) +
          f"{tot_all:12.1f}")
    out["ledger_rows"] = rows
    out["ledger_agg"] = {f"{r}|{c}": round(v, 2) for (r, c), v in agg.items()}

    print("\n  per-event detail:")
    for r in rows:
        print(f"    {r['run']:4}{r['node']:9}{r['start']:10}{r['minutes']:9.1f} min  "
              f"{r['cause']:18} {r['detail'][:60]}")

    json.dump(out, open(os.path.join(CACHE, "p5b.json"), "w"), indent=1, default=str)
    print("\nwrote cache/p5b.json")


if __name__ == "__main__":
    main()
