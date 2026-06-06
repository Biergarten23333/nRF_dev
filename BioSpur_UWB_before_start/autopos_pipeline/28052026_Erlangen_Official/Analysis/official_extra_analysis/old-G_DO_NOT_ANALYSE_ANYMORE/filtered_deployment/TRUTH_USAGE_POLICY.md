# Truth Usage Policy for Filtered Deployment Analysis

OptiTrack is an independent evaluation reference.

Allowed:

- evaluating filtered tag positions against OptiTrack truth;
- computing final absolute-error metrics;
- plotting filtered-vs-truth residuals;
- reporting bootstrap confidence intervals using the already evaluated errors.

Not allowed:

- using OptiTrack truth in the filter update;
- fitting filter parameters to minimize OptiTrack error;
- choosing Q/R, gating thresholds, or solver variants by looking at OptiTrack
  test error and then reporting the same data as an independent validation;
- fitting a transform from tag truth to tag output;
- estimating a scale correction from OptiTrack and calling it deployment output.

Preferred parameter sources:

- raw UWB range residual statistics;
- unfiltered repeatability within static captures;
- declared engineering defaults fixed before evaluation;
- separate calibration runs, if available later.

