# Experiment Summary — 2026-07-10 → 2026-07-12

**Repo:** `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`
**Branch:** `feature/wand-internal-sweep` · **HEAD:** `631911c3e` · **Firmware freeze:** `firmware_freeze_20260712/`
**Scope:** ~48 h of range-bias, layout-solver, tag-solver, CIR, listener, and Geiger-scanner work.
All numbers below are pulled from the named JSON/report artifacts, not from memory.

**One-line verdict:** No configuration fixed the wand caliper (stuck at **1/3** everywhere). Real, small wins were banked on **layout pair-RMS** (105.8 → 100.9 mm), **Geiger LOO** (158.7 → 146.2 mm via V5+per-anchor delay), and a large amount of **negative knowledge** — APS011, RSL-indexed correction, FP-SNR/qf weighting, per-tag `d_tag`, and CIR-morphology gating were each tested and closed. The APS011 firmware correction was **deployed then rolled back on hardware**; the system is back to the pre-APS011 V4-IO baseline.

---

## 1. APS011 Range-Bias Correction

### What was done
Deployed the DW1000 datasheet range-bias correction (`dwt_getrangebias()`, ch5/PRF64) fleet-wide — Geiger listener with an added `GEIGER_ANTENNA_DELAY_OFFSET_MM=100`, plus the 3 wand tags via OTA — field-tested it against the pre-APS011 baseline, then ran three offline re-analyses on the **same** baseline log to test every variant (naive firmware-exact, direct-RSL-from-CIR, slope-only) and a per-anchor antenna-delay analysis.

### Key result
- **Raw baseline gauge:** pooled slope **+3.65 %**, common-mode **−100.5 mm**, LOO |resid| median **157.5 mm** (n_loo=3225, n_solved=430).
- **Naive `dwt_getrangebias` — field test:** slope **+3.65 % → −7.01 %**, common-mode **−100.5 → +353.7 mm**, LOO **158 → 251 mm (+59 % worse)** → **NO-GO**. Root cause: the offset + `getrangebias` double-corrected the same DW1000 bias, and `getrangebias` assumes the EVK1000 0 dBi budget while the DWM1001C antenna is ~3 dBi (RSL ~3 dB high → datasheet correction ~3× too large).
- **Naive same-data recompute** (removes walk/offset confounds): slope +3.65 → −0.97 %, cm −100 → +98.8 mm, LOO **157.5 → 179.9 mm** — still net-negative, the field "catastrophe" was inflated by the two confounds.
- **Direct-RSL-from-CIR:** CIR is only a weak RSL proxy (Pearson −0.288); operating RSL span is just **2.7 dB** (expected ~14), so an RSL-indexed table is nearly a constant offset. Best sweep (min-LOO) = **154.9 mm** vs raw 157.5 (~2 %, within noise); the three sweep optima disagree.
- **Slope-only:** LOO **157.5 → 157.3 mm** (no change) — proves the gauge slope is not the dominant error.
- **Per-anchor delay analysis:** constant per-anchor bias RMS **128.6 mm** (A −156 … C +214 … E +194 mm); realizable LOO gain after re-trilateration + cross-validation is only **~7–11 mm** (157.5 → 146.3 in-sample / 150.7 CV). Irreducible multipath/GDOP floor **~146 mm**.

### Verdict
**DEAD END** for any RSL/range-indexed firmware correction (naive APS011 rolled back on hardware). The per-anchor-delay offset is a small real analysis-side win — **PENDING** (recommended as analysis-side offsets, not flashed).

### Quantitative improvement
Naive APS011 as deployed: **158 → 251 mm** LOO (made it worse). Best RSL variant: 157.5 → 154.9 mm (noise). Per-anchor delay offsets (not deployed): **157.5 → 146.3 mm**. Floor ~146 mm.

### Artifacts
- `analysis/aps011_rsl_recomputation/{REPORT.md, recompute_result.json, recompute.py}`
- `analysis/post_aps011_comparison.{json,py}`
- `analysis/per_anchor_delay_analysis/{REPORT.md, results.json, per_anchor_delay.py}`
- `APS011_RANGE_BIAS_HANDOFF.md`, `APS011_DEPLOY_CHECKLIST.md`
- `logs/geiger_scan_20260711_post_aps011/` (field test + `APS011_FIELD_TEST_SUMMARY.md`), `logs/geiger_scan_20260711_rollback_verify/`

### Notes / provenance
Rolled back on hardware: Geiger `getrangebias` off + offset → 0; all 3 wand tags re-OTA'd pre-APS011 (`ota_single_tag_BS9336/BS955A/BSCCF4_20260711_2225xx`), master-reset post-OTA. Register-level per-anchor ANTD table (mm/count = 4.6918, nominal 16436) is documented but **not flashed**; widening the solver delay bound (±60 → ±200) is rejected due to the delay↔scale degeneracy (ρ ≈ −0.977) until a metric constraint exists.

---

## 2. V5 Layout Solver

### What was done
Reparameterized the 8-anchor AutoPos layout solver (`autopos_pipeline/v5/solve_v5.py`) to break the delay↔scale degeneracy: each non-gauge delay `d_i = c + e_i`, where **c** is a single shared common-mode (scale-coupled, ρ=−0.977 → bounded ±150 mm, σ_c=25 mm) and **e_i** is a per-anchor differential (identifiable, ±200 mm, σ_e=30 mm). Same 25 effective DOF as V4-IO; two variants — **scale-lock** (`fix_common_mode=0`, e re-boxed at ±60 → deployable) and **unlocked** (honest best-fit, exposes scale unidentifiability, not deployable).

### Key result
- **Inter-anchor pair RMS:** V4-IO **105.76** → V5 scale-lock **100.9** → V5 unlocked **94.65 mm**.
- **Scale-lock layout deviation from V4-IO:** max 60.40 mm (G), rms 35.12 mm. Unlocked moves the layout max **534 mm** (c saturates its +150 box, `scale_identifiable=false`) — confirms scale is unidentifiable without a metric constraint.
- **C/H bound status:** scale-lock does **NOT** free them — C, H, D, G all stay clipped at the ±60 mm differential box. Only unlocked frees C (+3.24) / H (−19.38 mm), at the 534 mm cost.
- **Geiger LOO median |resid|** (517 frames): V4-IO no-delay **158.7** → V4-IO+delay **150.7** → V5 scale-lock+delay **146.2 mm**.
- **Caliper NOT fixed:** every config passes exactly **1/3**.

### Verdict
**INFRASTRUCTURE** — deployable, marginally better pair-RMS + Geiger LOO, adds honest scale-identifiability diagnostics; does not fix the caliper or free C/H in deployable form.

### Quantitative improvement
Pair-RMS **105.76 → 100.9 mm** (−4.9); Geiger LOO **158.7 → 146.2 mm**. Caliper 1/3 → 1/3.

### Artifacts
- `analysis/v5_vs_v4io/{REPORT.md, comparison.json, v5_layout_scalelock.json, v5_layout_unlocked.json, compare.py}`
- `autopos_pipeline/v5/solve_v5.py`

---

## 3. U5 Tag Position Solver

### What was done
Reworked and validated the host offline tag-position solver (`biospur_tagpos` C-core / `pg_lib`) across four internals: FP-SNR-driven per-frame RF-σ, the leave-one-out (LOO) anchor-rejection path, the per-anchor σ baseline, and an optional 4th-unknown per-tag antenna-delay `d_tag` co-estimation.

### Key result
- **RF-σ (FP-SNR):** σ = 25 mm × RF-multiplier (≥1.0) × Huber, multiplier = `clamp(10/max(SNR,1), 1, 10)`. On the 3,816-frame overnight set every anchor's median FP-SNR was 54–77 → **every multiplier = 1.0** → RF-σ ≡ flat-σ (position std identical: 35.0/36.7/117.6 mm x/y/z). The event anchors (B step, H multipath) carry the *highest* FP-SNR (75.8, 76.5), so FP-SNR cannot flag them. Mechanism itself verified on synthetic NLOS: σ inflated 5×, biased-anchor pull **17.1 → 0.7 mm** without dropping it.
- **LOO rejection:** REJECTED by design (`rejected_index` always −1, all 8 kept) — MC5000 tight z-DOP makes dropping 8→7 lose precision. LOO residual medians are used only to *evaluate* delays.
- **Uniform σ = 25 mm:** shipped as the hardware-floor baseline (`default_sigma_mm=25`), deliberately not per-anchor overnight values (those are environment-specific).
- **d_tag co-estimation:** per-tag medians BSCCF4 **+12.1**, BS9336 **−11.4**, BS955A **+31.6 mm**, **spread = 43 mm** (hypothesis needed 100–300). Caliper **1/3** for 3-unk / per-frame-4unk / batch-4unk alike (CCF4–955A 257 mm vs 660 truth = −403). Synthetic recovery: injected **+150 → recovered 149.5 mm** (std 9.2), position error unchanged (~28 mm) vs 226 mm when the 3-unk smears it. Machinery works; the wand tags simply have no large `d_tag`.

### Verdict (per sub-component)
- **RF-σ (FP-SNR):** DEAD END for this deployment (right plumbing, wrong CIR feature — measures attenuation, not bias/multipath).
- **LOO rejection:** REJECTED / kept-dead by design (z-DOP).
- **Uniform σ 25 mm:** ADOPTED as baseline.
- **d_tag co-estimation:** CORRECT-BUT-NOT-THE-LEVER — ship default OFF (`estimate_d_tag=0`); design doc is PROPOSED, not yet applied to the C-core.

### Quantitative improvement
RF-σ vs flat-σ on real data: **no change**. d_tag on: RMS −1 to −4 mm, caliper 1/3 → 1/3. Where a real per-anchor delay exists, calibrated delays cut LOO on delay-heavy anchors **C −37.5 mm**, **E −28.4 mm**.

### Artifacts
- `analysis/solver_architecture_audit/REPORT.md`
- `analysis/solver_v2_validation/{REPORT.md, validation_results.json, validate.py}`
- `analysis/per_tag_delay/{REPORT.md, results.json, tagpos_solver_4unknown_DESIGN.md, estimate.py, synthetic_recovery_test.py}`

---

## 3b. V5 + U5 vs V4-IO + T4 — end-to-end wand positioning

### What was done
End-to-end caliper/RMS comparison on `system_calibration_20260710_233443` (25,784 TR rows): **A = V4-io layout + T4** (git HEAD) vs **B = V5 scale-lock + U5** (working tree). Caveat: the wand TR log has no FP-SNR, so **U5's RF-σ is inert** — B reduces to uniform-25 mm σ + never-drop-anchor on the V5 layout.

### Key result
Caliper (truth 670 / 660 / 709 mm, ±50 mm):

| pair | truth | A: V4-io+T4 | B: V5+U5 |
|---|---|---|---|
| CCF4–9336 | 670 | 708.1 (+38.1, PASS) | 678.6 (+8.6, PASS) |
| CCF4–955A | 660 | 336.3 (−323.7, FAIL) | 377.4 (−282.6, FAIL) |
| 9336–955A | 709 | 795.0 (+86.0, FAIL) | 800.8 (+91.8, FAIL) |

**Pass: A 1/3, B 1/3 — no change.** Per-wand solve RMS A→B: 132.7→131.1, 118.1→115.0, 123.1→121.4 mm. Scatter ≈ flat (65.3/59.0/55.5 → 63.9/59.9/54.0 mm).

### Verdict
**DEAD END for the caliper goal** — V5+U5 does not change the pass count and only trims RMS ~1–3 mm. The CCF4–955A / 9336–955A triangle-shape distortion persists; it is set by ~110–130 mm per-wand position uncertainty, not the layout.

### Artifacts
- `analysis/v5u5_vs_v4iot4/{REPORT.md, comparison.json, _v4io_t4.json, _v5_u5.json, compare.py}`
- `analysis/v4io_t4_original/{REPORT.md, result.json}` (baseline cross-check: original T4 ≠ deployed Huber-T4 by 120/69/80 mm per wand, both still 1/3)

---

## 4. CIR Analysis

### What was done
A compact-accumulator audit (what CIR data actually reaches tag vs listener) plus an event-discrimination test computing 6 CIR-morphology features per frame against the two known overnight events (anchor-B step, anchor-E multipath bursts), with anchor A as stable control. 59,236 CIR frames decoded (A/B/E ≈ 19.7k each). Each feature's event AUC is compared to a **range-null AUC** to strip the distance/SNR confound.

### Key result
- **Compact `CRX;` BLE line carries ZERO CIR taps** — only 8 rxdiag scalars (~60–90 B). Cannot yield `rms_delay_spread` or a true `fp_to_peak`. Full accumulator (4064 B = 1016 taps) is **USB/CDC-only**; deployed `src/` tag reads no CIR. A windowed 20-tap on-device read is feasible (~84 µs ≈ 12 % of a slot); a full read (~4.4 ms) is not.
- **Discrimination (AUC | range-null):** `early_to_late` on **B step** = raw **0.924** (null 0.499) — the only feature clearing 0.75 *and* beating its range-null; confirmed real (Spearman vs range −0.019; moves opposite to SNR during the step). Magnitude small (~6 %).
- **E bursts:** best raw 0.796 (`fp_snr`, confounded null 0.675); `fp_to_peak` 0.764 confounded (Spearman vs SNR 0.901). **No feature discriminates E beyond its range/SNR confound.**
- **FP-SNR σ-scaling FAILED:** event anchors B/E/H FP-SNR = 76/54/77 (not low) → multiplier 1.0 everywhere. Best CIR-feature |ρ| vs error ≈ **0.10**.
- **NLOS ratio (`nlos_ratio_db`):** moves the wrong way for E (quiet 7.80 → image 6.47), AUC 0.207 — non-discriminating.

### Verdict
**PENDING (weak positive, not deployable).** Compact-format-for-morphology = DEAD END (no taps). Windowed on-device extraction = feasible INFRASTRUCTURE (not built). `early_to_late` genuinely beats FP-SNR on the B step — but the B step is a ~272 mm *geometry/pose change* where down-weighting is the wrong response (re-solve instead), and the E multipath, the case where down-weighting *is* correct, is caught by no shape feature. **The event we most want to catch, CIR catches least.** Treat RF-σ as a weak prior, never a gate.

### Quantitative improvement
None realized in the solver. Best genuine discriminator `early_to_late`/B step AUC 0.924.

### Artifacts
- `analysis/cir_compact_audit/REPORT.md`
- `analysis/cir_event_discrimination/{REPORT.md, summary.json, features.npz}`, `analysis/cir_event_discrimination.py`
- Source: `logs/geiger_overnight_static_20260711/scan.log`

---

## 5. Listener Architecture

### What was done
Built a zero-human-input calibration pipeline (`logs/listener_calibration/`) that places every node in the anchor frame: each listener is switched one at a time into broadcast Alt-SS-TWR **MODE_TAG**, collects ~30 s of per-anchor ranges, and is multilaterated against the freshest v4-io anchor solve. Listeners are held radio-off during AutoPos ("antenna-coupling safe").

### Key result
- **7-listener fleet** (all solved, 8/8 anchors, status OK on `system_calibration_20260710_233443`):

| name | host label | on-air | solve RMS (mm) |
|---|---|---|---|
| LB | 0xC001 | 0xB1C1 | 60.1 |
| LE | 0xC002 | 0xB1C2 | 123.4 |
| LF | 0xC003 | 0xB1C3 | 349.0 |
| LA | 0xC004 | 0xB1C4 | 239.3 |
| LCCF4 | 0xC005 | 0xB1C5 | 88.1 |
| L9336 | 0xC006 | 0xB1C6 | 145.6 |
| L955A | 0xC007 | 0xB1C7 | 204.6 |

- **Non-coplanar / 3-height geometry:** metadata records "listeners relocated to 3 heights (ceiling/mid/floor)"; as-solved z spans ~2.2 m (LF −168.7 … LB −2389.6 mm). *Literal metric heights (2.3/1.2/0.3 m) are not in the artifacts — only the qualitative three-tier label + the ~2.2 m spread.*
- **Co-located abandonment:** qualitative only — "co-located listeners removed from anchor proximity"; original design was RX-only beside Anchor E with "no direct proxy value for anchors far in azimuth." *No quantified coupling dB in the artifacts.*
- **CIR role:** listeners are passive CIR receivers for multistatic radar/imaging (`APP_LISTENER_CIR_CAPTURE_ENABLE`, compile-time). At the 07-10 cal only LB emitted CIR (1 CIR / 7 scalar); for the overnight run all 7 were rebuilt CIR-enabled (`cir1_*` builds). Full dump = 1016 taps, 1-in-10 polls, feeding the 21-channel overnight capture. Known issue: LCCF4 ~24 % CIR parse rate (UART bandwidth).

### Verdict
**INFRASTRUCTURE** — calibration pipeline + 7-node CIR fleet built and working. Co-located sub-approach is an abandoned **DEAD END**, replaced by the spatially-distributed 3-height layout.

### Artifacts
- `logs/listener_calibration/{calibrate_listener_positions.py, full_system_calibration.py}`
- `logs/system_calibration_20260710_233443/{listener_positions.json, system_config.json}`
- `logs/overnight_radar_20260711/metadata.json`
- `SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-{cir1,tagmode}_*_20260711.source`

---

## 6. Geiger Scanner (Listener-Scanner)

### What was done
A DWM1001C ("the Geiger", on-air 0xB1C0) runs a new **MODE_SCAN**: per cycle a ranging pass (8 anchors reply, SS-TWR mm each) then a CIR pass (round-robin anchor replies rank-0 so its full accumulator is readable), ~5 Hz over USB-CDC. It is a mobile self-positioning channel probe inside the fixed 8-anchor cage. Three experiments: a 120 s proxy-gate scan, a 10.6 h static overnight, and a person-vs-empty comparison.

### Key result
- **Proxy gate (CIR-predicts-error): UNDERPOWERED.** Best partial Spearman |ρ| = **−0.103** (`early_ratio`, p=0.046), best AUC = **0.6225**, n=385 — between the GO bar (|ρ|≥0.30 & p<0.01 or AUC≥0.70) and the NO-GO bar.
- **Gauge (the real correctable diagnosis):** common-mode intercept **−100.5 mm** (Geiger reads short); within-anchor slope **+3.65 % ± 0.55 %** (consistent with APS011 ~2.77 %). Interior median |LOO e| 151 mm vs 257 mm outside the cage.
- **Overnight static (10.61 h, 160,678 LSCAN @ 4.21 Hz, 0 reconnects):** per-anchor bias **IS stable** for clean links — **5/8 anchors STABLE (A,C,D,F,G), σ₁ ≈ 23–29 mm**. The drift budget is **discrete events, not thermal**: **B = STEP** (held ~−300 mm for 2.4 h, max |Δ| **353 mm**, σ₁ 161); **E = BURSTY** (53 excursions, LOS↔+290 mm image, max |Δ| **298.5 mm**, σ₁ 190); **H = NOISY** (σ₁ 67). Shared common-mode is tiny/slow: +11.75 mm morning rise, **1.25 mm/hr** (exponential warm-up rejected). CFO does NOT track range common-mode (ρ=−0.02) → not a bias proxy.
- **Person-effect:** person-induced common-mode range shift **−76 mm**; most-affected anchors G (ΔLOO −83.6) and C (−83.4), side-on to the seated body. Detectability below the 0.7 bar (best CIR AUC 0.624 @ E vs walk-difference floor 0.590).

### Verdict (per sub-item)
- Proxy gate: **DEAD END / PENDING** (UNDERPOWERED; needs a controlled tripod occlusion ladder).
- Gauge calibration: good **diagnosis**, but firmware fix rolled back → net **DEAD END on hardware**; move to analysis-side offsets.
- Overnight stability: **INFRASTRUCTURE / improved understanding** — bias stable to tens of mm; "drift" = per-anchor events, not thermal. Gate anchor E, watch B/H, use a robust median solver.
- Person/occlusion detection: **DEAD END** as a passive detector at this SNR; PENDING a fixed-geometry ladder.

### Quantitative improvement
Diagnosed (not banked): common-mode −100 mm, slope +3.65 %. The **+100 mm** firmware antenna-delay offset was expected to drive common-mode −100 → ~0, but on hardware over-corrected to **+354 mm** (slope → −7.01 %, ~3× overshoot) → rolled back, no net range gain retained.

### Notes / provenance — the ±100 mm offset story
Added because the P2 gauge fit measured the Geiger ~100 mm short (`GEIGER_ANTENNA_DELAY_OFFSET_MM=100` in `scan_calc_range_mm()`, Geiger-only, applied after APS011). Removed because it and `getrangebias` **double-corrected the same DW1000 bias** (+454 mm total) and the ~3 dBi antenna made `getrangebias` ~3× too large. Overnight capture ran on the rolled-back firmware (no getrangebias, no +100 mm). Person-effect absolute gauge numbers are APS011-contaminated — only person−clean deltas are clean; `agc` field is an always-0 firmware bug.

### Artifacts
- `logs/geiger_scan_20260711_161258_8anchor/analysis/{GEIGER_PROXY_GATE_REPORT.md, gate_verdict.json, pg_pipeline.py, HANDOFF_FOR_ANALYSIS.md}`
- `analysis/geiger_static_drift_20260711/{REPORT.md, report.json}`
- `analysis/person_effect_analysis/{REPORT.md, report.json}`
- `logs/geiger_overnight_static_20260711/` (metadata.json, start/stop_time.txt, scan.log ~1.3 GB)

---

## 7. qf (quality_percent) Audit

### What was done
Traced `quality_percent` from the solver weight back to its single firmware computation site, enumerated every compute/consume point, validated against 14,000 logged samples, then designed an RF-based replacement.

### Key result
- **What it measures:** a ranging **success-rate** — `src/uwb_range_tracker.c:58` `qf = 100 × recent_success / (success + failure)` over the last ~32 attempts. "Success" only checks `raw_mm != 0` + a delta gate (`ss_twr_init.c:1164`) — **zero RF registers**. Consumed as the solver weight `weight = 0.25 + qf/100` (`uwb_tag_loc.c:412`).
- **Why useless for NLOS:** NLOS bias moves the *range value*, not the success flag. Empirical (n=14,000): mean **99.98**, min **94**, fraction ==100 = **0.995**; obstructed anchor **B = 100 on all 3,500 samples**. Net weight ≈ **1.25 uniform** → effectively a no-op.
- **Proposed replacement:** the discriminating data (FP_AMPL1/2/3, cir_pwr, RXPACC, STD_NOISE) already reaches the tag in the RESP_DIAG trailer but is discarded. RF σ-multiplier ≥ 1.0 (`clamp(rf_snr_ref/max(FP_SNR,1),1,cap)`); the C hook is **built and verified** (synthetic FP-SNR=2 on a +400 mm anchor → σ ×5, pull 17.1 → 0.7 mm). **Caveat:** FP-SNR does not catch *this* deployment's events (B/E/H are the highest SNR); a timing/shape feature is needed, and best CIR |ρ| ≈ 0.10 → weak prior only.

### Verdict
**DEAD END** as an NLOS discriminator (wrong physical quantity — link completion, not signal integrity). RF replacement is partial **INFRASTRUCTURE**: σ-multiplier hook built + verified but a no-op until (a) a per-anchor RF scalar is on the capture path and (b) a feature that correlates with the failure mode exists. Keep qf for dead-link detection (qf<50) only.

### Artifacts
- `analysis/qf_trace/REPORT.md`, `analysis/qf_rf_metric_design.md`
- Firmware: `src/uwb_range_tracker.c:58`, `src/uwb_tag_loc.c:412`, `UWB_listener/src/main.c:469`
- Data: `logs/system_calibration_20260710_233443/autopos/pairs_all.csv`

---

## 8. Firmware State (as of the 2026-07-12 freeze)

Source: `firmware_freeze_20260712/manifest.json` + `.source` provenance. State label: **post-APS011-rollback (`rollback-20260711`), pre-V5-deploy**. Solver deployed = **V4-IO**; V5 designed, not deployed.

| device class | deployed image | provenance / build | notes |
|---|---|---|---|
| **Anchors A–H** | UNCHANGED by rollback | last repo build `build-anchor-unified-ota-fixeda19-g1200-r1000-20260701`; r800 in 06-25 freeze | **UNCONFIRMED from files** — needs user confirmation before any anchor flash; do NOT reflash unless proven different from Erlangen baseline |
| **Master_Tag (b120 m1)** | `master_current_merged_domains.hex` | `build-master-control-rollback-20260711` (`build_master_control_b120_m1.sh`, 2026-07-11T20:23Z) | J-Link SNR 1050070698; master-reset after flash |
| **Wand tags (BS9336/BS955A/BSCCF4)** | `tag_current` (signed.bin / dfu.zip / merged.hex) | `build-tag-rollback-20260711` (`build_tag_ble_unified.sh 0 10`, 2026-07-11T20:20Z) | pre-APS011; OTA via Master_Tag or J-Link per device |
| **Geiger / UWB_listener (MODE_SCAN)** | `geiger_current.hex` | `build-uwb-listener-rollback/UWB_listener` | J-Link SNR 760185886; getrangebias OFF, offset 0 |
| **Listeners ×7 (CIR fleet)** | `cir1_*_20260711` (CIR-enabled) | `build_uwb_listener_poll_diag.sh` | LB was already CIR-on; passive multistatic capture |

**Rolled back / discarded:** naive-APS011 builds `build-tag-aps011-a7win-20260711`, `build-master-control-b120-m1-master-tag-lfrc-aps011-20260711`, and Geiger `+100 mm` offset — all superseded by the `rollback-20260711` images. Freeze SHA256s in `firmware_freeze_20260712/SHA256SUMS.txt`; git deliberately **not** stashed (flashing doesn't touch tracked files; OTA-generated manifests snapshotted under `generated_current/`).

---

## Extra — Erlangen vs Home comparison

**What / result:** Pure-offline re-solve of the Vicon-validated Erlangen 2026-05-28 captures vs the home rig. Layout pair-fit RMS **Erlangen 48.04 mm vs Home 108.91 mm** (Erlangen ~2× tighter, bigger cleaner room). Rigid-wand caliper: Home **1/3**, Erlangen full-8-anchor capture **0/3**; per-tag solve residual ~80–160 mm in *both* rooms. **Verdict: DEAD END** for "cleaner room / validated firmware fixes the caliper" — the wand-triangle failure is intrinsic to tag ranging + multilateration, present even in the Vicon-validated setup; needs tag-side delay calibration or a metric constraint. (Caveat: BS9336's ranges collapsed at Erlangen to 2–3 anchors, so the conclusion rests on CCF4–955A + per-tag residuals, not a full 3/3 Erlangen caliper.)
**Artifacts:** `analysis/erlangen_vs_home/{REPORT.md, comparison.json, erlangen_layout.json, compare_offline.py}`

---

## Net Improvement Summary

| metric | start of campaign | best achieved | method |
|---|---|---|---|
| Geiger LOO median (mm) | **158.7** (V4-IO, no delay) | **146.2** (−12.5) | V5 scale-lock layout + per-anchor delays |
| — same, range-gauge LOO | 157.5 (raw baseline) | 146.3 in-sample / 150.7 CV | per-anchor delay offsets (analysis-side, not flashed) |
| Layout inter-anchor pair-RMS (mm) | **105.76** (V4-IO) | **100.9** (−4.9) deployable / 94.65 unlocked | V5 common-mode reparameterization |
| Caliper pass (of 3) | **1/3** | **1/3** (no change) | none — persists across V4-IO, V5, U5, d_tag, Erlangen |
| Per-wand solve RMS (mm) | 132.7 / 118.1 / 123.1 (V4-io+T4) | 131.1 / 115.0 / 121.4 (V5+U5) | V5+U5 — marginal (~1–3 mm) |
| Overnight anchor step/burst (mm) | B **353** / E **298.5** (uncontrolled) | still present (not weight-suppressible) | robust median solver + gate anchor E; CIR cannot flag them |
| Overnight thermal common-mode (mm) | — | **+11.75** over 10.6 h (1.25 mm/hr) | characterized as stable, not a warm-up transient |
| APS011 range correction | 158 mm LOO | **worse (251 mm)** → rolled back | DEAD END on hardware |

**Bottom line:** the campaign's positive deliverables are (1) a deployable V5 layout reparameterization (small pair-RMS + LOO gains, honest scale diagnostics), (2) the 7-listener CIR fleet + zero-input calibration pipeline (infrastructure), and (3) a large body of **closed hypotheses** — APS011/RSL-indexed correction, FP-SNR/qf weighting, per-tag `d_tag`, and CIR-morphology gating are each proven not to be the lever. The caliper remains **1/3** and is now well-localized to per-wand ranging + z-DOP geometry, not layout, not tag delay, not the room.

---

## Open Items (ranked by expected impact)

1. **Metric-scale constraint on the wand solve** — highest impact. The caliper (1/3) and V5 scale unidentifiability (c saturates ±150 mm; layout moves 534 mm unlocked) are the *same* degeneracy (delay↔scale ρ=−0.977). A known-distance jig / corner reflector / tripod ladder feeding the layout+wand solve is the only lever shown to be missing. Would also unlock the per-anchor ANTD gain (~7–11 mm) currently blocked by the ±60 mm bound.
2. **Per-tag position/z error on the wand triangle** — CCF4–955A solves ~400 mm too close; `d_tag` (43 mm spread) is *not* it. Root cause is a per-tag position error in the least-constrained z axis. Investigate wand-tag antenna phase-center offsets and z-DOP directly; consider caliper-as-constraint in the solve.
3. **Per-anchor delay offsets, analysis-side** — banked ~7–11 mm (LOO 157.5 → 146.3) is realizable now without a firmware flash; apply in the host solver rather than register ANTD (which is blocked by the scale degeneracy).
4. **A CIR feature that catches E-type multipath bias** — the one event where down-weighting is correct is caught by no current feature (FP-SNR, early_to_late, nlos_ratio all fail on E). A controlled tripod occlusion ladder (fixed geometry, removes the walk-difference confound of 0.590) is the pre-registered next step to decide the proxy gate.
5. **On-device windowed CIR extraction** — feasible (~84 µs / 20-tap read ≈ 12 % of a slot) and would put `fp_to_peak` / `rms_delay_spread` / `early_to_late` on the tag's telemetry path, unblocking the verified RF-σ hook. Only worth building after item 4 identifies a feature that actually correlates.
6. **Confirm anchor firmware** — the freeze cannot verify what is on anchors A–H from files. Confirm against the Erlangen baseline before any anchor reflash.
7. **Deploy V5 scale-lock** — low-risk, V4-IO-compatible, banks the pair-RMS/LOO gains; gated only on the user's call since it does not fix the caliper.
