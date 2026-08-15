# Human-motion model contract — S2

This is a synthetic-only, non-clinical articulated IMU model. Humans are not modeled as ideal robot mechanisms. Every functional axis and plane is a time-invariant best-fit calibration coordinate with finite covariance; instantaneous off-axis motion remains in the data and contributes to model-mismatch statistics.

The dynamic state contains full three-degree-of-freedom pelvis and torso orientations, full segment orientations, root translation/velocity display gauges, sensor bias, and slow relative-heading nuisance. A calibration label selects a physical residual family and balances information. It never creates a pose trajectory, freezes a passive segment, changes contact, resets a pose, or changes replay dynamics.

`Theta_shared` is common to every action. It contains observable sensor-to-segment axes/frames, best-fit elbow/knee and hip functional coordinates, sign and non-clinical zero conventions, identifiable relative-heading composites, supported generic proxy lever arms, and explicit mismatch scales. Limb axial-twist zero, clinical joint centres, absolute world heading, and absolute IMU-only root translation are not products.

The generic rendering skeleton is fixed and disclosed as non-subject-specific. It supplies proxy connectivity and lever-arm scale for the synthetic shared-point test; it is never called an operator measurement or anatomical truth.

## Functional factors

Olsson/Seel angular-rate and acceleration equations are finite-covariance robust residuals. They do not impose an exact per-frame hinge. Forearm pronation uses a soft cone about the estimated forearm longitudinal direction. High-knee uses pelvis–thigh relative motion and does not treat the shank as fixed. Heel-to-butt uses thigh–shank relative motion and does not produce a foot or heel position. Torso uses full relative SO(3), independent functional-frame evidence, and time-resolved shared-point acceleration with non-zero covariance.

## Gauges

One common global-yaw gauge and root translation gauge remain. Fixing their initial coordinates is bookkeeping, not a physical claim. The old torso board-axial/relative-heading split is not quotiented unless every published centerline output is invariant; S0 proved that it is not.
