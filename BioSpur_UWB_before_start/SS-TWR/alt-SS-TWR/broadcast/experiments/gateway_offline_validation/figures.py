#!/usr/bin/env python3
"""Figures + time-stability for the offline-gateway feasibility study (READ-ONLY).
  fig_per_anchor_capture.png   - per-listener per-anchor(rank) capture bars
  fig_union_vs_k.png           - union anchor coverage + poll capture vs #gateways
  fig_completeness_timeline.png- completeness over the hour + movement overlay
"""
import json
import os
import datetime as dt
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = os.path.dirname(__file__)
FIG = os.path.join(OUT, "figures")
RUNDIR = os.path.join(os.path.abspath(os.path.join(OUT, "..", "..")),
                      "logs", "overnight_power_position_high_20260715")
LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]

res = json.load(open(os.path.join(OUT, "results.json")))
master = pd.read_csv(os.path.join(OUT, "_master.csv"))

# ---- host epoch anchor for movement ISO -> epoch mapping ----
# manifest ts_start "2026-07-15 09:30:07" corresponds to min host_epoch_s.
h0 = master["host"].min()
manifest = json.load(open(os.path.join(RUNDIR, "listener_manifest.json")))
t0 = dt.datetime.strptime(manifest["ts_start"], "%Y-%m-%d %H:%M:%S")
sec_midnight0 = t0.hour * 3600 + t0.minute * 60 + t0.second


def iso_to_epoch(iso):
    hh, mm, ss = map(int, iso.split(":"))
    return h0 + (hh * 3600 + mm * 60 + ss - sec_midnight0)


mv = json.load(open(os.path.join(RUNDIR, "movement_events.json")))
mv_epochs = [(iso_to_epoch(e["start_iso"]), e.get("dur_s", 1.0)) for e in mv["events"]]

# =====================================================================
# FIG 1: per-listener per-anchor(rank) capture rate
# =====================================================================
fig, ax = plt.subplots(figsize=(9, 4.5))
x = np.arange(8)
w = 0.115
for i, L in enumerate(LISTENERS):
    rates = [100 * res["per_listener"][L]["per_anchor_capture_rate"][str(a)] for a in range(8)]
    ax.bar(x + (i - 3) * w, rates, w, label=L)
ax.set_xlabel("anchor id  (= responder rank; on-air offset = 1200 + rank*1000 us after poll)")
ax.set_ylabel("capture rate (% of poll-anchored sweeps)")
ax.set_title("Per-listener anchor-response capture — every listener catches only rank 1 & rank 6\n"
             "(identical pattern across 7 spatially-diverse listeners => firmware throughput limit, not RF geometry)")
ax.set_xticks(x)
ax.axhline(0, color="k", lw=0.5)
ax.legend(ncol=7, fontsize=7, loc="upper center")
ax.set_ylim(0, 100)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig_per_anchor_capture.png"), dpi=130)
plt.close(fig)

# =====================================================================
# FIG 2: union coverage vs k
# =====================================================================
uc = res["union_curve"]
ks = [u["k"] for u in uc]
mean_anch = [u["mean_anchors_union"] for u in uc]
pollcap = [100 * u["frac_poll_captured"] for u in uc]
all8 = [100 * u["frac_all8_anchors"] for u in uc]
order = res["greedy_order"]

fig, ax1 = plt.subplots(figsize=(8, 4.5))
ax1.plot(ks, mean_anch, "o-", color="tab:red", label="mean anchors/sweep (of 8)")
ax1.axhline(8, color="tab:red", ls=":", lw=1, label="target = 8 (full sweep)")
ax1.set_xlabel("number of gateways in union (greedy best-first: " + " → ".join(order) + ")")
ax1.set_ylabel("mean anchors captured per sweep", color="tab:red")
ax1.set_ylim(0, 8.4)
ax1.tick_params(axis="y", labelcolor="tab:red")
ax2 = ax1.twinx()
ax2.plot(ks, pollcap, "s--", color="tab:blue", label="poll captured (% of union sweeps)")
ax2.plot(ks, all8, "^--", color="tab:green", label="all-8 anchors (%)")
ax2.set_ylabel("percent", color="tab:blue")
ax2.set_ylim(0, 105)
ax2.tick_params(axis="y", labelcolor="tab:blue")
lines1, lab1 = ax1.get_legend_handles_labels()
lines2, lab2 = ax2.get_legend_handles_labels()
ax1.legend(lines1 + lines2, lab1 + lab2, fontsize=8, loc="center right")
ax1.set_title("Union coverage vs #gateways — anchor capture saturates at ~1.9/8; all-8 stays 0.00%\n"
              "(spatial diversity recovers POLLS but not RESPONSES: misses are temporally common-mode)")
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig_union_vs_k.png"), dpi=130)
plt.close(fig)

# =====================================================================
# FIG 3: completeness timeline + movement overlay
# =====================================================================
BIN = 30.0  # s
master = master.sort_values("host").reset_index(drop=True)
t = master["host"].values
tb = ((t - h0) / BIN).astype(int)
best1 = order[0]
best2 = order[:2]


def mask_anch(L):
    v = master[L].values
    v = np.where(v < 0, 0, v)
    return np.array([bin(int(x)).count("1") for x in v])


single = mask_anch(best1)
uni2 = master[best2].values
uni2 = np.where(uni2 < 0, 0, uni2)
uni2 = np.bitwise_or.reduce(uni2, axis=1)
uni2n = np.array([bin(int(x)).count("1") for x in uni2])
pollcap1 = (master[best1].values >= 0).astype(float)

df = pd.DataFrame({"tb": tb, "single": single, "uni2": uni2n, "pc1": pollcap1})
g = df.groupby("tb").agg(single=("single", "mean"), uni2=("uni2", "mean"),
                         pc1=("pc1", "mean"), n=("single", "size")).reset_index()
g["tmin"] = g["tb"] * BIN / 60.0

fig, (axA, axB) = plt.subplots(2, 1, figsize=(11, 6), sharex=True,
                               gridspec_kw={"height_ratios": [2, 1]})
axA.plot(g["tmin"], g["single"], lw=1.2, color="tab:orange", label=f"1 gateway ({best1})")
axA.plot(g["tmin"], g["uni2"], lw=1.2, color="tab:red", label=f"2-gateway union ({'+'.join(best2)})")
axA.axhline(8, color="gray", ls=":", lw=1, label="target 8")
for me, dur in mv_epochs:
    axA.axvspan((me - h0) / 60.0, (me - h0 + dur) / 60.0, color="purple", alpha=0.25, lw=0)
axA.plot([], [], color="purple", alpha=0.4, lw=6, label="movement event (operator walking)")
axA.set_ylabel("anchors captured / sweep")
axA.set_ylim(0, 8.4)
axA.legend(fontsize=8, ncol=2, loc="upper right")
axA.set_title("Sweep-capture completeness over the hour (30 s bins) with movement overlay\n"
              "Capture is flat ~1.8-1.9/8 regardless of operator movement — the limit is firmware, not RF occlusion")
axB.plot(g["tmin"], 100 * g["pc1"], lw=1.0, color="tab:blue", label=f"poll capture, 1 gateway ({best1})")
axB.plot(g["tmin"], g["n"] / g["n"].max() * 100, lw=0.8, color="gray", alpha=0.6,
         label="sweeps/bin (norm.)  — gaps = between-round idle")
for me, dur in mv_epochs:
    axB.axvspan((me - h0) / 60.0, (me - h0 + dur) / 60.0, color="purple", alpha=0.25, lw=0)
axB.set_ylabel("percent")
axB.set_xlabel("minutes since listener start (09:30:07)")
axB.legend(fontsize=8, loc="lower right")
axB.set_ylim(0, 105)
fig.tight_layout()
fig.savefig(os.path.join(FIG, "fig_completeness_timeline.png"), dpi=130)
plt.close(fig)

# ---- movement-window vs quiet completeness (numeric for report) ----
inmv = np.zeros(len(master), dtype=bool)
for me, dur in mv_epochs:
    inmv |= (t >= me - 1.0) & (t <= me + dur + 1.0)
stab = {
    "bin_s": BIN,
    "single_mean_anch_overall": float(single.mean()),
    "single_mean_anch_in_movement": float(single[inmv].mean()) if inmv.any() else None,
    "single_mean_anch_quiet": float(single[~inmv].mean()),
    "uni2_mean_anch_overall": float(uni2n.mean()),
    "n_sweeps_in_movement_windows": int(inmv.sum()),
    "timeline_bins": len(g),
}
json.dump(stab, open(os.path.join(OUT, "time_stability.json"), "w"), indent=2)
print("time stability:", json.dumps(stab, indent=2))
print("Figures written to", FIG)
