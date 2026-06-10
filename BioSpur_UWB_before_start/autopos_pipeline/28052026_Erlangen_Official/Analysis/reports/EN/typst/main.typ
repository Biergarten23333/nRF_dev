// Auto-generated Typst conversion of ../main_EN.tex.
// Generated mechanically for layout exploration; review before treating as the source of record.

#set document(title: "Vicon Motion Capture System Ground-Truth Measurement of the AutoPos UWB Self-Localisation System", author: "Zekai Xiao")
#set page(paper: "a4", margin: 25mm)
#set text(size: 11pt, lang: "en")
#set heading(numbering: "1.1")
#set par(justify: true)
#show link: set text(fill: blue)

#align(center)[
  #text(size: 17pt, weight: "bold")[Vicon Motion Capture System Ground-Truth Measurement #linebreak() of the AutoPos UWB Self-Localisation System]
  #v(6pt)
  #text(size: 12pt)[Erlangen, 28 May 2026]
  #v(10pt)
  Zekai Xiao #linebreak()
  Report generated from `main_EN.tex`
]

#outline(title: [Contents])
#pagebreak()


= Introduction and Dataset Definition <sec:intro>


This report presents the first external ground-truth validation of the AutoPos UWB anchor self-localisation system using a Vicon Motion Capture System as a sub-millimetre ground-truth reference.
The measurement was conducted on 28 May 2026 at the Machine Learning and Data Analytics Lab (MaD Lab), Friedrich-Alexander-Universität Erlangen-Nürnberg.
The primary accuracy claim is dataset-bound and is stated as follows.

#block(inset: 8pt, stroke: 0.6pt + gray, fill: rgb("#f6f6f6"))[
*Measured static accuracy in the Erlangen Vicon test.*
Using the headline v4-io/T4/F0 pipeline, AutoPos achieves 72.7 mm median 3D error, 171.5 mm P95 error, and 109.8 mm RMSE over 24 static tag positions.
Each reported static point is one 120 s session-averaged UWB coordinate compared against the corresponding Vicon ground-truth coordinate.
The same evaluation gives 37.4 mm median horizontal error and 61.9 mm median vertical error, with P95 errors of 73.6 mm horizontally and 170.0 mm vertically.
This supports a dataset-specific claim of sub-decimetre median static localisation and centimetre-level horizontal static accuracy in this lab geometry; it is not a universal UWB specification, because anchor layout, tag placement, vertical aperture, residual calibration, and propagation conditions all affect the measured accuracy.
]

Dynamic RotoArm accuracy, UWB-internal repeatability, residual delay/range-bias coupling, and offline UWB+IMU fusion are evaluated separately in the later sections.


== System Under Test

*8 UWB anchors* (DWM1001C-Dev modules: nRF52832 SoC + DW1000 UWB transceiver) run broadcast single-sided two-way ranging (broadcast SS-TWR) firmware.
All inter-anchor ranging and anchor self-localisation are performed exclusively from UWB range measurements.

#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/experiment_setup_rotoarm.jpg", width: 100%),
    image("../fig/experiment_setup_overhead.jpg", width: 100%)
  ),
  caption: [Physical setup of the 28 May 2026 Erlangen Vicon measurement.],
) <fig:experiment_setup>


*3 ultrasound (US) height sensors* are mounted on the upper-layer anchors F, G, H.
These sensors are part of the deployed BioSpur AutoPos system and provide a vertical-gauge input after UWB-only inter-anchor geometry recovery.

#figure(image("../fig/anchor_g_us_vicon_marker.png", width: 100%), caption: [Example upper-layer anchor assembly: Anchor G with UWB anchor housing, ultrasound height-sensor assembly, and Vicon marker holder.]) <fig:anchor_g_us>


Unlike sensor-fusion-based anchor calibration methods that incorporate inertial measurements @shi2019anchor, auxiliary calibration modules @krapez2020anchor, or explicit height priors and odometry constraints @nguyen2025coordinate directly into the anchor-position estimation, AutoPos uses the ultrasound measurements *_only_* as a post-calibration gauge input after UWB-only inter-anchor geometry recovery.
The ultrasound observations do not enter the self-calibration cost function; they act as a post-calibration spatial alignment constraint.


== Dataset Overview

The dataset includes:
- 24 static UWB-Tag positions distributed across the anchor array footprint (edge, centre; low, mid, high elevations; multiple antenna facing directions).
The static set is balanced over four tag-facing groups: each facing group contains 6 positions, with 3 edge and 3 centre positions and 2 low, 2 mid, and 2 high positions.
- 17 dynamic RotoArm captures: a rigid wand with two UWB tags (BS2DCE, BSDC91) rotating continuously at fixed angular speed. Across capture sessions, the RotoArm mounting angle was adjusted so that the rotation plane varied between horizontal and vertical orientations; the two tags also followed circular trajectories with different radii.
- Vicon Motion Capture System recordings of retroreflective markers on all 8 anchors and on the tag assemblies, providing sub-millimetre 3D ground-truth reference coordinates.


== Solver Nomenclature <sec:solver_nomenclature>

Throughout this report:
- *v1-old, v2, v3-full, v3-lite, v4-io*: AutoPos anchor self-calibration solver versions.
v4-io denotes the joint robust least-squares solver that minimises inter-anchor range residuals
using iteratively reweighted least squares with Huber loss,
solved with a Trust Region Reflective (TRF) optimisation method.
See @app:layout for full definitions.
- *T1-T4*: Pure-UWB tag localisation solver variants, all based on delay-corrected range residuals
r_i^res = (||x - a_i|| + d_anchor,i + d_tag) - r_i^meas
solved via Gauss-Newton iterations with Huber robust loss (k=2.0).
The variants differ in weighting and temporal context:
*T1* uses per-anchor sigma and Huber weighting only (memory-free);
*T2* adds a range-quality penalty derived from the DW1000 diagnostic fields;
*T3* further adds residual-history (EMA) weighting and a weak temporal prior to the previous position.
*T4* (current production solver) applies adaptive branching:
in full 8-anchor frames it uses the memory-free T1 path to avoid temporal-history bias;
under anchor dropout it activates T3-like quality/residual-history weighting and the temporal prior.
All outlier handling is _soft down-weighting_, not hard NLOS link rejection.
See @app:tag for full definitions.
- *F0-F5*: External position filters applied _after_ the tag solver on the solved 3D position stream.
They do not re-process ranges, modify the anchor layout, or change delay calibration.
F0 is a passthrough (all headline results use F0);
F1-F3 are progressively more robust online Kalman filters;
F4 is a deployable fixed-lag smoother;
F5 is an offline RTS smoother that serves as a diagnostic upper bound.
See @app:filter for full definitions.
- The _headline configuration_ is *v4-io/T4/F0*: the v4-io anchor layout, current production tag solver, and unfiltered position output.
All accuracy claims in this report refer to this unfiltered pipeline unless explicitly stated otherwise.


Complete mathematical definitions of all solver components are given in @app:solvers.


= Evaluation Methodology <sec:coord>


== Ground-Truth Reference Frame

The Vicon Motion Capture System provides the primary evaluation reference.
Retro-reflective markers attached to each anchor's DWM1001C module define the anchor phase-centre truth coordinates.
The Vicon-derived 3D positions serve as ground truth throughout this report unless explicitly stated otherwise.


== Coordinate-Axis Convention

The raw Vicon and intermediate analysis files use the laboratory export convention in which the vertical coordinate appears as the raw Y axis and the horizontal plane is the raw X-Z plane.
For consistency with the BioSpur/AutoPos convention used in the report, all tables and figures relabel the axes as x/y horizontal and z vertical:
```latex
x_{\mathrm{report}} = X_{\mathrm{raw}}, \qquad
    y_{\mathrm{report}} = Z_{\mathrm{raw}}, \qquad
    z_{\mathrm{report}} = Y_{\mathrm{raw}} .
```

This is a fixed axis relabelling applied before reporting component-wise errors; it is not an additional fitted alignment and it does not change Euclidean 3D distances.
Thus entries labelled "x/y horizontal" in the report correspond to the raw X-Z horizontal-plane error, and entries labelled "z vertical" correspond to the raw vertical Y-axis error.


== Spatial Alignment for AutoPos Anchor Layout

The raw AutoPos anchor coordinates are internally self-consistent but live in an arbitrary 3D gauge: before comparison with Vicon, the whole layout may be translated, rotated, and mirrored without changing any inter-anchor UWB ranges.
Because the F/G/H ultrasound-height calibration in this dataset is treated as a deployment-gauge input rather than an independently validated Vicon reference (see @sec:us_height), the primary Vicon evaluation uses an anchor-locked rigid registration.
The rigid registration removes the arbitrary global coordinate frame while preserving the AutoPos layout shape and scale.
The mirror ambiguity inherent in range-only self-calibration is resolved by choosing the handedness with the lower anchor residual against Vicon.

The primary spatial transform is therefore
```latex
\hat{\mathbf{p}}_i = \mathbf{R}\mathbf{p}_i + \mathbf{t},
    \qquad
    \mathbf{R}^{T}\mathbf{R}=\mathbf{I},
```

with det(R) = +/- 1, choosing the handedness that minimises the anchor residual, to account for the range-only reflection ambiguity.
The fitted transform solves
```latex
\min_{\mathbf{R},\mathbf{t}} \sum_{i \in \{A,\ldots,H\}}
    \left\lVert
    \hat{\mathbf{p}}_i^{\mathrm{AutoPos}}
    -
    \mathbf{p}_i^{\mathrm{Vicon}}
    \right\rVert^2 .
```

Only the 8 anchor positions are used to estimate this spatial transform.
Tag and RotoArm trajectories are _never_ used to fit the transform; the alignment is anchor-locked.
This procedure allows the unknown room-frame origin, orientation, and handedness to be removed, but it does not allow Vicon to scale-correct the deployed AutoPos layout.

For diagnostic purposes, a Sim(3) similarity alignment is also computed by adding one global scale factor to the rigid transform.
The Sim(3) residual is reported only to separate scale-related error from shape error.
It is not used as the primary absolute-accuracy claim, because a deployed AutoPos layout cannot rely on an externally supplied Vicon scale correction.
The corresponding v4-io diagnostic values are reported in @tab:layout, @fig:layout_scale.


== Temporal Alignment for RotoArm Captures

The static tag measurements are 120-second stationary sessions: each UWB position estimate is averaged over the session and compared with the corresponding Vicon static position.
They therefore do not require frame-level timestamp alignment.

The dynamic RotoArm measurements are more demanding because the Vicon and UWB systems were not hardware time-synchronised: no shared trigger pulse, GPIO event, or common UTC clock was recorded.
For each RotoArm capture, a single relative time offset is therefore estimated in post-processing.
The UWB sample times are shifted by , the Vicon trajectory is linearly interpolated at the shifted UWB sample times, and is selected by minimising the median 3D trajectory residual over the overlapping circular-motion samples of the two wand-mounted tags.
This is a coarse-to-fine scalar search using the primary v4-io/T4 trajectory; the selected capture-level offset is then frozen and reused for all other layout, tag-solver, and IMU-replay comparisons.

This procedure uses the closest available post-processed offset for each capture, but it is still not equivalent to physical time synchronisation.
It corrects the unknown capture start-time offset, but it is not a hardware latency measurement and cannot remove residual timestamp jitter, clock drift, or ambiguity caused by the periodic circular motion.
Consequently, RotoArm absolute trajectory errors should be interpreted as UWB dynamic error plus any residual time-alignment uncertainty, and the dynamic error tail should not be attributed solely to UWB ranging or localisation effects.
The static-position results are not affected by this limitation.


== Ultrasound Height Reference <sec:us_height>

The deployed AutoPos system uses ultrasound-measured heights for anchors F, G, H.
This remains the intended deployable configuration: the layout is determined from UWB inter-anchor ranges and then assigned a physical vertical gauge using the US height inputs, not from Vicon coordinates.
However, the 28 May dataset does not support an independent US-height accuracy claim.
The H-anchor ultrasound measurement is suspected to be affected by the short 20 cm carbon-fibre extension used during the measurement, which may have caused the sensor to range to an unintended local surface or support structure.
The F and G sensors used longer 30 cm carbon-fibre extensions and show smaller height discrepancies against Vicon, whereas H shows the largest discrepancy.
For this reason, the ultrasound data are reported as a system component and deployment-gauge input, but not as a separate validated ground-truth source.
The primary numerical results in this report therefore use the anchor-locked rigid Vicon registration from @sec:coord.


== Evaluation Objects

Throughout this report we distinguish two static-position quantities.
*Absolute accuracy* is the Euclidean 3D error between an estimated position and its Vicon ground-truth position (reported as median, P95, and RMSE); it is the Vicon-referenced error and reflects systematic bias plus random error.
*Repeatability* or *precision* (denoted σ3D) is the 3D spatial spread of repeated UWB estimates at a fixed static position, computed relative to that position's own UWB cluster rather than relative to the Vicon coordinate.
It is therefore an internal UWB stability metric, not a Vicon absolute-error metric.
The two are independent: post-processing can improve repeatability without changing absolute accuracy.

For RotoArm data the tag is continuously moving, so the static repeatability definition does not apply.
The dynamic section therefore reports two different quantities: UWB trajectory error against Vicon truth, and RotoArm geometric self-consistency metrics such as radius-difference RMS and turn-centre repeatability.
The self-consistency metrics can be computed from either the Vicon trajectory or the UWB trajectory using the same circle-fitting procedure; they are useful physical-consistency checks, but they are not substitutes for Vicon-referenced dynamic accuracy.

0.4em
- *Anchor layout:* 8 anchor positions, compared primarily via anchor-locked rigid Vicon registration; Sim(3) scale fitting is diagnostic only.
- *Static tag positions:* 24 positions, each recorded as one 120-second static session (1201 frames).
Per-position accuracy is the Euclidean 3D error between the session-averaged UWB tag coordinate and the Vicon truth position.
- *RotoArm UWB-Tag trajectories:* 17 captures of continuous circular motion.
Per-sample 3D error is computed after temporal alignment (see @sec:roto).


= AutoPos Anchor Self-Localisation versus Vicon Ground Truth <sec:anchor>


This section evaluates AutoPos anchor self-localisation against the anchor coordinates measured by the Vicon system.
The input to this evaluation is the recorded inter-anchor UWB ranging data, not the static-tag or RotoArm measurements.
The anchor solver versions defined in @sec:solver_nomenclature are all evaluated with the same anchor-locked rigid registration from @sec:coord.
The detailed overlay and coordinate table focus on v4-io because that layout is used by the report's headline v4-io/T4/F0 localisation pipeline.
This is not the only layout evaluated: @tab:layout gives the Vicon residuals for all anchor-layout solvers, and the complete per-anchor aligned coordinates for all solver versions are reported in @app:anchor_solver_coordinates.


== Layout Overlay and Anchor Coordinates

#figure(image("../fig/layout_vicon_vs_autopos_3d.png", width: 100%), caption: [Visual overlay of the AutoPos v4-io layout and Vicon ground-truth anchor positions. The left panel gives the 3D overview, and the right panel serves as the top-view anchor-ID map. The quantitative rigid-registration residuals used for the headline anchor-layout result are listed explicitly in @tab:anchor_coords.]) <fig:layout_3d>


@tab:anchor_coords lists the Vicon ground-truth coordinates and the aligned v4-io AutoPos coordinates for each anchor.
The AutoPos coordinates are shown after the anchor-locked rigid registration described in @sec:coord; raw AutoPos coordinates live in an arbitrary 3D gauge and are not directly comparable before alignment.

#figure(
  block(width: 100%)[
    #text(size: 8pt)[
      #table(columns: 8, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Anchor],
      table.cell(colspan: 3)[Vicon truth \[mm\]],
      table.cell(colspan: 3)[Aligned AutoPos v4-io \[mm\]],
      table.cell(colspan: 1)[3D err. \[mm\]],
      [],
      table.cell(colspan: 1)[x],
      table.cell(colspan: 1)[y],
      table.cell(colspan: 1)[z_vert],
      table.cell(colspan: 1)[x],
      table.cell(colspan: 1)[y],
      table.cell(colspan: 1)[z_vert],
      [],
      [A],
      [-1074.6],
      [-1624.3],
      [248.3],
      [-1159.4],
      [-1775.2],
      [182.1],
      [185.4],
      [B],
      [-1320.4],
      [1029.4],
      [270.3],
      [-1334.9],
      [1081.7],
      [206.1],
      [84.1],
      [C],
      [851.9],
      [1194.3],
      [222.4],
      [823.9],
      [1282.6],
      [180.6],
      [101.7],
      [D],
      [1082.8],
      [-1432.5],
      [240.9],
      [1158.8],
      [-1451.6],
      [187.9],
      [94.7],
      [E],
      [-1170.6],
      [-1581.7],
      [1648.0],
      [-1215.1],
      [-1593.3],
      [1667.6],
      [50.0],
      [F],
      [-1271.7],
      [1099.3],
      [1660.1],
      [-1257.5],
      [1118.6],
      [1737.7],
      [81.2],
      [G],
      [962.4],
      [1186.8],
      [1624.5],
      [1041.2],
      [1239.6],
      [1667.2],
      [104.0],
      [H],
      [1028.1],
      [-1552.1],
      [1630.3],
      [1030.9],
      [-1583.2],
      [1715.6],
      [90.9],
      )
    ]
  ],
  caption: [Per-anchor Vicon truth and rigid-registered v4-io AutoPos coordinates. Coordinates are reported in the AutoPos/BioSpur convention after anchor-only rigid registration: x/y are horizontal and z is vertical.],
) <tab:anchor_coords>


The largest per-anchor error is at anchor A (185.4 mm), which lies at the layout periphery.
The median per-anchor error is 92.8 mm.
In this report convention, z_vert is the vertical coordinate.
The listed coordinate differences are residuals after the anchor-locked rigid registration; no global scale correction is fitted.
The corresponding aligned coordinates for the other AutoPos layout solvers are provided in @app:anchor_solver_coordinates.


== Alignment Results

The rigid-registered AutoPos anchor layout accuracy for all solver versions is summarised below.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 6, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Solver],
      table.cell(colspan: 3)[3D residual \[mm\]],
      table.cell(colspan: 2)[Axis RMSE \[mm\]],
      [],
      table.cell(colspan: 1)[Median],
      table.cell(colspan: 1)[P95],
      table.cell(colspan: 1)[RMSE],
      table.cell(colspan: 1)[Horizontal],
      table.cell(colspan: 1)[Vertical],
      [v1-old],
      [84.6],
      [154.1],
      [101.3],
      [93.8],
      [38.1],
      [v2],
      [127.8],
      [184.0],
      [136.5],
      [109.0],
      [82.1],
      [v3-lite],
      [127.9],
      [184.2],
      [136.6],
      [108.8],
      [82.6],
      [v3-full],
      [118.9],
      [211.7],
      [143.4],
      [127.5],
      [65.8],
      [v4-io],
      [92.8],
      [156.9],
      [105.4],
      [86.8],
      [59.8],
      )
    ]
  ],
  caption: [Anchor layout residuals against Vicon ground truth under anchor-locked rigid registration (8 anchors).],
) <tab:layout>


Under rigid registration, the v4-io solver gives 92.8 mm median error, 156.9 mm P95 error, and 105.4 mm RMSE.
The horizontal RMSE is 86.8 mm and the vertical RMSE is 59.8 mm.
The anchor-only residual is not the sole selection criterion for the end-to-end pipeline: v1-old has the lowest 3D RMSE in @tab:layout, whereas v4-io is the robust joint delay-layout solver used for the headline v4-io/T4/F0 localisation pipeline.
This distinction is why the report shows the v4-io layout in detail but reports all solver versions under the same Vicon metric.

#figure(image("../fig/layout_v4_scale_diagnostic.png", width: 82%), caption: [Pairwise scale diagnostic for the v4-io anchor layout; positive values indicate AutoPos inter-anchor distances longer than Vicon.]) <fig:layout_scale>


== Scale Bias Interpretation

The diagnostic reduction from 105.4 mm (rigid registration) to 67.1 mm (Sim(3)), together with @fig:layout_scale, indicates that a substantial fraction of the absolute layout error is a coherent scale offset rather than random shape distortion.
@fig:layout_scale makes this visible directly: 27 of 28 anchor-pair distances are longer in AutoPos than in Vicon, with a median pairwise scale error of approximately +4.1%.
This scale offset is consistent with the known coupling between layout geometry and UWB ranging delay: the DW1000 firmware hardcodes the antenna delay at 16436 DTU, and the solver's layout-level residual delay corrections (`d_anchor_mm`, `tag_delay_mm`) absorb remaining delay-geometry ambiguity into the coordinate frame.
The Sim(3) diagnostic confirms that the dominant error mode is scale-like, not random topology distortion.

This behaviour is consistent with delay-geometry coupling in UWB self-calibration.
The anchor accuracy claim remains the rigid-registered result in @tab:layout; the Sim(3) scale correction is diagnostic only.


= Delay-Layout Coupling <sec:delay>


This section presents the clearest mechanism-level result of the measurement campaign.
It demonstrates that anchor geometry and residual delay/range-bias calibration are coupled quantities that cannot be separated naively.
The term "delay correction" in this section refers to the solver's fitted residual correction terms, not to a pure hardware antenna-delay measurement.
These residual corrections can absorb fixed antenna-delay mismatch, per-device range bias, and geometry-dependent error left after the nominal DW1000 antenna-delay setting.

A four-way ablation was performed by combining two layout sources (AutoPos v4-io self-calibrated coordinates, Vicon-measured coordinates) with different residual-correction treatments.
In @tab:delay_coupling, "Vicon-measured anchor coordinates" means that the UWB tag solver is given the anchor positions measured by Vicon instead of the anchor positions estimated by AutoPos from inter-anchor UWB ranging.
It does not refer to a different UWB-anchor hardware setup.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Configuration],
      [RMSE \[mm\]],
      [Median \[mm\]],
      [P95 \[mm\]],
      [Vicon-measured coords, no residual correction],
      [311.3],
      [307.3],
      [453.4],
      [Vicon-measured coords, AutoPos residual corrections],
      [252.2],
      [254.9],
      [394.6],
      [Vicon-measured coords, re-estimated residual corrections],
      [77.7],
      [64.1],
      [128.4],
      [AutoPos v4-io coords, co-fitted residual corrections],
      [108.9],
      [69.8],
      [173.9],
      )
    ]
  ],
  caption: [Delay-layout coupling ablation evaluated on static UWB-Tag localisation. Each row localises the same n=24 static tag positions with the T4 tag solver; the reported errors are 3D errors against Vicon tag ground truth.],
) <tab:delay_coupling>


== Key Findings

- *No residual correction:* using Vicon-measured coordinates without residual correction yields 311 mm RMSE - the worst of all four configurations.
Ground-truth geometry alone is insufficient.
- *Transplanting AutoPos residual corrections* to the Vicon frame reduces the error to 252 mm, but the corrections are conditioned on the AutoPos coordinate frame and do not transfer well.
- *Re-estimating residual corrections on the Vicon frame* gives the lowest RMSE in this ablation (78 mm), confirming that delay-geometry coupling is a major error source.
- *AutoPos self-calibration* achieves 109 mm RMSE.
It remains competitive because it co-fits geometry and residual corrections in a self-consistent frame.


Prior UWB calibration work already shows that anchor coordinates and antenna-delay or range-bias parameters are accuracy-critical, and many systems estimate them jointly or with additional calibration infrastructure @shah2022node @piavanini2022self @nguyen2025coordinate.
Some work treats hardware antenna delay as a separately calibrated, largely transferable quantity @shalaby2023calibration, while robotics calibration work has shown that UWB sensor error models can be difficult to generalise across anchor setups and environments @lutz2019visual.
Prior work has already shown that UWB anchor geometry and delay/range-bias parameters are coupled and often need joint calibration.
The novelty here is not the existence of that coupling, but the explicit four-way ablation in @tab:delay_coupling showing a concrete transfer failure: in the same physical deployment, Vicon-measured anchor coordinates degrade tag localisation unless the residual corrections are re-estimated in the Vicon coordinate frame.
To our knowledge, this is the first explicit four-way ablation demonstrating that externally measured optical ground-truth anchor coordinates cannot be substituted for a self-calibrated UWB layout without re-estimating residual delay/range-bias corrections in the same coordinate frame.

This result has direct implications for UWB system design and for the interpretation of ground-truth validation: optical reference coordinates cannot simply be dropped into a UWB solver as a "better layout" without re-estimating the residual correction parameters.
The fitted residual corrections are frame-conditioned quantities.


= Static UWB-Tag Evaluation <sec:static>


Static performance is evaluated from two complementary viewpoints.
This section reports the unfiltered static baseline: *Vicon-referenced absolute accuracy*, which measures how far one session-averaged UWB tag coordinate is from the Vicon ground-truth coordinate, and *UWB-internal repeatability*, which measures the frame-to-frame spread of repeated UWB estimates during the same static sessions.
The two metrics answer different questions and should not be collapsed into one number.


== Headline Absolute-Accuracy Result <sec:static_headline>

The headline static accuracy uses the v4-io layout and T4 tag solver.
At each of the 24 static tag positions, the tag was recorded during one 120-second static session.
The reported coordinate is the arithmetic mean of all valid per-frame UWB position estimates in that session; this single session-averaged coordinate is then compared with the Vicon ground-truth coordinate.
This gives 72.7 mm median 3D error, 171.5 mm P95 error, and 109.8 mm RMSE.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Metric],
      [x/y horizontal \[mm\]],
      [z vertical \[mm\]],
      [3D \[mm\]],
      [Median error],
      [37.4],
      [61.9],
      [72.7],
      [P95 error],
      [73.6],
      [170.0],
      [171.5],
      [RMSE error],
      [47.9],
      [98.8],
      [109.8],
      )
    ]
  ],
  caption: [Static UWB-Tag localisation accuracy (v4-io/T4, one session-averaged coordinate per static position, n=24 positions).],
) <tab:static_headline>


The dominant static error component is vertical, which is consistent with the weaker vertical geometry of this deployment.
The anchor array spans approximately 2.4 m×2.8 m horizontally, but only about 1.44 m between the lower and upper anchor layers; the 24 static tag positions cover about 1.50 m vertically.
Because the vertical aperture is smaller than the horizontal footprint, the z coordinate is less strongly constrained by the range geometry.
This is reflected in the error decomposition: 61.9 mm vs. 37.4 mm median, 170.0 mm vs. 73.6 mm P95, and 98.8 mm vs. 47.9 mm RMSE for vertical versus horizontal error.


== Headline Internal-Repeatability Result <sec:static_repeatability_headline>

The unfiltered v4-io/T4 static output has a headline internal repeatability of σ3D=67.1 mm.
This value is the median, over the 24 static sessions, of the 3D spread of repeated UWB position estimates within each 120-second session.
The corresponding P95 repeatability spread is 88.0 mm.
These numbers describe static readout stability, not the offset to Vicon ground truth.
The filter study in @sec:filter tests whether post-processing improves this internal stability and whether it also changes Vicon-referenced absolute accuracy.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Metric],
      [x/y horizontal \[mm\]],
      [z vertical \[mm\]],
      [σ3D \[mm\]],
      [Median spread],
      [56.1],
      [27.4],
      [67.1],
      [P95 spread],
      [80.8],
      [50.8],
      [88.0],
      [RMS spread],
      [59.9],
      [32.5],
      [68.1],
      )
    ]
  ],
  caption: [Headline static UWB-internal repeatability (v4-io/T4, unfiltered F0, n=24 static sessions). The values are the per-session frame-to-frame spread of repeated UWB estimates and do not use Vicon ground truth.],
) <tab:static_repeatability_headline>


== Layout-Solver Comparison

@tab:solver_static shows the T4 slice of the complete static solver-combination sweep reported in @app:static_solver_matrix.
For this table, the same recorded UWB ranging logs are solved offline on the computer with the same T4 tag solver and the same session-mean estimator; only the AutoPos anchor layout is changed.
The headline static pipeline in @tab:static_headline uses the v4-io/T4 row.

#figure(
  block(width: 100%)[
    #text(size: 8pt)[
      #table(columns: 12, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [],
      table.cell(colspan: 9)[Vicon-referenced absolute accuracy \[mm\]],
      table.cell(colspan: 2)[UWB-internal repeatability \[mm\]],
      [],
      table.cell(colspan: 3)[Median error],
      table.cell(colspan: 3)[P95 error],
      table.cell(colspan: 3)[RMSE],
      [Median],
      [P95],
      [Layout solver],
      [x/y horiz.],
      [z vert.],
      [3D],
      [x/y horiz.],
      [z vert.],
      [3D],
      [x/y horiz.],
      [z vert.],
      [3D],
      [σ3D],
      [σ3D],
      [v1-old],
      [49.5],
      [119.0],
      [134.9],
      [87.0],
      [232.7],
      [233.8],
      [53.6],
      [150.3],
      [159.6],
      [68.0],
      [107.3],
      [v2],
      [44.1],
      [68.3],
      [80.9],
      [73.2],
      [159.9],
      [166.8],
      [48.7],
      [94.2],
      [106.1],
      [62.7],
      [93.2],
      [v3-lite],
      [44.3],
      [68.4],
      [81.4],
      [73.2],
      [159.6],
      [166.5],
      [48.9],
      [94.2],
      [106.1],
      [62.7],
      [93.5],
      [v3-full],
      [41.4],
      [90.4],
      [98.1],
      [105.0],
      [230.6],
      [233.8],
      [54.6],
      [121.6],
      [133.3],
      [61.5],
      [102.1],
      [v4-io],
      [37.4],
      [61.9],
      [72.7],
      [73.6],
      [170.0],
      [171.5],
      [47.9],
      [98.8],
      [109.8],
      [67.1],
      [88.0],
      )
    ]
  ],
  caption: [Static UWB-Tag localisation by AutoPos layout solver under one common offline computer-side positioning pipeline (T4, session mean, n=24). Columns left of the vertical rule are *Vicon-referenced absolute accuracy*: one session-mean UWB coordinate per static position compared with Vicon ground truth. Columns right of the vertical rule are *UWB-internal repeatability*: the frame-to-frame 3D spread σ3D within the same static sessions, without using Vicon truth.],
) <tab:solver_static>


Under this common offline computer-side session-mean estimator, v4-io gives the lowest median 3D error, while v2 and v3-lite give slightly lower RMSE.
Thus v4-io/T4 is not selected because the other combinations were untested; it is selected as the report's main pipeline after comparing the solver variants and because it is the robust joint delay-layout solver used in the rest of the analysis.
The v4-io absolute-accuracy row matches the headline result in @tab:static_headline, and its repeatability columns match the headline internal-repeatability result in @sec:static_repeatability_headline.
The repeatability columns show a separate point: the unfiltered layout-solver variants have broadly similar frame-to-frame spread, so repeatability alone does not explain the Vicon-referenced absolute-accuracy ranking.


== Spatial Error Structure

The CDFs in @fig:static_error show the full percentile structure of the unfiltered static baseline.
Panel (a) reports Vicon-referenced absolute 3D error, so the x-axis is the distance from the session-averaged UWB coordinate to the Vicon ground-truth coordinate.
Panel (b) reports UWB-internal repeatability, so the x-axis is the per-session 3D spread σ3D of repeated UWB estimates.
The headline absolute-accuracy values in @tab:static_headline are therefore not separate calculations: the median is the P50 point of panel (a), and the P95 value is the 95% point.
The remaining right tail in panel (a) is caused by a small number of large-error static positions.
The static dataset also covers four tag-facing groups; facing-stratified values are treated as exploratory because each group contains only six positions.
The grouped values are reported in @tab:app_static_facing.

#figure(image("../fig/tag_error_cdf.png", width: 96%), caption: [Unfiltered static baseline CDFs (v4-io/T4, n=24 static sessions). Panel (a) shows Vicon-referenced absolute 3D error of the session-averaged tag coordinate. Panel (b) shows UWB-internal repeatability, measured as the per-session 3D spread σ3D of repeated UWB estimates.]) <fig:static_error>


= Static Filtering <sec:filter>


This section evaluates how external static filters change the two baseline static metrics reported in @sec:static: Vicon-referenced absolute accuracy and UWB-internal repeatability.
The T4 tag output was evaluated with the external position-filter chain F0-F5.
F0 is the unfiltered output; F1-F5 are post-processing filters applied after the UWB tag solver (see @app:filter).

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 5, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Configuration],
      [Repeatability σ3D \[mm\]],
      [Median \[mm\]],
      [P95 \[mm\]],
      [RMSE \[mm\]],
      [T4 + F0 (unfiltered)],
      [67.1],
      [72.7],
      [171.5],
      [109.8],
      [T4 + F1],
      [30.1],
      [66.1],
      [177.4],
      [110.3],
      [T4 + F2],
      [30.1],
      [66.1],
      [177.4],
      [110.3],
      [T4 + F3],
      [35.5],
      [67.1],
      [176.8],
      [110.3],
      [T4 + F4],
      [21.3],
      [66.1],
      [177.4],
      [110.3],
      [T4 + F5],
      [18.6],
      [65.3],
      [178.1],
      [110.5],
      )
    ]
  ],
  caption: [Effect of external position filters on UWB-internal repeatability and Vicon-referenced static accuracy (v4-io/T4, n=24 static positions).],
) <tab:filter>


Filtering reduces the per-position repeatability spread from 67.1 mm for F0 to 18.6 mm for F5 (3.6× improvement), improving visual stability.
However, the Vicon-referenced median, P95, and RMSE remain essentially unchanged across F0-F5.
This confirms that the position filters suppress frame-to-frame jitter (repeatability) but do not remove systematic bias (absolute accuracy).
In simpler terms, filtering makes the UWB point cloud less shaky, but it does not move the point-cloud centre onto the Vicon ground-truth position.
Filtering is not calibration.

#figure(image("../fig/filtered_static_cdf_v4io_all8.png", width: 96%), caption: [Static filtering comparison for unfiltered T4+F0 and filtered T4+F5. Panel (a) shows Vicon-referenced absolute 3D error; panel (b) shows UWB-internal repeatability, measured as the per-session 3D spread σ3D.]) <fig:filter_cdf>


@fig:filter_cdf shows why the filter is useful but should not be interpreted as calibration.
The Vicon-referenced absolute-error CDFs in panel (a) almost overlap: the median changes from 72.7 mm to 65.3 mm, while P95 remains about 175 mm.
By contrast, the repeatability CDF in panel (b) shifts strongly left, reducing the median per-session spread from 67.1 mm to 18.6 mm.


= Dynamic RotoArm UWB-Tag Evaluation <sec:roto>


All accuracy metrics in this section are *absolute accuracy* (per-sample 3D error to Vicon ground truth).
Static repeatability is not reported for the RotoArm data because repeatability requires repeated samples at the same stationary tag position.
Here the tags are continuously moving, so frame-to-frame variation mainly reflects motion along the circular trajectory rather than jitter around one fixed point.


== Temporal Alignment

As defined in @sec:coord, the Vicon Motion Capture System and UWB system share _no hardware time synchronisation_.
The RotoArm evaluation therefore uses one post-processed relative time offset per capture.
The median offset is 45.5 s (range 37.7-48.7 s); this range reflects differing Vicon capture start times, not alignment instability - the median alignment score is 101.8 mm, consistent with the static positioning error floor.

The offset is estimated from one solver configuration and reused across all layout/solver combinations.
Any residual time misalignment contributes directly to the reported dynamic error.
This limits the strength of causal attribution: the dynamic error tail cannot be fully decomposed into time-alignment error versus UWB-intrinsic dynamic error (range bias, antenna orientation, body obstruction) without hardware-synchronised ground truth.


== Headline Dynamic Result

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 2, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Metric],
      [Value \[mm\]],
      [Track-level 3D error P50],
      [105.8],
      [Track-level 3D error P95],
      [231.8],
      [Sample RMSE],
      [141.3],
      [Sample-weighted 3D P50 / P95],
      [102.6 / 256.9],
      [Sample x/y horizontal P50 / P95],
      [66.1 / 179.0],
      [Sample z vertical P50 / P95],
      [61.6 / 205.9],
      [Turn-centre 3D error (median)],
      [69.1],
      )
    ]
  ],
  caption: [Dynamic RotoArm accuracy (v4-io/T4, 34 tracks from 17 captures).],
) <tab:roto_headline>


For the track-level rows, each RotoArm track is first reduced to one value: the median of its sample-wise 3D errors to Vicon.
The P50 and P95 values are then computed across the 34 track-level values.
The sample-weighted rows instead pool all time samples before computing the percentile or RMSE.


== RotoArm Geometric Self-Consistency

The RotoArm also allows a physical self-consistency check that is separate from the UWB-against-Vicon trajectory error in @tab:roto_headline.
For each tag trajectory, a circle is fitted and the radius and per-turn centre stability are evaluated.
This metric can be computed for both the Vicon trajectory and the UWB trajectory using the same procedure.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 3, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Trajectory source],
      [Radius RMS \[mm\]],
      [Turn-centre median \[mm\]],
      [Vicon truth trajectory],
      [7.1],
      [0.30],
      [UWB-only trajectory],
      [25.9],
      [13.7],
      )
    ]
  ],
  caption: [RotoArm geometric self-consistency: Vicon truth vs. UWB-only trajectory.],
) <tab:roto_self_consistency>


The Vicon row is the measurement-system reference for this geometric check.
The UWB row shows that the RotoArm shape is recovered with 25.9 mm radius-difference RMS and 13.7 mm turn-centre repeatability median, but the UWB trajectory is still much less stable than the Vicon truth trajectory.
This table should not be read as absolute positioning accuracy; the absolute dynamic trajectory error remains the Vicon-referenced result in @tab:roto_headline.


== Negative Result: Vicon Anchors Do Not Solve Dynamic Error

Even with Vicon-measured anchor coordinates and re-estimated residual corrections, the dynamic RotoArm error remains approximately 106 mm median and 200 mm P95.
This is a key negative finding: the 100 mm-200 mm dynamic error tail is _not explained by anchor geometry alone_.

More plausible causes include:
dynamic range bias (Doppler-like effects on the UWB pulse),
antenna radiation pattern and orientation changes during rotation,
body/wand obstruction creating transient NLOS conditions,
multipath from the rotating assembly,
and solver assumptions that do not account for motion state.


== Error Dependence on Angular Speed and Phase

#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/roto_error_by_angular_speed.png", width: 100%),
    image("../fig/roto_error_by_phase.png", width: 100%)
  ),
  caption: [Dynamic error dependence on motion parameters (v4-io/T4).],
) <fig:roto_diagnostics>


== Solver Matrix

#figure(image("../fig/roto_solver_matrix_median3d.png", width: 75%), caption: [Solver matrix: track-median 3D error for all layout×tag-solver combinations (RotoArm).]) <fig:roto_solver_matrix>


@fig:roto_solver_matrix shows that v4-io consistently achieves the lowest dynamic error across tag solvers, but the improvement over v2/v3-lite is modest (5 mm).
The dynamic error floor is therefore not explained by layout quality alone.


= Monte Carlo Keep-kk Robustness and Anchor Dropout <sec:mc>


To assess system robustness against anchor failure, a Monte Carlo anchor-dropout study was performed: for each k in {4, 5, 6, 7, 8}, 5,000 random subsets of k anchors from the 8-anchor layout were drawn, and the tag positioning repeated.


== Degradation Curves

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 3, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [k],
      [Static σ3D median \[mm\]],
      [Roto turn-centre RMS median \[mm\]],
      [8],
      [61.7],
      [12.1],
      [7],
      [83.2],
      [21.6],
      [6],
      [107.3],
      [30.5],
      [5],
      [149.1],
      [41.4],
      [4],
      [196.4],
      [63.9],
      )
    ]
  ],
  caption: [Monte Carlo keep-k degradation (v4-io/T4).],
) <tab:keepk>


#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/mc_keepk_static_curves.png", width: 100%),
    image("../fig/mc_keepk_roto_curves.png", width: 100%)
  ),
  caption: [Monte Carlo keep-k degradation curves (v4-io/T4).],
) <fig:keepk>


== Stratified Keep-kk: Vertical Coverage Matters

A stratified analysis separates anchor dropout into "lower-heavy drop" (preferentially removing lower-row anchors A-D) and "upper-heavy drop" (preferentially removing upper-row anchors E-H):

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 3, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [k],
      [Lower-heavy drop \[mm\]],
      [Upper-heavy drop \[mm\]],
      [5],
      [96.0],
      [65.1],
      [4],
      [117.3],
      [57.2],
      )
    ]
  ],
  caption: [Stratified keep-k static repeatability (v4-io/T4, σ3D median).],
) <tab:stratified>


#figure(image("../fig/stratified_keepk_upper_vs_lower.png", width: 70%), caption: [Stratified keep-k: dropping upper-row anchors degrades accuracy far less than dropping lower-row anchors.]) <fig:stratified>


Vertical/spatial coverage matters at least as much as raw anchor count.
In this layout, losing lower-row anchors is more damaging than losing the same number of upper-row anchors, because it leaves a weaker surviving geometry for the tag locations used in the Erlangen static and RotoArm tests.
This has direct deployment implications: maintaining vertical anchor spread is critical.


= Wall/NLOS Simulation <sec:wall>


To contextualise the propagation environment's role in UWB accuracy, a Monte Carlo ray-tracing simulation was conducted.
This simulation models the effect of nearby walls and metallic objects on UWB ranging and self-calibration accuracy.
It is presented as discussion evidence, not as direct validation.


== Simulation Setup

The simulation sweeps anchor-to-wall distance from 0 cm to 300 cm for a 4-wall room enclosure.
Three material phases are compared:
- Phase 1: default heavy reflective wall model.
- Phase 2: Phase 1 plus photo-inspired random metal/equipment boxes near the layout boundary.
- Phase 3: wall material sensitivity (drywall, gypsum, sand-lime brick, reinforced concrete).

Each configuration is repeated with 12 random seeds for statistical stability.


== Results

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Wall distance],
      [Clear LOS \[m\]],
      [Default wall \[m\]],
      [Wall + metal \[m\]],
      [0 cm],
      [0.062],
      [0.764],
      [0.892],
      [40 cm],
      [0.062],
      [0.287],
      [0.405],
      [100 cm],
      [0.062],
      [0.088],
      [0.123],
      )
    ]
  ],
  caption: [Wall/NLOS simulation P95 positioning error at selected wall distances (4-wall enclosure).],
) <tab:wall>


#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/wall_nlos_p95_comparison.png", width: 100%),
    image("../fig/wall_nlos_convergence.png", width: 100%)
  ),
  caption: [Wall/NLOS simulation results.],
) <fig:wall>


The P95 converges to within 25% of the clear-LOS baseline at approximately 115 cm for the default wall model and 145 cm for wall-plus-metal.
This supports the physical expectation that walls, metal, and nearby obstruction can strongly inflate the error tail, and that maintaining 1 m clearance from reflective surfaces is important for stable UWB performance.

The Erlangen measurement was conducted in a large open lab space with the nearest wall at approximately 2 m from the closest anchor.
The simulation predicts that at this distance, wall-induced error should be small relative to the measured 100 mm-class UWB error, consistent with the observed clear-LOS CIR profiles (@sec:cir).


= UWB + Synthetic IMU Fusion <sec:imu>


*Important caveat:* The results are based on _offline replay/simulation_ of the Erlangen UWB data with synthetic IMU fusion, not a closed-loop embedded measurement.
They are presented as evidence of dynamic-extension capability, not as direct Vicon-validated IMU results.


== Simulation Design

Recorded UWB range data from the Erlangen RotoArm captures is replayed through a position-level fusion pipeline that integrates UWB positions with simulated IMU sensor models at three quality tiers:
- *L2* (MPU6050/JY61P-like): consumer-grade 6-axis IMU.
- *L16* (ICM-45686): automotive/industrial-grade 6-axis IMU.
- *L20* (Xsens MTi-3): high-grade MEMS IMU.

Each configuration is swept over a grid of fusion parameters (process noise, measurement noise, innovation gating) with 5 random seeds, evaluated against Vicon truth.


== Matched Fusion-Family Results

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 5, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Sensor],
      [Config ID],
      [P50 \[mm\]],
      [P95 \[mm\]],
      [RMSE \[mm\]],
      [L20 (Xsens MTi-3)],
      [X_A0_U4_P4_L20_I5_T2],
      [68.4],
      [112.1],
      [75.7],
      [L16 (ICM-45686)],
      [X_A0_U4_P4_L16_I5_T2],
      [69.0],
      [114.9],
      [76.1],
      [L2 (MPU6050-like)],
      [X_A0_U4_P4_L2_I5_T2],
      [83.9],
      [146.2],
      [94.2],
      [Same-P pure UWB],
      [A0/U4/P4],
      [78.2],
      [138.9],
      [86.2],
      [Pure UWB (B0/P0)],
      [v4-io/T4],
      [105.8],
      [231.8],
      [132.8],
      )
    ]
  ],
  caption: [Phase 4 UWB+IMU fusion: matched fusion-family comparison across sensor tiers.],
) <tab:imu>


The L20 configuration improves P95 by 26.8 mm relative to the matched A0/U4/P4 pure-UWB baseline.
Relative to the B0/P0 UWB baseline, the P95 improvement is 119.7 mm.
The L2 row shown in @tab:imu is the matched I5/T2 fusion-family comparison; the best L2 row in the wider sweep is X_A0_U4_P4_L2_I5_T4, with P50/P95 of 75.0 mm/129.5 mm.
Thus, even a consumer-grade IMU can reduce tail error, while L16/L20 remain the strongest matched-family choices.


== Spiky Track Case Study

Track R01 (tag BS2DCE) exhibits severe UWB-only spikes.
@tab:spiky shows the dramatic improvement from IMU fusion on this worst-case track.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Config],
      [3D P95 \[mm\]],
      [Vertical P95 \[mm\]],
      [XZ jumps >200 mm],
      [Pure UWB (B0)],
      [595.5],
      [545.1],
      [178],
      [L2 fusion],
      [188.4],
      [175.4],
      [0],
      [L16 fusion],
      [185.1],
      [175.1],
      [0],
      [L20 fusion],
      [169.4],
      [159.3],
      [0],
      )
    ]
  ],
  caption: [Spiky track R01/BS2DCE: pure UWB vs. IMU fusion.],
) <tab:spiky>


#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/phase4_spiky_track_xz_l2_l16_l20.png", width: 100%),
    image("../fig/phase4_spiky_track_err3d_l2_l16_l20.png", width: 100%)
  ),
  caption: [Spiky track R01/BS2DCE: pure UWB (grey) vs. L2/L16/L20 IMU fusion.],
) <fig:spiky>


All three IMU tiers completely eliminate the >200 mm jump events.
Even the consumer-grade L2 sensor reduces the 3D P95 from 595 mm to 188 mm.


== Sensor Tier Comparison

#figure(image("../fig/phase4_l2_l16_l20_sensor_comparison.png", width: 75%), caption: [Sensor tier comparison: L2 vs. L16 vs. L20 matched fusion-family performance.]) <fig:sensor_comparison>


L16 and L20 perform nearly identically on headline metrics; the L2 sensor shows a meaningful penalty (15 mm worse median, 30 mm worse P95).
This suggests that an automotive-grade IMU is sufficient for the fusion benefit; upgrading to Xsens-class hardware provides diminishing returns.


= CIR Evidence <sec:cir>


Channel Impulse Response (CIR) data was collected to characterise the propagation environment and support the interpretation of link-quality variation.
This section presents CIR as propagation evidence, not as a CIR-assisted localisation result.


== Full CIR Sweep

A full-CIR sweep (CIRRAW_AUTOPOS_SWEEP10) captured 958 complete CIR frames across all 8 masters and 10 sweep lines (85.5% coverage).
Each CIR frame contains 1016 complex samples (4064 bytes accumulator).

#figure(
  grid(columns: 2, gutter: 10pt,
    image("../fig/cir_full_frame_count_heatmap.png", width: 100%),
    image("../fig/cir_full_tail_ratio_heatmap.png", width: 100%)
  ),
  caption: [Full CIR sweep: link coverage and tail energy.],
) <fig:cir_heatmaps>


== Multipath Evidence

Several links exhibit elevated tail/main energy ratios (0.2), indicating significant multipath energy even under visually clear LOS conditions:
CG, GC, FG, EH, GF, HE.

#figure(image("../fig/cir_full_receiver_envelope_overview.png", width: 85%), caption: [CIR receiver envelope overview: clear LOS does not imply ideal single-path UWB propagation.]) <fig:cir_envelopes>


== 8-Hour Baseline Stability

An 8-hour continuous CIR recording between anchors F and H (31,514 frames) shows stable tail/main ratios:
- F: median 0.137, P95 0.215.
- H: median 0.132, P95 0.186.
- Peak index median: 750, IQR 4.

The stable CIR over 8 hours confirms that the multipath environment is static and deterministic, not transient.


== CIR-Weighted Layout Smoke Test

A preliminary CIR-weighted solver produced a baseline edge RMS of 79.4 mm vs. 82.5 mm for the CIR-weighted variant - no improvement.
This is expected: the CIR weighting interface is functional, but the current weighting heuristic does not yet exploit the CIR information effectively.
The CIR evidence is retained as a foundation for future link-quality-informed solvers.


= ML-Based Layout-Risk Analysis <sec:ml>


The separate AutoPos ML analysis is used here only as supporting deployment-risk analysis.
It does not override the Vicon-ground-truth results above: the current ML table contains 117 candidate rows, but only 5 real Vicon-labeled layouts and 0 train-allowed supervised rows.
Therefore, the ML outputs are useful for ranking hypotheses, DOP/risk screening, and future data-collection planning, not for a supervised generalisation claim.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 2, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Quantity],
      [Value],
      [Candidate layout rows],
      [117],
      [Real Vicon-labeled layouts],
      [5],
      [Train-allowed supervised rows],
      [0],
      [Deployment classes A/B/C/D],
      [8 / 20 / 55 / 34],
      [Erlangen Vicon test best drop4 class],
      [C],
      [Erlangen Vicon test worst surviving set],
      [ABGH after dropping CDEF],
      [Erlangen Vicon test drop4 score / ratio],
      [16.22 / 17.73x],
      )
    ]
  ],
  caption: [ML/layout-risk analysis summary.],
) <tab:ml>


The main deployment lesson is consistent with the Monte Carlo keep-k study: full 8-anchor performance is not sufficient to characterise robustness.
The ML/DOP analysis shows that the weakest cases are dominated by surviving-4 geometry, especially height-axis observability.
For the Erlangen Vicon test, the full 8-anchor Vicon result characterises nominal-layout accuracy, whereas the drop4 class-C result highlights reduced robustness under severe anchor loss.
High-reliability deployment should therefore monitor the active anchor set and reduce confidence when the surviving anchors collapse into weak sets such as ABGH, CDEF, ADFG, or BCEH-like patterns.


= Limitations and Future Work <sec:limitations>


== Measurement Limitations

- *No hardware time synchronisation.*
Vicon and UWB lack a shared clock.
The per-capture time offset is estimated post hoc, and residual misalignment may inflate the reported dynamic error.
Future measurements should use GPIO-pulse or event-based hardware synchronisation.

- *Single measurement site.*
All data comes from one lab environment in Erlangen.
The propagation conditions (large open space, 2 m to nearest wall, minimal metal clutter) are favourable.
Performance in cluttered, NLOS-rich, or multi-room environments may differ substantially.

- *Static tag positions are discrete.*
The 24 tag positions provide spatial coverage but not continuous spatial sampling.
Error interpolation between positions is approximate.

- *RotoArm is a constrained dynamic scenario.*
Continuous circular motion at fixed radius does not represent general 3D human motion.
The BioSpur full-body tracking application will require validation with unconstrained movement trajectories.

- *Phase 4 IMU results are replay/simulation.*
The IMU fusion evidence is based on offline replay with synthetic sensor models, not closed-loop embedded operation.
Hardware-integrated IMU validation is required before claiming real-time fusion performance.


== Identified Error Sources

- *Delay-layout coupling.*
The +4.36% pairwise scale bias (equivalently, a Sim(3) fitted scale of 0.9583) and the residual-correction ablation results confirm that residual delay/range-bias calibration and layout geometry are coupled.
Further WHY-analysis (per-tag antenna delay decomposition, two-way tag×anchor median-polish) is ongoing.

- *Distance-dependent error.*
The 167 mm/m error slope indicates that GDOP and range-bias accumulation at the array periphery are significant contributors.

- *Dynamic error floor at 100 mm.*
Neither layout improvement nor delay re-calibration reduces the dynamic median below 100 mm, pointing to UWB-intrinsic dynamic factors.

- *Multipath in clear LOS.*
CIR evidence shows that visually clear LOS does not guarantee single-path UWB propagation.
Tail energy ratios of 0.2 on several links suggest floor/ceiling reflections contribute to range bias.

- *Dropout geometry risk.*
The ML/DOP analysis shows that some surviving-4 anchor subsets have poor height-axis observability.
Runtime confidence should depend on the active anchor set, not only on the number of available anchors.


== Next Steps

- Complete WHY\#8-\#9 analysis: per-tag antenna delay decomposition and within-scenario anchor consistency checks.
- OTA firmware update of remaining tags (BS2DCE/BSDC91) to b57 for runtime layout calibration via APOS BLE push.
- Hardware-synchronised Vicon-UWB validation for unambiguous dynamic error attribution.
- Closed-loop embedded IMU fusion measurement with physical IMU sensors, not replay or simulation.
- Expand real Vicon-labeled layouts before supervised ML training; the current ML table is schema-ready but not training-ready.
- Ecological validation: multi-room, NLOS-rich environment with N 10 participants for BioSpur full-body tracking.


= Conclusion <sec:conclusion>


This report presents the first Vicon Motion Capture System ground-truth validation of the AutoPos UWB self-localisation system.
The key results are:

- *Anchor layout accuracy:*
Under anchor-locked rigid registration against Vicon, the v4-io solver recovers anchor geometry with 92.8 mm median error and 105.4 mm RMSE.
The Sim(3) diagnostic reduces the residual to 67.1 mm, showing that part of the residual is a coherent scale bias arising from delay-geometry coupling.

- *Delay-layout coupling:*
Using Vicon-measured coordinates _without_ re-estimating residual corrections produces 311 mm static tag RMSE - worse than the AutoPos v4-io layout with co-fitted residual corrections (109 mm static tag RMSE).
This demonstrates that layout and residual correction terms are coupled quantities that must be co-fitted.

- *Static tag localisation:*
The headline v4-io/T4/F0 pipeline achieves 72.7 mm median, 171.5 mm P95, and 109.8 mm RMSE across 24 positions.

- *Dynamic tag localisation:*
RotoArm evaluation yields 106 mm median and 232 mm P95.
This error is not removed by using Vicon-measured anchor coordinates, indicating that the dynamic error floor is not explained by anchor layout alone.

- *IMU fusion potential:*
Offline replay with simulated IMU models reduces Phase 4 track-median P95 from 138.9 mm for the same-P UWB baseline to 112.1 mm with L20.
Relative to the B0/P0 UWB baseline, the improvement is 119.7 mm; on the spiky R01/BS2DCE track, all three tested IMU tiers eliminate >200 mm jump events.

- *Propagation environment:*
CIR analysis confirms that clear LOS does not imply ideal single-path UWB, and wall/NLOS simulation quantifies the safe clearance distance (1 m).

- *Deployment-risk screening:*
ML/DOP analysis is not yet training-ready, but it identifies surviving-4 height-axis risk as an operational concern for high-reliability deployments.


In this Vicon dataset, the headline pipeline supports sub-decimetre median static localisation and approximately decimetre-class static RMSE.
In this dataset, the ultrasound height sensors are retained as a system component but are not used as an independent accuracy claim because the H-anchor height measurement is suspected to be affected by its short carbon-fibre extension.
The delay-layout coupling insight - that ground-truth geometry is insufficient without matched residual delay/range-bias calibration - is a finding with implications beyond this specific system.


Acknowledgements


The author thanks Prof. Dr. Björn Eskofier for providing access to the Vicon Motion Capture System at the Machine Learning and Data Analytics Lab (MaD Lab), FAU Erlangen-Nürnberg, and for his continued supervision and guidance throughout this PhD project.
The author also thanks Sophie for coordinating the measurement sessions and for her support with the Vicon system operation during the data collection on 28 May 2026.

#bibliography("../references.bib", style: "ieee")


#pagebreak()
#counter(heading).update(0)
#set heading(numbering: "A.1")


= Solver Component Definitions <app:solvers>


This appendix provides the complete mathematical definitions of the three solver layers referenced throughout this report:
the anchor layout solver (V-series), the per-frame tag position solver (T-series), and the external position filter (F-series).
The production pipeline is *v4-io/T4/F0*.


== Anchor Layout Solvers (V1-V4-io) <app:layout>


All layout solvers take directed inter-anchor UWB sweep ranges as input and output 3D anchor coordinates for anchors A-H.
Some versions additionally estimate per-anchor residual delay corrections `d_i`.

Gauge convention.
All layouts are computed in a relative coordinate frame with three gauge constraints:
anchor A at the origin, B on the positive x-axis, C in the xy-plane.
This removes the six rigid-body degrees of freedom (three translations, three rotations) inherent in distance-only observations.
The gauge does _not_ imply that A coincides with any external reference origin.

Pair range fusion.
Before layout estimation, directed range measurements `r_i->j` and `r_j->i` are fused into a single symmetric distance estimate `d_ij` per anchor pair.
Three fusion methods are used:
```latex
\text{V1 (mean):} \quad & \hat{d}_{ij} = \operatorname{mean}(r_{i \to j} \cup r_{j \to i}) \\[3pt]
  \text{V2 (IVW):} \quad & \hat{d}_{ij} = \frac{\sigma_{ji}^2 \bar{r}_{ij} + \sigma_{ij}^2 \bar{r}_{ji}}{\sigma_{ij}^2 + \sigma_{ji}^2} \\[3pt]
  \text{V3 (MAD/MVUE):} \quad & \hat{d}_{ij} = \frac{\hat{\sigma}_{ji}^2 \tilde{r}_{ij} + \hat{\sigma}_{ij}^2 \tilde{r}_{ji}}{\hat{\sigma}_{ij}^2 + \hat{\sigma}_{ji}^2}
```

Here the symbols denote, respectively, the sample mean, sample median, sample variance, and the 1.4826 MAD robust scale estimate.

V1-old: Classical MDS.
Embeds the pair distance matrix into 3D via eigendecomposition of the double-centred squared-distance matrix.
No nonlinear refinement and no delay estimation are used.

V2: MDS + regularised NLS.
MDS initialisation followed by nonlinear least-squares refinement of `sum_(i,j) (||x_i - x_j|| - d_ij)^2`
with a weak vertical-spread regulariser, annealed over three passes.
No delay estimation (`d_i = 0`).

V3-lite: Robust fusion, no delay.
Uses V3 MAD/MVUE pair fusion with MDS + NLS refinement.
Identical solver structure to V2 but with the robust pair distances.
No delay estimation (`d_i = 0`).

V3-full: Tukey IRLS with alternating delay.
Alternates between:
- *Coordinate update*: Tukey bisquare reweighted least squares on
`r_ij = ||x_i - x_j|| + d_i + d_j - d_ij`,
with Tukey threshold c = 4.685 and a MAD-based residual scale.
- *Delay update*: For each anchor i > 0, the delay is updated from the median residual over links connected to anchor i.

Iterated until convergence (Δx < 0.1 mm and Δd < 0.05 mm) or 50 iterations. Anchor A is the delay reference (`d_A = 0`).

V4-io: Joint Huber bounded-delay (production solver).
Jointly estimates coordinates and delays by minimising:
```latex
\min_{\mathbf{x},\,\mathbf{d}} \sum_{(i,j)} \rho_H\!\left(\frac{\lVert\mathbf{x}_i - \mathbf{x}_j\rVert + d_i + d_j - \hat{d}_{ij}}{15}\right)
  + \sum_{i>0} \left(\frac{d_i}{20}\right)^{\!2}
  + \Phi_{\mathrm{prior}}(\mathbf{x})
``` <eq:v4io>

where `rho_H` is the Huber loss with `f_scale = 2.0`, delay is bounded to `|d_i| <= 60 mm`, and `Phi_prior` is a soft two-layer vertical prior:
- Anchor D vertical deviation from the lower-layer (A/B/C) median: 180 mm.
- E/F/G/H vertical deviation from the upper-layer median: 220 mm.
- Layer gap bounded to [450, 2600] mm.

Solved via Trust Region Reflective (TRF) in `scipy.optimize.least_squares`, initialised from V3-lite MDS+NLS.
Anchor A is the delay reference (`d_A = 0`). Denominators (15, 20 mm) set the relative weighting between range residuals and delay regularisation.


== Tag Position Solvers (T1-T4) <app:tag>


All tag solvers are pure-UWB, single-frame, delay-corrected trilateration solvers.
Given anchor coordinates `a_i`, ranges `r_i`, per-anchor residual delay `d_anchor,i`, and common tag delay `d_tag`, each solver estimates the tag position x by iterative Gauss-Newton on the residual vector:
```latex
e_i = \lVert\mathbf{x} - \mathbf{a}_i\rVert + d_{\mathrm{anchor},i} + d_{\mathrm{tag}} - r_i
``` <eq:tag_residual>


The effective per-range sigma is:
```latex
\sigma_{\mathrm{eff},i} = \max\!\bigl(\sigma_{\mathrm{anchor},i},\; 5\bigr) \;\cdot\; \alpha_{\mathrm{quality},i} \;\cdot\; \alpha_{\mathrm{residual},i}
``` <eq:sigma_eff>


Quality penalty `alpha_quality` (T2+).
```latex
\alpha_{\mathrm{quality},i} = \operatorname{clamp}\!\left(1 + 1.5 \left(\frac{100 - \bar{q}_i}{50}\right)^{\!2},\; 1,\; 4\right)
```

where `q_i` is the average of instantaneous and EMA-smoothed DW1000 quality fields.
T1 sets `alpha_quality = 1`.

Residual-history penalty `alpha_residual` (T3+).
```latex
\alpha_{\mathrm{residual},i} = \operatorname{clamp}\!\left(1 + 0.5 \cdot \frac{\max(0,\; \mathrm{EMA}_i - 120)}{80},\; 1,\; 2.5\right)
```

where `EMA_i` is the exponentially weighted moving average of `|e_i|` (decay = 0.3).
T1 and T2 set `alpha_residual = 1`.

Robust weighting.
The normalised residual `tilde e_i = e_i / sigma_eff,i` is reweighted with Huber loss (k = 2.0):
```latex
w_i = \begin{cases} 1 & \text{if } |\tilde{e}_i| \leq 2 \\ 2 / |\tilde{e}_i| & \text{otherwise} \end{cases}
```


Temporal prior (T3+).
When a previous position `x_prev` is available, the normal equations include:
```latex
\mathbf{H} \mathrel{+}= \sigma_{\mathrm{prior}}^{-2}\,\mathbf{I}_3, \qquad
  \mathbf{g} \mathrel{+}= \sigma_{\mathrm{prior}}^{-1}\,(\mathbf{x} - \mathbf{x}_{\mathrm{prev}}) / \sigma_{\mathrm{prior}}
```

with `sigma_prior = 180 mm`. T1 and T2 do not use a temporal prior.

T4 adaptive branching.
T4 applies context-dependent method selection per frame:
```latex
\text{T4}(n) = \begin{cases}
    \text{T1 (memory-free)} & \text{if } n \geq 8 \\
    \text{T3 (quality + residual EMA + prior)} & \text{if } n < 8
  \end{cases}
```

In full 8-anchor frames, T4 avoids temporal-history bias by using the stateless T1 path.
Under anchor dropout (n < 8), T4 activates quality weighting, residual-history weighting, and the temporal prior.

Solver parameters.
All variants share: max_iters = 8, max_step = 500 mm, convergence = 0.02 mm.
All outlier handling is *soft down-weighting*; the C implementation does not execute a hard anchor-rejection loop.

Method summary.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 5, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Feature],
      [T1],
      [T2],
      [T3],
      [T4],
      [Per-anchor + Huber WLS],
      [],
      [],
      [],
      [],
      [Range quality penalty q],
      [],
      [],
      [],
      [active],
      [Residual EMA penalty r],
      [],
      [],
      [],
      [active],
      [Temporal prior],
      [],
      [],
      [],
      [active],
      [Memory-free for n >= 8],
      [],
      [],
      [],
      [],
      table.cell(colspan: 5)[Active only when n < 8 (anchor dropout).],
      )
    ]
  ],
  caption: [Tag solver variant feature matrix.],
) <tab:tag_methods_app>


== External Position Filters (F0-F5) <app:filter>


These filters operate on the solved 3D position stream `z_k = [x_k, y_k, z_k]^T` output by the tag solver.
They do not re-process UWB ranges, modify the anchor layout, or change delay calibration.

State and motion model (F1-F5).
All Kalman-based filters use a 6-state constant-velocity model:
```latex
\mathbf{x} = [p_x,\, p_y,\, p_z,\, v_x,\, v_y,\, v_z]^\top, \qquad
  \mathbf{F} = \begin{bmatrix} \mathbf{I}_3 & \Delta t\,\mathbf{I}_3 \\ \mathbf{0} & \mathbf{I}_3 \end{bmatrix}
```

Process noise Q is parameterised by an acceleration standard deviation `sigma_a`:
```latex
\mathbf{Q}_{pp} = \tfrac{1}{4}\Delta t^4 \sigma_a^2 \mathbf{I}_3, \quad
  \mathbf{Q}_{pv} = \tfrac{1}{2}\Delta t^3 \sigma_a^2 \mathbf{I}_3, \quad
  \mathbf{Q}_{vv} = \Delta t^2 \sigma_a^2 \mathbf{I}_3
```


F0: Passthrough.
`p_k = z_k`. No filtering. All headline results use F0.

F1: Online constant-velocity Kalman filter.
Standard predict-update cycle with fixed `sigma_a` and measurement noise R derived from the tag solver's residual RMS.

F2: Robust constant-velocity Kalman filter.
Identical to F1 but with innovation gating: if the normalised innovation squared
`NIS > 25`,
the measurement covariance is inflated R R (30, NIS/9).
This soft-rejects jump outliers without discarding the measurement entirely.

F3: Adaptive robust Kalman filter.
As F2, with speed-adaptive process noise:
```latex
\sigma_a = \begin{cases}
    \sigma_a^{\mathrm{high}} & \text{if } \lVert\mathbf{z}_k - \mathbf{z}_{k-1}\rVert / \Delta t > v_{\mathrm{threshold}} \\
    \sigma_a^{\mathrm{low}}  & \text{otherwise}
  \end{cases}
```

Static context: `sigma_a_low = 300`, `sigma_a_high = 2000`, `v_threshold = 500 mm/s`.
Dynamic (ROTO) context: `sigma_a_low = 900`, `sigma_a_high = 3200`, `v_threshold = 900 mm/s`.

F4.
The fixed-lag smoother applies a bounded-window weighted average after F2/F3:
```latex
\hat{\mathbf{p}}_k = \frac{\sum_{j=k-L}^{k+L} w_j \,\hat{\mathbf{p}}_j^{\mathrm{fwd}}}{\sum_{j=k-L}^{k+L} w_j}, \qquad
  w_j = \frac{1}{1 + |j - k|}
```

with lag L = 5 (ROTO) or L = 8 (static). Deployable with a known output latency of L / f_s seconds.

F5: Rauch-Tung-Striebel (RTS) offline smoother.
Full-sequence forward Kalman filtering followed by a backward smoothing pass:
```latex
\hat{\mathbf{x}}_k^s = \hat{\mathbf{x}}_k^f + \mathbf{C}_k \bigl(\hat{\mathbf{x}}_{k+1}^s - \hat{\mathbf{x}}_{k+1}^{-}\bigr), \qquad
  \mathbf{C}_k = \mathbf{P}_k^f \mathbf{F}_{k+1}^\top (\mathbf{P}_{k+1}^{-})^{-1}
```

where superscripts f, s, - denote filtered, smoothed, and predicted estimates.
F5 uses future samples and _cannot_ be deployed in real time.
It serves as a diagnostic upper bound on achievable accuracy for a given tag solver.


= Complete Solver-Combination Results <app:complete_results>


This appendix contains the complete solver-result tables that support the main-text configuration choices.
The main text follows v4-io/T4/F0 as the headline pipeline, but the other anchor-layout and tag-solver combinations were also evaluated.


== Aligned Anchor Coordinates for All Layout Solvers <app:anchor_solver_coordinates>

@tab:app_anchor_coords_all reports the aligned AutoPos coordinates for all anchor-layout solver versions under the same anchor-locked rigid registration used in @sec:anchor.
The Vicon truth coordinates are listed in @tab:anchor_coords; the table below lists each solver's aligned AutoPos coordinate and the resulting per-anchor 3D residual.
No Sim(3) scale correction is fitted.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 6, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Layout solver],
      [Anchor],
      [x \[mm\]],
      [y \[mm\]],
      [z_vert \[mm\]],
      [3D err. \[mm\]],
      [8*v1-old],
      [A],
      [-1160.2],
      [-1763.8],
      [181.7],
      [176.8],
      [],
      [B],
      [-1358.5],
      [1097.8],
      [273.6],
      [78.4],
      [],
      [C],
      [873.1],
      [1298.3],
      [186.8],
      [112.0],
      [],
      [D],
      [1142.7],
      [-1484.3],
      [233.7],
      [79.5],
      [],
      [E],
      [-1214.1],
      [-1626.8],
      [1645.9],
      [62.7],
      [],
      [F],
      [-1270.1],
      [1127.4],
      [1697.7],
      [47.0],
      [],
      [G],
      [1021.4],
      [1277.0],
      [1628.4],
      [107.8],
      [],
      [H],
      [1053.6],
      [-1606.2],
      [1697.0],
      [89.6],
      [8*v2],
      [A],
      [-1174.1],
      [-1771.3],
      [168.8],
      [194.6],
      [],
      [B],
      [-1391.7],
      [1103.9],
      [209.2],
      [119.9],
      [],
      [C],
      [906.2],
      [1320.8],
      [132.7],
      [164.3],
      [],
      [D],
      [1171.5],
      [-1498.6],
      [163.4],
      [135.1],
      [],
      [E],
      [-1225.8],
      [-1630.5],
      [1672.4],
      [77.6],
      [],
      [F],
      [-1278.8],
      [1131.1],
      [1760.7],
      [105.8],
      [],
      [G],
      [1024.9],
      [1272.5],
      [1681.7],
      [120.5],
      [],
      [H],
      [1055.8],
      [-1608.8],
      [1755.9],
      [140.5],
      [8*v3-lite],
      [A],
      [-1173.9],
      [-1771.2],
      [168.1],
      [194.7],
      [],
      [B],
      [-1391.3],
      [1103.8],
      [209.0],
      [119.7],
      [],
      [C],
      [905.4],
      [1320.5],
      [131.1],
      [164.7],
      [],
      [D],
      [1171.6],
      [-1498.1],
      [163.9],
      [134.6],
      [],
      [E],
      [-1226.1],
      [-1629.8],
      [1672.3],
      [77.4],
      [],
      [F],
      [-1278.3],
      [1130.0],
      [1761.7],
      [106.4],
      [],
      [G],
      [1025.2],
      [1272.9],
      [1682.4],
      [121.3],
      [],
      [H],
      [1055.2],
      [-1608.7],
      [1756.2],
      [140.7],
      [8*v3-full],
      [A],
      [-1195.5],
      [-1750.3],
      [178.0],
      [188.3],
      [],
      [B],
      [-1210.4],
      [1093.9],
      [207.9],
      [142.0],
      [],
      [C],
      [718.0],
      [1323.6],
      [208.1],
      [186.7],
      [],
      [D],
      [1222.7],
      [-1586.9],
      [323.9],
      [224.3],
      [],
      [E],
      [-1189.3],
      [-1568.5],
      [1691.1],
      [48.8],
      [],
      [F],
      [-1297.9],
      [1136.0],
      [1744.6],
      [95.8],
      [],
      [G],
      [996.7],
      [1219.9],
      [1654.7],
      [56.4],
      [],
      [H],
      [1043.5],
      [-1548.4],
      [1536.5],
      [95.2],
      [8*v4-io],
      [A],
      [-1159.4],
      [-1775.2],
      [182.1],
      [185.4],
      [],
      [B],
      [-1334.9],
      [1081.7],
      [206.1],
      [84.1],
      [],
      [C],
      [823.9],
      [1282.6],
      [180.6],
      [101.7],
      [],
      [D],
      [1158.8],
      [-1451.6],
      [187.9],
      [94.7],
      [],
      [E],
      [-1215.1],
      [-1593.3],
      [1667.6],
      [50.0],
      [],
      [F],
      [-1257.5],
      [1118.6],
      [1737.7],
      [81.2],
      [],
      [G],
      [1041.2],
      [1239.6],
      [1667.2],
      [104.0],
      [],
      [H],
      [1030.9],
      [-1583.2],
      [1715.6],
      [90.9],
      )
    ]
  ],
  caption: [Aligned AutoPos anchor coordinates for all anchor-layout solver versions. Coordinates use the report convention: x/y are horizontal and z_vert is vertical.],
) <tab:app_anchor_coords_all>


== Static Localisation Full Solver Sweep <app:static_solver_matrix>

@tab:app_static_T1, @tab:app_static_T2, @tab:app_static_T3, @tab:app_static_T4 report the complete static localisation sweep over five AutoPos anchor-layout solvers and four T-series tag solvers.
Every row uses the same 24 recorded static UWB sessions, the same anchor-locked rigid Vicon registration, and one session-mean coordinate per static position.
These are offline computer-side solves of the recorded UWB ranging logs.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Layout solver],
      [Median 3D \[mm\]],
      [P95 3D \[mm\]],
      [RMSE 3D \[mm\]],
      [v1-old],
      [157.4],
      [326.1],
      [192.6],
      [v2],
      [81.1],
      [248.0],
      [135.7],
      [v3-lite],
      [81.8],
      [248.5],
      [135.9],
      [v3-full],
      [120.6],
      [293.8],
      [160.7],
      [v4-io],
      [74.0],
      [282.1],
      [139.6],
      )
    ]
  ],
  caption: [Static localisation sweep, T1 tag solver (session mean, n=24).],
) <tab:app_static_T1>


#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Layout solver],
      [Median 3D \[mm\]],
      [P95 3D \[mm\]],
      [RMSE 3D \[mm\]],
      [v1-old],
      [157.4],
      [325.9],
      [192.3],
      [v2],
      [80.9],
      [246.6],
      [135.3],
      [v3-lite],
      [81.6],
      [247.0],
      [135.5],
      [v3-full],
      [121.9],
      [293.1],
      [160.1],
      [v4-io],
      [73.8],
      [280.7],
      [139.0],
      )
    ]
  ],
  caption: [Static localisation sweep, T2 tag solver (session mean, n=24).],
) <tab:app_static_T2>


#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 4, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Layout solver],
      [Median 3D \[mm\]],
      [P95 3D \[mm\]],
      [RMSE 3D \[mm\]],
      [v1-old],
      [128.8],
      [306.0],
      [168.0],
      [v2],
      [74.6],
      [183.7],
      [104.2],
      [v3-lite],
      [75.2],
      [183.8],
      [104.3],
      [v3-full],
      [90.1],
      [243.8],
      [129.7],
      [v4-io],
      [73.4],
      [163.4],
      [107.4],
      )
    ]
  ],
  caption: [Static localisation sweep, T3 tag solver (session mean, n=24).],
) <tab:app_static_T3>


#figure(
  block(width: 100%)[
    #text(size: 8pt)[
      #table(columns: 12, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [],
      table.cell(colspan: 9)[Vicon-referenced absolute accuracy \[mm\]],
      table.cell(colspan: 2)[UWB-internal repeatability \[mm\]],
      [],
      table.cell(colspan: 3)[Median error],
      table.cell(colspan: 3)[P95 error],
      table.cell(colspan: 3)[RMSE],
      [Median],
      [P95],
      [Layout solver],
      [x/y horiz.],
      [z vert.],
      [3D],
      [x/y horiz.],
      [z vert.],
      [3D],
      [x/y horiz.],
      [z vert.],
      [3D],
      [σ3D],
      [σ3D],
      [v1-old],
      [49.5],
      [119.0],
      [134.9],
      [87.0],
      [232.7],
      [233.8],
      [53.6],
      [150.3],
      [159.6],
      [68.0],
      [107.3],
      [v2],
      [44.1],
      [68.3],
      [80.9],
      [73.2],
      [159.9],
      [166.8],
      [48.7],
      [94.2],
      [106.1],
      [62.7],
      [93.2],
      [v3-lite],
      [44.3],
      [68.4],
      [81.4],
      [73.2],
      [159.6],
      [166.5],
      [48.9],
      [94.2],
      [106.1],
      [62.7],
      [93.5],
      [v3-full],
      [41.4],
      [90.4],
      [98.1],
      [105.0],
      [230.6],
      [233.8],
      [54.6],
      [121.6],
      [133.3],
      [61.5],
      [102.1],
      [v4-io],
      [37.4],
      [61.9],
      [72.7],
      [73.6],
      [170.0],
      [171.5],
      [47.9],
      [98.8],
      [109.8],
      [67.1],
      [88.0],
      )
    ]
  ],
  caption: [Static localisation sweep, T4 tag solver (session mean, n=24). This is the T4 slice shown in @tab:solver_static; absolute-accuracy columns use Vicon ground truth, while repeatability columns use the internal per-session UWB spread.],
) <tab:app_static_T4>


== Static Tag-Facing Stratification <app:static_facing>

@tab:app_static_facing groups the headline v4-io/T4 session-mean static errors by tag-facing group.
The four groups are balanced in the measurement design: each group contains 6 positions, including 3 edge and 3 centre positions and 2 low, 2 mid, and 2 high positions.
Because each group has only n=6, the table reports median, RMSE, and maximum error only; P95 is intentionally omitted because it is not stable for such a small group size.

#figure(
  block(width: 100%)[
    #text(size: 9.5pt)[
      #table(columns: 5, align: center, inset: 3.5pt, stroke: 0.45pt + gray,
      [Facing group],
      [n],
      [Median 3D \[mm\]],
      [RMSE 3D \[mm\]],
      [Max 3D \[mm\]],
      [ABEF],
      [6],
      [68.9],
      [103.9],
      [172.9],
      [ADHE],
      [6],
      [78.6],
      [89.5],
      [135.1],
      [BCGF],
      [6],
      [58.7],
      [133.2],
      [278.9],
      [CDHG],
      [6],
      [97.8],
      [108.2],
      [163.6],
      )
    ]
  ],
  caption: [Exploratory static tag-facing stratification for the headline v4-io/T4 session-mean pipeline.],
) <tab:app_static_facing>
