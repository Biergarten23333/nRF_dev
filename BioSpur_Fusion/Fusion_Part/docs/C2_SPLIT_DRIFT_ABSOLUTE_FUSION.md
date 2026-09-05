# Capture2 split drift/absolute raw-range fusion

## Ownership

The nominal pelvis state remains `RootState = [p_world, v_world, b_accel_sensor]`.
The 200 Hz pelvis IMU owns continuous propagation. UWB never enters as an old
solved position: every accepted A--H factor is a raw range evaluated at the
Beacon-only B306 TIMER2 epoch `strobe_us + t_round_us[anchor] / 2`.

Three correction channels have disjoint nominal-state ownership:

1. The absolute range channel updates only `p_world`. It uses all valid raw
   links, persistent non-negative node--anchor bias priors, one-sided positive
   NLOS influence, a gain of 0.20, and a 20 mm per-sweep limit. It cannot
   directly change velocity or accelerometer bias.
2. The fixed-lag drift channel updates `v_world` and, only when six scaled
   modes are observable, `b_accel_sensor`. It cannot directly move position.
3. The optional contact channel updates only `v_world`. It selects at most one
   near-stationary foot, uses 0.20 s side dwell and 30 mm height hysteresis,
   and has no foot-position, ground-height, or dual-foot lock factor.

Covariance marginal replacement preserves the old conditional covariance of
the untouched state block. This avoids an indefinite covariance while keeping
nominal-state ownership explicit.

## Fixed-lag drift observation

For one persistent `(node, anchor)` link, the drift channel compares the
current innovation with one observation 0.32--0.72 s earlier. Before using the
difference, it removes changes already owned by the other channels:

```text
delta_r_drift = r_now - r_old
              + (range_bias_now - range_bias_old)
              + u_now^T (cumulative_absolute_dp_now
                         - cumulative_absolute_dp_old)
```

The local error-state row is

```text
delta_r_drift ~= (dt * u_now^T) delta_v
                - (0.5 * dt^2 * u_now^T R_world_sensor) delta_b_accel
```

Rows retain the minimum of the two raw-range robust weights and receive a
second temporal Huber guard. Dimensionally scaled singular values determine
the observable rank. Rank three authorizes a velocity-only correction; an
accelerometer-bias correction requires rank six. The H01 run produced rank
three throughout, so its bias delta is exactly zero rather than a
regularization-driven estimate.

An accepted correction restarts the lag window because the old residual
linearization is no longer consistent with the corrected state.

## H01 bounded evidence

The implementation-failure run is retained at
`logs/c2_h01_split_drift_absolute_20260903_154606`. It allowed rank-three data
to alter accelerometer bias and produced 2.691 m endpoint displacement. That
failure established the rank gate.

The position-only comparator is
`logs/c2_h01_absolute_ablation_g020_s002_20260903_154851`: 1.508 m endpoint
displacement, 0.968 m median pelvis Z, and no velocity/bias UWB update.

After removing the range-bias and absolute-position ledgers, the final bounded
20 mm/s drift run is
`logs/c2_h01_split_final_off_20260903_155511`: 1.303 m endpoint
displacement, 0.963 m median pelvis Z, and zero accelerometer-bias correction.

The switchable contact comparator is
`logs/c2_h01_split_final_contact_20260903_155710`: 1.007 m endpoint
displacement, 0.992 m median pelvis Z, minimum proxy ankle Z of +0.029 m, and
22 accepted-side switches. This remains a diagnostic because the ankle input
is the frozen display proxy and H01 has no external trajectory truth.

Each NPZ preserves measured raw ranges, per-link epochs and sigmas, range-bias
priors, prefit/postfit innovations, robust weights, absolute position steps,
fixed-lag source rows, ranks, conditions, scaled singular values, corrections,
and optional contact decisions. `scientific_pass` remains false.
