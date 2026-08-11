# Minimum known manual trajectory plan

1. Freeze one signed capture manifest that maps Anchor IDs A–H/0–7 to current-room XYZ (mm), coordinate handedness/up axis, per-anchor and tag delay, production solver Git commit/SHA and R provenance. Do not refit it from the trajectory.
2. Perform a bench signed-axis check on one representative Fusion PCB, then record each board-to-body mounting rotation. Yaw must come from an explicit initial heading/fixture, not gravity.
3. Before the trajectory, add an accelerometer attitude correction/ZARU policy and replay the frozen 38 table events. Preserve all thresholds/configs and require the event response to be evaluated against a predeclared bound.
4. Use one Fusion PCB first. Hold still for 60 s, translate it along a measured 1 m straight rail or taped line, stop 30 s, return along the same path, stop 60 s. Add one known 90-degree in-place rotation as a separate segment.
5. Record external ground truth at surveyed endpoints (and preferably continuous Vicon/optical truth), while retaining the same B306 TIMER2 raw contract. Repeat three times without changing filter parameters.
6. Gate on structural health first, then endpoint displacement, return closure, vibration false motion, UWB residual/NIS and repeatability. Only after the single-node gate should the experiment expand to ten nodes or human mounting.
