# Trunk forward and residual model — S2

The three trunk phases are detected from interior IMU activity: left rotation, right rotation, and forward bend/recovery. They are not equal-duration slices and are not represented by endpoint locks.

The pelvis functional lateral direction is estimated independently from bilateral pelvis–thigh high-knee relative motion. Its sign is cross-checked against the one-shot T-pose bilateral arm-line proxy. Functional forward is the right-handed cross product of this independently measured lateral direction and gravity/up. No torso heading is used to define this forward direction; a dependency audit rejects a circular graph.

For forward bend, the torso–pelvis relative angular velocity is softly concentrated around the independently obtained lateral direction. For left/right turn it is softly concentrated around the functional superior direction with opposite signed distributions. Neither perpendicular energy nor pelvis motion is set to zero. Each phase reports cross-axis energy and uses a finite mismatch covariance.

Full relative gyro propagation preserves the complete three-degree-of-freedom torso trajectory but cannot create heading information by itself. Heading information comes only from coupling the noncommuting interior motion to the independent pelvis functional frame and from time-resolved shared-point acceleration.

For an effective lumbar graphical proxy `q`, sensor-origin acceleration is

`a_origin_W = R_W_from_B f_B + g_W`,

and the proxy acceleration predicted from sensor `i` is

`a_q_W = a_origin_W + R_W_from_B ([dot(omega_B)]_x + [omega_B]_x^2) r_B_to_q`.

The torso and pelvis predictions are compared with a robust finite covariance. Generic proxy lever arms are physically bound to the functional frames and disclosed. If free lever-arm nuisance absorbs the old torso direction in a profile, observability fails.
