# Body model v1

The implemented clean primitives use a pelvis-rooted ten-segment tree: pelvis,
torso, bilateral upper arm, forearm, thigh and shank. Segment poses and sensor
positions can only be generated through FK. Capture-level segment lengths are
immutable during a trajectory. Shoulder, hip and trunk orientation states are
multi-DOF. Elbow and knee are intended to use dominant-axis distributions with
finite secondary rotation. Joint centres and sensor attachments are soft,
uncertain quantities rather than exact coincidence constraints.

Implemented and tested: SE(3) composition, SO(3) interpolation, tree FK, and
segment-length invariance. Not yet estimated: subject lengths, sensor
extrinsics, joint centres or functional-axis dispersion. No historical body
calibration product is an input.

