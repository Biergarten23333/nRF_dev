# Phase 1 Validation Gates

| gate_id | status | blocking_next_phase | evidence |
| --- | --- | --- | --- |
| G1_frame_gravity | PASS | False | frame_conventions.md exists=True; L0/T11 P95=0.000 mm |
| G2_drift_from_L_properties | PASS | False | sensors.yaml exists=True; L2/T11 endpoint drift median=27512.8 mm |
| G3_range_bias_policy | PASS_OR_LIMITED_PROTO | True | range_bias_policy.md exists; T6 raw availability audited but solved-position proxy is used |
| G4_fixed_time_alignment | PASS | False | all Phase 1 rows use official aligned sample grid; no beta_s refit |
| G5_noise_seed_repeats | PASS_DEBUG_SINGLE_SEED | True | recorded stochastic seeds=34; final claims still require multiseed |
| G6_multimetric_verdict | PASS | False | summary metric columns present=True; figure_count=245 |

Status semantics:

- `PASS`: usable for the current phase gate.
- `PASS_OR_LIMITED_PROTO`: acceptable for Phase 1 prototype only; blocks broad Phase 2.
- `PASS_DEBUG_SINGLE_SEED`: acceptable for debug/screening only; final rows need repeated seeds.
- `FAIL`: stop before using this run for phase progression.
