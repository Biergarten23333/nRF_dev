# Estimator implementation

State: pelvis/root XYZ at 100 ms knots. Segment orientations are SO(3)-interpolated Q1 evidence relative to the static calibration orientation; all nine non-root sensor positions are generated recursively from fixed-length articulated edges. Root translation uses linear interpolation at each individual range time. Factors are standardized raw UWB ranges with pair-specific sigma and Cauchy loss, plus root velocity/acceleration temporal residuals. Pair health attacks rapidly and recovers slowly. SciPy sparse `least_squares` performs three-start static and single-start dynamic solves.

This reduced formulation does not optimize orientation corrections as states and has no uncertainty propagation; those omissions are part of the invalid verdict.
