# GENERIC_TEMPLATE_MOTION_DEMO_V1_1

## Verdict

`GENERIC_TEMPLATE_MOTION_DEMO_V1_1_FAIL`

The preview is visibly data-driven and all mandatory responsiveness and ablation checks pass, but the predeclared model-to-data gate does not. Overall accepted-observation residual median is 0.263214767 m against a 0.250 m limit; P95 is 0.748463030 m against a 0.800 m limit. Every clip is therefore labelled `LOW_CONFIDENCE_MODEL_MISMATCH`, even when its action-specific motion-transfer checks pass.

V1 remains immutable historical evidence. Its interpretation is `SOFTWARE_INTEGRITY_PASS`, `MOTION_FIDELITY_NOT_TESTED`, and `PREVIEW_REJECTED_BY_OPERATOR`. V1 used pelvis UWB for root translation; limb UWB affected normalized segment directions with at most approximately 20% nominal weight and did not directly constrain joint trajectories.

## Data path and firewall

The eleven native actions use the immutable calibration typed ledger, canonical `UWB_TAG_T4`, and repaired Q1. Golf and boxing use only their SHA-bound `custom_validation` intervals from `ACTION_SEGMENTS.json`, selected from the frozen canonical T4 and repaired-Q1 feature frontends. Their source SHAs are:

- T4: `6cb94dab2d54b57d754e0b905355ffeb9d234241d41e8a7ed833dab18a177366`
- Q1: `6d01fa6ea42f3f04fac7288d5889b50372246950e1259740ed294a988474a479`
- action metadata: `aa49bf149c34eb0669a176da3406a7746456666d9f545193dd2255c379be7d4c`

Walk and final-still rows were not selected or rendered. Operator measurements were not read. No raw capture was modified.

## Numerical audit

- All generic lengths remained immutable; maximum numerical error was `2.78e-16 m`.
- Topology remained connected, finite, and left/right identity stayed fixed.
- 171 pre-estimator UWB observations were rejected with exactly zero estimator weight.
- Constant-IMU, shuffled-IMU, shuffled-UWB-identity and zero-UWB ablations changed the output by 0.1391, 0.1008, 0.2133 and 0.4927 m RMS respectively; all exceeded the frozen 0.03 m data-driven threshold.
- Golf wrist-target/output correlations were 0.922 and 0.899; boxing correlations were 0.928 and 0.871.
- Two complete analysis replays were byte-identical.

## Preview inventory

Thirteen individual MP4 clips cover initial still attempt 2, T-pose, arms, left elbow, right elbow attempt 2, left/right knee, left/right heel, squats, trunk, golf swing and boxing. A chronological combined MP4 and GIF contain all thirteen. Each frame shows the action-local timestamp, status, generic skeleton, accepted UWB displacement targets and zero-weight rejected targets, plus the non-clinical disclaimer.

The media are H.264/yuv420p at 1280x720 and 15 FPS under the frozen V1.1 rendering gate. The combined GIF is 640x360. `MEDIA_SHA256SUMS.txt` closes the delivery hashes.

No walk release request was created because the calibration preview verdict is not PASS.
