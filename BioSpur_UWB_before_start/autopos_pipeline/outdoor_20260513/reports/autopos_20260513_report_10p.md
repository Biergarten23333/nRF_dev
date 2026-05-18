# AutoPos 2026-05-13 Outdoor Experiment: Technical Progress Report

## 1. Introduction and Experimental Setup

AutoPos is a self-calibration pipeline for estimating the 3D layout of UWB anchors from inter-anchor ranging. The long-term objective is to reduce the setup burden of UWB-anchored wearable motion-capture systems: instead of surveying each anchor manually or relying on an external motion-capture system during deployment, the anchors should recover a usable 3D layout from their own ranging data. The present report summarizes the 2026-05-13 outdoor experiment and focuses on repeatability, internal consistency, and robustness. No OptiTrack ground truth was available in this dataset, so the reported positioning values must not be interpreted as absolute positioning accuracy.

The experiment used eight DWM1001C anchors with custom SS-TWR firmware. The deployed geometry is a four-lower plus four-upper layout. The coordinates below are the V4-io layout coordinates after applying the reporting convention used in `setup_geometry`, where A-D are the physical lower layer and E-H are the physical upper layer. The measured footprint is approximately 3.17 m by 4.78 m, the Z span is 1.74 m, and the average layer separation is 1.51 m.

| Anchor | Layer | X (mm) | Y (mm) | Z (mm) | Delay equiv. (mm) |
| --- | --- | ---: | ---: | ---: | ---: |
| A | lower | 0.0 | 0.0 | 0.0 | 0.0 |
| B | lower | 2961.0 | 0.0 | 0.0 | 20.2 |
| C | lower | 3167.2 | 4507.1 | 0.0 | 32.3 |
| D | lower | 191.7 | 4650.6 | -70.9 | 20.7 |
| E | upper | 106.8 | -103.5 | 1441.4 | 5.9 |
| F | upper | 2882.6 | -14.6 | 1418.2 | -2.4 |
| G | upper | 2958.8 | 4672.3 | 1673.5 | 1.8 |
| H | upper | 39.4 | 4623.6 | 1420.8 | -0.7 |

![V4-io anchor layout](setup_geometry/anchor_layout_v4io.png)

The protocol context is important. The previous paper concept used a sequential unicast setting and an offline quality-aware selector. In that concept, approximately 24% of fixes used all eight anchors and approximately 75% used four anchors. The 2026-05-13 experiment instead uses broadcast SS-TWR, so each epoch can in principle collect responses from all anchors. This is not a controlled broadcast-versus-unicast comparison, but it changes the interpretation of the repeatability results because the current static dataset is dominated by high-redundancy epochs. In addition to higher anchor availability, broadcast SS-TWR also reduces temporal skew within a position estimate: ranges to multiple anchors are associated with a common broadcast epoch rather than being collected over a longer sequential polling cycle. This is especially relevant for moving Tag captures, where sequential polling can mix measurements from slightly different Tag positions and thereby introduce motion-induced inconsistency.

| Anchor count per epoch | Epochs | Percent of solve-eligible epochs |
| ---: | ---: | ---: |
| 4 | 0 | 0.0% |
| 5 | 2 | 0.0% |
| 6 | 12 | 0.1% |
| 7 | 9395 | 68.0% |
| 8 | 4408 | 31.9% |

![Static per-epoch anchor availability](setup_geometry/static_anchor_count_distribution_pie.png)

The validation dataset contains 23 static Tag sessions, 17 Roto sessions, and five Wand sessions. Static Tag sessions are ID01-ID09 and ID11-ID24; ID10 was not captured and is excluded from all statistics. Roto sessions are ID25-ID41. Wand sessions are W01-W05; W01-W04 can be used as static rigid-body constraints, while W05 is better treated as a diagnostic or coverage capture because it involves free movement and stronger TDMA timing effects.

Three layout generation configurations were evaluated. `FULL-COMPARE-1000` uses the 1000-set sweep and is the main clean result. `FULL-COMPARE-500` uses the first 500 sweep sets to check whether fewer sweep data are enough. `FULL-COMPARE-500+500` uses first-500 solve and last-500 holdout logic to check generalization. All captured static, Roto, and Wand data are evaluated consistently across these configurations.

## 2. Layout Self-Consistency

Layout self-consistency evaluates whether the recovered anchor layout can explain the inter-anchor sweep measurements. It is not a Tag positioning metric and should not be confused with downstream repeatability. It is nevertheless useful for understanding whether each solver is internally consistent with the ranging data used for calibration.

| Version | AutoPos RMS (mm) | p50 (mm) | p95 (mm) | max (mm) |
| --- | ---: | ---: | ---: | ---: |
| V1 | 64.2 | 28.3 | 143.9 | 183.1 |
| V2 | 40.4 | 27.1 | 80.4 | 90.4 |
| V3-lite | 40.8 | 27.4 | 82.0 | 91.7 |
| V3-full | 66.4 | 2.0 | 182.6 | 207.9 |
| V4-io | 44.3 | 15.3 | 87.7 | 163.3 |
| V4-io-roto | 57.9 | 22.8 | 134.1 | 146.9 |
| V4-io-wand | 44.2 | 17.5 | 87.6 | 163.8 |

V2 and V3-lite produce the cleanest inter-anchor residuals in this dataset, which confirms that robust pair fusion is already strong. V4-io has a slightly higher residual RMS, but it includes bounded per-anchor delay estimation and a production-oriented robust objective. V3-full is a useful warning case: its p50 residual is extremely low, but its p95 and maximum residuals are high, so p50 alone would give a misleadingly optimistic view. V4-io-roto deliberately trades some inter-anchor residual for RotoArm soft constraints and therefore should not be ranked purely by this table.

The three clean rebuild settings agree closely for V4-io. AutoPos RMS is 44.3 mm for the 1000-sweep setting, 44.7 mm for the 500-sweep setting, and 44.2 mm for the 500+500 setting. This stability is important because it indicates that the main conclusions are not artifacts of a single sweep split.

## 3. Static Tag Repeatability

Static Tag repeatability is the main downstream validation in the absence of OptiTrack. When the Tag is stationary, the scatter of the solved positions reflects how stable the layout and downstream solver are under real ranging conditions. It remains a repeatability metric, not an absolute accuracy metric.

| Dataset | Static sessions | X med (mm) | Y med (mm) | Z med (mm) | Horizontal med (mm) | 3D med (mm) | Z variance share | 3D p95 (mm) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FULL-COMPARE-1000 | 23 | 26.0 | 16.3 | 37.9 | 30.4 | 49.2 | 62.1% | 81.6 |
| FULL-COMPARE-500 | 23 | 26.4 | 16.5 | 39.9 | 30.5 | 48.4 | 63.8% | 80.9 |
| FULL-COMPARE-500+500 | 23 | 26.3 | 16.6 | 40.7 | 30.5 | 48.4 | 63.7% | 80.6 |

The dominant structure is the vertical component. Across the three configurations, X is about 26 mm, Y is about 16 mm, and Z is about 38-41 mm. Z contributes about 62-64% of the 3D variance, which means the current limitation is not isotropic random scatter. The system is primarily limited by vertical observability and geometry in the four-lower plus four-upper anchor layout.

Spatial grouping confirms that this limitation is not uniform across the workspace. Low-height captures are weaker than high-height captures, and the CDHG-facing group is the most difficult orientation in this dataset.

| Group type | Group | N | X med (mm) | Y med (mm) | Z med (mm) | 3D med (mm) | Z variance share | Worst ID |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| location | center | 12 | 25.9 | 15.0 | 36.5 | 46.9 | 63.0% | ID18 |
| location | edge | 11 | 26.3 | 18.1 | 41.8 | 50.4 | 62.1% | ID08 |
| height | high | 8 | 25.9 | 15.2 | 29.3 | 41.6 | 47.3% | ID09 |
| height | low | 7 | 27.1 | 16.4 | 49.9 | 59.7 | 70.8% | ID07 |
| height | mid | 8 | 22.7 | 16.6 | 39.8 | 48.8 | 66.1% | ID08 |
| facing | ABEF | 6 | 23.6 | 17.6 | 36.1 | 47.3 | 64.4% | ID17 |
| facing | ADHE | 5 | 25.9 | 17.7 | 35.4 | 47.8 | 54.8% | ID20 |
| facing | BCGF | 6 | 25.8 | 14.2 | 39.7 | 49.7 | 63.4% | ID18 |
| facing | CDHG | 6 | 30.4 | 17.6 | 56.0 | 67.8 | 63.0% | ID08 |

The worst static captures combine difficult geometry, low anchor availability, and orientation effects. For example, ID08 has only 19.5% epochs with all eight anchors present and has a 71.1 mm Z standard deviation. ID07 has only 14.8% all-eight epochs and an 80.7% Z variance share.

| Rank | ID | Location | Height | Facing | X std (mm) | Y std (mm) | Z std (mm) | 3D std (mm) | Z share | pct >=8 anchors |
| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | ID08 | edge | mid | CDHG | 42.5 | 30.3 | 71.1 | 88.2 | 64.9% | 19.5% |
| 2 | ID09 | edge | high | CDHG | 46.0 | 21.1 | 64.9 | 82.3 | 62.1% | 35.4% |
| 3 | ID07 | edge | low | CDHG | 27.5 | 18.8 | 68.2 | 75.9 | 80.7% | 14.8% |
| 4 | ID18 | center | low | BCGF | 27.1 | 13.7 | 60.7 | 67.9 | 80.0% | 39.9% |
| 5 | ID17 | center | low | ABEF | 26.8 | 19.7 | 51.8 | 61.5 | 70.8% | 35.8% |

Under the current high-redundancy broadcast dataset, V2, V3-lite, and V4-io all land near 49 mm static 3D median. This convergence is not the central scientific result; it shows that when many anchors are available, redundancy can absorb some layout differences. The more important question is how the system behaves when anchor availability or subset geometry degrades.

## 4. Strict 8/8 Static Analysis

The strict 8/8 analysis was added to answer one specific question: are the all-available results contaminated by frames in which one or more anchors are missing? The diagnostic keeps only static frames where A-H are all present and re-evaluates the same V4-io layout with the same downstream solver. This is not a production filter because it discards most of the data.

| Evaluation condition | Captures | Frames | X med (mm) | Y med (mm) | Z med (mm) | 3D med (mm) | 3D RMS (mm) | 3D p95 (mm) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All-available | 23 | 13817 | 26.0 | 16.3 | 37.9 | 49.2 | 54.8 | 81.6 |
| Strict 8/8 only | 23 | 4408 | 23.4 | 14.8 | 37.4 | 44.5 | 49.4 | 67.0 |

Only 31.9% of the static frames survive strict 8/8 filtering. The filter improves X and Y medians and reduces the 3D tail, which confirms that missing-anchor epochs add horizontal and tail degradation. However, the most important observation is that Z median is almost unchanged, moving from 37.9 mm to 37.4 mm. This means the Z weakness is not simply an availability problem. Even with all eight anchors present, the vertical component remains the dominant limitation, so the underlying issue is geometry-driven.

This result also helps interpret the 100 mm-plus regime. Occasional missing-anchor frames are not sufficient to explain such large Z degradation; long low-redundancy periods, such as 4-anchor or 5-anchor selector behavior, are a much more consistent explanation.

## 5. Anchor Redundancy Robustness

The keep-k robustness experiment directly tests the effect of reducing anchor redundancy. It uses the `FULL-COMPARE-1000/v4-io/layout.json` layout, the static captures, and 500 Monte Carlo repeats per keep-k setting. The procedure randomly keeps k anchors and recomputes the static repeatability.

![Random keep-k anchor robustness](../ROBUSTNESS/v4io_1000_static_robustness/figures/random_keep_k_z_3d.png)

| Effective anchor count | Z median (mm) | 3D median (mm) | 3D p95 (mm) |
| --- | ---: | ---: | ---: |
| keep 8 | 37.9 | 49.2 | 81.6 |
| keep 7 | 43.6 | 53.2 | 105.6 |
| keep 6 | 60.9 | 77.1 | 166.5 |
| keep 5 | 83.4 | 100.7 | 225.2 |
| keep 4 | 124.6 | 156.3 | 355.3 |

The degradation is monotonic and large. With keep-6, the system already moves to 60.9 mm Z median and 77.1 mm 3D median. With keep-5, it reaches the 80-100 mm regime. With keep-4, Z median becomes 124.6 mm and 3D median becomes 156.3 mm. The numerical fail rate can still be low in this setting, but that only means the solver returns a numerical result; it does not mean the returned result has acceptable repeatability.

Independent random dropout gives a complementary view. As dropout probability increases, the solver does not immediately fail. Instead, repeatability degrades first, then numerical failures become more common.

| Dropout setting | Solved rate | Fail rate | Z median (mm) | 3D median (mm) | 3D p95 (mm) |
| --- | ---: | ---: | ---: | ---: | ---: |
| p05 | 100.0% | 0.0% | 46.9 | 59.5 | 115.5 |
| p10 | 99.8% | 0.2% | 55.7 | 69.4 | 142.5 |
| p20 | 97.5% | 2.5% | 70.9 | 87.9 | 188.4 |
| p30 | 89.5% | 10.5% | 83.8 | 104.8 | 225.0 |
| p40 | 74.7% | 25.3% | 94.9 | 119.0 | 285.3 |

Leave-one-out analysis shows which individual anchors matter most for the geometry. Removing B gives the worst 3D median, while removing C gives the worst Z median. Removing E does not produce the worst median, even though E is the strongest residual-tail anchor in the residual diagnostics. Removing H also does not strongly degrade the median because H has much lower availability in the captured data.

![Leave-one-anchor-out robustness](../ROBUSTNESS/v4io_1000_static_robustness/figures/leave_one_anchor_out_z_3d.png)

| Condition | Z median (mm) | 3D median (mm) | 3D p95 (mm) | Interpretation |
| --- | ---: | ---: | ---: | --- |
| baseline all-available | 37.9 | 49.2 | 81.6 | reference |
| no_B | 45.2 | 58.4 | 78.1 | worst 3D median |
| no_C | 45.5 | 56.4 | 68.6 | worst Z median |
| no_E | 41.2 | 51.1 | 80.6 | E residual tail does not equal largest geometry impact |
| no_H | 37.4 | 49.0 | 73.4 | H already has low availability |

![Per-anchor residual p95](../ROBUSTNESS/v4io_1000_static_robustness/figures/residual_abs_p95_by_anchor.png)

| Anchor | Observations | Residual med (mm) | Residual RMS (mm) | Abs residual p95 (mm) | Low-Q <80 | Downweighted | Large >100 mm |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 13806 | -15.8 | 44.1 | 91.1 | 0.0% | 18.6% | 3.3% |
| B | 13808 | -12.5 | 101.7 | 196.9 | 0.0% | 18.3% | 10.6% |
| C | 13804 | 24.2 | 51.0 | 101.7 | 0.0% | 17.0% | 5.1% |
| D | 13802 | -27.2 | 69.5 | 153.6 | 0.0% | 23.5% | 13.0% |
| E | 13801 | 37.8 | 98.6 | 212.7 | 0.0% | 45.3% | 27.2% |
| F | 13811 | -5.3 | 42.7 | 99.2 | 0.0% | 15.7% | 4.9% |
| G | 13800 | -4.5 | 43.5 | 94.1 | 0.0% | 17.5% | 4.2% |
| H | 4479 | 18.6 | 52.5 | 109.1 | 90.6% | 19.9% | 6.5% |

The clean interpretation is to separate three phenomena. Anchor E has the largest residual tail and should be inspected for residual or NLOS-like behavior. Anchors B and C are more geometrically influential in leave-one-out tests. Anchor H has low availability and a high low-Q rate, so it is primarily an availability and signal-quality concern. These should not be collapsed into a single statement that one anchor is simply "bad."

The keep-k curve also resolves the apparent tension with the earlier paper concept. Under low-redundancy selector behavior, the current data can reproduce the 100 mm-plus regime. Under high-redundancy broadcast all-available evaluation, it does not.

## 6. Kinematic Validation: Roto and Wand

Roto captures are moving captures, so raw circle thickness should not be reported as dynamic positioning error. A rotating tag is supposed to sweep a circle; the meaningful diagnostics are whether the mechanical relationship between the two Roto tags is consistent and whether fitted turn centers repeat across revolutions.

| Version | Roto sessions | dR mean (mm) | dR RMS (mm) | abs dR med (mm) | abs dR p95 (mm) | turn-center med (mm) | turn-center p95 (mm) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| V4-io | 17 | -24.3 | 32.3 | 23.5 | 50.5 | 20.6 | 28.9 |
| V4-io-td | 17 | -23.5 | 31.6 | 22.8 | 49.1 | 20.0 | 28.3 |
| V4-io-roto | 17 | -21.4 | 29.9 | 24.9 | 45.4 | 18.0 | 25.9 |
| V4-io-wand | 17 | -23.2 | 31.7 | 21.4 | 49.8 | 19.1 | 28.6 |

For V4-io, the dR RMS is 32.3 mm and the per-revolution turn-center median is 20.6 mm. These are the appropriate kinematic consistency measures for this dataset. The dR mean is systematically negative at -24.3 mm, meaning the solved `R_outer - R_inner` is smaller than the mechanical 120 mm difference. This bias is not explained by random scatter. Plausible causes include tag-specific antenna delay, different inner-versus-outer antenna visibility or NLOS patterns, and residual layout scale or Z bias. The current dataset does not contain external ground truth to isolate the cause, so the bias should be carried forward as a known follow-up item.

Wand data have two roles. As a Tag validation object, the three wand tags can be solved as normal tags and their pairwise distances can be compared with tape-measured ground truth. For V4-io, the W01-W04 pairwise distance bias RMS is 59.3 mm over 10 valid edges. As a calibration constraint, W01-W04 can also be injected as a soft rigid-body constraint. This improves the V4-io static median from 49.2 mm to 48.6 mm and improves the static p95 from 81.6 mm to 77.3 mm, but the effect is small and should not be presented as the main contribution.

## 7. Solver Comparison and Ablation

The solver comparison should be interpreted carefully. Under high-redundancy broadcast conditions, V2, V3-lite, and V4-io all give static 3D medians around 49 mm. This does not mean delay-aware solvers are irrelevant. It means that all-available high-redundancy evaluation can hide some layout differences because many anchors are available and the downstream solver can average or absorb bias.

V4-io remains important because it is the production-oriented delay-aware solver. It estimates bounded per-anchor delay terms and uses a robust objective designed for field data. V4-io-td adds a common static Tag delay scan and finds a 3.0 mm common Tag delay, but the downstream effect is negligible: static median changes from 49.2 mm to 48.9 mm. This suggests that a single common Tag delay is not the dominant remaining limitation in the current dataset.

V4-io-roto and V4-io-wand are useful ablations. V4-io-roto uses RotoArm soft constraints, so improvements in Roto metrics are not independent holdout evidence; rather, they indicate that the constraints are compatible with the data. V4-io-wand uses W01-W04 soft Wand constraints and provides a small static-tail improvement. The main message is that under high-redundancy broadcast, solver-version differences are modest, while the value of V4-io becomes more important under degraded anchor availability or when future OptiTrack validation tests absolute bias.

## 8. FIM and Candidate Anchor Directions

The Fisher Information Matrix analysis is a geometric simulation, not an empirical validation. It assumes an unbiased Gaussian range model with approximately 50 mm range noise for the candidate anchor and does not model NLOS, antenna pattern, TDMA availability, synchronization errors, or installation error. Its role is to propose where an additional anchor would most improve vertical observability.

| Candidate | X (mm) | Y (mm) | Z (mm) | Median Z uncertainty reduction factor | p05 factor | Interpretation |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| center_low_level | 1538 | 2292 | 71 | 3.54 | 0.11 | Strong median gain, weak worst-region behavior |
| center_extra_high | 1538 | 2292 | -2473 | 3.32 | 1.76 | Most robust candidate |
| center_high_level | 1538 | 2292 | -1673 | 2.15 | 0.23 | Moderate gain |
| center_mid_level | 1538 | 2292 | -735 | 1.50 | 0.08 | Limited gain |

The best engineering recommendation is not simply to add an anchor wherever convenient. The keep-k analysis shows that removing anchors is risky, so the next step should be improving geometry and redundancy. Among the simulated candidates, `center_extra_high` is the most robust because its p05 reduction factor remains above 1, meaning it is less likely to help the median while hurting already difficult regions. Any real deployment with a ninth anchor must also revisit broadcast timing and response-window constraints.

## 9. Conclusions and Next Steps

The 2026-05-13 experiment shows that AutoPos is stable under the current broadcast dataset, with V4-io static 3D repeatability around 49 mm and Z repeatability around 38-41 mm across the three clean rebuild settings. The main limitation is vertical geometry: Z accounts for about 62-64% of the 3D variance, strict 8/8 filtering does not remove the Z weakness, and low-redundancy keep-k tests push the system into the 100 mm-plus regime. The earlier concept results are therefore best understood as a low-redundancy selector phenomenon rather than a simple failure of the AutoPos layout.

The next experimental priority is OptiTrack validation, because absolute accuracy cannot be claimed from this dataset alone. The second priority is improving vertical geometry, likely through a ninth anchor placed at a high central location, while respecting broadcast timing constraints. The third priority is targeted hardware and signal-quality investigation: E should be checked for residual-tail behavior, H for availability and low-Q behavior, and B/C for their geometric influence in leave-one-out tests.
