# Dependency policy

Dependency is raw measurements + future frozen calibration/model → EstimatorState/FactorGraph → SemanticAdapter → future CanonicalHumanState → serializers. Reverse flow is forbidden. Static import scan, runtime denylist, loader allowlist and entrypoint file/call trace are mandatory. V1/Q1/T4/map/calibration/pose/UltraInertialPoser are denied.
