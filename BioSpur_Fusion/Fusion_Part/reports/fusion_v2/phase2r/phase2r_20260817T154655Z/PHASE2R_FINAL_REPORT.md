# BioSpur Fusion Phase 2-R final report

## Verdict

- Primary: `STAGE_COMPLETE_NEEDS_USER_CAPTURE`
- Substages: `FAIL_PHASE2A_BLIND_NODE_ASSOCIATION`; `PHASE2BC_RESEARCH_CALIBRATION_LIMITED`
- Capabilities: `RESEARCH_CALIBRATION_LIMITED`, `PRODUCTION_INTRINSIC_NOT_YET_QUALIFIED`, `DEVICE_ANTENNA_METROLOGY_PENDING`, `WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN`, `CONTACT_UNOBSERVABLE`, `PCB_EDGE_TO_IMU_AXIS_UNRESOLVED`, `DIRECTED_EDGE_ID_UNRESOLVED`, `NO_EXTERNAL_ACCURACY_OR_CLINICAL_CLAIM`
- Execution contamination: historical mapping constants were exposed during source audit before candidate freeze. The candidate worker itself read zero mapping-revealing dataset bytes, but this executor permanently records `TRUTH_CONTAMINATED_DEVELOPMENT_REVISION` and makes no pristine-blind claim.

## Inputs and access

Exactly 19 promoted `rep_01` windows were consumed: 00_initial_still, 02_t_pose, 03_pelvis_hula_circle, 04_shoulder_left, 05_shoulder_right, 06_elbow_left, 07_elbow_right, 08_hip_left, 09_hip_right, 10_knee_left_seated, 11_knee_right_seated, 12_heel_raise_left, 13_heel_raise_right, 14_trunk_flex_extend, 15_trunk_axial_rotation, 16_squat, 17_final_still, 18_heel_to_butt_left, 19_heel_to_butt_right. Literal routing came from the frozen capture plan. Invalid, redo, rejected, non-promoted and deleted neutral-sway numeric consumption was zero. The real squat and final-still came only from the final promoted restart; the accepted squat blackout was not used to rewrite QC.

H00/H01/H02 direct opens, numeric decodes, arrays, statistics, plots and estimator factors were all zero. The consolidated ledger has 294 entries, 139930485 payload bytes read, 21674592 decoded numeric scalars, 2850 array materializations and 0 recorded factor consumptions; pretruth mapping payload reads=0, holdout payload reads=0. Ledger SHA-256: `d2ed34705d8cca64e2defbbeead90a2e4dea5b657e7ae8495a308188cfa8add5`.

## Mounting prior and segmentation

The H9 statement was stored append-only as one operator evidence source and modeled as a broad antipodal spherical direction cluster in anonymous sensor coordinates. `BSFC2CC` was structurally excluded, not treated as an outlier. Initial/final angular RMS was 0.284426/0.311981 rad; maximum node shift remained below the frozen conflict threshold, so no temporal mounting conflict was declared. Prior OFF/0.5x/1x/2x retained the same mapping. Its production factor count was zero to prevent accelerometer double counting.

The physical directed edge was not uniquely identified and no independent CAD/package/decoder chain proved edge-to-IMU-axis or raw specific-force sign. Therefore both signs remain and no `+X` was guessed.

The 19 windows yielded 284 candidate cycles and 291 unassigned transition/correction intervals. Boundary uncertainty spanned 0.059–0.281 s. Segmentation allowed variable repetitions, reversals, fatigue, coupling and natural correction; it did not assume three repetitions.

## Anonymous association and reveal

The frozen Top-1 score was 5.701316288; Top-2 was 5.701306894, giving observed margin 9.39408887e-06. The 2,000-permutation global-search null P99 was 0.084075783; the observed margin failed it. Across 1,000 stratified bootstraps, exact Top-1 frequency was 0.400, one-sided Wilson lower bound 0.375, and minimum selected-binding lower bound 0.623. Leave-one-action stability was 17/19 and leave-one-family stability 6/8. Prior-OFF and UWB-OFF selected the same mapping; UWB factor count was zero, so leave-one-anchor was correctly not applicable. All 0.5/1/2/5 ms timing perturbations retained the mapping.

Only after candidate bytes and ledgers were frozen was sealed truth revealed once. Commitment verification passed. Top-1 matched 8/10; truth rank in frozen Top-K was 3. There was no post-truth tuning, candidate reordering or automatic freeze. Operator truth is isolated in `OPERATOR_GROUND_TRUTH_MAPPING_BINDING.json` with authority `OPERATOR_RECORDED_POST_CAPTURE`; it is not represented as automatic recovery.

## Conditional calibration and observability

Only weak, mapping-conditional functional-axis distributions and soft low-dynamic gyro-bias estimates were retained. Full `T_segment_to_IMU`, accelerometer bias, metric translations, joint centres, bone lengths, antenna lever arms and external/world transforms are unobserved, prior-dominated or require metrology. Dynamic raw specific-force was disabled because no differentiable translational trajectory plus lever-arm metrology existed. Compliance remains unverified.

The local parameter block had 120 dimensions. Across relative SVD tolerances 1e-4 through 1e-8, data-only rank/nullity was 50/70 and prior-inclusive rank/nullity 120/0. Weak/gauge modes include global translation, global yaw, possible common velocity, independent segment/subtree heading, directed-edge sign/twist and contact-disabled modes. Priors supplied numerical rank, not new evidence.

Production factor counts were Phase1_orientation_factor=0, UWB_metric_factor=0, dynamic_raw_specific_force=0, low_dynamic_raw_specific_force=106238, mounting_cluster_factor=0, soft_functional_axis=132138, soft_low_dynamic_gyro_bias=42287. Accelerometer samples consumed once=106238; gyro samples consumed once=174425; accelerometer double-count=0. Q1/VQF, T4/old pose, historical mapping prior, UltraInertialPoser and H00/H01/H02 counts were zero.

The P3 loader dry-run returned `PASS_CONDITIONAL_P3_CONSTRUCTOR_COMPATIBILITY` for 10 instrumented segments, and covariance perturbation measurably increased prediction uncertainty. `authoritative_constructor_ready=false`. It is conditional compatibility only.

## Reproducibility, publication and next evidence

Two independent replays produced byte-identical core machine artifacts: true. Implementation commit: `49f9b2249b77d5a201480b75de080c4543cbccf9`. Attestation and remote publication SHAs are filled by the repo-external publication envelope after the second commit.

To cross the next scientific boundary, collect one coordinated evidence package: subject anthropometry; an independent per-device fixture/CAD measurement of IMU-to-UWB phase-centre geometry and PCB-edge-to-sensor-frame transform with covariance; surveyed world/anchor/floor transform and footwear/floor assumptions; and independent optical/Vicon or equivalent truth. If automatic association remains a product requirement, collect additional independent sessions/actions specifically resolving torso-pelvis, upper/forearm and thigh-shank ambiguities rather than retuning this revealed dataset. These gaps block authoritative extrinsics, metric/world pose, joint centres, bone geometry, contact and accuracy claims.

Phase 3 implementation was not started.
Phase 3 holdout numeric content remained sealed.
No external pose, metric-world, clinical-angle or accuracy claim is made.
