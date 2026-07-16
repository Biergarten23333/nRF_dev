#!/usr/bin/env python3
"""Offline-gateway feasibility pipeline (READ-ONLY).

Reconstructs sweeps from the 7-listener position-high capture and computes
sweep-capture completeness (Task 1), the payload/consistency status
(Tasks 2-3 are inspected here for what IS derivable), and dumps results.json
for the report + figures.

Method notes (each threshold has a provenance comment):
  * Sweep = one tag poll + its 8 anchor responses. Tags poll at 100 ms/tag
    (broadcast_tdma.c APP_TAG_TDMA_SLOT_PERIOD_MS=10, 10 slots => 10 Hz/tag).
  * Intra-listener grouping uses the exact DW1000 rx_ts_lo32 device clock
    (1 tick = 1/(128*499.2e6) s). Window 12 ms covers GUARD 1200us + rank7*1000us
    = 8200us (ss_twr_resp.c) + margin, < the 100 ms cadence => unambiguous.
  * anchor_id == responder rank (verified: offset = 1200 + rank*1000 us).
  * Cross-listener alignment uses the poll's on-air 802.15.4 seq byte (tag-set,
    identical at every listener) + shared host wall-clock (time.time(),
    capture_uwb_poll_listener.py) within 60 ms (< 100 ms cadence).
"""
import glob
import json
import os
import numpy as np
import pandas as pd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNDIR = os.path.join(ROOT, "logs", "overnight_power_position_high_20260715")
LOGDIR = os.path.join(RUNDIR, "listener")
OUT = os.path.dirname(__file__)
LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]
TAGS = [2, 3, 4]                 # 0xB102/03/04
TICKS_PER_US = 128.0 * 499.2     # DW1000 device tick -> us
WRAP = 2**32
WIN_US = 12000.0                 # in-sweep response window (provenance above)
ALIGN_S = 0.060                  # cross-listener host-time gate (provenance above)
GUARD_US = 1200.0                # ss_twr_resp.c APP_ALT_SS_TWR_GUARD_US
SPACING_US = 1000.0             # ss_twr_resp.c APP_ALT_SS_TWR_RESP_SPACING_US


def find_dir(L):
    hits = sorted(glob.glob(os.path.join(LOGDIR, L, "listener_*", "lrd.csv")))
    return os.path.dirname(hits[0]) if hits else None


def load_listener(L):
    d = find_dir(L)
    lpd = pd.read_csv(os.path.join(d, "lpd.csv"),
                      usecols=["host_epoch_s", "tag_id", "poll_seq", "rx_ts_lo32"])
    lrd = pd.read_csv(os.path.join(d, "lrd.csv"),
                      usecols=["host_epoch_s", "anchor_id", "dst", "rx_ts_lo32"])
    lrd["tag_id"] = lrd["dst"].map(lambda s: int(str(s), 16) - 0xB100)
    lrd = lrd[lrd["tag_id"].isin(TAGS)].copy()
    return lpd, lrd


def reconstruct_listener(L):
    """Return per-sweep DataFrame (poll-anchored): one row per captured poll,
    columns tag, poll_seq, host, mask (8-bit anchor bitmask), and the
    measured poll->response offset samples for anchor/rank checks."""
    lpd, lrd = load_listener(L)
    rows = []
    offset_samples = []   # (anchor_id, dt_us) for Task-3 order/spacing check
    for tag in TAGS:
        p = lpd[lpd["tag_id"] == tag].sort_values("host_epoch_s").reset_index(drop=True)
        r = lrd[lrd["tag_id"] == tag].sort_values("host_epoch_s").reset_index(drop=True)
        if len(p) == 0:
            continue
        ph = p["host_epoch_s"].values
        prx = p["rx_ts_lo32"].values.astype(np.int64)
        pseq = p["poll_seq"].values
        rh = r["host_epoch_s"].values
        rrx = r["rx_ts_lo32"].values.astype(np.int64)
        ra = r["anchor_id"].values.astype(np.int64)
        idx = np.searchsorted(ph, rh, side="right") - 1
        masks = np.zeros(len(p), dtype=np.int32)
        for j in range(len(rh)):
            i = idx[j]
            if i < 0:
                continue
            dt_us = ((int(rrx[j]) - int(prx[i])) % WRAP) / TICKS_PER_US
            if 0 < dt_us < WIN_US:
                a = int(ra[j])
                if 0 <= a <= 7:
                    masks[i] |= (1 << a)
                    offset_samples.append((a, dt_us))
        for i in range(len(p)):
            rows.append((tag, int(pseq[i]), float(ph[i]), int(masks[i])))
    sw = pd.DataFrame(rows, columns=["tag", "poll_seq", "host", "mask"])
    sw["nanch"] = sw["mask"].map(lambda m: bin(m).count("1"))
    off = pd.DataFrame(offset_samples, columns=["anchor", "dt_us"])
    return sw, off


def build_master(sweeps_by_L):
    """Union of captured polls -> master sweep list, clustered per tag by host
    wall-clock proximity. Cross-listener host jitter is p95=3.6 ms (measured,
    scratchpad/jitter.py) and the per-tag cadence is 100 ms, so a 30 ms gap
    cleanly merges every listener's copy of one real poll while separating
    consecutive polls. poll_seq is kept as metadata (majority) not a merge key,
    since a listener that misses a poll would otherwise fragment the cluster.
    Returns DataFrame: tag, poll_seq, host (canonical), + per-listener mask
    (-1 = that listener missed the poll)."""
    GAP_S = 0.030
    recs = []
    for L, (sw, _off) in sweeps_by_L.items():
        for t, s, h, m in zip(sw["tag"], sw["poll_seq"], sw["host"], sw["mask"]):
            recs.append((int(t), int(s), float(h), L, int(m)))
    allp = pd.DataFrame(recs, columns=["tag", "poll_seq", "host", "L", "mask"])
    out = {"tag": [], "poll_seq": [], "host": []}
    for L in LISTENERS:
        out[L] = []
    for tag in TAGS:
        g = allp[allp["tag"] == tag].sort_values("host").reset_index(drop=True)
        if len(g) == 0:
            continue
        h = g["host"].values
        newcl = np.concatenate([[True], np.diff(h) > GAP_S])
        cl = np.cumsum(newcl) - 1
        g["cl"] = cl
        for _, sub in g.groupby("cl"):
            out["tag"].append(tag)
            # majority poll_seq (they should all agree for a real poll)
            out["poll_seq"].append(int(sub["poll_seq"].mode().iloc[0]))
            out["host"].append(float(sub["host"].median()))
            masks = {}
            for L, m in zip(sub["L"], sub["mask"]):
                masks[L] = masks.get(L, 0) | int(m)
            for L in LISTENERS:
                out[L].append(masks.get(L, -1))
    return pd.DataFrame(out).sort_values("host").reset_index(drop=True)


def main():
    print("Loading + reconstructing per listener ...")
    sweeps_by_L = {}
    per_listener = {}
    all_off = []
    for L in LISTENERS:
        sw, off = reconstruct_listener(L)
        sweeps_by_L[L] = (sw, off)
        off2 = off.copy()
        off2["L"] = L
        all_off.append(off2)
        dist = {int(k): int(v) for k, v in sw["nanch"].value_counts().items()}
        anchor_rate = {a: float(((sw["mask"].values >> a) & 1).mean()) for a in range(8)}
        per_listener[L] = {
            "n_sweeps_pollcaptured": int(len(sw)),
            "mean_anchors_per_sweep": float(sw["nanch"].mean()),
            "frac_9of9": float((sw["nanch"] == 8).mean()),
            "nanch_distribution": dist,
            "per_anchor_capture_rate": anchor_rate,
            "per_tag_mean_anchors": {int(t): float(v) for t, v in
                                     sw.groupby("tag")["nanch"].mean().items()},
        }
        print(f"  {L}: {len(sw)} sweeps, mean {sw['nanch'].mean():.2f} anch/sweep, "
              f"9/9={100*(sw['nanch']==8).mean():.2f}%")

    print("Building master sweep list + union coverage ...")
    master = build_master(sweeps_by_L)
    n_master = len(master)
    print(f"  master sweeps (union of captured polls): {n_master}")

    # poll capture rate per listener vs master denominator
    poll_capture = {}
    for L in LISTENERS:
        poll_capture[L] = float((master[L].values >= 0).mean())

    # union coverage: greedy best-first ordering of listeners by mean anchors covered
    # per-master-sweep anchor union across a listener subset
    masks_mat = {L: np.where(master[L].values < 0, 0, master[L].values).astype(np.int32)
                 for L in LISTENERS}
    poll_seen = {L: (master[L].values >= 0) for L in LISTENERS}

    def union_stats(subset):
        um = np.zeros(n_master, dtype=np.int32)
        pc = np.zeros(n_master, dtype=bool)
        for L in subset:
            um |= masks_mat[L]
            pc |= poll_seen[L]
        nanch = np.array([bin(int(x)).count("1") for x in um])
        full9 = (nanch == 8) & pc
        return {
            "mean_anchors_union": float(nanch.mean()),
            "frac_all8_anchors": float((nanch == 8).mean()),
            "frac_poll_captured": float(pc.mean()),
            "frac_9of9": float(full9.mean()),
            "anchor_union_rate": {a: float(((um >> a) & 1).mean()) for a in range(8)},
        }

    # greedy best-first by marginal mean-anchor gain
    remaining = set(LISTENERS)
    order = []
    union_curve = []
    chosen = []
    while remaining:
        best, best_val = None, -1
        for L in remaining:
            val = union_stats(chosen + [L])["mean_anchors_union"]
            if val > best_val:
                best, best_val = L, val
        chosen.append(best)
        remaining.discard(best)
        order.append(best)
        st = union_stats(chosen)
        st["k"] = len(chosen)
        st["added"] = best
        union_curve.append(st)
        print(f"  k={len(chosen)} (+{best}): mean_anch_union={st['mean_anchors_union']:.2f} "
              f"all8={100*st['frac_all8_anchors']:.2f}% pollcap={100*st['frac_poll_captured']:.1f}%")

    # ---- Task 3: rank/order + spacing consistency (what IS derivable) ----
    off_all = pd.concat(all_off, ignore_index=True)
    # expected offset for anchor a (=rank a): GUARD + a*SPACING
    off_all["expected_us"] = GUARD_US + off_all["anchor"] * SPACING_US
    off_all["resid_us"] = off_all["dt_us"] - off_all["expected_us"]
    # only ranks the listener actually catches in bulk (a1,a6) are well-sampled
    rank_check = {}
    for a in range(8):
        s = off_all[off_all["anchor"] == a]["resid_us"]
        if len(s) >= 20:
            rank_check[a] = {"n": int(len(s)), "median_resid_us": float(s.median()),
                             "iqr_us": float(s.quantile(.75) - s.quantile(.25))}
    # measured inter-response spacing == RESP_SPACING? use a1->a6 gap / (6-1)
    # (both well sampled). per-listener median offsets:
    spacing_est = {}
    for L in LISTENERS:
        o = sweeps_by_L[L][1]
        m1 = o[o["anchor"] == 1]["dt_us"].median()
        m6 = o[o["anchor"] == 6]["dt_us"].median()
        if np.isfinite(m1) and np.isfinite(m6):
            spacing_est[L] = float((m6 - m1) / 5.0)

    results = {
        "run": "overnight_power_position_high_20260715",
        "listeners": LISTENERS,
        "n_master_sweeps": int(n_master),
        "constants": {
            "slot_period_ms": 10, "slots": 10, "rate_hz_per_tag": 10,
            "guard_us": GUARD_US, "resp_spacing_us": SPACING_US,
            "resp_frame_len": 36, "poll_frame_len": 17,
            "device_tick_s": 1.0 / (128 * 499.2e6),
        },
        "per_listener": per_listener,
        "poll_capture_rate": poll_capture,
        "union_curve": union_curve,
        "greedy_order": order,
        "task3_rank_offset_residual_us": rank_check,
        "task3_spacing_estimate_us": spacing_est,
    }
    with open(os.path.join(OUT, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    # persist master (compact) for the timeline figure
    master.to_csv(os.path.join(OUT, "_master.csv"), index=False)
    print("Wrote results.json + _master.csv")


if __name__ == "__main__":
    main()
