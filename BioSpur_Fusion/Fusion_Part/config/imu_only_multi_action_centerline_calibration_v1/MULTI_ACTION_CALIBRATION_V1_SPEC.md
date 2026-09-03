# IMU_ONLY_MULTI_ACTION_CENTERLINE_CALIBRATION_V1

This file freezes the implementation-level contract for the non-clinical, IMU-only, ten-node multi-action centerline calibration. It does not modify or supersede the historical Phase-A baseline.

## Frames and rotations

All rotations are active. `R_DST_from_SRC` maps coordinates from `SRC` to `DST`. `H` has subject-forward `+x_H`, subject-left `+y_H`, and up/opposite-gravity `+z_H`. `B_i` is node board frame. `N_i` is the Q2 gravity-aligned node navigation frame. Q2 publishes `R_N_i_from_B_i(t)`. The calibrated orientation is

`R_H_from_B_i(t) = R_H_from_N_i(yaw_i(t)) R_N_i_from_B_i(t)`.

There is one common global-yaw gauge. It is fixed by pelvis heading zero at the initial reference. Relative node headings remain estimated. No independent segment heading is fixed by fiat.

## Static and nuisance state

The shared static quotient state contains one unit longitudinal axis `a_B_i` per node, one transverse axis for torso and pelvis, four parent/child hinge-axis pairs, four discrete joint signs, four zero offsets, and nine relative initial node headings. Unit axes use two-dimensional tangent updates. Limb axial twist and hinge-center displacement are absent.

Each node also has a piecewise-linear yaw-correction spline with 1 s knots. The first delta knot is zero, separating the static initial heading from the slow nuisance drift. Knot increments have a non-zero covariance floor and are profiled nuisance variables, not serialized as action-specific extrinsics.

## Sampling

Q2 runs at native IMU rate. Functional axis identification uses timestamp-aligned native signals. Joint refinement uses 50 Hz samples after a fourth-order zero-phase Butterworth anti-aliasing filter with 20 Hz cutoff. Rotations use timestamp-based SO(3) Slerp. Rendering, if later authorized by all gates, uses 30 Hz.

## Published functional-axis residuals

For each parent/child hinge pair, the Olsson et al. residuals are implemented exactly in sensor frames:

`e_omega = (||omega_parent × h_parent|| - ||omega_child × h_child||) / (sqrt(2) sigma_omega)`

`e_acc = w_a (h_parent^T a_parent - h_child^T a_child) / (sqrt(2) sigma_a)`

`w_a = 1 / sqrt(1 + (||a_parent|| - ||a_child||)^2)`.

This is equivalent to Olsson's `w_omega=sigma_a/sigma_omega` when both residual families are expressed in acceleration units and then whitened by `sqrt(2)sigma_a`. The acceleration constraint is explicitly approximate: it assumes rotational/tangential acceleration projected onto the joint axis is small. Samples that strongly violate this approximation are downweighted by the published acceleration-norm term. No joint-center lever arm is estimated.

The relative-heading factor compares `R_H_from_B_parent h_parent_B` with `R_H_from_B_child h_child_B`. It is the 1-DoF specialization of the Laidig rotation/relative-orientation kinematic constraint. PCA is initializer and excitation diagnostic only.

Primary references:

- Olsson, Seel, Lehmann & Halvorsen, *Joint axis estimation for fast and slow movements using weighted gyroscope and acceleration constraints*, arXiv:1903.07353.
- Seel, Raisch & Schauer, *IMU-Based Joint Angle Measurement for Gait Analysis*, Sensors 2014, PMCID PMC4029684.
- Laidig, Weygers & Seel, *Self-Calibrating Magnetometer-Free Inertial Motion Tracking of 2-DoF Joints*, Sensors 2022, PMCID PMC9785932.

## Optimization

Every action/factor block computes a deterministic information score, rejects non-informative samples, retains 125–1000 samples for mandatory informative blocks, and whitens by measured or propagated covariance. The nonlinear solver is SciPy sparse trust-region least squares, one process/thread, fixed scaling/tolerances, Huber loss with `f_scale=1.5`, and deterministic ordering.

The fit uses one shared static state across all action blocks. Static poses are separate residual sets. Transitions remain in continuous Q2 and replay but have no action-specific pose residual. All sixteen elbow/knee common-sign combinations are scored against static extension and positive dedicated-flexion conventions. Multistart consists of nominal, deterministic `+15`, `-15`, `+30`, and `-30` degree tangent/heading perturbations.

## Observability and stop rules

The scaled physical Jacobian is evaluated after removing the common yaw, limb axial twist, and unestimated hinge-center gauges. Relative singular values at or below `1e-6` are null. Any additional physical nullspace blocks freezing and replay. Synthetic recovery is a hard precondition to real fitting.

## Freeze and replay

Canonical JSON uses sorted keys, compact separators, UTF-8, finite numbers, and newline termination. Its SHA-256 is written before replay. Replay runs in a separate process, verifies the expected SHA, does not load action windows, cannot change calibration parameters, and estimates the continuous timeline without label-triggered contact/root updates. Root x/y remain zero display gauges; root z is a label-independent floor/non-penetration display gauge and does not feed orientation.

## Claims and firewall

The product has fixed generic geometry, no absolute-position claim, no clinical joint-center/angle claim, and no axial-twist claim. UWB/T4, Anchor geometry, operator measurements, walk, and final_still remain sealed. Golf and boxing are post-freeze validation only and require immutable predeclared intervals.
