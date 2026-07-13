# Solver V2 Validation — V5 Layout + U5 Tag-Solver changes

**Date:** 2026-07-12
**Harness:** [`validate.py`](validate.py) → [`validation_results.json`](validation_results.json)
**CPU:** i7-8700K, 6 cores / **12 logical**, 32 GB RAM. All blocks CPU-only (no GPU).
Wall-clock **26.9 s** (overnight subsampled stride-41 → 3,816 frames + per-line CIR decode; parse 3.3 s).
Single-process, one core ~100 % for ~27 s (C core is µs/solve; Python LSCAN+CIR parse dominates).

> **Design note (revised):** the U5-host path **never drops an anchor** — the LOO/rejection
> path stays dead by design (MC5000: 8→7 anchors loses precision under tight z-DOP). All
> anchor influence is controlled by per-anchor σ = a **uniform 25 mm hardware baseline** ×
> a **per-frame RF-informed multiplier (≥ 1.0)**, then Huber. Per-anchor σ is NOT baked from
> one environment's overnight data.

> **TL;DR.** (1) V5 confirms the layout **scale is unidentifiable** from inter-anchor ranges
> (loosening the ±60 box moves the layout 534 mm) — needs a metric constraint. (2) The
> RF-informed σ mechanism works and **never drops an anchor**, but the chosen RF metric
> (**first-path SNR**) **does not discriminate this deployment's event anchors** — B/E/H all
> show *high* first-path SNR (54–77), so σ is never inflated and RF-σ ≡ flat-σ here. The B
> step / H multipath are range-*bias* events with an intact first path, invisible to FP-SNR.
> (3) Applying delays helps the delay-heavy anchors (C −38 mm, E −28 mm LOO residual).

---

## Block 1 — Layout: V4-IO vs V5 (inter-anchor, `pairs_all.csv`, 28 pairs)

| solver | pair RMS (mm) | common-mode c (mm) | max Δ from V4-IO (mm) | scale identifiable |
|---|---|---|---|---|
| **V4-IO (deployed)** | 105.76 | 0 (implicit) | 0 | (pinned by ±60 box) |
| **V5 (spec: c±150, e±200, σ_e=30)** | **94.65** | **150.0 (at bound)** | **534.2** | **No** |
| **V5 (scale-lock: c=0, e±60)** | 100.90 | 0 | 60.4 | Yes |

- V5's common-mode reparameterization **gets C and H off their individual +60 bound** (their
  differentials become small: 3.2, −19.4 mm) and improves pair RMS 105.8 → 94.7 mm.
- **But the common mode saturates ±150 and the layout moves 534 mm** — the inter-anchor data
  wants a ~150 mm shared bias that is degenerate with isotropic scale (ρ≈−0.977). Every
  loosened-delay variant (incl. the production `solve_v4_common_mode`) diverges 300–570 mm from
  V4-IO; the layout is only reproducible by re-imposing a ±60-class box (scale-lock, 60 mm).
- **Verdict:** absolute geometry is **not identifiable** from inter-anchor ranges alone
  (confirms `analysis/per_anchor_delay_analysis/REPORT.md:85-93`). Deploy **V5 scale-lock**
  (V4-IO-compatible) or keep V4-IO; the spec-config V5 ships flagged `scale_identifiable=false`
  with a 534 mm-divergence warning so it can't be deployed blind. A metric-scale constraint
  (corner reflector / measured baseline / fixed reference tag) is the prerequisite to loosening
  the box. Stability class on the short calibration sweep: all STABLE (too short for events).

---

## Block 2 — RF-informed σ (overnight static, 3,816 frames; NO LOO, all 8 anchors kept)

**(a) RF-metric discrimination — median first-path SNR from the CIR** (SNR_fp = fp_mag/σ_noise,
~480 CIR frames per anchor after `cir_aid` round-robin; the on-device `fp_ampl1/std_noise` proxy):

| | A | **B** | C | D | **E** | F | G | **H** |
|---|---|---|---|---|---|---|---|---|
| median FP-SNR | 60.2 | **75.8** | 54.2 | 72.7 | **54.1** | 69.6 | 66.4 | **76.5** |
| RF σ multiplier | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

**The event anchors are NOT the low-SNR anchors.** The known-bad B (the ~300 mm step) has the
*highest* first-path SNR (75.8); H (multipath) is 76.5; the lowest are E and C (~54) — and C is
a *stable* anchor. Every anchor's FP-SNR is 54–77, i.e. uniformly "clean LOS" by the spec's
mapping (SNR ≥ 10 → multiplier 1.0). **So FP-SNR triggers zero σ inflation and provides zero
discrimination for these events.**

**Why:** B's step and E/H multipath are range-**bias** events — the direct first path stays
strong (SNR high); the error comes from a geometry/antenna step or a *delayed* multipath
component arriving after an intact first path, not from first-path *attenuation*. First-path SNR
measures the wrong thing for this failure mode. (Consistent with the proxy-gate study finding
CIR features barely predict same-device range error, best |ρ|≈0.10.)

**(b) Position stability — RF-informed σ vs flat uniform σ = 25 mm** (both keep all 8 anchors):

| axis | flat σ=25 | RF-informed σ |
|---|---|---|
| position std (mm) | 35.0 / 36.7 / 117.6 | 35.0 / 36.7 / 117.6 |

Identical — because every RF multiplier is 1.0 (§a), so RF-σ reduces to flat-σ on this data.
The mechanism is correct (verified independently: a synthetic low FP-SNR on a +400 mm-biased
anchor inflates its σ 5× and cuts its position pull 17.1 mm → 0.7 mm, *without dropping it*),
but **it has nothing to act on here because FP-SNR does not fall on the event anchors.**

**Consequence for the design:** FP-SNR is the wrong CIR feature for these events. Options, in
order of promise: (i) use a **timing/shape** CIR feature that senses a delayed/blurred first
path — `FP_PK_ratio`, `RMS_delay_spread`, `rise_time`, or `friis_residual` (range-vs-power
consistency) from `pg_lib.cir_features` — as the σ driver instead of raw FP-SNR; (ii) accept
that step/geometry events are **not RF-observable** and rely on residual-only Huber + the
uniform σ (which is exactly the flat baseline above, and which never drops an anchor). The RF-σ
plumbing supports either — only the metric fed into it changes.

---

## Block 3 — pg_lib delays (baseline walk + person effect)

`solve_pos` now takes a backward-compatible `delays` kwarg (`delays=None` → identical old
behavior). Median |LOO residual| per anchor; delays = V4-IO `d_anchor_mm`.

**Baseline walk (517 frames), delay-heavy anchors:**

| | A | B | **C** | D | **E** | F | G | H |
|---|---|---|---|---|---|---|---|---|
| no delay (mm) | 224.2 | 124.9 | 196.3 | 138.7 | 170.7 | 153.9 | 153.2 | 134.9 |
| with delay (mm) | 216.0 | 127.0 | **158.8** | 136.5 | **142.3** | 159.0 | 156.2 | 132.4 |
| Δ (mm) | −8.2 | +2.1 | **−37.5** | −2.2 | **−28.4** | +5.1 | +3.0 | −2.5 |

Applying the calibrated delays cuts the LOO residual on the delay-heavy anchors (**C −37.5, E
−28.4 mm**) and is near-neutral elsewhere — delays help where a real per-anchor delay exists.

**Person effect (person 1,025 vs clean 758 frames):** near BCFG LOO-residual delta = **−11.4 mm**
vs far ADEH **−0.4 mm** → near−far = **−10.9 mm**: a spatially-localized, person-attributable
signature (~11 mm) the far anchors don't see.

---

## Bottom line per deliverable

| deliverable | status | validated result |
|---|---|---|
| **V5 layout** (`solve_v5.py`) | done | C/H off the bound + RMS 105.8→94.7, but **scale unidentifiable (534 mm)**; ships flagged; scale-lock = safe deployable. |
| **U5-host: RF-informed σ, NO LOO** (`tagpos_solver.c`) | done, verified | Never drops an anchor (`rejected=-1`, all 8 kept); σ = 25 mm × RF multiplier(≥1) × Huber; synthetic NLOS test cuts a biased anchor's pull 17→0.7 mm. |
| **RF metric discrimination** | done | **FP-SNR does NOT catch B/E/H events** (all SNR 54–77) → RF-σ ≡ flat-σ here. Recommend a timing/shape CIR feature instead of FP-SNR (see design doc). |
| **U5-analysis delays** (`pg_lib.solve_pos`) | done, verified | `delays=` kwarg; cuts C/E LOO residual 28–38 mm; backward-compatible. |
| **anchor_sigma.json** | done | **uniform `default_sigma_mm=25`** (hardware floor); NOT per-anchor overnight values (environment-specific). `load_anchor_sigma` expands it to all 8. |
| **firmware layout generator** | done | regenerates `uwb_anchor_layout.{c,h}` with delays + z-flip; compiles. |
| **RF metric design** | done | `analysis/qf_rf_metric_design.md` — metric A/B, σ multiplier convention, deployed-tree gap. |
| **U5-device RF weight (firmware)** | see design doc | Deployed `src/ss_twr_init.c` has **no** RESP_DIAG parse; realizable in the broadcast mirror. Design doc §6. |

## What still needs a decision / hardware
1. **A better RF feature than FP-SNR** for this deployment's events (timing/shape CIR feature),
   or acceptance that step/geometry events are not RF-observable (residual-Huber handles them).
2. **Metric-scale constraint** (corner reflector) before any looser-than-V4-IO layout ships.
3. **Firmware RF metric tree** — broadcast mirror (data exists) vs deployed `src/` port. Design doc §6.
