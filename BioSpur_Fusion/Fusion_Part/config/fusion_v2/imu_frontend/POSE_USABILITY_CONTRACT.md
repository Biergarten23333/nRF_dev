# Pose usability prerequisites for later Phase 3

This contract does not claim that Phase 1 produces body pose. A later Phase 3 must directly instrument ten carrier segments; any additional output must be explicitly marked virtual/model-inferred. Target initialization is at most 10 s of qualified low-dynamic data. Target output is 100 Hz with less than 50 ms processing latency. Qualified motion classes, outage horizons and error limits require new evidence and are presently `UNQUALIFIED`.

Availability means a timestamped output with valid input lineage, finite conditional covariance, a separate bounded timing-sensitivity envelope, and explicit weak-mode/degraded flags. Independent node yaw gauges may drift and cannot be silently aligned. Future Phase 3 aggregation must predeclare all-node and worst-segment pass rules before held-out evaluation. D2, D3 and future Phase 3 outcomes may not retroactively redefine usability.

`NODE_TO_BODY_MAPPING_UNKNOWN`, `NO_BODY_POSE_CLAIM`, `NO_ARTICULATED_POSE_CLAIM`.
