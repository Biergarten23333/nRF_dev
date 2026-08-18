# Phase 3-R IMU-only articulated pose architecture

The Phase 3 implementation is retained unchanged as history and classified
`REJECTED_AS_IMU_POSE_CORE` / `STAGE_INCOMPLETE_CORE_ESTIMATOR_NOT_IMPLEMENTED`.
Phase 3-R production code has zero imports from `articulated_v2`.

The rotation convention is `R_AB`: coordinates in B mapped into A. The sensor
calibration therefore enters the executable equation as
`R_WS = R_WI R_IS`, with `R_IS = R_SIᵀ`; a sensor quaternion is never renamed
as a segment quaternion.

Each IMU first enters a variable-dt 9-state error-state filter containing
orientation, three-axis gyro bias and three-axis accelerometer bias. Its one
robust accelerometer likelihood is the only consumer of each accelerometer
UID. A scheduled still interval updates gyro bias directly without a
retroactive orientation jump; at one attitude only the gravity-parallel
accelerometer-bias mode is updated, while the remaining modes stay
prior-dominated. Preparation and recovery buffers are not rest evidence. Ten
calibrated segment measurements then enter one 30-dimensional causal
manifold normal equation. Parent-child articulation, hinge-axis, multi-DOF ROM,
relative-heading and calibration-covariance factors place simultaneous blocks
in the same system and generate cross-segment posterior covariance.

Joint priors are distributions, not hard anatomical locks. Real qmt confidence
scales the hinge and heading information, robust losses bound incompatible
functional samples, and a backtracking line search accepts a manifold update
only when the executable factor objective decreases. The information matrix is
reconstructed at the accepted state.

The estimator outputs body-relative orientations and joint rotations. Global
yaw is an explicit gauge. Root L0 position is the fixed visualization origin,
not a measurement; world/root translation is unavailable. FK uses immutable
normalized lengths and cannot stretch.

## PIP and TransPose crosswalk

PIP motivates causal hierarchy followed by physical consistency. TransPose
motivates staged leaf/whole-body/joint representations. BioSpur adopts those
architectural ideas but not their trained networks: both official paths assume
a different six-sensor layout that includes a head sensor and consumes aligned
orientation/acceleration rather than ten raw independent-heading six-axis
streams. The adapter rejects fabricated head channels, zero filling, copied
sensors and any request to load the official six-point checkpoints.

VQF is executed as B0 and as an initializer, never as truth or a second factor.
qmt reset, hinge-axis and heading functions are executed where applicable;
their products remain confidence-gated conditional calibration or comparator
outputs. Neither package supplies external pose truth.

For each elbow/knee functional source action, the prior derived from that same
raw window is excluded from Production and the action initializes from B0. This
prevents qmt-derived products from double-counting their own source samples.
Axis confidence weights only the dominant-axis residual; heading confidence
weights only relative heading, and the two are recorded separately.

The temporal articulation model carries an explicit 27-dimensional compliance
state (nine joints by three tangent components) and nine 3x3 covariance blocks.
Both state and covariance are serialized in each action summary. B0 and B1
serialize segment and joint quaternions, normalized FK, uncertainty proxies,
quality metadata and gauge declarations. Every VQF run separately stores the
full quaternion, bias, bias uncertainty and rest arrays plus exact resampling
sample-UID lineage. Reported latency is algorithmic measurement age and
compute throughput; it is not mislabeled sensor-to-host latency.
