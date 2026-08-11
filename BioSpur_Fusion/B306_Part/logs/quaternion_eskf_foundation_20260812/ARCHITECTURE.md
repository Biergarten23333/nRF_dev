# Q1_T4_ESKF architecture

Q1 has a nominal `[p_N,v_N,q_NB,b_a_B,b_g_B]` state and 15-dimensional `[δp,δv,δθ,δb_a,δb_g]` error covariance. It integrates every audited IMU sample using actual B306 hardware timestamps. Bias-corrected gyro propagates a real quaternion; bias-corrected specific force is mathematically available for N-frame propagation. Process covariance includes accelerometer/gyro white noise and both bias random walks.

Stationary gravity and zero velocity are explicit Kalman measurements. T4 position is an asynchronous Kalman measurement only after V4↔N binding. All corrections use Joseph covariance form and a right-error reset Jacobian. Ordinary motion transitions never reset the filter.

Current real-data disposition is an attitude foundation plus the established S2P/T4 position-domain comparison. It is not fully coupled inertial/UWB Fusion because N and V4 are not physically bound.
