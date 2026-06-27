# Project-Wide Novelty Ledger + Literature Search

**Date:** 2026-06-27
**Author of this pass:** Claude (Opus 4.8), independent read-only scan of the workspace + fresh web literature search
**Scope:** Not just `novelty_check.tex`. This assesses the novelty of the *whole* BioSpur/AutoPos body of work as of today, item by item, against prior art.
**Stance requested by the user:** objective and fair, do **not** pander. This report tries to hold two opposite biases in check at once: (a) the "criticism is safer" bias that systematically under-rates the work, and (b) the author's natural wish for it to be novel.

---

## 给你的话(醒来先读这一段)

你不是"4 个月没有 novelty"。真相是:你把整个项目压缩成了**一篇单点论文的单个 claim**,然后拿"这一个 claim 够不够新"去给全部工作判刑——分母用错了。

这次独立联网检索的结论:

- **你手里不是 1 个勉强的 claim,而是 2–3 个可分离的可发表单元。**
- **最被你低估的是 RotArm(旋转臂 Z 注入)那一项。** 它的"encoder-free 角度自标定 + 倾斜双 tag 刚体 + 给各向异性 Z 信息注入"这个**具体组合**,我没在文献里找到。它有独立的 method-paper 潜力——**前提是**你能拿出"它确实改善了 Z 标定"的实测证据(这一点我从现有文档里没确认到,见下面 ⚠️)。
- delay–layout coupling 那篇方向对、能投,只是窄;别再被 hedge 到自我否定。
- 别担心"零件都是已知的"(SDP/Tukey/GNC/antenna-delay)——那是工程选型,本来就不该当卖点。评审不会因为你用了标准 robust solver 就扣分;他们扣分是因为**把三盘菜倒进一个碗**。

下面是逐项的、带引用的硬核判断。

---

## TL;DR verdict table

| # | Work unit | Verdict | Confidence | One-line reason |
|---|---|---|---|---|
| 1 | Delay–layout coupling / "wrong-geometry-wins" (the paper) | 🟡 New combination, real but narrow | High | Coupling exists cross-domain; the Vicon-grounded UWB characterization + 0.11 mm closure + causal intervention is not reported in UWB. |
| 2 | **RotArm: tilted rotating dual-tag rigid arm, encoder-free angle, Z-FIM injection** | 🔴 Novel combination, but **mechanism FALSIFIED in BOTH solver models** | High | Combination not found in lit, BUT two independent Vicon-validated dose-response tests — free-point (Appendix R) AND the proper **rigid-arm** model (Appendix R2) — both show vertical anchor error does **not** decrease with tilt (rigid slope **+0.03 mm/deg, r=+0.08**; vertical-90° bin is the *worst*; every single-capture tilt bin worse than no-roto baseline). all-roto helps only marginally via data volume, not tilt, and never reaches production no-roto. Mechanism dead. |
| 3 | Falsification / measurement-validity protocol | 🟡 Methodological, moderate | Medium | Winner's-curse/optimism is decision theory; EvAAL benchmarks positioning; nobody applies a validity protocol to *calibration-layout selection* in UWB. |
| 4 | Adaptive redundancy-switch tag solver (T4) + orientation-free IMU-|a|std gating | 🔵 redundancy-switch = minor; IMU-gate = sim-only | Medium | Redundancy-switch part is an honest small systems finding; the IMU-gate part is **NOT validated on real device IMU** (see IMU-provenance note). |
| 5 | Individual solver components (SDP/MDS init, MVUE fusion, Tukey IRLS, GNC, joint antenna-delay, clock-drift, bias learning) | 🔵 Known, well-executed | High | Textbook/known. Correct engineering, **not** paper novelty. Do not sell these. |
| 6 | Static 8-anchor two-layer geometry for Z | ⚪→🔵 Not novel | High | "Non-coplanar anchors help Z (GDOP/Fisher)" is textbook. Novelty is in #2's *active injection*, not the static layout. |
| 7 | Broadcast SS-TWR / TDMA / BLE control plane / OTA / unified firmware | ⚪ Not research novelty | High | Substantial engineering and scope; not a publishable novelty by itself. |
| 8 | Integrated Vicon-grounded measurement study + honest negative results | 🟡 Study-level value | Medium | The integration and the negative results (Vicon anchors don't fix dynamic; filtering ≠ calibration; wall/NLOS tail) are exactly what a *Measurement*-type reviewer values. |

Legend: 🟢 genuine novelty · 🟡 valuable new combination · 🔵 known-but-well-executed · ⚪ not novel.

**Search caveats:** web search is US-region and mostly abstract/HTML level; several closest papers (De Preter 2019 full text, Krapež earlier rigid-pattern paper, paywalled IEEE) were read only via abstracts/secondary descriptions. Treat "not found" as "not found in this pass," not "proven absent." Anything marked **must verify** below needs a full-text read before it goes in a manuscript.

> ### ⚠️ IMU-data provenance correction (added 2026-06-27, user-flagged + code-verified)
> **No real on-device IMU data is used anywhere in this campaign's analysis.** Verified in code:
> - `IMU-Fusion-Simulation/SIMULATION_PLAN.md`: the pipeline is **"Vicon-derived synthetic IMU"** (`01_generate_vicon_perfect_imu.py`, "Use Rxx.trc to generate Vicon body pose and perfect IMU").
> - `NAMING_RULES.md`: the **L0/L2/L16/L20 grades are "simulated IMU realism" levels, not real sensors** ("Do not call L sensor models algorithms").
> - `run_final_data_audit.py`: **"Static captures have no usable IMU signal: `imu_valid` sums to 0, `imu_n` sums to 0, accelerometer aggregate columns are null."**
> - Dynamic uses **`roto_pseudo_imu`** (synthesized). RotArm tilt/axis is computed from the **fitted circle-plane normal (geometry)**, not from the LIS2DH12.
>
> **Consequences:** (a) **Phase 4 UWB+IMU fusion** results = simulation with oracle-derived synthetic IMU → may only be presented as *simulation/replay evidence*, never as embedded-system measurement. (b) **Item 4's IMU-gate** was never exercised on real device IMU → demote to design/sim-only. (c) **RotArm's "encoder-free, IMU-for-axis, self-contained" claim** was *not* experimentally demonstrated self-contained — the rotation axis came from Vicon-derived geometry. The geometric Z-injection **idea** survives; the *demonstration* does not. (d) **Paper A (delay coupling) and Item 3 (protocol) are unaffected** — they use no IMU.
>
> **Net:** this correction *shrinks the overclaim surface*, which for a metrology venue (*Measurement*/*TIM*) is protective: a reviewer who caught a real-vs-synthetic IMU overclaim would taint the whole paper. Good that it's caught pre-submission.

---

## Item 1 — Delay–layout coupling / "wrong-geometry-wins"

**Claim under test:** In UWB *range-only anchor self-calibration*, a per-device antenna-delay / range-bias term shares a weakly observable direction with global layout scale, so a metrically distorted self-calibrated layout can *out-position* the metric-correct one in-environment when a correctable tag/range bias is left uncorrected; reducing the bias reverses the ranking.

**Nearest prior art (independent pass confirms `novelty_check.tex`'s list, adds nothing closer):**
- **AniTrack** (Luder et al. 2025) — self-localized anchors beat laser-surveyed anchors on tag accuracy (13.96 vs 16.57 cm), *observed but unexplained*. Still the single closest external observation. https://arxiv.org/pdf/2506.00216
- **Corbalan et al. 2023**, "Self-Localization of UWB Anchors: From Theory to Practice" — compares self-localized geometry to ground truth, reports geometry error propagates *weakly* to tag error, but never constructs a "wrong layout wins" case or isolates a cancellation cause. https://disi.unitn.it/~picco/papers/access23.pdf
- **Pan et al. 2015** (GNSS) — satellite-clock estimation absorbs orbit error and *improves* PPP positioning. The closest cross-domain "error absorption helps positioning." (Already cited.)
- **Burgess/Kuang/Åström** (acoustic TOA self-cal) — scale/offset affine ambiguities, delay-as-first-class-unknown. (Already cited.)
- Antenna-delay literature (Shalaby, Ledergerber, De Preter, Liu, Shah, Piavanini) frames delay as a *nuisance to remove to improve accuracy* — **none** frames bias-cancellation as making a wrong geometry win. Confirmed again this pass via the delay-calibration query (all results are "remove bias → better accuracy").

**Verdict:** 🟡 The *existence* of scale↔propagation-parameter coupling and of useful-bias effects is firmly prior art (correctly conceded in `novelty_check.tex`). The **specific, Vicon-grounded, closure-validated (189.24 / 68.82 / 120.42 / 120.53 mm, Δ=0.11 mm) UWB manifestation + the causal raw-frame intervention that flips the ranking + the explicit "metric correctness vs in-environment accuracy" separation** is, as far as this pass found, **not reported in UWB**. This is a real, narrow, publishable contribution. The current paper's framing (headline = the *falsification protocol*, the coupling = *characterization not first discovery*) is the correct, defensible framing. **Don't dilute it further; it's already honest to the point of self-harm.**

**Venue:** *Measurement* (primary), IEEE *TIM* (backup) — both fit; this is metrology, not a new RTLS.

---

## Item 2 — RotArm (the most under-valued item) ⚠️

**Claim under test:** A cheap, purpose-built **tilted rotating rigid arm carrying two tags at fixed radii on opposite sides** injects anisotropic vertical (Z) Fisher information into *range-only* anchor self-calibration of a near-two-layer 8-anchor array; the **rotation angle θ_k is self-calibrated from the over-determined UWB ranging (no encoder, no total station, no motion capture)**; the rotation *axis* is *designed* to be read from the on-board accelerometer (gravity, while static). Rigid-body (`|p1−p2| = r1+r2`) + circular-arc constraints regularize the solve. **Provenance reality (code-verified):** in the as-run campaign the axis was taken from the **fitted circle-plane normal (Vicon-derived geometry)**, not from the LIS2DH12 — the accelerometer-axis path is a *design intent*, not a demonstrated step (see IMU-provenance note).

**⚠️ Premise correction (Z information is NOT zero in this deployment):** The `F_zz ≈ 0` motivation in the V4 guide ("when all anchors are at the same height → `u_{i,z}≈0`") strictly applies to **coplanar/single-layer** anchors. This array is **two-layer (Δz≈1.4 m)**, so the upper/lower anchors give **opposite-sign `u_{i,z}`** — i.e. the array already provides vertical Fisher information and the Z axis is **weakly conditioned, not unobservable**. Empirical confirmation from the audit: vertical static median 61.9 mm vs horizontal 37.4 mm at `d_tag=0` (Z worse but tens of mm, not "blown up"), dropping to 39.3 mm vertical once a tag delay is applied (so much of the "Z weakness" is **delay coupling**, not missing geometry); stratified keep-k shows dropping a lower-layer anchor hurts more than an upper one (both layers contribute Z). **Consequence:** RotArm's honest pitch is *not* "0 → nonzero" but "improve the conditioning of an already-observable-but-weak, delay-entangled Z/scale direction." This is a weaker motivation and a third reason to temper the claim; the V4 guide's coplanar `F_zz≈0` wording must be corrected before submission (a reviewer will note the anchors are two-layer).

**Closest prior art found, and why each does NOT preempt the combination:**

| Prior art | What it has | What it lacks vs RotArm |
|---|---|---|
| **Trajectory-constrained rotating-arm rig for GNSS kinematic testing** (Measurement, 2019) [S0263224119303124] | A literal rotating arm, circular trajectory as positioning reference, published in your target journal | Trajectory measured by **total station** (not self-solved); single antenna (no dual-tag scale lock); a **reference/validation** rig, not an information *injector* for self-calibration; GNSS not UWB |
| **Krapež et al.** rigid 4-tag pattern "like the VICON IR calibration stick" (Sensors 2022, 22:9363) | Rigid multi-tag calibration body for UWB | **Static** pattern, LoS-reliant; no rotation; no encoder-free angle; no Z-FIM-injection argument |
| **Goudar et al. 2021** wand: 2 Decawave radios + Xsens IMU, TW-ToF (your `goudar_2021`) | Two tags + IMU on a moving rigid wand | Tightly-coupled **full 6-DoF IMU** drives the estimate; not encoder-free-from-ranging; not a constrained circular Z-injector |
| Motion/odometry-aided self-cal (Almansa 2020; "Fast self-cal aided by odometry" ION; "Real-Time Init of Unknown Anchors" 2025; "Two-Stage UWB Anchors' Self-Cal" IEEE 2025; GP one-shot calibration 2024) | Mobile node trajectory recovers anchor geometry incl. heights | Free/odometry-driven trajectory with external pose aid; **none** uses a constrained rotating dual-tag rig with the angle solved purely from UWB |
| UWB community knowledge: "vertical motion is required to observe anchor height" (multiple: emergency-cal 2503.22272; init 2506.15518) | Confirms Z needs vertical excursion | This is the *motivation* RotArm exploits, not the method; RotArm's novelty is the *apparatus + encoder-free angle*, not "Z needs vertical motion" |
| Acoustic/sensor-net self-cal with motion (Kuang/Åström stratified TDOA; circular mic arrays; TDOA 2D self-cal 2005.10298) | Delay-as-unknown, rotating/circular setups, scale recovery | Different domain; rotating mic arrays rotate the *array*, not a dual-source rigid arm probing a fixed array with self-solved angle |

**Verdict:** 🟢→🟡 **Genuine novel instrument/method combination.** Every ingredient exists *separately*; the specific construction was **not found** in this pass. The honest framing for a paper is *"a low-cost, encoder-free, self-angle-calibrating rotating dual-tag rig that injects controlled anisotropic Z information into range-only UWB anchor self-calibration"* — **not** "we discovered that vertical motion helps Z" (known). Lead with the three real differentiators: (1) **encoder-free** angle solved from the 16-measurements-per-unknown over-determination; (2) **dual-tag rigid-body scale lock**; (3) explicit **Z-FIM filling** with the `Δz = 2r·sinα` argument.

**⚠️ Two make-or-break caveats (both must clear before claiming a paper):**

*Strike 1 — does it help?* I did **not** find a clean experimental result showing *"layout calibrated WITH RotArm Z-injection has measurably better Z accuracy than WITHOUT."* The Erlangen evidence centers on `v4-io` layout, delay coupling, and static/dynamic tag accuracy; the dynamic ROTO floor stayed ~100 mm and the audit notes *"rigid-body ROTO solvers worsened the dynamic result."* A method paper needs the method to **demonstrably help**. **Action: locate (or run) the ablation `Z-error of anchor layout: RotArm-injected vs no-RotArm`** against Vicon.

*Strike 2 — was it self-contained?* Code-verified: the as-run rotation axis came from **Vicon-derived geometry**, and there is **no real on-device IMU anywhere** (static IMU = 0; dynamic = pseudo-IMU; Phase 4 = synthetic). So the headline selling point — *encoder-free, self-contained, no external reference* — was **not experimentally demonstrated**; it was Vicon-assisted. The geometric idea is intact and the gravity-from-static-accelerometer step is trivially feasible in principle, **but a reviewer will (correctly) ask for a real run where the axis comes from the device, not Vicon.**

**Verdict consequence:** until both strikes clear, RotArm is best described as a **proposed/designed calibration instrument with Vicon-assisted feasibility evidence**, not a demonstrated self-contained method. If both clear → genuine second paper. Until then, do **not** claim it as an experimentally validated self-contained Z-injector.

**Venue if validated:** IPIN, *Sensors*, or *Measurement*/*TIM* as a calibration-instrument method paper.

---

## Item 3 — Falsification / measurement-validity protocol

**Claim under test:** A reusable protocol that *separates* (a) metric correctness of self-calibrated anchor geometry from (b) in-environment tag positioning accuracy, via Sim(3)-scale validation vs Vicon, transfer matrix over layout/delay choices, **winner's-curse correction**, nested spatial CV, phase-center sensitivity, NLOS leakage audit, and raw-frame ranking reversal.

**Nearest prior art:**
- **Winner's curse / Optimizer's curse** (Smith & Winkler 2006, Mgmt Sci) — established in decision theory; "model-based evaluation is optimistically biased." Not applied to UWB calibration-layout selection. https://pubsonline.informs.org/doi/10.1287/mnsc.1050.0451
- **EvAAL framework** for indoor-localization benchmarking (uses held-out landmarks, 75th-percentile error) — community evaluation practice, but evaluates *positioning systems*, not *calibration validity*. https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5677241/
- **STAR-loc** (2023) — UWB+stereo in a Vicon arena with *known* anchors; a benchmark, not a metric-correctness-of-self-calibration protocol. https://arxiv.org/abs/2309.05518
- Corbalan 2023 — compares self-loc geometry to truth (closest in spirit) but no packaged validity protocol.

**Verdict:** 🟡 Each tool is individually known; **assembling them into a "is the best-positioning layout actually the physically correct one?" validity protocol for UWB self-calibration is a moderate methodological contribution.** This is actually the true headline of the paper in Item 1 (the doc already treats it as the second/headline contribution — correct). Importing "winner's curse" language explicitly into UWB calibration evaluation is a nice, defensible framing reviewers will respect.

---

## Item 4 — Adaptive redundancy-switch tag solver (T4) + orientation-free IMU gating

**Claim under test:** A tag-side solver that **switches by runtime anchor redundancy** — memory-free robust WLS when 8 anchors are present, T3-style temporal stabilization when <8 — plus a **`T4_V6_IMU_GATE`** that scales the previous-position prior by the **standard deviation of accelerometer magnitude** `std(|a|)` (orientation-free, since tag orientation is unknown).

**Nearest prior art:**
- Motion priors / particle filters for UWB with prior-position determination (NAVIGATION 2021, 68(2)). https://navi.ion.org/content/68/2/277
- Adaptive sensor weighting by **Helmert Variance Component Estimation** in Visual-Inertial-UWB fusion — data-driven dynamic weights.
- "Exploiting redundancy for UWB anomaly detection" (Frontiers Robotics AI 2023) — uses ranging redundancy for outlier/dropout handling. https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2023.1190296/full
- Continuous-time / fewer-anchor UWB-inertial (CT-UIO 2025) — handles low anchor count via splines + IMU. https://arxiv.org/html/2502.06287v2

**Verdict:** 🔵 The building blocks (robust WLS, motion priors, adaptive weighting, redundancy exploitation) are all known. The **redundancy-switch finding** — *memory-free is better at full redundancy, temporal stabilization only helps below 8 anchors, and several intuitive alternatives (signed-residual memory, hard leave-one-out, motion-gated switching, Tukey on the full-anchor path) were tested and rejected* — is an honest, useful **systems/empirical** contribution **and is the part to keep** (it uses no IMU). The **orientation-free `std(|a|)` gate**, however, was **never exercised on real device IMU** — static captures have zero IMU, and all IMU used is Vicon-synthetic (see IMU-provenance note). So the gate is **design/simulation-only** and must be labeled as such. Net: keep the redundancy-switch as an ablation-backed section of the measurement study (Item 8); present the IMU-gate only as a simulation-stage proposal, not a validated mechanism.

---

## Items 5–7 (for completeness — do NOT sell as novelty)

- **#5 Solver components** (SDP/MDS init, MVUE/MAD fusion, Tukey bisquare IRLS, GNC, joint antenna-delay linear solve, clock-drift, bias-learning outer loop): all standard robust-estimation / sensor-network-localization machinery. Correctly chosen and assembled. Reviewers expect these; presenting them as contributions invites "incremental" rejection.
- **#6 Static two-layer 8-anchor geometry**: GDOP/Fisher rationale for non-coplanar anchors is textbook; multiple 2025 papers (e.g., Sensors 25:5115 anchor-placement influence; coplanar PDOP work) cover placement effects. Not novel.
- **#7 Firmware/system** (broadcast SS-TWR, TDMA, BLE control plane, identity-safe OTA, unified anchor, wand mode): real engineering scope, no research novelty. Good for a system/demo section, not a novelty claim.

---

## Cross-cutting recommendation

1. **Stop judging 4 months by one over-hedged abstract.** You have **2–3 separable units**, not one fragile claim. The salami is sliced wrong — three dishes in one bowl.
   - **Paper A (ready):** Delay–layout coupling falsification study → *Measurement* / *TIM*. (Item 1 + Item 3 as the headline.)
   - **Paper B (highest upside, conditional):** RotArm encoder-free Z-injection self-calibration method → IPIN / *Sensors* / *Measurement*. **Gate: the WITH-vs-WITHOUT Z ablation must exist and be positive.**
   - **Paper/section C:** Adaptive T4 + IMU-gate dynamic positioning, as an ablation-backed section of the measurement study (Item 8).
2. **The #1 thing to verify next (determines whether Paper B exists):** find or run the ablation *"anchor-layout Z accuracy: RotArm-injected vs no-RotArm,"* validated against Vicon. This is the single highest-leverage experiment for your "do I have novelty" question.
3. **For Paper A:** the independent pass found **no closer prior art** than what `novelty_check.tex` already cites. Your literature positioning is solid. The remaining risk is purely "reviewer thinks coupling is a known ambiguity class" — the pre-emptive cross-domain citing (GNSS Pan 2015, acoustic Burgess 2015) + the causal intervention is the right defense, and it's already in the draft.
4. **Reframe for your own morale:** the brutal self-audit (superseding your own claims, 0.11 mm closure, "do not claim X") is a marker of a *strong* researcher, not a null result. Most published UWB papers would not survive the scrutiny you applied to yourself.

---

## Appendix R — RotArm Z-injection experiment (run 2026-06-27): mechanism NOT supported

**Question (Strike 1):** does injecting the tilted rotating-arm captures improve anchor **vertical (Z)** calibration vs Vicon, and does the improvement scale with rotation-plane tilt (the dose-response the Z-injection mechanism predicts)?

**Method:** Reused the validated `solve_v4_fusion/solve_v4.py` joint anchor+tag solver (note: this is the *generic free-point* solver, not the full rigid-arm θ_k model — the full model was never wired in). Inputs: Erlangen inter-anchor sweep (`sweep_SW01`, 28 fused pairs) + 24 static captures as the no-roto baseline; then each of `roto_R01..R17` injected separately, and all together. Tag delay floated to ±150 mm so tag bias can't masquerade as geometry. Each solved layout aligned to the Vicon truth (`erlangen_anchor_truth_all8_v4io.json`, Y-vertical) with **reflection-allowed** Procrustes; vertical = residual along Vicon Y. Harness pre-validated: it reproduces the documented production v4-io numbers exactly (rigid RMSE 105.42, median 92.77, Sim3 67.12, scale 0.958). Per-capture tilt computed from the SVD plane-normal of that capture's solved tag trajectory. Artifacts: `rotarm_zinjection_results.csv`, `rotarm_zinjection_dose_response.png`.

**Result:**

| condition | rigid vertY (mm) | Sim3 vertY (mm) |
|---|---|---|
| baseline (inter + static, no roto) | 81.2 | 34.0 |
| + all roto | 70.7 | 25.1 |
| per-tilt (3.5°→79.4°) | 65–70 | 32–35 |
| production v4-io (no roto, full pipeline) | 59.8 | 31.5 |

Dose-response (vertical error vs tilt): **rigid slope +0.031 mm/deg (r=+0.52); Sim3 slope +0.024 mm/deg (r=+0.65).** The mechanism predicts a *negative* slope. Observed slope is *positive* (higher tilt → slightly worse vertical). Low-tilt (<15°) mean rigid vertY = 67.0; high-tilt (>40°) mean = 68.3. A 3.5° near-horizontal rotation (≈zero Δz) helps vertical as much as a 42° one.

**Verdict:** The RotArm **tilt-based Z-injection mechanism is not supported** on this data with this solver — and the trend is reversed. Roto captures *do* modestly reduce vertical error (81→71 mm rigid), but **tilt-independently**, so the benefit is a generic "more tag-range constraints" effect, not injected Z information. Roto-injected vertical (~67 mm) does not even reach the existing no-roto production vertical (59.8 mm).

**Caveats (fair):** (1) Used the free-point solver, not the full rigid-arm estimator (|p1−p2| constraint, shared rotation plane, θ_k self-calibration). The full model is the proper final test — but the free-point solve has *more* freedom and the vertical excursion is present in the raw data, so a real Z-injection effect should have produced *some* negative trend even here; it produced a positive one. (2) Single room, one rig, one solver. (3) My baseline (81 mm) is weaker than production (60 mm) due to subsampling + no two-layer prior; the *relative* with/without comparison is the valid part.

**Constructive consequence (this null actually supports Paper A):** if the vertical weakness were "missing Z geometry," tilt-injection would fix it. It doesn't. That is positive evidence that the vertical error is dominated by **delay coupling, not missing geometry** — exactly Paper A's thesis (audit: vertical static error 61.9→39.3 mm under tag-delay correction). So this experiment is a clean **negative control that strengthens Paper A**: "we actively tried to inject Z geometry via tilted rotation; the dose-response was flat/reversed, confirming the vertical bias is a delay artifact, not a geometric observability gap."

**Recommendation:** Do **not** build Paper B on tilt-based Z-injection as currently realized. Either (a) implement the full rigid-arm estimator and re-run this exact dose-response before any method claim, or (b) fold this as a negative-control result into Paper A. On current evidence RotArm is a designed apparatus whose central mechanism failed its first falsification test.

## Appendix R2 — Full RIGID-ARM Z-injection test (run 2026-06-27): mechanism FALSIFIED

The user correctly objected that Appendix R's free-point solver discards the rigid-body constraint that is the whole point of RotArm. So I implemented the **full rigid-arm estimator**: per roto frame the two tag positions are derived from a shared per-session rotation (center `c`, axis `n`, angle `θ_k`), radii **pinned to the OptiTrack-measured 426/554 mm**, with an **anisotropic soft out-of-plane compliance** (±~3 mm, since the OptiTrack-measured arm flex is rigid along-rod ~0.3 mm but wobbles ~1→2.8 mm perpendicular, growing with tilt). This collapses per-frame DOF 6→1, forcing range residuals onto anchor geometry — the hypothesized Z-lever. Static captures collapsed to one point each; tag delay floated ±150 mm; layouts aligned to Vicon with the validated reflection-aware harness; **x-axis = OptiTrack true tilt** (the solver's own axis estimate was unreliable). Geometry validated every capture (inter-tag 960–1000 mm). Artifacts: `rotarm_zinjection_rigid_results.csv`, `rotarm_zinjection_rigid_dose_response.png`.

**Result (binned by OptiTrack true tilt):**

| condition | rigid vertY (mm) | Sim3 vertY (mm) | Δrigid vs baseline |
|---|---|---|---|
| baseline (inter + static, no roto) | 68.8 | 31.1 | — |
| tilt ~1° (n=1) | 83.7 | 38.2 | +14.9 |
| tilt ~22° (n=4) | 76.8 | 35.9 | +8.0 |
| tilt ~48° (n=4) | 81.1 | 38.1 | +12.3 |
| tilt ~72° (n=4) | 72.4 | 35.0 | +3.6 |
| **tilt ~90° (n=3, vertical)** | **86.9** | **41.8** | **+18.1 (worst)** |
| all-roto (17 together) | 63.4 | 30.2 | −5.4 |
| production v4-io (no roto, full pipeline) | 59.8 | 31.5 | — |

Dose-response slope (vertical error vs OptiTrack tilt, 16 captures): **rigid +0.027 mm/deg (r=+0.08); Sim3 +0.035 mm/deg (r=+0.17).** Mechanism predicts a *negative* slope; observed ≈ zero / slightly positive.

**Verdict — FAIL (pre-registered):** (1) **Every single-capture tilt bin is worse** than the no-roto baseline (+3.6 to +18.1 mm). (2) **No beneficial dose-response** — slope ≈ 0 (r=0.08), and the near-vertical (90°) bin — where Z-injection should be *strongest* — is the **worst**. (3) all-roto improves vertical only marginally (68.8→63.4), a **data-volume** effect from 17 captures, **not** a tilt effect, and it still does not reach the no-roto production layout (59.8). The pre-registered PASS condition (negative slope + all-roto clearly below baseline, ideally ≤ production) is **not met**.

**This confirms Appendix R with the proper model.** The tilt-based Z-injection mechanism is now falsified in **two independent solver formulations** (free-point and rigid-arm). The "maybe the solver was too weak" escape hatch the user raised is now closed: the rigid-body model the user correctly identified as the real test gives the same answer.

**Constructive consequence (unchanged, now stronger):** this is a clean **negative control that supports Paper A**. If the vertical anchor error were a geometric-observability gap, actively injecting tilted-rotation Z would reduce it — it does not, even with the rigid model. So the vertical bias is **delay coupling, not missing geometry** (matches the audit: vertical static error 61.9→39.3 mm under tag-delay correction). Recommendation stands: **do not build Paper B on tilt-based Z-injection; fold this two-model falsification into Paper A as a negative control.** Honest residual caveat: the arm flex (~3 mm) and OptiTrack wand-marker mislabeling (~50% of frames) are small/handled and do not change the verdict; the rigid baseline (68.8) underperforms production (59.8) due to the simplified harness, but the internal with/without and dose-response comparisons are the valid part.

## Sources (this pass)

- AniTrack (Luder 2025): https://arxiv.org/pdf/2506.00216
- Corbalan, Self-Localization of UWB Anchors (Access 2023): https://disi.unitn.it/~picco/papers/access23.pdf
- Krapež, Self-Calibrating Localization for Sport (Sensors 2022, 22:9363): https://www.mdpi.com/1424-8220/22/23/9363 · PMC: https://pmc.ncbi.nlm.nih.gov/articles/PMC9738763/
- Goudar et al., Online Spatio-temporal Calibration of UWB-Inertial (IROS 2021): https://arxiv.org/pdf/2108.00133
- Trajectory-constrained rotating-arm rig for GNSS kinematic testing (Measurement 2019): https://www.sciencedirect.com/science/article/abs/pii/S0263224119303124
- STAR-loc dataset (2023): https://arxiv.org/abs/2309.05518
- Almansa et al., Autocalibration of Mobile UWB (2020): https://arxiv.org/pdf/2004.06762
- Fast Self-calibration for Massive UWB Anchors Aided by Odometry (ION): https://www.ion.org/publications/abstract.cfm?articleID=19574
- Real-Time Initialization of Unknown Anchors for UWB-aided Navigation (2025): https://arxiv.org/html/2506.15518
- Two-Stage UWB Anchors' Self-Calibration and Trajectory Localization (IEEE 2025): https://ieeexplore.ieee.org/iel8/7361/11318834/11247815.pdf
- Large-Scale UWB Anchor Calibration + One-Shot Localization w/ Gaussian Process (2024): https://arxiv.org/pdf/2412.16880
- Robust simultaneous UWB-anchor calibration and robot localization for emergency situations (2025): https://arxiv.org/pdf/2503.22272
- Influence of UWB Anchor Placement on Localization Accuracy (Sensors 2025, 25:5115): https://doi.org/10.3390/s25165115
- Winner's/Optimizer's curse (Smith & Winkler, Mgmt Sci 2006): https://pubsonline.informs.org/doi/10.1287/mnsc.1050.0451
- EvAAL indoor-localization benchmarking framework: https://www.ncbi.nlm.nih.gov/pmc/articles/PMC5677241/
- Exploiting redundancy for UWB anomaly detection (Frontiers Robotics AI 2023): https://www.frontiersin.org/journals/robotics-and-ai/articles/10.3389/frobt.2023.1190296/full
- Prior position determination in particle filter for UWB (NAVIGATION 2021, 68(2)): https://navi.ion.org/content/68/2/277
- CT-UIO: Continuous-Time UWB-Inertial-Odometer, fewer anchors (2025): https://arxiv.org/html/2502.06287v2
- Sensor Networks TDOA Self-Calibration: 2D Complexity (2020): https://arxiv.org/pdf/2005.10298

---

*Generated as an independent read-only assessment. Items marked **must verify** require full-text reading / a confirming experiment before entering any manuscript.*
