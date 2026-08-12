# Exact frozen-Q1 failure reproduction

The b50e7889 implementation was reconstructed without importing the repaired discretizer. Both frozen failure timestamps and sample indices reproduce exactly.

## BSF3C79

- Frozen fixed-threshold failure: `596.100000 s`, sample `119200`.
- First absolute negative eigenvalue: `595.552000 s`.
- First Cholesky failure: `595.400000 s`.
- First condition beyond `1/eps`: `81.200000 s`.
- Dominant maximum-eigenvector energy by block: `{"accel_bias": 2.43379378639e-34, "attitude": 2.39760384295e-09, "gyro_bias": 2.52766832401e-27, "position": 0.999515622708, "velocity": 0.000484374894259}`.

## BSFC2CC

- Frozen fixed-threshold failure: `664.005000 s`, sample `132800`.
- First absolute negative eigenvalue: `653.856000 s`.
- First Cholesky failure: `not observed before frozen stop`.
- First condition beyond `1/eps`: `118.558000 s`.
- Dominant maximum-eigenvector energy by block: `{"accel_bias": 2.27206145798e-34, "attitude": 2.41284639316e-09, "gyro_bias": 4.62673098965e-26, "position": 0.999511708419, "velocity": 0.000488289167849}`.

The fixed `-1e-9` test confounded absolute negativity with scale. At the reported event the matrix was already unresolvable/Cholesky-invalid because its condition exceeded float64 resolution; the negative eigenvalue was not a statistically meaningful negative variance relative to the approximately `1e19` largest mode.
The ignored NPZ trace contains every propagation field. No measurement rows exist because gravity, ZUPT, and T4 were all disabled during supported rotation; the trace records their Jacobian/gain fields as unavailable rather than inventing updates.
