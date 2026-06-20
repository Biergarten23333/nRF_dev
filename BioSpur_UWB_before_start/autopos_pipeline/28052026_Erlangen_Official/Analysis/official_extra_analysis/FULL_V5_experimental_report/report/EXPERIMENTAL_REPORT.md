# Comprehensive Experimental Report: Erlangen 28-May-2026 V5 Analysis Campaign

Generated: 2026-06-18T17:15:27

This internal report documents the V4/V5 AutoPos analysis campaign for the Erlangen
28-May-2026 Vicon validation dataset. It is a technical record, not a paper draft.
It focuses on the 19 requested V5-era analysis directories listed in
`tables/report_source_inventory.csv` and uses the grand-synthesis registry,
final-gate locked headline table, and phase-center robustness table as the final
authoritative summaries [source: FULL_V5_grand_synthesis/tables/master_number_registry.csv;
FULL_V5_final_gate/tables/g1_locked_headline.csv;
FULL_V5_phase_center_sensitivity/tables/a6_robustness_summary.csv].

## 0. Executive Summary

The campaign covered 19 requested analysis directories, with the grand synthesis
scanning 23 total analysis directories and collecting 79 registry entries [source:
FULL_V5_grand_synthesis/reports/GRAND_SYNTHESIS.md]. The dataset contains 24 static# CODEX PROMPT — Complete Solver Algorithm Audit: V1-V5 & T1-T5

## MACHINE
- i7-8700K 6C/12T, 32GB RAM. CPU only (read-only audit, no computation).

## PURPOSE
Produce a complete, structured audit of EVERY solver version in the AutoPos codebase:
- **V1 through V5**: Anchor Layout Self-Calibration solvers
- **T1 through T5** (or however many exist): Tag Position solvers

For each version: document the exact algorithm, parameterization, cost function, constraints, inputs, outputs, and key differences from the previous version.

**This is a READ-ONLY audit. Do NOT modify any code.**

## BASE PATHS
```
BASE=/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline
```

## OUTPUT
```
$BASE/Analysis/solver_audit/
├── reports/
│   ├── ANCHOR_SOLVER_AUDIT.md      (V1-V5 complete)
│   ├── TAG_SOLVER_AUDIT.md         (T1-T5 complete)
│   └── SOLVER_AUDIT_SUMMARY.md     (comparison tables)
├── tables/
│   ├── anchor_solver_versions.csv
│   ├── tag_solver_versions.csv
│   └── parameter_comparison.csv
└── code_excerpts/
    ├── v1_anchor_core.py           (extracted key functions, read-only copy)
    ├── v2_anchor_core.py
    ├── ...
    ├── t1_tag_core.py
    └── ...
```

---

# STEP 1: FIND ALL SOLVER CODE

## 1A: Search for anchor layout solver versions
```bash
# Find all solver-related Python files:
find $BASE -name "*.py" -type f | xargs grep -l "anchor.*solver\|layout.*solver\|self_cal\|selfcal\|v1\|v2\|v3\|v4\|v5\|anchor.*position\|inter_anchor\|anchor.*delay\|common.mode" 2>/dev/null | sort | uniq

# Find version-specific directories:
find $BASE -type d -name "*v1*" -o -name "*v2*" -o -name "*v3*" -o -name "*v4*" -o -name "*v5*" -o -name "*solver*" -o -name "*layout*" -o -name "*calib*" | sort

# Find config files that define versions:
find $BASE -name "*.json" -o -name "*.yaml" -o -name "*.toml" | xargs grep -l "v1\|v2\|v3\|v4\|v5\|version\|solver" 2>/dev/null | sort

# Check the known solver output path:
ls -la $BASE/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/
ls -la $BASE/28052026_Erlangen_Official/solver/
find $BASE/28052026_Erlangen_Official/solver/ -name "*.py" -type f
```

## 1B: Search for tag position solver versions
```bash
find $BASE -name "*.py" -type f | xargs grep -l "tag.*solver\|tag.*position\|solve.*position\|position.*solver\|trilaterat\|multilater\|least_squares.*tag\|t1\|t2\|t3\|t4\|t5" 2>/dev/null | sort | uniq

# Find tag solver functions:
find $BASE -name "*.py" -type f | xargs grep -l "def.*solve.*tag\|def.*tag.*solve\|def.*position.*solve\|def.*solve.*position" 2>/dev/null | sort
```

## 1C: Search for shared utilities
```bash
find $BASE -name "*.py" -type f | xargs grep -l "huber\|robust\|loss.*function\|weight.*function\|aggregate.*range\|range.*aggregate\|percentile\|lower_trim\|median.*range" 2>/dev/null | sort | uniq
```

**List ALL files found. Group by: anchor solver, tag solver, utilities, config.**

---

# STEP 2: READ AND DOCUMENT EACH ANCHOR SOLVER VERSION

For each version V1 through V5 (and any others found):

### 2A: Identify the version
- File path
- Version identifier (how is it named in code?)
- Date/commit info if available (file modification time at minimum)

### 2B: Document the algorithm
For each version, extract and document:

```
VERSION: V?
FILE: /path/to/file.py
FUNCTION(S): function_name(args)

=== INPUTS ===
- Inter-anchor ranges: how are they provided? (raw frames, aggregated, format)
- Range aggregation: how are raw ranges reduced to one value per pair?
  (median, p30, lower_trim, custom?)
- Number of anchor pairs: C(N,2) for N anchors
- Any external constraints? (known positions, known distances, priors)

=== UNKNOWNS (what the solver estimates) ===
- Anchor positions: [N, 3]? How parameterized?
- Anchor delays: independent? common-mode c + e_i? bounded?
  - d_i bounds: what range?
  - e_reg: what value?
  - gauge constraint: which anchor is fixed?
- Any other unknowns? (scale? rotation? bias terms?)

=== COST FUNCTION ===
- What is minimized?
  - Sum of squared range residuals?
  - Weighted? How?
  - Robust loss? (Huber, Cauchy, soft_l1?)
  - Regularization terms? On what? With what weight?
- Write out the EXACT mathematical form if possible:
  min_{A, d} Σ_{i<j} ρ(r_ij_measured - ||A_i - A_j|| - d_i - d_j)
  + λ * regularization_term

=== OPTIMIZER ===
- scipy.optimize.least_squares? minimize? custom?
- loss parameter?
- f_scale?
- bounds?
- max iterations?
- tolerance?
- initialization strategy?

=== OUTPUTS ===
- Anchor positions format
- Anchor delays format
- Common-mode c? e_i?
- Any quality metrics?
- Output file format (JSON, CSV, etc.)

=== KEY DIFFERENCE FROM PREVIOUS VERSION ===
- What changed from V(n-1) to V(n)?
- Why was this change made? (if documented in comments/commits)
```

### 2C: Extract core code
Copy the key function(s) to `code_excerpts/v?_anchor_core.py` (read-only copy for reference).
Include only the core algorithm, not data loading/plotting boilerplate.

---

# STEP 3: READ AND DOCUMENT EACH TAG SOLVER VERSION

Same structure as Step 2, but for tag position solvers:

```
VERSION: T?
FILE: /path/to/file.py
FUNCTION(S): function_name(args)

=== INPUTS ===
- Tag-anchor ranges: raw frames or aggregated?
- Range aggregation method: median, p30, lower_trim_20?
- Anchor positions: which version? (V4, V5, Vicon?)
- Anchor delays: from which anchor solver?
- D_tag: how determined? Fixed? LOO? Per-device?
- Weighting: uniform? inverse-RMS? per-anchor?

=== UNKNOWNS ===
- Tag position: [3] (x, y, z)?
- Any per-solve unknowns? (D_tag? bias terms?)

=== COST FUNCTION ===
- min_{p} Σ_i ρ(r_i - ||p - A_i|| - d_i - D_tag)
- Loss function: L2? Huber? Student-t?
- f_scale if Huber?
- Weights?

=== OPTIMIZER ===
- scipy least_squares? Custom Gauss-Newton? PyTorch?
- Initialization: centroid? previous position? multiple starts?
- Convergence criteria?

=== OUTPUTS ===
- Tag position
- Residuals?
- Quality metrics?

=== KEY DIFFERENCE FROM PREVIOUS VERSION ===
```

---

# STEP 4: BUILD COMPARISON TABLES

### Table 1: Anchor Solver Version Comparison
```
| Feature | V1 | V2 | V3 | V4 | V5 |
|---|---|---|---|---|---|
| Delay model | ? | ? | ? | bounded independent | common-mode c + e_i |
| Delay bounds | ? | ? | ? | [-60, +60] | via e_reg |
| e_reg | N/A | N/A | N/A | N/A | 20.0 |
| Gauge constraint | ? | ? | ? | d_A=0 | d_A=0? |
| Range aggregation | ? | ? | ? | p50 | p50 |
| Loss function | ? | ? | ? | L2? | L2? |
| Optimizer | ? | ? | ? | ? | ? |
| N anchors | ? | ? | ? | 8 | 8 |
| Scale fix? | ? | ? | ? | No | Yes (via c) |
| Sim3 scale result | ? | ? | ? | 0.958 | 1.010 |
```

### Table 2: Tag Solver Version Comparison
```
| Feature | T1 | T2 | T3 | T4 | T5 |
|---|---|---|---|---|---|
| Range input | ? | ? | ? | ? | ? |
| Aggregation | ? | ? | ? | p50 | p50 |
| Loss function | ? | ? | ? | L2? | L2? |
| D_tag method | ? | ? | ? | ? | LOO |
| Weighting | ? | ? | ? | uniform | uniform |
| Optimizer | ? | ? | ? | ? | ? |
```

### Table 3: Parameter Evolution
```
| Parameter | V1/T1 | V2/T2 | V3/T3 | V4/T4 | V5/T5 | Recommended |
|---|---|---|---|---|---|---|
| e_reg | - | - | - | - | 20.0 | 0.0 (e_i=0) |
| inter-anchor agg | ? | ? | ? | p50 | p50 | p50 (keep) |
| tag-anchor agg | ? | ? | ? | p50 | p50 | lower_trim_20 |
| tag solver loss | ? | ? | ? | L2 | L2 | Huber(δ=30) |
| anchor solver loss | ? | ? | ? | ? | ? | Huber(δ=30) |
```

---

# STEP 5: FLAG ISSUES AND RECOMMENDATIONS

For each solver version, flag:
1. **Hardcoded values** that should be configurable
2. **Missing features** (no Huber option, no lower_trim option, etc.)
3. **Inconsistencies** between versions (e.g., V4 uses one convention, V5 uses another)
4. **Dead code** (solver versions that are never called)
5. **Documentation gaps** (undocumented parameters, unclear variable names)

---

## FINAL REPORT

`reports/SOLVER_AUDIT_SUMMARY.md`:

```
# AutoPos Solver Audit Summary

## Anchor Layout Solvers Found: N versions (V1-V?)
## Tag Position Solvers Found: N versions (T1-T?)

## Version History (brief):
V1: [date?] - [one-line description]
V2: [date?] - [one-line description]
...

## Current Production Configuration:
- Anchor solver: V? with parameters: ...
- Tag solver: T? with parameters: ...

## Recommended Changes (from Erlangen campaign):
1. ...
2. ...
3. ...

## Code Quality Issues:
1. ...
2. ...
```
positions and 17 ROTO captures with DWM1001C UWB devices and Vicon/OptiTrack ground
truth [source: FULL_V5/tables/static_summary_DLOO.csv; FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
The static V5 baseline uses 28,818 solved static frames [source:
FULL_V5/tables/static_summary_DLOO.csv]. The final ROTO joint-gate table covers
15,717 paired dynamic frames for the two-tag rigid test [source:
FULL_V5_final_gate/tables/g5_joint_solver_summary.csv].

Top-level findings:

- V5 fixes the anchor-side scale leak: V4 Sim3 scale is 0.958 and V5
  Sim3 scale is 1.010; rigid anchor RMSE improves from
  105.4 mm to 63.0 mm [source:
  FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv].
- V4 remains the empirical p50 static median winner on this 24-position campaign:
  V4+C_V4+D_LOO gives 57.9 mm and V5+C_V5+D_LOO gives
  67.8 mm [source: FULL_transfer_matrix/tables/transfer_matrix_48cells.csv].
- The best post-selected static rows are close: V4 apparent best is 54.9 mm, V5
  apparent best is 56.0 mm, and Vicon apparent best is 56.3 mm, all with p30 and
  inverse-RMS weighting [source: FULL_V5_followup_validation/tables/f6_final_comparison.csv].
- Winner's-curse correction moves the V4/V5 improved medians to about 64.5 mm and
  65.6 mm, respectively, and hard nested-CV medians range from 82.9 mm to 94.2 mm
  [source: FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv;
  FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv].
- The cancellation valley is supported by transfer-matrix, morph-valley, Fisher,
  profile-likelihood, and nullspace evidence, but the specific signed-radial
  mechanism remains only suggestive [source: FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv;
  FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv;
  FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv].
- ROTO remains a dynamic limitation: the conservative V5 best-fit-aligned median is
  101.5 mm, time-corrected SE(3) is 82.5 mm, and diagnostic Sim3 is 74.3 mm [source:
  FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv].
- Several tempting claims were falsified or downgraded: NLOS detector generalization,
  rigid-body ROTO improvement, universal deployability of p30, and strong transfer
  superiority of V5 [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

### Locked Headline Table

| Row | Variant | Description | Median 3D mm | P95 mm | RMSE mm | Evaluation | Source |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A | V4 production | p50, uniform, D=0 | 71.9 | 176.0 | 110.4 | in-sample, all 24 | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| B | V4 + D_LOO | p50, uniform, D_LOO | 57.9 | 110.6 | 74.4 | LOO-CV | FULL_transfer_matrix/tables/transfer_matrix_48cells.csv |
| C | V5 baseline | p50, uniform, D_LOO=49.6 | 67.8 | 160.5 | 86.4 | LOO-CV | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| D | V5 apparent best | p30, invRMS, D_recal=33.0 | 56.0 | 143.1 | 79.5 | in-sample post-selected | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| E | V4 apparent best | p30, invRMS, D_recal=18.2 | 54.9 | 154.8 | 79.6 | in-sample post-selected | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| F | V5 corrected | winner's curse adjustment | 65.6 |  |  | OOB-bootstrap | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| G | V4 corrected | winner's curse adjustment | 64.5 |  |  | OOB-bootstrap | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| H | V5 bootstrap CI | 95% CI [54.3, 63.7] |  |  |  | bootstrap 95% CI | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| I | Nested CV (height) | best variant selected on train | 82.9 |  |  | held-out test | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| J | Nested CV (quadrant) | best variant selected on train | 88.0 |  |  | held-out test | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| K | Nested CV (spatial6) | best variant selected on train | 94.2 |  |  | held-out test | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| L | ROTO V5 per-frame | anchor-bridge best-fit | 101.5 | 214.4 | 126.2 | BEST-FIT-ALIGNED | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| M | ROTO SE(3) aligned | per-capture SE(3) | 82.5 | 185.2 | 103.7 | diagnostic | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| N | ROTO Sim3 aligned | per-capture Sim3, scale 0.906 | 74.3 | 160.8 | 94.8 | diagnostic only | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |

### Claim Confidence Levels

| Level | Claim count | Meaning |
| --- | --- | --- |
| A | 8 | Proven within this campaign |
| B | 9 | Supported with caveats |
| C | 5 | Hypothesis only |
| D | 3 | Disproven or should not be claimed |

The locked table is the authoritative number set for subsequent writing. The claim
matrix contains 25 claims: 8 Level A, 9 Level B, 5 Level C,
and 3 Level D [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].

Three interpretation rules were adopted for this report. First, a result labeled
LOO-CV is cross-validated only inside the same 24-position Erlangen campaign; it is
not an independent external holdout [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv;
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv]. Second, rows labeled
in-sample, post-selected, D_sweep_opt, p30, or inverse-RMS best are diagnostic unless
the corresponding hard-split or bootstrap table also supports them [source:
FULL_V5_followup_validation/tables/f6_final_comparison.csv;
FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv]. Third, every ROTO
number depends on alignment convention; only the 101.5 mm anchor-bridge row is the
conservative current BEST-FIT-ALIGNED headline, while SE(3) and Sim3 rows are
diagnostic alignment audits [source: FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv;
FULL_V5_final_gate/tables/g1_locked_headline.csv].

The same caution applies to physical interpretation. V5's common-mode calibration
is a geometry result: it corrects the V4 scale defect from 0.958 to 1.010 and reduces
rigid anchor RMSE from 105.4 mm to 63.0 mm [source:
FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv]. The lower V4 tag-error
median is an empirical positioning result on this campaign: 57.9 mm for V4+LOO
versus 67.8 mm for V5+LOO [source:
FULL_transfer_matrix/tables/transfer_matrix_48cells.csv]. Those two facts can both
be true because anchor-side metric correctness and tag-side error cancellation are
different measurements. This report keeps those axes separate throughout, because
mixing them is the main way to overstate either V4 or V5.
All later claim labels should be read through that separation rather than as a
single ranking of calibration methods [source:
FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].
This is also why both positive and negative results are retained in the same
document.
The intent is reproducibility for future re-analysis, not selective presentation.


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
FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv].

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
[source: FULL_V5_mechanistic_deepdive/tables/m4_counterfactual.csv].

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


## 12.A What is proven within this campaign

| ID | Claim | Recommended wording |
| --- | --- | --- |
| 1 | V5 fixes V4's scale leak (0.958 -> 1.010) | V5 corrects the anchor-side scale defect on this campaign. |
| 3 | V4 gives better single-dataset positioning than V5 | V4 is the empirical static median winner on this 24-position campaign. |
| 9 | Fisher eigenvalue 1e-6 proves weak identifiability | The calibration has a measurable weak direction. |
| 10 | D/F are NLOS-heavy but geometrically essential | D/F are not simply removable outliers. |
| 15 | MC transfer result has V4 solver fidelity caveat | State the caveat explicitly. |
| 18 | ROTO accuracy is ~101 mm best-fit aligned | Report as BEST-FIT-ALIGNED only. |
| 19 | Static-dynamic gap is ~40 mm | The dynamic floor remains about 45 mm above static best. |
| 21 | AA-AT asymmetry is small | AA/AT asymmetry is small in this dataset. |

This block is copied from the grand-synthesis claim matrix and should be treated as
the campaign-level claim-control table [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].


## 12.B What is supported with caveats

| ID | Claim | Recommended wording |
| --- | --- | --- |
| 2 | V5 has more stable per-height D_tag | V5 reduces some geometry-induced tag-delay aliasing, but stability depends on the criterion. |
| 4 | The reason is scale-delay-NLOS cancellation | The lower V4 error is consistent with beneficial cancellation rather than proven by one statistic. |
| 6 | Vicon worse could be phase-center offset | Phase-center mismatch is a plausible alternative and should be stated. |
| 7 | p30 improvement is another cancellation | p30 is a strong batch-processing hypothesis, not a universal correction. |
| 11 | NLOS detectable from range statistics without CIR | Range statistics contain NLOS signal, but deployment generalization is unproven. |
| 13 | Student-t is the correct noise model | Student-t best describes this residual distribution. |
| 17 | p30 does not transfer to dynamic | p30 helped static batch ranges but not ROTO enough to change the dynamic floor. |
| 20 | 24 positions insufficient for learned methods | The current campaign is too small for strong learned-method claims. |
| 24 | Winner's curse gap is < X mm | Use corrected medians for paper headline sensitivity. |

This block is copied from the grand-synthesis claim matrix and should be treated as
the campaign-level claim-control table [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].


## 12.C What remains hypothesis only

| ID | Claim | Recommended wording |
| --- | --- | --- |
| 5 | Vicon oracle worse than self-cal proves cancellation | The Vicon result is compatible with cancellation but not uniquely diagnostic. |
| 14 | V5 transfers better to new rooms | V5 is expected to transfer better, but this needs direct validation. |
| 16 | D_tag is device-specific | Treat per-device D_tag as likely, not proven. |
| 23 | Headline numbers survive nested CV | Hard nested CV weakens, rather than confirms, aggressive headline claims. |
| 25 | Cancellation valley has specific radial mechanism | Radial decomposition is suggestive but not a stand-alone proof. |

This block is copied from the grand-synthesis claim matrix and should be treated as
the campaign-level claim-control table [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].


## 12.D What was disproven or should not be claimed

| ID | Claim | Recommended wording |
| --- | --- | --- |
| 8 | Every post-processing improvement benefits V4 more than V5 | Do not claim universal superiority; report the tested comparison. |
| 12 | NLOS detector generalizes across positions/anchors | Do not claim generalization yet. |
| 22 | Rigid body constraint improves ROTO | Do not claim improvement from the tested rigid projection. |

This block is copied from the grand-synthesis claim matrix and should be treated as
the campaign-level claim-control table [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv].


## 13. Negative Results Summary

The negative results are part of the experimental record. They prevent accidental
overclaiming and identify the boundaries of this dataset.

| Experiment | Result | Why it failed | Source |
| --- | --- | --- | --- |
| MLP learned range correction | MLP residual median 118.0 mm versus scalar 98.5 mm | 24 static positions were too few for a learned correction model | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| GNN attention correction | attention residual median 121.1 mm | graph model overfit or lacked enough independent data | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| Solver search | best 82.7 mm in GPU discovery; fixed search still about 82.6 mm | no candidate beat V4/V5 baselines after proper D_tag LOO handling | FULL_V5_overnight_batch2/tables/n2_solver_search_fixed.csv |
| Layout optimization | best optimized median 78.3 mm with mean anchor move 88.0 mm | optimized layout remained worse than baseline static results | FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md |
| Bayesian Gaussian posterior | 95% coverage 0.33; Student-t increased it to 0.46 | posterior remained under-calibrated | FULL_V5_final_gate/tables/g2_unified_noise_models.csv |
| NLOS detector generalization | random PR-AUC 0.949 collapsed to 0.42-0.55 in hard splits | model memorized anchor identity and campaign-specific structure | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| Rigid two-tag ROTO solver | joint range-level solver 261.8-264.2 mm versus independent 101.1 mm | tested constraint forced geometry but did not solve dynamic range bias | FULL_V5_final_gate/tables/g5_joint_solver_summary.csv |
| p30 dynamic transfer | ROTO p30 best 283.9 mm versus raw/p50 101.5 mm | static percentile aggregation did not transfer to single-frame dynamic ranges | FULL_V5_followup_validation/reports/FOLLOWUP_VALIDATION_SUMMARY.md |
| Dynamic NLOS weighting | soft NLOS weighting 104.2 mm versus approximately 104.9 mm baseline in that diagnostic | sliding-window features did not materially change the dynamic floor | FULL_V5_roto_deepdive/tables/r5_nlos_dynamic_results.csv |

These failures collectively show that the dataset is large enough to diagnose
scale-delay behavior and NLOS fingerprints, but not large enough to support strong
claims about learned correction models, deployable NLOS classification, or dynamic
rigid-body improvement.


## 14. Open Questions and Recommended Next Steps

The remaining high-value work is experimental rather than computational. First,
repeat the static validation in a second room with at least 6 known positions and
the same V4/V5 analysis stack. This is required before claiming V5 transfers better
than V4 [source: FULL_V5_grand_synthesis/tables/claim_evidence_matrix.csv]. Second,
measure antenna phase-center offsets physically using a ruler/caliper setup and a
known fixture, because the Vicon-marker result is compatible with phase-center
offset but not settled by the existing optical data [source:
FULL_V5_final_gate/tables/g3_phase_center_summary.csv].

Third, perform a tag-orientation sweep at one or more fixed positions. The current
board-frame incidence task was skipped because board orientation input was unavailable,
and the direction-dependent phase-center sensitivity remains a fitted proxy [source:
FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md;
FULL_V5_phase_center_sensitivity/tables/a4_best_fit.csv]. Fourth, test a real 9th
anchor. The simulated 9th anchor improves redundancy from +2 to +6 and mean median
from about 68.7 mm to 60.7 mm, but this needs hardware confirmation [source:
FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv].

Fifth, add CIR firmware and ground-truth NLOS labels. The NLOS detector has strong
random-split PR-AUC but weak leave-anchor and leave-height performance, so the next
dataset should separate link physics from anchor identity [source:
FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv]. Sixth, repeat the same
pipeline on DW3000 or another radio generation to determine whether the delay/NLOS
cancellation behavior is specific to this DW1000/DWM1001C campaign.


## Appendix A. Complete Numerical Registry

The following table is copied from the grand-synthesis registry and rounded for this
report. The unrounded values are preserved in `tables/master_number_registry.csv`
[source: FULL_V5_grand_synthesis/tables/master_number_registry.csv].

| Theme | Metric | Value | Unit | Source |
| --- | --- | --- | --- | --- |
| ANCHOR CALIBRATION | v4-io_sim3_scale | 0.9582672713308588 | scale | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v4-io_rigid_anchor_rmse | 105.41990950811795 | mm | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v5-commonmode_sim3_scale | 1.0097822800764376 | scale | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | v5-commonmode_rigid_anchor_rmse | 62.99190234655519 | mm | FULL_V5_scale_to_vicon/tables/v5_vs_v4_scale_comparison.csv |
| ANCHOR CALIBRATION | V5_common_mode_c | 111.98479117186592 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| ANCHOR CALIBRATION | V5_e_i_full_spread | 27.703674623990402 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| ANCHOR CALIBRATION | V5_e_i_max_abs | 15.352958619571629 | mm | FULL_V5/tables/delay_comparison_v4_vs_v5.csv |
| TAG DELAY | D_tag_LOO_p50_V5 | 49.621032516254864 | mm | FULL_4way_comparison/tables/v5_loo_tag_delay_summary.csv |
| STATIC ACCURACY | V4 production_median_3d | 71.87489868113639 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 production_rmse_3d | 110.3731093480212 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 baseline_median_3d | 67.80925857328509 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 baseline_rmse_3d | 86.39989161399005 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 improved_median_3d | 56.01127291158505 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V5 improved_rmse_3d | 79.48190537495643 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 improved_median_3d | 54.918375844369336 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | V4 improved_rmse_3d | 79.58617506422262 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | Vicon improved_median_3d | 56.327823937608464 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | Vicon improved_rmse_3d | 81.78860924977711 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| TAG DELAY | D_tag_LOO_p30_V5 | 32.98564490395356 | mm | FULL_V5_followup_validation/tables/f6_final_comparison.csv |
| STATIC ACCURACY | best_recalibrated_percentile_cell | 51.967964830439904 | mm | FULL_V5_followup_validation/tables/f4_percentile_recalibrated.csv |
| STATIC ACCURACY | bootstrap_median_3d_mean | 56.980035432925305 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_median_3d_ci95_low | 54.32221417665503 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_median_3d_ci95_high | 63.74947450556645 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_mean | 79.74367972561129 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_ci95_low | 76.13187169396316 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | bootstrap_rmse_ci95_high | 85.94714945150997 | mm | FULL_V5_overnight_batch2/tables/n6_bootstrap_ci.csv |
| STATIC ACCURACY | nested_cv_height_mean_test_median | 82.9248283137559 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | nested_cv_quadrant_mean_test_median | 88.04166953831984 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | nested_cv_spatial6_mean_test_median | 94.24971154923936 | mm | FULL_V5_batch3_falsification/tables/f1_nested_cv_summary.csv |
| STATIC ACCURACY | mean_optimism_gap_honest_minus_apparent | 9.568149640202272 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | std_optimism_gap | 29.610120273850693 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | corrected_headline_v4_54p9 | 64.48614964020227 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| STATIC ACCURACY | corrected_headline_v5_56p0 | 65.57914964020227 | mm | FULL_V5_batch3_falsification/tables/f2_optimism_summary.csv |
| TAG DELAY | V4_CV4_range_residual_tier_spread | 11.769944229102038 | mm | FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv |
| TAG DELAY | V5_CV5_range_residual_tier_spread | 7.44441095463543 | mm | FULL_V5_extended_mechanism_ablations/tables/item04_nlos_excluded_dtag.csv |
| CANCELLATION VALLEY | joint_morph_global_min_alpha | 0.15 | alpha | FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv |
| CANCELLATION VALLEY | joint_morph_global_min_median | 56.365467753849146 | mm | FULL_V5_extended_mechanism_ablations/tables/item06_morph_markers.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_alpha | 0.98 | alpha | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_dtag | 88.0 | mm | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | profile_alpha_dtag_min_median | 72.60865783691406 | mm | FULL_V5_batch3_falsification/tables/f3_profile_alpha_dtag.csv |
| CANCELLATION VALLEY | nullspace_perturbation_ratio_median | 0.26654862455007944 | ratio | FULL_V5_batch3_falsification/tables/f4_perturbation_ratio.csv |
| IDENTIFIABILITY | fisher_weakest_eigenvalue | 1e-06 | eigenvalue | FULL_V5_GPU_tier1/reports/task2_status.json |
| NLOS | shapley_D | 1242.8861577806022 | score | FULL_V5_GPU_discovery/tables/task3_shapley_values.csv |
| NLOS | shapley_F | 1229.4412957418533 | score | FULL_V5_GPU_discovery/tables/task3_shapley_values.csv |
| NLOS | nlos_detector_random_split_pr_auc | 0.9485277033169752 | PR-AUC | FULL_V5_GPU_discovery/tables/task6_cv_results.csv |
| NLOS | nlos_detector_leave_one_anchor_out_best_pr_auc | 0.5480578449328449 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | nlos_detector_leave_one_position_out_best_pr_auc | 0.7456140350877193 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | nlos_detector_leave_one_height_out_best_pr_auc | 0.3716080931435208 | PR-AUC | FULL_V5_batch3_falsification/tables/f5_nlos_splits.csv |
| NLOS | student_t_bic_winner | M2_student_t | model | FULL_V5_GPU_discovery/tables/task11_model_evidence.csv |
| DYNAMIC ROTO | E_current_anchor_bridge_existing_beta_overall_median | 101.48477739653228 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | F_time_corrected_SE3_overall_median | 82.51641444727665 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | D_Sim3_existing_beta_overall_median | 74.26356175882631 | mm | FULL_V5_roto_deepdive/tables/r2_alignment_summary.csv |
| DYNAMIC ROTO | roto_independent_median | 101.0842005143932 | mm | FULL_V5_roto_deepdive/tables/r3_joint_summary.csv |
| DYNAMIC ROTO | roto_joint_projection_median | 280.6019582729091 | mm | FULL_V5_roto_deepdive/tables/r3_joint_summary.csv |
| DYNAMIC ROTO | gap_D_tag mismatch | 22.85996656553211 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Motion blur | 6.39234421534392 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Time alignment recoverable | 0.7164016763858854 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Range aggregation / dynamic single-frame | 0.0 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_Unexplained | 15.505064939270362 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| DYNAMIC ROTO | gap_TOTAL static-to-dynamic gap | 45.47377739653228 | mm | FULL_V5_roto_deepdive/tables/r4_gap_decomposition.csv |
| TRANSFERABILITY | mc_P_V5_lt_V4_corrected_adversarial | 0.3 | probability | FULL_V5_overnight_batch2/tables/n1_adversarial_rooms.csv |
| TRANSFERABILITY | aa_at_mean_asymmetry | -4.664114889518814 | mm | FULL_V5_GPU_discovery/tables/task4_asymmetry_summary.csv |
| IDENTIFIABILITY | anchor_count_4_redundancy | -4.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_4_mean_median_3d | 141.46838925386066 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_5_redundancy | -4.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_5_mean_median_3d | 108.97585840358236 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_6_redundancy | -3.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_6_mean_median_3d | 89.76991030880558 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_7_redundancy | -1.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_7_mean_median_3d | 79.50112868398043 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_8_redundancy | 2.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_8_mean_median_3d | 68.73280887712593 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_9_redundancy | 6.0 | count | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| IDENTIFIABILITY | anchor_count_9_mean_median_3d | 60.66535924468193 | mm | FULL_V5_mechanistic_deepdive/tables/m5_identifiability_table.csv |
| ERROR DECOMPOSITION | V4+C_V4+D_LOO_mean_signed_radial | -7.753639229517627 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | V5+C_V5+D_LOO_mean_signed_radial | -4.834622887491421 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | Vicon+C_cm+D_LOO_mean_signed_radial | -5.094716962162548 | mm | FULL_V5_paper_strengthening/tables/p1_signed_radial_summary.csv |
| ERROR DECOMPOSITION | strongest_ei_correlation_predictor | layer_binary | name | FULL_V5_paper_strengthening/tables/p2_ei_correlations.csv |
| ERROR DECOMPOSITION | strongest_ei_correlation_r | -0.4598901872672946 | r | FULL_V5_paper_strengthening/tables/p2_ei_correlations.csv |

## Appendix B. Consistency Audit

| Metric | Source 1 | Value 1 | Source 2 | Value 2 | Discrepancy | Status |
| --- | --- | --- | --- | --- | --- | --- |
| V5+C_V5+D_LOO median_3d | FULL_V5/static_summary_DLOO | 67.849 | FULL_transfer_matrix | 67.849 | 0.000000 | OK |
| V5+C_V5+D_LOO median_3d | FULL_transfer_matrix | 67.849 | followup/f6 V5 baseline | 67.809 | 0.039472 | OK |
| V4+C_V4+D_LOO median_3d | FULL_transfer_matrix | 57.921 | mechanism summary expected | 57.921 | 9.410e-08 | OK |
| D_tag LOO | FULL_4way | 49.621 | FULL_V5/static_summary_DLOO | 49.621 | 3.252e-05 | OK |
| Shapley D | GPU_tier1 | 1242.886 | GPU_discovery | 1242.886 | 0.000000 | OK |
| Shapley F | GPU_tier1 | 1229.441 | GPU_discovery | 1229.441 | 0.000000 | OK |
| NLOS PR-AUC best | GPU_tier1 | 0.952 | GPU_discovery | 0.949 | 0.003960 | OK |

## Appendix C. Source Inventory and Runtime Notes

The requested report scope contains 19 directories. The inventory below records
CSV/report/figure counts at generation time [source: tables/report_source_inventory.csv].

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

The long-running CPU ablations were the extended mechanism items: Item 06 took
1494.0 s, Item 07 took 649.8 s, and the extended batch total wall time was 2714.7 s
[source: FULL_V5_extended_mechanism_ablations/reports/EXTENDED_MECHANISM_ABLATION_SUMMARY.md].
The GPU Tier 1 run took 12.08 min, and the full GPU discovery run took 19.06 min
[source: FULL_V5_GPU_tier1/reports/OVERNIGHT_COMPLETION.md;
FULL_V5_GPU_discovery/reports/OVERNIGHT_COMPLETION.md]. Later CPU batches were shorter:
falsification took 233.5 s, ROTO deep-dive 176.0 s, final gates 147.1 s, and
phase-center sensitivity 29.8 s [source:
FULL_V5_grand_synthesis/tables/directory_index.csv;
FULL_V5_final_gate/tables/final_gate_task_status.csv;
FULL_V5_phase_center_sensitivity/tables/phase_center_task_status.csv].

## Appendix D. Figure Manifest

The report directory copies key figures for convenience. The source file remains
the authoritative artifact.

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


<!-- Word count: 8008 -->
