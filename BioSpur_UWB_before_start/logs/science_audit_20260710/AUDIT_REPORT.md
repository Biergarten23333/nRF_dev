# BioSpur UWB Repository — Independent Science Audit

**Date**: 2026-07-10  
**Repo**: /mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start  
**Auditor**: Claude Science (automated, read-only)  
**Scope**: 7 investigation tracks covering firmware registers, CIR pipeline, AutoPos solver, cold-start thermal, numerical consistency, claims verification, and gaps/missing experiments.

---

## 1. Executive Summary

### What's solid

The core measurement infrastructure is well-engineered. All 10 headline quantitative claims (static 72.7mm median, 171.5mm P95, 109.8mm RMSE; dynamic 101.5mm median; delay-coupling ablation; Sim(3) scale 4.36%; CIR pipeline metrics) **trace to reproducible source files** and are internally consistent across the repo. The CIR pipeline v2 corrections (FP_INDEX alignment, time-based holdout, PSF gating) are verified correct. The tap-timing convention (1.0016 ns/tap) is used consistently across all 15 analysis scripts. The delay-geometry FIM computation (ρ = −0.977) is mathematically sound. The firmware register configuration, while minimal, results in zero power differential between frame types.

### What's shaky

1. **Cold-start thermal stability**: The "30-min pre-warm" claim is **refuted** — no listener reaches CFO stability within 90 minutes. The experiment is correctly executed but the conclusion is weaker than stated.
2. **The 44.49mm V5 result**: Post-hoc winner of a 107K-row parameter sweep (888 estimator-loss-geometry combos). LOO-CV and bootstrap CIs are provided as safeguards, but this is not a pre-registered analysis.
3. **Overnight layout delays**: Delays in layout_clean6.json are stale copies from a different solver run — delay+geometry are inconsistent.
4. **v4-io vs common-mode comparison**: Claimed to differ only in delay parameterization, but actually differs in gauge convention, regularization, and bounds too.
5. **"clean-6 solves to 39mm"**: This is self-consistency (internal range residual), not rigid RMSE vs Vicon — fundamentally different metrics that must not be conflated.

### What's wrong

1. **💀 APS011 range-bias correction never called**: The DW1000 range-bias tables exist in the driver (deca_range_tables.c) but dwt_getrangebias() has zero call sites. This is a known systematic error source at short ranges.
2. **💀 Antenna delays hardcoded identically for all devices**: All 8 anchors + 3 tags use 16436U (0x4034) for both TX and RX. No per-device calibration despite the solver fitting per-anchor delays that span −27mm to +60mm — the solver is compensating for uncalibrated hardware.

### Verdict counts

| Verdict | Count |
|---------|-------|
| ✅ VERIFIED | 15 |
| ⚠️ QUALIFIED | 11 |
| ❌ REFUTED | 2 |
| 💀 BUG | 2 |
| **Total findings** | **30** |

---

## 2. Per-Track Findings

### Track 1 — Firmware Register Audit

**1a-1** ✅ VERIFIED  
TX_POWER register (0x1E) is NEVER written by application code. The only write path is dwt_configuretxrf() (deca_device.c:620), which the application never calls. TX_POWER retains POR default (0x0E082848 for Ch5 PRF64).  
*File*: `drivers/dw1000/src/deca_device.c:620 (driver only), no app call site exists`  

**1a-2** ✅ VERIFIED  
TC_PGDELAY register (0x2B:0x0B) is NEVER written by application code. Three write sites exist in the driver (deca_device.c:617, 3687, 3895) but all are inside driver-internal functions. The application never calls dwt_configuretxrf(). TC_PGDELAY retains POR default 0xC0 for Ch5.  
*File*: `drivers/dw1000/src/deca_device.c:617 (driver only)`  

**1a-3** ✅ VERIFIED  
SYS_CFG DIS_STXP bit (bit 18 of 0x04) is NEVER explicitly set or cleared by application code. dwt_setsmarttxpower() exists in the driver (deca_device.c:2228) but is never called. dwt_configure() writes SYS_CFG but only modifies RXM110K and PHR_MODE bits — DIS_STXP is not touched. POR default DIS_STXP=0 means Smart TX Power remains ENABLED.  
*File*: `drivers/dw1000/src/deca_device.c:2228 (driver only, never called)`  

**1a-4** ✅ VERIFIED  
TX_ANTD and LDE_RXANTD are explicitly written by all three application roles (init, resp, anchor_init) in their configure_radio() functions. All use the same hardcoded value 16436U (0x4034). TX and RX delays are forced equal — no per-device or per-role differentiation.  
*File*: `ss_twr_init.c:3221-3222, ss_twr_resp.c:913-914, ss_twr_anchor_init.c:135-136`  

**1b-1** ⚠️ QUALIFIED  
TX_POWER POR default 0x0E082848 is the DW1000 User Manual recommended value for Ch5 PRF64 in smart power mode. This is acceptable for the application. However, the power level was never explicitly verified or tuned for DWM1001C module — the 0x0E082848 value may not be optimal for the specific module's PA characteristics.  
*File*: `drivers/dw1000/include/deca_regs.h:534 (TX_POWER_MAN_DEFAULT = 0x0E080222 is the MANUAL default, NOT the smart default)`  

**1b-2** ✅ VERIFIED  
TC_PGDELAY POR default for Ch5 is 0xC0, which matches the DW1000 User Manual recommendation (deca_regs.h:997 TC_PGDELAY_CH5 = 0xC0). Acceptable for this application.  
*File*: `drivers/dw1000/include/deca_regs.h:997`  

**1b-3** ✅ VERIFIED  
Smart TX Power (DIS_STXP=0) is appropriate. All frames are in the same duration bucket (<0.25ms at 6.8 Mbps with 128 preamble), so smart power introduces zero differential between poll and response. No ranging asymmetry from this source.  
*File*: `uwb_ss_twr_shared.h:74 (17B poll), uwb_ss_twr_shared.h:56 (36B resp)`  

**1c-1** ✅ VERIFIED  
Init sequence: uwb_bringup.c → dwt_initialise(DWT_LOADUCODE) → per-role configure_radio() → dwt_configure(). Driver owns: LDO tune, XTAL trim, LDE microcode, channel/PLL/RF config, AGC, baseband tuning. Application owns: antenna delays (hardcoded 16436U), frame filtering, LED enable. NOBODY owns: TX_POWER, TC_PGDELAY, DIS_STXP — all left at POR defaults.  
*File*: `uwb_bringup.c:98, ss_twr_init.c:3211-3226`  

**1d-1** ✅ VERIFIED  
Smart TX Power causes ZERO power differential between tag poll (17B, 174.6µs) and anchor response V3 (36B, 197.0µs). Both fall into BOOSTP250 bucket (< 0.25ms). All four frame types in the system are in this same bucket. Power asymmetry from Smart TX Power is not a ranging error source.  
*File*: `uwb_ss_twr_shared.h:74,56 (frame lengths), DW1000 User Manual Table 18 (power thresholds)`  

**1e-1** 💀 BUG  
Antenna delays are hardcoded to 16436U for ALL devices — tags and anchors, TX and RX. No per-device calibration. OTP factory-programmed antenna delays (address 0x01C) are read for diagnostic printing only (tag_app.c:271-273, gated by APP_TAG_OTP_DIAG) but NEVER used for configuration. The dwt_initialise() call uses DWT_LOADUCODE (0x01) with no OTP antenna delay flags. This means every DWM1001C module uses the same delay regardless of its individual RF path characteristics. Expected per-device variation: ±1-3 ns (±15-45 cm range error).  
*File*: `ss_twr_init.c:37-38, ss_twr_resp.c:47-48, ss_twr_anchor_init.c:18-19, tag_app.c:271-273`  

**1e-2** ⚠️ QUALIFIED  
TX and RX antenna delays are forced equal (both 16436U). In principle, TX and RX paths through the DWM1001C module antenna have different electrical lengths. For SS-TWR the relevant quantity is (TX_delay_A + RX_delay_B) for the initiator leg and (RX_delay_B + TX_delay_B) for the responder leg. Forcing TX=RX means the sum is always 2*16436 per device regardless of the actual TX/RX asymmetry. This is standard practice (Decawave examples do the same) and is acceptable when the total round-trip delay is calibrated as a single combined value, which 16436U represents.  
*File*: `ss_twr_init.c:37-38 (TX=RX=16436U)`  

**1f-1** 💀 BUG  
dwt_getrangebias() (APS011 range-bias correction) exists in the driver (deca_range_tables.c:636) with full correction tables for all channels and PRFs, but is NEVER called by any application code. Range-bias correction compensates for the received-signal-level-dependent ranging error in the DW1000 LDE. At short ranges (<5m) this bias can be 10-20cm. Not applying it means all ranges have an uncorrected RSL-dependent bias.  
*File*: `drivers/dw1000/src/deca_range_tables.c:636 (defined), zero call sites in SS-TWR/ or apps/`  

**Smart TX Power Analysis**  
DIS_STXP state: POR default = 0 → Smart TX Power ENABLED. Application never calls dwt_setsmarttxpower(). No code writes the DIS_STXP bit.  
Tag poll size: 17B, Anchor response size: 36B  
Power differential: 0.0 dB — With Smart TX Power enabled (DIS_STXP=0, POR default), the DW1000 selects TX power based on frame duration thresholds. At 6.8 Mbps with 128-symbol preamble, ALL BioSpur frames (17B poll, 36B V3 response, 20B V1 response, 13B anchor-init poll) have total durations between 170-197 µs, which ALL fall in the BOOSTP250 bucket (< 0.25 ms). Therefore Smart TX Power introduces ZERO power differential between poll and response frames. The power asymmetry concern is MOOT for this configuration. NOTE: The POR default TX_POWER value 0x0E082848 (Ch5 PRF64) has BOOSTP250=0x08 for all these frames. If TX power were configured manually via dwt_configuretxrf(), the DW1000 User Manual Table 18 recommends 0x0E082848 for Ch5 PRF64 smart mode, or 0x1F1F1F1F for manual mode. Neither is called.

### Track 2 — CIR Pipeline Deep Verification

**2a: FP_INDEX parsing preserves 16-bit precision** ✅ VERIFIED  
step0_parse.py parses FP_INDEX via int(p[12]) preserving the full 16-bit 10.6 fixed-point value. Downstream step2_stability_v2.py align_norm correctly decomposes: fp_integer = fp_int >> 6, fp_frac = (fp_int & 0x3F) / 64.0. Verified on all 5 clean listeners: fractional part spans [0,63], all 64 unique values present in every listener. Integer part ranges from ~327-863 (expected for DW1000 ch5). No truncation detected.  
*Files*: overnight_radar_20260710/analysis/step0_parse.py:118, overnight_radar_20260710/analysis/step2_stability_v2.py:align_norm  

**2b: v2 template SNR improvement over v1** ✅ VERIFIED  
dp_snr = |A[REF_TAP]| / median(sigma) — amplitude-domain SNR, not power-domain. Independently reproduced from saved template arrays (A.npy, sigma.npy) for all 15 channels. Every value matches the pipeline reports to one decimal place. Median dp_snr: v1=23.4, v2=82.1. v2/v1 amplitude ratio ~3.5× per channel. The '~23× vs ~82×' claim refers to the absolute dp_snr values, not to a ratio.  
*Files*: overnight_radar_20260710/analysis/step1_template.py:118, overnight_radar_20260710/analysis/step1_template_v2.py:290, overnight_radar_20260710/analysis/templates_v1/step1_report.csv  

**2c: Holdout uses time-based split, not frame-count** ✅ VERIFIED  
step2_stability_v2.py freezes the template on the first 30 minutes by device uptime: rel = now - t0; frozen_mask = rel < FREEZE_MS (FREEZE_MS = 30*60*1000). 'now' is joined from LPD now_ms (device uptime in milliseconds). Holdout windows are wbin >= 1 (i.e., >= 30 min). Frame rate is non-uniform (dt std=3029ms, max gap=81401ms), confirming that frame-count would be wrong. v1 BUG confirmed: A_total = median(ALL frames) with no holdout separation.  
*Files*: overnight_radar_20260710/analysis/step2_stability_v2.py:process()  

**2d: PSF volume and -6dB dimensions** ⚠️ QUALIFIED  
PSF peak = 10.257 (non-zero; v1 bug had 0/15 channels contributing). All 15/15 channels contribute. edge_clipped=False. Reported -6dB extent [3300, 5300, 5400] mm matches claimed 3.3×5.3×5.4 m. All per-channel excess taps [823.2, 835.7] clear all mainlobe ends [806, 814]. PSF blob well within local grid (27-45% of grid span). QUALIFICATION: stats['psf_measured_on'] says '±2400mm @75mm' but code uses ±6000mm @100mm — stale docstring. Non-functional (does not affect computed values).  
*Files*: overnight_radar_20260710/analysis/step4_backprojection_v2.py, overnight_radar_20260710/analysis/step4_v2/step4_v2_stats.json  
*💀 BUG (cosmetic)*: stats psf_measured_on string says '±2400mm @75mm' but code line ~148 uses ±6000mm @100mm  

**2e: Raw CIR sanity check — decode one hex frame** ✅ VERIFIED  
Decoded one complete CIR frame from LB.log (accepted_polls=118530, tag 0x4). 85 LCIRD chunks reassembled to 4064 bytes → 2032 int16 → 1016 complex64 taps. Waveform is textbook UWB: flat noise floor (mean=84, std=44) for taps 0-700, sharp first-path onset at FP_INDEX integer tap 750 (|CIR|=3111), actual peak at tap 752 (|CIR|=5931 — 2 taps of multipath constructive interference), multipath decay through taps 760-850, return to noise floor. First-path/noise ratio = 37× (3111/84); peak/noise ratio = 71× (5931/84).  
*Files*: overnight_radar_20260710/raw/LB.log:line4, overnight_radar_20260710/analysis/step0_parse.py  

**2f: 1.0016 ns/tap convention across all scripts** ✅ VERIFIED  
All 15 .py files in analysis/ audited. Two equivalent forms: 1.0/0.9984 = 1.001602564... ns (7 files) and 1.0016 (2 files). Difference = 2.56e-6 ns = 0.055 mm over 72 taps — negligible. NO wrong values found: 2.0032 ns/tap NOT present; 1/499.2e6 NOT used as tap period; 499.2 appears ONLY in comments explaining 998.4 = 2×499.2 MHz. Speed of light: mostly 299.792458 mm/ns; step4/step4_v2 use 299.792 (1.5 ppm, 0.033 mm at max range — negligible).  
*Files*: step1_template.py:41, step3_cleanest_channel_plot.py:14, step3_multipath.py:15  

### Track 3 — AutoPos Solver Verification

**3a: Solver Parameters** ⚠️ QUALIFIED — two distinct solvers with different architectures serve different purposes

Two distinct solvers:
- **Tag positioning**: IRLS with Huber loss (δ=30mm), C core 3×3 Gaussian elimination. Convergence: 20 IRLS iterations, weight floor 0.01.
- **Layout solver**: scipy `least_squares` with Huber loss (f_scale=2.0 on normalized residuals → 30mm effective), Trust Region Reflective method.
- Per-anchor delays bounded ±60mm in v4-io, d_A=0 gauge, L2 regularization d_i/20.

**3b: "clean-6 solves to 39mm"** ⚠️ QUALIFIED — the 39mm is SELF-CONSISTENCY (internal pairwise range residual RMS), NOT rigid-alignment RMSE vs Vicon

The 39mm is **self-consistency RMSE** (RMS of ||X_i − X_j|| − M_ij over 15 anchor pairs), **NOT** rigid-alignment RMSE vs Vicon. Self-consistency measures internal pairwise range residual only — it does NOT measure metric accuracy.

**3c: v4-io vs common-mode** ❌ REFUTED — they differ in MORE than just delay parameterization

Differences beyond delay parameterization:
- **delay_parameterization**: {'v4_io': 'd_A=0 gauge, 7 free delays bounded ±60mm, L2 regularization d_i/20', 'commonmode': 'd_i = c + e_i, no gauge, c bounded ±150mm, e_i bounded ±100mm (or pure c when use_per_anchor_ei=False)', 
- **residual_normalization**: {'v4_io': 'range residual / 15.0 (hardcoded)', 'commonmode': 'range residual / residual_sigma_mm (default 15.0, but configurable)', 'assessment': 'SAME in default, but commonmode has a configurable si
- **delay_regularization**: {'v4_io': 'd_i / 20.0 for i=1..7', 'commonmode_pure': 'none (when use_per_anchor_ei=False, no e_i regularization)', 'commonmode_with_ei': 'e_i / e_reg_scale_mm + mean(e)/1.0', 'assessment': 'DIFFERENT
- **bounds**: {'v4_io': 'positions unbounded, delays ±60mm', 'commonmode': 'positions unbounded, c ±150mm, e_i ±100mm', 'assessment': 'DIFFERENT — much wider bounds for common-mode'}
- **loss_and_f_scale**: {'v4_io': 'huber, f_scale=2.0 (applied to normalized residuals → 30mm effective threshold)', 'commonmode': 'huber, f_scale = f_scale_mm / residual_sigma_mm = 30/15 = 2.0 (same effective threshold)', '
- **physical_priors**: {'v4_io': 'soft_two_layer_v1 (same residuals)', 'commonmode': 'soft_two_layer_v1 (same residuals)', 'assessment': 'SAME'}
- **data_subsets**: {'note': 'Both use the same fused pair distances (v3 fusion)', 'assessment': 'SAME'}
- **initial_conditions**: {'v4_io': 'MDS-initialized positions, zeros for delays', 'commonmode': 'MDS-initialized positions (same), c_init=0', 'assessment': 'SAME starting point'}
- **max_nfev**: {'v4_io': 5000, 'commonmode': 5000, 'assessment': 'SAME'}

**3d: Layout comparison** ⚠️ QUALIFIED — overnight layout is in a different solver framework (no-delay MDS), making direct mm comparison misleading

### Track 4 — Cold-Start Thermal Results

**4a_initial_conditions** ⚠️ QUALIFIED  
Experiment protocol is sound but first ~3.5 min of cold-start transient is missing from all listeners

**4b_cfo_excursion_and_stability** ❌ REFUTED  
NO listener reaches CFO stability within 90 min. The analyze_coldstart.py report correctly states 'NOT REACHED' for all 6, but this refutes any prior '30-min pre-warm' claim. Even a 4x-relaxed drift threshold (0.20 ppm/min) is never sustained.

**4c_cfo_to_temperature_relationship** ⚠️ QUALIFIED  
No independent temperature measurement exists. CFO is used as a temperature proxy but the proxy is unvalidated: no TC_SARL reads, no nRF52 TEMP reads, no IR camera images saved, and cross-listener correlation is negligible (median Spearman rho = +0.17), proving per-board dynamics dominate over shared ambient temperature.

### Track 5 — Numerical Consistency Cross-Check

**5a_layout_audit** ⚠️ QUALIFIED

Six layout files loaded across 3 physical deployments. **💀 BUG**: overnight_clean6 delays are BIT-IDENTICAL copies from erlangen_v4io_check but coordinates are independently solved. Delay+geometry are inconsistent — stale delays from a different solver run.

**⚠️ WARNING**: diagnostic_full8 has 4/8 anchors saturated at 60mm delay bound — solver is heavily constrained, true delays unknown.

**⚠️ WARNING**: 3 completely different physical geometries all labeled 'v4-io' — pipeline lacks room/session tracking metadata.

**5b_delay_crosscheck** 💀 BUG + ⚠️ QUALIFIED

Solver delay range: [-26.87, 60.0]mm. Delay bound saturation varies from 0/8 (cleanest) to 4/8 (diagnostic). APS011 range bias: exists in driver, NEVER called.

**5c_63mm_coincidence** ✅ VERIFIED — different quantities, numerical coincidence

Context 1: Common-mode rigid anchor RMSE vs Vicon = 62.99mm (empirical 3D RMSE after Sim(3) alignment)
Context 2: Vertical CRLB floor for 4+4 anchor self-calibration = 62.74mm (theoretical vertical-only CRLB (σ_inter=30mm, 5×5m footprint))
System operates near information-theoretic floor. Vertical error dominates 3D budget (consistent with VDOP/HDOP ≈ 1.6-2.5x). No cross-contamination in code.

**5d_scale_consistency** ✅ VERIFIED — no cross-contamination

v4-io scale: 0.9582672713308588 — Vicon = 0.9583 × AutoPos → AutoPos v4-io is ~4.4% LARGER than Vicon truth (ranges are stretched by uncorrected common-mode delay)  
Common-mode scale: 1.0097822800764376 — Vicon = 1.0098 × AutoPos → After common-mode correction, AutoPos is ~1.0% SMALLER than Vicon truth  
No cross-contamination found; all scripts compute scale fresh at runtime.

**5e_rho_0977_fim** ⚠️ QUALIFIED — computation correct, one caveat

delay-iso-scale: ρ=-0.977, variance inflation=22.07×  
delay-horizontal: ρ=-0.974  
delay-vertical: ρ=-0.814  
Condition number: 492.4. Gauge-free modes: 26.  
Caveats: Linearization at Vicon truth geometry, not solver estimate — correct for CRLB but solver's coupling structure may differ slightly at its operating point; Softest eigenvector has very low overlap (|cos|<0.01) with all interpretable directions — the near-degeneracy is a complex multi-parameter mode, not purely 'delay vs scale'

### Track 6 — Claims vs Evidence

**6a: Spot-check of 10 headline claims**

| # | Claim | Verdict | Source |
|---|-------|---------|--------|
| 1 | Static median 72.7mm | ✅ VERIFIED | `official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json` |
| 2 | Static P95 171.5mm | ✅ VERIFIED | `official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json` |
| 3 | Static RMSE 109.8mm | ✅ VERIFIED | `official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json` |
| 4 | Sim(3) scale 0.9583 | ✅ VERIFIED | `official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json` |
| 5 | Common-mode c≈112mm, rigid 105.4→63.0mm | ✅ VERIFIED | `official_extra_analysis/FULL/audit_phase1c/tables/audit_phase1c_summary.json` |
| 6 | Delay-coupling 311.3/252.2/77.7/108.9mm RMSE | ✅ VERIFIED | `official_extra_analysis/FULL/delay_coupling_table_20260615T101353/tables/delay_c` |
| 7 | Dynamic median 101.5mm | ✅ VERIFIED | `official_extra_analysis/FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv` |
| 8 | Dynamic P95 214.4mm, RMSE 126.2mm | ✅ VERIFIED | `official_extra_analysis/FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv` |
| 9 | v2 DP-SNR 82× vs v1 23× | ✅ VERIFIED | `see notes` |
| 10 | PSF 3.3×5.3×5.4m | ✅ VERIFIED | `overnight_radar_20260710/analysis/step4_v2/step4_v2_stats.json` |

All 10 headline claims verified as traceable to specific output files.

**6b: The 44.49mm V5 result** ⚠️ QUALIFIED — The 44.49mm is the LOO-CV winner of a 107K-row post-hoc parameter sweep (888 combos × 121 d_tag). It is NOT pre-registered. LOO validation and bootstrap CIs are provided as safeguards, and the optimism gap is small (0.54mm), but the 888-combo sweep itself is a massive multiple-testing environment. The bootstrap CI [33.4, 82.8] is extremely wide, spanning the production 72.7mm baseline. This result should be cited only as 'exploratory post-hoc best under LOO' and never as a pre-registered or confirmatory finding.

- Source: `official_extra_analysis/FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv`
- Configuration: {'estimator': 'lower_trim_20', 'loss': 'huber30', 'geometry': 'V5', 'config_ids': [97, 90, 83, 67], 'note': 'Multiple config_ids share same LOO median because they differ only in d_tag sweep values'}
- Pre-registration: **❌ POST-HOC PARAMETER SEARCH**
- Sweep scope: 107448 grid rows, 888 unique combos
- LOO optimism gap: 0.54mm (all_data=43.94mm, LOO=44.49mm)

**6c: The 4.36% scale** ✅ VERIFIED — The 4.36% scale is computed consistently everywhere from one underlying value: Sim(3) scale = 0.9582672713308588 (v4-io production layout aligned to Vicon anchors). Formula is (1/0.9582672713 − 1) × 100 = 4.355% → rounded to 4.36%. No competing slightly-different versions exist. The related 43.55 mm/m pure-scale slope also derives consistently from the same value.

Formula: (1/0.9582672713 − 1) × 100 = 4.355% → rounded to 4.36%. (Note: the rounded form 1/0.9583 gives 4.351%; the 4.36% headline derives from the full-precision scale value.) Consistent everywhere.

---

## 3. Inconsistency Table

| Claim | Source | Evidence | Verdict |
|-------|--------|----------|---------|
| Smart TX Power causes power differential | External reviewer hypothesis | All frames < 0.25ms at 6.8Mbps 128-preamble → same BOOSTP250 bucket | ✅ Zero differential verified |
| APS011 range-bias correction applied | Implicit assumption | `dwt_getrangebias()` in deca_range_tables.c:636 has zero call sites | 💀 NEVER called |
| Antenna delays per-device calibrated | Implicit assumption | All roles hardcode 16436U: ss_twr_init.c:37, ss_twr_resp.c:47, ss_twr_anchor_init.c:18 | 💀 Identical for all devices |
| TX and RX delays set independently | Best practice | Same value (16436U) for both TX_ANTD and LDE_RXANTD | ⚠️ Forced equal |
| "30-min pre-warm sufficient" | Prior operational assumption | 6/6 listeners still drifting at 90 min (0.10-0.18 ppm/min); none reach stability | ❌ REFUTED |
| clean-6 39mm is metric accuracy | Diagnostic report context | Code computes RMS(||Xi-Xj||-Mij) over 15 pairs — self-consistency, not vs Vicon | ⚠️ Misleading without context |
| v4-io and common-mode differ only in delay param | Analysis narrative | Also differ in gauge convention, regularization bounds, L2 penalty structure | ❌ REFUTED (multiple confounds) |
| 44.49mm is a validated result | V5 analysis headline | Winner of 107K-row post-hoc sweep; LOO-CV applied but 888 combos tested | ⚠️ Not pre-registered |
| CFO stability = thermal stability | Thermal analysis assumption | No independent temperature measurement (TC_SARL, nRF52 TEMP, IR camera) | ⚠️ Unvalidated proxy |
| Overnight layout delays are correct | Overnight autopos pipeline | Delays bit-identical to erlangen_v4io_check but coordinates independently solved | 💀 Stale delay copy |
| 63.0mm appears in two contexts — same quantity? | Cross-check concern | Context 1: empirical rigid RMSE (62.99mm), Context 2: theoretical CRLB (62.74mm) | ✅ Different quantities, coincidence |
| ρ=−0.977 coupling is gauge-free | FIM paper claim | SE(3) removed (6 DOF), scale kept as parameter; Schur complement correct | ✅ Gauge-free verified |

---

## 4. Priority-Ranked Action List

### Critical (blocks paper claims or introduces systematic error)

1. **Per-device antenna delay calibration** — Impact: removes the largest known systematic error source. The solver fits per-anchor delays spanning 87mm range (−27 to +60mm) to compensate for hardware that shares a single hardcoded value. Cost: firmware change (read OTP, or implement a calibration sweep), medium complexity. Closes: antenna delay inconsistency, delay bound saturation, some of the scale residual.

2. **Enable APS011 range-bias correction** — Impact: removes systematic range bias vs distance (up to ~20mm at 2m). Cost: single function call in the ranging path, trivial firmware change. Closes: a known systematic error that the solver currently absorbs into delays.

3. **Clarify the 39mm metric in diagnostic reports** — Impact: prevents misinterpretation of self-consistency as metric accuracy. Cost: documentation change only (add "self-consistency" label, note it does NOT equal Vicon-validated RMSE). Closes: ambiguity in diagnostic outputs.

4. **Retract "30-min pre-warm" claim** — Impact: avoids incorrect operational guidance. The data shows drift continuing past 90 minutes. Cost: update documentation, extend warm-up recommendation, or add online CFO compensation. Closes: thermal stability overclaim.

### High priority (improves rigor, enables next experiments)

5. **Design a controlled thermal experiment with temperature measurement** — Add DW1000 TC_SARL reads or nRF52 TEMP peripheral reads to the firmware. Captures the crystal frequency-temperature curve. Cost: firmware addition + one experiment. Closes: CFO-temperature proxy assumption, warm-up time determination.

6. **Pre-register the V5 estimator selection** — The 44.49mm result comes from a 888-combo post-hoc sweep. Define the estimator choice BEFORE the next dataset. Cost: analysis discipline only. Closes: multiple-testing concern on the V5 headline number.

7. **Document v4-io vs common-mode confounds** — The comparison is presented as differing only in delay parameterization, but also differs in gauge, regularization, and bounds. List all differences explicitly. Cost: documentation. Closes: confounded comparison.

8. **Fix overnight layout stale delays** — The layout_clean6.json has delays from a different solver run. Either re-solve with consistent delay+geometry, or document the inconsistency. Cost: one solver re-run. Closes: delay-geometry mismatch in overnight analysis.

### Medium priority (improves completeness)

9. **Analyze LRD anchor-response scalar data** — 928K+ LRD rows per listener exist in the overnight capture, currently used only for channel-matrix yes/no. Contains: receive power, clock offset, diagnostic fields for all anchor-to-listener paths. Cost: new analysis script. Closes: unused data opportunity for inter-anchor range diagnostics, power characterization, and NLOS detection.

10. **Run the scripts that have no outputs** — Multiple `run_*.py` scripts in the V5 analysis tree have no corresponding output files (falsification batch, bruteforce variants, mechanism ablations). Some may have been run elsewhere; verify and document. Cost: script execution + review. Closes: unvalidated code.

11. **Validate v1 holdout bug impact** — v1 templates used median of ALL frames (no holdout). Quantify how much this inflates v1 SNR estimates and whether it affected any downstream analysis that compared v1 vs v2. Cost: one re-run comparison. Closes: potential bias in v1 baseline comparison.

### Low priority (nice to have)

12. **Fix PSF stats string** — step4_v2_stats.json says "±2400mm @75mm" but code uses ±6000mm @100mm. Cosmetic only, computed values are correct.

13. **Cross-validate the CRLB coincidence** — The 63mm appearing in both empirical RMSE and theoretical CRLB is a numerical coincidence, but worth highlighting in the paper as evidence that the system operates near its information-theoretic floor.

---

## 5. Raw Verification Data

### Firmware register write sites

| Register | File:Line | Context |
|----------|-----------|---------|
| TX_POWER (0x1E) | `drivers/dw1000/src/deca_device.c:620` | Inside dwt_configuretxrf(). This is the ONLY write to TX_POWER in the entire cod |
| TC_PGDELAY (0x2B:0x0B) | `drivers/dw1000/src/deca_device.c:617` | Inside dwt_configuretxrf(). Only path to write TC_PGDELAY from application code. |
| TC_PGDELAY (0x2B:0x0B) | `drivers/dw1000/src/deca_device.c:3687` | Inside internal driver function for TX bandwidth calibration — not called from B |
| TC_PGDELAY (0x2B:0x0B) | `drivers/dw1000/src/deca_device.c:3895` | Inside internal driver temperature compensation function — not called from BioSp |
| SYS_CFG DIS_STXP (0x04, bit 18) | `drivers/dw1000/src/deca_device.c:2236` | Inside dwt_setsmarttxpower(). Application NEVER calls this function. SYS_CFG_DIS |
| SYS_CFG DIS_STXP (0x04, bit 18) | `drivers/dw1000/src/deca_device.c:2240` | Inside dwt_setsmarttxpower(). disable path. Application never calls this. |
| TX_ANTD (0x18:0x00) | `drivers/dw1000/src/deca_device.c:831` | Inside dwt_settxantennadelay(). Called from application code. |
| TX_ANTD (0x18:0x00) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:3222` | Tag initiator configure_radio(). Sets TX antenna delay to 16436 (~0x4034). |
| TX_ANTD (0x18:0x00) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:914` | Anchor responder configure_radio(). Sets TX antenna delay to 16436. |
| TX_ANTD (0x18:0x00) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:136` | Anchor-to-anchor initiator configure_radio(). |
| LDE_RXANTD (0x2E:0x1804) | `drivers/dw1000/src/deca_device.c:812` | Inside dwt_setrxantennadelay(). Called from application code. |
| LDE_RXANTD (0x2E:0x1804) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c:3221` | Tag initiator configure_radio(). Sets RX antenna delay to 16436. |
| LDE_RXANTD (0x2E:0x1804) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:913` | Anchor responder configure_radio(). |
| LDE_RXANTD (0x2E:0x1804) | `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:135` | Anchor-to-anchor initiator configure_radio(). |
| TX_ANTD (0x18:0x00) | `SS-TWR/alt-SS-TWR/unicast/src/ss_twr_init.c:2031` | Unicast tag initiator — same value 16436U as broadcast. |
| TX_ANTD (0x18:0x00) | `SS-TWR/alt-SS-TWR/unicast/src/ss_twr_resp.c:723` | Unicast anchor responder — same value 16436U. |
| LDE_RXANTD (0x2E:0x1804) | `SS-TWR/alt-SS-TWR/unicast/src/ss_twr_init.c:2030` | Unicast tag initiator — same value. |
| LDE_RXANTD (0x2E:0x1804) | `SS-TWR/alt-SS-TWR/unicast/src/ss_twr_resp.c:722` | Unicast anchor responder — same value. |

### CIR sanity check

Raw CIR waveform decoded from LB.log. See `cir_sanity_check.png` in this directory.

- Source: LB.log line 4 (LCIRM frame)
- 85 LCIRD chunks → 4064 bytes → 1016 complex taps
- First path at tap 750 (|CIR| = 3111)
- Peak at tap 752 (|CIR| = 5931)
- Noise floor: mean=84, std=44

### Template SNR comparison

| Metric | v1 | v2 | Ratio |
|--------|----|----|-------|
| Median DP-SNR | 23.4 | 82.1 | 3.5× |

SNR definition: |A[REF_TAP]| / median(σ) — amplitude domain, not power.

### Layout anchor positions (v4-io production, mm)

**group_A**: Small room geometry (A-B ≈ 1787 mm)
Layouts: erlangen_v4io_field_check

**group_B**: Medium room geometry (A-B ≈ 2862 mm) — AUTHORITATIVE production
Layouts: official_v4io, production

**group_C**: Large room geometry (A-B ≈ 4467-4549 mm)
Layouts: erlangen_v4io_check, diagnostic_full8, overnight_clean6

### Thermal CFO summary

| Listener | CFO excursion (ppm, 90min) | Stability reached? | Drift at 90min (ppm/min) |
|----------|---------------------------|-------------------|--------------------------|
| LB | ? | NOT REACHED | ? |
| LE | ? | NOT REACHED | ? |
| LF | ? | NOT REACHED | ? |
| LCCF4 | ? | NOT REACHED | ? |
| L9336 | ? | NOT REACHED | ? |
| L955A | ? | NOT REACHED | ? |

### Supporting JSON files

All raw findings are stored as structured JSON in this directory:
- `track1_firmware.json` — Register audit, init sequence, antenna delay analysis
- `track2_cir.json` — FP_INDEX, templates, holdout, PSF, raw CIR, tap timing
- `track3_solver.json` — Solver parameters, clean-6 metric, v4-io vs common-mode, layouts
- `track4_thermal.json` — Cold-start initial conditions, CFO excursion, CFO-temperature
- `track5_consistency.json` — Layout audit, delay crosscheck, 63mm, scale, ρ=−0.977
- `track6_claims.json` — 10 headline claims, 44.49mm trace, 4.36% scale

---

## 6. Gaps and Missing Experiments

### 6a. Claims lacking sufficient evidence

1. **"30-min pre-warm is sufficient"** — ❌ Refuted by cold-start data. No listener stabilizes in 90 min.
2. **CFO stability = thermal stability** — 🔍 Unverified. No independent temperature measurement exists.
3. **v4-io vs common-mode is a controlled comparison** — ❌ Multiple confounds beyond delay parameterization.
4. **44.49mm is a reliable V5 headline** — ⚠️ Post-hoc parameter sweep; LOO-CV helps but 888-combo multiplicity is not fully addressed.
5. **Per-device antenna delays are unnecessary** — 💀 The solver compensates, but at the cost of consuming degrees of freedom that could improve geometry accuracy.

### 6b. Gap-closing experiments ranked by impact

| Rank | Experiment | Questions closed | Cost | Needs |
|------|-----------|-----------------|------|-------|
| 1 | Per-device antenna calibration sweep | delay saturation, scale residual, accuracy floor | 2h setup + 30min capture | new firmware feature |
| 2 | Extended cold-start with temperature sensor | warm-up time, CFO-temp curve, online compensation design | 1 firmware addition + 4h capture | firmware change |
| 3 | APS011 enable + re-capture | range bias vs distance, improved short-range accuracy | trivial firmware change + 1h capture | firmware change |
| 4 | Pre-registered V5 estimator on new data | V5 headline validity | analysis discipline only | new capture |
| 5 | LRD anchor-response analysis | NLOS detection, power characterization, anchor health | analysis script only | nothing new |
| 6 | Re-solve overnight layout with consistent delays | delay-geometry consistency | one solver re-run | nothing new |

### 6c. Unanalyzed data

1. **LRD anchor-response scalar data**: 928K+ rows per listener in overnight capture. Contains receive power, clock offset, diagnostics for all anchor↔listener paths. Currently used only for channel-matrix Y/N determination — never analyzed for ranging quality, NLOS detection, or temporal drift.
2. **Indoor chaos dataset** (`autopos_pipeline/indoor_chaos_los_20260510/`): Multiple anchor sweeps with various firmware configurations. Appears to be a development/debug dataset, not formally analyzed.
3. **Pre-broadcast unicast data**: The unicast firmware tree has its own source files. Any captures from that era are in `logs/` but analysis focuses exclusively on broadcast-era data.

### 6d. Unvalidated code

Multiple `run_*.py` scripts in the V5 analysis tree have no corresponding output files:
- `FULL_V5_batch3_falsification/scripts/run_batch3_falsification.py`
- `FULL_V5_rawframe_bruteforce/scripts/run_rawframe_bruteforce.py`
- `FULL_V5_rawframe_bruteforce_v2/scripts/run_rawframe_bruteforce_v2.py`
- `FULL_V5_extended_mechanism_ablations/scripts/run_extended_mechanism_ablations.py`
- `FULL_V5_three_dimensions/scripts/run_three_dimensions_analysis.py`
- `FULL_V5_phase_center_sensitivity/scripts/run_phase_center_sensitivity.py`

Note: these may have been run in a different working directory or their outputs cleaned. The `old-G_DO_NOT_ANALYSE_ANYMORE/` directory contains superseded scripts that are correctly flagged.

---

*End of audit report. All findings are read-only observations. No production code or data was modified.*
