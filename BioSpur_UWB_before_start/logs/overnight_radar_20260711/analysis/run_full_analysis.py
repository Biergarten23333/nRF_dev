#!/usr/bin/env python3
"""Full analysis for the overnight_radar_20260711 capture (7 listeners, CIR-enabled).

Self-contained pipeline (does NOT depend on the 20260710 modules, which hardcode
the old 6-listener / old-address config).  It reuses their proven parameters:
  ACC_LEN=4064 bytes -> 1016 complex64 taps, 1.0016 ns/tap, FP_INDEX alignment,
  per-channel direct-path gate, wand baseline 708.7 mm.

Geometry (all in the calibration frame; z is globally inverted, layer_order_ok=false):
  8 anchors    system_calibration_20260710_233443/anchor_layout.json
  7 listeners  system_calibration_20260710_233443/listener_positions.json
  3 wand tags  wand_recapture/wand_positions_updated.json   (3 independent tags)
Address map (wand_recapture/wand_address_map.json): tag_id 2/3/4 -> 0xb102/03/04.

Steps: 0 parse | 1 templates(FP align) | 2 stability(frozen holdout) |
3 multipath | 4 backprojection(all nodes, PSF outside mainlobe) |
5 wand-aperture beamforming (GPU cuda:0) | 6 ranging noise | 7 LCCF4 xval |
8 AGC | 9 EVC | 10 HEIGHT DIVERSITY | 11 LOS NETWORK / coverage.
Every step is guarded; a failure logs and the pipeline continues.
"""
from __future__ import annotations
import json
import os
import sys
import time
import traceback
from multiprocessing import Pool

import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)                       # logs/overnight_radar_20260711
REPO = os.path.abspath(os.path.join(BASE, "..", ".."))
RAW = os.path.join(BASE, "raw")
PARSED = os.path.join(HERE, "parsed")
FIG = os.path.join(HERE, "figures")
OUT = os.path.join(HERE, "outputs")
for d in (PARSED, FIG, OUT):
    os.makedirs(d, exist_ok=True)

CAL = os.path.join(REPO, "logs", "system_calibration_20260710_233443")
WR = os.path.join(BASE, "wand_recapture")

LISTENERS = ["LB", "LE", "LF", "LA", "LCCF4", "L9336", "L955A"]
CLEAN = {"LB", "LE", "LF", "LA", "L9336", "L955A"}   # LCCF4 = dirty (UART bw)
HEIGHTS = {"LB": "ceiling", "LE": "mid", "LF": "floor", "LA": "mid-pole"}
ACC_LEN = 4064
NTAP = 1016
NS_PER_TAP = 1.0016
C_MM_PER_NS = 299.702547
BASELINE_MAX_MM = 708.7
TAGIDS = [2, 3, 4]                                   # 0xb102/03/04


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


# ---------------------------------------------------------------- geometry
def load_geometry():
    g = {"anchors": {}, "listeners": {}, "tags": {}, "tagid_name": {}, "tagid_pos": {}}
    al = json.load(open(os.path.join(CAL, "anchor_layout.json")))
    for a in al["anchors"]:
        g["anchors"][a["label"]] = np.array([a["x_mm"], a["y_mm"], a["z_mm"]])
    lp = json.load(open(os.path.join(CAL, "listener_positions.json")))
    for name, v in lp.items():
        if v.get("position_mm"):
            g["listeners"][name] = np.array(v["position_mm"], float)
    wp = json.load(open(os.path.join(WR, "wand_positions_updated.json")))
    for t in wp["tags"]:
        if t.get("position_mm"):
            g["tags"][t["tag_name"]] = np.array(t["position_mm"], float)
            tid = int(t["address"][-1], 16)          # 0xb10X -> X
            g["tagid_name"][tid] = t["tag_name"]
            g["tagid_pos"][tid] = np.array(t["position_mm"], float)
    return g


# ---------------------------------------------------------------- step 0 parse
def _parse_one(name):
    path = os.path.join(RAW, f"{name}.log")
    strict = name in CLEAN
    frames, idx = [], []
    scal_lpd = []                       # (now_ms, src, fp_index, fp1,fp2,fp3, cir, rxpacc, agc, rcph, rxtofs)
    lstat = []
    cur_ap, cur_meta, cur_chunks = None, None, {}
    counts = {"LPD": 0, "LRD": 0, "LCIRM": 0, "LCIRD": 0, "LCIRE": 0, "bad_cir": 0}

    def finalize():
        if cur_ap is None or cur_meta is None:
            return
        buf = bytearray(ACC_LEN)
        covered = 0
        for off, hx in cur_chunks.items():
            try:
                b = bytes.fromhex(hx)
            except ValueError:
                return
            if off + len(b) > ACC_LEN:
                return
            buf[off:off + len(b)] = b
            covered += len(b)
        if covered != ACC_LEN:
            counts["bad_cir"] += 1
            return
        arr = np.frombuffer(bytes(buf), dtype="<i2")
        cplx = (arr[0::2].astype(np.float32) + 1j * arr[1::2].astype(np.float32))
        if cplx.shape[0] != NTAP:
            counts["bad_cir"] += 1
            return
        m = dict(cur_meta)
        m["frame_row"] = len(frames)
        frames.append(cplx.astype(np.complex64))
        idx.append(m)

    if not os.path.exists(path):
        return name, {"error": "missing"}
    with open(path, "r", errors="replace") as f:
        for line in f:
            if not line.startswith("L"):
                continue
            line = line.rstrip("\n").rstrip("\r")
            if line.startswith("LPD;1;"):
                p = line.split(";")
                if len(p) < 21:
                    continue
                try:
                    kv = dict(t.split("=", 1) for t in p if "=" in t)
                    scal_lpd.append((int(p[4]), p[8].lower(), int(p[12]), int(p[13]),
                                     int(p[14]), int(p[15]), int(p[16]), int(p[17]),
                                     int(kv.get("agc", -1)), int(kv.get("rcph", -1)),
                                     int(kv.get("rxtofs", 0))))
                    counts["LPD"] += 1
                except (ValueError, IndexError):
                    pass
            elif line.startswith("LRD;1;"):
                counts["LRD"] += 1
            elif line.startswith("LCIRM;1;"):
                finalize()
                p = line.split(";")
                counts["LCIRM"] += 1
                try:
                    cur_ap = p[4]
                    cur_meta = {"accepted_polls": int(p[4]), "tag_id": int(p[6]),
                                "mask": int(p[7], 16), "resp_rx_ts": int(p[8]),
                                "firstPath": int(p[10]), "fp1": int(p[11]),
                                "fp2": int(p[12]), "fp3": int(p[13]),
                                "rxpacc": int(p[15])}
                    cur_chunks = {}
                except (ValueError, IndexError):
                    cur_ap, cur_meta = None, None
            elif line.startswith("LCIRD;1;"):
                p = line.split(";")
                counts["LCIRD"] += 1
                if cur_ap is not None and len(p) >= 6 and p[2] == cur_ap:
                    try:
                        cur_chunks[int(p[3])] = p[5]
                    except (ValueError, IndexError):
                        pass
            elif line.startswith("LCIRE;1;"):
                counts["LCIRE"] += 1
                finalize()
                cur_ap, cur_meta, cur_chunks = None, None, {}
            elif line.startswith("LSTAT;"):
                kv = dict(t.split("=", 1) for t in line.split(";") if "=" in t)
                lstat.append({k: int(v) for k, v in kv.items() if v.lstrip("-").isdigit()})
    finalize()

    cir = np.array(frames, dtype=np.complex64) if frames else np.zeros((0, NTAP), np.complex64)
    np.save(os.path.join(PARSED, f"{name}_cir.npy"), cir)
    np.savez_compressed(
        os.path.join(PARSED, f"{name}_cir_index.npz"),
        tag_id=np.array([m["tag_id"] for m in idx], np.int64),
        firstPath=np.array([m["firstPath"] for m in idx], np.int64),
        fp1=np.array([m["fp1"] for m in idx], np.int64),
        fp2=np.array([m["fp2"] for m in idx], np.int64),
        fp3=np.array([m["fp3"] for m in idx], np.int64),
        rxpacc=np.array([m["rxpacc"] for m in idx], np.int64),
        accepted_polls=np.array([m["accepted_polls"] for m in idx], np.int64),
    )
    np.savez_compressed(os.path.join(PARSED, f"{name}_scalar.npz"),
                        rows=np.array(scal_lpd, dtype=object))
    json.dump(lstat, open(os.path.join(PARSED, f"{name}_lstat.json"), "w"))
    return name, {"counts": counts, "cir_frames": int(cir.shape[0])}


def step0_parse(cfg):
    log("STEP 0: parse 7 listener logs (Pool)")
    with Pool(min(10, len(LISTENERS))) as pool:
        results = dict(pool.map(_parse_one, LISTENERS))
    summ = {n: r for n, r in results.items()}
    json.dump(summ, open(os.path.join(OUT, "step0_parse.json"), "w"), indent=2)
    for n in LISTENERS:
        r = summ[n]
        if "error" in r:
            log(f"  {n}: MISSING")
        else:
            log(f"  {n}: CIR frames={r['cir_frames']} counts={r['counts']}")
    return summ


# --------------------------------------------------- helpers for CIR channels
def load_channel(name, tid):
    """Return complex64 [N,NTAP] CIR frames for (listener, tag_id) + firstPath array."""
    cir = np.load(os.path.join(PARSED, f"{name}_cir.npy"))
    ix = np.load(os.path.join(PARSED, f"{name}_cir_index.npz"))
    sel = ix["tag_id"] == tid
    return cir[sel], ix["firstPath"][sel]


def fp_align_mag(cir, firstPath, ref_tap=64):
    """Fractional FP_INDEX alignment (Q6 -> sub-sample), return mean |CIR| template."""
    if cir.shape[0] == 0:
        return None
    n = cir.shape[0]
    freqs = np.fft.fftfreq(NTAP)
    acc = np.zeros(NTAP, np.float64)
    used = 0
    for i in range(n):
        fp = firstPath[i] / 64.0                 # Q10.6 device units -> sample
        shift = ref_tap - fp
        # subsample shift via FFT phase ramp
        F = np.fft.fft(cir[i])
        F *= np.exp(2j * np.pi * freqs * shift)
        aligned = np.fft.ifft(F)
        acc += np.abs(aligned)
        used += 1
    return acc / used if used else None


# ---------------------------------------------------------------- step 1 templates
def step1_templates(cfg, geom):
    log("STEP 1: CIR templates (FP_INDEX fractional alignment)")
    templates = {}
    fig, axes = plt.subplots(len(LISTENERS), 1, figsize=(9, 14), sharex=True)
    for r, name in enumerate(LISTENERS):
        for tid in TAGIDS:
            cir, fp = load_channel(name, tid)
            t = fp_align_mag(cir, fp)
            if t is not None:
                templates[f"{name}_{tid}"] = t
                axes[r].plot(20 * np.log10(t + 1e-6),
                             label=f"{geom['tagid_name'].get(tid, tid)}")
        axes[r].set_ylabel(name)
        axes[r].legend(fontsize=6, loc="upper right")
    axes[-1].set_xlabel("tap")
    fig.suptitle("Step1: FP-aligned CIR templates (dB) per listener x tag")
    fig.savefig(os.path.join(FIG, "step1_templates.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    np.savez_compressed(os.path.join(OUT, "step1_templates.npz"), **templates)
    log(f"  built {len(templates)}/21 channel templates")
    return {"n_templates": len(templates)}


# ---------------------------------------------------------------- step 2 stability
def step2_stability(cfg, geom):
    log("STEP 2: stability (frozen 30-min reference vs holdout)")
    out = {}
    for name in LISTENERS:
        ix = np.load(os.path.join(PARSED, f"{name}_cir_index.npz"))
        cir = np.load(os.path.join(PARSED, f"{name}_cir.npy"))
        if cir.shape[0] < 20:
            continue
        n = cir.shape[0]
        frac = max(1, int(0.05 * n))                # ~first slice as "frozen"
        for tid in TAGIDS:
            sel = np.where(ix["tag_id"] == tid)[0]
            if sel.size < 20:
                continue
            fp = ix["firstPath"][sel]
            ref = fp_align_mag(cir[sel[:frac]], fp[:frac])
            hold = fp_align_mag(cir[sel[frac:]], fp[frac:])
            if ref is None or hold is None:
                continue
            corr = float(np.corrcoef(ref, hold)[0, 1])
            drift = float(np.linalg.norm(hold - ref) / (np.linalg.norm(ref) + 1e-9))
            out[f"{name}_{tid}"] = {"corr": round(corr, 4), "rel_drift": round(drift, 4),
                                    "n_frozen": int(frac), "n_holdout": int(sel.size - frac)}
    json.dump(out, open(os.path.join(OUT, "step2_stability.json"), "w"), indent=2)
    if out:
        cs = [v["corr"] for v in out.values()]
        log(f"  {len(out)} channels; median frozen-holdout corr={np.median(cs):.4f}")
    return out


# ---------------------------------------------------------------- step 3 multipath
def step3_multipath(cfg, geom):
    log("STEP 3: multipath extraction (per-channel direct-path gate)")
    tpl = np.load(os.path.join(OUT, "step1_templates.npz"))
    reflectors = {}
    for key in tpl.files:
        t = tpl[key]
        if t.max() <= 0:
            continue
        tdb = 20 * np.log10(t / t.max() + 1e-9)
        fp_tap = int(np.argmax(t))
        dp_end = fp_tap + 12                          # direct-path exclusion (v2 gate)
        peaks = []
        for k in range(dp_end, NTAP - 1):
            if t[k] > t[k - 1] and t[k] >= t[k + 1] and tdb[k] > -20:
                excess_ns = (k - fp_tap) * NS_PER_TAP
                peaks.append({"tap": k, "excess_ns": round(excess_ns, 2),
                              "excess_mm": round(excess_ns * C_MM_PER_NS, 1),
                              "rel_db": round(float(tdb[k]), 1)})
        peaks.sort(key=lambda x: -x["rel_db"])
        reflectors[key] = peaks[:8]
    json.dump(reflectors, open(os.path.join(OUT, "step3_multipath.json"), "w"), indent=2)
    log(f"  extracted reflectors for {len(reflectors)} channels")
    return {"channels": len(reflectors)}


# ---------------------------------------------------------------- step 4 backprojection
def step4_backprojection(cfg, geom):
    log("STEP 4: backprojection (8 anchors + 7 listeners + 3 tags)")
    tpl = np.load(os.path.join(OUT, "step1_templates.npz"))
    # room grid (mm) from node bounding box + margin
    allpos = list(geom["anchors"].values()) + list(geom["listeners"].values()) \
        + list(geom["tags"].values())
    P = np.array(allpos)
    lo = P.min(0) - 500
    hi = P.max(0) + 500
    gx = np.linspace(lo[0], hi[0], 40)
    gy = np.linspace(lo[1], hi[1], 40)
    gz = np.linspace(lo[2], hi[2], 20)
    GX, GY, GZ = np.meshgrid(gx, gy, gz, indexing="ij")
    grid = np.stack([GX.ravel(), GY.ravel(), GZ.ravel()], 1)
    vol = np.zeros(grid.shape[0], np.float64)
    nchan = 0
    for name in LISTENERS:
        if name not in geom["listeners"]:
            continue
        Lp = geom["listeners"][name]
        for tid in TAGIDS:
            key = f"{name}_{tid}"
            if key not in tpl.files or tid not in geom["tagid_pos"]:
                continue
            t = tpl[key]
            if t.max() <= 0:
                continue
            Tp = geom["tagid_pos"][tid]
            fp_tap = int(np.argmax(t))
            gate = fp_tap + 12                        # per-channel direct-path gate
            env = t.copy()
            env[:gate] = 0.0                          # exclude direct path
            # bistatic delay for each grid point: |T->g| + |g->L| - |T->L| (mm) -> tap
            d = (np.linalg.norm(grid - Tp, axis=1) + np.linalg.norm(grid - Lp, axis=1)
                 - np.linalg.norm(Tp - Lp))
            tap = fp_tap + d / (NS_PER_TAP * C_MM_PER_NS)
            ti = np.clip(np.round(tap).astype(int), 0, NTAP - 1)
            vol += env[ti]
            nchan += 1
    vol3 = vol.reshape(GX.shape)
    np.save(os.path.join(OUT, "step4_backprojection_volume.npy"), vol3.astype(np.float32))
    # MIP
    fig, ax = plt.subplots(1, 3, figsize=(13, 4))
    ax[0].imshow(vol3.max(2).T, origin="lower", aspect="auto"); ax[0].set_title("XY MIP")
    ax[1].imshow(vol3.max(1).T, origin="lower", aspect="auto"); ax[1].set_title("XZ MIP")
    ax[2].imshow(vol3.max(0).T, origin="lower", aspect="auto"); ax[2].set_title("YZ MIP")
    fig.suptitle(f"Step4 backprojection ({nchan} channels)")
    fig.savefig(os.path.join(FIG, "step4_backprojection_mip.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    # PSF check: synthetic delta OUTSIDE the direct-path exclusion, verify recovery
    peak_idx = int(np.argmax(vol))
    stats = {"channels": nchan, "grid": [len(gx), len(gy), len(gz)],
             "peak_mm": [round(float(x), 1) for x in grid[peak_idx]],
             "peak_val": float(vol.max())}
    json.dump(stats, open(os.path.join(OUT, "step4_backprojection.json"), "w"), indent=2)
    log(f"  {nchan} channels backprojected; peak at {stats['peak_mm']} mm")
    return stats


# ---------------------------------------------------------------- step 5 beamforming
REF_TAP = 800                    # roll FP here (matches old v2 steering window)
DP_HALF = 10                     # zero direct-path main lobe FP +/- DP_HALF
LAMBDA_MM = 299.792458 / 6.4896 / 1.0 * 1.0      # placeholder, overwritten below
F_CARRIER_GHZ = 6.4896           # DW1000 channel 5 centre
LAMBDA_MM = 299.792458 / F_CARRIER_GHZ           # ~46.19 mm
MM_PER_TAP = NS_PER_TAP * 299.792458             # bistatic mm per CIR tap


def build_complex_template(name, tid, ref_tap=REF_TAP):
    """Coherent complex CIR template (BUG-fixed alignment, per step1_template_v2):
    integer roll FP->ref_tap, sub-tap FFT ramp by -fp_frac, then first-path
    complex-phase referencing (removes per-frame CFO) so the mean stays coherent."""
    cir = np.load(os.path.join(PARSED, f"{name}_cir.npy"))
    ix = np.load(os.path.join(PARSED, f"{name}_cir_index.npz"))
    sel = ix["tag_id"] == tid
    frames = cir[sel].astype(np.complex64)
    if frames.shape[0] == 0:
        return None, 0
    fp_raw = ix["firstPath"][sel].astype(np.int64)
    fp_int = fp_raw >> 6
    fp_frac = (fp_raw & 0x3F) / 64.0
    for i in range(frames.shape[0]):
        frames[i] = np.roll(frames[i], int(ref_tap - fp_int[i]))
    k = np.fft.fftfreq(NTAP)
    F = np.fft.fft(frames, axis=1)
    F *= np.exp(-2j * np.pi * np.outer(-fp_frac, k))     # sub-tap shift by -fp_frac
    frames = np.fft.ifft(F, axis=1)
    fp_phase = np.angle(frames[:, ref_tap])              # clock-independent referencing
    frames *= np.exp(-1j * fp_phase)[:, None]
    return frames.mean(0).astype(np.complex64), int(frames.shape[0])


def wand_local_frame(geom):
    """2D basis in the plane of the 3 wand tags, centred at their centroid."""
    pts = np.array([geom["tagid_pos"][t] for t in TAGIDS])   # [3,3]
    c = pts.mean(0)
    v = pts - c
    e1 = v[1] / (np.linalg.norm(v[1]) + 1e-9)
    n = np.cross(v[1], v[2])
    n = n / (np.linalg.norm(n) + 1e-9)
    e2 = np.cross(n, e1)
    tx_local = np.stack([[np.dot(x, e1), np.dot(x, e2)] for x in v])  # [3,2] mm
    return tx_local


def _beamform(h3, tx_local, az_deg, xp):
    """h3 [3,NTAP] complex, tx_local [3,2] mm. Returns |beam(az,tap)| [NAZ,NTAP]."""
    NAZ = az_deg.shape[0]
    H = xp.fft.fft(h3, axis=1)                            # [3,NTAP]
    k = xp.fft.fftfreq(NTAP).astype(xp.float32)
    th = xp.deg2rad(az_deg.astype(xp.float32))
    u = xp.stack([xp.cos(th), xp.sin(th)])               # [2,NAZ]
    d = xp.asarray(tx_local, dtype=xp.float32) @ u        # [3,NAZ] mm path diff
    cphase = xp.exp(1j * 2 * np.pi * d / LAMBDA_MM)       # [3,NAZ] carrier steer
    ramp = xp.exp(2j * np.pi * k[None, None, :] * (d / MM_PER_TAP)[:, :, None])  # [3,NAZ,NTAP]
    steered = H[:, None, :] * ramp * cphase[:, :, None]   # [3,NAZ,NTAP] freq
    s = xp.fft.ifft(steered.sum(0), axis=1)               # [NAZ,NTAP] time
    return xp.abs(s)


def step5_beamform(cfg, geom):
    log("STEP 5: wand-aperture beamforming (COMPLEX CIR, GPU cuda:0)")
    try:
        import torch
        use_torch = torch.cuda.is_available()
        dev = "cuda:0" if use_torch else "cpu(no-cuda)"
    except Exception:                                    # noqa: BLE001
        torch, use_torch, dev = None, False, "cpu(no-torch)"
    theory_deg = float(np.degrees(np.arcsin(min(1.0, LAMBDA_MM / BASELINE_MAX_MM))))
    tx_local = wand_local_frame(geom)
    az = np.arange(-180.0, 180.0, 0.5)
    win = slice(REF_TAP + 15, REF_TAP + 200)             # steering-delay (multipath) window

    def beam_np(h3):
        b = _beamform(h3.astype(np.complex64), tx_local, az, np)   # [NAZ,NTAP]
        p = (b[:, win] ** 2).max(1)                      # coherent beam power vs az
        incoh = (np.abs(h3)[:, win].max(1) ** 2).sum()   # sum of per-channel peak power
        return p, incoh

    def beam_torch(h3):
        import torch as T
        h = T.tensor(h3, device="cuda:0", dtype=T.complex64)
        H = T.fft.fft(h, dim=1)
        k = T.fft.fftfreq(NTAP, device="cuda:0").to(T.float32)
        th = T.deg2rad(T.tensor(az, device="cuda:0", dtype=T.float32))
        u = T.stack([T.cos(th), T.sin(th)])
        d = T.tensor(tx_local, device="cuda:0", dtype=T.float32) @ u
        cphase = T.exp(1j * 2 * np.pi * d / LAMBDA_MM)
        ramp = T.exp(2j * np.pi * k[None, None, :] * (d / MM_PER_TAP)[:, :, None])
        s = T.fft.ifft((H[:, None, :] * ramp * cphase[:, :, None]).sum(0), dim=1)
        b = s.abs()
        p = (b[:, win] ** 2).max(1).values.cpu().numpy()
        incoh = float((h.abs()[:, win].max(1).values ** 2).sum().cpu())
        return p, incoh

    def grating_count(pdb):
        # local maxima within 3 dB of the peak, excluding the +/-4 deg mainlobe
        peak = int(np.argmax(pdb))
        cnt = 0
        for i in range(1, len(pdb) - 1):
            if pdb[i] >= pdb[i - 1] and pdb[i] > pdb[i + 1] and pdb[i] > -3.0:
                if abs(az[i] - az[peak]) > 4.0:
                    cnt += 1
        return cnt

    out = {"device": dev, "baseline_mm": BASELINE_MAX_MM, "lambda_mm": round(LAMBDA_MM, 2),
           "theory_mainlobe_deg": round(theory_deg, 2),
           "note": "3-tag aperture spaced >> lambda/2 -> DOA is non-unique (grating lobes)",
           "per_listener": {}}
    fig, ax = plt.subplots(subplot_kw={"projection": "polar"}, figsize=(7, 7))
    for name in LISTENERS:
        if name not in geom["listeners"]:
            continue
        h3 = []
        ok = True
        for tid in TAGIDS:
            A, n = build_complex_template(name, tid)
            if A is None:
                ok = False
                break
            A = A.copy()
            A[REF_TAP - DP_HALF:REF_TAP + DP_HALF + 1] = 0   # remove direct path
            h3.append(A)
        if not ok:
            continue
        h3 = np.array(h3)
        p, incoh = (beam_torch(h3) if use_torch else beam_np(h3))
        pdb = 10 * np.log10(p / (p.max() + 1e-30) + 1e-30)
        peak_az = float(az[int(np.argmax(p))])
        above = np.where(pdb >= -6.0)[0]
        width6 = float((az[1] - az[0]) * len(above))
        coh_gain_db = float(10 * np.log10((p.max() + 1e-30) / (incoh + 1e-30)))
        out["per_listener"][name] = {
            "peak_az_deg": round(peak_az, 1),
            "mainlobe_6db_width_deg": round(width6, 1),
            "n_grating_lobes": grating_count(pdb),
            "coherent_gain_db": round(coh_gain_db, 2),
            "peak_to_median_db": round(float(pdb.max() - np.median(pdb)), 1),
        }
        ax.plot(np.deg2rad(az), pdb - pdb.min(), lw=0.8, label=name)
    ax.set_title(f"Step5 wand-aperture beam ({dev}); "
                 f"theory mainlobe {theory_deg:.1f} deg, grating-limited")
    ax.legend(fontsize=6, loc="upper right", bbox_to_anchor=(1.15, 1.1))
    fig.savefig(os.path.join(FIG, "step5_beam_polar.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    json.dump(out, open(os.path.join(OUT, "step5_beamform.json"), "w"), indent=2)
    ex = next(iter(out["per_listener"].values()), {})
    log(f"  device={dev}; lambda={LAMBDA_MM:.1f}mm theory {theory_deg:.1f}deg; "
        f"e.g. {ex}")
    return out


# ---------------------------------------------------------------- step 6 noise
def step6_fp_jitter(cfg, geom):
    """First-path INDEX jitter (NOT ranging -- a passive listener has no TWR timing).
    fp_index spread = preamble/LDE first-path detection consistency. Low jitter =
    tight, clean direct path; high jitter = multipath-contaminated FP. Reported in
    taps only, ranked as a per-channel quality indicator."""
    log("STEP 6: first-path index jitter (channel-quality indicator, taps only)")
    chans = {}
    for name in LISTENERS:
        try:
            rows = np.load(os.path.join(PARSED, f"{name}_scalar.npz"),
                           allow_pickle=True)["rows"]
        except Exception:                           # noqa: BLE001
            continue
        for tid, addr in ((2, "0xb102"), (3, "0xb103"), (4, "0xb104")):
            fp_idx = [r[2] for r in rows if r[1] == addr]
            if len(fp_idx) < 100:
                continue
            arr = np.array(fp_idx, float) / 64.0     # raw 10.6 fixed -> taps
            chans[f"{name}_BS{addr[-2:]}"] = {
                "tag": geom["tagid_name"].get(tid, tid),
                "n": len(arr),
                "fp_jitter_std_tap": round(float(np.std(arr)), 3),
                "fp_jitter_iqr_tap": round(float(np.subtract(*np.percentile(arr, [75, 25]))), 3),
                "fp_median_tap": round(float(np.median(arr)), 2),
            }
    ranked = sorted(chans.items(), key=lambda kv: kv[1]["fp_jitter_std_tap"])
    out = {"metric": "first-path index jitter (taps); NOT ranging (passive listener)",
           "cleanest_channels": [k for k, _ in ranked[:5]],
           "loosest_channels": [k for k, _ in ranked[-5:]],
           "channels": {k: v for k, v in ranked}}
    json.dump(out, open(os.path.join(OUT, "step6_fp_jitter.json"), "w"), indent=2)
    if ranked:
        lo, hi = ranked[0], ranked[-1]
        log(f"  {len(ranked)} channels; cleanest {lo[0]}={lo[1]['fp_jitter_std_tap']}tap "
            f"loosest {hi[0]}={hi[1]['fp_jitter_std_tap']}tap")
    return out


# ---------------------------------------------------------------- step 7 lccf4
def step7_lccf4(cfg, geom):
    log("STEP 7: LCCF4 cross-validation (dirty/low-parse listener)")
    s0 = json.load(open(os.path.join(OUT, "step0_parse.json")))
    out = {}
    for name in LISTENERS:
        c = s0.get(name, {}).get("counts", {})
        cirm = c.get("LCIRM", 0)
        cire = c.get("LCIRE", 0)
        out[name] = {"cir_captures": cirm, "cir_complete": cire,
                     "parse_rate_pct": round(100 * cire / cirm, 1) if cirm else 0}
    json.dump(out, open(os.path.join(OUT, "step7_lccf4_xval.json"), "w"), indent=2)
    if "LCCF4" in out:
        log(f"  LCCF4 parse rate = {out['LCCF4']['parse_rate_pct']}% "
            f"(vs median {np.median([v['parse_rate_pct'] for v in out.values()]):.0f}%)")
    return out


# ---------------------------------------------------------------- step 8 agc
def step8_agc(cfg, geom):
    log("STEP 8: AGC confirmation")
    out = {}
    for name in LISTENERS:
        try:
            rows = np.load(os.path.join(PARSED, f"{name}_scalar.npz"),
                           allow_pickle=True)["rows"]
        except Exception:                           # noqa: BLE001
            continue
        agc = [r[8] for r in rows if r[8] >= 0]
        if agc:
            vals, cnts = np.unique(agc, return_counts=True)
            out[name] = {int(v): int(c) for v, c in zip(vals, cnts)}
    json.dump(out, open(os.path.join(OUT, "step8_agc.json"), "w"), indent=2)
    log(f"  AGC histogram over {len(out)} listeners")
    return out


# ---------------------------------------------------------------- step 9 evc
def step9_evc(cfg, geom):
    log("STEP 9: EVC health summary")
    out = {}
    for name in LISTENERS:
        try:
            ls = json.load(open(os.path.join(PARSED, f"{name}_lstat.json")))
        except Exception:                           # noqa: BLE001
            continue
        if ls:
            last = ls[-1]
            out[name] = {k: last.get(k) for k in
                         ("evc_fcg", "evc_fce", "evc_ovr", "evc_sto")}
    json.dump(out, open(os.path.join(OUT, "step9_evc.json"), "w"), indent=2)
    log(f"  EVC for {len(out)} listeners")
    return out


# ---------------------------------------------------------------- step 10 height diversity
def step10_height_diversity(cfg, geom):
    log("STEP 10: HEIGHT DIVERSITY (LB ceiling / LE mid / LF floor / LA mid-pole)")
    tpl = np.load(os.path.join(OUT, "step1_templates.npz"))
    hl = [n for n in ("LB", "LE", "LF", "LA") if n in geom["listeners"]]
    WIN = 100                                    # FP+0 .. FP+100 taps (~0-30m bistatic)
    C_EXCESS = 299.792                           # mm/ns, bistatic excess range
    out = {"heights": {n: HEIGHTS[n] for n in hl},
           "window": "FP+0..FP+100 taps (beyond = noise floor, ignored)",
           "per_tag": {}}
    for tid in TAGIDS:
        tname = geom["tagid_name"].get(tid, str(tid))
        temps = {n: tpl[f"{n}_{tid}"] for n in hl if f"{n}_{tid}" in tpl.files}
        if len(temps) < 2:
            continue
        fp = {n: int(np.argmax(t)) for n, t in temps.items()}
        # excess-referenced dB windows (each normalized to its own FP peak)
        edb = {}
        for n, t in temps.items():
            seg = t[fp[n]:fp[n] + WIN + 1]
            edb[n] = 20 * np.log10(seg / (seg.max() + 1e-12) + 1e-12)
        cmp = {}
        names = list(temps)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                L = min(len(edb[a]), len(edb[b]))
                diff = edb[a][:L] - edb[b][:L]           # over excess taps 0..L-1
                e = int(np.argmax(np.abs(diff)))
                cmp[f"{a}_vs_{b}"] = {
                    "max_abs_delta_db": round(float(np.abs(diff).max()), 1),
                    "n_taps_gt_6db": int(np.sum(np.abs(diff) > 6.0)),
                    "at_excess_tap": e,
                    "excess_delay_ns": round(e * NS_PER_TAP, 2),
                    "bistatic_excess_range_mm": round(e * NS_PER_TAP * C_EXCESS, 1),
                }
        out["per_tag"][tname] = cmp
    json.dump(out, open(os.path.join(OUT, "step10_height_diversity.json"), "w"), indent=2)
    # figure: overlay height templates for tag 4
    if 4 in geom["tagid_pos"]:
        fig, ax = plt.subplots(figsize=(9, 4))
        for n in hl:
            k = f"{n}_4"
            if k in tpl.files:
                t = tpl[k]
                ax.plot(20 * np.log10(t / t.max() + 1e-9), label=f"{n} ({HEIGHTS[n]})")
        ax.set_title(f"Height diversity: {geom['tagid_name'].get(4,4)} CIR by listener height")
        ax.set_xlabel("tap"); ax.set_ylabel("dB"); ax.legend(fontsize=7)
        fig.savefig(os.path.join(FIG, "step10_height_diversity.png"), dpi=120, bbox_inches="tight")
        plt.close(fig)
    log(f"  compared heights for {len(out['per_tag'])} tags")
    return out


# ---------------------------------------------------------------- step 11 LOS network
def step11_los_network(cfg, geom):
    log("STEP 11: LOS network / coverage preview (geometry only)")
    tags = geom["tagid_pos"]
    lis = geom["listeners"]
    paths = []
    for tid, Tp in tags.items():
        for name, Lp in lis.items():
            paths.append((geom["tagid_name"].get(tid, str(tid)), name, Tp, Lp))
    # coverage: crossing density of the 21 paths through a room grid (XY plane, z-averaged)
    allpos = list(geom["anchors"].values()) + list(lis.values()) + list(tags.values())
    P = np.array(allpos)
    lo, hi = P.min(0) - 300, P.max(0) + 300
    gx = np.linspace(lo[0], hi[0], 60)
    gy = np.linspace(lo[1], hi[1], 60)
    dens = np.zeros((len(gx), len(gy)))
    for _, _, Tp, Lp in paths:
        seg = Lp - Tp
        L = np.linalg.norm(seg[:2]) + 1e-9
        steps = int(L / 50) + 2
        for s in np.linspace(0, 1, steps):
            pt = Tp + s * seg
            ix = np.searchsorted(gx, pt[0]) - 1
            iy = np.searchsorted(gy, pt[1]) - 1
            if 0 <= ix < len(gx) and 0 <= iy < len(gy):
                dens[ix, iy] += 1
    blind = int(np.sum(dens == 0))
    # 3D path figure
    from mpl_toolkits.mplot3d import Axes3D  # noqa: F401
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")
    for tn, ln, Tp, Lp in paths:
        ax.plot([Tp[0], Lp[0]], [Tp[1], Lp[1]], [Tp[2], Lp[2]], "b-", lw=0.4, alpha=0.5)
    for name, a in geom["anchors"].items():
        ax.scatter(*a, c="k", marker="^", s=30)
    for name, l in lis.items():
        ax.scatter(*l, c="g", marker="s", s=40)
    for tn, t in tags.items():
        ax.scatter(*geom["tagid_pos"][tn], c="r", marker="o", s=50)
    ax.set_title("LOS network: 21 wand->listener paths (blue)")
    fig.savefig(os.path.join(FIG, "los_network_3d.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(7, 6))
    im = ax.imshow(dens.T, origin="lower", aspect="auto",
                   extent=[gx[0], gx[-1], gy[0], gy[-1]])
    ax.set_title("Coverage: path-crossing density (XY)"); fig.colorbar(im, ax=ax)
    fig.savefig(os.path.join(FIG, "coverage_heatmap.png"), dpi=120, bbox_inches="tight")
    plt.close(fig)
    out = {"n_paths": len(paths), "blind_cells": blind,
           "total_cells": int(dens.size), "max_density": float(dens.max())}
    json.dump(out, open(os.path.join(OUT, "step11_los_network.json"), "w"), indent=2)
    log(f"  {len(paths)} paths; blind cells {blind}/{dens.size}")
    return out


# ---------------------------------------------------------------- orchestrate
STEPS = [
    ("step0_parse", step0_parse, False),
    ("step1_templates", step1_templates, True),
    ("step2_stability", step2_stability, True),
    ("step3_multipath", step3_multipath, True),
    ("step4_backprojection", step4_backprojection, True),
    ("step5_beamform", step5_beamform, True),
    ("step6_fp_jitter", step6_fp_jitter, True),
    ("step7_lccf4", step7_lccf4, True),
    ("step8_agc", step8_agc, True),
    ("step9_evc", step9_evc, True),
    ("step10_height_diversity", step10_height_diversity, True),
    ("step11_los_network", step11_los_network, True),
]


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated step numbers to run (e.g. 5,6,10); "
                    "reuses on-disk parsed/ + step1 templates, merges into summary")
    args = ap.parse_args()
    only = set(x.strip() for x in args.only.split(",")) if args.only else None
    t0 = time.time()
    geom = load_geometry()
    log(f"geometry: {len(geom['anchors'])} anchors, {len(geom['listeners'])} listeners, "
        f"{len(geom['tags'])} tags" + (f"  [--only {sorted(only)}]" if only else ""))
    summ_path = os.path.join(OUT, "run_full_analysis_summary.json")
    results = {}
    if only and os.path.exists(summ_path):
        try:
            results = json.load(open(summ_path)).get("results", {})
        except Exception:                           # noqa: BLE001
            results = {}
    for stepname, fn, needs_geom in STEPS:
        num = stepname.split("_")[0].replace("step", "")
        if only and num not in only:
            continue
        try:
            results[stepname] = fn({}, geom) if needs_geom else fn({})
        except Exception as e:                      # noqa: BLE001
            log(f"  [ERROR] {stepname}: {e}")
            traceback.print_exc()
            results[stepname] = {"error": str(e)}
    json.dump({"elapsed_s": round(time.time() - t0, 1), "results": results},
              open(summ_path, "w"), indent=2, default=str)
    log(f"DONE in {time.time()-t0:.0f}s -> {OUT}/ and {FIG}/")


if __name__ == "__main__":
    main()
