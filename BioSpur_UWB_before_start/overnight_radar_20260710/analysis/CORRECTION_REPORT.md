# Overnight Analysis — Correction Report (v1 → v2)

**Trigger:** external code review found 2 critical errors + 1 bug in the overnight
CIR pipeline. **Scope:** re-derive affected steps, preserve v1 outputs for audit
(`*_v1/` dirs), emit corrected `*_v2/` outputs. All work is in-repo under
`overnight_radar_20260710/analysis/`. Raw `.log` files were **not** touched.

## Headline: what each fix changed

| step | v1 (as-published) | v2 (corrected) | conclusion |
|---|---|---|---|
| **1 alignment** | RCPHASE sub-tap; dp_snr median **23.4** (13.8–40.7) | FP_INDEX sub-tap; dp_snr median **82.1** (35.4–114) | templates were **degraded**; fixed |
| **2 stability** | leakage: 0/15 cross 1σ, worst **0.84σ** | holdout: **4/15 cross 1σ**, worst **1.31σ** | ">10 h" must be **qualified** |
| **3 PSF** | **6.0×5.2×4.0 m** (0/15 ch, all-zero → full grid) | **3.3×5.3×5.4 m** (15/15 ch, unclipped) | v1 number **retracted**; conclusion survives |
| 3-multipath | 5.3 reflectors/ch | 17.7 reflectors/ch | tighter σ → more sensitive |
| 5 beamform | grating-lobe limited, 28.6×λ/2 | grating-lobe limited, 28.6×λ/2 | **unchanged** (as expected) |

---

## BUG 1 (critical) — RCPHASE is not sub-tap time; use FP_INDEX

**What was wrong.** `step1_template.py` shifted each CIR by `rcph/128` taps.
RCPHASE (RX_TTCKO bits 31:25) is the **carrier phase** of the received signal vs
the sampling clock — not a sub-tap arrival time. The sub-tap arrival time is the
fractional part of **FP_INDEX** (LDE first-path index, 10.6 fixed-point).

**Parser check (step0): no fix needed.** Ground-truth: `main.c:576` stores
`diag.firstPath` **raw** (16-bit 10.6); parsed `fp_index` values are the full
value (median ≈ 47854 → /64 = 747.7 taps; min 22210 / max 54085), and the
fractional 6 bits are non-zero in **99.6%** of frames. The parser already
preserves the full 16-bit value — **no reparse required.**

**Fix (`step1_template_v2.py`).**
- coarse: integer roll `REF_TAP − (fp_index >> 6)` (floor)
- sub-tap: FFT phase ramp by `−fp_frac`, `fp_frac = (fp_index & 0x3F)/64` — centres
  the true first path (which sits at `REF_TAP + fp_frac` after the floor roll)
  exactly on `REF_TAP`. **The RCPHASE ramp is removed entirely.**
- kept: first-path complex-phase referencing (clock-independent) and rxpacc
  amplitude normalization — both correct and unchanged.

**Alignment quality — FP-tap amplitude CV (std/mean, lower = tighter), same coarse
roll, three sub-tap treatments (3 cleanest channels):**

| channel | dp_snr(v2) | raw (coarse only) | v1 = rcph/128 | v2 = fp_frac |
|---|---:|---:|---:|---:|
| LB←0xb15a | 114.0 | 0.602 | 0.972 | **0.052** |
| L955A←0xb1f4 | 103.5 | 0.737 | 1.044 | **0.048** |
| LF←0xb136 | 95.6 | 0.572 | 1.046 | **0.060** |

**v2 is tighter than v1 on 15/15 channels.** Notably, **v1 (rcph) was worse than
doing nothing** (coarse-only) on every channel — the RCPHASE shift was *actively
degrading* the templates. Corrected DP-SNR rises ~3.5× (median 23.4 → 82.1).
All downstream steps were re-run on `templates_v2/`.

---

## BUG 2 (critical) — Step 2 stability had data leakage

**What was wrong.** `step2_stability.py` built `A_total` from **all** frames across
10.5 h, then measured each 30-min window's drift against `A_total`. Because
`A_total` contains the window under test, drift is under-estimated (no holdout).

**Fix (`step2_stability_v2.py`).** Freeze the template on **only the first 30 min**
(`A_frozen` = complex median, `sigma_frozen` = 1.4826·MAD of those frames). Every
window from 30 min to 10.5 h is a **pure holdout**:
`drift_rms = sqrt(mean(|A_window − A_frozen|²)) / mean(sigma_frozen)`. Also tracks,
per holdout window: FP-tap amplitude drift, FP-tap phase drift → mm, and
false-alarm taps (`|A_window − A_frozen| > 5·sigma_frozen`). Uses the corrected
FP_INDEX alignment.

**Result:**

| metric | v1 (leaky) | v2 (holdout) |
|---|---|---|
| max drift, median / worst | 0.49σ / **0.84σ** | 0.78σ / **1.31σ** |
| channels crossing 1σ within 10 h | **0 / 15** | **4 / 15** |
| false-alarm taps / hour (median / worst) | — | 0.0 / 0.7 |
| FP phase drift → mm | — | ≈ 0 mm (removed by FP referencing) |

The 4 channels that cross the 1σ shelf-life threshold (all on listener **LE @
anchor E**, plus the cleanest LB channel right at the edge):

| channel | shelf-life (t @ 1σ) | max drift |
|---|---|---|
| LE←0xb1f4 | 6.0 h | 1.28σ |
| LE←0xb136 | 6.5 h | 1.24σ |
| LE←0xb15a | 6.5 h | 1.31σ |
| LB←0xb1f4 | 10.0 h | 1.00σ |

**Honest verdict:** the leakage under-estimated drift (worst 0.84σ → 1.31σ). The
">10 h template shelf-life" claim holds for **11/15 channels**, but the **LE
listener's channels drift past 1σ at ~6 h**. False-alarm taps stay ≤0.7/h even for
drifting channels, so background subtraction remains practically clean — but the
"static template valid for the full night on **every** channel" statement is
retracted.

---

## BUG 3 — Step 4 PSF was all-zero (gate/target bug)

**What was wrong.** The synthetic PSF target `[1500,1500,500] mm` has small
bistatic excess → tap ≈ 801–809 for every channel, **inside** the direct-path gate
(`DP_END = 812`). `synth_psf` only injects a delta when `DP_END < t0 ≤ TAIL_END`,
so **0/15 channels** contributed → PSF volume all-zero → `res_6db`'s
`vol ≥ peak·10^(−6/20)` with `peak = 0` selected the **entire grid**. The published
"6.0 × 5.2 × 4.0 m PSF" was literally the search-grid size, not a physics result.

**Fix (`step4_backprojection_v2.py`).**
1. Replaced the hardcoded `DP_END = 812` with the **per-channel Step-3 main-lobe
   gate** (peak in [FP,FP+15], walk out to 25% of peak) — measured ends are
   **806–814**, matching Step 3 instead of a magic number.
2. Moved the synthetic target **outside** the exclusion zone (grid-searched so all
   channels clear their main lobe): target `[-1000, 3900, -1500] mm`, per-channel
   excess taps **823–836** (all > main lobe, ≤ TAIL_END 872).
3. Measured the PSF on a **dedicated ±6000 mm local grid** centred on the target so
   the blob is never clipped (`edge_clipped = False`).
4. Re-ran with `templates_v2`.

**Result:** PSF now has **15/15 channels** contributing (v1: 0/15), peak 10.3.
Corrected **−6 dB extent = 3.3 × 5.3 × 5.4 m** (real, unclipped). Image dynamic
range 6.5 → 6.9 dB.

**Interpretation.** The incoherent-backprojection geometric PSF is genuinely
**metres-scale** — this *confirms* "no usable spatial resolution / range-limited
bullseye", but the specific v1 number **6.0×5.2×4.0 m is retracted** (it was the
full-grid artifact and only coincidentally similar in magnitude). Caveat: this is
the geometric PSF of the imaging operator (ideal delta); real resolution is
additionally limited by the ~12-tap (~3.6 m) direct-path pulse width and the
near-in blind zone (~2.1–4.2 m). The **6.9 dB dynamic range** independently
confirms the clutter-limited image (a real image needs 20+ dB).

---

## Downstream re-runs on the corrected templates

**Step 3 multipath (`multipath_v2/`).** Same algorithm, `templates_v2` input. The
tighter alignment lowers the per-tap noise floor (σ), so more weak reflectors clear
5σ: **17.7 reflectors/channel** (v1: 5.3), near-in blind zone ~2.8 m (≈ v1).
Reflectors span ~1.8–15 m bistatic excess. This is increased *sensitivity*, not new
physics.

**Step 5 coherent beamforming (`beamforming_v2/`, GPU cuda:0).** Verdict
**unchanged**, as anticipated: phase alignment is correct (synthetic 45° recovered
to 45.0°, 2.0° main lobe vs 3.74° theory), but the 3-element wand aperture is
**28.6× λ/2 undersampled** → **23 near-height ambiguous lobes span 62° of azimuth**
→ DOA is non-unique. The grating-lobe conclusion does not change; only the
real-data beam plots now use the corrected templates.

---

## Conclusions: what survives vs what is retracted

**Survives (unchanged or strengthened):**
- Channel & hardware quality is excellent — *strengthened* (DP-SNR ~3.5× higher
  after correct alignment).
- Coherent beamforming is grating-lobe limited; DOA non-unique (Step 5 identical).
- Incoherent static-wand imaging is range-limited (bullseye): 6.9 dB dynamic range,
  metres-scale PSF, near-in blind zone.
- Multipath structure is real and rich (Step 3, now more sensitive).

**Retracted / revised:**
- ❌ "All 15 channels stay < 1σ for 10.5 h (worst 0.84σ)" → ✅ **11/15 < 1σ; 4
  channels (listener LE + cleanest LB) cross 1σ at 6–10 h**, worst 1.31σ. Static
  background subtraction is valid multi-hour, but **not uniformly >10 h on every
  channel** — the LE-side channels have ~6 h shelf-life.
- ❌ "Multistatic PSF = 6.0 × 5.2 × 4.0 m" → ✅ **3.3 × 5.3 × 5.4 m** (the v1 number
  was an all-zero-PSF / full-grid bug; the corrected value still confirms poor
  spatial resolution).
- ⚠️ All v1 template-derived numbers (Steps 2–5) came from RCPHASE-misaligned
  templates and are **superseded** by the `*_v2` outputs.

---

## Outputs

**Preserved (audit):** `templates_v1/ stability_v1/ step4_v1/ multipath_v1/ beamforming_v1/`

**Corrected (this pass):**
- `step1_template_v2.py` → `templates_v2/` (15 A+σ, `step1_v2_report.csv`, `alignment_compare.csv`)
- `step2_stability_v2.py` → `stability_v2/` (`drift_summary.csv`, `drift_curves_holdout.png`, `false_alarm_rate.png`, `stability_v2_summary.json`)
- `step4_backprojection_v2.py` → `step4_v2/` (`backprojection_volume.npy`, `step4_backprojection_mip.png`, `step4_psf_mip.png`, `step4_v2_stats.json`)
- `step3_multipath_v2.py` → `multipath_v2/` (15 reflector JSONs, `all_reflectors_summary.csv`)
- `step5_coherent_beamform_v2.py` → `beamforming_v2/` (polar beams, `doa_detections.csv`, `resolution_check.png`, `coherent_vs_incoherent.png`, `summary.json`)

**Execution notes:** per-channel work used `multiprocessing.Pool(10)`; Step 5 used
GPU `cuda:0` (67.9 MB VRAM peak). All heavy jobs ran under `ulimit -v` with RAM
monitored (≥14 GB free throughout) — no OOM risk to the concurrent cold-start
capture.
