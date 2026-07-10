#!/usr/bin/env python3
"""Re-orient the best-effort layout so physical vertical = +Z.

AutoPos/MDS recovers anchor shape only up to an arbitrary 3D rotation; my frame
tumbled the known two-layer box (lower=A,B,C,D  upper=E,F,G,H, ~1.6m apart) so the
Z axis was meaningless. Fix: the plane-normal separating the two layers IS vertical.
Define it from the CLEAN anchors only (lower {A,C,D}, upper {E,F,G} — exclude the
flagged B,H), rotate that normal to +Z, put the lower plane at z=0. Rotation is
distance-preserving, so Step-4 imaging is unaffected; only coords/figure change.
"""
import os, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
BE = os.path.join(HERE, "layout_besteffort.json")
WR = os.path.join(HERE, "wand_positions_rigid.json")
CLEAN6 = os.path.join(HERE, "layout_clean6.json")

LOWER = ["A", "B", "C", "D"]; UPPER = ["E", "F", "G", "H"]
LOWER_CLEAN = ["A", "C", "D"]; UPPER_CLEAN = ["E", "F", "G"]


def plane_normal(pts):
    """Best-fit plane normal via SVD (pts: k x 3)."""
    c = pts.mean(0); u, s, vt = np.linalg.svd(pts - c, full_matrices=False)
    return vt[2], c, s  # smallest-singular-vector = normal; s[2] = planarity residual


def rot_a_to_b(a, b):
    a = a / np.linalg.norm(a); b = b / np.linalg.norm(b)
    v = np.cross(a, b); s = np.linalg.norm(v); c = np.dot(a, b)
    if s < 1e-12:
        return np.eye(3) if c > 0 else np.diag([1, -1, -1.0])
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / s ** 2)


def main():
    be = json.load(open(BE))
    P = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]], float) for a in be["anchors"]}

    lo = np.array([P[l] for l in LOWER_CLEAN]); up = np.array([P[l] for l in UPPER_CLEAN])
    n_lo, c_lo, s_lo = plane_normal(lo); n_up, c_up, s_up = plane_normal(up)
    sep_vec = c_up - c_lo
    # orient normals from lower->upper
    if np.dot(n_lo, sep_vec) < 0: n_lo = -n_lo
    if np.dot(n_up, sep_vec) < 0: n_up = -n_up
    vertical = n_lo + n_up; vertical /= np.linalg.norm(vertical)

    print("Planarity check (smallest SVD residual — small => coplanar):")
    print(f"  lower {{A,C,D}} residual={s_lo[2]:.1f}mm   upper {{E,F,G}} residual={s_up[2]:.1f}mm")
    print(f"  normal agreement (deg between lower & upper normals): "
          f"{np.degrees(np.arccos(np.clip(np.dot(n_lo,n_up),-1,1))):.1f}")
    print(f"  layer separation (centroid-to-centroid along vertical): "
          f"{abs(np.dot(sep_vec, vertical)):.0f}mm")

    R = rot_a_to_b(vertical, np.array([0, 0, 1.0]))
    # rotate everything; translate so lower-clean plane sits at z=0, keep xy centroid near origin-ish of clean set
    def xf(p): return R @ p
    z_lo = np.mean([xf(P[l])[2] for l in LOWER_CLEAN])
    xy0 = np.mean([xf(P[l])[:2] for l in ["A", "C", "D", "E", "F", "G"]], axis=0)
    def canon(p):
        q = xf(p); return np.array([q[0] - xy0[0], q[1] - xy0[1], q[2] - z_lo])

    Pc = {l: canon(P[l]) for l in P}
    print("\nCanonical layout (Z = vertical; lower ABCD ~0, upper EFGH ~+sep):")
    for grp, name in [(LOWER, "LOWER"), (UPPER, "UPPER")]:
        for l in grp:
            q = Pc[l]; flag = "  <FLAG" if l in ("B", "H") else ""
            print(f"  {name} {l}: ({q[0]:7.0f},{q[1]:7.0f},{q[2]:7.0f}){flag}")
    zl = np.mean([Pc[l][2] for l in LOWER_CLEAN]); zu = np.mean([Pc[l][2] for l in UPPER_CLEAN])
    print(f"  => lower-clean mean z={zl:.0f}mm  upper-clean mean z={zu:.0f}mm  sep={zu-zl:.0f}mm")

    # write canonical best-effort layout
    qmap = {a["label"]: a for a in be["anchors"]}
    for a in be["anchors"]:
        q = Pc[a["label"]]
        a["x_mm"], a["y_mm"], a["z_mm"] = round(float(q[0]), 1), round(float(q[1]), 1), round(float(q[2]), 1)
    be["frame"] = "canonical: +Z=vertical (two-layer normal from clean anchors); lower ABCD z~0, upper EFGH z~+sep"
    be["layer_lower"] = LOWER; be["layer_upper"] = UPPER
    be["layer_separation_mm"] = round(float(zu - zl), 0)
    json.dump(be, open(BE, "w"), indent=2)

    # apply same transform to wand positions + clean6 layout (keep everything in one frame)
    wr = json.load(open(WR))
    for k in ("BS9336", "BS955A", "BSCCF4", "centroid"):
        if k in wr: wr[k] = [round(float(x), 1) for x in canon(np.array(wr[k]))]
    wr["frame"] = be["frame"]
    json.dump(wr, open(WR, "w"), indent=2)

    c6 = json.load(open(CLEAN6))
    for a in c6["anchors"]:
        q = Pc[a["label"]]; a["x_mm"], a["y_mm"], a["z_mm"] = round(float(q[0]), 1), round(float(q[1]), 1), round(float(q[2]), 1)
    c6["frame"] = be["frame"]
    json.dump(c6, open(CLEAN6, "w"), indent=2)
    print("\n[reorient] rewrote layout_besteffort.json, wand_positions_rigid.json, layout_clean6.json (canonical frame)")
    print("[note] rotation is distance-preserving -> Step-4 imaging results unchanged.")


if __name__ == "__main__":
    main()
