#!/usr/bin/env python3
"""Stage-1 verification: per-listener sweep reconstruction + completeness.
READ-ONLY. Confirms the per-sweep distinct-anchor distribution that drives D1.
"""
import glob
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
LOGDIR = os.path.join(ROOT, "logs", "overnight_power_position_high_20260715", "listener")
LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]
TICKS_PER_US = 128.0 * 499.2
WRAP = 2**32
WIN_US = 12000.0  # in-sweep window: covers GUARD 1200 + rank7*1000 = 8200us + margin


def find_dir(L):
    hits = sorted(glob.glob(os.path.join(LOGDIR, L, "listener_*", "lrd.csv")))
    return os.path.dirname(hits[0]) if hits else None


def reconstruct(L):
    d = find_dir(L)
    lpd = pd.read_csv(os.path.join(d, "lpd.csv"),
                      usecols=["host_epoch_s", "tag_id", "poll_seq", "rx_ts_lo32"])
    lrd = pd.read_csv(os.path.join(d, "lrd.csv"),
                      usecols=["host_epoch_s", "anchor_id", "dst", "rx_ts_lo32"])
    # dst hex string -> tag_id (0xb102 -> 2)
    lrd["tag_id"] = lrd["dst"].apply(lambda s: int(str(s), 16) - 0xB100)
    sweeps = []  # (tag, poll_seq, host_time, anchor_bitmask, n_anchors)
    for tag in (2, 3, 4):
        p = lpd[lpd["tag_id"] == tag].sort_values("host_epoch_s").reset_index(drop=True)
        r = lrd[lrd["tag_id"] == tag].sort_values("host_epoch_s").reset_index(drop=True)
        if len(p) == 0:
            continue
        p_host = p["host_epoch_s"].values
        p_rx = p["rx_ts_lo32"].values.astype(np.int64)
        p_seq = p["poll_seq"].values
        r_host = r["host_epoch_s"].values
        r_rx = r["rx_ts_lo32"].values.astype(np.int64)
        r_anc = r["anchor_id"].values
        # for each response, find latest poll with host<=resp host (searchsorted)
        idx = np.searchsorted(p_host, r_host, side="right") - 1
        masks = np.zeros(len(p), dtype=np.int32)
        for j in range(len(r_host)):
            i = idx[j]
            if i < 0:
                continue
            dt = (int(r_rx[j]) - int(p_rx[i])) % WRAP
            dt_us = dt / TICKS_PER_US
            if 0 < dt_us < WIN_US:
                a = int(r_anc[j])
                if 0 <= a <= 7:
                    masks[i] |= (1 << a)
        for i in range(len(p)):
            m = int(masks[i])
            sweeps.append((tag, int(p_seq[i]), float(p_host[i]), m, bin(m).count("1")))
    return pd.DataFrame(sweeps, columns=["tag", "poll_seq", "host", "mask", "nanch"])


if __name__ == "__main__":
    L = sys.argv[1] if len(sys.argv) > 1 else "LB"
    sw = reconstruct(L)
    print(f"=== {L}: {len(sw)} reconstructed sweeps (poll-anchored) ===")
    print("distinct-anchors-per-sweep distribution (0..8):")
    vc = sw["nanch"].value_counts().sort_index()
    for k in range(9):
        n = int(vc.get(k, 0))
        print(f"  {k} anchors: {n:6d}  ({100*n/len(sw):5.1f}%)")
    print(f"mean anchors/sweep: {sw['nanch'].mean():.2f}")
    print(f"9/9 (poll+8 all): {100*(sw['nanch']==8).mean():.2f}%")
    print("per-anchor capture rate (fraction of sweeps hearing anchor a):")
    for a in range(8):
        rate = ((sw["mask"].values >> a) & 1).mean()
        print(f"  anchor {a}: {100*rate:5.1f}%")
    print("per-tag mean anchors/sweep:", dict(sw.groupby("tag")["nanch"].mean().round(2)))
