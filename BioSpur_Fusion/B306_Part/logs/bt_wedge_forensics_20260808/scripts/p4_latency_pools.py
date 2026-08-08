#!/usr/bin/env python3
"""P4 -- §6 latency precursor (drift-removed), §9 pool constraints in NODE
time, §10 counter boundaries, plus the notify-stall precursor census that
`publisher_max_us` and `q_hwm_*` make possible.

Drift removal is not optional: the fleet runs ~-33 ppm against the master, so
over 600 s an undetrended residual moves ~20 ms -- the same order as the
signal being looked for. The fit is robust (Theil-Sen on a subsample) per
node per boot segment, on healthy pre-event data only.
"""
import json
import os
import sys
import datetime as dt
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import RUNS, CACHE, OUT  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow.parquet as pq         # noqa: E402


def T(x):
    return dt.datetime.fromtimestamp(x).strftime("%H:%M:%S.%f")[:-3]


def theil_sen(x, y, npair=20000, rng=None):
    rng = rng or np.random.default_rng(12345)
    n = len(x)
    i = rng.integers(0, n, npair)
    j = rng.integers(0, n, npair)
    ok = i != j
    i, j = i[ok], j[ok]
    dx = x[j] - x[i]
    m = np.abs(dx) > 1e-9
    slopes = (y[j][m] - y[i][m]) / dx[m]
    a = float(np.median(slopes))
    b = float(np.median(y - a * x))
    return a, b


class RunData:
    def __init__(self, run):
        r = pq.read_table(os.path.join(CACHE, f"recs_{run}.parquet"))
        self.node = np.array(r.column("node").to_pylist())
        self.kind = r.column("kind").to_numpy()
        self.th = r.column("t_host").to_numpy()
        self.nu = r.column("node_us").to_numpy()
        self.seq = r.column("seq").to_numpy()
        self.pol = pq.read_table(os.path.join(CACHE, f"pol_{run}.parquet"))
        self.que = pq.read_table(os.path.join(CACHE, f"que_{run}.parquet"))
        self.tlm = pq.read_table(os.path.join(CACHE, f"tlm_{run}.parquet"))
        self.nodes = sorted(set(self.node.tolist()))

    def col(self, tbl, nd, name):
        nm = np.array(tbl.column("name").to_pylist())
        m = nm == nd
        try:
            return (tbl.column("t_host").to_numpy()[m],
                    tbl.column(name).to_numpy(zero_copy_only=False)[m])
        except Exception:
            return None, None


def latency(R, nd, onset, kindsel, fit_lo, fit_hi):
    """Detrended master-reception latency residual, in ms."""
    m = (R.node == nd) & (R.kind == kindsel) & (R.nu > 0)
    t, u = R.th[m], R.nu[m].astype(np.float64) * 1e-6
    if len(t) < 500:
        return None
    fit = (t >= fit_lo) & (t <= fit_hi)
    if fit.sum() < 300:
        fit = t <= onset
    a, b = theil_sen(u[fit], t[fit])
    res = (t - (a * u + b)) * 1000.0
    return t, res, a


def stat(res, t, lo, hi):
    m = (t > lo) & (t <= hi)
    if m.sum() < 3:
        return None
    v = res[m]
    return {"n": int(m.sum()), "median": round(float(np.median(v)), 2),
            "p95": round(float(np.percentile(v, 95)), 2),
            "max": round(float(v.max()), 2)}


def main():
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    wedges = [r for r in reg["wedge_candidates"]
              if r["classification"] == "STEADY_STATE_HOST_WEDGE"]
    out = {"latency": {}, "pools": {}, "precursor": {}, "boundaries": {}}
    runs = {}

    # ---------- fleet-wide notify-stall precursor census ----------
    print("=" * 96)
    print("NOTIFY-STALL CENSUS -- every increase of publisher_max_us, all nodes, all runs")
    print("publisher_max_us is a monotone max of the bt_gatt_notify() call duration.")
    print("Each increase is a new record-longest call. Anything >10 ms is a stall.")
    print("=" * 96)
    census = []
    for run in ("N5", "N7", "N8"):
        R = runs.setdefault(run, RunData(run))
        for nd in R.nodes:
            t, v = R.col(R.que, nd, "publisher_max_us")
            if t is None or len(t) < 10:
                continue
            v = np.asarray(v, dtype=np.float64)
            jumps = np.nonzero(np.diff(v) > 0)[0]
            big = [(float(t[i + 1]), float(v[i + 1])) for i in jumps
                   if v[i + 1] >= 10000]
            th_, hw = R.col(R.que, nd, "q_hwm_imu")
            census.append({"run": run, "node": nd,
                           "final_publisher_max_us": float(v[-1]),
                           "n_calls_over_10ms": len(big),
                           "max_q_hwm_imu": int(np.nanmax(hw)) if hw is not None else None,
                           "stalls": [(T(a), int(b)) for a, b in big[-6:]]})
    wnodes = {(w["run"], w["node"]) for w in wedges}
    print(f"{'run':4}{'node':10}{'wedged':>7}{'pubmax_us':>11}{'>10ms':>7}{'qhwm_imu':>9}  recent stalls")
    for c in sorted(census, key=lambda x: (-x["final_publisher_max_us"])):
        print(f"{c['run']:4}{c['node']:10}"
              f"{'YES' if (c['run'], c['node']) in wnodes else '.':>7}"
              f"{c['final_publisher_max_us']:11.0f}{c['n_calls_over_10ms']:7d}"
              f"{str(c['max_q_hwm_imu']):>9}  {c['stalls']}")
    out["precursor"]["census"] = census

    # ---------- per-event latency, pools in node time, boundaries ----------
    for w in wedges:
        run, nd, onset = w["run"], w["node"], w["onset_lower_epoch"]
        R = runs[run]
        key = f"{run}_{nd}"
        print("\n" + "=" * 96)
        print(f"{key}  onset {T(onset)}")

        # ---- §6 latency
        lat = {}
        for lbl, k in (("IMU", 0), ("UWB", 1)):
            r = latency(R, nd, onset, k, onset - 3600, onset - 120)
            if r is None:
                lat[lbl] = "INSUFFICIENT"
                continue
            t, res, a = r
            lat[lbl] = {"ppm_vs_master": round((a - 1.0) * 1e6, 1),
                        "w1800": stat(res, t, onset - 1800, onset - 600),
                        "w600": stat(res, t, onset - 600, onset - 60),
                        "w60": stat(res, t, onset - 60, onset - 10),
                        "w10": stat(res, t, onset - 10, onset - 2),
                        "w2": stat(res, t, onset - 2, onset),
                        "last256": stat(res, t, t[max(0, len(t) - 256)] - 1e-6, onset)}
            print(f"  latency {lbl}: ppm={lat[lbl]['ppm_vs_master']}  " +
                  "  ".join(f"{k2}={v['median']}/{v['p95']}" for k2, v in lat[lbl].items()
                            if isinstance(v, dict)))
        # matched controls, same windows
        ctrl = []
        for c in R.nodes:
            if c == nd:
                continue
            r = latency(R, c, onset, 1, onset - 3600, onset - 120)
            if r is None:
                continue
            t, res, a = r
            s2, s10 = stat(res, t, onset - 60, onset - 10), stat(res, t, onset - 10, onset)
            if s2 and s10:
                ctrl.append({"node": c, "w60_med": s2["median"], "w60_p95": s2["p95"],
                             "w10_med": s10["median"], "w10_p95": s10["p95"]})
        lat["controls"] = ctrl
        if ctrl:
            print(f"  controls UWB w60 median of medians = "
                  f"{np.median([c['w60_med'] for c in ctrl]):.2f} ms, "
                  f"w10 = {np.median([c['w10_med'] for c in ctrl]):.2f} ms")
        out["latency"][key] = lat

        # ---- §9 pools in NODE time
        nmp = np.array(R.pol.column("name").to_pylist())
        mp = nmp == nd
        pt = R.pol.column("t_host").to_numpy()[mp]
        pn = R.pol.column("node_ms").to_numpy(zero_copy_only=False)[mp]
        pre = pt <= onset
        i = int(np.nonzero(pre)[0][-1])
        # node-time of the last data record
        md = (R.node == nd) & (R.th <= onset) & (R.nu > 0)
        last_nu_ms = float(R.nu[md][-1]) / 1000.0
        pools = {c: float(R.pol.column(c).to_numpy(zero_copy_only=False)[mp][i])
                 for c in R.pol.column_names if c.endswith("_avail") or c.endswith("_lw")}
        gap_ms = last_nu_ms - float(pn[i])
        out["pools"][key] = {
            "last_pool_node_ms": float(pn[i]),
            "last_record_node_ms": last_nu_ms,
            "node_time_gap_ms": round(gap_ms, 1),
            "master_time_gap_ms": round((onset - float(pt[i])) * 1000, 1),
            "values": pools,
            "att_drain_time_ms_at_31hz": round(8 / 31.0 * 1000, 1),
        }
        print(f"  pools: last strobe at node_ms={pn[i]:.0f}, last record node_ms="
              f"{last_nu_ms:.0f}  -> gap {gap_ms:.0f} ms "
              f"(att_pool would need {8/31.0*1000:.0f} ms to drain at 31 notif/s)")
        print(f"         {pools}")

        # ---- §10 boundaries
        mi = (R.node == nd) & (R.kind == 0) & (R.th <= onset)
        mu = (R.node == nd) & (R.kind == 1) & (R.th <= onset)
        tq, nok = R.col(R.que, nd, "publisher_count")
        tt, wd = R.col(R.tlm, nd, "watchdog_feeds")
        tw, twr = R.col(R.tlm, nd, "timer_wraps")
        last_nu = float(R.nu[mu][-1])
        b = {
            "imu_seq_last": int(R.seq[mi][-1]),
            "imu_seq_dist_to_wrap": 65536 - int(R.seq[mi][-1]),
            "uwb_sweep_last": int(R.seq[mu][-1]),
            "publisher_count_last": int(nok[-1]) if nok is not None else None,
            "watchdog_feeds_last": int(wd[-1]) if wd is not None else None,
            "timer_wraps": int(twr[-1]) if twr is not None else None,
            "timer2_low32_us_at_onset": int(last_nu % (2 ** 32)),
            "timer2_dist_to_wrap_s": round((2 ** 32 - (last_nu % (2 ** 32))) / 1e6, 1),
            "since_boot_s": round(last_nu / 1e6, 1),
            "imu_seq_wrap_grid_phase_s": round((last_nu / 1e6) % 327.68, 2),
        }
        out["boundaries"][key] = b
        print(f"  boundaries: imu_seq={b['imu_seq_last']} (dist {b['imu_seq_dist_to_wrap']}), "
              f"sweep={b['uwb_sweep_last']}, pub_count={b['publisher_count_last']}, "
              f"uptime={b['since_boot_s']}s, TIMER2 low32 dist to wrap "
              f"{b['timer2_dist_to_wrap_s']}s, wraps={b['timer_wraps']}")

    json.dump(out, open(os.path.join(CACHE, "p4.json"), "w"), indent=1, default=str)
    print("\nwrote cache/p4.json")


if __name__ == "__main__":
    main()
