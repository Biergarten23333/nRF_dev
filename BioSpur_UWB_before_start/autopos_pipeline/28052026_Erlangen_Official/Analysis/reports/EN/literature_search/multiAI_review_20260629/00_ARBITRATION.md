# ARBITRATION — 5-AI joint review of the 4 novelty-check docs
# 2026-06-29. Reviewers: Opus4.6max, GLM5.2, Opus4.7, Opus4.8max, GPT5.5pro.
# Method: every disputed claim verified against CURRENT source (.tex + delay_scale_observability.py),
#         not by vote. Line numbers cited. Standing rule: objective, do NOT pander to the author.

## TL;DR
- Numbers across the 4 docs are ~95% internally consistent (all 5 reviewers agree). The damage is
  NOT numeric drift; it is (a) cross-doc FRAMING integrity and (b) over-universal CLAIM SCOPE.
- The single sharpest real issue (Opus4.8): Paper A trumpets in-environment accuracy from the very
  layout Paper B proves is metric-incorrect (scale-biased), without disclosing it.
- One whole critique cluster (planning/strategy voice) hinges on ONE user decision (memo vs manuscript).
- One widely-flagged "must-fix" is STALE/WRONG: the "~46 cm" typo does not exist in current source.

================================================================================
## GATE — RESOLVED BY USER 2026-06-29
================================================================================
>>> G1 = (i) INTERNAL novelty-check memos. KEEP the planning/strategy sections.
    CONSEQUENCE: **S1 is DROPPED** (Target Venue / Risk Assessment / Publication Strategy /
    Priority-disclosure / "blocking value" / "unpatentable" all STAY — genre-appropriate).
    GLM5.2's "not papers / desk-reject / rewrite as manuscripts" is DISMISSED.
    EXCEPTION that still stands: the Batstone "[verify exact venue/pages...]" TODO (A4) is sloppy
    even for a supervisor-facing memo → still fix.
>>> G2 = COORDINATED PAIR (A and B cross-cite). CONSEQUENCE: all cross-doc items are CONFIRMED
    must-fix — S2, S4, and the citation collisions (A4: Ridolfi same-key-different-paper,
    Piavanini 2022a/b, Shah-2021 author mismatch) must be reconciled because a reviewer WILL
    read both side by side.

(original gate text, for the record:)

G1. **Are these 4 files (i) internal novelty-check memos, or (ii) the seed of actual manuscripts?**
   - GLM5.2 says "not papers → desk-reject." Opus4.8/GPT say "they're memos by design, planning voice is genre-appropriate."
   - VERDICT: both are right, conditionally. The titles literally say "Novelty Assessment"/"Concept Brief".
     IF (i): keep Target-Venue/Risk-Assessment/Publication-Strategy/Priority-disclosure — they belong.
     IF (ii): strip ALL of them (see Tier-S #1). My own earlier sweep half-did this (removed Zotero/Note-for-reader
     but LEFT Target Venue + Risk Assessment + Publication Strategy + defensive-publication + Batstone TODO),
     which is why reviewers still see them. CONFIRMED still present: A-long L330/339/359/370/376/385;
     A-short L112/116/120; B-long L430/448/466; B-short L61 + "Target: Measurement".
   - >>> ASK USER. This gates Tier-S#1, A-long#8, A-short#5, B-long#15, B-short#6/7.

G2. **Do A and B circulate as a coordinated PAIR (cross-cited), or can each stand alone?**
   - If they're a pair, the cross-doc framing/citation collisions (Tier-S #2,#3,#4 + citation ledger) are must-fix.
   - If A ships first as standalone citable prior and B much later, some cross-doc items relax.
   - >>> ASK USER.

================================================================================
## TIER S — must-fix, VERIFIED real, high severity (do regardless, once G1/G2 known)
================================================================================

S1. [PLANNING VOICE — gated by G1(ii)] Strip strategy/IP sections from any manuscript-bound version.
    CONFIRMED in source: A-long "Target Venue"(330)/"Risk Assessment"(339)/"Publication Strategy...Front-Matter"(359)/
    "blocking value"(370)/"unpatentable by others"(376)/"Recommended title"(385); A-short "Priority and disclosure"(112)/
    "Publish this system paper first and early"(120); B-long "Target Venue and Titles"(430)/"Risk Assessment"(448);
    B-short "Priority and disclosure"(61)/"Target: Measurement (IF~5.6)". 5/5 reviewers. My prior sweep MISSED these.

S2. [CROSS-DOC INTEGRITY — the sharpest, Opus4.8 D2-b] **A reports its headline accuracy from the layout B proves
    is metric-incorrect, and never says so.** A-long L40 "recovers full 3D anchor coordinates" + L285/296 "vertical
    ~59mm, 3D ~68mm" — all from the production (v4-io) layout. B-long L37: "the v4-io layout is scale-biased
    (metric-incorrect) ... Sim(3) scale 0.958." B's THESIS is that such in-environment accuracy is not a
    calibration-quality proxy. So A is trumpeting exactly the "wrong-calibration-wins" number as a success without
    the caveat. FIX: A must add "...recovers anchor coordinates up to a common-mode scale bias quantified in the
    companion paper" and state its in-env accuracy is NOT a metric-correctness claim. VERIFIED real.

S3. [STATISTICAL RIGOR — Opus4.8 B-long#1, the strongest single methodological critique] B's "decisive" result =
    68.5 vs 72.7 mm = **4.2 mm median flip over 24 static positions with NO CI / no significance test**, while B
    dismisses AniTrack's 26 mm/n=7 external gap as "within one SD." You cannot apply a within-1-SD discount to a
    26mm/n=7 effect AND rest the headline on a 4.2mm/n=24 effect with no test. The comparison IS paired+held-out
    (leave-one-out tag constant, same 24 positions), so it may well survive — but "the decisive control settles it"
    is UNSUPPORTED until shown. FIX: report a paired/bootstrap CI on 68.5 vs 72.7; if it crosses 0, soften
    "decisive"→"indicates". B-long + B-short. VERIFIED real (B-long L37; AniTrack framing in §2.6).
    >>> S3 result + INTUITION (self-consistent != metric; why the wrong map wins at the median; punchline =
        needs an external metric ref, multi-room is the lever to pin true scale): see 07_S3_S6_results.md
        ("S3 VERDICT" + "S3 INTUITION" blocks).

S4. [FRAMING CONFLICT — 5/5 reviewers, highest consensus] **A misframes B's headline AND overstates B's conditional
    result as unconditional.**
    (a) A-long L34 "B's headline result is an anchor-side common-delay↔layout-scale degeneracy"; L268 "that paper's
        headline anchor-side delay↔scale result" — but B-long L279-280 says "the methodological falsification
        protocol (the second contribution) is the headline claim, and the empirical coupling result is framed as
        characterization rather than first discovery." A picks a headline B explicitly declines.
        (NOTE B is ALSO internally inconsistent: its thesis L37 leads with the coupling, its contribution L279
        crowns the protocol — B must pick ONE headline first, THEN A matches it.)
    (b) A-long L321 "the GPS receiver-clock/altitude coupling --- which no amount of added geometric diversity
        removes" = UNCONDITIONAL. B-long L37: "The effect is conditional: once the tag delay is corrected ... the
        metric-correct layout wins." FIX A→ "without explicit tag-delay correction, no amount of added geometry
        removes...". VERIFIED real.

S5. [OVER-UNIVERSAL "FIRST" — GPT/GLM/Opus4.7] A-long L40 "AutoPos is, to our knowledge, the first UWB anchor
    self-calibration system that recovers full 3D anchor coordinates jointly with per-device antenna delays from
    inter-anchor ranging alone" — unconditional. Defensible only as a CONJUNCTION (commodity static hw + inter-
    anchor-only + no surveyed ref + joint delay + Vicon-validated + 4+4). Pereira(sim)/Batstone(mobile) occupy
    parts. FIX: "first Vicon-validated commodity-hardware demonstration of reference-free 3D inter-anchor-only UWB
    anchor self-calibration with joint per-device delay estimation." Same disease in A-short ("first demonstration"),
    B-long L265-ish ("first gauge-free Fisher..."→"to our knowledge, ... on real UWB self-cal hardware"). VERIFIED.

S6. [NUMBER PROVENANCE — all reviewers, must pin] A-long 3D absolute **~68 mm** (L296 = sqrt(35²+59²) per-axis
    quadrature) vs B-long v4-io 3D **median 72.7 mm** (L37), SAME campaign, statistic unstated. The gap (~4.7mm)
    coincidentally = B's decisive effect (4.2mm). Most likely benign (per-axis-quadrature ≠ true 3D median compose
    differently) BUT undocumented — CANNOT confirm benign without re-running; do NOT assume cherry-pick, do NOT
    wave away. FIX: state the exact statistic in BOTH (median? mean? RMS? aligned?), confirm same data/definition.
    Also unify A's own vertical: "~59mm"(L285) / "~60mm"(L265) / "~63mm floor"(L178) used interchangeably.

S7. [LOGICAL RIGOR — Opus4.7#5, Opus4.8#2, GPT#5] A-long L265 "matching the ~60mm production vertical error, so the
    geometry is already at its floor and the residual is delay." The ~63mm CRLB bounds an ANCHOR's self-cal vertical;
    the ~59-60mm is the TAG's positioning error — different estimands. Numeric near-equality does NOT prove
    "geometry at floor → residual is delay." Also abstract presents 59 "near" a 63 bound it sits BELOW (implies
    saturating a bound you're under). FIX: derive a geometry-limited floor on TAG vertical (DOP-scaled), or downgrade
    "the residual is delay" from proof to indication + defer decomposition to B. VERIFIED real.

================================================================================
## TIER A — must-fix, VERIFIED real, lower severity
================================================================================

A1. [A-short internal contradiction — GPT/Opus] L53-54 "with a single coplanar layer the vertical is badly
    ill-conditioned --- the height still falls out" vs L89 "vertical CRLB exactly singular for a coplanar layer."
    "Falls out" (=still recoverable) contradicts "exactly singular" (=unrecoverable). FIX: "vertical is unobservable
    in the ideal range-only model; any apparent height comes from priors/nonidealities, not calibrated geometry."

A2. [B-long SIGN exposition — Opus4.8 D4, GPT D2.5] L37 "common-mode range bias EXPANDS it ... scale 0.958" vs L88
    "a positive common bias SHRINKS the layout, matching the negative alias sign." Same scale 0.958, opposite verbs.
    NOT necessarily a numerical error — it's a "range-bias"(+→expand) vs "antenna-delay"(+→shrink) sign-convention
    COLLISION left implicit. But a metrology reviewer WILL read it as a contradiction. FIX: state once — (i)
    s_{A→V}=0.958 ⇒ AutoPos layout expanded by 1/0.958=1.044; (ii) +delay ↔ −range/scale, so "+delay shrinks" and
    "+range-bias expands" are the same statement. VERIFIED real (exposition).

A3. [AXIS CONVENTION y vs z — GPT D2.4] A-long uses vertical = y (∂r/∂y, L172/312/401/423; horizontal "X-Z plane")
    while A-short uses vertical = z (Δz, L56). Cross-doc conflict. FIX: pick one (recommend z=vertical for
    localization readers) and make all 4 match. VERIFIED real.

A4. [CITATION LEDGER — GPT forensic + cross-doc internal] Verified against both bib lists:
    - Shah-2022 Chaiwong: A-long INCLUDES "Chaiwong, K." (L475) but B-long (L496) and B-short OMIT it. FIX: add to B.
      [reviewers said "B-long+B-short"; A is already correct — confirmed.]
    - Batstone "[verify exact venue/pages against PDF before submission]" present ONLY in B-long L466 (A-long L429
      clean). FIX: remove + insert real cite (GPT: IPIN 2017, DOI 10.1109/IPIN.2017.8115885 — web-verify).
    - Ridolfi 2021: A-long ridolfi_2021 (Fontaine/Van Herbruggen/Joseph, L463) and B-long
      ridolfi_2021_self_calibration... (Kaya/Berkvens/Weyn, ACM Comput Surveys, L494) are TWO DIFFERENT papers under
      the same "Ridolfi 2021" key. If A+B circulate together, disambiguate. VERIFIED different.
    - Shah 2021: A-long (L471-473, pp.52030-52044, authors Shah/Kaemarungsi/Demeechai) vs B-long (L495, IEEE Access,
      authors Shah/Chaiwong/Kovavisaruch/Kaemarungsi/Demeechai) — same work, A's author list shorter. Internal
      inconsistency CONFIRMED. GPT claims correct pages = 63294-63305 (web-verify).
    - Piavanini 2022: A-long piavanini_2022 (L459) vs B-long piavanini_2022_a_calibration_method_for (MetroInd4.0,
      L491). Earlier reviewers say A's is the Sensors-22(23)-9363 sport paper → would be a DIFFERENT Piavanini 2022.
      NEEDS the author to confirm whether A and B cite the same Piavanini paper; if not, 2022a/2022b. NEEDS-VERIFY.
    - Shah 2019 (A-long L468, "Shah, C.L., Shin, S.-Y., Jeon, J."): GPT says the actual Sensors-2019 error-estimation
      paper is by Cung Lian Sang et al. — possible misattribution (note "Shah, C.L." here vs "Shah, S." elsewhere =
      maybe two different groups conflated). NEEDS-VERIFY-likely-fix.
    - Yuan 2024 (A-long L488): author list truncated vs arXiv 2412.16880 full list. NEEDS-VERIFY-likely-fix.
    - Missing competitor: Liu & Cao 2025 (UWB+LiDAR auto-cal <30s, arXiv 2503.22272) — add to A Table 1 external-
      sensor cluster. Doesn't break inter-anchor-only claim. (GPT, web-found.)

A5. [SHORT↔LONG fidelity — GPT D3, multiple]
    - Xiang 2025 in A-short (L145) but NOT A-long → short is superset of long. FIX: add to A-long or drop from A-short.
    - B-short DROPS (i) the tag-side vertical-alias strand that A-short defers to B [most consequential], (ii) the
      Fisher caveat (softest modes = within-layer wiggle; read delay marginal — B-long L89-93). Add both to B-short.

A6. [SCOPE LANGUAGE — GPT's overall #1] A-long/A-short repeatedly universalize one-room/24-static-position/one-lot
    evidence: "the number later work must beat"(A-long L148), "vertical has no shortcut", "no amount of geometry
    removes"(L321). B is honestly scoped ("one room, 24 static positions"); A is not. FIX: add deployment-scope
    qualifiers to A's strong claims. VERIFIED (A's universal phrasing present; B's scope present).

================================================================================
## CONFLICTS BETWEEN REVIEWERS — adjudicated by source/script
================================================================================

C1. **"46 cm" typo (Opus4.6/4.7/4.8 say catastrophic must-fix; GPT didn't flag).** >>> RESOLVED: the 4 Opus
    reviewers are WRONG / STALE. Current source A-short L97 reads "$\sim$4--6~cm". The PDF en-dash rendered as
    "4 6"→read as "46" by the 4 Opus models. GPT correctly read ~6cm. **NO content bug.** (Optional: check the PDF
    font so the en-dash doesn't visually collapse — cosmetic, not a number error.) DISMISS the "46 cm" must-fix.

C2. **8.9:1 valley elongation — Opus4.7 ("=√((1+ρ)/(1-ρ))≈9.3, consistent") vs Opus4.8 ("should be √22≈4.7, suspect").**
    >>> RESOLVED by the script. delay_scale_observability.py L171-175: elong = sqrt(w2[1]/w2[0]) = sqrt(condition
    number of the 2×2 cost matrix in physical mm units). So 8.9:1 is an EIGENVALUE ASPECT RATIO, ≈ sqrt((1+ρ)/(1-ρ))
    =9.3 for the normalized case (8.9 with real per-axis physical units). Opus4.7 is RIGHT on the math. Opus4.8 is
    RIGHT that 8.9 ≠ √22 and that VIF(22×, marginal/conditional variance) and elongation(8.9, valley aspect) are
    DIFFERENT measures — but WRONG to imply 8.9 is suspect (it was never meant to be √VIF). **No numerical error.**
    Optional NICE-TO-HAVE: one half-sentence noting 22× and 8.9:1 are distinct (marginal-variance vs valley-aspect)
    so a sharp reviewer doesn't expect 8.9≈√22.

C3. **"These are/aren't papers" (GLM desk-reject vs Opus4.8/GPT memo-by-design).** >>> RESOLVED into GATE G1. Not a
    factual conflict; depends on user intent. Both correct under their assumption.

================================================================================
## DISMISS / DOWNWEIGHT (stale, wrong, or low-value)
================================================================================
- "46 cm" typo — DISMISS (C1, source is "4--6 cm").
- Ligature/dash garble (GLM, GPT C-axis) — mostly PDF-EXTRACTION artifacts in the reviewers' copy-paste, NOT source
  defects; "CramérRao"/"delaylayout" without hyphen are extraction noise. Low priority; a recompile check suffices.
- GLM "rewrite everything as manuscripts / fix Table 1 alignment / ligatures" — contingent on G1(ii); Table-1
  "alignment" was a text-extraction artifact, the LaTeX table is fine.
- Pan-2015 "38.8/36.1%" PPP figures (Opus4.8 flagged unverified) — analogy survives even if % drift; low risk.

================================================================================
## GENUINELY CLEAN — do NOT touch (all reviewers + my check agree)
================================================================================
- B-long↔B-short core numerics fully consistent (0.958→1.010, ρ=−0.977, 22×, 1↔−1.22mm, c=112, 105.4→63.0,
  72.7/109.5/72.9/68.5, P95 317.5/156, four-way 311/252/78, AniTrack 13.96/16.57). VERIFIED.
- Internal degeneracy math: VIF=1/(1−ρ²)=22 (script L149), valley sqrt(cond2x2)=8.9 (script L173) — both correctly
  computed and correctly distinct.
- B-long's cross-domain prior-art engagement (acoustic/GNSS/AniTrack) + "we don't claim the bare coupling is new"
  hedge + Group-1/Group-2 "why unseen" argument — strongest part, keep.
- A-long Table 1 "every axis occupied, the conjunction is empty" defense + repeatability-vs-absolute discipline — keep.
- worst-anchor CRLB 226→134→122→93 monotone, consistent with mean trend — keep.
- Firmware 16436 / 9 modules / 4+4 consistent across A+B — keep.

================================================================================
## SUGGESTED ORDER OF OPERATIONS (after G1/G2 answered)
================================================================================
1. Answer G1, G2.  2. S2 + S4 (cross-doc integrity — cheapest, highest risk-removal).  3. S3 (run the paired test;
   it gates whether "decisive" survives — do this BEFORE polishing prose around it).  4. S5+S6+S7 (A's claim/number
   discipline).  5. S1 (strip planning voice if G1=ii).  6. Tier A.  7. citation ledger (A4) needs a web pass.
