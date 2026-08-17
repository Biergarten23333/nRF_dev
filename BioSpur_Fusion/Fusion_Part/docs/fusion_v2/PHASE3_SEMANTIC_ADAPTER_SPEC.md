# Phase 3 semantic adapter

The dependency direction is `EstimatorState/FactorGraph -> SemanticAdapter ->
CanonicalHumanState`. The adapter cannot configure factors or turn unavailable
degrees of freedom into zeros that appear valid.

All orientations use scalar-first unit quaternions and local right tangent
perturbations. `L0` is an operator-mapped session-local numerical realization,
not surveyed world. Global translation, yaw, possible common velocity, and
independent-heading weak modes remain explicit. Conditional position fields are
model-inferred scale outputs; world state and feet are unavailable. Every state
records measurement cutoff, output time, estimate kind, latency, mapping and
calibration references, gauges, validity, and degraded reasons.
