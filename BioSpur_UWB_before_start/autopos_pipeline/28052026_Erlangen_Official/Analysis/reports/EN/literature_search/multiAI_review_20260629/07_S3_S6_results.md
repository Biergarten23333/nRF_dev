# S3 (significance) + S6 (number provenance) — DATA-BACKED RESULTS
# 2026-06-29. Computed from the actual Erlangen static validation data, n=24 positions.
# Scripts: scratchpad/s3s6_probe.py, scratchpad/s3_test.py
# Data: official_extra_analysis/FULL_V5/tables/static_per_position.csv  (V5 common-mode layer, per tag-delay mode)
#       official_extra_analysis/FULL_4way_comparison/.../production_static_method_probe_per_session.csv (v4-io)

## Per-axis medians (24 static positions, Sim3-aligned to Vicon)
  layer / condition                         | h_med | v_med | 3d_med | 3d_rms | 3d_p95
  v4-io  (DEPLOYED, scale-biased, T4 mean)   | 37.4  | 61.9  |  72.7  | 109.8  | 171.5
  V5 common-mode D0 (tag-delay UNcorrected)  | 45.2  | 89.4  | 109.5  | 140.5  | 223.9
  V5 common-mode D_LOO_CV (corrected, 49.6mm)| 33.4  | 59.4  |  67.8  |  82.8  | 153.6
  -----------------------------------------------------------------------------------
  A-long HEADLINE CLAIM                       | ~35   | ~59   |  ~68   |   -    |   -

================================================================================
## S6 VERDICT — what layer are A's headline 35/59/68 from?
================================================================================
- A's 3D = 68 matches the CORRECTED layer (V5 D_LOO_CV = 67.8), NOT the deployed v4-io (72.7).
- A's vertical 59 and horizontal 35: BOTH layouts sit at vertical median ~60 (v4-io 61.9, corrected 59.4)
  and horizontal ~33-37, so vertical/horizontal ALONE do not discriminate.
- The DISCRIMINATOR is the 3D number: 68 = corrected layer; deployed v4-io is 72.7.
  Also note quadrature sqrt(35^2+59^2)=68.6 -> A's triplet is internally a (33-37 / 59 / 68) set =
  the corrected layer, not v4-io's (37/62/73).

  SELF-CORRECTION (my earlier claim was wrong): I first said "vertical 59 can only come from the
  corrected layer; bare v4-io can't." FALSE — I conflated v4-io vertical RMSE (98.8) with its MEDIAN
  (61.9). The deployed v4-io DOES give vertical median ~62. So the vertical claim is defensible from
  either layer; only the 3D=68 (vs 72.7) points specifically at the corrected layer.

- RESOLVED against main_EN (the professor-facing baseline; user 2026-06-29: "deployment never corrects
  tag delay — anchor delays absorbed by solver, tag left untouched"):
  main_EN.tex L86/88 headline (v4-io/T4/F0, tag UNcorrected):
     3D median 72.7 / horizontal median 37.4 / vertical median 61.9 mm  (P95 171.5 / RMSE 109.8).
  main_EN L90 already DISCLOSES: "the 72.7 headline partly benefits from the expanded production layout
     compensating an uncorrected tag-side delay, so the clean metric-corrected layout gives [109.5]
     median when tag-side delay is left at zero."  L89 already states the dataset-specific scope.
     L502 explicitly warns the 69.8 ablation-estimator median is NOT the headline (headline = 72.7).
  => VERDICT: Paper A's 35/59/68 DIVERGES from its own delivered baseline. 3D 68 vs 72.7 (~5mm low,
     = the very ablation/corrected value main_EN says not to headline); A also dropped main_EN's L90
     disclosure. The deployed system is honest v4-io=72.7 (per main_EN); only the novelty-check A
     mis-stated it.  FIX: conform Paper A headline to main_EN -> 72.7 / 37.4 / 61.9 median, carry the
     L90 tag-delay/scale-bias disclosure + L89 scope. This also discharges reviewers' S2 (baseline
     already discloses; A just needs to re-inherit it).
  BONUS (resolves arbitration A3 axis y/z): main_EN L237-245 relabels axes to z=vertical (raw Vicon Y).
     So A-long's d r/d y should become d r/d z (z vertical) to match the baseline convention.

================================================================================
## S3 VERDICT — is B's "decisive" 4.2mm flip significant?  (v4-io 72.7 vs corrected 67.8, n=24 paired)
================================================================================
THE MEDIAN FLIP IS NOT SIGNIFICANT:
  - diff of medians = 72.69 - 67.85 = +4.84 mm (this is B's headline "68.5 vs 72.7")
  - paired bootstrap 95% CI on (median_v4io - median_corr) = [-9.9, +43.4] mm  -> CROSSES ZERO.
  => Opus4.8's critique is VALIDATED: "the decisive control settles it" cannot rest on the median gap.

BUT THE CORRECTED LAYER IS SIGNIFICANTLY BETTER ON THE PAIRED / MEAN / TAIL EVIDENCE:
  - corrected better at 17 / 24 positions
  - Wilcoxon signed-rank: z=-2.37, p2 = 0.018  (significant)
  - sign test: p2 = 0.064 (marginal)
  - median paired per-position diff = +15.3 mm ; MEAN paired diff = +20.0 mm
  - bootstrap 95% CI on MEAN paired diff = [+6.6, +35.0] mm  -> EXCLUDES ZERO (significant)
  - tails: P95 171.5 -> 153.6 (-17.9) ; RMSE 109.8 -> 82.8 (-27.0) ; mean 92.8 -> 72.7 (-20.1)

WHY THE MEDIANS LOOK CLOSE BUT THE PAIRED TEST IS SIGNIFICANT: the two layouts differ a LOT at
individual positions (median paired |diff| ~15mm) but their marginal medians happen to land ~5mm apart.
Quoting the 5mm median gap hides the real, position-paired effect, which lives in the tail (RMSE/P95).

=> FIX for B-long + B-short: do NOT headline the 4.2/4.8mm MEDIAN as "decisive / settles it". Re-ground
   the claim on (i) the paired test (corrected wins 17/24, Wilcoxon p=0.018) and (ii) the tail/dispersion
   improvement (RMSE -27mm, P95 -18mm, mean -20mm), which is where the effect is real and significant.
   The underlying claim ("once tag delay is corrected the metric-correct layout is the better positioner")
   SURVIVES — but via paired/tail statistics, not the median.

CAVEATS (honest):
  - v4-io errors (production_method_probe) and corrected errors (FULL_V5) come from two analysis runs;
    I paired them by position ID (same 24 captures). B should re-run the paired test inside ONE pipeline
    to publish a clean p-value, and report the test it uses.
  - 17/24 + Wilcoxon p=0.018 is moderate, not overwhelming, on n=24. Honest wording: "the corrected
    metric-correct layout is the better positioner at most positions and in the tail (paired p~0.02),
    though the marginal-median gap is within noise."

================================================================================
## S3 INTUITION — what the "reversal" actually is (reusable B pitch framing)
================================================================================
ONE-LINE: the self-calibrated layout is SELF-CONSISTENT but NOT METRIC. That distinction IS Paper B.

WHAT IS BEING COMPARED:
  - v4-io  = production self-cal layout. GEOMETRY IS WRONG: scale +4.4% (Sim3-to-Vicon scale 0.958 =>
    layout expanded by 1/0.958 ~= 1.044). Cause: firmware hardcodes one antenna delay (16436) for all
    nodes, so the ~50mm common-mode TAG delay is never corrected and gets absorbed as layout scale.
  - common-mode / D_LOO_CV = METRIC-CORRECT layout: the global tag-delay constant (~49.6mm) is estimated
    (leave-one-out) and removed, so scale is right.

THE "REVERSAL" (expectation vs observation):
  - Expectation: correct geometry should position better.
  - Observed at the MEDIAN: the WRONG (scale-biased) v4-io is nominally ~4-5mm lower. The wrong map wins.

WHY (physical mechanism — the two errors cancel):
  - Every range is systematically ~50mm too long (uncorrected tag delay, common-mode).
  - v4-io's map is also ~4.4% too big, so its tag<->anchor geometric distances are inflated too =>
    the too-long ranges "fit" the too-big map, the two inflations cancel during trilateration, tag lands
    near truth. The layout's SCALE ERROR is silently doing the tag-delay correction for you.
  - The metric-correct map is the right size, but if positioning still feeds the uncorrected ~50mm-long
    ranges, ranges no longer fit the map => the delay error shows up directly as position error => worse.
  => The 4.2mm "reversal" is not a quality result; it is PHYSICAL EVIDENCE of the delay<->scale coupling.

SELF-CONSISTENT != METRICALLY CORRECT (the thesis):
  - A whole FAMILY of (scale, delay) pairs reproduces the identical ranges. Ranges alone cannot pick the
    physically true one. That null direction is the rho=-0.977 / 22x variance-inflation degeneracy.
  - v4-io is just one self-consistent member of that family, scale-biased 4.4%. It positions well only
    INSIDE its own delay convention; step outside (fix geometry, not delay) and it stops fitting.

WHY NOT HEADLINE THE 4.2mm: it is within noise (bootstrap CI crosses 0) AND the robust stats point the
  OTHER way (corrected wins 17/24, Wilcoxon p=0.018; RMSE 109.8->82.8, P95 171.5->153.6). Stated as
  "wrong geometry wins" it both over-claims and gets contradicted by its own table. Lead with rho /
  22x / falsification protocol / paired+tail; demote the median to "within noise, multi-room firms it up".

PUNCHLINE (ties to the experiment runway): to pick the metrically-correct member out of the family,
  ranges can't do it -- you need an EXTERNAL METRIC REFERENCE (known baseline / OTP per-device delay /
  Vicon). Sweeping LAYOUTS across ROOMS is precisely the lever that pushes on this degeneracy and pins
  the true scale -> that is why the experimental program B asks for is the right next step, not optional.

PITCH-SAFE SENTENCE (verbatim-usable):
  "The self-calibrated layout is internally self-consistent but not metric -- delay and scale are aliased
   into a whole family of equally-consistent solutions, and ranges alone cannot pick the true one; that is
   the identifiability limit, and breaking it needs an external metric reference."
