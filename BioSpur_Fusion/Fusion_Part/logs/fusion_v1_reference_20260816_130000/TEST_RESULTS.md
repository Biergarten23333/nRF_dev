# Test results

`python3 -m pytest fusion_v1/tests -q`: **11 passed in 0.35 s**.

Coverage includes raw framing/schema primitives, EOF quarantine, IMU timestamp
semantics, affine common-clock mapping, individual UWB half-round-trip timing,
SE(3) interpolation, articulated length invariance, robust loss and health
hysteresis. No end-to-end estimator test is claimed.

