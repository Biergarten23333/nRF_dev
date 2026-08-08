#!/usr/bin/env python3
"""P2 -- one detector, applied identically to N5/N6/N7/N8.

Detection is recomputed from the record streams. `events.jsonl` and any
DATA_PLANE_SILENT marker are cross-checks only (v43 could not see its own
wedge in N7).

Definitions, fixed here and used everywhere downstream:
  joint stall  both IMU and UWB delivery gapped at the same time
  onset_lower  last delivered record of EITHER stream before the gap
  onset_upper  onset_lower + one nominal inter-arrival of the slower stream
               (UWB, 120 ms) -- the first deadline that was provably missed
  wedge        joint stall >= 20 s
  near miss    joint stall in [T_near, 20 s) that RECOVERED
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT, NODES  # noqa: E402

import numpy as np                      # noqa: E402
import pyarrow.parquet as pq            # noqa: E402

WEDGE_S = 20.0
NEAR_S = 2.0
UWB_NOM = 0.120
IMU_NOM = 0.050


def load(run, name, cols=None):
    p = os.path.join(CACHE, f"{name}_{run}.parquet")
    if not os.path.exists(p):
        return None
    return pq.read_table(p, columns=cols)


def gaps(t, thresh):
    """Return (i, t[i], t[i+1]) for every consecutive pair further than
    thresh apart. t must be sorted."""
    if len(t) < 2:
        return []
    d = np.diff(t)
    idx = np.nonzero(d > thresh)[0]
    return [(int(i), float(t[i]), float(t[i + 1])) for i in idx]


def joint_stalls(t_imu, t_uwb, t_end, min_s):
    """Intervals where BOTH streams are silent for >= min_s.

    Built by intersecting per-stream silence intervals. A stream that has
    ended contributes silence to t_end.
    """
    def silences(t):
        out = []
        if len(t) == 0:
            return out
        for _, a, b in gaps(t, min_s * 0.5):
            out.append((a, b))
        if t_end - t[-1] >= min_s * 0.5:
            out.append((float(t[-1]), float(t_end)))
        return out

    a_list, b_list = silences(t_imu), silences(t_uwb)
    res = []
    j = 0
    for a0, a1 in a_list:
        while j < len(b_list) and b_list[j][1] < a0:
            j += 1
        k = j
        while k < len(b_list) and b_list[k][0] < a1:
            lo, hi = max(a0, b_list[k][0]), min(a1, b_list[k][1])
            if hi - lo >= min_s:
                res.append((lo, hi))
            k += 1
    return res


def run_detect(run):
    cfg = RUNS[run]
    rec = load(run, "recs")
    if rec is None:
        return None
    node = np.array(rec.column("node").to_pylist())
    kind = rec.column("kind").to_numpy()
    th = rec.column("t_host").to_numpy()
    order = np.argsort(th, kind="stable")
    node, kind, th = node[order], kind[order], th[order]
    t_run0, t_run1 = float(th[0]), float(th[-1])

    qos = load(run, "qos", ["t_host", "name", "reports", "crc_ok", "crc_error",
                            "nak", "rx_timeout", "event_gaps", "handle",
                            "delivered_imu", "delivered_uwb", "delivered_ctl"])
    qn = np.array(qos.column("name").to_pylist()) if qos else np.array([])
    qt = qos.column("t_host").to_numpy() if qos else np.array([])

    tlm = load(run, "tlm", ["t_host", "name", "node_ms", "reset_reason",
                            "watchdog_feeds", "imu_hreset", "imu_hrecover_ok",
                            "imu_hrecover_fail", "imu_i2c_err", "uart_restarts",
                            "timer_wraps", "notify_ok", "drop_unsub", "drop_err"])
    tn = np.array(tlm.column("name").to_pylist()) if tlm else np.array([])
    tt = tlm.column("t_host").to_numpy() if tlm else np.array([])
    tnode_ms = tlm.column("node_ms").to_numpy() if tlm else np.array([])

    out = {"run": run, "fw": cfg["fw"], "master_fw": cfg["master_fw"],
           "t_run0": t_run0, "t_run1": t_run1,
           "duration_h": (t_run1 - t_run0) / 3600.0,
           "nodes": {}}

    for nd in sorted(set(node.tolist())):
        m = node == nd
        ti = th[m & (kind == 0)]
        tu = th[m & (kind == 1)]
        if len(ti) == 0 and len(tu) == 0:
            continue
        qm = qt[qn == nd] if len(qn) else np.array([])
        tm = tt[tn == nd] if len(tn) else np.array([])
        nms = tnode_ms[tn == nd] if len(tn) else np.array([])
        # boot segments: node_ms going backwards
        boots = []
        if len(nms) > 1:
            back = np.nonzero(np.diff(nms) < 0)[0]
            boots = [float(tm[i + 1]) for i in back]

        stalls = joint_stalls(ti, tu, t_run1, NEAR_S)
        recs = []
        for lo, hi in stalls:
            dur = hi - lo
            terminal = hi >= t_run1 - 1.0
            # QoS continuity strictly after onset
            q_after = qm[(qm > lo + 1.0) & (qm < hi)]
            q_alive_s = float(q_after[-1] - lo) if len(q_after) else 0.0
            tlm_after = tm[(tm > lo + 1.0) & (tm < hi)]
            resets = [b for b in boots if lo < b < hi + 5]
            recs.append({
                "onset_lower": lo, "onset_upper": lo + UWB_NOM,
                "recover_t": None if terminal else hi,
                "dur_s": dur, "terminal": terminal,
                "qos_alive_after_s": q_alive_s,
                "qos_records_during": int(len(q_after)),
                "tlm_records_during": int(len(tlm_after)),
                "reset_during": len(resets) > 0,
                "class_pre": "WEDGE_CANDIDATE" if dur >= WEDGE_S else "NEAR_MISS",
            })
        # single-stream stops (one silent, the other alive)
        single = []
        for label, ta, tb, nom in (("IMU_ONLY", ti, tu, IMU_NOM),
                                   ("UWB_ONLY", tu, ti, UWB_NOM)):
            for _, a, b in gaps(ta, max(NEAR_S, nom * 20)):
                other = tb[(tb > a) & (tb < b)]
                if len(other) >= 3:      # the other stream kept running
                    single.append({"kind": label, "t0": float(a), "t1": float(b),
                                   "dur_s": float(b - a),
                                   "other_records": int(len(other))})
            if len(ta) and t_run1 - ta[-1] >= WEDGE_S:
                other = tb[tb > ta[-1]]
                if len(other) >= 3:
                    single.append({"kind": label + "_TERMINAL", "t0": float(ta[-1]),
                                   "t1": t_run1, "dur_s": float(t_run1 - ta[-1]),
                                   "other_records": int(len(other))})

        out["nodes"][nd] = {
            "imu_records": int(len(ti)), "uwb_records": int(len(tu)),
            "t_first": float(min(ti[0] if len(ti) else 9e18,
                                 tu[0] if len(tu) else 9e18)),
            "t_last": float(max(ti[-1] if len(ti) else 0,
                                tu[-1] if len(tu) else 0)),
            "boot_segments": len(boots) + 1, "boot_times": boots,
            "joint_stalls": recs, "single_stream": single,
        }
    return out


def main():
    allr = {}
    for run in ("N5", "N6", "N7", "N8"):
        r = run_detect(run)
        if r is None:
            print(f"{run}: INSUFFICIENT -- no extracted records")
            continue
        allr[run] = r
        nw = sum(1 for n in r["nodes"].values()
                 for s in n["joint_stalls"] if s["dur_s"] >= WEDGE_S)
        nn = sum(1 for n in r["nodes"].values()
                 for s in n["joint_stalls"] if s["dur_s"] < WEDGE_S)
        print(f"{run}: {r['duration_h']:.2f} h, {len(r['nodes'])} nodes, "
              f"{nw} joint stalls >={WEDGE_S:.0f}s, {nn} near-misses >={NEAR_S:.0f}s")
    json.dump(allr, open(os.path.join(CACHE, "detect_raw.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
