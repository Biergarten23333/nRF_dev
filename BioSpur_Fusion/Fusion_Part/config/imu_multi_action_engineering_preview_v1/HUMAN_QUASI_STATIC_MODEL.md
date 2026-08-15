# Human quasi-static frontend model

For node `i` and accepted sample `t`, the frontend computes continuous evidence
`w_i(t) in [0,1]`.  Let `omega` be gyro rate after a preliminary robust center,
`a` acceleration, `g` nominal gravity, `j` filtered jerk, and `domega` the
local gyro-rate change.  Each feature uses a smooth Cauchy confidence:

```text
C(x;s) = 1 / (1 + (x/s)^2)
w_local = (C(|omega|;s_w) C(|domega|;s_dw)
           C(||a||/g-1;s_a) C(j;s_j))^(1/4)
```

`w_local` is smoothed over a declared temporal support.  Cross-node low-frequency
agreement contributes only a bounded multiplier in `[1-c,1]`; it can never set
another node's evidence to zero.  Input validity remains a separate binary
transport/accounting fact.

Gyro bias is a per-node weighted Huber M-estimate over low-dynamic evidence from
the initial neutral window, the independent T-pose window, and signal-selected
low-dynamic returns.  These intervals share a sensor bias but are never averaged
into a body-pose anchor.  Iteratively:

```text
b <- sum_t w_i(t) h(||omega-b||/sigma) omega / sum_t w_i(t) h(...)
```

Gravity direction is estimated separately for neutral and T-pose using a robust
weighted spherical mean of normalized acceleration.  Kish effective sample
size is capped by the declared correlation-time duration.  Bias covariance and
gravity angular uncertainty use this correlation-adjusted effective sample
size.  No whole-window fallback exists.

The three independent products and gates are:

1. `SENSOR_DATA_VALIDITY`: source accounting, monotonic timestamps, finite
   values, rate, and gaps.
2. `BIAS_AND_GRAVITY_ESTIMATION_WITH_UNCERTAINTY`: effective sample size,
   bias standard uncertainty, gravity angular uncertainty, and finite Q2.
3. `NEUTRAL_POSE_REFERENCE_QUALITY`: robust orientation dispersion of the
   neutral window.  T-pose quality is reported and gated independently.

Failure due to insufficient evidence is
`BLOCKED_Q2_BIAS_OR_GRAVITY_UNCERTAINTY_TOO_LARGE`, never operator fault.  Q2
uses fixed robust bias plus confidence-weighted gravity corrections.  Quaternion
semantics are scalar-first Hamilton `q_NB`, active board-to-navigation rotation;
body gyro increments multiply on the right.  Common heading remains an
unobserved deterministic display gauge.
