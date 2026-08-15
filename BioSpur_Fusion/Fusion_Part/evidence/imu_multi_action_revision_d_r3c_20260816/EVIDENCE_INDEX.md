# Revision D R3C checkpoint evidence

```text
R3C_VERDICT = PASS_R3C_SIGNAL_DERIVED_MOTION_EVIDENCE
FORMAL_R3C_RUN_COUNT = 1
D0_READY_FOR_SEPARATE_AUTHORIZATION = true
```

This checkpoint establishes that signal-derived relative motion and reversal evidence are present after repairing the invalid R3B activity normalizer. It does not qualify repetition counting, exact phase boundaries, functional-axis calibration, or joint-zero calibration.

```text
ACTIVE_MOTION_EVIDENCE = PASS
CYCLE_COUNT_ACCURACY = NOT_QUALIFIED
PHASE_BOUNDARY_ACCURACY = NOT_QUALIFIED
FUNCTIONAL_AXIS_CALIBRATION = NOT_YET_PERFORMED
JOINT_ZERO_CALIBRATION = NOT_YET_PERFORMED
```

The detector reported 26/25 complete cycles for the left/right arms and approximately 16 for each elbow, versus an operator protocol of roughly five repetitions per motion block. These extrema counts are inconsistent with literal repetition counts and must not be treated as ground truth. D0 must not turn every detected extremum into an independent calibration observation.

The R3B failure remains immutable and unadoptable. R3C-0 showed that R3B used absolute Q2 attitude covariance, including an unobserved yaw gauge, as adjacent-frame rate uncertainty without temporal cross-covariance. R3C instead uses the same-signal quiet-baseline median and robust scale with the predeclared 0.035 rad/s process-noise floor. The historical R3 failed-target record is classified `METADATA_TARGET_MISATTRIBUTION`.

## Pending source audits

These items are not PASS and remain required before D0:

```text
PENDING_SOURCE_AUDIT_1 =
  WHETHER_Q2_NODE_ORIENTATIONS_SHARE_ONE_NAVIGATION_FRAME

PENDING_SOURCE_AUDIT_2 =
  INDEPENDENT_PER_NODE_YAW_GAUGE_INVARIANCE_OF_R3C_ACTIVITY

PENDING_SOURCE_AUDIT_3 =
  COMMON_RIGID_MOTION_NEGATIVE_CONTROL

PENDING_SOURCE_AUDIT_4 =
  CYCLE_EXTREMA_FRAGMENTATION
```

## Included compact evidence

- `formal/`: formal result, report, full 19-chain motion/cycle timeline, candidate ledger, factor eligibility, deterministic activity-array NPZ, frozen per-chain activity-scale components, access audit, and original formal SHA manifest.
- `r3c0/`: formula audit, dimensional audit, 19-chain old/empirical/process scale comparison, and historical reproducibility audit.
- `synthetic/`: raw IMU through production Q2/common-time qualification and negative controls.
- `observation/`: observation-only 19-chain scale/threshold/finite sanity result and access audit; no cycles were computed in that stage.
- `r3a/`, `r3b/`: stage data-access audits.
- `freeze/`: frozen R3C contract, action-chain map, source/config/input binding, and freeze manifest.
- `TEST_RESULTS.md`: py_compile, import-closure, and 17-test result.

Every copied artifact is byte-identical to its ignored local source. Exact source path, copy path, size, and SHA-256 are recorded in `EVIDENCE_MANIFEST.json`.

## Deliberately excluded payloads

The 19 chain PNGs remain under:

```text
/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_imu_multi_action_revision_d_r3c_formal_20260816/r3c_chain_plots/
```

They are omitted to keep the checkpoint compact; each image SHA-256 is recorded in the manifest. The raw calibration ledger and 297 MB Q2 cache are not committed. Their absolute paths, sizes, recorded SHA-256 values, schemas, and allowlisted array patterns are metadata-only entries in the manifest.

No UWB/T4/Anchor payload, operator measurements, final_still, walk, golf, boxing, MP4, GIF, build output, cache, or Python bytecode is present.

```text
D0 = NOT_STARTED
JACOBIAN = NOT_STARTED
SOLVER = NOT_STARTED
FREEZE = NOT_CREATED
REPLAY = NOT_STARTED
RENDER = NOT_STARTED
FINAL_STILL = SEALED
GOLF = SEALED
BOXING = SEALED
WALK = SEALED
UWB = SEALED
```
