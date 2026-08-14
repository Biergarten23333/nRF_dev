# Ten-node body Fusion V2 offline report

Top-level verdict: `BLOCKED_FRAME_OBSERVABILITY`.

The historical result is formally split into `BODY_MAPPING_CONSTRAINED_PASS`, `TIME_ALIGNMENT_NOT_PROVEN`, `F1_BODY_FUSION_FAIL`, and `CURRENT_F1_ANIMATIONS_INVALID_AS_FUSION_EVIDENCE`. This V2 replay closes the independent strict time gate, but does not rehabilitate historical F1.

Gate 0 passes on Listener-backed 120 ms epochs. Typed ingest closes exact observation accounting and never uses Master arrival as measurement time. Canonical UWB_TAG_T4 and Q1 attitude/preintegration ran only through the calibration `trunk` stop. The physical `R_N<-V4` plus ten sensor-to-segment extrinsics remain rank-deficient: per-sensor gravity does not close independent yaw gauges, and T-Pose/body display frame H is not promoted to N. The real joint estimator therefore did not run, calibration was not frozen, and held-out walk/final-still measurements were not opened by calibration or estimation.

The articulated estimator and immutable-geometry construction are qualified only by deterministic synthetic tests. No absolute-accuracy or clinical-angle claim is made. No animation was generated and no hardware interface was accessed.
