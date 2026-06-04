# Measurement Reviewer Audit: AutoPos × OptiTrack/Vicon

This is a skeptical data interrogation of the corrected FULL analysis. It is not a replacement for the main report.

## WHY #1: Why Does ROTO Not Collapse When Static Does?

**Numbers computed.**

- Self-cal v4-io/T4 ROTO sample P50 after the existing offset: 102.6 mm; after 1 ms local absolute-error refit: 102.7 mm.
- The corresponding track-median P50 values are 105.8 -> 106.0 mm. The paper headline uses track-median; this audit's pooled diagnostics also show sample-pooled values.
- Vicon-truth+delaycal/T4 ROTO sample P50 after the same test: 104.5 -> 104.1 mm.
- Median OptiTrack linear speed during ROTO is 639.1 mm/s (P95 906.0 mm/s). A 0.8 ms broadcast window therefore contributes only 0.51 mm median / 0.72 mm P95 displacement.
- Post-hoc per-capture rigid registration reduces self-cal sample P50 from 102.7 to 91.6 mm.

**Non-circular budget.** These are descriptive median/P95 quantities, not independent RMS components; they must not be quadrature-summed.

- excluded constant-offset effect: -0.06 mm P50 (102.6 -> 102.7 mm sample-pooled).
- excluded 0.8 ms protocol-window motion: 0.72 mm P95 displacement.
- oracle post-hoc rigid removable term: 11.1 mm P50 (truth-fitted, diagnostic only).
- unattributed per-sample residual after that oracle rigid fit: 91.6 mm P50.

**Verdict.** The 0.8 ms protocol-window motion is negligible, and constant capture-level time offset is not the bottleneck. The oracle rigid fit removes only about 11 mm P50; the dominant remaining term is the ~92 mm per-sample residual, not a clean spatial registration error.

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
- Legacy no-groundtruth ROTO self-consistency, FULL self-cal v4-io/T4: dR RMS 25.9 mm; abs dR median/P95 26.0/37.8 mm; turn-center repeatability median/P95 13.7/24.7 mm; inner/outer center separation median/P95 37.6/55.4 mm.
- The same legacy dR RMS across FULL controls: self-cal 25.9 mm; Vicon-truth+delaycal 18.0 mm; scale-to-Vicon+delaycal 15.6 mm; one-baseline E-H+delaycal 13.4 mm; best one-baseline solver-delay row nan mm.
- New OptiTrack/Vicon circle-level absolute metric: turn-center absolute 3D RMS, 34 tag-tracks: FULL self-cal 72.1 mm; Vicon-truth+delaycal 72.7 mm; scale-to-Vicon+delaycal 76.7 mm; one-baseline E-H+delaycal 77.1 mm; best one-baseline solver-delay row 71.4 mm.
- For comparison, the corresponding FULL self-cal sample-pooled absolute 3D RMSE is 141.3 mm, and Vicon-truth+delaycal sample-pooled absolute 3D RMSE is 125.4 mm.

**Verdict.** Absolute error does not collapse under a better constant time offset, so the current capture-level beta is not the main bottleneck. The old no-groundtruth circle metrics remain useful but must be labeled as self-consistency: they test radius separation, per-turn center repeatability, and two-tag center agreement. The 72-77 mm number is the new OptiTrack/Vicon absolute turn-center error, not the old turn-center repeatability metric. Thus the traditional circle-level ROTO metrics survive as relative/physical consistency checks, while absolute per-sample dynamic accuracy remains around the 105 mm P50 / 125-141 mm RMSE class.

**Reviewer-survivability.** The relative-distance claim survives as a scale/delay-consistency metric. It must not be sold as absolute dynamic accuracy.

## WHY #6: Does Intra-Capture Clock Skew Explain The ROTO Residual?

**Test run.** For each capture, both wand tags share one affine time model:

`t_query = uwb_time_s + beta + alpha * (uwb_time_s - t_ref)`

with `t_ref` at the capture start. Alpha is reported in ppm. Search used coarse ±300 ppm / 10 ppm with beta ±50 ms / 1 ms, then fine ±20 ppm / 1 ppm with beta ±5 ms / 0.5 ms.

**Numbers computed.**

- Self-cal sample-pooled P50 beta0 / const-best / skew-best: 102.6 / 102.7 / 102.6 mm.
- Self-cal track-median P50 beta0 / const-best / skew-best: 105.8 / 106.0 / 105.5 mm.
- Self-cal alpha median ± IQR: -112.0 ± 228.0 ppm; sign consistency 70.6%.
- Vicon-truth+delaycal sample-pooled P50 const-best / skew-best: 104.1 / 104.1 mm; alpha median -48.0 ppm.
- Timing leverage bound: at the observed median ROTO speed of 639.1 mm/s, explaining the remaining 91.6 mm residual would require about 143 ms equivalent timing error.
- If that were interpreted as a linear skew accumulated across a full 120 s capture, it would be about 1195 ppm; over any shorter local interval the required ppm is even larger.

**Verdict.** `SKEW_EXCLUDED`.

**Consequence for the paper.** Do not claim the clocks are synchronized to a ppm bound; that is not what this test measures. The valid claim is stronger for this metric: offset/skew/jitter have no material leverage on the observed 90 mm-class dynamic residual.

**Reviewer-survivability.** This survives as the correct falsification test because it checks both error reduction and cross-capture alpha consistency. If alpha is unstable, a lower skew-fit error is not enough evidence for physical clock drift; here the error surface is effectively flat.

## WHY #7: Is ROTO Mostly Un-Averaged Single-Shot Static Precision?

**Test run.** Replayed the raw static frames through the same `v4-io/T4` and `Vicon-truth+delaycal/T4` layouts, without per-position averaging, then compared those per-frame errors to ROTO per-sample errors. The same test also bins both distributions by range-only GDOP deciles.

**Numbers computed.**

- Self-cal static single-shot 3D P50/P95: 89.6 / 259.3 mm; static per-position aggregate: 69.7 / 173.9 mm.
- Self-cal ROTO single-shot 3D P50/P95: 102.6 / 256.9 mm.
- Self-cal dynamic excess over static single-shot: 3D P50 13.1 mm; XZ P50 17.3 mm; Y P50 -4.3 mm.
- Self-cal averaging benefit from dwell-time static aggregation: 3D P50 19.9 mm; XZ P50 11.4 mm; Y P50 5.9 mm.
- Self-cal GDOP-bin median absolute static-vs-ROTO P50 gap: 22.2 mm across 4 shared bins; bins above 15 mm gap: 3.
- Vicon-truth+delaycal dynamic excess over static single-shot: 3D P50 24.9 mm; XZ P50 11.5 mm; Y P50 16.7 mm.

**Verdict.** Self-cal `DYNAMIC_EXCESS_PRESENT`; Vicon-truth `DYNAMIC_EXCESS_PRESENT`.

**Consequence for the paper.** Pooled 3D P50 is nearly explained by lost static averaging, but XZ and GDOP-conditioned bins retain a small dynamic excess; report this as a limited real dynamic term, not a timing artifact.

**Reviewer-survivability.** This is the right next falsification test because it compares like with like: per-sample dynamic error versus per-sample static error, with GDOP controlled. If dynamic excess remains, it is a real dynamic/orientation/ranging term rather than a timing artifact or a lost-averaging artifact.

## WHY #8: Is The WHY #7 Residual A Per-Tag Delay Mismatch?

**Tests run.**

- Aggregated static bias/scatter from the WHY #7 per-position table.
- Read raw ROTO `tr_all.csv` per-anchor ranges and computed `measured - geometric truth` residuals at the fixed validated ROTO beta.
- Also computed model-corrected residuals `measured - geometry - d_anchor - tag_delay`; the uniformity gate uses this corrected quantity because the question is per-tag delay, not anchor-delay pattern.
- Re-solved ROTO after subtracting per-tag constant bias only if the residual gate passed.

**Numbers computed.**

- Static bias/scatter split, self-cal: bias 3D/XZ/Y medians 72.7 / 37.4 / 61.9 mm; scatter RMS 3D/XZ/Y medians 67.1 / 35.2 / 45.8 mm.
- BS2DCE model-corrected range residual median/IQR: 67.6 / 16.7 mm; uniform gate=True.
- BSDC91 model-corrected range residual median/IQR: 22.9 / 24.0 mm; uniform gate=False.
- Bias-removal re-solve, self-cal: dynamic excess 3D P50 13.1 -> 5.9 mm; GDOP-bin gap 22.2 -> 16.1 mm.
- GDOP overlap, self-cal: static P5/P50/P95 1.114 / 1.140 / 1.197; ROTO P5/P50/P95 1.156 / 1.180 / 1.198.
- Shared GDOP bins used by WHY #7: 4; thin bins with either side n<30: 0; gating parity: MATCH.

**Verdict.** `TAG_DELAY_PARTIAL`.

**Consequence for the paper.** Per-tag delay removes part of the residual, but a region/dynamic component remains.

**Reviewer-survivability.** The per-tag bias estimate is diagnostic because it uses Vicon truth here. Unlike post-hoc rigid fitting, however, a constant per-tag residual delay is deployable as a firmware antenna-delay trim if confirmed on an independent known baseline.

## WHY #9: Raw Tag × Anchor Residual Decomposition

**Terminology.**

- The firmware antenna-delay setting is `16436` DTU, TX=RX on all devices, with no OTP antenna-delay read in this firmware.
- Solver `d_anchor_mm` and `tag_delay_mm` are layout-level residual delay corrections: software terms fitted on top of data already generated with the firmware-16436 setting.
- In this decomposition, `grand` is common-mode firmware-16436 miscalibration plus global scale plus mean-coordinate gauge. Only tag/anchor differences are identifiable.

**Tests run.**

- Built a raw `measured - geometric truth` median table over three tags and eight anchors for both self-cal and Vicon-truth scenarios.
- Ran NaN-aware median polish to estimate `grand`, `tag_main`, `anchor_main`, and tag-by-anchor interaction without subtracting solver `d_anchor_mm` or `tag_delay_mm`.
- Checked cross-scenario stability of pairwise `tag_main` differences, then disambiguated the anchor flag with within-scenario `anchor_main` versus each scenario's own `d_anchor_mm`.

**Numbers computed.**

- Self-cal grand common-mode: 64.7 mm; Vicon-truth grand common-mode: 134.9 mm.
- Self-cal interaction median/max abs: 0.9 / 12.8 mm; Vicon-truth: 2.4 / 14.2 mm.
- `BS2DCE - BSF66F` tag_main difference: self-cal 31.0 mm; Vicon-truth 34.6 mm; cross-scenario |delta| 3.6 mm.
- `BSDC91 - BSF66F` tag_main difference: self-cal -12.7 mm; Vicon-truth -11.7 mm; cross-scenario |delta| 1.0 mm.
- `BS2DCE - BSDC91` tag_main difference: self-cal 43.7 mm; Vicon-truth 46.3 mm; cross-scenario |delta| 2.6 mm.
- Anchor consistency against solver `d_anchor_mm` relative to A: verdict `ANCHOR_CONSISTENCY_FLAGGED`, correlation -0.54, median absolute gap 99.7 mm.
- Within-scenario anchor check, self-cal: corr 0.31, median abs gap 11.1 mm; Vicon-truth: corr 0.46, median abs gap 21.6 mm.
- Coordinate/scale absorption signature, self `anchor_main_rel_A` minus Vicon `anchor_main_rel_A`: median abs 113.2 mm; B/C/D 111.4 / 114.9 / 98.8 mm.
- Vicon `anchor_main` relative to A for B/C/D: -76.1 / -91.6 / -32.6 mm; solver `d_anchor_mm` relative to A for B/C/D: 37.1 / 60.0 / 60.0 mm.
- Anchor disambiguation verdict: `ANCHOR_DECOMP_INCONSISTENT_INVESTIGATE`.

**Verdict.** `PER_TAG_PHYSICAL_STABLE`.

**Consequence for the paper.** Pairwise tag_main differences are stable and are the deployable per-device residual trim targets; confirm with an independent known-baseline loop before changing firmware antenna-delay settings. At least one within-scenario anchor check fails; resolve a centering/sign/reference bug before interpreting the cross-scenario flag.

**Reviewer-survivability.** Quote only differences. For tags, use pairwise `tag_main` differences; for anchors, use `anchor_main` relative to A and carry the anchor-consistency verdict with it. Absolute per-anchor or per-tag delay still requires a known baseline or inter-anchor ranging.

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

## Report Coverage Check

**Reviewer-audit coverage.** This report now covers every table generated under `reviewer_audit/tables`: WHY #1/#2 dynamic time, rigid, and circle metrics; WHY #3 one-baseline LOOCV; WHY #4 Procrustes scale; WHY #5 production-vs-raw; WHY #6 clock skew; WHY #7 single-shot/GDOP/static-bias decomposition; WHY #8 bias/scatter, tag-delay, and GDOP-overlap checks; and WHY #9 raw tag-by-anchor residual decomposition.

**What is summarized rather than printed row-by-row.** Large per-capture, per-anchor, per-tag, and full solver-matrix tables are intentionally indexed in Output Tables rather than expanded in text. They are comparison evidence, but not headline claims.

**Separate FULL diagnostics.** The broader FULL directory also contains DOP grids, Monte Carlo keep-k/drop-anchor runs, temporal drift checks, pair residual diagnostics, and anchor-health scorecards. Those are not missing from this reviewer audit; they are robustness/sensor-health appendices and should be pulled into the paper only if a reviewer asks about geometry sensitivity, anchor removal, or acquisition drift.

## Headline Recommendation

For the paper headline, use `v4-io/T4 raw replay` as the static deployment-capable claim: `69.7 mm P50 / 173.9 mm P95` 3D, with XY/Z split reported separately. Report `production 74.0/282.1` as the legacy/current exported production-output ablation unless production is actually switched to T4. For dynamic ROTO, the honest claim is `about 105.8 mm P50 / 231.8 mm P95` absolute 3D for self-cal v4-io/T4, and `105.6 mm P50 / 200.4 mm P95` for Vicon-truth+delaycal; this shows ROTO absolute error is not primarily a layout-calibration issue. Demote similarity-scale, one-baseline-best, and per-capture post-hoc rigid results to diagnostic/ablation status.

## Output Tables

- `../tables/why1_dynamic_error_budget.csv`
- `../tables/why2_time_offset_refit.csv`
- `../tables/why2_posthoc_rigid_per_capture.csv`
- `../tables/why2_roto_refit_summary.csv`
- `../tables/why2_roto_circle_metrics.csv`
- `../tables/why3_one_baseline_loocv.csv`
- `../tables/why3_one_baseline_cv_summary.csv`
- `../tables/why4_procrustes_check.csv`
- `../tables/why5_production_vs_raw_methods.csv`
- `../tables/why6_time_skew_per_capture.csv`
- `../tables/why6_time_skew_summary.csv`
- `../tables/why7_single_shot_summary.csv`
- `../tables/why7_error_vs_gdop.csv`
- `../tables/why7_static_bias_scatter.csv`
- `../tables/why8_bias_scatter_summary.csv`
- `../tables/why8_tag_range_residuals.csv`
- `../tables/why8_tag_delay_resolve_summary.csv`
- `../tables/why8_gdop_overlap.csv`
- `../tables/why9_residual_cells.csv`
- `../tables/why9_twoway_effects.csv`
- `../tables/why9_stability_summary.csv`
- `../tables/why9_anchor_consistency.csv`
