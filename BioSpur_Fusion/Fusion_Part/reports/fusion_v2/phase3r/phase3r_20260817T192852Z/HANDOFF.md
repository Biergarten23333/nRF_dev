# Phase 3-R handoff

Primary verdict: `PASS_PHASE3R_IMU_ONLY_ARTICULATED_POSE_ENGINEERING_BASELINE`.

This is an operator-mapped, session-scoped, IMU-only engineering baseline. It
does not estimate metric root translation, world XYZ, contact, clinical joint
angles or external accuracy. Global yaw remains a declared gauge. Phase 4 was
not started.

The external evidence root contains 22 machine-readable production trajectories,
22 complete B0/B1/P NPZ bundles, 22 full VQF state and exact-lineage bundles,
17 required triptych GIFs, the real calibration/qmt record, master summary,
data-access ledger/summary, evidence manifest and raw qualification JSON. The
committed representative PNG samples T-pose, left hip,
squat, walk, boxing and golf; it is illustrative, not truth.

Before any accuracy/generalization claim, collect a new independent evaluation
session with external pose truth. Keep H00/H01/H02 classified
`CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC`; do not promote them to holdout. Do not
start UWB fusion or Phase 4 from these reports alone.
