# Filter Design for Filtered Deployment Output

## Layer Separation

Do not put an EKF/UKF into the anchor-layout validation story. The deployment
filter belongs to the tag-output layer.

The intended processing stack is:

```text
Anchor self-calibration:
  inter-anchor ranges -> AutoPos layout solver -> fixed anchors + delays

Unfiltered tag baseline:
  tag-to-anchor ranges + fixed layout -> T1/T2/T3/T4 per-frame positions

Filtered deployment output:
  either per-frame positions -> external filter
  or raw tag-to-anchor ranges + fixed layout -> native filtered tag solver
```

## External Filters: T1--T4 + F

These filters consume an existing per-frame tag position stream. They are easy
to explain and are the cleanest first supplement because they do not alter the
range solver itself.

### F1: Causal Position KF, Constant Velocity

Purpose: deployment-friendly smoothing of per-frame tag positions.

State:

```math
x_t = [p_x, p_y, p_z, v_x, v_y, v_z]^T
```

Prediction:

```math
p_t = p_{t-1} + v_{t-1}\Delta t,\qquad v_t = v_{t-1} + w_v
```

Measurement:

```math
z_t = p_t + \epsilon_t
```

Use cases:

- First filtered deployment headline.
- Static repeatability/jitter reduction.
- Roto trajectory smoothing without changing the underlying tag solver.

Risks:

- It smooths output but cannot remove layout scale bias.
- It can add lag in dynamic/roto sequences if process noise is too small.

### F2: Robust Position KF

Same state model as F1, but the position measurement update uses innovation
gating and Huber-style downweighting.

Use cases:

- Static sessions with occasional bad frames.
- Dynamic trajectories with isolated spikes.

Notes:

- This is still a position-domain filter.
- It should report rejected/downweighted frame counts.

### F3: Adaptive Static/Dynamic Position KF

Same as F1/F2, but switches process noise based on detected motion:

```text
stationary -> small Q
moving     -> larger Q
```

Motion can be detected from position-step magnitude, range innovation, or IMU if
available. For the Erlangen static analysis, it should mostly stay in stationary
mode.

Use cases:

- Combined static + roto deployment story.
- Lower jitter while avoiding too much lag during motion.

Risks:

- More hyperparameters.
- Must not tune the switch threshold against OptiTrack error.

### F4: Fixed-Lag Position Smoother

Short fixed-lag smoother over per-frame positions, e.g. 0.5--2 s. This is not a
pure online output unless the lag is acceptable in the application.

Use cases:

- Optional deployment mode where small latency is acceptable.

Report status:

- Secondary only.
- Keep separate from strict causal filters.

### F5: Offline RTS Smoother

Rauch--Tung--Striebel smoothing over the whole capture.

Use cases:

- Offline upper bound.
- Figure showing how much of the noise is temporal jitter.

Report status:

- Appendix only.
- Not a real-time deployment claim because it uses future frames.

## Native Filtered Solver: T5 Family

T5 solvers estimate tag state directly from tag-to-anchor ranges, using the
fixed AutoPos layout and fixed anchor delays. In T5, the filter is not a layer
after multilateration; it is the tag solver.

### T5a: Range-EKF-CV

State:

```math
x_t = [p_x, p_y, p_z, v_x, v_y, v_z]^T
```

Measurement for anchor `i`:

```math
\rho_{i,t} = \|p_t-a_i\| + d_i + d_\mathrm{tag} + \epsilon_{i,t}
```

Jacobian row:

```math
H_i = [u_{ix}, u_{iy}, u_{iz}, 0, 0, 0],
\qquad
u_i = \frac{p_t-a_i}{\|p_t-a_i\|}
```

Use cases:

- Main native filtered deployment solver.
- Best comparison against `T4+F1`.

### T5b: Robust Range-EKF-CV

Same as T5a, but with robust range innovation handling:

- anchor-specific measurement sigma;
- Huber downweighting;
- chi-square/NIS innovation gating;
- optional persistent residual soft downweighting.

Use cases:

- Deployment candidate when one anchor/link has intermittent bad ranges.

This should be the likely long-term production candidate if it beats `T4+F1`.

### T5c: Adaptive Static/Dynamic Range-EKF

Same measurement model as T5a/T5b, but with process-noise mode switching:

```text
static mode  -> low Q, low jitter
dynamic mode -> higher Q, less lag
```

Use cases:

- Static sessions and roto sessions in one solver.
- More deployment-realistic than a one-mode EKF.

### T5d: Range-UKF-CV

Unscented Kalman filter with the same state and measurement model. This avoids
manual linearization but is more expensive and harder to justify if EKF already
works.

Use cases:

- Appendix comparison.
- Low-anchor-count or poor-geometry sensitivity check.

### T5e: Range-EKF with Common Bias State

Optional diagnostic state:

```math
x_t = [p_x, p_y, p_z, v_x, v_y, v_z, b]^T
```

Measurement:

```math
\rho_{i,t} = \|p_t-a_i\| + d_i + d_\mathrm{tag} + b_t + \epsilon_{i,t}
```

This can absorb common range bias, tag delay mismatch, or layout/delay coupling.
For that reason it must not be the first deployment headline.

Use cases:

- Diagnostic lower bound.
- Testing whether common range bias explains residual structure.

Report status:

- Appendix or diagnostic only unless externally calibrated.

## Recommended Experiment Order

1. `T4+F1` versus existing `T4` for `v4-io/all8` and `v4-io/noG`.
2. `T4+F2` if there are isolated per-frame spikes.
3. `T5a` versus `T4+F1`.
4. `T5b` versus `T5a`.
5. `T5c` for combined static/roto deployment.
6. `T5d` and `T5e` only as appendix/diagnostic variants.

