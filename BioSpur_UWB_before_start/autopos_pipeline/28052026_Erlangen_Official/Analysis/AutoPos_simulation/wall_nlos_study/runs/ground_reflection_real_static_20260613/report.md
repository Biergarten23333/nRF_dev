# Ground-Reflection Real-Static Consistency Check

This is a first-order consistency check, not validation. Parameters are swept and not tuned to the measured vertical-error magnitudes.

Robust flags over all 36 swept combinations:

- `vertical_dominance_all`: True
- `low_worst_all`: False
- `measured_order_low_high_mid_all`: False
- `sign_match_low_all`: False
- `sign_match_all_tiers_all`: False

Measured signed vertical medians (UWB minus Vicon), mm:

- low: -135.0 (negative)
- mid: -32.5 (negative)
- high: 85.4 (positive)
