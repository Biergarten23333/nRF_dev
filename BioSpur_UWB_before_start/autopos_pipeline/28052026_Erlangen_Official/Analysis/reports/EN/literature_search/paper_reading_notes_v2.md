# Paper Reading Notes V2

This revision expands the local full-text corpus beyond the first 20 PDFs. Zotero still contains zero PDF attachments. The downloaded corpus now contains 41 PDFs, of which 40 are machine-readable full texts tied to registry entries. One Patwari PDF remains scanned/no-text. De Preter 2019 remains a close metadata/abstract-only paper: Semantic Scholar/OpenAlex point to a KU Leuven OA record, but the current repository bitstream resolved to an unrelated poster, while SciSpace and IEEE did not provide a usable local proceedings PDF.

## Coverage Summary

- Registry rows: 128
- ABSTRACT_AND_METADATA_ONLY_NO_LOCAL_PDF: 2
- FULL_TEXT_READ: 40
- PDF_AVAILABLE_SCANNED_NO_TEXT: 1
- abstract_read_via_openalex: 84
- metadata_read_via_openalex: 1
- Exact novelty phrase scan across machine-readable full texts: no hits for delay-layout, wrong-metric, ranking-flip, scale-delay, common-mode, or error-cancellation formulations.

## Targeted Close-Paper Notes

### Prorok 2013 thesis: targeted CRB/FIM check

The thesis is now treated as a substantive full-text source, not a one-line background item. The relevant UWB section models positive NLOS bias and explicitly derives a Cramer-Rao lower-bound analysis for the proposed probabilistic range model. The text also uses Fisher information language and discusses how NLOS probability and bias affect estimation quality. This directly supports the legitimacy of our Fisher/CRB vocabulary. However, the thesis does not jointly estimate anchor layout scale with antenna delay, does not compare a metric-correct and metric-distorted anchor calibration, and does not report a same-environment accuracy reversal after reducing a positive tag-anchor tail. Verdict: close on NLOS bias and CRB, not on delay-layout coupling.

### De Preter et al. 2019: access audit

This remains one of the closest titles because the abstract describes range-bias modeling and semi-automated autocalibration of UWB anchors. Semantic Scholar reports a green OA PDF at a KU Leuven LIRIAS bitstream, and OpenAlex reports a LIRIAS OA landing page, but the current `retrieve/644167` endpoint returned an unrelated one-page poster. The SciSpace PDF endpoint returned HTTP 403 and IEEE returned bot-protection HTML from this workstation. Therefore the registry marks it as abstract/metadata only. Based on the abstract, it estimates anchor coordinates and a range-bias model parameter from captured tag data; it does not state the delay-layout scale coupling or wrong-metric-wins result.

## Per-Paper Full-Text Checks

### [shi_2019_anchor_self_localization_algorit] Anchor self-localization algorithm based on UWB ranging and inertial measurements

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 11 pages / 5983 words
- Cluster/relevance: A / 5
- Targeted term counts: calibr=6, bias=8, nlos=3, delay=1, ground truth=11, anchor=99, self-local=42
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [queralta_2020_uwb_based_system_for_uav] UWB-based System for UAV Localization in GNSS-Denied Environments: Characterization and Dataset

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 10 pages / 7602 words
- Cluster/relevance: A / 5
- Targeted term counts: calibr=12, nlos=3, delay=2, ground truth=4, anchor=111
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [ahmad_2024_a_novel_self_calibrated_uwb_base] A Novel Self-Calibrated UWB-Based Indoor Localization Systems for Context-Aware Applications

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 15 pages / 11877 words
- Cluster/relevance: A / 5
- Targeted term counts: calibr=28, bias=1, nlos=34, delay=2, fisher=1, cram=1, scale=4, anchor=8
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [schwarzbach_2026_evaluation_of_grid_based_uncerta] Evaluation of Grid-Based Uncertainty Propagation for Collaborative Self-Calibration in Indoor Positioning Systems

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 61 pages / 7037 words
- Cluster/relevance: A / 5
- Targeted term counts: calibr=34, bias=6, nlos=53, scale=3, ground truth=3, anchor=55, self-local=1
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [wang_2019_design_and_implementation_of] Design and Implementation of Synchronization-free TDOA Localization System Based on UWB

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 12 pages / 7576 words
- Cluster/relevance: A;B / 5
- Targeted term counts: calibr=1, delay=18, anchor=9
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [shah_2021_antenna_delay_calibration_of] Antenna Delay Calibration of UWB Nodes

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 13 pages / 8340 words
- Cluster/relevance: A;B / 5
- Targeted term counts: calibr=98, bias=20, nlos=10, delay=172, anchor=11
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [piavanini_2022_a_self_calibrating_localization_] A Self-Calibrating Localization Solution for Sport Applications with UWB Technology

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 23 pages / 11709 words
- Cluster/relevance: A;B / 5
- Targeted term counts: calibr=24, bias=15, nlos=1, delay=70, scale=1, ground truth=1, anchor=82, self-local=11
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [shah_2022_antenna_delay_independent_simult] Antenna Delay-Independent Simultaneous Ranging for UWB-Based RTLSs

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 15 pages / 8294 words
- Cluster/relevance: A;B / 5
- Targeted term counts: calibr=9, bias=8, nlos=8, delay=90, anchor=49, self-local=1
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [shah_2022_node_calibration_in_uwb_based] Node Calibration in UWB-Based RTLSs Using Multiple Simultaneous Ranging

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 14 pages / 7859 words
- Cluster/relevance: A;B / 5
- Targeted term counts: calibr=112, bias=2, nlos=8, delay=83, scale=1, anchor=80
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [santamaria_pedr_n_2025_machine_learning_integration_in] Machine Learning Integration in Ultra-Wideband-Based Indoor Positioning Systems: A Comprehensive Review

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 57 pages / 30197 words
- Cluster/relevance: A;B;C;D / 5
- Targeted term counts: calibr=6, bias=49, nlos=153, delay=7, cram=2, scale=13, ground truth=16, anchor=37
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [lutz_2019_visual_inertial_slam_uwb_error_model] Visual-inertial SLAM aided estimation of anchor poses and sensor error model parameters of UWB radio modules

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 10 pages / 7286 words
- Cluster/relevance: A;B;D;E / 5
- Targeted term counts: calibr=26, bias=7, nlos=6, delay=8, anchor=127
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [kong_2023_nlos_identification_for_uwb] NLOS Identification for UWB Positioning Based on IDBO and Convolutional Neural Networks

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 18 pages / 9414 words
- Cluster/relevance: A;C;D / 5
- Targeted term counts: bias=1, nlos=96, delay=2, anchor=1
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [difranco_2017_calibration_free_network_localization_nlos_uwb] Calibration-Free Network Localization using Non-Line-of-Sight UWB Measurements

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 13 pages / 10067 words
- Cluster/relevance: A;C;D;E / 5
- Targeted term counts: calibr=20, bias=19, nlos=50, scale=4, ground truth=4, anchor=10
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [wymeersch_2012_a_machine_learning_approach] A Machine Learning Approach to Ranging Error Mitigation for UWB Localization

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 11 pages / 7580 words
- Cluster/relevance: A;C;D;F / 5
- Targeted term counts: bias=6, nlos=84, delay=7, fisher=1, anchor=14
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [prorok_2013_models_algorithms_uwb_localization_thesis] Models and Algorithms for Ultra-Wideband Localization in Single- and Multi-Robot Systems

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 177 pages / 63581 words
- Cluster/relevance: A;C;D;F / 5
- Targeted term counts: calibr=70, bias=27, nlos=91, delay=13, fisher=1, cram=5, scale=29, ground truth=35, anchor=2, self-local=3
- Verdict: PARTIALLY. Strongly relevant for UWB positive NLOS bias and CRB/FIM analysis; it does not study anchor-delay/layout-scale coupling or a wrong-metric calibration outperforming a correct one.

### [chen_2022_nlos_identification_and_correcti] NLOS Identification- and Correction-Focused Fusion of UWB and LiDAR-SLAM Based on Factor Graph Optimization for High-Precision Positioning with Reduced Drift

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 23 pages / 10448 words
- Cluster/relevance: A;C;D;F / 5
- Targeted term counts: calibr=3, bias=1, nlos=101, delay=1, scale=5, anchor=89
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [ledergerber_2015_robot_self_localization_one_way_uwb] A robot self-localization system using one-way ultra-wideband communication

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 8 pages / 5528 words
- Cluster/relevance: A;D / 5
- Targeted term counts: bias=9, delay=5, cram=1, scale=3, ground truth=1, anchor=61, self-local=14
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [poulose_2020_uwb_indoor_localization_using] UWB Indoor Localization Using Deep Learning LSTM Networks

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 24 pages / 8052 words
- Cluster/relevance: A;D / 5
- Targeted term counts: nlos=13, delay=2, anchor=56
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [herbruggen_2023_multihop_self_calibration_algori] Multihop Self-Calibration Algorithm for Ultra-Wideband (UWB) Anchor Node Positioning

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 12 pages / 9693 words
- Cluster/relevance: A;D / 5
- Targeted term counts: calibr=77, bias=6, nlos=12, fisher=1, scale=10, ground truth=2, anchor=233, self-local=2
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [corbalan_2023_self_localization_uwb_anchors] Self-Localization of Ultra-Wideband Anchors: From Theory to Practice

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 16 pages / 12747 words
- Cluster/relevance: A;D;E / 5
- Targeted term counts: calibr=6, bias=5, nlos=23, scale=21, ground truth=9, anchor=236, self-local=76
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [almansa_2020_autocalibration_mobile_uwb_localization] Autocalibration of a Mobile UWB Localization System for Ad-Hoc Multi-Robot Deployments in GNSS-Denied Environments

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 11 pages / 4228 words
- Cluster/relevance: A;D;F / 5
- Targeted term counts: calibr=56, delay=2, anchor=100
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [jim_nez_2021_improving_the_accuracy_of] Improving the Accuracy of Decawave’s UWB MDEK1001 Location System by Gaining Access to Multiple Ranges

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 32 pages / 13671 words
- Cluster/relevance: A;D;F / 5
- Targeted term counts: calibr=7, bias=5, nlos=22, delay=1, scale=2, ground truth=1, anchor=141
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [hamer_2018_self_calibrating_ultra_wideband_network] Self-Calibrating Ultra-Wideband Network Supporting Multi-Robot Localization

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 14 pages / 10283 words
- Cluster/relevance: A;E / 5
- Targeted term counts: calibr=15, bias=26, nlos=4, delay=12, fisher=1, cram=1, scale=2, ground truth=3, anchor=223, self-local=12
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [shalaby_2023_calibration_and_uncertainty_char] Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 8 pages / 5967 words
- Cluster/relevance: B / 5
- Targeted term counts: calibr=85, bias=60, delay=47, scale=1, ground truth=2, anchor=6
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [liu_2024_data_driven_antenna_delay_calibr] Data-Driven Antenna Delay Calibration for UWB Devices for Network Positioning

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 12 pages / 8831 words
- Cluster/relevance: B / 5
- Targeted term counts: calibr=75, bias=22, nlos=1, delay=102, scale=2, anchor=24
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [liu_2024_ranging_offset_calibration_and] Ranging Offset Calibration and Moving Average Filter Enhanced Reliable UWB Positioning in Classic User Environments

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 19 pages / 11957 words
- Cluster/relevance: B / 5
- Targeted term counts: calibr=77, bias=2, nlos=49, delay=2
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [ledergerber_2018_calibrating_away_inaccuracies_uwb] Calibrating Away Inaccuracies in Ultra Wideband Range Measurements: A Maximum Likelihood Approach

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 13 pages / 8448 words
- Cluster/relevance: B;C;D;F / 5
- Targeted term counts: calibr=26, bias=9, nlos=3, delay=5, scale=1, ground truth=7, anchor=29, self-local=1
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [ledergerber_2017_uwb_range_measurement_gp] Ultra-Wideband Range Measurement Model with Gaussian Processes

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 7 pages / 4524 words
- Cluster/relevance: B;C;F / 5
- Targeted term counts: bias=5, ground truth=1, anchor=15, self-local=1
- Verdict: PARTIALLY. Close prior art for calibration, bias, anchor self-localization, or UWB error modeling; it does not report the delay-layout-NLOS cancellation/ranking-flip claim.

### [goudar_2021_online_spatiotemporal_calibration_uwb_imu] Online Spatio-temporal Calibration of Tightly-coupled Ultrawideband-aided Inertial Localization

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 10 pages / 7797 words
- Cluster/relevance: B;D;E / 5
- Targeted term counts: calibr=34, bias=12, nlos=1, delay=10, scale=2, ground truth=8, anchor=45
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [shalaby_2022_calibration_and_uncertainty_char] Calibration and Uncertainty Characterization for Ultra-Wideband Two-Way-Ranging Measurements

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 8 pages / 5967 words
- Cluster/relevance: B;F / 5
- Targeted term counts: calibr=85, bias=60, delay=47, scale=1, ground truth=2, anchor=6
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [meghani_2019_empirical_based_ranging_error] Empirical Based Ranging Error Mitigation in IR-UWB: A Fuzzy Approach

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 13 pages / 8562 words
- Cluster/relevance: C / 5
- Targeted term counts: bias=7, nlos=98, delay=13, anchor=16
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [yang_2024_uwb_nlos_identification_and] UWB NLOS Identification and Mitigation based on Bidirectional Encoder Representations from Transformer (BERT) Deep Learning

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 7 pages / 5140 words
- Cluster/relevance: C / 5
- Targeted term counts: bias=3, nlos=68, delay=7, scale=2, anchor=2
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [fontaine_2023_transfer_learning_for_uwb] Transfer Learning for UWB Error Correction and (N)LOS Classification in Multiple Environments

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 17 pages / 13932 words
- Cluster/relevance: C;D / 5
- Targeted term counts: bias=1, nlos=84, delay=4, scale=2, ground truth=5, anchor=26
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [jeong_2023_hybrid_quantum_convolutional_neu] Hybrid Quantum Convolutional Neural Networks for UWB Signal Classification

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 15 pages / 11923 words
- Cluster/relevance: C;D / 5
- Targeted term counts: bias=2, nlos=54, delay=3, scale=1, anchor=4
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [g_ven_2007_nlos_identification_and_weighted] NLOS Identification and Weighted Least-Squares Localization for UWB Systems Using Multipath Channel Statistics

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 15 pages / 10372 words
- Cluster/relevance: C;D;F / 5
- Targeted term counts: bias=43, nlos=185, delay=45, cram=1, anchor=1
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [sung_2023_accurate_indoor_positioning_for] Accurate Indoor Positioning for UWB-Based Personal Devices Using Deep Learning

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 20 pages / 11365 words
- Cluster/relevance: C;D;F / 5
- Targeted term counts: calibr=3, bias=3, nlos=62, delay=4, ground truth=15, anchor=51
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [joudarwin_2008_position_error_bound_uwb] On the Accuracy of Localization Systems Using Wideband Antenna Arrays

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 17 pages / 9989 words
- Cluster/relevance: D;E / 5
- Targeted term counts: bias=58, nlos=59, delay=11, fisher=1, cram=5, anchor=4
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [wymeersch_2009_cooperative_localization_wireless_networks] Cooperative Localization in Wireless Networks

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 25 pages / 19104 words
- Cluster/relevance: D;E / 5
- Targeted term counts: bias=4, nlos=27, delay=8, fisher=3, scale=7, anchor=39, self-local=6
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [aspnes_2006_theory_network_localization] A Theory of Network Localization

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 36 pages / 14357 words
- Cluster/relevance: E / 5
- Targeted term counts: scale=6, anchor=1
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [rath_2020_single_anchor_positioning_multip] Single-Anchor Positioning: Multipath Processing With Non-Coherent Directional Measurements

- Read status: FULL_TEXT_READ
- Pages/word count from pdftotext: 19 pages / 14827 words
- Cluster/relevance: E / 4
- Targeted term counts: bias=1, nlos=1, delay=59, fisher=6, cram=4, scale=7, anchor=73
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

### [patwari_2005_locating_the_nodes] Locating the Nodes: Cooperative Localization in Wireless Sensor Networks

- Read status: PDF_AVAILABLE_SCANNED_NO_TEXT
- Verdict: counted as available but not machine-readable; not used as full-text evidence.

### [de_preter_2019_range_bias_modeling_autocalibration] Range Bias Modeling and Autocalibration of an UWB Positioning System

- Read status: ABSTRACT_AND_METADATA_ONLY_NO_LOCAL_PDF
- Verdict: PARTIALLY. Very close by title and abstract because it combines range-bias modeling with UWB autocalibration; no usable proceedings full text was obtained, and the abstract does not report delay-layout scale coupling or wrong-metric-wins.

### [hesch_2014_camera_imu_observability] Camera-IMU-based localization: Observability analysis and consistency improvement

- Read status: ABSTRACT_AND_METADATA_ONLY_NO_LOCAL_PDF
- Verdict: NO. Relevant background, but no delay-layout coupling or wrong-metric-wins claim found.

## [luder_2025_anitrack] AniTrack: UWB Localization for Animal Tracking

- Read status: FULL_TEXT_READ (arXiv PDF)
- Pages/word count from pdftotext: 7 pages / 5046 words; paper body is approximately 5 pages.
- Cluster/relevance: A;D / 5
- Hardware and environment: DWM3000, SS-TWR, 5 anchors, 600 m2 outdoor deployment.
- COUPLING CHECK: NOT DISCUSSED. They observed the paradox but did not analyze it.
- IDENTIFIABILITY CHECK: not addressed.
- CANCELLATION CHECK: not addressed.
- EVALUATION CHECK: They compared ground-truth anchors against self-localized anchors for tag positioning. Ground-truth anchors gave 16.57 cm average and 14.62 cm median 2D error. Self-localized anchors gave 13.96 cm average and 10.98 cm median 2D error. Self-localized anchors were better.
- Short quote: "self-localized anchors were more accurate".
- VERDICT: CRITICAL INDEPENDENT EVIDENCE. They observed wrong-calibration-wins on different hardware, a different environment, and a different spatial scale. They did not explain why. Our delay-layout coupling theory provides the mechanism.
