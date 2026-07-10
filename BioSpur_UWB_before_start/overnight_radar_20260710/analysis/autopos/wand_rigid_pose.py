#!/usr/bin/env python3
"""Solve the wand's 6-DOF rigid-body pose from tag->anchor ranges.

The wand is a precise rigid T (docs/wand_mode.md): body-frame positions
  BS9336=(385,0,0), BS955A=(0,-595,0), BSCCF4=(-285,0,0) mm.
Independent per-tag multilateration was ill-conditioned (110-134mm resid, rigid-T
violated). Instead fit ONE pose (R,t) so all 3 tags share the known shape:
  world_i = R @ body_i + t,  minimize sum (|world_i - anchor_a| - range_ia)^2
over the clean-6 anchors only (B,H excluded). 6 unknowns, up to 18 range obs.
Reports per-anchor range residuals to expose any bad wand->anchor link.
"""
import os, csv, json
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
TR = ("/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/"
      "captures/erlangen_20260528_optitrack/wand3_W01_BS9336_BS955A_BSCCF4_120s_20260710_143311/"
      "tag_capture_20260710_143313/tr_all.csv")
LAY = os.path.join(HERE, "layout_clean6.json")
BODY = {"BS9336": np.array([385., 0, 0]),
        "BS955A": np.array([0., -595, 0]),
        "BSCCF4": np.array([-285., 0, 0])}


def rot(aa):
    th = np.linalg.norm(aa)
    if th < 1e-12:
        return np.eye(3)
    k = aa / th; K = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(th) * K + (1 - np.cos(th)) * K @ K


def main():
    lay = json.load(open(LAY))
    anch = {a["id"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]]) for a in lay["anchors"]}
    anch_lbl = {a["id"]: a["label"] for a in lay["anchors"]}

    # median range per (tag, anchor_id), valid + quality>=? use valid==1
    acc = {}
    for row in csv.DictReader(open(TR)):
        if row.get("valid") not in ("1", 1):
            continue
        tag = row.get("peer_name"); aid = int(float(row.get("anchor_id")))
        if tag not in BODY or aid not in anch:
            continue
        try:
            r = float(row.get("range_mm"))
        except (TypeError, ValueError):
            continue
        if r <= 0:
            continue
        acc.setdefault((tag, aid), []).append(r)
    obs = {k: float(np.median(v)) for k, v in acc.items() if len(v) >= 20}
    tags = list(BODY)

    # initial guess: centroid of anchors + identity rotation
    t = np.mean([anch[a] for a in anch], axis=0).astype(float)
    aa = np.zeros(3)

    for _ in range(200):
        R = rot(aa)
        rows_J = []; res = []
        for (tag, aid), rng in obs.items():
            w = R @ BODY[tag] + t
            d = w - anch[aid]; dist = np.linalg.norm(d) + 1e-9
            res.append(dist - rng)
            u = d / dist
            # d(world)/d t = I ; d(world)/d aa ~ -[R b]_x (small-angle)
            Rb = R @ BODY[tag]
            dRb = np.array([[0, -Rb[2], Rb[1]], [Rb[2], 0, -Rb[0]], [-Rb[1], Rb[0], 0]])  # d(R b)/dw = -[Rb]_x * dw...
            J_t = u
            J_aa = u @ (-dRb)   # chain: d dist/d aa = u . d(world)/d aa, d(world)/daa = [Rb]_x? sign handled by GN
            rows_J.append(np.concatenate([J_t, J_aa]))
        Jm = np.array(rows_J); r = np.array(res)
        try:
            step = np.linalg.solve(Jm.T @ Jm + np.eye(6) * 1e-3, -(Jm.T @ r))
        except np.linalg.LinAlgError:
            break
        t = t + step[:3]; aa = aa + step[3:]
        if np.linalg.norm(step) < 1e-7:
            break

    R = rot(aa)
    world = {tag: R @ BODY[tag] + t for tag in tags}
    # residuals
    per_anchor = {}
    allres = []
    for (tag, aid), rng in obs.items():
        d = np.linalg.norm(world[tag] - anch[aid]) - rng
        allres.append(d); per_anchor.setdefault(aid, []).append(d)
    allres = np.array(allres)
    print(f"Rigid wand pose fit: {len(obs)} range obs, RMS residual = {np.sqrt(np.mean(allres**2)):.1f}mm "
          f"(max {np.max(np.abs(allres)):.0f}mm)")
    print("Per-anchor range residual (wand->anchor), flag |mean|>80mm:")
    for aid in sorted(per_anchor):
        v = np.array(per_anchor[aid]); f = " <==" if abs(v.mean()) > 80 else ""
        print(f"  {anch_lbl[aid]}(id{aid}): mean={v.mean():+.0f}mm rms={np.sqrt(np.mean(v**2)):.0f}mm n={len(v)}{f}")
    print("\nWand tag world positions (rigid-constrained):")
    for tag in tags:
        w = world[tag]; print(f"  {tag}: ({w[0]:.0f},{w[1]:.0f},{w[2]:.0f})mm")
    cen = np.mean([world[t] for t in tags], axis=0)
    print(f"  centroid: ({cen[0]:.0f},{cen[1]:.0f},{cen[2]:.0f})mm")
    # rigid-T check (should now be exact by construction)
    print("\nPairwise (now rigid-constrained, should match known):")
    known = {("BS9336","BS955A"): np.linalg.norm(BODY["BS9336"]-BODY["BS955A"]),
             ("BS9336","BSCCF4"): np.linalg.norm(BODY["BS9336"]-BODY["BSCCF4"]),
             ("BS955A","BSCCF4"): np.linalg.norm(BODY["BS955A"]-BODY["BSCCF4"])}
    for (a,b),kd in known.items():
        print(f"  {a}-{b}: {np.linalg.norm(world[a]-world[b]):.0f}mm (known {kd:.0f})")
    out = {tag: world[tag].tolist() for tag in tags}
    out["centroid"] = cen.tolist()
    out["fit_rms_mm"] = float(np.sqrt(np.mean(allres**2)))
    out["method"] = "6-DOF rigid-body pose, clean-6 anchors, median ranges"
    json.dump(out, open(os.path.join(HERE, "wand_positions_rigid.json"), "w"), indent=2)
    print(f"\nwrote wand_positions_rigid.json")


if __name__ == "__main__":
    main()
