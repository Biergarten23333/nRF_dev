# Multi-AI review — reviewer: OPUS 4.8 MAX (4 PDFs at once)
# captured 2026-06-29 — RAW, not yet arbitrated
# (deepest/most adversarial of the four so far)

## Framing note from reviewer
These 4 files are NOVELTY/POSITIONING MEMOS + CONCEPT BRIEFS, not manuscripts (labelled "Novelty Assessment"/
"Concept Brief", contain strategy voice). So Axis C (manuscript float/typography) largely N/A; reviewed the
manuscript-bound embedded text (recommended abstracts, citable sentences, ref lists) to manuscript standard.
Axes A & D apply fully and are where the damage is.

## VERDICT
- A-long: novelty borderline (fine Sensors/IPIN, weak TIM), writing needs-pass, fmt minor.
- A-short: borderline, writing needs-pass (hard typo "~46 cm"), fmt clean.
- B-long: borderline (framing new; decisive empirical leg statistically THIN), writing needs-pass->HEAVY-EDIT
  (internal sign contradiction, median/RMSE mixing, unreconciled delay accounting, self-contradictory "headline"),
  fmt minor (must-fix: [verify...] TODO left in bibliography).
- B-short: borderline, needs-pass, clean.
- D: REAL CONFLICTS not minor drift.

## Axis D
- D1-a (key): A 3D abs ~68mm (=sqrt(35^2+59^2)) vs B v4-io 3D MEDIAN 72.7mm, same Erlangen campaign, statistic
  unstated. GAP 68 vs 72.7 = ~4.7mm = SAME magnitude as B's entire decisive effect (72.7-68.5=4.2mm). The
  definitional difference between A's and B's number is as large as the effect B's conclusion rests on.
  Fix: state exact statistic both papers; if A is Sim(3)-aligned & B raw in-env, say so.
- D1-b: 59/60/63 used interchangeably for "the vertical"; abstract "≈59mm absolute ... near ≈63mm CRLB floor"
  => achieved (59) sits BELOW quoted bound (63). Different estimands (anchor self-cal CRLB vs tag positioning)
  so not a literal violation, but presented as "number near its floor" implies saturating a bound you're below.
- D1-c: three unreconciled delay magnitudes in B: common-mode c=112mm, per-anchor mean 94.6/max 148.2mm,
  firmware-OTP gap 36 units. If e_i regularized to 0, mean(d_i)≈c=112 contradicts stated 94.6. Need single delay ledger.
- D2-a (must-fix): A and B disagree on what B's headline is; AND B internally inconsistent (thesis p1 leads with
  coupling/wrong-geometry, contribution sec p7 demotes that to "characterization" and crowns the PROTOCOL).
  Pick ONE designated headline for B; make A describe that.
- D2-b (HIGH VALUE, new): A reports the accuracy of the layout B proves is metric-INCORRECT (v4-io = scale-biased,
  Sim3 0.958, 4.4% expanded) and never says so. A's "honest limits" flag only tag-side residual, never the
  anchor-side SCALE BIAS that is B's whole point. A abstract "recovers anchor coordinates and per-device delays"
  must become "...up to a common-mode scale bias quantified in the companion paper." Same geometry = "success"(A)
  vs "metric-incorrect"(B).
- D2-c: A self-contradicts ownership: p2 "We name and stake the effect here" vs p7 "Paper B establishes priority".
- D3: (i) A-short "~46 cm" hard error not in A-long (mangled en-dash 4-6cm->46cm). (ii) Xiang 2025 cited in A-short
  but ABSENT from A-long refs (short is superset of long — wrong direction). (iii) B-short DROPS the rotating-arm
  vertical-injection control AND the tag-side vertical-aliasing strand entirely — that's the empirical leg of
  "residual is delay not geometry" and exactly what A-short defers to B; reader of A-short+B-short can't find it.
- D4 (sign contradiction INSIDE B): B-long p1 "under-absorbed common-mode bias EXPANDS it ... Sim3 0.958" vs p3
  "(a positive common bias SHRINKS the layout, matching the negative alias sign)". Positive range bias inflates
  measured inter-anchor distances -> fitted layout LARGER -> AutoPos->Vicon scale 0.958<1, so "expands"(p1) is
  right, "shrinks"(p3) backwards. Nail the sign convention once.
- D4 novelty carve otherwise CLEAN on paper (A: capability+4+4+diagnostics; B: Fisher+conditional+protocol).

## A-long must-fix
1. Citable priority sentence OVER-CLAIMS OBSERVABILITY: says 4+4 "renders the vertical observable" but paper
   itself says 4+1 already unlocks it and 4+4 is a worst-case-UNIFORMITY/conditioning argument "not an
   observability one". The verbatim-citable sentence conflates observability vs conditioning. Fix: "renders the
   vertical WELL-CONDITIONED"; reserve "observable" for k>=1.
2. CRLB-on-anchor used as bound on TAG error: ~63mm inter-anchor vertical CRLB "matching ~60mm production vertical
   error so geometry at its floor and residual is delay" — CRLB bounds an anchor's self-cal vertical; 59-60 is the
   tag's positioning error. Numeric near-equality of two estimands doesn't prove "geometry at floor -> residual is
   delay". Derive geometry-limited floor on TAG vertical (DOP-scaled), or downgrade proof->heuristic. Load-bearing.
3. Axis-label X/Y/Z inconsistency: horizontal "XZ plane" => vertical=Y (matches ∂r/∂y=0), but prose repeatedly
   calls vertical "Z" ("a Z sensor","obtain Z","Recovering Z"). Define frame once (vertical=Y), purge stray Z.
4. Headline accuracy statistic unstated + undisclosed scale bias (D1-a, D2-b).
## A-long nice
5. Diagnostic (1) "ranging residual misleading w/ few anchors" ~ folklore (under-determined fit always low resid);
   lead with diagnostic (2) subset-dispersion (less obvious). 6. Fig1 plots sqrt(CRLB) not CRLB; label "CRLB (std
   bound)". 7. thesis ~70 words.
## A-long clean
Table 1 prior-art partition honest; "every axis occupied, conjunction empty" is right defense; repeatability-vs-
absolute discipline maintained.

## A-short
must: 1. "~46 cm" (D3). 2. inherits A-long #1(observability)+#2(CRLB conflation)+D2-b(scale non-disclosure).
nice: 3. Xiang 2025 reconcile. clean: 2-page floats fine, faithful apart from typo.

## B-long must-fix
1. THE BIG ONE: "decisive" result = 4.2mm median flip (68.5 vs 72.7) over 24 static positions, P95 156-317mm,
   NO CI, no significance test. Meanwhile B dismisses AniTrack's 26mm gap (13.96 vs 16.57, n=7) as "within one
   SD over only seven". CANNOT discount a 26mm/n=7 external effect AND rest your headline on a 4.2mm/n=24 effect
   with no CI. Report bootstrap CI / paired test on 68.5 vs 72.7; if it crosses zero, "decisive fair-comparison
   control settles it" must go. CENTRAL VULNERABILITY.
2. Internal sign contradiction expands vs shrinks (D4).
3. Median vs RMSE mixed in head-to-heads: four-way ablation RMSE (311/252/78), layout ranking median
   (72.7/68.5/109.5), anchor-geom RMSE (105.4->63.0), tails P95. Decisive comparison sets RMSE-derived correction
   against median positioning. One statistic family per head-to-head, or one table all 4 columns. Right-skew =>
   RMSE>>median; cross-statistic comparison silently flatters whichever quoted as median.
4. Delay ledger unreconciled (112/94.6/148.2/36 units) (D1-c).
5. BIBLIOGRAPHY TODO: Batstone 2017 "[verify exact venue/pages against PDF before submission]" — remove. Also
   A cites Batstone "pp.1-8", B omits pages — make identical.
6. SHARED-REF COLLISIONS with A (all checkable, real not fabricated):
   - Piavanini 2022: A="Self-Calibrating Localization ... Sport", Sensors 22(23) 9363; B="Calibration Method for
     Antenna Delay Estimation and Anchor Self-Localization", MetroInd4.0&IoT. TWO distinct real papers same group;
     shared key "Piavanini 2022" resolves differently across A/B -> disambiguate 2022a/2022b in BOTH lists.
   - Ridolfi 2021: A=Wireless Networks NLOS-ML; B=ACM Comput Surveys self-cal/collab survey. Different works,
     same disambiguation. Verify+split a/b.
   - Shah 2022: A author list includes "Chaiwong, K." (2nd), B omits — same title/venue, different author list. One wrong.
## B-long nice
7. 22x VIF vs 8.9:1 valley elongation are NOT the same: VIF=1/(1-ρ^2)=22; sqrt(22)=4.7 not 8.9; 8.9:1 is a
   coordinate-dependent aspect ratio in mm-at-edge units. State they measure different things. [ARBITRATION FLAG:
   Opus 4.7 instead computed sqrt((1+ρ)/(1-ρ))=sqrt(86)=9.3≈8.9 and called it CONSISTENT — reviewers disagree on
   the formula; must resolve which elongation definition the figure uses.]
8. Fisher caveat load-bearing: "globally-softest Fisher modes are within-layer wiggle artifacts ... coupling read
   from delay direction's marginal not softest raw eigenvector" => ρ=-0.977 depends on marginal-projection choice;
   pre-empt "did projection manufacture the coupling?" by tying cost-clamp(~70%) + recovered scale as independent
   corroboration explicitly.
9. AniTrack "DWM3000 hardware" — reviewer saw custom SS-TWR hw (ETH/Zoo Zurich) but did NOT see exact "DWM3000"
   string in source; verify chip/module before asserting. 16.57cm + n=7 in body not abstract — confirm. (13.96 confirmed.)
## B-long clean
Cross-domain prior art (Burgess/Kuang/Astrom, Crocco, Thrun, Shen-Win, Pan 2015 GNSS) thorough; "we don't claim
the bare coupling is new" correct hedge; Group-1/Group-2 "why unseen" is strongest part.
NOTE: Pan 2015 "38.8%/36.1%" PPP figures NOT verified — mark unverified.

## B-short
must: inherits B-long #1(4.2mm/no-CI), #2(sign), #3(median/RMSE).
nice: "one room 24 positions — High" risk row is the single most defensible external-validity sentence; keep.
clean: faithful compression, internally consistent refs.

## Single most important (Opus 4.8 MAX)
Both flagship results rest on IN-ENVIRONMENT positioning differences the program itself shows are unreliable /
within its own noise, AND the two papers partly contradict on this point:
 - B's thesis: same-env tag accuracy is NOT a reliable proxy for calibration quality. A's headline = a strong
   in-env vertical (59mm) produced by the v4-io layout B PROVES is scale-biased -> A trumpets exactly the number
   B says not to trust, never telling reader the geometry is metric-incorrect.
 - B's own decisive claim = 4.2mm median flip / 24 / no CI, while discarding 26mm/n=7 external as within-1-SD.
 - A's 68mm vs B's 72.7mm gap (~5mm) = same size as decisive 4.2mm effect => headline effect currently
   indistinguishable from inter-paper definitional drift.
Action: bootstrap/paired CI on 68.5 vs 72.7 (if crosses zero, "decisive control" framing collapses); fix ONE
shared error-statistic definition; make A disclose its accuracy comes from the scale-biased layout. If 4.2mm
survives + statistic pinned, program holds (Fisher ρ=-0.977/22x + protocol are real independent of the flip).
Everything else (46cm typo, observability/conditioning slip, sign, ref collisions, biblio TODO) = mechanical,
fixable in an afternoon.
