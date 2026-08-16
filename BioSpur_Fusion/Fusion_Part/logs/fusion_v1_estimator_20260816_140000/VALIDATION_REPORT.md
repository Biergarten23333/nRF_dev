# Frozen validation

Estimator and subject-calibration hashes were frozen before opening validation. Golf and boxing remained unopened.

```json
{
  "WALK": {
    "converged": true,
    "cost": 2323.303134155266,
    "nfev": 142,
    "runtime_s": 21.285340309143066,
    "range_residual_median_m": -0.05689089095602373,
    "range_residual_robust_sigma_m": 0.4321349685484062,
    "root_axis_jitter_m": [
      0.6823749842168074,
      0.7573921287647306,
      0.10813230199420791
    ],
    "maximum_segment_length_variation_m": 0.0,
    "minimum_health": 0.0,
    "ranges": 6635
  },
  "FINAL_STILL": {
    "converged": true,
    "cost": 3050.225224437941,
    "nfev": 55,
    "runtime_s": 6.377437353134155,
    "range_residual_median_m": -0.037072960157070955,
    "range_residual_robust_sigma_m": 0.31675524936970584,
    "root_axis_jitter_m": [
      0.018699723534983573,
      0.009670552548959111,
      0.01596197854875039
    ],
    "maximum_segment_length_variation_m": 0.0,
    "minimum_health": 0.0,
    "ranges": 5303
  }
}
```

Both validation solves are finite; interpret residual spreads and jitter as internal consistency only because no external truth exists. No post-validation tuning was performed.
