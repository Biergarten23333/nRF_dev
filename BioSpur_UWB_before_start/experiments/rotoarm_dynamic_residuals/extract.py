#!/usr/bin/env python3
"""
Per-capture residual extraction (Task 1 core). Runs in a process-pool worker:
extract_capture(capname, stem) -> compact per-tag annotated arrays + meta.

Alignment: non-uniform local time-warp (roto_lib.windowed_tau); ALL analysis in
Vicon rotation-phase (theta) space, which survives an unknown global offset (C1).
Every sample carries anchor, rank(==anchor_id), theta, revolution, radial velocity,
link elevation (C3), and a skew-compensated geometry (Task 2). All per-link bias is
ABSOLUTE vs Vicon geometry (C2).
"""
import math
import numpy as np
import roto_lib as RL


def extract_capture(capname, stem, tau_shift_frames=0.0):
    V = RL.parse_vicon(stem)
    tvec = V['t']
    req = list(RL.LETTERS) + list(RL.TAG_MARKERS)
    vic_ok = all(V.get(m) is not None and np.isfinite(V[m]).any() for m in req)

    sweeps_by_tag = {}
    for tag in RL.ROTO_TAGS:
        sw, _ = RL.parse_uwb(capname, tag)
        if sw:
            sweeps_by_tag[tag] = sw
    if not sweeps_by_tag:
        return dict(capname=capname, stem=stem, ok=False, reason="no UWB")
    if not vic_ok:
        return dict(capname=capname, stem=stem, ok=False, reason="Vicon markers NaN (unusable for residuals)",
                    n_sweeps={t: len(s) for t, s in sweeps_by_tag.items()})

    anc = RL.anchor_positions(V)
    mapping, map_resid = RL.resolve_tag_mapping(sweeps_by_tag, V, anc)
    slot_frames = (RL.NOMINAL_SLOT_MS / 1000.0) * RL.FPS
    out = dict(capname=capname, stem=stem, ok=True, mapping=mapping,
               map_resid={f"{t}->{m}": (None if v > 1e17 else float(v)) for (t, m), v in map_resid.items()},
               tags={})

    for tag, sw in sweeps_by_tag.items():
        marker = mapping[tag]
        M = V[marker]
        circ = RL.fit_plane_circle(M)
        tau_sweep, su, diag = RL.windowed_tau(sw, M, anc)
        tau_sweep = tau_sweep + tau_shift_frames / RL.FPS   # C1 perturbation hook
        nF = len(M)

        # per-anchor geom range + radial velocity over all Vicon frames
        g_full = {L: np.linalg.norm(M - anc[L], axis=1) for L in RL.LETTERS}
        drdt_full = {L: np.gradient(g_full[L], tvec) for L in RL.LETTERS}

        ks = sorted(sw)
        frac_sweep = np.clip((su + tau_sweep) * RL.FPS, 0, nF - 1 - 1e-6)
        p_sweep = RL._interp_pos(M, frac_sweep)
        Qs = p_sweep - circ['center']
        th_sweep = np.arctan2(Qs @ circ['v'], Qs @ circ['u'])
        valid_sweep = np.all(np.isfinite(p_sweep), axis=1)
        th_un = np.full(len(ks), np.nan)
        if valid_sweep.any():
            th_un[valid_sweep] = np.unwrap(th_sweep[valid_sweep])
            base = th_un[valid_sweep][0]
        rev_sweep = np.array([np.floor((th_un[i] - (base if valid_sweep.any() else 0)) / (2*math.pi))
                              if valid_sweep[i] else -1 for i in range(len(ks))])

        # revolutions / rate from unwrapped valid phase
        vs = valid_sweep
        if vs.sum() > 2:
            t0, t1 = su[vs][0], su[vs][-1]
            n_rev = float((th_un[vs][-1] - th_un[vs][0]) / (2*math.pi))
            rev_per_s = n_rev / (t1 - t0 + 1e-9)
            v_tan = abs(rev_per_s) * 2*math.pi * circ['radius']
        else:
            n_rev = 0.0; rev_per_s = 0.0; v_tan = 0.0

        A=[]; Rm=[]; RAW=[]; Qn=[]; G=[]; E=[]; GS=[]; ES=[]
        TH=[]; REV=[]; DR=[]; EL=[]; SW=[]; TZ=[]; SS=[]
        idxfull = np.arange(nF)
        for i, k in enumerate(ks):
            if not valid_sweep[i]:
                continue
            fr = frac_sweep[i]
            i0 = int(math.floor(fr)); w = fr - i0
            p = p_sweep[i]
            th_i = float(th_sweep[i]); rev_i = int(rev_sweep[i])
            for a in sorted(sw[k]['r']):
                L = RL.LETTERS[a]
                r = sw[k]['r'][a]
                g = float(g_full[L][i0]*(1-w) + g_full[L][i0+1]*w)
                # skew-compensated geom at rank-shifted time
                fr_s = min(max(fr + a*slot_frames, 0), nF - 1 - 1e-6)
                j0 = int(math.floor(fr_s)); w2 = fr_s - j0
                gs = float(g_full[L][j0]*(1-w2) + g_full[L][j0+1]*w2)
                dr = float(drdt_full[L][i0]*(1-w) + drdt_full[L][i0+1]*w)
                dz = abs(anc[L][2]-p[2]); horiz = math.hypot(anc[L][0]-p[0], anc[L][1]-p[1])
                el = math.degrees(math.atan2(dz, horiz))
                A.append(a); Rm.append(r); RAW.append(sw[k]['raw'].get(a, r))
                Qn.append(sw[k]['q'].get(a, np.nan)); G.append(g); E.append(r-g)
                GS.append(gs); ES.append(r-gs); TH.append(th_i); REV.append(rev_i)
                DR.append(dr); EL.append(el); SW.append(k); TZ.append(float(p[2])); SS.append(su[i])
        d = dict(
            marker=marker, radius=float(circ['radius']), tilt_deg=float(circ['tilt_deg']),
            oop_rms=float(circ['oop_rms']), rad_rms=float(circ['rad_rms']),
            zspan=float(circ['zspan']), center=[float(x) for x in circ['center']],
            n_sweeps=len(ks), n_valid_sweeps=int(valid_sweep.sum()), n_rev=abs(n_rev),
            rev_per_s=float(rev_per_s), v_tan=float(v_tan), align_diag=diag,
            anchor=np.array(A, np.int8), rank=np.array(A, np.int8),
            r=np.array(Rm), raw=np.array(RAW), q=np.array(Qn),
            g=np.array(G), e=np.array(E), g_skew=np.array(GS), e_skew=np.array(ES),
            theta=np.array(TH), rev=np.array(REV, np.int32), drdt=np.array(DR),
            elev=np.array(EL), sweep=np.array(SW, np.int32), tagz=np.array(TZ),
            s=np.array(SS),
        )
        d['elev_bin'] = RL.elev_bin(d['elev'])
        out['tags'][tag] = d
    return out


if __name__ == "__main__":
    import time
    for cap, stem in [("roto_R01_BS2DCE_BSDC91_120s_20260528_125256", "R01"),
                      ("roto_R14_BS2DCE_BSDC91_120s_20260528_134048", "R14"),
                      ("roto_R15_BS2DCE_BSDC91_120s_20260528_134410", "R15")]:
        t0 = time.time()
        r = extract_capture(cap, stem)
        print(f"\n=== {stem}  ({time.time()-t0:.1f}s)  ok={r['ok']} ===")
        if not r['ok']:
            print("  reason:", r.get('reason')); continue
        print("  mapping:", r['mapping'], "map_resid:", r['map_resid'])
        for tag, d in r['tags'].items():
            print(f"  {tag}->{d['marker']}: R={d['radius']:.0f} tilt={d['tilt_deg']:.0f} "
                  f"n={d['e'].size} rev={d['n_rev']:.1f} v={d['v_tan']:.0f}mm/s "
                  f"warp_span={d['align_diag']['tau_span']:.2f}s")
            for b in ('shallow', 'unverified', 'steep'):
                m = d['elev_bin'] == b
                if m.sum():
                    print(f"      {b:>10}: n={m.sum():5d} e mean={d['e'][m].mean():7.1f} "
                          f"rms={np.sqrt(np.mean(d['e'][m]**2)):6.1f} elev {d['elev'][m].min():.0f}-{d['elev'][m].max():.0f}")
