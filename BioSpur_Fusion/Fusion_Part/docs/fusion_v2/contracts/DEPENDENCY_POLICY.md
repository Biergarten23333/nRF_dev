# Dependency policy

Raw measurements and future frozen calibration/model flow to internal state/factors, then semantic adapter and serializers. Reverse flow is forbidden. Q1, T4, fusion_v1 estimator, historical mapping/calibration/pose and UltraInertialPoser are rejected by static import, runtime file-access, loader allowlist and entrypoint coverage checks.
