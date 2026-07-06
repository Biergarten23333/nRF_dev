# Repo Archaeology — 2026-07-06

Scope: read-only forensic inventory of `BioSpur_UWB_before_start`, focused on
`autopos_pipeline/28052026_Erlangen_Official/{Analysis/official_extra_analysis,solver}` and
`SS-TWR/alt-SS-TWR/broadcast/handoff_scripts_20260704`. All findings below were verified directly
against files on disk (path, size, mtime, head/tail or full content for small files) — nothing here
is inferred from chat memory alone. Where a chat-remembered number is quoted, it is cross-checked
against an actual file.

## 1. Git Timeline (last 60 days)

`git log --since=2026-05-01` on `feature/wand-internal-sweep` returns **97 commits** from
2026-05-02 to 2026-07-05. Grouped by theme (dates are commit dates, not necessarily work dates):

| Date(s) | Commit(s) | Theme |
| --- | --- | --- |
| 2026-05-02 – 05-12 | `68c0386cf` … `e064de5b9` | alt-SS-TWR broadcast firmware bring-up (BROADCAST_B51/B54 diagnostics, overnight guard bisect) |
| 2026-05-14 – 05-19 | `23b9b0a19`, `5f16b3995`, gesture-recognition adds | Outdoor AutoPos full-compare analysis; BioSpur_Gesture_Recognition scaffolding |
| 2026-05-23 – 05-29 | `d0d947b24`, `5022f0296`, `1936d01c3`, `1a9316b22`, `994c36c86`, `4f0729017` | **Erlangen Vicon validation session setup** (`autopos_pipeline/28052026_Erlangen_Official` created), multi-tag TDMA roster guard |
| 2026-06-01 – 06-03 | `029126351`, `489592d71`, `7f0921c0f`, `63ccfeb20` | AutoPos_simulation wall-NLOS study; **FULL ROTO ablations + reviewer audit (`why1`…`why5` tables first appear)** |
| 2026-06-04 | `f23fab900`, `290129377`, `3eb55e877`, `fe676318f` | FULL-US height-gauge analysis; **production T4 static headline locked**; FULL metrology resilience audits; reviewer-audit ROTO circle metrics (`why6`–`why11`) |
| 2026-06-05 – 06-08 | `bd1ee21cb`, `0d48089e3`, `b1dda9798`, `9a9c85acb`, `f436c856e`, `11a2e1f20`, `6a919b41a`, `77e597593`, `7135cd778` | IMU-fusion simulation pipeline + Phase 4 results; AutoPos wall-NLOS simulation; **`FULL_4way_comparison` populated** (production_method_probe, resilience_gap_audit, reporting_checklist, roto_filtered, roto_pseudo_imu) |
| 2026-06-10 | `30b13caae`, `468f33d68`, `618443bc4`, `88cb037c2`, `06c497643` | Erlangen AutoPos validation analysis update; tail-rejection + common-mode report alignment; IMU-fusion objective formatting |
| 2026-06-11 | (audit_phase1c layouts/tables, no dated commit hash captured separately — see AUDIT_FINDINGS.md, dated 2026-06-11) | **Common-mode solver variant** (`audit_phase1c_common_mode.py`) and **oracle per-anchor delay table** (`item1_oracle_*`) produced |
| 2026-06-13 | `503f13022`, `8136a464d` | **`audit_phase1`, `audit_phase1c`, `audit_phase1d` scripts + outputs committed** (delay-bound audit, common-mode audit, tag-delay-cancellation audit); ground-reflection wall study |
| 2026-06-14 | `1b39411c1`, `f762ecf47` | Common-mode tag-delay validation added + follow-up notes documented |
| 2026-06-15 | `fcefb0950` | **"Add static delay-coupling analysis suite"** — this is the commit that adds `run_delay_coupling_table.py` and `run_inframe_tagdelay_estimate.py` (both under `FULL/scripts/`) |
| 2026-06-17 | `b34aab863` | **"Add CIR capture updates and V5 analysis outputs"** — adds `FULL_V5_mechanism_ablations` (incl. `A_hard_cv/`), `FULL_transfer_matrix`, `FULL_4way_comparison` V5 LOO-tag-delay layouts (25×4 JSON files), `FULL_V4_vs_V5_final` |
| 2026-06-20 | `22a6f640b` | **"Add V5 convention unification and solver audit outputs"** — adds `FULL_V5_batch3_falsification` (F1–F6 tasks incl. nested CV), `FULL_V5_extended_mechanism_ablations`, more transfer-matrix figures |
| 2026-06-22 | (untracked at scan time / same session) | `roto_spatiotemporal_dnn` Optuna sweeps land under `FULL/logs`, `FULL/models`, `FULL/tables` (DNN range-residual model) |
| 2026-06-24 – 06-26 | `672d5e76e`, `c04b8b794`, `4fffa3185`, `659456d67`, `18db9da59`, `2aff8b271` | UWB spatial-error-field experiments; runtime CIR controls; 20260624 validation recovery; 6-tag nordiag baseline sweeps |
| 2026-06-27 | `c4fb161e5`, `cb2cb967f`, `ac25bfa2e`, `b667e73c3`, `3bb75ac2f` | Anchor-7 tail-responder fix; **novelty_check + additional_exp paper updates and litsearch** (→ `NOVELTY_LEDGER_LITSEARCH_20260627.md`); RFD/Listener-E handoff |
| 2026-06-29 – 06-30 | `189cebd7b`, `aa1d9c393`, `e29e1e059`, `5182da971` | UWB wand internal sweep instrumentation; listenerE victimcatch2 capture; **paper EN report updates (autopos_system + delay_layout_coupling)** |
| 2026-07-02 | `6737a78fc`, `53fb4d006`, `c5f38a225` | Fixed-a19 anchor OTA + novelty-check paper updates; listener self-heal firmware; **anchor-B occlusion probe + AutoPos v4-io re-solve (C/G moved)** |
| 2026-07-03 | `c07896348`, `06c774b52`, `16884ad55` | UWB capture logs via LFS; overnight-soak L-B CIR raw logs; **TDMA phase-beat mechanism verdict + promoted arbitration scripts** |
| 2026-07-03 – 07-04 | `c0c766b8b`, `b96752461`, `375271a53` | JISPIN publication draft assets; **volume-sensing pivot** (4-CIR listener fleet); repo consolidated onto 500GB SSD |
| 2026-07-05 | `3ee5b5f81` | **Roto-SAR + volume-sensing analysis toolkit** (this is the last commit; everything in `handoff_scripts_20260704/` newer than this — `diag_*`, `coherent_stage*`, the two 2026-07-06 audit .md files — is **untracked**, confirmed by `git status` showing `??`) |

**Specifically checked (per instructions), via `git log --diff-filter=A -- <pathspec>`:**

| Pathspec | Ever committed? | Commit |
| --- | --- | --- |
| `solve_v4_common_mode*` | ❌ **No commit ever adds a file with this literal name.** | — |
| `run_delay_coupling_table.py` | ✅ | `fcefb0950` (2026-06-15) |
| `run_inframe_tagdelay_estimate.py` | ✅ | `fcefb0950` (2026-06-15) |
| `diag_aps011_*` (intervention/slope_only/solver_accounting) | ❌ Never committed — confirmed **untracked** (`??` in `git status`) | — |
| `diag_erlangen_modes.py` | ❌ Never committed — untracked | — |
| `diag_sigma_map.py` | ❌ Never committed — untracked | — |
| `diag_theta_decomp.py` | ❌ Never committed — untracked | — |
| `audit_phase1*` scripts | ✅ | `503f13022` (2026-06-13) |
| `why*` (reviewer_audit) tables | ✅ | `63ccfeb20`, `fe676318f`, `3eb55e877`, `290129377`, `11a2e1f20` (2026-06-03→06-06) |
| `*falsification*` dir | ✅ `FULL_V5_batch3_falsification` | `22a6f640b` (2026-06-20) |
| `*mechanism_ablation*` dirs | ✅ `FULL_V5_mechanism_ablations` (`b34aab863`, 06-17), `FULL_V5_extended_mechanism_ablations` (`22a6f640b`, 06-20) | |
| `*transfer_matrix*` dirs | ✅ `FULL_transfer_matrix` (`b34aab863`, 06-17); more figures in `22a6f640b` (06-20) | |

**Important nuance on the common-mode solver:** there is no file literally named `solve_v4_common_mode*` anywhere in git history or on disk. The actual implementation lives under different names: `FULL/scripts/audit_phase1c_common_mode.py`, `FULL/scripts/run_v5_commonmode_anchor_analysis.py`, `FULL/scripts/run_commonmode_tagdelay_candidate.py`, and solver-side outputs `solver/outputs/v1_to_v4_io_field_check/{v4-io-commonmode,v5-commonmode}/layout.json`. If the common-mode solver script was discussed by that name in chat, it either never existed under that name or is a name that was only discussed, not created.

## 2. Directory Tree Status

| Directory | Exists? | File count | Latest mtime |
| --- | --- | --- | --- |
| `$ANALYSIS/FULL/` | ✅ yes | 1217 | 2026-06-22 15:26 (`models/roto_spatiotemporal_dnn_best_no_geo.pt`) |
| `$ANALYSIS/FULL_V5/` | ✅ yes | 21 | 2026-06-17 19:47 |
| `$ANALYSIS/FULL_4way_comparison/` | ✅ yes | 218 | 2026-06-17 18:21 |
| `$ANALYSIS/FULL_transfer_matrix/` | ✅ yes | 10 | 2026-06-17 19:48 |
| `$ANALYSIS/FULL_V5_mechanism_ablations/` | ✅ yes | 21 | 2026-06-17 21:09 |
| `$ANALYSIS/FULL_V5_batch3_falsification/` | ✅ yes | 50 | 2026-06-18 01:39 |
| `$ANALYSIS/FULL_V5_align_to_Vicon/` | ✅ yes | 9 | 2026-06-17 19:48 |
| `$SOLVER/outputs/` | ✅ yes | 78 | 2026-06-17 17:11 (`v1_to_v4_io_field_check/v5-commonmode/layout.json`) |
| `$HANDOFF/` | ✅ yes | 79 | 2026-07-06 13:41 (`diag_erlangen_ablation.py`, untracked) |

`FULL/` subdirectory highlights (maxdepth 2): `audit_phase1/`, `audit_phase1c/`, `audit_phase1d/`, `common_mode_axis_split_20260613/`, `commonmode_tagdelay_candidate_20260614T181639/` (with `tag_delay_000p000|080p000|091p153|095p000` subdirs), `delay_coupling_table_20260615T101353/`, `inframe_tagdelay_estimate_20260614T195544/` (33 `tag_delay_*` subdirs from 000 to 150mm), `v5_commonmode_anchor_analysis_20260617T171125/`, `vertical_consistency_stretch_20260613/`, `roto_absolute/`, `filtered_deployment/`, `filtered_deployment_mean/`, `models/` (DNN checkpoints), `scripts/` (~35 analysis scripts), `tables/` (~110 CSV/MD outputs).

`FULL_V5_mechanism_ablations/` subdirs: `A_hard_cv/tables/`, `B_residual_field/tables/`, `C_cancellation_valley/tables/`, `D_per_height_dtag/tables/`, `E_dtag_curves/tables/`, `F_multi_criterion_dtag/tables/`, `reports/`, `scripts/`.

`FULL_V5_batch3_falsification/` subdirs: `figures/` (9 PNGs, f1–f5), `reports/` (9 .md + 6 `f*_status.json` + `SCRIPT_VERIFICATION.json`), `scripts/run_batch3_falsification.py`, `tables/` (18 CSVs incl. `f1_nested_cv_results.csv`, `f1_nested_cv_summary.csv`, 6 `checkpoint_f*_done.txt`).

**Note:** the `$ANALYSIS` directory in fact contains far more than the ~40 subdirectories mentioned in the task brief — a non-exhaustive scan turned up additionally: `FULL_4way_comparison_US`, `FULL_AutoPos_align_to_Vicon(_US)`, `FULL_AutoPos_scale_to_vicon(_US)`, `FULL_AutoPos_one_baseline_scale_correction(_US)`, `FULL_V5_scale_to_vicon`, `FULL_V5_one_baseline_scale_correction`, `FULL_V5_GPU_discovery`, `FULL_V5_GPU_tier1`, `FULL_V5_rawframe_bruteforce(_v2/_v3)`, `FULL_V5_overnight_batch2`, `FULL_V5_final_gate`, `FULL_V5_final_audit`, `FULL_V5_convention_unification`, `FULL_V5_mechanistic_deepdive`, `FULL_V5_roto_deepdive`, `FULL_V5_three_dimensions`, `FULL_V5_anchor_lower_trim`, `FULL_V5_phase_center_sensitivity`, `FULL_V5_followup_validation`, `FULL_V5_paper_strengthening`, `FULL_V5_experimental_report`, `FULL_V4_vs_V5_final`, `FULL_NO_US_VS_US`, `FULL_US`, and `old-G_DO_NOT_ANALYSE_ANYMORE` (explicitly flagged by the researcher as stale/do-not-use).

## 3. Product Existence Check (10 items)

| # | Item | Status | Path | Size | Modified | Key content |
| --- | --- | --- | --- | --- | --- | --- |
| 3.1 | Four-way ablation table | ✅ **FOUND, matches chat memory** | `$ANALYSIS/FULL/delay_coupling_table_20260615T101353/DELAY_COUPLING_TABLE.md` (+ `tables/delay_coupling_table.csv`, `delay_coupling_vs_frozen_report.csv`, `delay_coupling_per_session.csv`) | 1827 B (.md) | 2026-06-15 10:14 | RMSE rows: D1 no-correction 311.3, D2 transplanted 252.2, C re-estimated-in-Vicon-frame 77.7, A AutoPos-co-fitted 108.9 — i.e. **~307/~255/~78/~109 is real** and matches `main_EN.tex` `tab:delay_coupling` (line 494-497) to <0.2mm. The .md even diffs itself against the frozen report numbers (deltas ≤0.18mm). |
| 3.2 | Tag-delay sweep curve | ✅ **FOUND, two-minima structure confirmed** | `$ANALYSIS/FULL/inframe_tagdelay_estimate_20260614T195544/tables/inframe_tagdelay_profile.csv` (34 data rows) and `$ANALYSIS/FULL_4way_comparison/tables/B_dtag_sweep_curve.csv` (62 data rows, 2mm step 0–120mm) | 31.7 KB / 9.5 KB | 2026-06-14 19:57 / 2026-06-17 17:35 | In `inframe_tagdelay_profile.csv`: `range_ssr_per_term` (ranging-residual criterion) is minimized at **d_tag=55mm** (5,151,935 vs 5,155,918 @50mm and 5,153,544 @57mm); `err_3d_median_mm` (Vicon-referenced accuracy) bottoms near **d_tag=65–90mm** (58.92mm @65, 58.48mm @**90mm** — the global min in this file) — i.e. the "range-optimal ≈55mm / Vicon-optimal ≈90mm" split is real. `B_dtag_sweep_curve.csv` (finer-grained, self-cal-frame LOO variant) shows a related double-dip at ~34mm/~76mm instead — a related but distinct sweep, same qualitative phenomenon. |
| 3.3 | Common-mode solver variant | ✅ **FOUND, exact number match** | `$ANALYSIS/FULL/audit_phase1c/tables/item2_common_mode_summary.csv`, layout at `$ANALYSIS/FULL/audit_phase1c/layouts/v4io_common_mode/layout.json`; also `$SOLVER/outputs/v1_to_v4_io_field_check/{v4-io-commonmode,v5-commonmode}/layout.json` | 1950 B (layout.json) | 2026-06-11 23:32 | `c_mm = 111.98` (mean of 8 per-anchor `d_anchor_mm` values, range 99.6–127.3), `rigid_anchor_rmse_mm = 62.99` — **exactly matches "c≈112mm, rigid RMSE≈63mm."** Same row also carries `current_sim3_scale=0.9583`, `sim3_scale=1.0098`, `current_static_t4_mean_rmse_mm=109.84`, `..._median_mm=72.69`, `..._p95_mm=171.49` — all of stage-4's target numbers trace to this single CSV row. |
| 3.4 | **Hard CV / Nested CV — CRITICAL CHECK** | ✅ **BOTH RAN, outputs exist and are non-trivial/non-empty** | Hard CV: `$ANALYSIS/FULL_V5_mechanism_ablations/A_hard_cv/tables/{hard_cv_summary.csv, hard_cv_height_tier_results.csv, hard_cv_edge_center_results.csv, hard_cv_position_tier_assignments.csv}`. Nested CV: `$ANALYSIS/FULL_V5_batch3_falsification/tables/{f1_nested_cv_results.csv (13 data rows), f1_nested_cv_summary.csv}` + `reports/TASK_F1_NESTED_CV.md` + `figures/f1_nested_cv_comparison.png` | hard_cv_summary.csv 501B; f1_nested_cv_results.csv 1547B | 2026-06-17 20:49 (hard_cv); 2026-06-18 01:35 (nested_cv) | **Hard CV real numbers:** V4+C_V4 full-LOO median 57.92mm, worst-tier (LOW) 66.33mm, degradation +8.41mm; V5+C_V5 full-LOO 67.85mm, worst-tier 72.39mm, degradation +4.54mm; Vicon+C_Vicon_cm 63.39→75.59mm, +12.20mm. **Nested CV real numbers:** per-split mean test median 82.9mm (height), 88.0mm (quadrant), 94.2mm (spatial6), with per-fold `train_median_3d`/`test_median_3d` pairs showing real train↔test gaps (e.g. fold1: train 87.8mm vs test 71.1mm; fold4: train 83.3mm vs test 144.7mm). `FALSIFICATION_COMPLETION.md` reports F1 status "ok", top-selected variant `V4_CV4\|p50\|inverse_rms\|range_residual_LOO_on_train`. **These are not stub/placeholder files — both analyses genuinely executed with plausible fold-level variance.** |
| 3.5 | Transfer matrix | ✅ **FOUND, exact row-count match** | `$ANALYSIS/FULL_transfer_matrix/tables/transfer_matrix_Dsweep_detail.csv` | 231,562 B | 2026-06-17 19:44 | **733 lines total = 732 data rows**, exactly matching "732 rows (12 layout×correction combos)." Columns include `layout_source, correction_source, d_tag_mm, median_3d_mm, rmse_3d_mm, ...` sweeping `d_tag_mm` 0–120mm across combos like `L_Vicon×C_Vicon_cm`, `L_V5×C_Vicon_cm`, etc. |
| 3.6 | Oracle delay table | ✅ **FOUND, exact number match** | `$ANALYSIS/FULL/audit_phase1c/tables/item1_oracle_per_anchor_delay.csv` (8 rows, one per anchor) + `item1_oracle_summary.csv` | 730B / 442B | 2026-06-11 23:32 | Per-anchor `oracle_d_i_mm`: A=148.22 (largest), B=96.34, C=127.42, D=114.66, E=48.94, F=50.02, G=86.13, H=85.25. Summary: `mean_oracle_d_mm=94.62`, `median=91.24`, `largest_anchor=A`, `largest_oracle_d_mm=148.22` — **exact match to "d_A=148.2 largest, mean≈94.6."** |
| 3.7 | OTP read record | ✅ **FOUND, present but disabled by default** | `$REPO/SS-TWR/alt-SS-TWR/broadcast/apps/tag/src/tag_app.c` (function `tag_print_otp_diag()`, guarded by `#ifndef APP_TAG_OTP_DIAG` / `#define APP_TAG_OTP_DIAG 0U`) | — | — | Reads DW1000 OTP word 0x1C (`otp_01c`) and decodes `ant_delay` as tag=high16/anchor=low16 via `dwt_otprevision()`/raw OTP dump (`OTP_DUMP[...]`, `OTP_DECODE ...`). Code exists and is functional but compiled out unless `APP_TAG_OTP_DIAG=1`. Grep hits for "0x4058"/"16472" elsewhere were false positives (coincidental numeric matches in unrelated CSV log files, not OTP code). |
| 3.8 | `main_EN.tex` current state | ✅ **FOUND, complete, internally consistent** | `$PIPELINE/Analysis/reports/EN/main_EN.tex` | 165,869 B | 2026-06-15 11:58 | 2403 lines. Has `\label{sec:delay_decomposition}` (line 524) and `\label{sec:delay}` (line 469). Four-way ablation table at `\label{tab:delay_coupling}` (lines 483-499) with the exact 311.3/252.2/77.7/108.9 RMSE row set from item 3.1. "72.7/171.5/109.8" appear 10+ times, always consistent (headline static median/P95/RMSE). "4.36%/0.9583/scale" appear together describing the Sim(3) diagnostic (0.9583 AutoPos→Vicon, apparent +4.36% scale, common-mode-corrected 1.0098). **`grep -c "TODO\|FIXME\|XXX\|PLACEHOLDER"` = 0** — no leftover placeholders. (Note: text does use `\SI{\DelayAblationSelfCalibratedRmseMm}{...}`-style LaTeX macros for some values at line 90 — these are presumably resolved by a numbers-macro file not scanned here, not literal TODOs.) |
| 3.9 | `DIAG_SIGMA_MAP_RESULTS_20260706.md` | ✅ **FOUND, all 4 requested sections present + 1 bonus section** | `$HANDOFF/DIAG_SIGMA_MAP_RESULTS_20260706.md` | 51,768 B | 2026-07-06 13:46 (today, still being actively edited) | 669 lines. Section headers: `## TL;DR — verdict`, `## Method`, `## Results`, `## 5. Confound controls...`, `## 5b. APS011 range-bias...`, `## 6. θ-locked residual decomposition...`, `## 7. Gate + expected weighting gain`, `## 8. Next step...`, `## 5b-REVISED — APS011 SLOPE-ONLY (2026-07-06, supersedes §5b)`, `## 5c — ANCHOR-ANCHOR RESPONSE ACCOUNTING (2026-07-06)`, `## 5d — ERLANGEN REAL LAYOUT ERROR: ORTHOGONAL-MODE DECOMPOSITION (2026-07-06)`, and an additional **`## 5e — v4-io DELAY-TREATMENT ABLATION LADDER (2026-07-06)`** not mentioned in the task brief but present, adjudicating "the 63mm residual + A-shape attribution." **§5b, §5b-REVISED, §5c, §5d all confirmed present.** |
| 3.10 | Falsification batch scripts/results | ✅ **FOUND** | Scripts: `$ANALYSIS/FULL_V5_batch3_falsification/scripts/run_batch3_falsification.py` (1 script). CSVs: 18 files under `tables/` (f1–f6 series). MD: 9 reports under `reports/` (`FALSIFICATION_COMPLETION.md`, `INTERNAL_REBUTTAL.md`, `REVIEWER_ATTACK.md`, `TASK_F1..F6_*.md`) | — | 2026-06-18 | `FALSIFICATION_COMPLETION.md`: all 6 tasks status "ok" (F1 nested-CV 4.0s, F2 winner's-curse optimism-gap 9.6mm/0.7s, F3 profile-likelihood best-scale 72.0mm/220.8s, F4 nullspace ratio@10mm=0.180/0.4s, F5 NLOS-leakage LOAO-MLP PR-AUC 0.419/7.6s, F6 review-simulation 3 claims demoted C/D). `REVIEWER_ATTACK.md` and `INTERNAL_REBUTTAL.md` are substantive self-critique documents (not stubs), e.g. rebuttal explicitly concedes "V5 transferability remains a hypothesis... V4 can win under low vertical spread." |

## 4. Number Consistency Audit

| Number(s) | Where found | Consistent? |
| --- | --- | --- |
| 72.7 / 109.8 / 171.5 | `main_EN.tex` (10+ occurrences, lines 86,502,551,619,629-631,699,766,791,1019,1631,1728-1729,2351); `$ANALYSIS/FULL/audit_phase1c/tables/item2_common_mode_summary.csv` (`current_static_t4_mean_median/p95/rmse`) | ✅ **Consistent everywhere checked.** Same triple appears in the tex prose, the LaTeX headline table, the caption discussion, and the raw common-mode-audit CSV that generated it. |
| 4.36 / 0.9583 / 4.355 | `main_EN.tex` lines 451-462 (`+4.36%` apparent scale from `0.9583` Sim(3) fit); `$ANALYSIS/FULL/audit_phase1c/tables/item2_common_mode_summary.csv` (`current_sim3_scale_autopos_to_vicon=0.9583`) | ✅ Consistent. "4.355" not found verbatim but 4.36% is the rounded value of (1/0.9583 − 1) ≈ 4.35% — same underlying number, rounding difference only, not a contradiction. |
| 112.0 / 63.0 / 1.0098 | `$ANALYSIS/FULL/audit_phase1c/tables/item2_common_mode_summary.csv` (`c_mm=111.98`, `rigid_anchor_rmse_mm=62.99`, `sim3_scale=1.0098`); `main_EN.tex` line 544 ("Sim(3) scale moves from 0.9583 to 1.0098, and the rigid anchor RMSE improves from 105.4mm to 63.0mm") | ✅ Consistent — same common-mode-audit row underlies both. |
| 94.6 / 148.2 / 120.5 | `$ANALYSIS/FULL/audit_phase1c/tables/item1_oracle_summary.csv` (`mean_oracle_d_mm=94.62`, `largest_oracle_d_mm=148.22` for anchor A); `main_EN.tex` line 1643 ("mean excess is 120.5mm per pair... oracle anchor-side estimate has mean 94.6mm per device... anchor A is largest") | ✅ Consistent — 94.6/148.2 are the oracle per-device delay numbers; 120.5mm is a *different* quantity (mean pairwise range-excess, not per-device delay) referenced in the same sentence — not a duplicate/conflicting use of the same metric, just two related numbers quoted together. No contradiction found. |
| 49.6 / 91.153 / 58.6 / 109.5 / 67.2 | Extremely broad hits (653 files) because these are common short numeric substrings across the ~40-directory analysis tree; narrowed manually: `91.153` = the fixed "`D_oracle_91`" d_tag test-point used identically across `FULL_transfer_matrix/tables/transfer_matrix_48cells.csv`, `FULL_V4_vs_V5_final/reports/PHASE6_V4_VS_V5_FINAL.md`, and both `run_full_v5_ablation_pipeline.py` copies (`D_LOO_CV=49.621, D_oracle_91=91.153` — same literal constant reused verbatim in 3+ pipeline stages) | ✅ Consistent (same named constant reused deliberately across pipeline stages, not drift). 58.6/109.5/67.2 individually recur in many unrelated static/roto accuracy tables (expected, since median/RMSE values cluster in this range across many ablations) — no specific contradictory pair identified given the volume; flagging as **not a red flag**, just a common-magnitude coincidence across ~40 near-duplicate experiment directories. |

**Overall: no genuine numeric contradictions were found for any of the five target quantities.** Everywhere a chat-remembered number was checked against disk, it traced cleanly to one authoritative source CSV/JSON that is also cross-referenced in `main_EN.tex` and (for the four-way table) even self-validated against a "frozen report" diff with sub-millimetre deltas.

## 5. Other Reports & Documents

- **Executive/summary report:** `$PIPELINE/Analysis/reports/EN/exclusive_EN.tex` (10,196 B, 2026-06-15 10:14) — exists, plus compiled `.pdf`/`.aux`/`.fls`/`.log`/`.synctex.gz` artifacts (i.e., it has actually been LaTeX-compiled, not just drafted).
- **Audit findings:** `$PIPELINE/Analysis/reports/EN/AUDIT_FINDINGS.md` (32,604 B, 2026-06-11) — scopes Phase 1/1c/1d audits, points to the exact scripts and output dirs (`audit_phase1_revised.py`, `audit_phase1c_common_mode.py`, `audit_phase1d_tag_delay_cancellation.py`), states "No paper text was edited."
- **Novelty / prior-art:** `$PIPELINE/Analysis/reports/EN/novelty_check_autopos_system.tex` (38,710 B, 2026-06-30) and `novelty_check_delay_layout_coupling.tex` (57,719 B, 2026-06-30), each with `_short` and `_submission` variants and compiled `.pdf`s (i.e. these were carried through to a submittable state, not left as drafts). Also `$PIPELINE/Analysis/reports/EN/literature_search/{NOVELTY_LEDGER_LITSEARCH_20260627.md (34,936 B), novelty_gap_verification.md (3,160 B), novelty_gap_verification_v2.md (2,948 B)}` — a v1→v2 revision exists, suggesting the gap-verification was iterated on at least once.

## 6. Summary: What EXISTS vs What is MISSING

### Confirmed on disk (usable directly)
- Four-way delay-coupling ablation table (3.1) — CSV + MD, self-validated against frozen report.
- Tag-delay sweep with the range-optimal/Vicon-optimal double-minimum structure (3.2) — two related sweep files.
- Common-mode solver variant, c≈112mm / RMSE≈63mm (3.3) — full layout.json + summary CSV.
- **Hard CV and Nested CV both actually ran with real, non-trivial fold-level output (3.4)** — the single most important finding: neither needs to be re-run.
- Transfer-matrix D-sweep, 732 rows exactly (3.5).
- Oracle per-anchor delay table, exact match to remembered numbers (3.6).
- OTP antenna-delay firmware read path (3.7) — present, compiled out by default (`APP_TAG_OTP_DIAG=0`).
- `main_EN.tex` (3.8) — 2403 lines, zero TODO/FIXME/placeholder markers, numbers internally consistent.
- `DIAG_SIGMA_MAP_RESULTS_20260706.md` (3.9) — all requested sections plus a bonus §5e.
- Falsification batch F1–F6 scripts/tables/reports (3.10) — all "ok" status.
- Executive summary, audit findings, and novelty-check documents (Stage 5) — all exist, compiled to PDF.

### Scripts exist but output missing/empty (needs re-run)
- None identified in this scan. Every script located in stages 2–3 that was cross-checked against an output directory had corresponding non-empty output files with plausible, non-placeholder numeric content.

### Not found (needs to be done from scratch)
- A file literally named `solve_v4_common_mode*` — never existed under that name (functionality exists under `audit_phase1c_common_mode.py` / `run_v5_commonmode_anchor_analysis.py` instead; if a specific different variant was meant, it was only discussed in chat, never materialized as a distinctly-named file).
- `diag_aps011_intervention.py`, `diag_aps011_slope_only.py`, `diag_aps011_solver_accounting.py`, `diag_erlangen_modes.py`, `diag_sigma_map.py`, `diag_theta_decomp.py` — all present **on disk** in `$HANDOFF` (confirmed via directory listing) but **never git-committed** (untracked `??`). Not missing, but at risk of loss if the working tree is ever reset/cleaned without committing first.
- LaTeX numeric macros referenced at `main_EN.tex` line 90 (`\DelayAblationSelfCalibratedRmseMm`, `\DelayAblationSurveyedReestimatedRmseMm`, `\CommonModeNoTagDelayStaticMedianMm`) were not traced to a macro-definition file in this scan — worth a follow-up grep for `\newcommand.*DelayAblation` if those values need auditing.
