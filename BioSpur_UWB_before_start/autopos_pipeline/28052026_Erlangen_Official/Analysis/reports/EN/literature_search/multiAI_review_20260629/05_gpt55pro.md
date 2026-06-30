## VERDICT BLOCK

| PDF                                        |                                                          Axis A: novelty |              Axis B: writing | Axis C: formatting |
| ------------------------------------------ | -----------------------------------------------------------------------: | ---------------------------: | -----------------: |
| **A-long — AutoPos system**                | **borderline-to-strong, if scoped as a systems/deployment contribution** |               **heavy-edit** | **minor problems** |
| **A-short — AutoPos system brief**         |        **borderline; over-compressed and more over-claimed than A-long** |               **heavy-edit** |       **problems** |
| **B-long — delay/layout mechanism**        |       **borderline-to-strong as a measurement-validity/mechanism paper** | **needs-pass to heavy-edit** | **minor problems** |
| **B-short — delay/layout mechanism brief** |           **borderline; faithful on core numbers but drops key caveats** |               **heavy-edit** |       **problems** |
| **D — cross-document consistency**         |                   **minor numeric drift, but real framing/口径 conflicts** |                              |                    |

Mapping is unambiguous from filenames: `novelty_check_autopos_system_A_Long.pdf` = **A-long**, `novelty_check_autopos_system_short_A_Short.pdf` = **A-short**, `novelty_check_delay_layout_coupling_B_Long.pdf` = **B-long**, `novelty_check_delay_layout_coupling_short_B_Short.pdf` = **B-short**.

---

# AXIS D — CROSS-DOCUMENT CONSISTENCY

## D1. Cross-document numeric table

| Quantity                                               |                                                                                                               A-long |                                                                                      A-short |                                                                                       B-long |                                B-short | Consistency finding                                                                                                            |
| ------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------: | -------------------------------------------------------------------------------------------: | -------------------------------------------------------------------------------------------: | -------------------------------------: | ------------------------------------------------------------------------------------------------------------------------------ |
| Anchor array                                           |                                                                  8× DWM1001C, balanced **4+4**, two elevation levels |                                                                         8× DWM1001C, **4+4** |                            deployed **eight-anchor** two-layer array; nine modules incl. tag | nine modules incl. tag; deployed array | **Consistent**, but B-short does not explicitly restate 8 anchors / 4+4.                                                       |
| Generic firmware antenna-delay default                 |                                                                                               **16436** device units |                                                                    generic default, no value |                                                  **16436** device units, all anchors and tag |                              **16436** | **Consistent**, A-short omits numeric value.                                                                                   |
| Factory OTP values                                     |                                              not in main A-long body; A-long says OTP is near-uniform and not useful |                                                                                      omitted |                                eight modules **16472**, one **16451**; 21-unit offset ≈98 mm |                                   same | **Consistent**, but only B-long gives full hardware evidence.                                                                  |
| Fitted per-device delay span                           |                                                                                                            not given |                                                                                      omitted |                                      ≈**49–148 mm**; mean **94.6 mm**, anchor A **148.2 mm** |                         ≈**49–148 mm** | **Consistent**, but B-short drops mean/error-budget closure.                                                                   |
| Pairwise excess closure                                |                                                                                                              omitted |                                                                                      omitted |                                                                                  **0.11 mm** |                                omitted | Not inconsistent; short drops a useful validation number.                                                                      |
| Coplanar vertical observability                        |                                                                                        “exactly singular,” ∂r/∂y = 0 | first says “badly ill-conditioned — the height still falls out”; later says exactly singular |                                                                  singular for coplanar layer |                                omitted | **Conflict in A-short.** “Height still falls out” contradicts “exactly singular.”                                              |
| Vertical axis notation                                 |                                                        vertical appears as **y** in ∂r/∂y; horizontal is “X–Z plane” |                                                    heading says “pure-UWB **Z**” and uses Δz |                                                  mostly “vertical coordinate,” no axis label |                          no axis label | **Real notation conflict.** Decide whether vertical is y or z and make all docs match.                                         |
| 4+1 mean/worst vertical CRLB                           |                                                                                                    ∼**161 / 226 mm** |                                                                                      omitted |                                                                                      omitted |                                omitted | No conflict.                                                                                                                   |
| 4+2 mean vertical CRLB                                 |                                                                                                           ∼**81 mm** |                                                                                      omitted |                                                                                      omitted |                                omitted | No conflict.                                                                                                                   |
| 4+4 mean vertical CRLB                                 |                                                                                                           ∼**63 mm** |                                                                                   ∼**63 mm** |                                                                              about **63 mm** |                                omitted | **Consistent**, B-short should include if it is meant to support A’s vertical-limit claim.                                     |
| Worst-anchor vertical CRLB sequence                    |                                                                                                **226→134→122→93 mm** |                                                                                      omitted |                                                                                      omitted |                                omitted | No conflict.                                                                                                                   |
| Balanced vs clustered pair                             |                                                                                                            **1.24×** |                                                                                      omitted |                                                                                      omitted |                                omitted | No conflict.                                                                                                                   |
| Production / absolute vertical error                   |                                                                                ≈**59 mm**, also “∼60 mm” and “≈6 cm” |                                                                        ∼**59 mm**, ∼**6 cm** |                    referenced as production vertical error ≈63 mm floor, but no 59 mm result |                                omitted | **Acceptable rounding**, but B-short lacks the vertical strand entirely.                                                       |
| Repeatability                                          |                                               horizontal ∼27 mm; vertical ∼41 mm; 3D ∼50 mm; all-eight online ∼28 mm |                                                                    ∼5 cm 3D / ∼4 cm vertical |                                                                    not a system-result focus |                                omitted | **Consistent**, but “∼28 mm when all eight anchors visible online” needs clearer relation to “∼50 mm 3D.”                      |
| Absolute accuracy                                      |                                                                        horizontal ∼35 mm; vertical ∼59 mm; 3D ∼68 mm |                                                                    ∼7 cm 3D / ∼6 cm vertical |                                                                                  not central |                                omitted | **Consistent rounding.**                                                                                                       |
| Literature review counts                               |                                                         37 systems; 5/37 3D; 32/37 2D; ∼19% within 10 cm; best ∼5 cm |                                                                                         same |                                                                                     not used |                               not used | **Consistent**, but this is a narrow clinical UWB/MIMU review and should not be used as a universal UWB field statistic.       |
| Broadcast throughput                                   |                                                                            **10 Hz**, up to **10 simultaneous tags** |                                                         mentioned indirectly? mostly omitted |                                                                                      omitted |                                omitted | No conflict, but A-short should keep it if 4+4 minimality depends on it.                                                       |
| Ranging residual diagnostic                            | lowest residual **11.5 mm** gives worst positioning ∼**169 mm**; best positioner **49 mm** with residual **12.9 mm** |                                                                             qualitative only |                                                                                      omitted |                                omitted | No conflict; A-short drops quantitative support.                                                                               |
| Fixed all-8-anchor variants                            |                                                                                                        ∼**28–33 mm** |                                                                                      omitted |                                                                                      omitted |                                omitted | No conflict.                                                                                                                   |
| AutoPos-to-Vicon Sim(3) scale                          |                                                                                                              omitted |                                                                                      omitted |                                                     **0.958 → 1.010** with common-mode delay |                      **0.958 → 1.010** | **Consistent**, but B-long has a sign/wording ambiguity: “expanded layout” vs later “positive common bias shrinks the layout.” |
| Rigid anchor RMSE under common-mode reparameterization |                                                                                                              omitted |                                                                                      omitted |                                                                          **105.4 → 63.0 mm** |                    **105.4 → 63.0 mm** | **Consistent.**                                                                                                                |
| Common-mode delay c                                    |                                                                                                              omitted |                                                                                      omitted |                                                                                   **112 mm** |                             **112 mm** | **Consistent.**                                                                                                                |
| Cost clamp                                             |                                                                                                              omitted |                                                                                      omitted |                            cost rises about **70%**; five perturbed initializations converge |                                   same | **Consistent.**                                                                                                                |
| Fisher ρ                                               |                                                                                                              omitted |                                                                                      omitted |                                                                               **ρ = −0.977** |                         **ρ = −0.977** | **Consistent.**                                                                                                                |
| Variance inflation                                     |                                                                                                              omitted |                                                                                      omitted |                                                                                      **22×** |                                **22×** | **Consistent.**                                                                                                                |
| Alias slope                                            |                                                                                                              omitted |                                                                                      omitted |                                               **1 mm delay ↔ −1.22 mm edge scale**, −645 ppm |   **1 mm delay ↔ −1.22 mm edge scale** | **Consistent**, B-short drops −645 ppm.                                                                                        |
| Profile-likelihood elongation                          |                                                                                                              omitted |                                                                                      omitted |                                                                                    **8.9:1** |                                omitted | No conflict; B-short drops figure-specific detail.                                                                             |
| v4-io static median                                    |                                                                                                              omitted |                                                                                      omitted |                                                                                  **72.7 mm** |                            **72.7 mm** | **Consistent.**                                                                                                                |
| Common-mode static median, uncorrected tag delay       |                                                                                                              omitted |                                                                                      omitted |                                                                                 **109.5 mm** |                           **109.5 mm** | **Consistent.**                                                                                                                |
| Per-frame joint tag-delay result                       |                                                                                                              omitted |                                                                                      omitted |                                                         median **72.9 mm**, P95 **317.5 mm** |   median **72.9 mm**, P95 **317.5 mm** | **Consistent.**                                                                                                                |
| Single global tag-delay constant                       |                                                                                                              omitted |                                                                                      omitted |                                                            ≈**50 mm**, leave-one-out/no-peek |                             ≈**50 mm** | **Consistent.**                                                                                                                |
| Metric-correct layout after global tag delay           |                                                                                                              omitted |                                                                                      omitted |                                                      **68.5 mm**, P95 **156 mm**, vs 72.7 mm |                                   same | **Consistent.**                                                                                                                |
| Four-way ablation                                      |                                                                                                              omitted |                                                                                      omitted | Vicon coordinates: **311 mm** no correction, **252 mm** transplanted, **78 mm** re-estimated |                                   same | **Consistent.**                                                                                                                |
| VDOP/HDOP and alias ratio                              |                                                                         A-long says VDOP > HDOP, deployment DOP in B |                                                                                      omitted |                           vertical-to-horizontal alias ratio about **7**; VDOP/HDOP ≈**1.6** |                                omitted | **Framing risk.** Alias ratio 7 and VDOP/HDOP 1.6 are different metrics; label explicitly. B-short drops both.                 |
| Active vertical-information injection                  |                                                                                                          points to B |                                                                                  points to B |                                                    no reduction / no dose-response with tilt |                                omitted | **Cross-doc support gap.** A-short relies on B for vertical-limit explanation, but B-short does not carry it.                  |
| AniTrack comparison                                    |                                                                                                              omitted |                                                                                      omitted |                             **13.96 cm vs 16.57 cm**, 600 m², seven positions, within one SD |             **13.96 vs 16.57 cm**, n=7 | **Consistent.**                                                                                                                |
| External-validity scope                                |                                                                                            single deployment implied |                                                                          not explicit enough |                                                            one room, **24 static positions** |      one room, **24 static positions** | **B is scoped; A is less scoped.** A needs the same one-room/one-lot/static-position caution.                                  |

Core numeric consistency is mostly good. The highest-risk inconsistencies are not values; they are **axis notation**, **coplanar observability wording**, and **A’s promise that B explains the vertical limit while B-short mostly does not**.

---

## D2. Framing / 口径 conflicts

1. **A says B explains the vertical limit; B-short does not.**
   A-long says Paper B explains why the residual vertical error is a per-tag delay aliasing rather than a geometry deficit, and A-short says the companion mechanism paper explains why the vertical limit exists. B-long does contain that strand: it says the 4+4 array is already at the vertical observability floor and that tag range bias aliases predominantly into vertical, with VDOP/HDOP ≈1.6 and an active vertical-information injection showing no dose-response.  
   **Problem:** B-short’s thesis and contribution focus almost entirely on anchor-side delay/layout-scale coupling and same-environment metric correctness. It omits the vertical-alias mechanism.
   **Fix:** Add one compact B-short sentence: “A tag-side strand explains AutoPos’s residual vertical error: after 4+4 geometry reaches the vertical CRLB floor, common tag range bias aliases mainly into height; vertical-information injection showed no dose-response.”

2. **Paper A partially double-claims Paper B’s novelty.**
   A-long says “We name and stake the effect here; the companion paper supplies its identifiability analysis,” while later saying Paper B establishes priority on delay/layout coupling.  
   **Problem:** This creates priority overlap. A should not “stake” the delay/layout coupling if B is the mechanism paper.
   **Fix:** In A, write: “Paper A observes the residual vertical bias empirically; Paper B names and analyzes the delay/layout coupling.”

3. **A-short contradicts A-long on coplanar vertical observability.**
   A-short: “with a single coplanar layer the vertical is badly ill-conditioned — the height still falls out.” Later A-short and A-long say a coplanar layer is exactly singular.  
   **Fix:** Replace with: “With ideal coplanar inter-anchor ranging, vertical is unobservable; in practice any apparent height comes from priors, nonidealities, or external constraints and is not a calibrated vertical.”

4. **Coordinate convention is inconsistent.**
   A-long uses vertical as **y** in ∂r/∂y and calls the horizontal plane X–Z; A-short calls the vertical “Z” and uses Δz.  
   **Fix:** Pick one convention. For metrology/localization readers, use **z = vertical** unless there is a strong reason not to. Then change A-long to “∂r/∂z = 0” and “horizontal X–Y plane.”

5. **B-long has a scale-sign ambiguity.**
   B-long says an under-absorbed common-mode range bias “expands” the layout to AutoPos-to-Vicon scale 0.958, but later says a positive common bias “shrinks” the layout.  
   **Fix:** Define the Sim(3) convention once: “s_{A→V}=0.958 means AutoPos coordinates must be multiplied by 0.958 to match Vicon; therefore the AutoPos layout is expanded by 1/0.958.” Then align all “shrink/expand” language with that convention.

6. **A’s universal language outruns B’s one-room evidence.**
   B-long is honest that the Erlangen campaign is one room with 24 static positions and that transfer remains unproven.  A-long and A-short repeatedly use broader phrasing such as “the number later work must beat,” “the vertical has no shortcut,” and “no amount of added geometry removes” without equally visible scope limits.
   **Fix:** Add “in this deployment / under this hardware lot and acquisition protocol / pending frozen-pipeline multi-room validation.”

---

## D3. Short-vs-long fidelity

1. **A-short is not fully faithful to A-long because it introduces Xiang 2025 while A-long does not cite it.**
   A-short uses Xiang 2025 to support the UAV/rangefinder contrast; A-long makes the same general argument but does not include Xiang in the reference list. 
   **Fix:** Either add Xiang 2025 to A-long or remove it from A-short.

2. **A-short drops the “why eight anchors, not more” throughput argument too aggressively.**
   A-long says 4+4 is constrained by 10 Hz × up to 10 tags and broadcast coherence.  A-short mentions the designed acquisition but not the throughput constraint in the same place as the 4+4 minimality claim.
   **Fix:** Add: “The eight-anchor count is also a throughput constraint: the broadcast scheme is dimensioned for 10 Hz on up to 10 tags.”

3. **B-short drops a key Fisher caveat.**
   B-long says the softest Fisher modes are within-layer wiggles and that the delay-scale coupling must be read from the delay direction’s marginal, not from the raw softest eigenvector. 
   **Fix:** Add one clause in B-short: “The delay-scale claim is based on the delay marginal/profile, not the globally softest eigenvector.”

4. **B-short drops the vertical-alias strand that A-short depends on.**
   This is the most consequential short/long fidelity problem. Add the tag-side vertical-delay alias sentence noted above.

---

## D4. No double-counting / no contradiction

The intended division is clear: **A = deployed 4+4 pure-UWB self-calibration system; B = delay/layout mechanism and validation protocol.** The actual text blurs that line in two places: A says it “names and stakes” delay/layout coupling, and B invokes the AutoPos vertical residual as if it is part of B’s mechanism story but B-short does not carry the vertical evidence. Fixing those two sentences would largely solve the overlap.

---

# A-LONG — AutoPos system paper

## Must-fix before submission

1. **A-long p.1 / Section 1: “AutoPos is, to our knowledge, the first UWB anchor self-calibration system that recovers full 3D anchor coordinates jointly with per-device antenna delays from inter-anchor ranging alone …” → novelty claim too broad.**
   **Problem:** The contribution is defensible only as a conjunction: static commodity hardware, inter-anchor-only, no surveyed reference, joint delay, Vicon validation, 4+4 vertical geometry. Joint position+delay estimation and UWB self-calibration are prior art. A-long acknowledges this later, but the first sentence still reads like a “first system” claim. 
   **Fix:** “To our knowledge, AutoPos is the first Vicon-validated commodity-hardware demonstration of reference-free **3D inter-anchor-only** UWB anchor self-calibration with joint per-device delay estimation.”

2. **A-long p.2–3 / Table 1: prior-art table compresses incompatible “reference” notions.**
   **Problem:** “No known ref.” mixes “no known anchor coordinates,” “no known mobile/tag positions,” “no external sensor,” and “no known height.” Shah 2022, for example, simultaneously measures anchor position and antenna delay but uses a mobile node at known positions; the paper itself states it does not require a predefined coordinate reference among anchors but does use known mobile positions. ([MDPI][1])
   **Fix:** Split into columns: “Known anchor positions?”, “Known tag/mobile positions?”, “External trajectory/sensor?”, “Known heights?”, “Delay estimated?”

3. **A-long p.3 / Section 4: “the number later work must beat” is promotional, not academic.**
   **Problem:** This is priority rhetoric. It will irritate a Measurement/TIM/IPIN reviewer.
   **Fix:** “This provides a reference baseline for later reference-free pure-UWB vertical self-calibration systems.”

4. **A-long p.3–4 / 4+4 geometry: “balanced 4+4 is therefore the smallest array…” is under-qualified.**
   **Problem:** The claim is true only under the assumed deployment geometry, anchor budget, noise model, and “balanced raising” family. It is not a universal minimality theorem.
   **Fix:** “Within the evaluated 4+k balanced-raising family and the assumed range-noise model, 4+4 is the smallest tested layout that gives uniformly acceptable vertical conditioning.”

5. **A-long p.4 / Figure 1 and text: “production floor” overstates CRLB status.**
   **Problem:** A CRLB is a lower bound under a statistical model, not an empirical production floor unless the noise model, estimator efficiency, and bias conditions are justified. Here the vertical error is also said to include systematic delay aliasing.
   **Fix:** Replace “floor” with “model-based lower bound” unless you explicitly prove estimator efficiency and include bias.

6. **A-long p.6 / comparison to UWB+IMU literature: static-vs-dynamic caveat is good but should be moved earlier.**
   **Problem:** A reviewer will see “same band as best fusion systems” before seeing that AutoPos uses static tags while reviewed systems track moving tags. 
   **Fix:** Put the caveat before the accuracy comparison: “Because our validation uses static tags, while the review concerns moving UWB+IMU systems, the comparison is only a scale-of-error comparison.”

7. **A-long p.7 / Relationship to Paper B: “no amount of added geometric diversity removes” is too universal.**
   **Problem:** B-long’s evidence is one room, one deployed array, one active vertical-injection protocol. It supports “additional vertical calibration information in this tested protocol did not reduce the error,” not a universal impossibility theorem.
   **Fix:** “In this deployment, after 4+4 geometry reaches the modeled vertical bound, added vertical calibration excitation did not reduce the residual error; B attributes the remaining bias to tag-side delay/altitude aliasing.”

8. **A-long p.7–8 / publication strategy and defensive-publication material: not manuscript prose.**
   **Quoted text:** “To maximise the blocking value of the priority record…” and “unpatentable by others.” 
   **Problem:** This belongs in an internal IP/publication plan, not a top-tier paper.
   **Fix:** Remove from manuscript. If needed internally, put it in a separate memo.

9. **A-long references: misattributed / incorrect citations.**
   **Problem 1:** A-long lists “Shah, C.L., Shin, S.-Y. and Jeon, J., 2019. Numerical and experimental evaluation…” but the actual Sensors paper is by **Cung Lian Sang, Adams, Hörmann, Hesse, Porrmann, and Rückert**, not Shah/Shin/Jeon. ([MDPI][2])
   **Problem 2:** A-long lists Shah 2021 IEEE Access pp. **52030–52044**, but the Antenna Delay Calibration of UWB Nodes paper is IEEE Access 9, **63294–63305**. ([MDPI][3])
   **Problem 3:** A-long’s Yuan 2024 reference omits multiple authors; arXiv lists Shenghai Yuan, Boyang Lou, Thien-Minh Nguyen, Pengyu Yin, Muqing Cao, Xinghang Xu, Jianping Li, Jie Xu, Siyu Chen, and Lihua Xie. ([arXiv][4])
   **Fix:** Correct all three before any external submission.

10. **A-long prior art is missing a relevant 2025 external-sensor competitor.**
    **Problem:** Liu & Cao 2025 propose simultaneous UWB-anchor calibration and robot localization using UWB + LiDAR in 3D, with automatic calibration within 30 seconds. It does not break the “inter-anchor-only” claim, but it belongs in the 3D external-sensor cluster. ([arXiv][5])
    **Fix:** Add to Table 1 under “3D but external sensor / LiDAR-dependent.”

## Nice-to-have

11. **A-long p.1–2: “vertical axis has no shortcut” is a useful hook but too absolute.**
    **Fix:** “For the target body-worn clinical use case, the vertical cannot be supplied by the external aids used in UAV/robot systems.”

12. **A-long p.4: Figure 1 is readable, but the caption is too dense.**
    **Fix:** Move the numeric sequence into the text or a small table; keep the caption to what the reader needs to interpret the panels.

13. **A-long p.8–9: reference formatting is inconsistent.**
    Some entries are arXiv-only, some conference entries lack DOI/pages, and the style varies. Normalize to the venue style.

14. **A-long PDF accessibility:** the rendered PDF is readable, but extraction garbles ligatures/dashes because of Type 3 / non-Unicode-mapped glyphs. This affects copy-paste and indexing. Recompile with modern font encoding.

---

# A-SHORT — AutoPos system brief

## Must-fix before submission

1. **A-short p.1: coplanar vertical wording is technically wrong.**
   **Quoted text:** “with a single coplanar layer the vertical is badly ill-conditioned — the height still falls out, just far too inaccurate to use.” 
   **Problem:** This contradicts the later “vertical CRLB exactly singular” statement.
   **Fix:** “With a single coplanar inter-anchor layer, vertical is unobservable in the ideal range-only model; any apparent height comes from nonidealities or priors, not from calibrated geometry.”

2. **A-short p.1: vertical axis notation conflicts with A-long.**
   **Quoted text:** “Why pure-UWB Z, and why 4+4.”
   **Problem:** A-long uses ∂r/∂y for the vertical and calls X–Z horizontal.
   **Fix:** Use z as vertical everywhere, or change this heading to “Why pure-UWB vertical, and why 4+4.”

3. **A-short p.1: universal claim about external height aids is too broad.**
   **Quoted text:** “Across both directions the vertical is imported from an external aid, never recovered from UWB geometry alone.”
   **Problem:** This is a universal prior-art claim in a two-page brief. It rests on selected literature, not a systematic survey of all UWB vertical-positioning work.
   **Fix:** “In the cited clinical and UAV examples, the vertical is supplied by external aids rather than by UWB geometry alone.”

4. **A-short p.1–2: the short version drops key caveats while keeping the “first” claim.**
   **Problem:** It states the empty-cell claim and the 59 mm / 63 mm headline but omits important constraints: static tag validation, one room / one hardware lot, and dependence on the eight-anchor broadcast acquisition.
   **Fix:** Add one sentence: “These results are for the tested single-room, static-tag validation and the designed eight-anchor broadcast acquisition; multi-room transfer remains to be shown.”

5. **A-short p.2: process / strategy language is inappropriate for a paper.**
   **Quoted text:** “Publish this system paper first and early … so it becomes the citable prior work the companion paper builds on.” 
   **Problem:** This is an internal publication plan.
   **Fix:** Remove.

6. **A-short references: Xiang 2025 is not in A-long.**
   **Problem:** The short introduces a prior-art comparison not present in the long version. Xiang 2025 is real and uses a UAV rangefinder for altitude, but long/short fidelity requires matching coverage. ([MDPI][6])
   **Fix:** Add Xiang 2025 to A-long or delete from A-short.

7. **A-short p.1–2: “centimetre level” for 59 mm needs care.**
   **Problem:** “Centimetre level” is acceptable in the broad sense of several centimetres, but some reviewers read it as 1–2 cm.
   **Fix:** “multi-centimetre vertical accuracy” or “≈6 cm vertical accuracy.”

## Nice-to-have

8. **A-short p.1: grammar / typography.**
   **Quoted text:** “Anchor self -calibration” and “Why pure-UWB Z , and why 4+4.”
   **Fix:** “Anchor self-calibration” and “Why pure-UWB vertical, and why 4+4.”

9. **A-short p.1–2: too many dense paragraphs.**
   **Problem:** The brief is readable only with effort; it compresses a full paper into two pages without enough signposting.
   **Fix:** Use three bullets under “Contribution” and a small table for “Claim / Evidence / Scope.”

10. **A-short p.2: reference list is very small.**
    **Fix:** If this is for a two-page venue, use a conference-compliant compact bibliography; if it is an internal brief, readability matters more than fitting everything onto two pages.

---

# B-LONG — delay/layout mechanism paper

## Must-fix before submission

1. **B-long p.1 / thesis: one sentence carries too many claims.**
   **Quoted text:** “UWB anchor self-calibration couples antenna-delay/range-bias parameters with layout scale, which makes same-environment tag accuracy unreliable as a calibration-quality metric…” 
   **Problem:** The thesis conflates general mechanism, one dataset, conditional tag-delay cancellation, and ranking reversal. It reads as a universal law.
   **Fix:** “In the Erlangen campaign, same-environment tag accuracy was an unreliable proxy for metric calibration quality because an uncorrected tag delay made a scale-biased layout outperform a metric-correct layout.”

2. **B-long p.3–4: delay-scale Fisher result is strong, but the generality must be scoped.**
   **Problem:** The reported ρ = −0.977, 22× variance inflation, −1.22 mm/mm alias, and 8.9:1 valley are for one deployed geometry. 
   **Fix:** “On the deployed Erlangen eight-anchor geometry…” should precede every headline use of these values.

3. **B-long p.3: sign convention around scale is ambiguous.**
   **Problem:** “scale 0.958” is described as an expanded layout in one place and later as consistent with a positive common bias shrinking the layout.
   **Fix:** Define whether Sim(3) scale maps AutoPos→Vicon or Vicon→AutoPos, and state the physical implication once.

4. **B-long p.5 / Section 2.6: AniTrack comparison is properly cautious but still risky.**
   **Problem:** You correctly say it is not confirmation, but the section heading “Independent Observation of the Wrong-Calibration-Wins Paradox” overstates it. The cited AniTrack paper reports self-localized anchors averaging 13.96 cm in a 600 m² test, but it does not diagnose delay/layout coupling. ([arXiv][7])
   **Fix:** Rename: “Related observation: self-localized anchors can outperform surveyed anchors in tag error.”

5. **B-long p.7 / Table 1: “Most prior work satisfies at most one” is overbroad.**
   **Problem:** You have not shown a systematic screening protocol for all UWB calibration papers.
   **Fix:** “The closest papers we identified do not jointly satisfy all three conditions.”

6. **B-long p.7: contribution hierarchy conflicts with title/thesis.**
   **Quoted text:** “the methodological falsification protocol … is the headline claim, and the empirical coupling result is framed as characterization rather than first discovery.” 
   **Problem:** The title and opening thesis foreground delay/layout coupling; this sentence says the protocol is the headline.
   **Fix:** Decide the paper’s primary claim. For Measurement/TIM, I would lead with **metric correctness versus positioning accuracy**, then present delay/layout coupling as the mechanism.

7. **B-long p.8: “coupling is intrinsic to commodity single-antenna hardware” is over-claimed.**
   **Problem:** The evidence is nine modules from one manufacturing lot. 
   **Fix:** “In this commodity single-antenna hardware lot, factory OTP did not supply the needed per-device delay corrections; this makes self-calibration susceptible to the coupling unless per-unit bench calibration is performed.”

8. **B-long p.8–9: “first gauge-free Fisher/proﬁle-likelihood quantification” is plausible but needs citation discipline.**
   **Problem:** Cross-domain ambiguity is known; UWB delay/bias calibration is well studied. You can claim “we did not find,” but not an absolute first unless the search protocol is included.
   **Fix:** “To our knowledge, this is the first gauge-free Fisher/profile-likelihood quantification of this delay-scale near-degeneracy on real UWB self-calibration hardware.”

9. **B-long p.12 / references: process note left in bibliography.**
   **Quoted text:** “Batstone … [verify exact venue/pages against PDF before submission].” 
   **Problem:** This is unacceptable in any submission.
   **Fix:** Replace with the verified citation. The Batstone IPIN 2017 record exists and lists IPIN 2017 with DOI 10.1109/IPIN.2017.8115885. ([ResearchGate][8])

10. **B-long references: Shah 2022 author list is incomplete.**
    **Problem:** B-long lists Shah, Kovavisaruch, Kaemarungsi, Demeechai for the 2022 Sensors paper; the MDPI page shows the work concerns simultaneous measurement of anchor position and antenna delay, and the citation metadata includes Chaiwong among the authors. ([MDPI][1])
    **Fix:** Add K. Chaiwong.

11. **B-long p.2 / Hamer claim needs verification.**
    **Problem:** B-long says the qualitative common-delay/scale tradeoff has been noted in UWB by Hamer & D’Andrea. I found Hamer & D’Andrea’s self-calibrating UWB network, but did not verify an explicit delay-layout-scale degeneracy claim in the searchable snippets. ([IEEE Xplore][9])
    **Fix:** Either quote the exact Hamer passage in the manuscript or weaken to “related UWB self-calibration work models propagation delay and anchor computation, but does not quantify the delay-scale near-degeneracy.”

## Nice-to-have

12. **B-long p.4 / Figure 1: readable, but not grayscale-safe.**
    **Problem:** The red dashed alias line over a heat map may not survive grayscale printing.
    **Fix:** Add a high-contrast contour/line style and avoid relying on color alone.

13. **B-long p.7 / Table 1: the table is conceptually useful but too text-heavy.**
    **Fix:** Compress each row to one phrase; move detailed explanation to prose.

14. **B-long p.8 / Factory OTP paragraph: split.**
    **Problem:** It contains OTP values, fitted delays, temperature field, crystal trim, and interpretation in one paragraph.
    **Fix:** Use a small table: module group / OTP / fitted delay range / interpretation.

15. **B-long p.11–12: “Target Venue and Titles” and “Risk Assessment” are internal meta-sections.**
    **Problem:** They are useful for planning but not manuscript content.
    **Fix:** Remove from submission manuscript. Keep the “What we are not claiming” material, but integrate it into limitations.

---

# B-SHORT — delay/layout mechanism brief

## Must-fix before submission

1. **B-short p.1: thesis is too dense for a short paper.**
   **Problem:** The first paragraph includes the main claim, Sim(3), common-mode recovery, three medians, P95, global constant, and philosophical conclusion. It is technically coherent but overloaded. 
   **Fix:** Split into: “Mechanism,” “Paradox,” “Control,” “Conclusion.”

2. **B-short p.1: “v4-io” is undefined.**
   **Problem:** A reviewer outside the project cannot know whether v4-io is firmware, solver version, layout, or dataset.
   **Fix:** “the production AutoPos self-calibrated layout (‘v4-io’).”

3. **B-short p.1: drops the Fisher caveat from B-long.**
   **Problem:** It reports ρ = −0.977 and 22× but not the caveat that the softest raw Fisher eigenmodes are within-layer wiggles and the delay-scale result is from the delay marginal/profile.
   **Fix:** Add the caveat in one sentence.

4. **B-short p.1–2: missing tag-side vertical aliasing strand.**
   **Problem:** A-short points to B for the vertical residual, but B-short never states the vertical mechanism.
   **Fix:** Add: “Separately, the same campaign shows a tag-side delay/altitude alias: after 4+4 reaches the vertical bound, added vertical excitation did not reduce vertical error.”

5. **B-short p.2: “A raw-frame intervention…” is structurally misplaced.**
   **Problem:** It appears after the cross-domain conjecture, making the causal evidence feel like an afterthought.
   **Fix:** Move it directly after the global-tag-delay control paragraph.

6. **B-short p.2: “Priority and disclosure” is internal strategy language.**
   **Problem:** A manuscript should not discuss defensive-publication value.
   **Fix:** Rename to “Claim scope” or remove.

7. **B-short p.2: final paragraph contains venue targeting.**
   **Quoted text:** “Target: Measurement (IF ∼5.6).”
   **Problem:** Not submission prose.
   **Fix:** Remove.

8. **B-short references: Shah 2022 author list incomplete, same as B-long.**
   **Fix:** Add Chaiwong.

## Nice-to-have

9. **B-short p.1 / Literature Gap table is cramped.**
   **Problem:** At print size, the table is readable but unpleasant. It consumes space that could clarify the mechanism.
   **Fix:** Replace with four bullets: self-calibration, delay calibration, NLOS mitigation, identifiability.

10. **B-short p.2 / Risk table is useful but should be reframed.**
    **Problem:** “Risk and Status” sounds like an internal review memo.
    **Fix:** “Limitations.”

11. **B-short p.3 / references are too dense.**
    **Fix:** If this is a true short paper, cite only the nearest prior art and move the rest to long/supplement.

---

# AXIS A — NOVELTY & PRIOR ART, PER DOCUMENT

## A-long

**Actual novel contribution, one sentence:** A-long’s real contribution is a Vicon-validated, commodity-hardware, reference-free **3D inter-anchor-only** UWB anchor self-calibration system with joint per-device delay estimation, using a balanced 4+4 array and reporting both vertical accuracy and deployment diagnostics. 

**Novelty judgment:** Defensible as a systems/deployment contribution; not defensible as a new estimator. The nearest competitors are Decawave/Qorvo PANS auto-positioning, Corbalan 2023, De Preter 2019, Piavanini 2022, Shah 2022, Batstone 2017, Hamer & D’Andrea 2018, and recent external-sensor 3D calibration papers such as Yuan 2024/2025, Nguyen 2025, Delama 2025, and Liu & Cao 2025. The paper already acknowledges many of these, which is good. It should add missing recent external-sensor work and tighten the table categories. ([arXiv][5])

**Universal-law claims to scope:** “vertical has no shortcut,” “number later work must beat,” “no amount of geometry removes,” and “first demonstration” need deployment scope.

**Citation issues:** The Shah 2019 reference is misattributed; Shah 2021 page range is wrong; Yuan author list is incomplete.

**Positioning honesty:** Mostly honest in the long form because it admits no new estimator, static-vs-dynamic caveat, and unvalidated physical delays. The risky part is the headline rhetoric.

## A-short

**Actual novel contribution, one sentence:** A-short compresses the same AutoPos system claim: 4+4 reference-free pure-UWB 3D anchor self-calibration with joint delay estimation and Vicon-validated vertical accuracy.

**Novelty judgment:** Borderline. The short version is less defensible than A-long because it drops too many caveats while keeping the strongest “empty cell / first” language.

**Universal-law claims to scope:** “never recovered from UWB geometry alone” and “body-worn clinical tag can carry no such aid” need narrowing.

**Citation issues:** Xiang 2025 is real and relevant to the UAV/rangefinder contrast, but it is not in A-long, so the short is not a pure compression. ([MDPI][6])

**Positioning honesty:** Weaker than A-long. It needs one explicit “single-room/static-tag/current hardware” limitation.

## B-long

**Actual novel contribution, one sentence:** B-long’s real contribution is a Vicon-grounded demonstration that anchor-side common-mode delay is strongly coupled to isotropic layout scale in a real UWB self-calibration, and that uncorrected tag delay can make a scale-biased layout look better than a metric-correct one until a global tag-delay correction reverses the ranking. 

**Novelty judgment:** Borderline-to-strong for Measurement/TIM if framed as measurement validity, not as discovery of delay/scale ambiguity in general. Cross-domain ambiguity and delay calibration are prior art; the stronger claim is the **measured UWB ranking inversion plus falsification protocol**.

**Universal-law claims to scope:** “same-environment tag accuracy is unreliable” should become “can be unreliable under coupled geometry/delay bias.”

**Citation issues:** Batstone verification note left in references; Shah 2022 author omission; Hamer explicit delay-scale precedent unverified from the sources I checked.

**Positioning honesty:** Stronger than A. It explicitly acknowledges acoustic/GNSS precedents, AniTrack is not treated as confirmation, and one-room/24-position limits are stated. 

## B-short

**Actual novel contribution, one sentence:** B-short compresses B-long’s delay-scale near-degeneracy, wrong-geometry-wins condition, and global-tag-delay control.

**Novelty judgment:** Borderline. The core numbers match B-long, but key caveats are missing.

**Universal-law claims to scope:** “must be validated separately” is acceptable as a recommendation, but “same-environment accuracy is not reliable” should be “is not sufficient.”

**Citation issues:** Same Shah 2022 author omission; otherwise the main references I checked are real.

**Positioning honesty:** Good on the conditional nature of the effect, weak on omitted vertical-alias strand and omitted Fisher caveat.

---

# AXIS C — PDF FORMATTING & PRESENTATION, PER DOCUMENT

## A-long

1. **Floats:** Clean. Table 1 appears immediately after the Section 3 discussion; Figure 1 appears after first mention and is close enough.
2. **Figures:** Figure 1 is readable at print size. Caption is self-contained but too long.
3. **Tables:** Table 1 is readable, but the binary columns oversimplify reference conditions.
4. **References/cross-refs:** No “??” found. Reference metadata problems noted above.
5. **Typography:** Rendered output is readable. However, PDF text extraction garbles dashes/ligatures, indicating font encoding/accessibility problems.

## A-short

1. **Floats:** None.
2. **Figures:** None.
3. **Tables:** None.
4. **References/cross-refs:** No broken refs found; references are cramped.
5. **Typography:** Text is dense and small. Page 2 is especially crowded; acceptable for an internal concept brief, weak for a reviewed short paper.

## B-long

1. **Floats:** Figure 1 appears after first mention, acceptable. Table 2 spans pages 9–11; continuation is mostly clear.
2. **Figures:** Figure 1 is readable, but color dependence is a print/grayscale risk.
3. **Tables:** Table 1 and Table 2 are dense. Table 2 is useful but visually heavy.
4. **References/cross-refs:** No “??” found, but the Batstone reference contains an explicit verification note.
5. **Typography:** Generally clean. Same PDF text-extraction/font encoding problem as A.

## B-short

1. **Floats:** None.
2. **Figures:** None; acceptable, though the Fisher result would benefit from one compact plot if space allowed.
3. **Tables:** The Literature Gap table on page 1 and Risk table on page 2 are cramped.
4. **References/cross-refs:** No broken refs found; reference list is very dense.
5. **Typography:** Problems. The brief is readable on screen but too compressed for print review.

---

# Single most important thing wrong across the whole program

The program’s core weakness is **not numeric inconsistency**; the numbers are mostly aligned. The main problem is **claim scope**: the documents repeatedly promote one-room, one-hardware-lot, static-tag evidence into language that sounds like a general law of UWB self-calibration and vertical observability. Fix that by making the contribution conditional and precise: **“In the tested Erlangen/AutoPos deployment, under the 4+4 broadcast acquisition and commodity DWM1001C hardware, metric correctness and same-environment accuracy diverge because delay parameters couple to scale and tag delay aliases into height.”** That claim is defensible. The current broader rhetoric is what a strong reviewer will attack.

[1]: https://www.mdpi.com/1424-8220/22/3/864 "Node Calibration in UWB-Based RTLSs Using Multiple Simultaneous Ranging | MDPI"
[2]: https://www.mdpi.com/1424-8220/19/3/616?utm_source=chatgpt.com "Numerical and Experimental Evaluation of Error Estimation ..."
[3]: https://www.mdpi.com/2224-2708/12/1/1?utm_source=chatgpt.com "Antenna Delay-Independent Simultaneous Ranging for ..."
[4]: https://arxiv.org/abs/2412.16880?utm_source=chatgpt.com "Large-Scale UWB Anchor Calibration and One-Shot Localization Using Gaussian Process"
[5]: https://arxiv.org/abs/2503.22272?utm_source=chatgpt.com "Robust simultaneous UWB-anchor calibration and robot localization for emergency situations"
[6]: https://www.mdpi.com/1424-8220/25/4/1052?utm_source=chatgpt.com "An Improved UWB Indoor Positioning Approach for UAVs ..."
[7]: https://arxiv.org/abs/2506.00216?utm_source=chatgpt.com "AniTrack: A Power-Efficient, Time-Slotted and Robust UWB Localization System for Animal Tracking in a Controlled Setting"
[8]: https://www.researchgate.net/publication/321260827_Towards_real-time_time-of-arrival_self-calibration_using_ultra-wideband_anchors?utm_source=chatgpt.com "Towards real-time time-of-arrival self-calibration using ultra ..."
[9]: https://ieeexplore.ieee.org/document/8344407/?utm_source=chatgpt.com "Self-Calibrating Ultra-Wideband Network Supporting Multi- ..."
