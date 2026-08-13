# Convention specification

All vectors are 3×1 column vectors. `q_NS` is a scalar-first Hamilton quaternion. `R_NS(q)` is the active rotation implemented by `q v q*`, mapping sensor/board `S` into the gravity-aligned local navigation frame `N`. Body-frame gyro increments right-multiply the nominal quaternion: `q_NS <- q_NS * Exp(omega_S dt)`. The Q1 attitude error is right-multiplicative.

The accelerometer reports specific force. With `g_N=[0,0,-9.80665] m/s²`, inertial acceleration is `a_N=R_NS f_S+g_N`; stationary upward specific force therefore cancels negative gravity. `R_V4_N` is an active proper rotation and maps local navigation vectors into V4. The fitted residual is `R_V4_N delta_p_N - delta_p_V4` (directional diagnostics normalize both operands only after the signed chronological vectors exist).

T4 positions use the canonical capture-bound `V4IO_LAYOUT.json`. T4 displacement is always `p_end-p_start` in chronological hardware time. No endpoint is geometrically reordered, and no absolute value, dominant-component sign, cluster orientation or reverse-stroke sign copying is allowed. IMU uses `base_us+delta_us`; UWB uses `strobe_us`; both are expanded B306 hardware timestamps. Host monotonic time brackets operator actions only.

The Wahba cross-covariance is `target.T @ source`, so SVD returns a source-to-target active map. A determinant correction is mandatory for a proper rotation. An unconstrained determinant -1 result is diagnostic evidence of a reflection; it is never silently accepted as an attitude.
