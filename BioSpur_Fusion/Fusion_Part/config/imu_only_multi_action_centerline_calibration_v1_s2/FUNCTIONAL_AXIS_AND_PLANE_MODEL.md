# Functional axis and plane model — S2

Functional coordinates are best-fit, non-clinical descriptions of dominant repeated motion. PCA is permitted only as an initializer and cluster diagnostic.

- Elbow curl uses only the detected curl phase and a combined Olsson gyroscope/acceleration residual with finite covariance.
- Pronation/supination uses only the detected forearm-rotation phase. The relative angular velocity is softly concentrated around the forearm longitudinal direction; perpendicular energy remains non-zero and is reported.
- Front high-knee uses pelvis–thigh relative angular velocity to estimate a hip-flexion-dominant functional plane and body-forward proxy. Shank verticality is a soft quality likelihood.
- Rear heel-to-butt uses thigh–shank relative motion for the knee best-fit axis, sign and large-flexion consistency. Thigh verticality is a soft quality likelihood.
- Squats provide bilateral cross-action consistency and mismatch diagnostics, not contact or absolute pelvis-height observations.

Every phase uses deterministic information balancing, measured synthetic noise plus non-zero floors, Huber loss, and explicit cross-axis/model-mismatch reports. No residual sets off-axis motion to exactly zero.
