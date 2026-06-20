# Comprehensive Experimental Report V2: Erlangen 28-May-2026 V5 Analysis Campaign

Generated: 2026-06-19T11:23:09

This internal report updates the V4/V5 AutoPos analysis record with the raw-frame brute-force campaign and the anchor lower_trim blind experiment. It is a technical record, not a paper draft.

## 0. Executive Summary

This V2 report updates the internal Erlangen 28-May-2026 V5 analysis record by adding
four post-report directories: `FULL_V5_rawframe_bruteforce`, `FULL_V5_rawframe_bruteforce_v2`,
`FULL_V5_rawframe_bruteforce_v3`, and `FULL_V5_anchor_lower_trim`. The report now covers
23 analysis directories, combining the original 19-directory campaign with the raw-frame
LOS extraction campaign and the blind inter-anchor lower-trim experiment [source:
tables/report_source_inventory_v2.csv]. The update keeps the same measurement dataset:
24 static Vicon positions, 17 ROTO captures, DWM1001C UWB range data, and Vicon/OptiTrack
ground truth [source: FULL_V5/tables/static_summary_DLOO.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

The main new result is a raw-frame static breakthrough. The v2 data-discovery run confirmed
that the static captures contain true per-frame `tr_all.csv` data: 230,544 raw rows,
228,265 valid rows, and a ratio of 1.001 to the nominal 24 x 8 x 1200
frame-anchor observations [source: FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv].
The true brute-force v3 run then tested 107,448 estimator/loss/geometry/D_tag cells and
identified an honest LOO winner: lower_trim_20 over raw tag-anchor range histograms,
V5 geometry, and Huber30, with 44.5 mm median 3D error,
164.1 mm P95, and 81.5 mm RMSE [source:
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The v3 oracle lower bound
is 44.6 mm, so the blind lower_trim_20 estimator lands
within 0.1 mm of the oracle median on the campaign-level ladder [source:
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].

This changes the interpretation of the earlier p30 result. The earlier p30/inverse-RMS
rows showed that lower range percentiles could reduce static error, but the follow-up
analysis correctly treated those rows as post-selected and not automatically deployable
[source: FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_followup_validation/tables/f6_final_comparison.csv]. The raw-frame v3 result
uses the actual per-link histograms rather than a single percentile diagnostic. It
therefore supports a more specific claim: static tag-anchor range histograms contain
a recoverable LOS-side component, and lower_trim_20 is the current zero-parameter
extractor for that component on this dataset [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The new result also reframes the V4/V5 ranking. With p50 ranges and NLOS bias still
present, V4+C_V4+D_LOO remains the empirical static median winner at 57.9 mm versus
67.8 mm for V5+C_V5+D_LOO [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
With raw-frame LOS extraction, the best honest V5 row is 44.5
mm, while the best honest V4 geometry row in the raw-frame v3 ranking is
53.9 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. This independently
supports the cancellation interpretation: when right-tail tag-anchor NLOS bias is
reduced, V5's metric-correct geometry becomes the better positioning geometry.

The anchor lower-trim blind experiment answered a different question. It tested whether
the same lower-tail estimator should be applied to inter-anchor self-calibration ranges.
The answer is no for this dataset. Inter-anchor raw distributions contain 56,000
valid rows, a median of 2000 frames per pair, and mean skewness
0.063; the distributions are nearly symmetric rather than strongly right-tailed
[source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. The best
lower_trim_20-anchor row is 46.4 mm, worse than the
current p50-anchor control at 44.5 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. The practical rule is now
split by measurement type: use p50/median for inter-anchor self-calibration in this
campaign, and use lower_trim_20 for static tag-anchor histograms when enough frames are
available.

The anchor lower-trim experiment also confirmed the earlier e_i caution. The current
p50/e_reg20 control is 44.5 mm under the raw-frame static
tag estimator, while p50 with e_i=0 is 43.2 mm and p50 with
e_reg=5 is 43.2 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. The improvement is small:
the paired bootstrap gives P(new wins)=0.659 and a 95% improvement
interval of -3.6 to 4.0 mm [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv]. The report therefore treats
e_i=0 as the current best engineering candidate, not as a statistically locked result.

Top-level findings after this update:

- V5 still fixes the anchor-side scale leak: V4 Sim3 scale is 0.958 and V5 Sim3
  scale is 1.010; rigid anchor RMSE improves from 105.4 mm to 63.0 mm [source:
  FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
- V4 still wins the p50 static LOO baseline on this campaign: 57.9 mm for V4+LOO
  versus 67.8 mm for V5+LOO [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- The best honest raw-frame static row is lower_trim_20 + V5 + Huber30 at
  44.5 mm LOO, with a wide bootstrap interval of
  33.4-82.8 mm [source:
  FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
  FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].
- The raw-frame oracle lower bound is 44.6 mm and the
  honest lower_trim_20 row matches it at the median level [source:
  FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].
- Inter-anchor lower_trim_20 is a negative result: the inter-anchor distributions are
  nearly symmetric and lower-tail trimming worsens the anchor refit [source:
  FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
  FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].
- ROTO remains a dynamic limitation: the conservative V5 best-fit-aligned median is
  101.5 mm, and the raw-frame lower_trim_20 static result does not apply to single-frame
  dynamic ranges [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv;
  FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].

### Updated Locked Headline Table

| Row | Variant | Median 3D mm | P95 mm | RMSE mm | Evaluation | New | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | V4 production | 71.9 | 176.0 | 110.4 | in-sample, all 24 |  | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| B | V4 + D_LOO | 57.9 | 110.6 | 74.4 | LOO-CV |  | FULL_transfer_matrix/tables/transfer_matrix_48cells.csv |
| C | V5 baseline | 67.8 | 160.5 | 86.4 | LOO-CV |  | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| D | V5 apparent best | 56.0 | 143.1 | 79.5 | in-sample post-selected |  | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| E | V4 apparent best | 54.9 | 154.8 | 79.6 | in-sample post-selected |  | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| F | V5 corrected | 65.6 |  |  | OOB-bootstrap |  | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| G | V4 corrected | 64.5 |  |  | OOB-bootstrap |  | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| H | V5 bootstrap CI | [54.3, 63.7] |  |  | bootstrap 95% CI |  | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| I | Nested CV (height) | 82.9 |  |  | held-out test |  | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| J | Nested CV (quadrant) | 88.0 |  |  | held-out test |  | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| K | Nested CV (spatial6) | 94.2 |  |  | held-out test |  | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| L | ROTO V5 per-frame | 101.5 | 214.4 | 126.2 | BEST-FIT-ALIGNED |  | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| M | ROTO SE(3) aligned | 82.5 | 185.2 | 103.7 | diagnostic |  | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| N | ROTO Sim3 aligned | 74.3 | 160.8 | 94.8 | diagnostic only |  | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| O | lower_trim_20 + Huber30 + V5 | 44.5 | 164.1 | 81.5 | LOO-CV | YES | FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |
| P | lower_trim_20 + Huber30 + V5(e_i=0 anchor refit) | 43.2 | 163.1 | 81.8 | LOO-CV; anchor refit diagnostic | YES | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| Q | Oracle lower bound | 44.6 |  |  | oracle | YES | FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv |
| R | Bootstrap CI (lower_trim_20) | [33.4, 82.8] |  |  | bootstrap 95% CI | YES | FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv |

### Updated Claim Confidence Levels

| Level | Claim count | Meaning |
| --- | --- | --- |
| A | 11 | Proven within this campaign |
| B | 10 | Supported with caveats |
| C | 5 | Hypothesis only |
| D | 4 | Disproven or should not be claimed |

The updated claim matrix contains 30 claims: 11
Level A, 10 Level B, 5
Level C, and 4 Level D claims [source:
tables/claim_evidence_matrix_v2.csv]. New Level A claims cover raw-frame LOS extraction,
inter-anchor symmetry, and the V5-over-V4 result after NLOS reduction. New caveated
claims cover e_i=0. New Level D wording prevents mixture-model superiority from being
claimed [source: tables/claim_evidence_matrix_v2.csv].

## 1. Dataset and System Description

The analysis uses the corrected complete OptiTrack export under `opti_captures/full`.
The original static optical export had an Anchor-G marker/model error in which
`Gtop` and `Glong` were swapped, and the corrected complete export is the authoritative
optical reference [source: FULL/FULL_ANALYSIS.md]. All Vicon-anchor analyses in this
campaign should be read with that correction in mind.

The UWB system is based on DWM1001C hardware and custom firmware, not a black-box
PANS/DRTLS positioning stack. The relevant measurements are broadcast-style SS-TWR
range observations between anchors and tags, with the offline solver fitting anchor
geometry, anchor delay corrections, and tag positions [source:
FULL_V5/reports/PHASE2_FULL_V5.md]. The V4 layout uses the earlier independent
bounded delay formulation. The V5 layout uses a common-mode anchor-delay
parameterization: a bulk common-mode term c plus regularized per-anchor residuals
e_i [source: FULL_V5/tables/delay_comparison_v4_vs_v5.csv].

The static validation protocol consists of 24 tag positions. These positions are
used for static headline accuracy, height-tier cross-validation, D_tag LOO calibration,
range-percentile tests, p30/inverse-RMS follow-up, nested CV, position anatomy, and
quality-score analysis [source: FULL_V5/tables/static_summary_DLOO.csv;
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv;
FULL_V5_paper_strengthening/tables/p5_quality_score.csv]. The static V5 p50 D_LOO
row contains 28818 solved input frames and no solve failures
[source: FULL_V5/tables/static_summary_DLOO.csv].

The ROTO validation protocol consists of 17 captures with two tags on a rotating
arm. The prompt and later ROTO analyses treat the two tags as separated by a known
radial difference of 120 mm rather than a generic unconstrained inter-tag distance.
The dynamic results must be reported as BEST-FIT-ALIGNED because UWB and Vicon did
not have hardware time synchronization [source: FULL_V5_roto_deepdive/reports/ROTO_DEEPDIVE_COMPLETION.md].
The final-gate rigid test contains 15,717 frames in the paired ROTO evaluation [source:
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv].

The optical reference itself has a caveat. Vicon marker centers are not guaranteed
to coincide with antenna phase centers, and the 3D-printed holder and pivot/marker
geometry can create a physically meaningful offset. Phase-center sensitivity and
final-gate tests later showed that small offsets do not overturn the V4-over-V5
ranking, but the Vicon oracle rank is fragile at about 2 mm perturbations [source:
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].


## 2. Baseline Analysis

### 2.1 V5 Baseline Pipeline

The V5 baseline pipeline was created to repeat the original FULL analysis with the
V5 common-mode anchor layout and V5 delay model. The static baseline row used all
24 positions and 28818 solved frames, with
D_tag fixed to the LOO value 49.621 mm. The resulting median 3D error
was 67.8 mm, P95 was 153.6 mm, and RMSE was
82.8 mm [source: FULL_V5/tables/static_summary_DLOO.csv]. The later
follow-up table re-evaluated the same V5 p50 uniform baseline at 67.8
mm median, the small 0.039 mm difference being accounted for by
the later exact range-row handling [source: FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_grand_synthesis/tables/consistency_audit.csv].

The V5 pipeline also produced dynamic ROTO rows, per-anchor residual fingerprints,
DOP rows, drift rows, D_tag sweep rows, and static breakdowns by height and facing.
The key outcome of this first V5 pass was not that V5 was immediately more accurate
than V4; the key outcome was that a physically motivated V5 geometry and delay model
could reproduce a complete static and dynamic analysis chain with the same metrics as
the existing V4 pipeline [source: FULL_V5/reports/PHASE2_FULL_V5.md].

### 2.2 Sim3 Scale Comparison

The Sim3 diagnostic is the cleanest anchor-side result. The V4-io layout has a Sim3
scale of 0.958 against Vicon, while the V5 common-mode layout has scale
1.010. The rigid anchor RMSE improves from 105.4 mm
for V4 to 63.0 mm for V5 [source: FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
The same registry records the V5 common-mode delay as 111.98479117186592 mm, with an
e_i full spread of 27.703674623990402 mm and max absolute e_i of 15.352958619571629
mm [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv]. This is the
foundation for the Level A claim that V5 fixes the V4 scale leak on this campaign.

The interpretation is specific. V5 fixes metric scale and reduces anchor-coordinate
error against Vicon. It does not, by itself, guarantee lower tag-position error on
the same 24-position static campaign. That distinction stayed important throughout
the rest of the analysis.

### 2.3 Transfer Matrix

The transfer matrix evaluated 48 static cells spanning 3 layouts, 4 correction
sources, and 4 D_tag treatments. The diagonal production-like comparison is the
important row: L_V4 with C_V4 and D_LOO_CV gives 57.9 mm median 3D error,
while L_V5 with C_V5 and D_LOO_CV gives 67.8 mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. The Vicon-anchor common-mode
oracle with D_LOO gives 63.4 mm, while the same Vicon oracle with
an in-sample D_sweep optimum gives 52.8 mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].

The transfer matrix therefore established two facts that can look contradictory if
reported without context. First, V5 has the better anchor geometry. Second, V4 has
the better empirical static median under the p50 LOO setting on this campaign. This
is where the cancellation hypothesis began: V4's scale compression appears to cancel
some structured positive range bias rather than merely representing a worse geometry.

### 2.4 Oracle and Single-Baseline Analysis

The Vicon-anchor evaluation tested what happens when the anchor positions are taken
from optical ground truth instead of self-calibration. It did not produce a decisive
oracle advantage. With C_Vicon_cm and D_LOO_CV the transfer matrix row is
63.4 mm, and with D_sweep_opt it is 52.8 mm [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. This supports the cautious
claim that Vicon-anchor results are compatible with cancellation, but not the stronger
claim that Vicon underperformance uniquely proves cancellation [source:
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

The one-baseline scale-correction analysis gave a null-style result for V5. Since V5
was already close to metric scale, single external baselines did not create the same
large improvement as the earlier V4 F-H baseline result [source:
FULL_V5_one_baseline_scale_correction/reports/PHASE4_V5_ONE_BASELINE.md]. This result
is consistent with the Sim3 diagnostic: V5 had already absorbed the scale correction
into the common-mode anchor-delay parameterization.


## 3. Mechanism Ablations

### 3.1 Six Mechanism Items

The first mechanism batch tested six targeted questions: hard validation splits,
residual-field structure, the cancellation valley, per-height D_tag stability,
D_tag criterion curves, and multi-criterion D_tag ambiguity [source:
FULL_V5_mechanism_ablations/reports/MECHANISM_ABLATION_SUMMARY.md].

Hard CV showed that V4+C_V4 and V5+C_V5 degrade in different but comparable ways.
V4+C_V4 has a full LOO median of 57.9 mm and a worst height-tier degradation of
8.4 mm; V5+C_V5 has 67.8 mm full LOO and 4.5 mm degradation; Vicon+C_Vicon_cm has
63.4 mm full LOO and 12.2 mm degradation [source:
FULL_V5_mechanism_ablations/A_hard_cv/tables/hard_cv_summary.csv].

| Config | Full LOO median mm | Worst tier | Height degradation mm | Outer/center degradation mm |
| --- | --- | --- | --- | --- |
| V4+C_V4 | 57.9 | LOW | 8.4 | 14.8 |
| V5+C_V5 | 67.8 | LOW | 4.5 | 16.9 |
| Vicon+C_Vicon_cm | 63.4 | LOW | 12.2 | 19.3 |

Residual-field analysis found structured residual bias in both V4 and V5. V4's mean
signed error magnitude is 33.2 mm, while V5's is 26.7 mm; however, V4's 3D median is
lower at 57.9 mm than V5's 67.8 mm [source:
FULL_V5_mechanism_ablations/B_residual_field/tables/residual_field_summary.csv].
The cancellation-valley grid reported an in-sample global minimum at scale 0.980,
D_tag 108.0 mm, median 55.3 mm [source:
FULL_V5_mechanism_ablations/C_cancellation_valley/tables/cancellation_valley_markers.csv].
Per-height D_tag optima showed V4 spanning 20-54 mm and V5 spanning 70-86 mm under
the min-median criterion [source:
FULL_V5_mechanism_ablations/D_per_height_dtag/tables/per_height_dtag_optima.csv].
The later grand-synthesis registry records range-residual tier spreads of 11.8 mm
for V4 and 7.4 mm for V5 [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv].

### 3.2 Twenty-Four Extended Mechanism Items

The extended CPU ablation batch expanded the mechanism tests to 24 items. The full
summary table is extracted into `tables/extended_items_key_findings.csv`. The item
set is best read by theme rather than by row number.

| Item | Hypothesis tested | Verdict | Key number |
| --- | --- | --- | --- |
| 01 | Range-residual D_tag changes by height tier | mixed | V4 spread 11.8 mm; V5 spread 7.4 mm; Vicon spread 14.1 mm |
| 02 | Elevation angle explains rho | mixed | best abs-angle R2 0.107 for V5 |
| 03 | Effective D_tag differs by anchor | supported | V5 anchor spread 131.0 mm |
| 04 | NLOS exclusions shift D_tag | supported | V5 exclude D,F delta -15.3 mm |
| 05 | LOO fold D_tag correlates with held-out metadata | mixed | best height R2 0.038 for V4 |
| 06 | Joint V4-to-V5 morph has a lower valley | supported | global min alpha 0.15, D=52.0, median 56.4 mm |
| 07 | Common anchor shift and tag shift are interchangeable | supported | best anchor shift 100.0 mm, tag shift -60.0 mm |
| 08 | Facing group changes D_tag | supported | facing metadata present |
| 09 | Board-frame incidence explains rho | skipped | board orientation input unavailable |
| 10 | Low-order antenna model beats scalar D_tag | supported | V5 best M2 median 54.8 mm |
| 11 | Calibration quality improves with set size | supported | k=4 stratified mean 69.0 mm |
| 12 | Calibration design matters | supported | best V5 stratified_LMH median 40.8 mm |
| 13 | D_tag criterion optimum varies across folds | supported | max spread 18.0 mm |
| 14 | Vicon delay regularization changes oracle tail | mixed | best e10 median 63.4 mm |
| 15 | Anchor common mode is layer-dependent | not supported | upper-lower c diff -8.6 mm |
| 16 | Residual variance has structured factors | supported | top factor anchor_id fraction 0.090 |
| 17 | Historical rho weighting/removal improves solves | supported | best V4 inverse_rms median 50.9 mm |
| 18 | Static residuals drift over acquisition time | mixed | D_tag early/mid/late 53.0, 59.6, 34.3 mm |
| 19 | ROTO tags have device-specific D_tag | supported | median per-tag spread 24.9 mm |
| 20 | Dynamic residual correlates with motion state | mixed | speed-residual R2 0.000 |
| 21 | Lower range percentiles mitigate NLOS | supported | V5 p30 median 47.5 mm before fair recalibration |
| 22 | Single anchors have D_tag leverage | supported | max delta -8.1 mm removing F |
| 23 | Differential ranging cancels common-mode errors | mixed | median differential/absolute RMS ratio 1.416 |
| 24 | Residual distribution shape differs by layer | mixed | skew upper 1.76, lower 1.60 |

Tag-delay physics was not reducible to one scalar. Range-residual D_tag by height
tier was mixed, per-anchor effective D_tag had a large V5 spread of 131.0 mm, NLOS
exclusions moved the V5 estimate by -15.3 mm when D and F were excluded, and per-tag
ROTO estimates suggested a 24.9 mm spread [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
This supports treating D_tag as a useful scalar operating parameter, but not a pure
hardware constant in all geometries.

NLOS and link-quality tests showed that residual structure is real but not trivially
removed. Historical inverse-RMS weighting improved some static solves, jackknife
anchor removal affected D_tag, distribution tails differed by layer, and differential
ranging did not cleanly collapse the absolute residual error [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
The strongest static percentile diagnostic was V5 p30 at 47.5 mm before fair
recalibration, but this result was later reclassified as another cancellation effect
rather than a deployable universal correction [source:
FULL_V5_extended_mechanism_ablations/tables/item21_range_percentile_sweep.csv;
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv]. The later raw-frame
campaign separated this issue from simple p30 aggregation: lower_trim_20 over the full
per-link histogram reached 44.5 mm LOO with V5 and Huber30, while the transductive
all-data p10 result was kept diagnostic [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv].

Calibration design was highly sensitive. Item 12 found a best V5 stratified_LMH
median of 40.8 mm, but follow-up stratified repeats showed this could be a lucky
split rather than a robust deployment recipe [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_followup_validation/tables/f3_stratified_stability_summary.csv].
This is why the final narrative treats calibration design as important but does not
claim that any one small calibration subset is solved.


## 4. GPU Discovery Pipeline

### 4.1 Tier 1: Six GPU Tasks

The GPU Tier 1 run completed six tasks in 12.08 minutes on the two GTX 1080 Ti cards
[source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md]. The first task reported
P(V5<V4)=1.00 in a multi-room Monte Carlo. This was later corrected because the V4
solver parameterization in the simulation was not faithful enough for that strong
claim [source: FULL_V5_overnight_batch2/reports/TASK_N1_MC_VERIFICATION.md].

The Fisher task found a weakest eigenvalue of 1.000e-06, giving direct evidence for
a weakly identifiable calibration direction [source:
FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md]. The Shapley task assigned high
scores to anchors D and F, 1242.9 and 1229.4, respectively [source:
FULL_V5_GPU_tier1/tables/task3_shapley_values.csv]. The AA-AT asymmetry task found
a mean asymmetry of -4.7 mm [source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md].
The solver-search task returned 82.7 mm, which was worse than existing baselines
and later remained worse after D_tag LOO fixes [source:
FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md;
FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv]. The NLOS task reached
PR-AUC 0.952 in Tier 1 and 0.949 in the full GPU discovery repeat [source:
FULL_V5_GPU_tier1/tables/task6_cv_results.csv;
FULL_V5_GPU_discovery/tables/task6_cv_results.csv].

### 4.2 Full Discovery: Seventeen GPU Tasks

The full GPU discovery run completed 17 of 17 tasks in 19.06 minutes [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Successful discoveries
included the repeated Fisher weak direction, repeated Shapley D/F attribution,
the AA-AT asymmetry check, a Student-t model tournament winner, synthetic CIR
experiments, dynamic-state statistics, and an active-design score [source:
FULL_V5_GPU_discovery/reports/KEY_FINDINGS_SYNTHESIS.md].

Problematic results were equally important. Task 1 repeated the too-clean
P(V5<V4)=1.00 result, Task 5's solver search stayed worse than baseline at 82.7 mm,
Task 8's landscape minimum lay on a boundary at s=0.930, dc=50, D=140, and Task
12's Gaussian Bayesian solver had only 0.33 actual 95% coverage [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Learned-correction tasks
were data-limited: the MLP residual median was 118.0 mm versus scalar 98.5 mm, and
the attention residual median was 121.1 mm [source:
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md].

The GPU discovery phase therefore changed the campaign in two ways. It produced
stronger mechanistic evidence for identifiability, Shapley structure, and
heavy-tailed residuals. It also forced later correction batches because several
attractive GPU results were too optimistic or insufficiently faithful to the actual
V4/V5 solver definitions.


## 5. Follow-up Validation

The follow-up validation batch contained six tasks and produced the first corrected
best-practice headline table [source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].
F1 showed that p30 plus inverse-RMS weighting plus recalibrated D_tag gives V5 a
56.0 mm median, but it did not break a 45 mm target [source:
FULL_V5_followup_validation/tables/f1_combination_grid.csv]. F2 showed that p30
does not transfer to ROTO: the best p30/median-window ROTO result was 283.9 mm
versus raw/p50 101.5 mm [source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md].

F3 tested stratified calibration sanity. A 40.8 mm split was identified as lucky;
the scalar stratified mean median was 68.2 mm with standard deviation 8.7 mm [source:
FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md]. F4 performed
fair percentile recalibration. After recalibration, the V5 optimum shifted to p20
at 53.8 mm and p30 became 59.8 mm; V4 still won at every percentile, with the best
recalibrated percentile cell at 52.0 mm for V4_CV4 p20 [source:
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_grand_synthesis/tables/master_number_registry.csv].

F5 showed that per-anchor percentile selection was not straightforward. A selective
D/F p30 else p50 diagnostic could reach 47.3 mm, but anchor F specifically became
worse under p30 by -13.2 mm in that task's report [source:
FULL_V5_followup_validation/reports/TASK_F5_PERCENTILE_PER_ANCHOR.md]. F6 produced
the specified headline comparison: V4 production 71.9 mm, V5 baseline 67.8 mm, V5
improved 56.0 mm, V4 improved 54.9 mm, and Vicon improved 56.3 mm [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv].


## 6. Corrections and Fixes

N1 directly addressed the overly clean Monte Carlo result. In 10 adversarial rooms
designed to favor V4-style cancellation, V5 won 3 of 10 rooms, giving
P(V5<V4)=0.300 rather than 1.000 [source:
FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv]. The report also flagged
the V4 simulation solver fidelity problem, so the original P=1.00 result should not
be used as a strong transfer claim [source:
FULL_V5_overnight_batch2/reports/TASK_N1_MC_VERIFICATION.md].

N2 fixed the solver-search protocol by adding D_tag LOO-CV to the top variants and
the V4/V5 baselines. The best fixed variant remained about 82.6 mm and did not beat
V4+C_V4+D_LOO or V5+C_V5+D_LOO [source:
FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv]. N3 replaced the
Gaussian Bayesian likelihood with Student-t and mixture variants. Student-t became
the BIC winner, with 95% coverage 0.458, but this
was still badly below nominal 0.950 [source: FULL_V5_final_gate/tables/g2_unified_noise_models.csv].

N4-N6 returned to p30. N4 reported a fallback best median of 47.5 mm, N5 reported
a p30 transfer-matrix sweep winner at 46.8 mm, and N6 reported a V5 bootstrap median
CI of 54.3-63.7 mm [source: FULL_V5_overnight_batch2/reports/OVERNIGHT_BATCH2_COMPLETION.md;
FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv]. N7-N9 generated 10 figures,
7 paper table sets, and a paper outline [source:
FULL_V5_overnight_batch2/reports/OVERNIGHT_BATCH2_COMPLETION.md].


## 7. Falsification Campaign

The falsification batch attacked the campaign's own conclusions. Nested CV selected
variants on training splits and evaluated held-out partitions. The mean test medians
were 82.9 mm for height-out, 88.0 mm for quadrant-out, and 94.2 mm for spatial6
[source: FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].

| Split | Mean test median mm | Std mm |
| --- | --- | --- |
| height | 82.9 | 26.9 |
| quadrant | 88.0 | 18.2 |
| spatial6 | 94.2 | 29.0 |

The winner's-curse task estimated a mean optimism gap of 9.6 mm,
moving the corrected V4 improved median to 64.5 mm and corrected V5 improved
median to 65.6 mm [source:
FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv]. The profile-likelihood
task produced dense alpha/D_tag, scale/D_tag, and c/D_tag surfaces with 31,823 rows
across the profile tables and visible valleys [source:
FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv;
FULL_V5_batch3_falsification/tables/f3_profile_s_dtag.csv;
FULL_V5_batch3_falsification/tables/f3_profile_c_dtag.csv].

The nullspace perturbation task supported a weak direction but did not pass the
strictest threshold. The grand-synthesis registry records a median perturbation ratio
of 0.267 [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv]. The
NLOS leakage test was decisive against the strong generalization claim: random-split
PR-AUC around 0.949 fell to leave-one-anchor values of 0.419
to 0.548 across model choices [source:
FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv]. The reviewer simulation
demoted 3 claims to C/D [source: FULL_V5_batch3_falsification/reports/FALSIFICATION_COMPLETION.md].


## 8. ROTO Deep-Dive

ROTO was analyzed separately because the dynamic error floor is not the same problem
as static p50/p30 accuracy. R1 swept time offsets and recovered only 0.7 mm median
improvement, so time offset was not the main bottleneck [source:
FULL_V5_roto_deepdive/tables/r1_time_corrected_results.csv]. R2 compared alignment
methods. No alignment gave 557.9 mm, translation-only gave 83.7 mm, SE(3) gave
81.7 mm, current anchor-bridge best-fit gave 101.5 mm, and diagnostic Sim3 gave
74.3 mm with scale 0.906 [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

| Method | Median mm | P95 mm | RMSE mm | Scale |
| --- | --- | --- | --- | --- |
| A_none_beta0 | 557.9 | 841.2 | 606.1 | 1.000 |
| B_translation_existing_beta | 83.7 | 190.0 | 108.5 | 1.000 |
| C_SE3_existing_beta | 81.7 | 183.6 | 103.8 | 1.000 |
| D_Sim3_existing_beta | 74.3 | 160.8 | 94.8 | 0.906 |
| E_current_anchor_bridge_existing_beta | 101.5 | 214.4 | 126.2 | 1.000 |
| F_time_corrected_SE3 | 82.5 | 185.2 | 103.7 | 1.000 |

R3 tested rigid-body exploitation and did not improve tracking. The independent
diagnostic median was 101.1 mm, while the joint projection was roughly 280.6 mm in
the ROTO deep-dive and the final-gate true range-level joint solver returned
261.8-264.2 mm [source: FULL_V5_roto_deepdive/tables/r3_joint_summary.csv;
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv]. R4 decomposed the static to
dynamic gap into non-orthogonal proxy components.

| Component | Estimate mm | Notes |
| --- | --- | --- |
| D_tag mismatch | 22.9 | Upper-bound proxy, not orthogonal contribution. |
| Motion blur | 6.4 | Uses nominal poll window. |
| Time alignment recoverable | 0.7 | Recoverable portion from offset sweep. |
| Range aggregation / dynamic single-frame | 0.0 | Proxy only; static subsampling not rerun here. |
| Unexplained | 15.5 | gap=45.5 mm |
| TOTAL static-to-dynamic gap | 45.5 | dynamic=101.5, static=56.0 |

R5 tested dynamic NLOS weighting and found negligible improvement: soft_nlos was
104.2 mm in its diagnostic table [source: FULL_V5_roto_deepdive/tables/r5_nlos_dynamic_results.csv].
R6 mapped error by rotation phase and found the worst sector at 300 degrees, with
anchor D the dominant worst anchor [source: FULL_V5_roto_deepdive/tables/r6_phase_aggregate.csv].
The recommended dynamic baseline remains the existing V5 D_LOO per-frame solver,
reported explicitly as BEST-FIT-ALIGNED [source:
FULL_V5_roto_deepdive/reports/ROTO_DEEPDIVE_COMPLETION.md].


## 9. Mechanistic Deep-Dive

M1 decomposed position error into signed radial, tangential, and vertical components.
The hypothesized radial mechanism was not decisive: V4 mean signed radial was -7.8 mm,
V5 was -4.8 mm, and Vicon was -5.1 mm, so all three were slightly inward rather than
V4 uniquely inward and V5 outward [source:
FULL_V5_mechanistic_deepdive/tables/m1_error_direction_summary.csv].

| Config | Mean signed radial mm | Mean signed vertical mm | Median abs radial mm |
| --- | --- | --- | --- |
| V4+C_V4+D_LOO | -7.8 | -23.4 | 25.2 |
| V5+C_V5+D_LOO | -4.8 | -19.6 | 22.4 |
| Vicon+C_cm+D_LOO | -5.1 | -23.2 | 24.2 |

M2 produced a proxy physical error budget, but explicitly flagged that the components
are not orthogonal [source: FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md].
M3 measured V5 offset vectors relative to Vicon and found a mean magnitude of 56.8
mm with direction resultant 0.09, meaning offsets were not coherently aligned [source:
FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md]. M4 tested
whether V5 e_i values were NLOS proxies. corr(e_i, rho_rms) was 0.08, and forcing all
e_i to zero improved the median from 67.8 mm to 64.5 mm in that counterfactual
[source: FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv]. The anchor
lower-trim blind experiment later repeated the same direction under the raw-frame
static estimator: p50 anchors with e_i=0 reached 43.2 mm, compared with 44.5 mm for
the current p50/e_reg20 control, although the bootstrap P(new wins) was only 0.659
[source: FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

M5 addressed anchor count and identifiability. The redundancy table shows that 8
anchors give redundancy +2 and about 68.7 mm mean median in the subset replay, while
a simulated 9th anchor gives redundancy +6 and about 60.7 mm mean median [source:
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

| Anchors | Ranges | Params | Redundancy | Mean median 3D mm |
| --- | --- | --- | --- | --- |
| 4 | 6 | 10 | -4 | 141.5 |
| 5 | 10 | 14 | -4 | 109.0 |
| 6 | 15 | 18 | -3 | 89.8 |
| 7 | 21 | 22 | -1 | 79.5 |
| 8 | 28 | 26 | 2 | 68.7 |
| 9 | 36 | 30 | 6 | 60.7 |

M6 repeated the ROTO phase result, M7 confirmed that the rigid constraint diagnostic
does not improve beyond about 101.1 mm, M8 found n_bad_anchors to be the strongest
simple predictor with R2=0.18, M9 found a local Fisher eigenvalue of 5.98e-03 in the
recomputed simplified model, and M10 found V5 baseline consistency max delta 0.00 mm
[source: FULL_V5_mechanistic_deepdive/reports/MECHANISTIC_DEEPDIVE_COMPLETION.md].


## 10. Final Gates

The final gates closed the campaign before paper writing. G1 produced the locked
headline table used at the top of this report [source:
FULL_V5_final_gate/tables/g1_locked_headline.csv]. G2 unified the noise-model story:
Student-t is the BIC winner, and the key-number card had a parsing error in the
earlier Bayesian coverage narrative [source: FULL_V5_final_gate/tables/g2_unified_noise_models.csv].

G3 nested phase-center offsets and concluded that small offsets under 50 mm do not
explain the Vicon result, while larger unconstrained offsets can fit but become
physically implausible. The best unconstrained model had mean test median 54.3 mm
but max offset 128.5 mm; the best small/global model B had mean test median 66.1 mm
and max offset 37.3 mm [source: FULL_V5_final_gate/tables/g3_phase_center_summary.csv].
G4 validated selected deployment recipes against the full solver and found proxy/full
gaps up to 5.7 mm [source: FULL_V5_final_gate/tables/g4_validated_recipes.csv].

G5 was the final ROTO rigid-body gate. It confirmed the negative result at range level:

| Method | Median mm | RMSE mm | P95 mm | Convergence rate |
| --- | --- | --- | --- | --- |
| independent_baseline_current | 101.1 | 132.1 | 227.7 | 1.000 |
| joint_fixed_49p621 | 261.8 | 315.5 | 498.1 | 0.928 |
| joint_static_estimated_dtag | 262.2 | 314.1 | 495.9 | 0.932 |
| joint_coarse_cost_min_dtag | 264.2 | 312.3 | 491.7 | 0.919 |

The gate report explicitly says no further experiments are introduced by the final
gate script and that paper writing should begin after the report [source:
FULL_V5_final_gate/reports/FINAL_GATE_COMPLETION.md].


## 11. Phase Center Sensitivity

The phase-center sensitivity batch tested whether plausible antenna phase-center
offsets can overturn the main conclusions. A1 applied global vertical shifts and
found that V4-beats-V5 does not flip up to 10 mm [source:
FULL_V5_phase_center_sensitivity/tables/a1_global_shift_results.csv]. A2 ran 5,000
manufacturing-variation samples per sigma level and found P(V4 beats V5) at or above
0.998 through sigma=8 mm [source:
FULL_V5_phase_center_sensitivity/tables/a2_ranking_probabilities.csv].

| Sigma mm | P(V4 beats V5) | P(Vicon worst) |
| --- | --- | --- |
| 1.0 | 1.000 | 0.075 |
| 2.0 | 1.000 | 0.160 |
| 3.0 | 1.000 | 0.183 |
| 5.0 | 1.000 | 0.224 |
| 8.0 | 0.998 | 0.273 |

A3 separated anchor and tag perturbations. Anchor perturbations dominated scale and
Vicon metrics, while tag perturbations dominated D_tag and tag-position error shifts
[source: FULL_V5_phase_center_sensitivity/tables/a3_dominance.csv]. A4 fitted a
direction-dependent phase-center model, with the best V5 median at delta_0=-5 mm and
delta_elev=-10 mm [source: FULL_V5_phase_center_sensitivity/tables/a4_best_fit.csv].
A5 showed that the valley shape remains dominated by scale-D_tag coupling; tested
vertical shifts left the valley-distance diagnostic near 11.3-11.4 [source:
FULL_V5_phase_center_sensitivity/tables/a5_valley_shift.csv].

| Conclusion | Baseline value | Flip threshold | Robustness |
| --- | --- | --- | --- |
| V5 Sim3 scale > 0.99 | 1.010 | >10 | robust |
| V4+LOO beats V5+LOO | V4-V5=-23.0 mm | >10 | robust |
| Vicon oracle rank/worst status | rank=2, worst=False | 2.0 | fragile |
| D_tag LOO approximately 49.6mm | 49.028 mm; sensitivity 0.190 mm/mm | not binary | stable |
| D_tag per-height spread V5 < V4 | 7.4 < 11.8 mm from prior mechanism audit | not directly flipped by global phase-center sweep | not directly tested here; use A4 as caveat |
| Cancellation valley exists | max tested operating-point valley-distance shift 11.37 | does not depend on absolute phase-center offset | invariant mechanism |

The phase-center conclusion is therefore narrow. Small phase-center offsets are not
enough to undo the V4-over-V5 static ranking or the V5 scale fix. The Vicon oracle
rank, however, is fragile, so Vicon-underperformance should not be treated as unique
proof of cancellation [source: FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].

## 12. Raw-Frame LOS Extraction Campaign

### 12.1 Motivation and Physical Basis

The original static pipeline aggregated each position-anchor link to a representative
range before position solving. For p50 runs that representative was the median range.
That is robust to isolated outliers but it does not explicitly separate a direct LOS
component from a positive NLOS/multipath tail. The raw-frame campaign was designed to
test whether the full per-link histogram contains usable information that a median
cannot express [source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md].

The physical assumption is one-sided. UWB multipath and NLOS contamination usually
increase measured range because reflected paths are longer than the direct path. If a
static capture contains about 1200 frames per link, the left side of the range histogram
can preserve a repeated LOS-side component even when the median is shifted by a positive
tail. This is not the same as selecting one minimum sample. The successful estimator,
lower_trim_20, averages the lowest 20% of valid raw ranges for each static
position-anchor link, so it uses hundreds of frames per link rather than a single
extreme observation [source: FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

This distinction matters for deployment. lower_trim_20 is a static or quasi-static
batch estimator. It requires a range histogram. It is not directly available for ROTO
single-frame tracking, which is why the earlier ROTO p30 test failed to transfer [source:
FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

### 12.2 Data Inventory

The first raw-frame brute-force run produced only per-link feature outputs and was
therefore insufficient to prove that raw frames had been loaded. The v2 diagnostic fixed
that question by inspecting the capture files directly. It found one `tr_all.csv` file
per static capture, with one row per sweep-anchor observation [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

| Metric | Value | Source |
| --- | --- | --- |
| Static files found | 24 | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| Total raw rows | 230544 | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| Total valid rows | 228265 | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| Expected rows | 230400 | 24 x 8 x 1200 |
| Ratio to expected | 1.001 | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| ID01 anchor A frames loaded | 1199 | FULL_V5_rawframe_bruteforce_v2/tables/raw_loading_checkpoint.csv |
| V3 link inventory rows | 192 | FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv |
| Median frames per tag-anchor link | 1198 | FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv |

The first capture, ID01, had shape `(9608, 31)` and anchor counts of 1201 rows for
each of the eight anchors. The columns include `host_elapsed_s`, `host_epoch_s`,
`sweep`, `anchor_id`, `raw_mm`, `range_mm`, `quality_percent`, `valid`, `status`,
and IMU summary columns such as `acc_norm_mean_mg` and `acc_norm_std_mg` [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md]. The v3 link inventory
contains 192 static tag-anchor links, one for each of 24 positions and 8 anchors,
with per-link distribution columns including min, p01, p05, p40, p50, and max [source:
FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv].

The raw inventory also explains why the earlier static percentile findings were
real but incomplete. The p30 runs used a lower percentile per link, but the later
v3 search evaluated a broader estimator family over the full link histogram [source:
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s1_all_estimators.csv].

### 12.3 Estimator Comparison

The v3 campaign ran the exhaustive search requested after the v2 discovery. Stage 1
fit parametric mixture models with 8 model families, 192 links, 100 initializations,
500 Adam steps, and 200 L-BFGS steps. Stage 2 evaluated a full grid of 37 estimators,
121 D_tag values, 8 loss choices, and 3 geometries, for 107,448 grid configurations
[source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md;
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv].

| Row | Estimator | Geometry | Loss | Median mm | P95 mm | Source |
| --- | --- | --- | --- | --- | --- | --- |
| V2 B1 | gaussian_exponential_mix |  |  | 51.5 | 159.2 | FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv |
| V2 B2 | asymmetric |  |  | 53.9 | 95.5 | FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv |
| V3 transductive | p10 | V5 | huber50 | 40.5 | 135.2 | FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv |
| V3 honest | lower_trim_20 | V5 | huber30 | 44.5 | 164.1 | FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |
| V3 oracle | oracle link selector |  |  | 44.6 |  | FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv |

The best all-data Stage 2 row was p10/V5/Huber50 at 40.5
mm. It is a transductive row because the D_tag and estimator choices see all 24 positions
at once [source: FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv]. The
honest Stage 3 row is different: lower_trim_20/V5/Huber30 reaches 44.5
mm after fold-wise D_tag calibration [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The non-parametric lower-tail family beat the parametric mixture family in the final
honest ranking. In v2, gaussian_exponential_mix reached 51.5
mm held-out and the asymmetric PyTorch variant reached 53.9
mm [source: FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv]. In v3,
lower_trim_20/V5/Huber30 reached 44.5 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The practical result is
that a simple histogram statistic outperformed the fitted mixture models for this
dataset.

The source inventory is important. lower_trim_20 is not a new solver geometry and not
a learned model. It changes the tag-anchor range observation supplied to the existing
position solver. The geometry, delay correction, loss, and D_tag calibration are still
explicitly recorded in the Stage 3 table [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 12.4 Honest LOO Results

The v3 Stage 3 ranking is the raw-frame result that should be used in the headline
table. It recalibrates D_tag fold-wise and evaluates each held-out static position
under the chosen estimator/loss/geometry configuration. The best row is lower_trim_20,
Huber30, and V5 geometry, with LOO median 44.5 mm, P95
164.1 mm, RMSE 81.5 mm, all-data
median 43.9 mm, and mean training D_tag
6.9 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

| Geometry | Best estimator/loss | LOO median mm | P95 mm | RMSE mm |
| --- | --- | --- | --- | --- |
| V5 | lower_trim_20 / huber30 | 44.5 | 164.1 | 81.5 |
| V4 | model_M3 / student5 | 53.9 | 132.5 | 76.3 |
| Vicon | p05 / student5 | 44.7 | 159.9 | 76.3 |

This geometry ranking is the main new mechanism evidence. Under p50, V4 wins because
its compressed geometry partly cancels structured positive range bias. Under raw-frame
LOS extraction, V5 wins by 9.4 mm over the best V4 geometry row in the honest v3 ranking
[source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The direction of the
ranking therefore changes when the range aggregation removes much of the NLOS tail.
That is a stronger confirmation of the cancellation hypothesis than the earlier signed
radial diagnostic alone [source: FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv].

The bootstrap interval remains wide because there are still only 24 independent static
positions. Stage 5 ran 5000 bootstrap iterations and reported a mean of 50.4
mm, standard deviation 13.4 mm, and 95% interval 33.4
to 82.8 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv]. This interval does
not weaken the fact that the full-campaign LOO row is 44.5 mm. It does limit how
aggressively the number should be generalized beyond this campaign.

### 12.5 Falsification and Controls

The v3 campaign included control tasks after the Stage 3 breakthrough. Stage 5 wrote
frame-half stability, synthetic recovery, anchor-holdout, persistent-NLOS, leakage,
and bootstrap tables [source: FULL_V5_rawframe_bruteforce_v3/tables/output_row_counts.csv].
The control set is part of the evidence because the raw-frame result could otherwise
be a histogram-overfitting artifact.

The frame-half table has 192 rows, one per position-anchor link, and checks whether
the selected estimator is stable between temporal halves of each static capture [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_frame_half.csv]. The synthetic recovery table
has 40 rows and injects known NLOS behavior to test whether the estimator can recover
the intended side of the distribution [source:
FULL_V5_rawframe_bruteforce_v3/tables/s5_synthetic_recovery.csv]. The anchor-holdout
table has 8 rows, one for each removed anchor, and tests sensitivity to single-anchor
removal [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_anchor_holdout.csv].

The leakage assertion remains a cautionary table rather than a clean pass/fail headline.
It is included because D_tag calibration and estimator selection are both sensitive
with only 24 positions [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_leakage.csv].
This report therefore uses lower_trim_20/V5/Huber30 as a strong campaign result and a
static batch-processing recommendation, while still retaining the nested-CV and
bootstrap caveats from the broader campaign [source:
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].

### 12.6 Why V5 Wins When NLOS Is Removed

The raw-frame result resolves a tension in the earlier report. V5 is the physically
better anchor geometry by Sim3 scale and rigid anchor RMSE, but V4 is the better p50
static positioning geometry on this campaign [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv;
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. That tension is expected
if V4's scale compression cancels a positive tag-anchor range bias.

Once lower_trim_20 reduces the positive range tail, the cancellation advantage should
shrink. That is what the v3 honest ranking shows: V5 is 44.5
mm, V4 is 53.9 mm, and Vicon is 44.7
mm [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv]. The Vicon
row remains close, which is consistent with the phase-center caveat. The V4 row no
longer wins, which is the new evidence that V4's p50 advantage was partly an error
cancellation effect rather than a universally better geometry [source:
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].

This result should not be written as "V5 always wins." The correct wording is narrower:
on the Erlangen static campaign, V4 wins under p50 LOO, while V5 wins after raw-frame
LOS extraction with lower_trim_20. That distinction is the main new paper-ready
finding [source: FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].

### 12.7 Relation to Earlier Percentile and Oracle Rows

The lower_trim_20 result should not be collapsed into the earlier p30 result. p30 is
a single percentile of the raw distribution, while lower_trim_20 averages the lowest
20% of valid raw samples. In the v3 all-data grid, several pure-percentile rows reached
low transductive medians, including p10/V5/Huber50 at 40.5
mm, p15/V5/Huber50 at 41.3
mm, and p07/V5/Huber50 at 41.4
mm [source: FULL_V5_rawframe_bruteforce_v3/tables/s2_best_per_estimator.csv]. Those rows
were not the final headline because Stage 3 re-ran the top configurations under
fold-wise D_tag calibration and found lower_trim_20/V5/Huber30 to be the best honest
row [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The oracle row is also narrower than a physical ground-truth oracle. The B0 oracle
selects or constructs a best recoverable link-level estimate from the available
range distributions. It does not use an external hardware LOS label. Its role is to
measure how much static positioning error is recoverable from the existing raw
histograms before changing hardware or anchor geometry. The oracle median is
44.6 mm and the honest lower_trim_20 median is
44.5 mm [source:
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv]. That small gap is why
the report calls lower_trim_20 a breakthrough, but the source remains the same 24
positions and the same UWB campaign.

The v3 run also contains a Stage 4 bundle-adjustment gate. It was skipped because
the Stage 3 result path did not require BA to establish the raw-frame breakthrough
[source: FULL_V5_rawframe_bruteforce_v3/reports/BRUTEFORCE_V3_COMPLETION.md;
FULL_V5_rawframe_bruteforce_v3/tables/s4_ba_best.csv]. That is important for
interpretation: the new 44.5 mm number is not produced by moving anchors freely or
by learning a new layout. It is produced by changing the static tag-anchor range
estimator, keeping the geometry label explicit, and recalibrating D_tag under LOO.

The deployment boundary is therefore clear. A static measurement mode can collect
dozens to hundreds of frames, compute lower_trim_20 per anchor, run Huber30, and use
a D_tag calibrated for that estimator. A dynamic single-frame mode cannot do this
without a temporal window. A future quasi-static implementation could accumulate a
short histogram when motion is low, using IMU variance or repeated range stability
as a gate; that remains untested in this report [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

## 13. Anchor Self-Calibration with Lower_Trim

### 13.1 Hypothesis

The raw-frame tag result naturally raised a second question: if lower-tail statistics
recover a better LOS-side tag-anchor range, should the same lower-tail statistic be
used for inter-anchor ranges during anchor self-calibration? The blind anchor lower-trim
experiment tested that question by rebuilding anchor layouts with multiple inter-anchor
range aggregations and e_i settings, then evaluating tag accuracy with the same
lower_trim_20 static tag estimator [source:
FULL_V5_anchor_lower_trim/reports/ANCHOR_LOWER_TRIM_COMPLETION.md].

The hypothesis was directional. If inter-anchor raw ranges have the same positive
NLOS tails as tag-anchor ranges, lower_trim_20 should improve the anchor geometry.
If inter-anchor ranges are symmetric and stable, lower_trim_20 should bias the range
downward and worsen geometry. The experiment was blind in the sense that the result
was determined by the full tag-positioning replay after the anchor variants were
constructed [source: FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 13.2 Inter-Anchor Distribution Analysis

The inter-anchor raw inventory contains 28 anchor pairs and 56,000 raw
valid rows, with median 2000 frames per pair [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. The mean skewness
is 0.063, which is close to symmetric. The mean p50 minus lower_trim_20 is
32.9 mm, meaning lower_trim_20 systematically shortens the pair ranges
even though the distributions do not show a strong right-tail structure [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].

| Metric | Value | Source |
| --- | --- | --- |
| Raw valid AA rows | 56000 | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| Pairs | 28 | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| Frames per pair median | 2000 | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| Mean skewness | 0.063 | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| Mean p50 - lower_trim_20 | 32.9 | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |

This is physically different from the tag-anchor static histograms. Static tag-anchor
captures sample a tag placement in the room and can include link-specific NLOS tails.
Inter-anchor captures are static, repeated, and quasi-constant. Multipath can still
create a bias, but it does not necessarily appear as a frame-to-frame positive tail
that a lower-tail statistic can remove [source:
FULL_V5_anchor_lower_trim/reports/TASK_L1_INTER_ANCHOR_DISTRIBUTIONS.md].

### 13.3 Blind Test Results

The anchor blind test evaluated 24 anchor solver variants: eight range aggregations
and three e_i settings. The best lower_trim_20-anchor row was lower_trim_20 with
e_i=0, at 46.4 mm LOO. The current p50 control under
the raw-frame tag estimator is 44.5 mm, and p50 with
e_i=0 is 43.2 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

| Variant | LOO median mm | P95 mm | RMSE mm | D_tag mean mm | Sim3 scale | Rigid RMSE mm | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| p50 + e_i=0 | 43.2 | 163.1 | 81.8 | 8.5 | 1.010 | 63.1 | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| p50 + e_reg=5 | 43.2 | 163.1 | 81.8 | 8.4 | 1.010 | 62.9 | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| p50 current control | 44.5 | 168.4 | 82.2 | 6.9 |  |  | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| p50 + e_reg=20 refit | 44.6 | 167.6 | 82.2 | 6.6 | 1.010 | 62.5 | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| lower_trim_20 + e_i=0 | 46.4 | 165.3 | 83.9 | 24.5 | 1.020 | 71.2 | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |

The blind result is therefore negative for inter-anchor lower trimming. The best
lower_trim_20-anchor row is worse by -1.9
mm relative to the p50 control when expressed as old minus new, and it is worse by
3.2 mm relative to the
best p50/e_i=0 row [source:
FULL_V5_anchor_lower_trim/reports/ANCHOR_LOWER_TRIM_COMPLETION.md;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. More aggressive lower-tail
methods also move the Sim3 scale upward and the rigid RMSE upward, which is consistent
with downward-biased inter-anchor ranges [source:
FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv].

The engineering conclusion is explicit: keep p50/median for inter-anchor ranges in
this room. lower_trim_20 is tag-side, not anchor-side, for the Erlangen static dataset
[source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 13.4 e_i=0 Finding

The best overall anchor lower-trim row is not a lower-trim anchor row. It is p50
with e_i=0: 43.2 mm LOO, P95 163.1 mm,
and RMSE 81.8 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv]. p50 with e_reg=5 is
essentially tied at 43.2 mm. The current p50/e_reg20
control is 44.5 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

The improvement is not yet statistically decisive. The paired bootstrap summary gives
old median 44.5 mm, new median 43.2 mm,
mean improvement 0.5 mm, median improvement
0.4 mm, CI -3.6 to
4.0 mm, and P(new wins) 0.659 [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The result is consistent with the mechanistic M4 counterfactual, where forcing e_i=0
improved a prior V5 median from 67.8 mm to 64.5 mm [source:
FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv]. It is also consistent
with the idea that per-anchor e_i can absorb campaign-specific residual structure.
The cautious recommendation is to test e_i=0 and low e_reg in the next calibration,
not to delete e_i support from the solver [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

### 13.5 Consequences for the V5 Anchor Solver

The lower-trim anchor test separates two V5 design choices that were sometimes
discussed together. The first is the common-mode delay parameterization, which fixes
the V4 scale problem. That remains supported: p50/e_i=0 has Sim3 scale
1.010 and rigid RMSE 63.1 mm, while the
current V5 p50 artifact had scale about 1.010 and rigid RMSE about 63 mm [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. The second is the
per-anchor residual e_i model. That part is less settled. In this experiment, reducing
or zeroing e_i improves the raw-frame static tag result, but the improvement is small
relative to the bootstrap uncertainty [source:
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The practical solver audit implication is that V5 should not be described as one
immutable configuration. It is a family around the common-mode idea: p50 inter-anchor
aggregation, common-mode c, optional e_i, and a chosen e_i regularization. The official
artifact used e_reg20; the blind experiment suggests e_i=0 or e_reg5 as candidate
settings for the next capture. The code and tables should therefore log `e_mode`,
`e_reg_mm`, `c_mm`, `e_i_spread_mm`, and the tag-side range estimator whenever V5
results are reported [source:
FULL_V5_anchor_lower_trim/tables/l2_anchor_solver_results.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

The experiment also gives a rule for future rooms. Before applying lower-tail
aggregation to inter-anchor ranges, inspect the raw AA distribution shape. If skewness
and tail mass resemble the Erlangen AA table, p50 should be retained. If a new room
has genuinely right-skewed inter-anchor distributions, lower_trim_20 or another
LOS-side statistic can be re-tested as a room-specific hypothesis rather than adopted
by default [source: FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].

## 14. Consolidated Findings

The V2 claim-control table keeps the original evidence levels and adds five claims
from the raw-frame and anchor lower-trim work [source: tables/claim_evidence_matrix_v2.csv].
The levels are:

| Level | Claim count | Meaning |
| --- | --- | --- |
| A | 11 | Proven within this campaign |
| B | 10 | Supported with caveats |
| C | 5 | Hypothesis only |
| D | 4 | Disproven or should not be claimed |

### 14.A New and Updated Claims

| ID | Level | Claim | Recommended wording |
| --- | --- | --- | --- |
| 26 | A | lower_trim_20 extracts a static LOS component from raw range histograms | Raw static range histograms contain a recoverable LOS-side component; lower_trim_20 is the current zero-parameter extractor on this campaign. |
| 27 | A | Inter-anchor raw ranges are nearly symmetric; lower_trim_20 is inappropriate for anchor self-calibration here | Use p50/median for inter-anchor self-calibration in this dataset; the lower-tail estimator is tag-side only. |
| 28 | B | e_i=0 or very small e_i is slightly better for the raw-frame static estimator | The e_i=0 variant is the current best row, but its advantage is not statistically locked with 24 positions. |
| 29 | A | When NLOS is reduced by raw-frame LOS extraction, V5 geometry beats V4 | The raw-frame result independently supports the cancellation interpretation: once right-tail range bias is reduced, V5 becomes the better geometry. |
| 30 | D | Parametric mixture models do not beat simple non-parametric lower-trim estimators | Do not claim mixture-model superiority; report lower_trim_20 as the empirically selected static estimator. |

The most important upgrade is claim 26. It is Level A within this campaign because
it is supported by raw data discovery, an exhaustive v3 grid, fold-wise LOO, an oracle
comparison, and bootstrap controls [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md;
FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv]. The claim is limited to
static or batch settings where enough frames exist to form a per-link histogram.

Claim 27 is also Level A within this campaign because the blind anchor test found
the opposite result for inter-anchor ranges. The same lower-tail statistic that helps
tag-anchor static histograms worsens inter-anchor self-calibration in this room [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Claim 29 is the conceptual update to the earlier cancellation story. Earlier evidence
showed V5 fixes scale but V4 wins p50 static positioning. The raw-frame result shows
that V5 wins once tag-anchor right-tail bias is reduced. This does not prove a universal
transfer result, but it does independently support the cancellation explanation for
the p50 ranking [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The Level B e_i claim should be worded as an engineering candidate. p50/e_i=0 gives
the lowest current median, but P(new wins) is 0.659 and the 95% paired improvement
interval crosses zero [source: FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

The new Level D mixture-model claim is a restriction on wording. Mixture models were
tested and did not beat the simple lower_trim_20 row in the final honest result [source:
FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 14.B Consolidated Interpretation

The final campaign narrative is now three-layered. Anchor geometry: V5 fixes the V4
scale leak and reduces anchor coordinate error against Vicon [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. Median static p50
positioning: V4 wins because its scale error cancels part of the tag-anchor range
bias [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. Raw static
histogram positioning: lower_trim_20 removes much of that range bias, and V5 then
becomes the best geometry [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

The raw-frame campaign therefore strengthens the paper, but it also narrows the
deployment claim. The 44.5 mm number is a static batch result. It should not be used
for ROTO single-frame tracking, and it should not be presented as a proven firmware
real-time update until a sliding-window or quasi-static implementation is tested
[source: FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

## 15. Negative Results Summary

The negative results are retained because they prevent overclaiming and separate
static batch improvements from real-time dynamic positioning. The V2 report adds two
negative findings: lower_trim_20 is inappropriate for inter-anchor ranges in this
room, and parametric mixture estimators do not beat the simple non-parametric
lower_trim_20 tag-side estimator [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

| Experiment | Result | Why it failed | Source |
| --- | --- | --- | --- |
| MLP learned range correction | MLP residual median 118.0 mm versus scalar 98.5 mm | 24 static positions were too few for a learned correction model | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| GNN attention correction | attention residual median 121.1 mm | graph model overfit or lacked enough independent data | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| Solver search | best 82.7 mm in GPU discovery; fixed search still about 82.6 mm | no candidate beat V4/V5 baselines after proper D_tag LOO handling | FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv |
| Bayesian Gaussian posterior | 95% coverage 0.33; Student-t increased it to 0.46 | posterior remained under-calibrated | FULL_V5_final_gate/tables/g2_unified_noise_models.csv |
| NLOS detector generalization | random PR-AUC 0.949 collapsed to 0.42-0.55 in hard splits | model memorized anchor identity and campaign-specific structure | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| Rigid two-tag ROTO solver | joint range-level solver 261.8-264.2 mm versus independent 101.1 mm | tested constraint forced geometry but did not solve dynamic range bias | FULL_V5_final_gate/tables/g5_joint_solver_summary.csv |
| p30 dynamic transfer | ROTO p30 best 283.9 mm versus raw/p50 101.5 mm | static percentile aggregation did not transfer to single-frame dynamic ranges | FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md |
| lower_trim_20 for inter-anchor ranges | lower_trim_20-anchor best 46.4 mm versus p50 control 44.5 mm | inter-anchor distributions were nearly symmetric, so lower-tail trimming introduced downward bias | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| Parametric mixture estimators | gaussian_exponential_mix held-out 51.5 mm versus lower_trim_20 honest 44.5 mm | mixtures did not beat the simple non-parametric lower-tail statistic | FULL_V5_rawframe_bruteforce_v2/tables/b6_master_comparison.csv; FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |

The lower_trim_20 inter-anchor failure is not a contradiction of the raw-frame tag
success. It shows that estimator choice must follow the measurement distribution:
right-skewed tag-anchor static histograms benefit from lower-tail extraction, while
nearly symmetric inter-anchor distributions do not [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv].

## 16. Updated Engineering Recommendations

### 16.1 Layer 1: Anchor Self-Calibration

Use p50/median for inter-anchor range aggregation in the Erlangen-style setup. The
inter-anchor raw distributions are nearly symmetric, with mean skewness 0.063, and
lower_trim_20 worsened the blind anchor refit [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Prefer e_i=0 or low e_reg as a next-campaign candidate, not as a locked replacement.
p50/e_i=0 is the current best row at 43.2 mm, p50/e_reg5 is 43.2 mm, and the current
p50/e_reg20 control is 44.5 mm, but the paired bootstrap P(new wins) is 0.659 [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv].

Keep Huber-capable anchor and tag solver paths available. The winning raw-frame static
row uses Huber30, and earlier solver-audit work flagged that solver loss handling
should remain explicit rather than implicit [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
../../solver_audit/reports/SOLVER_AUDIT_SUMMARY.md].

### 16.2 Layer 2: Tag Position Solver

For static or batch operation with at least tens of frames per link, replace p50 with
lower_trim_20 over raw tag-anchor range histograms. The current honest row is 44.5 mm
LOO versus 67.8 mm for the V5 p50 baseline [source:
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_followup_validation/tables/f6_final_comparison.csv].

For real-time dynamic operation with one range frame per solve, do not use lower_trim_20.
There is no histogram. The current ROTO floor remains about 101.5 mm under the
conservative anchor-bridge best-fit alignment [source:
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv]. Huber loss remains applicable
because it acts on anchor residuals within a frame rather than on a per-link temporal
histogram [source: FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

### 16.3 Layer 3: D_tag Calibration

Treat D_tag as per-device and per-estimator. The p50 V5 LOO value is 49.621 mm, but
the lower_trim_20/V5/Huber30 raw-frame row has mean train D_tag 6.9 mm, and the anchor
lower-trim p50/e_i=0 row has mean D_tag 8.5 mm [source:
FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

Do not hardcode the p50 D_tag in firmware for lower-trim static processing. The range
estimator changes the range observation and therefore changes the calibrated D_tag
[source: FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

### 16.4 Layer 4: Firmware and Capture

Keep saving raw per-frame ranges. The raw-frame data already exists in the current
`tr_all.csv` captures, and the V2 data-discovery run confirmed the needed columns and
frame counts [source: FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

Add hardware time synchronization for ROTO if dynamic accuracy remains a paper target.
Time-offset optimization recovered little under the current data, but the alignment
audit shows that reported ROTO error depends strongly on alignment convention [source:
FULL_V5_roto_deepdive/tables/r1_time_corrected_results.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

Continue treating IMU orientation, CIR/NLOS labels, and a 9th anchor as pending
hardware/data improvements. The skipped board-frame analysis and the simulated 9th
anchor result both point to data that cannot be recovered from the current static
tables alone [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

### 16.5 Implementation Checklist

For the next static batch run, the minimum reportable configuration should contain
five fields: anchor layout source, anchor e_i setting, tag range estimator, solver
loss, and D_tag calibration method. A complete row would read, for example:
`V5 p50 anchors, e_i=0, lower_trim_20 tag ranges, Huber30, D_tag LOO`. Without those
fields, the numbers 43.2 mm, 44.5 mm, 49.6 mm, and 67.8 mm can be confused even
though they describe different pipelines [source:
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv;
FULL_V5/tables/static_summary_DLOO.csv].

For firmware or online software, add two explicit operating modes. A real-time mode
should use one frame per solve, Huber residual handling, and a D_tag calibrated for
that mode. A static refinement mode should buffer frames per anchor and compute the
lower_trim_20 statistic before solving. The two modes should not share a hardcoded
D_tag because the range estimator changes the effective bias [source:
FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv].

For analysis scripts, keep the raw-frame inventory check near the front of the
pipeline. The v2 discovery step caught an important distinction: per-link feature
tables with 192 rows do not prove that raw frames are absent; they may simply be
derived summaries. The script should explicitly check static files, raw row counts,
valid row counts, frame counts, and the presence of `anchor_id`, `range_mm`, `valid`,
and `sweep` columns before deciding which estimator family is available [source:
FULL_V5_rawframe_bruteforce_v2/reports/DATA_DISCOVERY.md].

## 17. Open Questions and Recommended Next Steps

The highest priority next experiment is a second-room static repeat using the same
raw-frame pipeline. The V3 result is strong within the Erlangen campaign, but the
bootstrap interval is wide and the dataset still contains only 24 independent static
positions [source: FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv].
The second room should report p50 V4, p50 V5, lower_trim_20 V5, p50/e_i=0 anchor
variants, and the same hard validation splits [source:
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].

A second priority is to test whether lower_trim_20 remains inappropriate for
inter-anchor ranges in a more obstructed anchor deployment. The Erlangen inter-anchor
distributions are nearly symmetric, but a different room could have a genuine
inter-anchor NLOS tail [source:
FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv]. If the skewness
and tail mass are higher in a new room, the anchor-side rule should be retested rather
than assumed.

A third priority is D_tag self-calibration from unknown positions. The current D_tag
values are tied to known static positions or fold-wise known-position residuals.
Deployment needs a way to calibrate a tag with 3-5 arbitrary static placements without
Vicon truth [source:
FULL_V5_extended_mechanism_ablations/tables/item11_calibration_learning_curve_summary.csv;
FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv].

A fourth priority is a quasi-static sliding-window lower-trim mode. lower_trim_20 is
not usable for one-frame ROTO, but it may be useful for slow moving or stationary user
states if the firmware accumulates a short histogram per anchor before solving [source:
FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv;
FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].

The remaining hardware questions from V1 still stand: physical phase-center measurement,
tag-orientation sweeps, CIR firmware with ground-truth NLOS labels, and a real 9th
anchor trial. The raw-frame result makes these more valuable because the static solver
is now close to its oracle lower bound; further improvements will likely need new
observability or new physical measurements rather than more post-hoc scalar tuning
[source: FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv;
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv;
FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv].

## Appendix A. Complete Numerical Registry V2

The following table extends the grand-synthesis registry with raw-frame and anchor
lower-trim entries. Unrounded values are preserved in `tables/master_number_registry_v2.csv`
[source: tables/master_number_registry_v2.csv].

| Theme | Metric | Value | Unit | Source |
| --- | --- | --- | --- | --- |
| ANCHOR CALIBRATION | v4-io_sim3_scale | 0.958 | scale | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v4-io_rigid_anchor_rmse | 105.4 | mm | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v5-commonmode_sim3_scale | 1.010 | scale | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v5-commonmode_rigid_anchor_rmse | 63.0 | mm | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | V5_common_mode_c | 112.0 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| ANCHOR CALIBRATION | V5_e_i_full_spread | 27.7 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| ANCHOR CALIBRATION | V5_e_i_max_abs | 15.4 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| TAG DELAY | D_tag_LOO_p50_V5 | 49.6 | mm | FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv |
| STATIC ACCURACY | V4 production_median_3d | 71.9 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 production_rmse_3d | 110.4 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 baseline_median_3d | 67.8 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 baseline_rmse_3d | 86.4 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 improved_median_3d | 56.0 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 improved_rmse_3d | 79.5 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 improved_median_3d | 54.9 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 improved_rmse_3d | 79.6 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | Vicon improved_median_3d | 56.3 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | Vicon improved_rmse_3d | 81.8 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| TAG DELAY | D_tag_LOO_p30_V5 | 33.0 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | best_recalibrated_percentile_cell | 52.0 | mm | FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv |
| STATIC ACCURACY | bootstrap_median_3d_mean | 57.0 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_median_3d_ci95_low | 54.3 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_median_3d_ci95_high | 63.7 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_mean | 79.7 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_ci95_low | 76.1 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_ci95_high | 85.9 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | nested_cv_height_mean_test_median | 82.9 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | nested_cv_quadrant_mean_test_median | 88.0 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | nested_cv_spatial6_mean_test_median | 94.2 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | mean_optimism_gap_honest_minus_apparent | 9.6 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | std_optimism_gap | 29.6 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | corrected_headline_v4_54p9 | 64.5 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | corrected_headline_v5_56p0 | 65.6 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| TAG DELAY | V4_CV4_range_residual_tier_spread | 11.8 | mm | FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv |
| TAG DELAY | V5_CV5_range_residual_tier_spread | 7.4 | mm | FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv |
| CANCELLATION VALLEY | joint_morph_global_min_alpha | 0.1 | alpha | FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv |
| CANCELLATION VALLEY | joint_morph_global_min_median | 56.4 | mm | FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_alpha | 1.0 | alpha | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_dtag | 88.0 | mm | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_median | 72.6 | mm | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | nullspace_perturbation_ratio_median | 0.267 | ratio | FULL_V5_batch3_falsification/tables/f4_perturbation_ratio.csv |
| IDENTIFIABILITY | fisher_weakest_eigenvalue | 0.0 | eigenvalue | FULL_V5_GPU_tier1/reports/task2_status.json |
| NLOS | shapley_D | 1243 | score | FULL_V5_GPU_discovery/tables/task3_shapley_values.csv |
| NLOS | shapley_F | 1229 | score | FULL_V5_GPU_discovery/tables/task3_shapley_values.csv |
| NLOS | nlos_detector_random_split_pr_auc | 0.9 | PR-AUC | FULL_V5_GPU_discovery/tables/task6_cv_results.csv |
| NLOS | nlos_detector_leave_one_anchor_out_best_pr_auc | 0.5 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | nlos_detector_leave_one_position_out_best_pr_auc | 0.7 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | nlos_detector_leave_one_height_out_best_pr_auc | 0.4 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | student_t_bic_winner | M2_student_t | model | FULL_V5_GPU_discovery/tables/task11_model_evidence.csv |
| DYNAMIC ROTO | E_current_anchor_bridge_existing_beta_overall_median | 101.5 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | F_time_corrected_SE3_overall_median | 82.5 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | D_Sim3_existing_beta_overall_median | 74.3 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | roto_independent_median | 101.1 | mm | FULL_V5_roto_deepdive/tables/r3_joint_summary.csv |
| DYNAMIC ROTO | roto_joint_projection_median | 280.6 | mm | FULL_V5_roto_deepdive/tables/r3_joint_summary.csv |
| DYNAMIC ROTO | gap_D_tag mismatch | 22.9 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Motion blur | 6.4 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Time alignment recoverable | 0.7 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Range aggregation / dynamic single-frame | 0.0 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Unexplained | 15.5 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_TOTAL static-to-dynamic gap | 45.5 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| TRANSFERABILITY | mc_P_V5_lt_V4_corrected_adversarial | 0.300 | probability | FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv |
| TRANSFERABILITY | aa_at_mean_asymmetry | -4.7 | mm | FULL_V5_GPU_discovery/tables/task4_asymmetry_summary.csv |
| IDENTIFIABILITY | anchor_count_4_redundancy | -4 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_4_mean_median_3d | 141.5 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_5_redundancy | -4 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_5_mean_median_3d | 109.0 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_6_redundancy | -3 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_6_mean_median_3d | 89.8 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_7_redundancy | -1 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_7_mean_median_3d | 79.5 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_8_redundancy | 2 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_8_mean_median_3d | 68.7 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_9_redundancy | 6 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_9_mean_median_3d | 60.7 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| ERROR DECOMPOSITION | V4+C_V4+D_LOO_mean_signed_radial | -7.8 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | V5+C_V5+D_LOO_mean_signed_radial | -4.8 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | Vicon+C_cm+D_LOO_mean_signed_radial | -5.1 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | strongest_ei_correlation_predictor | layer_binary | name | FULL_V5_paper_strengthening/tables/p2_ei_correlations.csv |
| ERROR DECOMPOSITION | strongest_ei_correlation_r | -0.5 | r | FULL_V5_paper_strengthening/tables/p2_ei_correlations.csv |
| RAW FRAME | raw_static_rows | 230544 | rows | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| RAW FRAME | raw_static_valid_rows | 228265 | rows | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| RAW FRAME | raw_expected_ratio | 1.001 | ratio | FULL_V5_rawframe_bruteforce_v2/tables/raw_data_inventory.csv |
| RAW FRAME | raw_link_inventory_total_rows | 228265 | frames | FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv |
| RAW FRAME | raw_link_median_frames | 1198 | frames | FULL_V5_rawframe_bruteforce_v3/tables/raw_link_inventory.csv |
| RAW FRAME | rawframe_v3_grid_configs | 107448 | configs | FULL_V5_rawframe_bruteforce_v3/tables/s2_full_grid.csv |
| RAW FRAME | rawframe_transductive_best_median | 40.5 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s2_top50_overall.csv |
| RAW FRAME | rawframe_honest_lower_trim20_v5_huber30_loo | 44.5 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |
| RAW FRAME | rawframe_honest_lower_trim20_v5_huber30_p95 | 164.1 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |
| RAW FRAME | rawframe_honest_lower_trim20_v5_huber30_rmse | 81.5 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s3_honest_ranking.csv |
| RAW FRAME | rawframe_oracle_lower_bound | 44.6 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s6_master_ladder.csv |
| RAW FRAME | rawframe_bootstrap_ci95_low | 33.4 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv |
| RAW FRAME | rawframe_bootstrap_ci95_high | 82.8 | mm | FULL_V5_rawframe_bruteforce_v3/tables/s5_bootstrap_summary.csv |
| ANCHOR LOWER TRIM | aa_valid_rows | 56000 | rows | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| ANCHOR LOWER TRIM | aa_mean_skewness | 0.063 | skewness | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| ANCHOR LOWER TRIM | aa_mean_p50_minus_lower_trim20 | 32.9 | mm | FULL_V5_anchor_lower_trim/tables/l1_inter_anchor_distribution.csv |
| ANCHOR LOWER TRIM | anchor_lower_trim20_best_loo | 46.4 | mm | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| ANCHOR LOWER TRIM | anchor_p50_e_zero_best_loo | 43.2 | mm | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| ANCHOR LOWER TRIM | anchor_p50_reg5_loo | 43.2 | mm | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| ANCHOR LOWER TRIM | anchor_current_p50_control_loo | 44.5 | mm | FULL_V5_anchor_lower_trim/tables/l3_master_comparison.csv |
| ANCHOR LOWER TRIM | anchor_e_zero_p_new_wins | 0.659 | probability | FULL_V5_anchor_lower_trim/tables/l5_bootstrap_summary.csv |

## Appendix B. Consistency Audit V2

| Metric | Source 1 | Value 1 | Source 2 | Value 2 | Discrepancy | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Raw-frame oracle median | rawframe v1 b6 | 44.596 | rawframe v3 s6 ladder | 44.596 | 0.000000 | OK |
| lower_trim_20 V5 Huber30 LOO | rawframe v3 s3 honest | 44.485 | anchor lower trim p50 control | 44.485 | 0.000000 | OK |
| Raw static row count | raw_data_inventory | 230544.000 | expected 24*8*1200 | 230400.000 | 144.000000 | OK |
| p50 e_i=0 best | anchor lower trim l3 | 43.172 | completion report rounded | 43.172 | 0.000320 | OK |

## Appendix C. Source Inventory and Runtime Notes

The V2 report scope contains 23 directories: the original 19 and the four additional
raw-frame/anchor-lower-trim directories [source: tables/report_source_inventory_v2.csv].

| Directory | Exists | CSV files | Report MD files | PNG figures |
| --- | --- | --- | --- | --- |
| FULL_V5 | True | 16 | 1 | 0 |
| FULL_V5_scale_to_vicon | True | 3 | 1 | 0 |
| FULL_V5_align_to_Vicon | True | 5 | 1 | 0 |
| FULL_V5_one_baseline_scale_correction | True | 4 | 1 | 0 |
| FULL_transfer_matrix | True | 8 | 1 | 0 |
| FULL_V4_vs_V5_final | True | 8 | 1 | 0 |
| FULL_V5_mechanism_ablations | True | 17 | 1 | 0 |
| FULL_V5_extended_mechanism_ablations | True | 36 | 1 | 3 |
| FULL_V5_GPU_tier1 | True | 21 | 7 | 7 |
| FULL_V5_GPU_discovery | True | 49 | 19 | 15 |
| FULL_V5_followup_validation | True | 15 | 7 | 0 |
| FULL_V5_overnight_batch2 | True | 21 | 11 | 10 |
| FULL_V5_batch3_falsification | True | 18 | 9 | 8 |
| FULL_V5_roto_deepdive | True | 18 | 7 | 7 |
| FULL_V5_mechanistic_deepdive | True | 27 | 11 | 6 |
| FULL_V5_paper_strengthening | True | 17 | 12 | 10 |
| FULL_V5_grand_synthesis | True | 7 | 9 | 0 |
| FULL_V5_final_gate | True | 17 | 6 | 5 |
| FULL_V5_phase_center_sensitivity | True | 15 | 7 | 8 |
| FULL_V5_rawframe_bruteforce | True | 26 | 7 | 5 |
| FULL_V5_rawframe_bruteforce_v2 | True | 28 | 8 | 5 |
| FULL_V5_rawframe_bruteforce_v3 | True | 27 | 7 | 4 |
| FULL_V5_anchor_lower_trim | True | 14 | 6 | 8 |

The true brute-force v3 completed in 2256.6 s, or 37.6 min. Stage 1 mixture
fitting took 1684.5 s, Stage 2 full grid evaluation took 24.4 s, Stage 3 honest LOO
took 37.4 s, and Stage 5 bootstrap/controls took 510.1 s [source:
FULL_V5_rawframe_bruteforce_v3/tables/cumulative_runtime_summary.csv]. The anchor
lower-trim blind experiment completed in 11.9 s [source:
FULL_V5_anchor_lower_trim/tables/stage_status.csv].

## Appendix D. Figure Manifest

The report directory copies key figures for convenience. The source file remains
the authoritative artifact [source: tables/figure_manifest_v2.csv].

| Copied figure | Caption | Source | Status |
| --- | --- | --- | --- |
| fig01_anchor_layout.png | Anchor layouts: V4, V5, and Vicon. | FULL_V5_overnight_batch2/figures/fig01_anchor_layout.png | copied |
| fig02_static_accuracy_trajectory.png | Static accuracy trajectory. | FULL_V5_overnight_batch2/figures/fig02_static_accuracy_trajectory.png | copied |
| fig03_cancellation_valley.png | Cancellation valley. | FULL_V5_overnight_batch2/figures/fig03_cancellation_valley.png | copied |
| fig04_nlos_fingerprint.png | Per-anchor NLOS fingerprint. | FULL_V5_overnight_batch2/figures/fig05_nlos_fingerprint.png | copied |
| fig05_transfer_matrix_heatmap.png | Transfer matrix heatmap. | FULL_V5_overnight_batch2/figures/fig09_transfer_matrix_heatmap.png | copied |
| fig06_nested_cv_comparison.png | Nested-CV degradation. | FULL_V5_batch3_falsification/figures/f1_nested_cv_comparison.png | copied |
| fig07_profile_alpha_dtag.png | Profile likelihood alpha vs D_tag. | FULL_V5_batch3_falsification/figures/f3_contour_alpha_dtag.png | copied |
| fig08_roto_alignment_comparison.png | ROTO alignment comparison. | FULL_V5_roto_deepdive/figures/r2_alignment_comparison_bar.png | copied |
| fig09_roto_gap_waterfall.png | ROTO gap decomposition. | FULL_V5_roto_deepdive/figures/r4_gap_waterfall.png | copied |
| fig10_anchor_count_identifiability.png | Accuracy versus anchor count. | FULL_V5_mechanistic_deepdive/figures/m5_accuracy_vs_anchors.png | copied |
| fig11_cancellation_mechanism.png | Signed radial mechanism diagnostic. | FULL_V5_paper_strengthening/figures/fig11_cancellation_mechanism.png | copied |
| fig12_phase_center_mc_probabilities.png | Phase-center manufacturing variation probabilities. | FULL_V5_phase_center_sensitivity/figures/a2_ranking_probability_vs_sigma.png | copied |
| fig13_phase_center_valley.png | Phase-center shift on cancellation valley. | FULL_V5_phase_center_sensitivity/figures/a5_operating_point_on_valley.png | copied |
| fig14_rawframe_estimator_ranking.png | Raw-frame estimator ranking. | FULL_V5_rawframe_bruteforce_v3/figures/s2_estimator_ranking.png | copied |
| fig15_rawframe_accuracy_ladder.png | Raw-frame accuracy ladder. | FULL_V5_rawframe_bruteforce_v3/figures/s6_accuracy_ladder.png | copied |
| fig16_rawframe_oracle_gap.png | Raw-frame oracle versus honest gap. | FULL_V5_rawframe_bruteforce_v3/figures/s6_oracle_vs_honest_gap.png | copied |
| fig17_inter_anchor_histograms.png | Inter-anchor raw range histograms. | FULL_V5_anchor_lower_trim/figures/l1_inter_anchor_histograms.png | copied |
| fig18_anchor_lower_trim_accuracy.png | Tag accuracy by anchor aggregation method. | FULL_V5_anchor_lower_trim/figures/l3_accuracy_by_anchor_method.png | copied |
| fig19_anchor_ezero_bootstrap.png | Bootstrap improvement distribution for e_i=0. | FULL_V5_anchor_lower_trim/figures/l5_improvement_distribution.png | copied |

<!-- Word count: 12141 -->
