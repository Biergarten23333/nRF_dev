# Phase 3-R2.1 final result

Verdict: `FAIL_PHASE3R2_1_STATIC_POSE_SEMANTICS`. The real pipeline completed; this is not a missing-execution or timing hard-stop result.

- Initial formal still is not semantically correct: all four B0/P arm axes are about 107–129 degrees from gravity-down rather than within 15/25 degrees.
- Worst raw-confirmed recovery rest is `04_shoulder_left` / `upper_arm_right`: B0 P95 0.052002 rad/s, P P95 4.180065 rad/s, P/B0 ratio 77.443, P endpoint drift 15.420 deg. Classification: `COUPLED_SOLVER_STATIC_MOTION_INJECTION`.
- Continuous session: 1,763,258 real rows, 74,010/74,010 scheduled/emitted ticks, action resets 0, boot resets 0, bias carryover true.
- One bundle: 498,103 FIT rows, 180 real calibration factors, 18/18 actions, SHA-256 `d44a88ca0e063a3a0efa7a225fd4bda6824d455c2aedbbef1baad9532d92c349`.
- H00/H01/H02 ran with that same bundle and passed engineering finite/changing-response checks; they remain contaminated retrospective diagnostics.
- Time is bounded correlated nuisance. The historical 825.651 us / 1.019 ms diagnostic produces a worst 2 ms differential endpoint bound of 2.400 deg; C2CC pelvis bound is 2.400 deg. It does not change the failing semantics verdict.
- 44 animations: 22 FORMAL_ONLY and 22 FULL_CONTEXT.
- Independent completeness and mutation suites passed; detached pytest: 49 passed.

Scientific root cause: the real FIT-derived sensor-to-segment calibration/frame-sign solution is not semantically valid, and the coupled solver additionally injects motion during raw-confirmed rest. Existing reality is sufficient to demonstrate this failure; no new capture is requested by this result.
