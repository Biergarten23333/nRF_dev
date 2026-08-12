# Repair description

1. Added an explicit `FrameBinding.spatial_active` contract. Unbound real data disables p/v nominal propagation and the corresponding p-v-attitude/accelerometer-bias F/G blocks together. Bound synthetic/full operation remains 15-state.
2. Replaced Euler covariance transition with exact zero-order-hold transition: Van Loan for full coupling and closed-form Rodrigues attitude/gyro-bias transition in unbound mode.
3. Integrated continuous process noise over the interval, retaining gyro-noise, gyro-bias cross terms, and both bias random walks.
4. Replaced the fixed absolute PSD decision with a scale-aware backward-error bound and added condition/Cholesky diagnostics. This diagnostic change does not make the repair pass: repaired covariance remains Cholesky-factorable.

No state clipping, covariance clipping, epsilon-I loading, reset, restart, or noise retuning is used.
