# Ten-node body-calibration offline analysis

Top-level verdict: `BODY_ASSIGNMENT_AMBIGUOUS`.

The capture and canonical `UWB_TAG_T4` replay are usable, but the anonymous anatomical assignment is not uniquely identifiable without external truth or surveyed lengths. No visually plausible skeleton is promoted as proof.

## Provenance

- AutoPos/V4-io binding commit: `87d9027cc368cd05e707dd3a564e4c28b9c505ee`
- canonical solver: `UWB_TAG_T4` (lineage `3acfeeda5fede3b157081549fdf1a5f4ca939a82`)
- deployment: `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/deployments/current_room_autopos_20260811_183541`
- loaded geometry: `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json`
- layout SHA-256: `20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1`
- reflected intermediate explicitly rejected.
- A-H identity map: `UWB_GEOMETRY_PROVENANCE.json`

## Component verdicts

- CAPTURE_INTEGRITY: `PASS`
- UWB_REPLAY_READINESS: `PASS`
- IMU_Q1_READINESS: `PASS`
- BODY_ASSIGNMENT_IDENTIFIABILITY: `AMBIGUOUS`
- EXTRINSIC_IDENTIFIABILITY: `CONDITIONAL_GRAVITY_ONLY`
- FUSION_NUMERICAL_INTEGRITY: `PASS`
- HELDOUT_GENERALIZATION: `CONDITIONAL`
- IKFK_SUFFICIENT_FOR_MVP: `NO`

Best-vs-second assignment cost margin: 0.010996921; both are diagnostic, not frozen anatomical truth. Q0 repaired-Q1 attitude and B0 canonical T4 ran. The separately labelled auxiliary T4-CV diagnostic used Joseph updates but is **not** Q1 fusion. F1 was blocked because the relative V4-io frame is not a surveyed navigation/gravity frame; F2/F3 and anatomical angles were additionally blocked by assignment, yaw, and segment-length ambiguity. This prevents a false identity frame binding. Walk and final-still were opened only after freeze `9fdebc9a2ef08600480eb7694bc6d76a2687f23eec2d8efd7928ee7fcbc65908` and were not used for fitting. Metrics are self-consistency, not absolute accuracy.

Raw SHA was verified before and after. No hardware interface was accessed.
