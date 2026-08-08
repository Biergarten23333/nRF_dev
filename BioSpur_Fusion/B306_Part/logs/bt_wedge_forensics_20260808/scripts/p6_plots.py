#!/usr/bin/env python3
"""One parquet + one PNG per event, and the IMU latency control distribution
the §6 verdict needs."""
import json
import os
import sys
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CACHE, OUT, PLOTS  # noqa: E402

import numpy as np                   # noqa: E402
import pyarrow as pa                 # noqa: E402
import pyarrow.parquet as pq         # noqa: E402
import matplotlib                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt      # noqa: E402
from p4_latency_pools import RunData, latency, T   # noqa: E402


def main():
    os.makedirs(PLOTS, exist_ok=True)
    reg = json.load(open(os.path.join(OUT, "WEDGE_EVENTS.json")))
    wedges = [r for r in reg["wedge_candidates"]
              if r["classification"] == "STEADY_STATE_HOST_WEDGE"]
    runs = {}
    ctrl_summary = {}
    for w in wedges:
        run, nd, on = w["run"], w["node"], w["onset_lower_epoch"]
        R = runs.setdefault(run, RunData(run))
        key = f"{run}_{nd}"

        # ---- parquet: every record of this node in [-1800, +60] s
        m = (R.node == nd) & (R.th > on - 1800) & (R.th < on + 60)
        tbl = pa.table({"t_host": R.th[m], "kind": R.kind[m],
                        "node_us": R.nu[m], "seq": R.seq[m]})
        pq.write_table(tbl, os.path.join(OUT, f"EVENT_{key}.parquet"),
                       compression="zstd")

        fig, ax = plt.subplots(4, 1, figsize=(11, 11), sharex=True)
        rel = lambda t: t - on            # noqa: E731

        # 1) delivered rate, node vs controls
        edges = np.arange(-1800, 61, 5.0)
        for lbl, k, col in (("IMU", 0, "tab:blue"), ("UWB", 1, "tab:orange")):
            t = R.th[(R.node == nd) & (R.kind == k)]
            h, _ = np.histogram(rel(t), edges)
            ax[0].step(edges[:-1], h / 5.0, where="post", color=col, label=f"{nd} {lbl}")
        cn = [c for c in R.nodes if c != nd]
        for k, col in ((0, "tab:blue"), (1, "tab:orange")):
            hs = []
            for c in cn:
                t = R.th[(R.node == c) & (R.kind == k)]
                h, _ = np.histogram(rel(t), edges)
                hs.append(h / 5.0)
            if hs:
                ax[0].step(edges[:-1], np.median(hs, 0), where="post", color=col,
                           alpha=0.35, ls="--", label="controls median")
        ax[0].set_ylabel("records/s"); ax[0].legend(fontsize=7, ncol=2)
        ax[0].set_title(f"{key}  onset {T(on)}   ({w['classification']})")

        # 2) detrended latency residual
        for lbl, k, col in (("IMU", 0, "tab:blue"), ("UWB", 1, "tab:orange")):
            r = latency(R, nd, on, k, on - 3600, on - 120)
            if r is None:
                continue
            t, res, _ = r
            s = (t > on - 1800) & (t <= on)
            ax[1].plot(rel(t[s]), res[s], ".", ms=1, color=col, label=f"{lbl} residual")
        ax[1].set_ylabel("latency residual (ms)"); ax[1].legend(fontsize=7)
        ax[1].axhline(0, color="k", lw=0.4)

        # 3) node queue / notify counters
        tq = np.array(R.que.column("name").to_pylist()) == nd
        qt = R.que.column("t_host").to_numpy()[tq]
        for c, col in (("q_hwm_imu", "tab:green"), ("q_hwm_uwb", "tab:red"),
                       ("q_hwm_ctl", "tab:purple")):
            v = R.que.column(c).to_numpy(zero_copy_only=False)[tq]
            ax[2].plot(rel(qt), v, lw=1, color=col, label=c)
        ax2b = ax[2].twinx()
        pm = R.que.column("publisher_max_us").to_numpy(zero_copy_only=False)[tq]
        ax2b.plot(rel(qt), np.asarray(pm, float) / 1000.0, lw=1, color="k",
                  label="publisher_max_us (ms)")
        ax2b.set_ylabel("publisher_max_us (ms)")
        ax[2].set_ylabel("queue high-water"); ax[2].legend(fontsize=7, loc="upper left")
        ax2b.legend(fontsize=7, loc="upper right")

        # 4) pools
        tp = np.array(R.pol.column("name").to_pylist()) == nd
        pt = R.pol.column("t_host").to_numpy()[tp]
        for c in ("att_pool_avail", "acl_tx_pool_avail", "hci_rx_pool_avail",
                  "hci_cmd_pool_avail"):
            if c in R.pol.column_names:
                v = R.pol.column(c).to_numpy(zero_copy_only=False)[tp]
                ax[3].plot(rel(pt), v, lw=1, label=c)
        ax[3].set_ylabel("pool avail"); ax[3].set_xlabel("seconds relative to onset")
        ax[3].legend(fontsize=7, ncol=2)
        for a in ax:
            a.axvline(0, color="r", lw=0.8)
            a.set_xlim(-1800, 60)
            a.grid(alpha=0.2)
        fig.tight_layout()
        fig.savefig(os.path.join(OUT, f"EVENT_{key}.png"), dpi=110)
        plt.close(fig)

        # ---- IMU latency control distribution for the §6 verdict
        vals = []
        for c in cn:
            r = latency(R, c, on, 0, on - 3600, on - 120)
            if r is None:
                continue
            t, res, _ = r
            a = (t > on - 600) & (t <= on - 60)
            b = (t > on - 60) & (t <= on)
            if a.sum() > 100 and b.sum() > 100:
                vals.append(float(np.median(res[b]) - np.median(res[a])))
        rr = latency(R, nd, on, 0, on - 3600, on - 120)
        ev = None
        if rr:
            t, res, _ = rr
            a = (t > on - 600) & (t <= on - 60)
            b = (t > on - 60) & (t <= on)
            ev = float(np.median(res[b]) - np.median(res[a]))
        ctrl_summary[key] = {
            "event_imu_step_ms": round(ev, 2) if ev is not None else None,
            "control_steps_ms": [round(v, 2) for v in sorted(vals)],
            "control_median": round(float(np.median(vals)), 2) if vals else None,
            "control_absmax": round(float(np.max(np.abs(vals))), 2) if vals else None}
        print(f"{key}: IMU 60 s-vs-600 s median step = "
              f"{ctrl_summary[key]['event_imu_step_ms']} ms, controls "
              f"median {ctrl_summary[key]['control_median']} ms, "
              f"|max| {ctrl_summary[key]['control_absmax']} ms  "
              f"(n={len(vals)})")
    json.dump(ctrl_summary, open(os.path.join(CACHE, "imu_step.json"), "w"), indent=1)
    print("wrote EVENT_*.parquet / EVENT_*.png")


if __name__ == "__main__":
    main()
