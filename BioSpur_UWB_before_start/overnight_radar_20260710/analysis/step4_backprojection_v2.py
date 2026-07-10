#!/usr/bin/env python3
"""STEP 4 v2 — multistatic backprojection + synthetic PSF (BUG 3 fix).

BUG 3: step4 (v1) placed a synthetic PSF point target at [1500,1500,500] mm,
whose bistatic excess maps to ~tap 801-809 for every channel — INSIDE the
hardcoded direct-path gate (DP_END=812). synth_psf only injects a delta when
`DP_END < t0 <= TAIL_END`, so ALL 15 channels were gated out, the PSF volume
was all-zero, and res_6db's `volp >= peak*10^(-6/20)` with peak=0 selected the
ENTIRE grid -> the reported "6.0 x 5.2 x 4.0 m PSF" was the grid size, not a
physics result.

FIX:
  1. Replace the hardcoded DP_END=812 with the SAME per-channel direct-path
     main-lobe exclusion Step 3 uses (peak in [FP,FP+15], walk out to 25% of
     peak) — the gate now matches Step 3 instead of a magic number.
  2. Place the synthetic PSF target OUTSIDE the exclusion zone: search the grid
     for a voxel whose bistatic-excess tap clears every channel's main lobe
     (target ~FP+40), and report its actual per-channel taps.
  3. Rerun PSF + real backprojection with templates_v2 (corrected alignment).

Outputs (step4_v2/): backprojection_volume.npy, step4_backprojection_mip.png,
                     step4_psf_mip.png, step4_v2_stats.json
"""
import os, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates_v2")
AUTOPOS = os.path.join(HERE, "autopos")
OUT = os.path.join(HERE, "step4_v2"); os.makedirs(OUT, exist_ok=True)

REF_TAP = 800
TAP_NS = 1.0016
C = 299.792                 # mm/ns
MM_PER_TAP = TAP_NS * C     # ~300.3 mm/tap
TAIL_END = 872              # backproject up to FP+72 (~21.6 m excess)
TAGS = {"0xb136": "BS9336", "0xb15a": "BS955A", "0xb1f4": "BSCCF4"}
RXL = ["LB", "LE", "LF", "L9336", "L955A"]


def load_positions():
    be = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]])
          for a in json.load(open(os.path.join(AUTOPOS, "layout_besteffort.json")))["anchors"]}
    wr = json.load(open(os.path.join(AUTOPOS, "wand_positions_rigid.json")))
    wand = {k: np.array(wr[k]) for k in ("BS9336", "BS955A", "BSCCF4")}
    rx = {"LB": be["B"], "LE": be["E"], "LF": be["F"],
          "L9336": wand["BS9336"], "L955A": wand["BS955A"]}
    tx = {"0xb136": wand["BS9336"], "0xb15a": wand["BS955A"], "0xb1f4": wand["BSCCF4"]}
    return rx, tx, wand


def mainlobe_end(mag):
    """Per-channel direct-path main-lobe end tap — identical logic to Step 3."""
    dp_peak = REF_TAP + int(np.argmax(mag[REF_TAP:REF_TAP + 16]))
    fp_amp = mag[dp_peak]
    t = dp_peak + 1
    while t < REF_TAP + 40:
        if mag[t] < 0.25 * fp_amp and mag[t + 1] < 0.25 * fp_amp:
            return t, dp_peak
        t += 1
    return REF_TAP + 12, dp_peak


def channel_tail(L, src):
    """Magnitude tail with the per-channel main lobe (Step-3 gate) removed."""
    A = np.load(os.path.join(TPL, f"{L}_{src}_A.npy"))
    m = np.abs(A).astype(np.float64)
    ml_end, _ = mainlobe_end(m)
    m[:ml_end + 1] = 0.0
    m[TAIL_END + 1:] = 0.0
    pk = m.max()
    if pk > 0:
        m = m / pk
    return m, ml_end


def excess_tap(target, RX, TX):
    base = np.linalg.norm(TX - RX)
    e = np.linalg.norm(target - TX) + np.linalg.norm(target - RX) - base
    return REF_TAP + e / MM_PER_TAP


def backproject(grid_pts, shape, rx, tx, channels, tails):
    img = np.zeros(grid_pts.shape[0], dtype=np.float64)
    taps = np.arange(1016)
    for (L, src) in channels:
        m = tails[(L, src)][0]
        RX = rx[L]; TX = tx[src]; base = np.linalg.norm(TX - RX)
        d_tx = np.linalg.norm(grid_pts - TX[None, :], axis=1)
        d_rx = np.linalg.norm(grid_pts - RX[None, :], axis=1)
        tap = REF_TAP + (d_tx + d_rx - base) / MM_PER_TAP
        img += np.interp(tap, taps, m, left=0.0, right=0.0)
    return img.reshape(shape)


def synth_psf(grid_pts, shape, rx, tx, channels, target, ml_ends):
    """Ideal point scatterer at `target`: unit delta at its per-channel excess
    tap (only if that tap clears THAT channel's main lobe), then backproject."""
    img = np.zeros(grid_pts.shape[0], dtype=np.float64)
    taps = np.arange(1016)
    used = 0
    per_ch_tap = {}
    for (L, src) in channels:
        RX = rx[L]; TX = tx[src]; base = np.linalg.norm(TX - RX)
        t0 = excess_tap(target, RX, TX)
        per_ch_tap[f"{L}_{src}"] = round(float(t0), 2)
        m = np.zeros(1016)
        if ml_ends[(L, src)] < t0 <= TAIL_END:
            lo = int(np.floor(t0))
            m[lo] = 1 - (t0 - lo); m[lo + 1] = t0 - lo
            used += 1
        d_tx = np.linalg.norm(grid_pts - TX[None, :], axis=1)
        d_rx = np.linalg.norm(grid_pts - RX[None, :], axis=1)
        tap = REF_TAP + (d_tx + d_rx - base) / MM_PER_TAP
        img += np.interp(tap, taps, m, left=0.0, right=0.0)
    return img.reshape(shape), used, per_ch_tap


def pick_psf_target(gx, gy, gz, rx, tx, channels, ml_ends, want_tap=REF_TAP + 40):
    """Grid-search a target whose per-channel excess taps all clear the main
    lobe and stay <= TAIL_END; prefer median tap near want_tap."""
    # coarse candidate grid (every 4th node) to keep it cheap
    best = None
    for x in gx[::4]:
        for y in gy[::4]:
            for z in gz[::4]:
                tgt = np.array([x, y, z], float)
                taps = np.array([excess_tap(tgt, rx[L], tx[s]) for (L, s) in channels])
                mls = np.array([ml_ends[(L, s)] for (L, s) in channels])
                if np.all(taps > mls + 1) and np.all(taps <= TAIL_END):
                    score = abs(np.median(taps) - want_tap)
                    if best is None or score < best[0]:
                        best = (score, tgt, taps)
    return best


def mip_panel(vol, extent, rx, tx, title, path):
    fig, axs = plt.subplots(1, 3, figsize=(15, 5))
    names = ["XY (top)", "XZ (front)", "YZ (side)"]
    mips = [vol.max(axis=2), vol.max(axis=1), vol.max(axis=0)]
    projs = [{"rx": [(p[0], p[1]) for p in rx.values()], "tx": [(p[0], p[1]) for p in tx.values()]},
             {"rx": [(p[0], p[2]) for p in rx.values()], "tx": [(p[0], p[2]) for p in tx.values()]},
             {"rx": [(p[1], p[2]) for p in rx.values()], "tx": [(p[1], p[2]) for p in tx.values()]}]
    for ax, mip, ext, nm, pr in zip(axs, mips, extent, names, projs):
        im = ax.imshow(mip.T, origin="lower", extent=ext, aspect="auto", cmap="inferno")
        rxs = np.array(pr["rx"]); txs = np.array(pr["tx"])
        ax.scatter(rxs[:, 0], rxs[:, 1], c="cyan", marker="^", s=70, label="RX", edgecolors="k")
        ax.scatter(txs[:, 0], txs[:, 1], c="lime", marker="*", s=160, label="TX", edgecolors="k")
        ax.set_title(nm); ax.set_xlabel("mm"); ax.set_ylabel("mm")
        fig.colorbar(im, ax=ax, fraction=0.046)
    axs[0].legend(loc="upper right", fontsize=8)
    fig.suptitle(title); fig.tight_layout(); fig.savefig(path, dpi=150); plt.close(fig)


def res_6db(volp, gx, gy, gz):
    peak = volp.max()
    if peak <= 0:
        return None, None
    mask = volp >= peak * 10 ** (-6 / 20)      # -6 dB
    sel = np.argwhere(mask)
    span = (sel.max(0) - sel.min(0) + 1) * np.array([gx[1]-gx[0], gy[1]-gy[0], gz[1]-gz[0]])
    pk_idx = np.unravel_index(np.argmax(volp), volp.shape)
    return span, pk_idx


def main():
    rx, tx, wand = load_positions()
    channels = [(L, s) for L in RXL for s in TAGS]
    tails = {(L, s): channel_tail(L, s) for (L, s) in channels}
    ml_ends = {(L, s): tails[(L, s)][1] for (L, s) in channels}
    print(f"[Step4v2] {len(channels)} channels (templates_v2). main-lobe end taps: "
          f"min={min(ml_ends.values())} max={max(ml_ends.values())} "
          f"(vs v1 hardcoded gate 812=FP+12)")

    gx = np.arange(-1000, 5001, 75.0); gy = np.arange(-1200, 4001, 75.0); gz = np.arange(-1500, 2501, 75.0)
    X, Y, Z = np.meshgrid(gx, gy, gz, indexing="ij")
    shape = X.shape
    pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    extent = [(gx[0], gx[-1], gy[0], gy[-1]), (gx[0], gx[-1], gz[0], gz[-1]), (gy[0], gy[-1], gz[0], gz[-1])]
    print(f"[Step4v2] grid {shape} = {pts.shape[0]:,} voxels @75mm")

    # real backprojection
    vol = backproject(pts, shape, rx, tx, channels, tails)
    np.save(os.path.join(OUT, "backprojection_volume.npy"), vol.astype(np.float32))
    mip_panel(vol, extent, rx, tx,
              "Step 4 v2 — multistatic backprojection (templates_v2, Step-3 main-lobe gate)",
              os.path.join(OUT, "step4_backprojection_mip.png"))

    # --- synthetic PSF at a target that clears the exclusion zone (BUG 3 fix) ---
    # A resolvable scatterer must sit BEYOND the near-in blind zone (~6.6 m
    # bistatic excess), which for this compact rig lands near/beyond the room
    # edge -> so we measure the PSF on a DEDICATED LOCAL grid centred on the
    # target (never clipped by the room grid, unlike the v1-style shared grid).
    best = pick_psf_target(gx, gy, gz, rx, tx, channels, ml_ends)
    if best is None:
        print("[Step4v2] WARN: no in-gate target found on coarse grid; fallback")
        target = np.array([3500.0, 3000.0, 1500.0])
    else:
        _, target, taps_at_target = best
        print(f"[Step4v2] PSF target = {target.astype(int).tolist()} mm; per-channel excess "
              f"taps: min={taps_at_target.min():.1f} median={np.median(taps_at_target):.1f} "
              f"max={taps_at_target.max():.1f} (all > main lobe, <= {TAIL_END})")
    # local grid ±6000 mm around the target @100 mm -> large enough to contain
    # the (broad, incoherent) PSF blob; edge-clip flag reports if even this is
    # exceeded (which itself is the finding: no spatial localization).
    lx = target[0] + np.arange(-6000, 6001, 100.0)
    ly = target[1] + np.arange(-6000, 6001, 100.0)
    lz = target[2] + np.arange(-6000, 6001, 100.0)
    LX, LY, LZ = np.meshgrid(lx, ly, lz, indexing="ij")
    lshape = LX.shape
    lpts = np.stack([LX.ravel(), LY.ravel(), LZ.ravel()], axis=1)
    lextent = [(lx[0], lx[-1], ly[0], ly[-1]), (lx[0], lx[-1], lz[0], lz[-1]), (ly[0], ly[-1], lz[0], lz[-1])]
    psf, used, per_ch_tap = synth_psf(lpts, lshape, rx, tx, channels, target, ml_ends)
    # flag if the -6 dB blob touches the local-grid edge (would mean clipping)
    m6 = psf >= psf.max() * 10 ** (-6 / 20)
    edge_clip = bool(m6[0].any() or m6[-1].any() or m6[:, 0].any() or m6[:, -1].any()
                     or m6[:, :, 0].any() or m6[:, :, -1].any())
    print(f"[Step4v2] PSF: {used}/{len(channels)} channels contributed (v1 bug: 0/15), "
          f"psf peak={psf.max():.3f}, local grid {lshape}, edge-clipped={edge_clip}")
    mip_panel(psf, lextent, rx, tx,
              f"Step 4 v2 — multistatic PSF (point scatterer @ {target.astype(int).tolist()} mm, "
              f"{used}/15 ch, local grid)",
              os.path.join(OUT, "step4_psf_mip.png"))

    span, pk_idx = res_6db(psf, lx, ly, lz)
    dyn = float(20 * np.log10(vol.max() / (np.median(vol[vol > 0]) + 1e-9)))
    stats = {
        "n_channels": len(channels), "tap_ns": TAP_NS, "mm_per_tap": round(MM_PER_TAP, 1),
        "gate": "per-channel Step-3 main-lobe end (dynamic), NOT hardcoded 812",
        "mainlobe_end_taps": {f"{L}_{s}": int(ml_ends[(L, s)]) for (L, s) in channels},
        "tail_end_tap": TAIL_END,
        "near_in_blind_mm_min": round((min(ml_ends.values()) - REF_TAP) * MM_PER_TAP, 0),
        "near_in_blind_mm_max": round((max(ml_ends.values()) - REF_TAP) * MM_PER_TAP, 0),
        "psf_target_mm": target.astype(float).tolist(),
        "psf_channels_used": int(used),
        "psf_per_channel_excess_tap": per_ch_tap,
        "psf_peak": float(psf.max()),
        "psf_measured_on": "dedicated local grid +/-2400mm @75mm centred on target",
        "psf_edge_clipped": edge_clip,
        "psf_6dB_extent_mm": [round(float(s), 0) for s in span] if span is not None else None,
        "psf_note": ("GEOMETRIC PSF of the backprojection operator (ideal delta target). "
                     "Real resolution is additionally limited by the ~12-tap (~3.6 m) "
                     "direct-path pulse width and the near-in blind zone; and the real "
                     "image dynamic range below quantifies the clutter floor."),
        "image_peak": float(vol.max()),
        "image_dyn_range_db": round(dyn, 2),
        "grid_extent_mm": [float(gx[-1]-gx[0]), float(gy[-1]-gy[0]), float(gz[-1]-gz[0])],
    }
    json.dump(stats, open(os.path.join(OUT, "step4_v2_stats.json"), "w"), indent=2)
    print(f"[Step4v2] CORRECTED PSF -6dB extent (X,Y,Z) = {stats['psf_6dB_extent_mm']} mm "
          f"(grid is {stats['grid_extent_mm']} mm — PSF must be smaller than grid to be real)")
    print(f"[Step4v2] image dynamic range = {dyn:.1f} dB")
    print(f"[Step4v2] wrote {OUT}/")


if __name__ == "__main__":
    main()
