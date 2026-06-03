# Measurement Reviewer Audit: AutoPos × OptiTrack/Vicon

This is a skeptical data interrogation of the corrected FULL analysis. It is not a replacement for the main report.

## WHY #1: Why Does ROTO Not Collapse When Static Does?

**Numbers computed.**

- Self-cal v4-io/T4 ROTO sample P50 after the existing offset: 102.6 mm; after 1 ms local absolute-error refit: 102.7 mm.
- Vicon-truth+delaycal/T4 ROTO sample P50 after the same test: 104.5 -> 104.1 mm.
- Median OptiTrack linear speed during ROTO is 639.1 mm/s (P95 906.0 mm/s). A 0.8 ms broadcast window therefore contributes only 0.51 mm median / 0.72 mm P95 displacement.
- Post-hoc per-capture rigid registration reduces self-cal sample P50 from 102.7 to 91.6 mm.

**Budget.** Using a quadrature decomposition, because vector error components should not be arithmetically added:

- constant time-offset residual: 0.00 mm
- 0.8 ms motion-window upper contribution: 0.72 mm
- spatially coherent residual removable by per-capture rigid fit: 46.4 mm
- remaining wand/ranging/antenna-pattern residual after post-hoc rigid: 91.6 mm
- quadrature sum: 102.7 mm (observed sample P50 102.6 mm).

**Verdict.** The 0.8 ms protocol-window motion is negligible. Constant capture-level time offset is also not the bottleneck. The dominant error is a mixture of spatially coherent residual registration/phase and residual rotating-wand ranging behavior.

**Reviewer-survivability.** Survives only if described as a diagnostic decomposition. The per-capture rigid fit uses Vicon truth and is not a deployable correction.

## WHY #2: Why Relative-Distance Improves But Absolute Stays Around 105 mm

**Tests run.**

- Refit each capture's time offset on a 1 ms grid around the existing beta, minimizing absolute 3D median error.
- Fit one post-hoc proper rigid transform per capture, using all overlapping two-tag ROTO samples.

**Numbers computed.**

- Self-cal time refit drop: P50 -0.06 mm; P95 changes are in `why2_time_offset_refit.csv`.
- Vicon-truth+delaycal time refit drop: P50 0.36 mm.
- Self-cal post-hoc rigid drop: P50 11.1 mm (102.7 -> 91.6).
- Vicon-truth+delaycal post-hoc rigid drop: P50 17.8 mm (104.1 -> 86.3).

**Verdict.** Absolute error does not collapse under a better constant time offset, so the current capture-level beta is not the main bottleneck. A truth-fitted per-capture rigid transform removes a large part of the residual, so relative-distance can improve while absolute error remains high: relative distance is mostly insensitive to absolute phase/frame bias.

**Reviewer-survivability.** The relative-distance claim survives as a scale/delay-consistency metric. It must not be sold as absolute dynamic accuracy.

## WHY #3: Why Can One-Baseline Beat Vicon Truth In Median?

**Test run.** Leave-one-static-position-out cross-validation. For each held-out ID, the baseline pair was selected only on the other 23 static IDs, then evaluated on the held-out ID.

**Numbers computed.**

- In-sample best candidate: v1-old/F-H, P50 55.2 / P95 141.0 mm.
- Pre-registered v4-io/E-H reference: P50 58.1 / P95 130.2 mm.
- LOOCV selected-baseline result: P50 60.3 / P95 141.0 mm over 24 held-out positions.
- Vicon-truth+delaycal reference: P50 64.1 / P95 128.4 mm.
- Selected baselines across folds: `v1-old/B-E:2; v1-old/C-G:10; v1-old/F-H:12`.

**Verdict.** The one-baseline median advantage does not disappear under this LOOCV test, but the P95 remains worse than Vicon truth. This means the baseline correction is a useful engineering diagnostic, not a clean headline accuracy claim.

**Reviewer-survivability.** Survives as an ablation showing that one independent baseline can break scale/delay coupling. Does not survive as a field accuracy headline unless the baseline choice is pre-registered.

## WHY #4: Why Rigid RMS 105.4 != Similarity RMS 67.1

**Numbers computed.**

- v4-io all8 reflection-allowed rigid RMS: 105.4 mm.
- v4-io all8 similarity RMS: 67.1 mm.
- Similarity scale: 0.958267; scale delta from 1: -0.041733.

**Verdict.** This is not internally inconsistent. The scale is not 1.0000; it is 0.958267. The 38.3 mm RMS gap is the similarity fit using its only extra DOF: scale.

**Reviewer-survivability.** Survives if the report prints similarity scale to at least four decimals and labels similarity RMS diagnostic-only. If rounded to `1.0`, it will look like a computation bug.

## WHY #5: Why Production P95 Is Much Worse Than T4 Raw Replay

**Numbers computed.**

- Production v4-io static: P50 74.0 / P95 282.1 mm.
- Raw replay T1 P95: 283.7 mm; raw replay T4 P95: 173.9 mm.
- Production minus T4 P95 gap: 108.2 mm.

**Verdict.** Production output tracks the T1/T2-class tail, not the T3/T4 tail. The code default `SolverConfig.method` is T1, and T4 is only explicitly used in the raw replay/ablation scripts.

**Reviewer-survivability.** A paper can report both, but it must define them cleanly: production/current-export result versus achievable deployment estimator result. Do not call 74.0/282.1 the final system limit when the same v4-io layout reaches 69.7/173.9 under T4 replay.

## Headline Recommendation

For the paper headline, use `v4-io/T4 raw replay` as the static deployment-capable claim: `69.7 mm P50 / 173.9 mm P95` 3D, with XY/Z split reported separately. Report `production 74.0/282.1` as the legacy/current exported production-output ablation unless production is actually switched to T4. For dynamic ROTO, the honest claim is `about 105.8 mm P50 / 231.8 mm P95` absolute 3D for self-cal v4-io/T4, and `105.6 mm P50 / 200.4 mm P95` for Vicon-truth+delaycal; this shows ROTO absolute error is not primarily a layout-calibration issue. Demote similarity-scale, one-baseline-best, and per-capture post-hoc rigid results to diagnostic/ablation status.

## Output Tables

- `../tables/why1_dynamic_error_budget.csv`
- `../tables/why2_time_offset_refit.csv`
- `../tables/why2_posthoc_rigid_per_capture.csv`
- `../tables/why2_roto_refit_summary.csv`
- `../tables/why3_one_baseline_loocv.csv`
- `../tables/why3_one_baseline_cv_summary.csv`
- `../tables/why4_procrustes_check.csv`
- `../tables/why5_production_vs_raw_methods.csv`
