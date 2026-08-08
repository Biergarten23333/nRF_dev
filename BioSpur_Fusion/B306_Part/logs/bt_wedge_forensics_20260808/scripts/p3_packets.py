#!/usr/bin/env python3
"""P3 -- event packets (§4), path-activity census (§5), triggers (§7),
terminal notification sequence (§8).

Every panel carries matched same-window healthy controls; a number without
its fleet context has repeatedly misled this project.
"""
import json
import os
import sys
import datetime as dt
from collections import defaultdict, Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow.parquet as pq         # noqa: E402

# Wire payload sizes, from sizeof() on B306_Part/include/biospur_fusion_ble.h
SIZE = {"UWB": 184, "IMU": 150, "TELEMETRY": 243, "QUEUE": 58, "POOL": 140}
WINDOWS = [1800, 600, 60, 10, 2]


def T(x, frac=3):
    s = dt.datetime.fromtimestamp(x).strftime("%H:%M:%S.%f")
    return s[:-(6 - frac)] if frac else s[:-7]


class Run:
    def __init__(self, run):
        self.run = run
        r = pq.read_table(os.path.join(CACHE, f"recs_{run}.parquet"))
        self.node = np.array(r.column("node").to_pylist())
        self.kind = r.column("kind").to_numpy()
        self.th = r.column("t_host").to_numpy()
        self.mm = r.column("master_ms").to_numpy()
        self.nu = r.column("node_us").to_numpy()
        self.seq = r.column("seq").to_numpy()
        o = np.argsort(self.th, kind="stable")
        for a in ("node", "kind", "th", "mm", "nu", "seq"):
            setattr(self, a, getattr(self, a)[o])
        self.t0, self.t1 = float(self.th[0]), float(self.th[-1])
        self.tlm = pq.read_table(os.path.join(CACHE, f"tlm_{run}.parquet"))
        self.que = pq.read_table(os.path.join(CACHE, f"que_{run}.parquet"))
        self.pol = pq.read_table(os.path.join(CACHE, f"pol_{run}.parquet"))
        self.qos = pq.read_table(os.path.join(CACHE, f"qos_{run}.parquet"))
        self.ctl = [json.loads(l) for l in
                    open(os.path.join(CACHE, f"ctl_{run}.jsonl"))]
        self.nodes = sorted(set(self.node.tolist()))

    def tab(self, t, nd, cols=None):
        nm = np.array(t.column("name").to_pylist())
        m = nm == nd
        d = {"t_host": t.column("t_host").to_numpy()[m]}
        for c in (cols or t.column_names):
            if c in ("name", "t_host"):
                continue
            try:
                d[c] = t.column(c).to_numpy(zero_copy_only=False)[m]
            except Exception:
                pass
        return d

    def stream_t(self, nd, k):
        return self.th[(self.node == nd) & (self.kind == k)]


def rate_panel(R, nd, onset, controls):
    """Per-stream delivered rate in windows before onset, node vs controls."""
    out = {}
    for lbl, k in (("IMU", 0), ("UWB", 1)):
        t = R.stream_t(nd, k)
        row = {}
        for w in WINDOWS:
            n = int(((t > onset - w) & (t <= onset)).sum())
            row[f"{w}s"] = round(n / w, 3)
        cr = []
        for c in controls:
            tc = R.stream_t(c, k)
            cr.append([int(((tc > onset - w) & (tc <= onset)).sum()) / w for w in WINDOWS])
        if cr:
            a = np.array(cr)
            row["ctrl_median"] = {f"{w}s": round(float(np.median(a[:, i])), 3)
                                  for i, w in enumerate(WINDOWS)}
        out[lbl] = row
    return out


def counter_panel(R, nd, onset):
    """Last delivered value of every node counter before onset, plus its
    delta over the preceding 60 s and 600 s."""
    res = {}
    for name, tbl in (("telemetry", R.tlm), ("queue", R.que), ("pool", R.pol)):
        d = R.tab(tbl, nd)
        t = d["t_host"]
        pre = t <= onset
        if not pre.any():
            res[name] = "INSUFFICIENT -- no records before onset"
            continue
        i = int(np.nonzero(pre)[0][-1])
        row = {"t_last": T(float(t[i])), "lag_to_onset_s": round(onset - float(t[i]), 3)}
        for c, v in d.items():
            if c == "t_host" or v.dtype.kind not in "iuf":
                continue
            last = v[i]
            if last is None or (isinstance(last, float) and np.isnan(last)):
                continue
            row[c] = int(last) if float(last).is_integer() else float(last)
            for w, tag in ((60, "d60"), (600, "d600")):
                j = np.nonzero(t <= onset - w)[0]
                if len(j):
                    dv = last - v[int(j[-1])]
                    if dv:
                        row.setdefault("_deltas", {})[f"{c}.{tag}"] = float(dv)
        res[name] = row
    return res


def qos_panel(R, nd, onset, controls):
    d = R.tab(R.qos, nd)
    t = d["t_host"]
    pre = (t > onset - 600) & (t <= onset)
    post = (t > onset) & (t <= onset + 600)
    def summ(mask):
        if not mask.any():
            return None
        return {k: round(float(np.nanmean(v[mask])), 3)
                for k, v in d.items()
                if k not in ("t_host",) and v.dtype.kind in "iuf"
                and k in ("reports", "crc_ok", "crc_error", "nak", "rx_timeout",
                          "event_gaps", "window_ms")}
    ctrl = []
    for c in controls:
        dc = R.tab(R.qos, c)
        m = (dc["t_host"] > onset - 600) & (dc["t_host"] <= onset)
        if m.any() and "reports" in dc:
            ctrl.append(float(np.nanmean(dc["reports"][m])))
    return {"pre600": summ(pre), "post600": summ(post),
            "ctrl_reports_pre600_median": round(float(np.median(ctrl)), 2) if ctrl else None,
            "n_post_records": int(post.sum())}


def channel_shift(R, nd, onset):
    """Channel-map-update proxy: L1 distance between the normalised channel
    histogram before and after onset (§5.2)."""
    d = R.tab(R.qos, nd)
    t = d["t_host"]
    chs = [c for c in d if c.startswith("ch")]
    if not chs:
        return None
    def h(mask):
        v = np.array([np.nansum(d[c][mask]) for c in sorted(chs)], float)
        s = v.sum()
        return v / s if s else v
    a, b = (t > onset - 300) & (t <= onset), (t > onset) & (t <= onset + 300)
    if not a.any() or not b.any():
        return None
    return {"l1": round(float(np.abs(h(a) - h(b)).sum()), 4),
            "used_pre": int((h(a) > 0).sum()), "used_post": int((h(b) > 0).sum())}


def inbound(R, nd, onset, wins=(60, 10, 2, 1, 0.1)):
    """Every master->node operation, bucketed by lead time before onset."""
    ev = []
    for c in R.ctl:
        tgt = c.get("target") or c.get("name")
        if tgt != nd:
            continue
        ty = c["type"]
        if ty in ("FUSION_REPLY",):     # node -> master, not inbound work
            continue
        ev.append((c["t_host"], ty, c.get("line", c.get("raw", ""))[:70]))
    ev.sort()
    pre = {f"{w}s": [(round(onset - t, 3), ty, ln) for t, ty, ln in ev
                     if onset - w < t <= onset] for w in wins}
    post = [(round(t - onset, 3), ty, ln) for t, ty, ln in ev
            if onset < t <= onset + 300]
    return {"pre": {k: v for k, v in pre.items()}, "post300": post[:20],
            "last_before": (round(onset - ev[-1][0], 3), ev[-1][1], ev[-1][2])
            if ev and ev[-1][0] <= onset else None}


def terminal_records(R, nd, onset, n=256):
    """Last n delivered records with stream, size, generation and reception."""
    m = (R.node == nd) & (R.th <= onset)
    idx = np.nonzero(m)[0][-n:]
    out = []
    for i in idx:
        k = int(R.kind[i])
        out.append({"t": float(R.th[i]), "stream": "IMU" if k == 0 else "UWB",
                    "bytes": SIZE["IMU" if k == 0 else "UWB"],
                    "master_ms": int(R.mm[i]), "node_us": int(R.nu[i]),
                    "seq": int(R.seq[i])})
    return out


def conn_event_packing(recs):
    """Cluster master_ms mod 50 ms; count notifications per connection event."""
    if not recs:
        return None
    ms = np.array([r["master_ms"] for r in recs])
    ce = ms // 50
    c = Counter(ce.tolist())
    per = list(c.values())
    return {"n_records": len(recs), "n_conn_events": len(c),
            "notif_per_event_mean": round(float(np.mean(per)), 3),
            "notif_per_event_max": int(max(per)),
            "anchor_phase_mod50_hist": dict(sorted(Counter((ms % 50).tolist()).items())[:8])}


def drain_tail(recs, onset):
    """§4.y -- inter-arrival profile of the last records, and how compressed
    the very end is relative to the stream's own nominal spacing."""
    if len(recs) < 20:
        return None
    t = np.array([r["t"] for r in recs])
    d = np.diff(t)
    tail = {}
    for k in (5, 10, 20, 50):
        tail[f"last{k}_mean_gap_ms"] = round(float(np.mean(d[-k:]) * 1000), 2)
    tail["baseline_mean_gap_ms"] = round(float(np.median(d)) * 1000, 2)
    # records whose gap is < 25 % of baseline = back-to-back = were queued
    base = float(np.median(d))
    run_ = 0
    for x in d[::-1]:
        if x < 0.25 * base:
            run_ += 1
        else:
            break
    tail["trailing_back_to_back_records"] = run_
    tail["last_record_to_onset_ms"] = round((onset - t[-1]) * 1000, 2)
    return tail


def simultaneity(R, nd, onset):
    """§4.x -- order the last record of every stream."""
    res = {}
    for lbl, k in (("IMU", 0), ("UWB", 1)):
        t = R.stream_t(nd, k)
        t = t[t <= onset + 1]
        res[lbl] = float(t[-1]) if len(t) else None
    for lbl, tbl in (("TELEMETRY", R.tlm), ("QUEUE", R.que), ("POOL", R.pol)):
        d = R.tab(tbl, nd)["t_host"]
        d = d[d <= onset + 1]
        res[lbl] = float(d[-1]) if len(d) else None
    q = R.tab(R.qos, nd)
    if "delivered_ctl" in q:
        t, v = q["t_host"], q["delivered_ctl"]
        m = t <= onset + 60
        if m.any():
            vv, tt = v[m], t[m]
            ch = np.nonzero(np.diff(vv) > 0)[0]
            res["QOS_delivered_ctl_last_increment"] = float(tt[ch[-1] + 1]) if len(ch) else None
    base = max(x for x in res.values() if x)
    res["_spread_ms"] = {k: round((base - v) * 1000, 1)
                         for k, v in res.items() if v and not k.startswith("_")}
    res["_last_of_all"] = T(base)
    return res


def build(run, node, onset, label):
    R = CACHE_RUNS.setdefault(run, Run(run))
    controls = [n for n in R.nodes if n != node]
    # keep only controls that were healthy through the window
    good = []
    for c in controls:
        t = R.stream_t(c, 1)
        if ((t > onset - 600) & (t <= onset)).sum() > 4000 and \
           ((t > onset) & (t <= onset + 60)).sum() > 300:
            good.append(c)
    pkt = {
        "run": run, "node": node, "label": label,
        "onset_lower": onset, "onset_lower_wall": T(onset),
        "controls_used": good, "n_controls": len(good),
        "rates": rate_panel(R, node, onset, good),
        "counters": counter_panel(R, node, onset),
        "qos": qos_panel(R, node, onset, good),
        "channel_shift": channel_shift(R, node, onset),
        "inbound": inbound(R, node, onset),
        "simultaneity": simultaneity(R, node, onset),
    }
    tr = terminal_records(R, node, onset)
    pkt["terminal"] = {
        "last8": [{"t": T(r["t"], 3), "stream": r["stream"], "bytes": r["bytes"],
                   "seq": r["seq"], "node_us": r["node_us"]} for r in tr[-8:]],
        "stream_mix_last256": dict(Counter(r["stream"] for r in tr)),
        "packing": conn_event_packing(tr),
        "drain_tail": drain_tail(tr, onset),
    }
    # matched control terminal packing, same window
    cp = []
    for c in good[:6]:
        crt = terminal_records(R, c, onset)
        p = conn_event_packing(crt)
        d = drain_tail(crt, onset)
        if p and d:
            cp.append({"node": c, **{k: p[k] for k in
                                     ("notif_per_event_mean", "notif_per_event_max")},
                       "trailing_back_to_back_records": d["trailing_back_to_back_records"],
                       "baseline_mean_gap_ms": d["baseline_mean_gap_ms"]})
    pkt["terminal"]["controls"] = cp
    return pkt


CACHE_RUNS = {}

EVENTS = [
    ("N7", "BSF6C53", 1786112183.012, "N7 wedge"),
    ("N8", "BSFEC35", None, "N8 wedge 1"),
    ("N8", "BSF1120", None, "N8 wedge 2"),
    ("N8", "BSF44AD", None, "N8 wedge 3 (newly pinned)"),
]

if __name__ == "__main__":
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    onsets = {(r["run"], r["node"]): r["onset_lower_epoch"]
              for r in reg["wedge_candidates"]
              if r["classification"] == "STEADY_STATE_HOST_WEDGE"}
    out = {}
    for run, nd, _, label in EVENTS:
        onset = onsets.get((run, nd))
        if onset is None:
            print(f"{run}/{nd}: INSUFFICIENT -- not in registry as a wedge")
            continue
        p = build(run, nd, onset, label)
        out[f"{run}_{nd}"] = p
        print(f"=== {run} {nd} onset {p['onset_lower_wall']} "
              f"controls={p['n_controls']}")
    json.dump(out, open(os.path.join(CACHE, "event_packets.json"), "w"),
              indent=1, default=str)
    print("wrote cache/event_packets.json")
