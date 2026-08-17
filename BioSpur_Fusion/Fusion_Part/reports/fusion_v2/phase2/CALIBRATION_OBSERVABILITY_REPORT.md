# Phase 2 conditional calibration observability

The real D1 evidence does not support an authoritative continuous calibration. The registered coordinate gauges are global translation (three dimensions) and global yaw (one dimension). Removing only those gauges does not identify the remaining output-sensitive modes.

The data-only weakest modes are pelvis-versus-torso association, the two upper-arm assignments, each session `T_segment→IMU`, joint centres and functional axes, subject bone lengths, soft-tissue compliance, and device `T_IMU→antenna`. The latter is intentionally excluded from body co-fitting. No independent CAD/fixture transform with covariance was verified. Anchor geometry is a relative reference and retains `WORLD_SCALE_EXTERNAL_METROLOGY_NOT_PROVEN`.

Finite broad conditional covariance is preserved for all ten top-K hypotheses; it is not a substitute for rank. The tracked cross-covariance archive contains positive nonzero extrinsic diagonal blocks used only for the Phase 3 conditional-constructor perturbation probe. Data-only rank, prior-supported rank and chi-square repetition consistency cannot be honestly claimed because essential unilateral actions have no untouched complete repetition and subject/device metrology is absent.

Accordingly no numerical clipping, perfect hinge, zero compliance, invented anthropometry, historical mapping, Q1/T4 timeline or UWB mirror choice was used to remove these modes. The observability verdict is `UNSUPPORTED_OUTPUT_SENSITIVE_MODES_REQUIRE_CAPTURE_AND_METROLOGY`.

