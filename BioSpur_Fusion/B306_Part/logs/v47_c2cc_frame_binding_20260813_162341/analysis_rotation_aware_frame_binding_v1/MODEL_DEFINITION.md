# Rotation-aware model

Each mount is initialized independently from one second of stationary gyro and gravity. Repaired Q1 propagation supplies the full time-varying `R_N<-S(t)` on actual B306 timestamps. Each action is filtered independently with a five-sample median stage and a frozen 1 Hz second-order low-pass; alternating T4 reversals define short endpoint-ZUPT displacement constraints. No T4 second difference is used.

Vertical T4 strokes are signed by the gravity-aligned IMU displacement and robustly estimate physical up. A proper `R_V4<-N` maps gravity exactly and fits the remaining yaw from the horizontal displacement lines. Unsigned line residuals test axis compatibility; signed residuals and per-action counts test yaw polarity and observability. A candidate transform that fails either test is diagnostic only.

The primary coincident-origin calculation is not a physical zero-lever assertion. The external Streichholz CAD proves only component reference locations and a planar envelope. Every endpoint constraint is therefore subjected to a conservative 50 mm full-3D lever sensitivity bound. Validation and rotation-only blocks cannot tune any parameter.
