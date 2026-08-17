# Fusion Phase 2-R

This namespace implements the guarded Phase 2-R workflow for a targeted
ten-node capture. It separates three claims: anonymous node association,
mapping-conditional research calibration, and unsupported absolute metric/world
calibration.

Dataset reads are routed through `DataAccessBroker`. Before candidate freeze it
admits only exact metadata and the 19 literal promoted Phase 2 slices derived
from `CAPTURE_PLAN_FINAL.json`. Mapping-revealing paths, invalidated attempts,
neutral sway and Phase 3 holdout payloads are denied before `open`.

The association score is an exact global bijection over anonymous action-motion
signatures. Statistical qualification uses stratified complete-block bootstrap,
complete-block permutation nulls and leave-one-action/family solves. The H9
mounting declaration is permutation-symmetric and diagnostic-only; it contributes
zero additional accelerometer factors, preventing double counting.

Conditional calibration intentionally disables dynamic accelerometer and metric
UWB factors when differentiable translational motion and independent antenna
metrology are unavailable. Resulting gyro biases and functional axes are weak,
mapping-conditional research quantities. Full segment extrinsics, anatomy,
contact and world trajectory remain unqualified.
