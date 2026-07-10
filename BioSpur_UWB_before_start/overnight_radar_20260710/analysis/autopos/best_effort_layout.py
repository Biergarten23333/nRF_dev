#!/usr/bin/env python3
"""Best-effort 8-anchor layout from the post-rotation sweep (run 140144).

Reality (diagnosed): anchors A,C,D,E,F,G form a clean 39mm core; B and H each
have direction-dependent range biases (B: stepped-on/corner; H: mis-pointed at
C/D/G) that no re-orientation cleared. User chose "stop fiddling, best-effort".

Strategy (two-stage, so the bad anchors can't contaminate the good ones):
  1. Solve the clean-6 (A,C,D,E,F,G) by metric MDS + GN refine -> 39mm frame.
     Procrustes-align that frame to the earlier v4-io layout.json (clean-6
     correspondence) so absolute coordinates stay comparable/interpretable.
  2. Place H against the FIXED clean-6 by robust (Huber) least-squares, using
     for each H-link the OTHER-anchor-initiated direction (x->H), which is the
     unbiased side of H's asymmetry. F->H is down-weighted (F noisy).
  3. Place B against the FIXED clean-6 by robust (Huber) least-squares over both
     directions (B has no clean subset after rotation).
  4. Emit layout_besteffort.json with per-anchor quality + residual, plus a
     machine-readable confidence tier the imaging step can weight by.
"""
import os, json, statistics
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = ("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/"
       "captures/erlangen_20260528_optitrack/sweep_SW01_100_prewarm10_20260710_140144/sweep100/summary.json")
V4IO = ("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/"
        "solver/outputs/v4io_field_check/v4-io/layout.json")
OUT = os.path.join(HERE, "layout_besteffort.json")
CLEAN = list("ACDEFG")
LABELS = list("ABCDEFGH")


def load_dists(summary_path):
    d = json.load(open(summary_path))
    D = {}
    for k in sorted(d["rounds"]):
        for ln in d["rounds"][k].get("sw_lines", []):
            body = ln.split("SW-")[1]
            t = body.split(","); m = t[0]; i = 1
            while i + 1 < len(t):
                try:
                    D.setdefault((m, t[i]), []).append(int(t[i + 1]))
                except ValueError:
                    pass
                i += 3
    med = {k: statistics.median(v) for k, v in D.items()}
    std = {k: statistics.pstdev(v) for k, v in D.items()}
    return med, std


def undirected(med, a, b):
    vals = [v for v in (med.get((a, b)), med.get((b, a))) if v is not None]
    return float(np.mean(vals)) if vals else None


def solve_mds(labels, med):
    n = len(labels); ii = {c: k for k, c in enumerate(labels)}
    M = np.zeros((n, n))
    for a in labels:
        for b in labels:
            if a != b:
                M[ii[a], ii[b]] = undirected(med, a, b)
    M = (M + M.T) / 2
    D2 = M ** 2; J = np.eye(n) - np.ones((n, n)) / n; B = -0.5 * J @ D2 @ J
    w, V = np.linalg.eigh(B); o = np.argsort(w)[::-1]
    X = V[:, o][:, :3] * np.sqrt(np.clip(w[o][:3], 0, None))
    for _ in range(2000):
        g = np.zeros_like(X)
        for i in range(n):
            for j in range(i + 1, n):
                diff = X[i] - X[j]; rng = np.linalg.norm(diff) + 1e-9
                res = rng - M[i, j]; u = diff / rng; g[i] += res * u; g[j] -= res * u
        X -= 0.03 * g
    res = np.array([np.linalg.norm(X[i] - X[j]) - M[i, j]
                    for i in range(n) for j in range(i + 1, n)])
    return {labels[i]: X[i] for i in range(n)}, float(np.sqrt(np.mean(res ** 2)))


def procrustes(src, dst):
    """Rigid (rotation+translation, no scale) mapping src->dst for matched pts."""
    S = np.array(src); D = np.array(dst)
    cs = S.mean(0); cd = D.mean(0)
    H = (S - cs).T @ (D - cd)
    U, _, Vt = np.linalg.svd(H)
    R = Vt.T @ U.T
    if np.linalg.det(R) < 0:
        Vt[-1] *= -1; R = Vt.T @ U.T
    t = cd - R @ cs
    return R, t


def robust_place(pos6, links, huber=100.0, iters=200):
    """Least-squares 3D position from ranges to fixed anchors, Huber-weighted.
    links: list of (anchor_label, distance_mm). Returns x, weighted_rms, weights."""
    A = np.array([pos6[l] for l, _ in links]); dv = np.array([d for _, d in links])
    x = A.mean(0)
    w = np.ones(len(dv))
    for _ in range(iters):
        diff = x - A; rng = np.linalg.norm(diff, axis=1) + 1e-9
        res = rng - dv
        a = np.abs(res)
        w = np.where(a <= huber, 1.0, huber / np.maximum(a, 1e-9))
        Jm = diff / rng[:, None]
        WJ = Jm * w[:, None]
        try:
            dx = np.linalg.lstsq(WJ.T @ Jm, -(WJ.T @ res), rcond=None)[0]
        except np.linalg.LinAlgError:
            break
        x = x + dx
        if np.linalg.norm(dx) < 1e-6:
            break
    diff = x - A; rng = np.linalg.norm(diff, axis=1); res = rng - dv
    wrms = float(np.sqrt(np.sum(w * res ** 2) / np.sum(w)))
    return x, wrms, res, w


def main():
    med, std = load_dists(RUN)

    # Stage 1: clean-6 frame, aligned to v4-io coords
    pos6, rms6 = solve_mds(CLEAN, med)
    v4 = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]])
          for a in json.load(open(V4IO))["anchors"]}
    R, t = procrustes([pos6[l] for l in CLEAN], [v4[l] for l in CLEAN])
    pos6 = {l: R @ pos6[l] + t for l in CLEAN}

    anchors = {}
    for l in CLEAN:
        anchors[l] = dict(pos=pos6[l], quality="high", fit_rms_mm=round(rms6, 1),
                          confidence="trust", note="clean core (self-consistent 39mm)")

    # Stage 2: place H using other-initiated (x->H) direction; down-weight F->H (noisy)
    h_links = []
    for l in CLEAN:
        dv = med.get((l, "H"))            # x -> H : the unbiased side of H's asymmetry
        if dv is None:
            dv = undirected(med, l, "H")
        h_links.append((l, dv))
    xH, wrmsH, resH, wH = robust_place(pos6, h_links, huber=120.0)
    anchors["H"] = dict(pos=xH, quality="medium", fit_rms_mm=round(wrmsH, 1),
                        confidence="flag",
                        note="mis-pointed at C/D/G; placed from x->H (clean side), F->H down-weighted")

    # Stage 3: place B robustly from both directions (no clean subset)
    b_links = [(l, undirected(med, l, "B")) for l in CLEAN]
    xB, wrmsB, resB, wB = robust_place(pos6, b_links, huber=120.0)
    anchors["B"] = dict(pos=xB, quality="low", fit_rms_mm=round(wrmsB, 1),
                        confidence="flag",
                        note="directional bias (stepped-on/corner); Huber-placed, ~150mm uncertain")

    # assemble output (v4-io compatible schema + quality fields)
    out = {"version": "best-effort-20260710",
           "source_sweep": os.path.basename(os.path.dirname(os.path.dirname(RUN))),
           "frame": "aligned to v4io_field_check/v4-io via clean-6 Procrustes; A at ~origin",
           "clean_core": CLEAN, "flagged": ["B", "H"],
           "clean_core_rms_mm": round(rms6, 1),
           "anchor_ids": list(range(8)),
           "anchors": []}
    for idx, l in enumerate(LABELS):
        a = anchors[l]; p = a["pos"]
        out["anchors"].append({
            "id": idx, "label": l,
            "x_mm": round(float(p[0]), 1), "y_mm": round(float(p[1]), 1), "z_mm": round(float(p[2]), 1),
            "quality": a["quality"], "confidence": a["confidence"],
            "fit_rms_mm": a["fit_rms_mm"], "note": a["note"],
        })
    json.dump(out, open(OUT, "w"), indent=2)

    print("=== Best-effort 8-anchor layout ===")
    print(f"clean-6 (A,C,D,E,F,G) self-consistency RMS = {rms6:.1f}mm  [aligned to v4-io frame]")
    print(f"{'lbl':4}{'x_mm':>9}{'y_mm':>9}{'z_mm':>9}{'quality':>9}{'fit_rms':>9}")
    for a in out["anchors"]:
        print(f"{a['label']:4}{a['x_mm']:>9.0f}{a['y_mm']:>9.0f}{a['z_mm']:>9.0f}{a['quality']:>9}{a['fit_rms_mm']:>9}")
    print(f"\nH placement per-link residual (x->H, clean side):")
    for (l, dv), r, w in zip(h_links, resH, wH):
        print(f"   H-{l}: meas={dv:.0f}  resid={r:+.0f}  w={w:.2f}")
    print(f"B placement per-link residual (both-dir mean):")
    for (l, dv), r, w in zip(b_links, resB, wB):
        print(f"   B-{l}: meas={dv:.0f}  resid={r:+.0f}  w={w:.2f}")
    print(f"\n[wrote] {OUT}")


if __name__ == "__main__":
    main()
