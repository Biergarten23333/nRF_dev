#!/usr/bin/env python3
"""STEP 4 — multistatic backprojection room image from the overnight CIR.

15 channels = 5 clean listeners (RX) x 3 wand tags (TX). Each channel's coherent
template A (Step 1) is FP-referenced (direct path at tap REF_TAP=800). We image
reflectors by EXCESS-delay backprojection: for a voxel p, the bistatic excess
path e(p)=|TX-p|+|p-RX|-|TX-RX| maps to a tap; we sample the channel's magnitude
tail there and accumulate INCOHERENTLY across channels (no cross-listener clock
=> coherent cross-channel sum impossible; per-channel is already coherent).

Geometry (this rig):
  RX: LB@anchorB(best-effort), LE@anchorE, LF@anchorF (clean-6),
      L9336@wandBS9336, L955A@wandBS955A (co-located with those wand tags).
  TX: BS9336(0xb136), BS955A(0xb15a), BSCCF4(0xb1f4) rigid-pose wand positions.

Physics caveat (known from prior 3-day work + Step 3): static wand aperture +
specular indoor walls + ~3.3m near-in blind zone => range-limited "bullseye",
NOT a clean room map. This step QUANTIFIES that: it renders the image AND a
synthetic point-target PSF so the achieved resolution is explicit and honest.
"""
import os, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates")
AUTOPOS = os.path.join(HERE, "autopos")
OUT = os.path.join(HERE, "step4"); os.makedirs(OUT, exist_ok=True)

REF_TAP = 800
TAP_NS = 1.0016          # DW1000 CIR sample interval (documented ~1.0016 ns)
C = 299.792              # mm/ns
MM_PER_TAP = TAP_NS * C  # ~300 mm of path per tap
DP_END = 812             # remove direct-path main lobe (taps <= 812)
TAIL_END = 872           # backproject taps (DP_END, TAIL_END]  ~ up to 18 m excess

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


def channel_tail(L, src):
    A = np.load(os.path.join(TPL, f"{L}_{src}_A.npy"))
    m = np.abs(A).astype(np.float64)
    m[:DP_END + 1] = 0.0            # remove direct path + main lobe
    m[TAIL_END + 1:] = 0.0
    # normalize each channel by its own peak tail energy so no single strong
    # channel dominates the incoherent sum
    pk = m.max()
    if pk > 0:
        m = m / pk
    return m


def backproject(grid_pts, shape, rx, tx, use_channels):
    img = np.zeros(grid_pts.shape[0], dtype=np.float64)
    taps = np.arange(1016)
    for (L, src) in use_channels:
        m = channel_tail(L, src)
        RX = rx[L]; TX = tx[src]
        base = np.linalg.norm(TX - RX)
        d_tx = np.linalg.norm(grid_pts - TX[None, :], axis=1)
        d_rx = np.linalg.norm(grid_pts - RX[None, :], axis=1)
        excess = d_tx + d_rx - base                     # mm
        tap = REF_TAP + excess / MM_PER_TAP             # fractional tap
        # linear-interpolate the magnitude tail at the (fractional) tap
        val = np.interp(tap, taps, m, left=0.0, right=0.0)
        img += val
    return img.reshape(shape)


def synth_psf(grid_pts, shape, rx, tx, use_channels, target):
    """Ideal point scatterer at `target`: put a unit delta at its excess tap per
    channel, backproject -> the multistatic PSF (impulse response of the imager)."""
    img = np.zeros(grid_pts.shape[0], dtype=np.float64)
    taps = np.arange(1016)
    for (L, src) in use_channels:
        RX = rx[L]; TX = tx[src]; base = np.linalg.norm(TX - RX)
        e_t = np.linalg.norm(target - TX) + np.linalg.norm(target - RX) - base
        m = np.zeros(1016)
        t0 = REF_TAP + e_t / MM_PER_TAP
        if DP_END < t0 <= TAIL_END:
            lo = int(np.floor(t0)); m[lo] = 1 - (t0 - lo); m[lo + 1] = t0 - lo  # delta
        d_tx = np.linalg.norm(grid_pts - TX[None, :], axis=1)
        d_rx = np.linalg.norm(grid_pts - RX[None, :], axis=1)
        tap = REF_TAP + (d_tx + d_rx - base) / MM_PER_TAP
        img += np.interp(tap, taps, m, left=0.0, right=0.0)
    return img.reshape(shape)


def mip_panel(vol, axes, extent_list, rx, tx, wand, title, path):
    fig, axs = plt.subplots(1, 3, figsize=(15, 5))
    names = ["XY (top)", "XZ (front)", "YZ (side)"]
    mips = [vol.max(axis=2), vol.max(axis=1), vol.max(axis=0)]
    projxy = {"rx": [(p[0], p[1]) for p in rx.values()], "tx": [(p[0], p[1]) for p in tx.values()]}
    projxz = {"rx": [(p[0], p[2]) for p in rx.values()], "tx": [(p[0], p[2]) for p in tx.values()]}
    projyz = {"rx": [(p[1], p[2]) for p in rx.values()], "tx": [(p[1], p[2]) for p in tx.values()]}
    projs = [projxy, projxz, projyz]
    for ax, mip, ext, nm, pr in zip(axs, mips, extent_list, names, projs):
        im = ax.imshow(mip.T, origin="lower", extent=ext, aspect="auto", cmap="inferno")
        rxs = np.array(pr["rx"]); txs = np.array(pr["tx"])
        ax.scatter(rxs[:, 0], rxs[:, 1], c="cyan", marker="^", s=70, label="RX listener", edgecolors="k")
        ax.scatter(txs[:, 0], txs[:, 1], c="lime", marker="*", s=160, label="TX wand", edgecolors="k")
        ax.set_title(nm); ax.set_xlabel("mm"); ax.set_ylabel("mm")
        fig.colorbar(im, ax=ax, fraction=0.046)
    axs[0].legend(loc="upper right", fontsize=8)
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(path, dpi=150); plt.close(fig)


def main():
    rx, tx, wand = load_positions()
    channels = [(L, s) for L in RXL for s in TAGS]
    print(f"[Step4] {len(channels)} channels; RX={list(rx)}, TX={list(TAGS.values())}")

    # grid over the anchor volume + margin
    gx = np.arange(-1000, 5001, 75.0); gy = np.arange(-1200, 4001, 75.0); gz = np.arange(-1500, 2501, 75.0)
    X, Y, Z = np.meshgrid(gx, gy, gz, indexing="ij")
    shape = X.shape
    pts = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    extent = [(gx[0], gx[-1], gy[0], gy[-1]), (gx[0], gx[-1], gz[0], gz[-1]), (gy[0], gy[-1], gz[0], gz[-1])]
    print(f"[Step4] grid {shape} = {pts.shape[0]:,} voxels @75mm")

    # real image
    vol = backproject(pts, shape, rx, tx, channels)
    np.save(os.path.join(OUT, "backprojection_volume.npy"), vol.astype(np.float32))
    mip_panel(vol, None, extent, rx, tx, wand,
              "Step 4 — multistatic backprojection (overnight static scene, direct path removed)",
              os.path.join(OUT, "step4_backprojection_mip.png"))

    # synthetic point-target PSF at the wand centroid region (a plausible scatterer spot)
    target = np.array([1500.0, 1500.0, 500.0])
    psf = synth_psf(pts, shape, rx, tx, channels, target)
    mip_panel(psf, None, extent, rx, tx, wand,
              f"Step 4 — multistatic PSF (ideal point scatterer @ {target.astype(int).tolist()} mm)",
              os.path.join(OUT, "step4_psf_mip.png"))

    # PSF resolution: -6 dB extent of the PSF blob around the target
    def res_6db(volp, target):
        idx = np.array([np.argmin(np.abs(gx - target[0])), np.argmin(np.abs(gy - target[1])),
                        np.argmin(np.abs(gz - target[2]))])
        peak = volp.max()
        mask = volp >= peak * 10 ** (-6 / 20)   # -6 dB
        sel = np.argwhere(mask)
        if sel.size == 0:
            return None
        span = (sel.max(0) - sel.min(0)) * 75.0
        return span
    span = res_6db(psf, target)

    stats = {"n_channels": len(channels),
             "tap_ns": TAP_NS, "mm_per_tap": round(MM_PER_TAP, 1),
             "dp_removed_taps_le": DP_END, "tail_end_tap": TAIL_END,
             "near_in_blind_mm": round((DP_END - REF_TAP) * MM_PER_TAP, 0),
             "image_peak": float(vol.max()), "image_dyn_range_db": float(20*np.log10(vol.max()/ (np.median(vol[vol>0])+1e-9))),
             "psf_6dB_extent_mm": [round(float(s), 0) for s in span] if span is not None else None,
             "rx_positions": {k: v.tolist() for k, v in rx.items()},
             "tx_positions": {k: TAGS[k] for k in TAGS},
             "wand_positions": {k: wand[k].tolist() for k in wand},
             "wand_fit_rms_mm": json.load(open(os.path.join(AUTOPOS, "wand_positions_rigid.json")))["fit_rms_mm"]}
    json.dump(stats, open(os.path.join(OUT, "step4_stats.json"), "w"), indent=2)
    print(f"[Step4] near-in blind zone = {stats['near_in_blind_mm']:.0f} mm (direct-path removal)")
    print(f"[Step4] PSF -6dB extent (X,Y,Z) = {stats['psf_6dB_extent_mm']} mm  <- the achievable resolution")
    print(f"[Step4] image dynamic range = {stats['image_dyn_range_db']:.1f} dB")
    print(f"[Step4] wrote {OUT}/step4_backprojection_mip.png, step4_psf_mip.png, step4_stats.json")


if __name__ == "__main__":
    main()
