# Phase 0 Validation Gates

| gate_id | status | blocking_next_phase | evidence |
| --- | --- | --- | --- |
| G4_fixed_time_alignment | PASS | False | R01-R17 pairing manifest and official beta_s alignment table |
| G6_multimetric_verdict | PASS | False | baseline summary emits P50/P95/deltaR/radius metrics and PNG figure index |

Status semantics:

- `PASS`: usable for the current phase gate.
- `PASS_OR_LIMITED_PROTO`: acceptable for Phase 1 prototype only; blocks broad Phase 2.
- `PASS_DEBUG_SINGLE_SEED`: acceptable for debug/screening only; final rows need repeated seeds.
- `FAIL`: stop before using this run for phase progression.
