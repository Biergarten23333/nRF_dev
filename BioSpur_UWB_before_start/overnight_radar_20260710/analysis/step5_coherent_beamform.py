#!/usr/bin/env python3
"""STEP 5 — Coherent wand-aperture beamforming for reflector DOA.

The overnight incoherent backprojection (step4) collapsed to a featureless
bullseye because it summed |CIR| across channels (amplitude only, phase
discarded). This script instead phase-steers the THREE wand-tag CIR templates
across the small wand aperture (T-shape, ~0.7 m) to resolve azimuth.

Physics (far-field of the wand aperture, per anchor-side listener):
  A scatterer seen from the wand centroid in direction u(theta)=[cos,sin] makes
  tag i's path differ by  d_i(theta) = tx_local_2d[i] . u(theta)   [mm].
  In the complex baseband CIR that longer path shows up as
    (a) an ENVELOPE delay of d_i/c        -> tap_shift_i = d_i / tap_spacing_mm
    (b) a CARRIER phase of -2*pi*d_i/lambda.
  To STEER toward theta we apply the inverse to each tag and sum coherently:
    h_steer_i = advance_envelope(h_i, tap_shift_i) * exp(+j 2 pi d_i / lambda)
  The carrier term (b) carries the fine angular information: the aperture is
  D/lambda ~ 709/46.2 ~ 15 wavelengths, giving a ~arcsin(lambda/D) ~ 3.7 deg
  main lobe. (Envelope term (a) is only ~2 taps; it keeps tags co-registered
  in the delay bin.)  With only 3 elements at 285-595 mm spacing (>> lambda/2)
  the pattern is heavily aliased -> grating lobes; step5 measures both the main
  lobe width AND the grating-lobe spacing so the verdict is honest.

Inputs  (all IN-REPO, relative to this file):
  templates/{L}_{src}_A.npy            complex64[1016], FP referenced, FP@tap 800
  autopos/wand_positions_rigid.json    3D wand-tag rigid pose (room frame)
  autopos/layout_besteffort.json       anchor (=listener) positions
  step4/backprojection_volume.npy      incoherent bullseye (for the comparison)

Outputs (analysis/beamforming/, IN-REPO):
  {L}_polar_beam.png                   per-listener polar map (az x bistatic range)
  doa_detections.csv                   thresholded detections
  triangulated_reflectors.png          best-effort cross-listener intersection
  resolution_check.png / summary.json  synthetic PSF main-lobe + grating lobes
  coherent_vs_incoherent.png           side-by-side with the step4 bullseye

GPU: ONE device (cuda:0). cuda:1 left free (per project constraint).
"""
import os, json, time, csv
import numpy as np
import torch

HERE = os.path.dirname(os.path.abspath(__file__))
TPL = os.path.join(HERE, "templates")
AUTOPOS = os.path.join(HERE, "autopos")
STEP4 = os.path.join(HERE, "step4")
OUT = os.path.join(HERE, "beamforming")
os.makedirs(OUT, exist_ok=True)

# ---- constants (verified against data, not assumed) -------------------------
C = 299_792_458.0
F_CARRIER = 6489.6e6                       # ch5 centre
LAMBDA_MM = C / F_CARRIER * 1e3            # 46.19 mm
TAP_NS = 1.0 / 0.9984                      # 1.0016 ns/tap (998.4 MHz accumulator)
TAP_SPACING_MM = TAP_NS * 1e-9 * C * 1e3   # 300.28 mm/tap
NTAP = 1016
FP = 800                                   # step1 rolls first-path to REF_TAP=800
DP_HALF = 10                               # zero FP +/- 10 (direct-path main lobe)
DELAY0, DELAY1 = FP + 15, FP + 200         # steering delay taps [815, 1000)
AZ = np.arange(-180.0, 180.0, 0.5)         # 720 azimuths
NAZ = AZ.size

RXL = ["LB", "LE", "LF"]                    # anchor-side listeners only
ANCHOR_OF = {"LB": "B", "LE": "E", "LF": "F"}
# tag id -> (device, caliper local-2D mm).  Verified: OVERNIGHT_ANALYSIS_REPORT.md,
# step4_backprojection.py.  Caliper baselines 670/660/709 (exact) beat the noisy
# autopos rigid fit (104 mm rms) for the STEERING geometry.
TAG_ORDER = ["0xb1f4", "0xb136", "0xb15a"]
TAG_DEV = {"0xb1f4": "BSCCF4", "0xb136": "BS9336", "0xb15a": "BS955A"}
CAL_LOCAL = {"0xb1f4": (-285.0, 0.0), "0xb136": (385.0, 0.0), "0xb15a": (0.0, -595.0)}
BASELINE_MAX_MM = 708.7                    # 9336<->955A, for theoretical resolution

DEV = "cuda:0"


# ---------------------------------------------------------------------------- #
def load_templates():
    """Return dict L -> complex64 tensor [3,1016] (tag order = TAG_ORDER),
    direct path zeroed (FP +/- DP_HALF)."""
    out = {}
    for L in RXL:
        rows = []
        for s in TAG_ORDER:
            A = np.load(os.path.join(TPL, f"{L}_{s}_A.npy")).astype(np.complex64)
            A[FP - DP_HALF:FP + DP_HALF + 1] = 0     # remove direct-path main lobe
            rows.append(A)
        out[L] = torch.from_numpy(np.stack(rows)).to(DEV)
    return out


def wand_local_frame():
    """Build the wand-plane local 2D frame from the rigid 3D pose so we can
    express listener directions (room frame) as a local azimuth for markers.
    e1 = CCF4->9336 (caliper +x), e2 in-plane toward -955A side (caliper +y)."""
    wr = json.load(open(os.path.join(AUTOPOS, "wand_positions_rigid.json")))
    c = np.array(wr["centroid"])
    p = {d: np.array(wr[d]) for d in ("BSCCF4", "BS9336", "BS955A")}
    e1 = p["BS9336"] - p["BSCCF4"]; e1 /= np.linalg.norm(e1)
    nrm = np.cross(p["BS9336"] - p["BSCCF4"], p["BS955A"] - p["BSCCF4"])
    nrm /= np.linalg.norm(nrm)
    e2 = np.cross(nrm, e1); e2 /= np.linalg.norm(e2)
    # caliper 955A is at local y=-595 -> ensure (p955a-c).e2 < 0
    if np.dot(p["BS955A"] - c, e2) > 0:
        e2 = -e2
    return c, e1, e2


def local_azimuth(vec3, e1, e2):
    return np.degrees(np.arctan2(np.dot(vec3, e2), np.dot(vec3, e1)))


def beamform(h3, tx_local, device=DEV):
    """Coherent true-time-delay beamformer.
    h3        : complex64 [3,1016]  (tags in TAG_ORDER, DP removed)
    tx_local  : float [3,2] mm      (caliper local coords, same tag order)
    returns   : power [NAZ, NTAP] (linear), and az grid.
    """
    h = h3.to(device)
    H = torch.fft.fft(h, dim=1)                                   # [3,1016]
    k = torch.fft.fftfreq(NTAP, device=device).to(torch.float32)  # cycles/tap [1016]

    th = torch.deg2rad(torch.tensor(AZ, device=device, dtype=torch.float32))
    u = torch.stack([torch.cos(th), torch.sin(th)], 0)            # [2, NAZ]
    txl = torch.tensor(tx_local, device=device, dtype=torch.float32)  # [3,2]
    d = txl @ u                                                   # [3, NAZ]  d_i(theta) mm
    tap_shift = d / TAP_SPACING_MM                                # [3, NAZ]  taps
    cphase = torch.exp(1j * 2 * np.pi * d / LAMBDA_MM)            # [3, NAZ]  carrier steer

    # envelope advance (delay by -tap_shift): multiply H by exp(+j 2pi k tap_shift)
    ramp = torch.exp(1j * 2 * np.pi *
                     tap_shift[:, :, None] * k[None, None, :])    # [3, NAZ, 1016]
    steered = H[:, None, :] * ramp * cphase[:, :, None]           # [3, NAZ, 1016]
    S = steered.sum(0)                                            # [NAZ, 1016] freq
    s = torch.fft.ifft(S, dim=1)                                  # [NAZ, 1016] time
    return (s.real ** 2 + s.imag ** 2)                            # power


def to_db(power, ref=None):
    p = power.detach().cpu().numpy()
    ref = p.max() if ref is None else ref
    return 10 * np.log10(np.maximum(p, ref * 1e-12) / ref)


# ---------------------------------------------------------------------------- #
def synth_channel(theta0_deg, tap0, tx_local, device=DEV):
    """Forward model: a unit point scatterer at (theta0, tap0). Tag i gets a
    unit impulse at tap0 delayed by +tap_shift_i and carrier phase -2pi d_i/lam."""
    th = np.radians(theta0_deg)
    u = np.array([np.cos(th), np.sin(th)])
    d = tx_local @ u                                             # [3] mm
    tap_shift = d / TAP_SPACING_MM
    k = np.fft.fftfreq(NTAP)
    base = np.zeros(NTAP, np.complex64); base[tap0] = 1.0
    Bf = np.fft.fft(base)
    rows = []
    for i in range(3):
        shifted = np.fft.ifft(Bf * np.exp(-2j * np.pi * k * tap_shift[i]))  # delay +shift
        rows.append((shifted * np.exp(-2j * np.pi * d[i] / LAMBDA_MM)).astype(np.complex64))
    return torch.from_numpy(np.stack(rows)).to(device)


def mainlobe_and_grating(theta0_deg, tap0, tx_local):
    """Inject the synthetic scatterer, run the beamformer, measure -3 dB main-lobe
    azimuth width and the grating-lobe spacing at the injection delay."""
    h = synth_channel(theta0_deg, tap0, tx_local)
    P = beamform(h, tx_local)
    row = P[:, tap0].detach().cpu().numpy()
    db = 10 * np.log10(np.maximum(row, row.max() * 1e-12) / row.max())
    pk = int(np.argmax(db))
    # -3 dB main-lobe width around the recovered peak
    lo = pk
    while lo - 1 >= 0 and db[lo - 1] >= -3.0:
        lo -= 1
    hi = pk
    while hi + 1 < NAZ and db[hi + 1] >= -3.0:
        hi += 1
    width = (hi - lo) * (AZ[1] - AZ[0])
    # honest ambiguity accounting: with 3 elements spaced >> lambda/2 the pattern
    # is a picket fence. Count near-height lobes and the azimuth span they occupy.
    from scipy.signal import find_peaks
    pks1, _ = find_peaks(db, height=-1.0)
    pks3, _ = find_peaks(db, height=-3.0)
    pks6, _ = find_peaks(db, height=-6.0)
    gl = np.sort(np.abs(AZ[pks3] - AZ[pk]))
    grating = float(gl[gl > 1.0][0]) if np.any(gl > 1.0) else float("nan")
    span6 = float((db >= -6.0).sum() * (AZ[1] - AZ[0]))     # deg of az within 6 dB of peak
    return dict(recovered_az=float(AZ[pk]), injected_az=float(theta0_deg),
                mainlobe_deg=float(width), grating_deg=grating,
                theory_deg=float(np.degrees(np.arcsin(LAMBDA_MM / BASELINE_MAX_MM))),
                row_db=db, peaks=AZ[pks1],
                n_lobes_1db=int(len(pks1)), n_lobes_3db=int(len(pks3)),
                n_lobes_6db=int(len(pks6)), ambig_span6_deg=round(span6, 1))


# ---------------------------------------------------------------------------- #
def main():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t_all = time.time()
    print(f"[cfg] lambda={LAMBDA_MM:.2f}mm  tap={TAP_SPACING_MM:.2f}mm  FP={FP}  "
          f"delay taps [{DELAY0},{DELAY1})  az {NAZ} pts  device={DEV}")
    print(f"[cfg] GPU: {torch.cuda.get_device_name(0)}")

    tx_local = np.array([CAL_LOCAL[s] for s in TAG_ORDER], np.float32)

    # geometry verification
    print("\n[geom] caliper local-2D (mm) & baselines:")
    for a in range(3):
        for b in range(a + 1, 3):
            bl = np.linalg.norm(tx_local[a] - tx_local[b])
            print(f"       {TAG_DEV[TAG_ORDER[a]]}-{TAG_DEV[TAG_ORDER[b]]}: {bl:.1f} mm")
    c, e1, e2 = wand_local_frame()
    wr = json.load(open(os.path.join(AUTOPOS, "wand_positions_rigid.json")))
    print("[geom] rigid pose projected into local frame (sign check vs caliper):")
    for s in TAG_ORDER:
        v = np.array(wr[TAG_DEV[s]]) - c
        print(f"       {TAG_DEV[s]:7s} local=({np.dot(v,e1):+7.1f},{np.dot(v,e2):+7.1f})  "
              f"caliper=({CAL_LOCAL[s][0]:+.0f},{CAL_LOCAL[s][1]:+.0f})")

    # listener directions (room frame) -> local azimuth markers
    lay = json.load(open(os.path.join(AUTOPOS, "layout_besteffort.json")))
    anc = {a["label"]: np.array([a["x_mm"], a["y_mm"], a["z_mm"]]) for a in lay["anchors"]}
    look_az = {}
    for L in RXL:
        v = anc[ANCHOR_OF[L]] - c
        look_az[L] = local_azimuth(v, e1, e2)
    print("[geom] wand->listener direct-path local azimuth:",
          {L: round(look_az[L], 1) for L in RXL})

    # ---- STEP 1: beamform each listener ----
    tmpls = load_templates()
    torch.cuda.reset_peak_memory_stats(0)
    beams_db, timing = {}, {}
    for L in RXL:
        t0 = time.time()
        P = beamform(tmpls[L], tx_local)
        torch.cuda.synchronize(0)
        timing[L] = time.time() - t0
        beams_db[L] = to_db(P[:, DELAY0:DELAY1])          # [NAZ, 185] dB re per-listener max
        print(f"[beam] {L}: {timing[L]*1e3:.1f} ms  peak@az="
              f"{AZ[np.unravel_index(beams_db[L].argmax(), beams_db[L].shape)[0]]:.1f} deg")
    vram = torch.cuda.max_memory_allocated(0) / 1e6

    delay_taps = np.arange(DELAY0, DELAY1)
    bistatic_m = (delay_taps - FP) * TAP_SPACING_MM / 1e3     # excess -> bistatic range (m)
    excess_ns = (delay_taps - FP) * TAP_NS

    # ---- STEP 5: resolution via synthetic injection ----
    res = mainlobe_and_grating(45.0, FP + 80, tx_local)
    print(f"\n[res] injected az=45 recovered={res['recovered_az']:.1f}  "
          f"mainlobe(-3dB)={res['mainlobe_deg']:.2f} deg  theory={res['theory_deg']:.2f} deg")
    print(f"[res] AMBIGUITY: nearest lobe {res['grating_deg']:.1f} deg away; "
          f"{res['n_lobes_3db']} lobes within 3dB, {res['n_lobes_6db']} within 6dB "
          f"spanning {res['ambig_span6_deg']:.0f} deg of azimuth")

    # ---- STEP 2: polar plots ----
    TH = np.radians(AZ)
    for L in RXL:
        fig = plt.figure(figsize=(7, 7))
        ax = fig.add_subplot(111, projection="polar")
        ax.set_theta_zero_location("E"); ax.set_theta_direction(1)
        Rg, Thg = np.meshgrid(bistatic_m, TH)
        pcm = ax.pcolormesh(Thg, Rg, np.clip(beams_db[L], -20, 0),
                            shading="auto", cmap="turbo", vmin=-20, vmax=0)
        ax.plot([np.radians(look_az[L])] * 2, [bistatic_m[0], bistatic_m[-1]],
                "w--", lw=1.6, label="wand->listener")
        ax.set_title(f"{L}  coherent wand-aperture beam\n(radial = bistatic excess range, m)",
                     fontsize=11, pad=18)
        ax.legend(loc="upper right", bbox_to_anchor=(1.15, 1.10), fontsize=8)
        cb = fig.colorbar(pcm, ax=ax, pad=0.10, shrink=0.7); cb.set_label("power (dB)")
        fig.savefig(os.path.join(OUT, f"{L}_polar_beam.png"), dpi=130, bbox_inches="tight")
        plt.close(fig)

    # ---- STEP 3: DOA extraction (-6 dB per listener) ----
    dets = []
    for L in RXL:
        b = beams_db[L]
        for ti in range(b.shape[1]):
            col = b[:, ti]
            # local maxima above -6 dB
            for ai in range(NAZ):
                if col[ai] >= -6.0 and col[ai] >= col[(ai - 1) % NAZ] and col[ai] >= col[(ai + 1) % NAZ]:
                    dets.append(dict(listener=L, azimuth_deg=round(float(AZ[ai]), 2),
                                     excess_delay_ns=round(float(excess_ns[ti]), 3),
                                     bistatic_range_m=round(float(bistatic_m[ti]), 3),
                                     power_dB=round(float(col[ai]), 2)))
    with open(os.path.join(OUT, "doa_detections.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["listener", "azimuth_deg", "excess_delay_ns",
                                          "bistatic_range_m", "power_dB"])
        w.writeheader(); w.writerows(dets)
    ndet = {L: sum(d["listener"] == L for d in dets) for L in RXL}
    print(f"[doa] detections (>-6dB local maxima): {ndet}  total={len(dets)}")

    # ---- STEP 5 fig: resolution ----
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.fill_between(AZ, res["row_db"], -6, where=(res["row_db"] >= -6), color="orange",
                    alpha=0.25, label=f"within 6 dB ({res['ambig_span6_deg']:.0f} deg of az)")
    ax.plot(AZ, res["row_db"], lw=1.1)
    ax.axvline(res["recovered_az"], color="g", ls="--", lw=1, label=f"true peak {res['recovered_az']:.1f} deg")
    ax.axhline(-3, color="r", ls=":", lw=1, label="-3 dB")
    ax.axhline(-6, color="brown", ls=":", lw=0.8)
    ax.set(xlabel="azimuth (deg)", ylabel="power (dB)", ylim=(-25, 1),
           title=f"Synthetic point scatterer @45 deg  |  main lobe {res['mainlobe_deg']:.1f} deg "
                 f"(theory {res['theory_deg']:.1f})  |  {res['n_lobes_6db']} ambiguous lobes "
                 f"within 6 dB -> DOA non-unique")
    ax.legend(fontsize=8, loc="lower center", ncol=3); ax.grid(alpha=0.3)
    fig.savefig(os.path.join(OUT, "resolution_check.png"), dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---- STEP 6: coherent vs incoherent ----
    fig = plt.figure(figsize=(13, 6))
    axi = fig.add_subplot(121)
    bp = os.path.join(STEP4, "backprojection_volume.npy")
    if os.path.exists(bp):
        vol = np.load(bp)
        mip = vol.max(axis=2)                          # top-down XY MIP
        mdb = 20 * np.log10(np.maximum(mip, mip.max() * 1e-3) / mip.max())
        im = axi.imshow(mdb.T, origin="lower", cmap="turbo", vmin=-20, vmax=0, aspect="auto")
        fig.colorbar(im, ax=axi, shrink=0.8, label="dB")
    axi.set_title("Incoherent backprojection (step4)\nXY MIP - the featureless bullseye")
    axc = fig.add_subplot(122, projection="polar")
    axc.set_theta_zero_location("E"); axc.set_theta_direction(1)
    Rg, Thg = np.meshgrid(bistatic_m, TH)
    pcm = axc.pcolormesh(Thg, Rg, np.clip(beams_db["LF"], -20, 0), shading="auto",
                         cmap="turbo", vmin=-20, vmax=0)
    fig.colorbar(pcm, ax=axc, shrink=0.6, pad=0.10, label="dB")
    axc.set_title("Coherent wand beam (LF)\naz x bistatic range")
    fig.suptitle("Step 6 - incoherent amplitude bullseye  vs  coherent phase beam", fontsize=12)
    fig.savefig(os.path.join(OUT, "coherent_vs_incoherent.png"), dpi=130, bbox_inches="tight")
    plt.close(fig)

    # ---- STEP 4: best-effort triangulation (cross-listener az agreement) ----
    triangulated = False
    tri_note = "not attempted"
    try:
        # bin strongest detection per (listener, delay tap), look for >=2 listeners
        # agreeing in bistatic range; intersect wand-frame azimuth rays from the
        # wand centroid (NOTE: heavily caveated - see report).
        strongest = {}
        for d in dets:
            key = (d["listener"], round(d["bistatic_range_m"], 1))
            if key not in strongest or d["power_dB"] > strongest[key]["power_dB"]:
                strongest[key] = d
        by_range = {}
        for (L, r), d in strongest.items():
            by_range.setdefault(r, {})[L] = d
        # simple pairwise intersection in the wand LOCAL 2D plane
        pts = []
        for r, dd in by_range.items():
            Ls = list(dd)
            for i in range(len(Ls)):
                for j in range(i + 1, len(Ls)):
                    a1 = np.radians(dd[Ls[i]]["azimuth_deg"]); a2 = np.radians(dd[Ls[j]]["azimuth_deg"])
                    # rays from centroid (local origin) - if nearly parallel skip
                    if abs(np.sin(a1 - a2)) < 0.05:
                        continue
                    # both rays pass through origin -> intersection is origin; use
                    # listener local positions as ray origins instead
                    o1 = np.array([np.dot(anc[ANCHOR_OF[Ls[i]]] - c, e1), np.dot(anc[ANCHOR_OF[Ls[i]]] - c, e2)])
                    o2 = np.array([np.dot(anc[ANCHOR_OF[Ls[j]]] - c, e1), np.dot(anc[ANCHOR_OF[Ls[j]]] - c, e2)])
                    d1 = np.array([np.cos(a1), np.sin(a1)]); d2 = np.array([np.cos(a2), np.sin(a2)])
                    Aa = np.array([[d1[0], -d2[0]], [d1[1], -d2[1]]])
                    if abs(np.linalg.det(Aa)) < 1e-6:
                        continue
                    t = np.linalg.solve(Aa, o2 - o1)
                    P = o1 + t[0] * d1
                    if np.linalg.norm(P) < 8000:   # within ~8 m of wand
                        pts.append(P)
        if pts:
            triangulated = True
            pts = np.array(pts)
            fig, ax = plt.subplots(figsize=(7, 7))
            for L in RXL:
                o = np.array([np.dot(anc[ANCHOR_OF[L]] - c, e1), np.dot(anc[ANCHOR_OF[L]] - c, e2)])
                ax.plot(*o, "^", ms=12, label=f"{L}")
                ax.annotate(L, o)
            ax.plot(0, 0, "ks", ms=8, label="wand centroid")
            ax.scatter(pts[:, 0], pts[:, 1], c="r", s=30, alpha=0.5, label="triangulated")
            ax.set(xlabel="local x (mm)", ylabel="local y (mm)",
                   title="Step 4 best-effort reflector triangulation (wand local plane)")
            ax.legend(fontsize=8); ax.axis("equal"); ax.grid(alpha=0.3)
            fig.savefig(os.path.join(OUT, "triangulated_reflectors.png"), dpi=130, bbox_inches="tight")
            plt.close(fig)
            tri_note = f"{len(pts)} candidate intersections (SEE CAVEAT)"
        else:
            tri_note = "no cross-listener range agreement -> no intersection"
    except Exception as e:
        tri_note = f"failed: {e}"
    print(f"[tri] {tri_note}")

    # element spacing vs lambda/2 (the Nyquist sampling limit for unambiguous DOA)
    spacings = [np.linalg.norm(tx_local[a] - tx_local[b])
                for a in range(3) for b in range(a + 1, 3)]
    min_spacing = float(min(spacings))
    nyquist_mm = LAMBDA_MM / 2.0
    undersample = round(min_spacing / nyquist_mm, 1)

    # ---- summary ----
    summary = dict(
        gpu=torch.cuda.get_device_name(0), vram_peak_mb=round(vram, 1),
        wall_s=round(time.time() - t_all, 2),
        ms_per_listener={L: round(timing[L] * 1e3, 1) for L in RXL},
        n_detections_raw=ndet,
        n_detections_note="dominated by grating lobes - NOT physical reflector count",
        mainlobe_deg=round(res["mainlobe_deg"], 2), theory_deg=round(res["theory_deg"], 2),
        phase_alignment_ok=abs(res["recovered_az"] - res["injected_az"]) < 1.0,
        recovered_vs_injected=[round(res["recovered_az"], 1), res["injected_az"]],
        nearest_ambiguous_lobe_deg=round(res["grating_deg"], 2),
        n_ambiguous_lobes_within_3dB=res["n_lobes_3db"],
        n_ambiguous_lobes_within_6dB=res["n_lobes_6db"],
        ambiguity_az_span_within_6dB_deg=res["ambig_span6_deg"],
        min_element_spacing_mm=round(min_spacing, 0),
        nyquist_spacing_mm=round(nyquist_mm, 1),
        aperture_undersampled_x=undersample,
        triangulation=tri_note,
        lambda_mm=round(LAMBDA_MM, 2), tap_mm=round(TAP_SPACING_MM, 2),
    )

    # honest verdict: sharp main lobe alone is not success - the array is
    # grating-lobe limited when spacing >> lambda/2 and many lobes rival the peak.
    unambiguous = (summary["phase_alignment_ok"]
                   and res["n_lobes_3db"] <= 3
                   and res["ambig_span6_deg"] <= 30.0)
    if unambiguous:
        verdict = ("coherent beamforming reveals angular structure that incoherent "
                   "backprojection missed")
    else:
        verdict = ("no clean angular structure recovered - the 3-element wand aperture "
                   f"is grating-lobe limited (min spacing {min_spacing:.0f}mm = "
                   f"{undersample}x lambda/2). Phase alignment is correct (synthetic 45deg "
                   f"recovered to {res['recovered_az']:.1f}deg, {res['mainlobe_deg']:.1f}deg "
                   f"main lobe) but {res['n_lobes_6db']} near-height ambiguous lobes span "
                   f"{res['ambig_span6_deg']:.0f}deg of azimuth -> DOA is non-unique; the "
                   "real-data beams show only near-in clutter shaped by this ambiguous pattern")
    print("\n================= OUTPUT SUMMARY =================")
    for k, v in summary.items():
        print(f"  {k:28s}: {v}")
    print(f"  VERDICT                     : {verdict}")
    print("=================================================")
    summary["verdict"] = verdict
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    print(f"[out] wrote -> {OUT}")


if __name__ == "__main__":
    main()
