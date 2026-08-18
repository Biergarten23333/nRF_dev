# Phase 3-R2 frame algebra

`R_AB` is an active rotation that maps coordinates in frame B into frame A.
Quaternions are scalar-first. Multiplication follows frame composition. The
runtime orientation relation is

```text
R_WI : I -> W
R_IS : S -> I
R_SI = inverse(R_IS)
R_WS = R_WI R_IS
q_WS = q_WI * q_IS
```

The filter and articulated solver use right-local perturbations:
`q' = q * Exp(delta)`. Therefore an IMU-frame orientation covariance must be
carried into the segment-local tangent with `Ad(R_SI)`, then combined with the
calibration covariance. `compose_right_covariance` implements that expression
and keeps the optional cross term. A 90-degree non-diagonal anisotropic golden
test rejects omission, inverse direction, multiplication-order, and frame-leak
mutations.

Global yaw is a declared L0 convention, not a data-observed world heading.
Root world translation is unavailable. Rendered link lengths are conditional
model dimensions and are not measured anthropometry.
