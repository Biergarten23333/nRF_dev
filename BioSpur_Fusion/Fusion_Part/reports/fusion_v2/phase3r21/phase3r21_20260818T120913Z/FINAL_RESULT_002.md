# Phase 3-R2.1 final result — forward repair 002

Verdict: `FAIL_PHASE3R2_1_STATIC_POSE_SEMANTICS`. The complete real pipeline executed. This is neither a missing-execution result nor a timing hard stop, and it does not request recapture.

## Static pose semantics

Initial-still signed arm directions pass for B0, B1, and P. The worst arm segment is `upper_arm_right`: B0 `8.120/8.382 deg`, B1 `8.120/8.382 deg`, and P `7.826/7.971 deg` median/P95. Initial leg directions and natural elbow-flexion gates also pass. Final-still forearms-down passes.

T-pose B0 passes: upper-arm-left median `2.111 deg`, upper-arm-right `8.463 deg`; forearms are evaluated against the horizontal plane to permit natural elbow flexion. P fails because the coupled solution shifts the upper arms to `30.478 deg` left and `24.920 deg` right median even though the independent B0 frame is valid.

## Static stability

The ESKF recovery attitude-snap defect was repaired. The worst old `9.328 rad/s, 94.3x` result fell to `0.950 rad/s, 25.4x`, but the frozen `1.25x` gate still fails. The worst raw-confirmed row is `16_squat / upper_arm_right`: B0 median/RMS/P95 `0.01533/0.01944/0.03545 rad/s`, endpoint drift `1.557 deg`; P `0.20514/0.77868/0.94977 rad/s`, endpoint drift `33.994 deg`. Raw gyro P95 is `0.04992 rad/s`, so this is solver-injected motion rather than an operator-stillness claim.

## Continuous execution and evidence

- Real development IMU: `1,522,793` unique rows (`9,136,758` numeric IMU scalars), ten nodes, one numeric decode per UID.
- Frozen causal replay: `1,763,258` rows including `240,465` H rows; `74,010/74,010` scheduled/emitted/finite ticks; B0/B1/P each nonzero for all ticks.
- Action-boundary resets: `0`; boot resets: `0`; bias carryover: `true`; gap policy remains predicted/degraded/unavailable with growing covariance.
- One bundle from `498,103` FIT rows and `180` real calibration factors. Each of the 18 FIT-bearing actions contributes exactly 10 factors; validation rows used by the initializer: `0`. Bundle SHA-256: `dbe4b3e6252b00521d76492decbe663bd5962478c14aaa5dece14964e5e88693`.
- H00/H01/H02 use the same bundle and all produce finite, changing B0/B1/P trajectories. They remain `H_RETROSPECTIVE_CONTAMINATED` and make no accuracy/generalization claim.
- Historical time diagnostic: P95 `825.650949 us`, maximum `1019.184708 us`. Worst 2 ms differential pose bound: `2.4 deg`; C2CC pelvis: `2.4 deg`. Time remains a bounded correlated nuisance and is not the failure cause.
- UWB: co-located transport exposure `1`; semantic numeric decode, arrays, statistics/plots, factor/initializer consumption, and config influence all `0`.
- Synthetic scientific samples: `0`. Coverage, finite-state, per-window evaluability, bounded-time, H engineering, and initial/final semantic gates pass.

## Qualification and root cause

Independent structural completeness and mutation suites pass; detached pytest is `52 passed`; 44 animations were generated (22 formal-only, 22 full-context).

The remaining failure is in the coupled pose core and conditional calibration observability: elbow qmt confidence is only `0.281/0.352`, axial twist is unresolved, and P's functional-axis constraints shift otherwise-valid B0 arm semantics and inject rest motion. The time envelope is much too small to explain this. Existing reality is sufficient to demonstrate the software/model failure, so no recapture is requested.
