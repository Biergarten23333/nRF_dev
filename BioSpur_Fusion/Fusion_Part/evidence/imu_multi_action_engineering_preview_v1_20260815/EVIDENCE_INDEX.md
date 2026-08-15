# IMU Multi-Action Engineering Preview V1 — Audit Checkpoint

This snapshot makes the failed Attempt 7, Derivative Qualification V2, and the
failed Attempt8-V2 single-start run directly reviewable from Git. It does not
claim a calibration pass.

## Frozen outcomes

- `ATTEMPT7_ORIGINAL_GATE = FAIL`
- `ATTEMPT7_JV = PASS`
- `ATTEMPT7_SCALAR_COST_METRIC = NUMERICALLY_ILL_CONDITIONED_NEAR_ZERO_DERIVATIVE`
- `ATTEMPT7_ADOPTABLE = false`
- `DERIVATIVE_V2_QUALIFICATION = PASS`
- `GLOBAL_DATA_ONLY_PROFILED_RANK = 55/56`
- `GLOBAL_DATA_PLUS_PROTOCOL_PRIOR_PROFILED_RANK = 56/56`
- `ATTEMPT8_V2_SINGLE_START = FAIL_SINGLE_START_NOT_CONVERGED`
- `NEXT_STAGE_AUTHORIZED = false`

The data-only rank deficiency is intentionally reported separately from the
rank obtained after adding protocol-prior rows. Prior-derived rank is not
represented as data-only observability.

## Directory map

- `phase_a_compact/`: human quasi-static Q2, mapping, static-pose, uncertainty,
  and data-access evidence. The 284 MiB Q2 cache is SHA-bound but not committed.
- `attempt7_original_freeze/`: byte-identical original Attempt 7 artifacts,
  reconstructed source snapshot, full residual/Jacobian/weight/gradient NPZ,
  row metadata, and immutable FAIL disposition.
- `derivative_v2_synthetic/`: frozen V2 specification and independently
  resolved mixed absolute+relative tolerance.
- `derivative_v2_preflight/`: four-point zero-iteration Jacobian/cost-gradient
  qualification, negative controls, and separate data-only/prior information.
- `derivative_v2_revision_source/`: exact isolated source used by the final
  shared-Jacobian single-start run.
- `attempt8_v2_single_start/`: deterministic-x0 result and forced stop after one
  start. No multistart, freeze, replay, or rendering occurred.

## Data firewall

No raw calibration ledger, raw capture, Q2 cache payload, UWB/T4/Anchor data,
operator measurements, golf, boxing, walk, or final-still payload is committed.
Their local provenance and hashes remain recorded in the manifests.

`GOLF_STATUS = SEALED`

`BOXING_STATUS = SEALED`

`WALK_STATUS = SEALED`

`FINAL_STILL_STATUS = SEALED`

`UWB_STATUS = SEALED`
