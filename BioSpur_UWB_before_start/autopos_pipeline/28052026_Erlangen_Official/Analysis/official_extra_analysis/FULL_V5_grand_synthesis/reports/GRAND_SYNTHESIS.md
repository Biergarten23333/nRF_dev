# Grand Synthesis

Generated: 2026-06-18T02:13:06

## Campaign Overview

The synthesis scanned 23 analysis directories and collected 79 registry entries. All four requested prerequisite completion reports were present.

## Prerequisite Check

| directory | status | completion_file |
| --- | --- | --- |
| FULL_V5_batch3_falsification | OK | FULL_V5_batch3_falsification/reports/FALSIFICATION_COMPLETION.md |
| FULL_V5_roto_deepdive | OK | FULL_V5_roto_deepdive/reports/ROTO_DEEPDIVE_COMPLETION.md |
| FULL_V5_mechanistic_deepdive | OK | FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md |
| FULL_V5_paper_strengthening | OK | FULL_V5_paper_strengthening/reports/PAPER_STRENGTHENING_COMPLETION.md |

## Master Number Registry

| theme | metric_name | value | unit | source_directory |
| --- | --- | --- | --- | --- |
| ANCHOR CALIBRATION | v4-io_sim3_scale | 0.958 | scale | FULL_V5_scale_to_vicon |
| ANCHOR CALIBRATION | v4-io_rigid_anchor_rmse | 105.420 | mm | FULL_V5_scale_to_vicon |
| ANCHOR CALIBRATION | v5-commonmode_sim3_scale | 1.010 | scale | FULL_V5_scale_to_vicon |
| ANCHOR CALIBRATION | v5-commonmode_rigid_anchor_rmse | 62.992 | mm | FULL_V5_scale_to_vicon |
| ANCHOR CALIBRATION | V5_common_mode_c | 111.985 | mm | FULL_V5 |
| ANCHOR CALIBRATION | V5_e_i_full_spread | 27.704 | mm | FULL_V5 |
| ANCHOR CALIBRATION | V5_e_i_max_abs | 15.353 | mm | FULL_V5 |
| TAG DELAY | D_tag_LOO_p50_V5 | 49.621 | mm | FULL_4way_comparison |
| STATIC ACCURACY | V4 production_median_3d | 71.875 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V4 production_rmse_3d | 110.373 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V5 baseline_median_3d | 67.809 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V5 baseline_rmse_3d | 86.400 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V5 improved_median_3d | 56.011 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V5 improved_rmse_3d | 79.482 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V4 improved_median_3d | 54.918 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | V4 improved_rmse_3d | 79.586 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | Vicon improved_median_3d | 56.328 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | Vicon improved_rmse_3d | 81.789 | mm | FULL_V5_followup_validation |
| TAG DELAY | D_tag_LOO_p30_V5 | 32.986 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | best_recalibrated_percentile_cell | 51.968 | mm | FULL_V5_followup_validation |
| STATIC ACCURACY | bootstrap_median_3d_mean | 56.980 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | bootstrap_median_3d_ci95_low | 54.322 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | bootstrap_median_3d_ci95_high | 63.749 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | bootstrap_rmse_mean | 79.744 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | bootstrap_rmse_ci95_low | 76.132 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | bootstrap_rmse_ci95_high | 85.947 | mm | FULL_V5_overnight_batch2 |
| STATIC ACCURACY | nested_cv_height_mean_test_median | 82.925 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | nested_cv_quadrant_mean_test_median | 88.042 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | nested_cv_spatial6_mean_test_median | 94.250 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | mean_optimism_gap_honest_minus_apparent | 9.568 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | std_optimism_gap | 29.610 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | corrected_headline_v4_54p9 | 64.486 | mm | FULL_V5_batch3_falsification |
| STATIC ACCURACY | corrected_headline_v5_56p0 | 65.579 | mm | FULL_V5_batch3_falsification |
| TAG DELAY | V4_CV4_range_residual_tier_spread | 11.770 | mm | FULL_V5_extended_mechanism_ablations |
| TAG DELAY | V5_CV5_range_residual_tier_spread | 7.444 | mm | FULL_V5_extended_mechanism_ablations |
| CANCELLATION VALLEY | joint_morph_global_min_alpha | 0.150 | alpha | FULL_V5_extended_mechanism_ablations |
| CANCELLATION VALLEY | joint_morph_global_min_median | 56.365 | mm | FULL_V5_extended_mechanism_ablations |
| CANCELLATION VALLEY | profile_alpha_dtag_min_alpha | 0.980 | alpha | FULL_V5_batch3_falsification |
| CANCELLATION VALLEY | profile_alpha_dtag_min_dtag | 88.000 | mm | FULL_V5_batch3_falsification |
| CANCELLATION VALLEY | profile_alpha_dtag_min_median | 72.609 | mm | FULL_V5_batch3_falsification |
| CANCELLATION VALLEY | nullspace_perturbation_ratio_median | 0.267 | ratio | FULL_V5_batch3_falsification |
| IDENTIFIABILITY | fisher_weakest_eigenvalue | 0.000 | eigenvalue | FULL_V5_GPU_tier1 |
| NLOS | shapley_D | 1242.886 | score | FULL_V5_GPU_discovery |
| NLOS | shapley_F | 1229.441 | score | FULL_V5_GPU_discovery |
| NLOS | nlos_detector_random_split_pr_auc | 0.949 | PR-AUC | FULL_V5_GPU_discovery |
| NLOS | nlos_detector_leave_one_anchor_out_best_pr_auc | 0.548 | PR-AUC | FULL_V5_batch3_falsification |
| NLOS | nlos_detector_leave_one_position_out_best_pr_auc | 0.746 | PR-AUC | FULL_V5_batch3_falsification |
| NLOS | nlos_detector_leave_one_height_out_best_pr_auc | 0.372 | PR-AUC | FULL_V5_batch3_falsification |
| NLOS | student_t_bic_winner | M2_student_t | model | FULL_V5_GPU_discovery |
| DYNAMIC ROTO | E_current_anchor_bridge_existing_beta_overall_median | 101.485 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | F_time_corrected_SE3_overall_median | 82.516 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | D_Sim3_existing_beta_overall_median | 74.264 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | roto_independent_median | 101.084 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | roto_joint_projection_median | 280.602 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_D_tag mismatch | 22.860 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_Motion blur | 6.392 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_Time alignment recoverable | 0.716 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_Range aggregation / dynamic single-frame | 0.000 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_Unexplained | 15.505 | mm | FULL_V5_roto_deepdive |
| DYNAMIC ROTO | gap_TOTAL static-to-dynamic gap | 45.474 | mm | FULL_V5_roto_deepdive |
| TRANSFERABILITY | mc_P_V5_lt_V4_corrected_adversarial | 0.300 | probability | FULL_V5_overnight_batch2 |
| TRANSFERABILITY | aa_at_mean_asymmetry | -4.664 | mm | FULL_V5_GPU_discovery |
| IDENTIFIABILITY | anchor_count_4_redundancy | -4.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_4_mean_median_3d | 141.468 | mm | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_5_redundancy | -4.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_5_mean_median_3d | 108.976 | mm | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_6_redundancy | -3.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_6_mean_median_3d | 89.770 | mm | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_7_redundancy | -1.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_7_mean_median_3d | 79.501 | mm | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_8_redundancy | 2.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_8_mean_median_3d | 68.733 | mm | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_9_redundancy | 6.000 | count | FULL_V5_mechanistic_deepdive |
| IDENTIFIABILITY | anchor_count_9_mean_median_3d | 60.665 | mm | FULL_V5_mechanistic_deepdive |
| ERROR DECOMPOSITION | V4+C_V4+D_LOO_mean_signed_radial | -7.754 | mm | FULL_V5_paper_strengthening |
| ERROR DECOMPOSITION | V5+C_V5+D_LOO_mean_signed_radial | -4.835 | mm | FULL_V5_paper_strengthening |
| ERROR DECOMPOSITION | Vicon+C_cm+D_LOO_mean_signed_radial | -5.095 | mm | FULL_V5_paper_strengthening |
| ERROR DECOMPOSITION | strongest_ei_correlation_predictor | layer_binary | name | FULL_V5_paper_strengthening |
| ERROR DECOMPOSITION | strongest_ei_correlation_r | -0.460 | r | FULL_V5_paper_strengthening |

## Consistency Audit

| metric | value_1 | value_2 | discrepancy | status |
| --- | --- | --- | --- | --- |
| V5+C_V5+D_LOO median_3d | 67.849 | 67.849 | 0.000 | OK |
| V5+C_V5+D_LOO median_3d | 67.849 | 67.809 | 0.039 | OK |
| V4+C_V4+D_LOO median_3d | 57.921 | 57.921 | 0.000 | OK |
| D_tag LOO | 49.621 | 49.621 | 0.000 | OK |
| Shapley D | 1242.886 | 1242.886 | 0.000 | OK |
| Shapley F | 1229.441 | 1229.441 | 0.000 | OK |
| NLOS PR-AUC best | 0.952 | 0.949 | 0.004 | OK |

## Claim Evidence Matrix

| claim_id | claim_text | level | recommended_paper_wording |
| --- | --- | --- | --- |
| 1 | V5 fixes V4's scale leak (0.958 -> 1.010) | A | V5 corrects the anchor-side scale defect on this campaign. |
| 2 | V5 has more stable per-height D_tag | B | V5 reduces some geometry-induced tag-delay aliasing, but stability depends on the criterion. |
| 3 | V4 gives better single-dataset positioning than V5 | A | V4 is the empirical static median winner on this 24-position campaign. |
| 4 | The reason is scale-delay-NLOS cancellation | B | The lower V4 error is consistent with beneficial cancellation rather than proven by one statistic. |
| 5 | Vicon oracle worse than self-cal proves cancellation | C | The Vicon result is compatible with cancellation but not uniquely diagnostic. |
| 6 | Vicon worse could be phase-center offset | B | Phase-center mismatch is a plausible alternative and should be stated. |
| 7 | p30 improvement is another cancellation | B | p30 is a strong batch-processing hypothesis, not a universal correction. |
| 8 | Every post-processing improvement benefits V4 more than V5 | D | Do not claim universal superiority; report the tested comparison. |
| 9 | Fisher eigenvalue 1e-6 proves weak identifiability | A | The calibration has a measurable weak direction. |
| 10 | D/F are NLOS-heavy but geometrically essential | A | D/F are not simply removable outliers. |
| 11 | NLOS detectable from range statistics without CIR | B | Range statistics contain NLOS signal, but deployment generalization is unproven. |
| 12 | NLOS detector generalizes across positions/anchors | D | Do not claim generalization yet. |
| 13 | Student-t is the correct noise model | B | Student-t best describes this residual distribution. |
| 14 | V5 transfers better to new rooms | C | V5 is expected to transfer better, but this needs direct validation. |
| 15 | MC transfer result has V4 solver fidelity caveat | A | State the caveat explicitly. |
| 16 | D_tag is device-specific | C | Treat per-device D_tag as likely, not proven. |
| 17 | p30 does not transfer to dynamic | B | p30 helped static batch ranges but not ROTO enough to change the dynamic floor. |
| 18 | ROTO accuracy is ~101 mm best-fit aligned | A | Report as BEST-FIT-ALIGNED only. |
| 19 | Static-dynamic gap is ~40 mm | A | The dynamic floor remains about 45 mm above static best. |
| 20 | 24 positions insufficient for learned methods | B | The current campaign is too small for strong learned-method claims. |
| 21 | AA-AT asymmetry is small | A | AA/AT asymmetry is small in this dataset. |
| 22 | Rigid body constraint improves ROTO | D | Do not claim improvement from the tested rigid projection. |
| 23 | Headline numbers survive nested CV | C | Hard nested CV weakens, rather than confirms, aggressive headline claims. |
| 24 | Winner's curse gap is < X mm | B | Use corrected medians for paper headline sensitivity. |
| 25 | Cancellation valley has specific radial mechanism | C | Radial decomposition is suggestive but not a stand-alone proof. |

## Final Corrected Headline Table

| Variant | median_3d | P95 | RMSE | nested_CV_median | winners_curse_corrected_median | bootstrap_CI |
| --- | --- | --- | --- | --- | --- | --- |
| V4 production | 71.875 | 175.996 | 110.373 |  |  |  |
| V5 baseline | 67.809 | 160.509 | 86.400 |  |  |  |
| V5 improved | 56.011 | 143.120 | 79.482 |  | 65.579 | [54.3, 63.7] |
| V4 improved | 54.918 | 154.784 | 79.586 |  | 64.487 |  |
| Vicon improved | 56.328 | 147.948 | 81.789 |  |  |  |
| Nested CV selected (height) |  |  |  | 82.925 |  |  |
| Nested CV selected (quadrant) |  |  |  | 88.042 |  |  |
| Nested CV selected (spatial6) |  |  |  | 94.250 |  |  |
| ROTO V5 raw/current best-fit | 101.485 | 214.369 | 126.226 |  |  |  |
| ROTO time-corrected SE3 | 82.516 | 185.207 | 103.746 |  |  |  |
| ROTO rigid-body projection | 280.602 |  | 315.974 |  |  |  |

# Narrative Summary

V5 fixes the anchor-side defect that motivated the entire analysis campaign. The V4 self-calibrated layout has a Sim3 scale of 0.958 and a rigid anchor RMSE of about 105 mm against Vicon, whereas the V5 common-mode layout has a Sim3 scale of 1.010 and a rigid RMSE of about 63 mm. The V5 delay model also moves the bulk anchor bias into an explicit common-mode term of about 112 mm, leaving a per-anchor residual spread of about 28 mm. These numbers support a Level A claim that V5 corrects the metric scale leak observed in V4 on this campaign, although they do not by themselves prove better positioning on every dataset.

The static positioning story is more subtle. On the original p50 range aggregation, V4+C_V4+D_LOO gives a 57.9 mm median 3D error, while V5+C_V5+D_LOO gives about 67.8 mm. Under the best-practice p30/inverse-RMS follow-up, V4 remains slightly ahead at 54.9 mm, V5 reaches 56.0 mm, and the Vicon-anchor control reaches 56.3 mm. That ordering is consistent with beneficial cancellation: V4's compressed scale partly offsets positive range bias from NLOS and tag-delay effects. The morph valley has a global minimum near alpha=0.15 with a median of 56.4 mm, and the profile-likelihood scan also shows a broad alpha/D_tag valley. The radial decomposition is suggestive rather than decisive, because V4 and V5 mean signed radial errors are both slightly inward and not statistically strong.

The falsification batch weakens aggressive headline claims. Nested CV test medians ranged from 82.9 to 94.2 mm across hard split types, and the estimated winner's-curse optimism gap averaged about 9.6 mm. The corrected medians for the V4 and V5 improved rows are therefore about 64.5 and 65.6 mm, respectively. These tests do not invalidate the physical V5 result, but they do mean the paper should avoid presenting any single in-sample best number as a deployment guarantee. The defensible claim is that V4 is the empirical winner on this 24-position campaign, while V5 is the more physically correct anchor calibration.

The remaining accuracy floor is dominated by structured range error rather than Gaussian noise. The Student-t residual model is the BIC winner, D/F have high Shapley values and large residual fingerprints, and the NLOS classifier reaches about 0.95 PR-AUC in random splits. However, hard generalization tests are much weaker: leave-one-anchor and leave-one-height PR-AUC values collapse relative to random split performance. Thus range statistics contain useful NLOS information, but the detector should be framed as exploratory until validated on independent rooms or anchors.

Dynamic ROTO tracking remains a separate limitation. The current anchor-bridge best-fit-aligned ROTO median is about 101.5 mm, and a time-corrected SE(3) evaluation gives about 82.5 mm under a more permissive alignment. The tested rigid-body projection did not improve tracking; it worsened the median to about 281 mm, so the paper should not claim a rigid-constraint improvement. The gap decomposition attributes roughly 23 mm to likely tag-delay mismatch, 6 mm to motion blur under the nominal timing assumption, less than 1 mm to recoverable time offset, and about 15.5 mm to unexplained residual structure. These components are not orthogonal, but they make clear that the dynamic floor is not solved by p30 aggregation alone.

The transferability case remains plausible but unproven. V5 should transfer better because its geometry is metric-correct and because the V4 solution relies on dataset-specific cancellation. The initial GPU Monte Carlo result was too strong, and the corrected adversarial-room analysis reduced the evidence to a caveated transfer hypothesis rather than proof. AA-AT asymmetry is small at about -4.7 mm, which supports the use of the self-calibration range model in this campaign. A real cross-room capture is still required for a Level A transfer claim.

The practical deployment recommendation is therefore conservative. Use the V5 common-mode calibration with the 20 mm residual regularization as the default geometry, calibrate D_tag per device using a small number of known positions, retain robust or Student-t-like losses, and use p30 or similar lower-percentile aggregation only for static batch processing after validation. The best current static processing recipe reaches about 56 mm on this campaign, but the paper should report both naive and corrected estimates. Future work should prioritize an independent room, CIR-based NLOS labels, a physical antenna phase-centre measurement, and a 9th-anchor experiment to improve identifiability.

## Directory Index

| directory | n_tables | n_figures | n_reports | tasks_ok | tasks_fail |
| --- | --- | --- | --- | --- | --- |
| FULL | 78 | 0 | 0 | 0 | 0 |
| FULL_4way_comparison | 14 | 0 | 4 | 0 | 0 |
| FULL_AutoPos_align_to_Vicon | 3 | 0 | 1 | 0 | 0 |
| FULL_AutoPos_one_baseline_scale_correction | 2 | 0 | 1 | 0 | 0 |
| FULL_AutoPos_scale_to_vicon | 2 | 0 | 1 | 0 | 0 |
| FULL_V4_vs_V5_final | 8 | 0 | 1 | 0 | 0 |
| FULL_V5 | 16 | 0 | 1 | 0 | 0 |
| FULL_V5_GPU_discovery | 49 | 15 | 19 | 17 | 0 |
| FULL_V5_GPU_tier1 | 21 | 7 | 7 | 6 | 0 |
| FULL_V5_align_to_Vicon | 5 | 0 | 1 | 0 | 0 |
| FULL_V5_batch3_falsification | 18 | 8 | 9 | 6 | 0 |
| FULL_V5_extended_mechanism_ablations | 34 | 3 | 1 | 0 | 0 |
| FULL_V5_followup_validation | 15 | 0 | 7 | 0 | 0 |
| FULL_V5_grand_synthesis | 7 | 0 | 9 | 0 | 0 |
| FULL_V5_mechanism_ablations | 0 | 0 | 1 | 0 | 0 |
| FULL_V5_mechanistic_deepdive | 27 | 6 | 11 | 10 | 0 |
| FULL_V5_one_baseline_scale_correction | 4 | 0 | 1 | 0 | 0 |
| FULL_V5_overnight_batch2 | 21 | 10 | 11 | 9 | 0 |
| FULL_V5_paper_strengthening | 17 | 10 | 12 | 10 | 0 |
| FULL_V5_roto_deepdive | 18 | 7 | 7 | 6 | 0 |
| FULL_V5_scale_to_vicon | 3 | 0 | 1 | 0 | 0 |
| FULL_transfer_matrix | 8 | 0 | 1 | 0 | 0 |
| old-G_DO_NOT_ANALYSE_ANYMORE | 64 | 0 | 2 | 0 | 0 |

## Remaining Gaps

| item | status | action_needed | priority |
| --- | --- | --- | --- |
| All claims at Level A or B | PARTIAL | Downgrade Level C/D claims in paper. | MUST-HAVE |
| Nested CV confirms headlines | PARTIAL | Use nested CV as falsification, not confirmation. | MUST-HAVE |
| Winner's curse gap < 5mm | FAILED | Mean gap is about 9.6 mm; report corrected medians. | MUST-HAVE |
| NLOS detector generalizes | FAILED | Need leave-room/leave-anchor validation before deployment claim. | MUST-HAVE |
| Cancellation valley visualized | DONE | Use in paper. | MUST-HAVE |
| ROTO gap explained | PARTIAL | Components are proxies and not orthogonal. | NICE-TO-HAVE |
| Phase-center alternative addressed | PARTIAL | Needs physical antenna offset measurement. | MUST-HAVE |
| Error budget decomposed | DONE | Include caveat on interactions. | MUST-HAVE |
| Fisher eigenvector interpreted | DONE | Use with nullspace check. | MUST-HAVE |
| Publication figures complete | DONE | Select final subset. | NICE-TO-HAVE |
| LaTeX tables complete | DONE | Check formatting in main paper. | NICE-TO-HAVE |
| LaTeX draft sections available | DONE | Edit into final manuscript. | OPTIONAL |

## Recommended Paper Structure

Use the paper structure from the previous outline, but make the Results order: anchor-side scale fix, static accuracy with corrected table, cancellation/identifiability, NLOS residual structure, dynamic ROTO limitation, and deployment recommendations. Keep transferability in Discussion unless an independent-room experiment is added.
