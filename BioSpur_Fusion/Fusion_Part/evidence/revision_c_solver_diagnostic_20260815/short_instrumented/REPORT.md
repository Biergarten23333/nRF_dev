# REVISION_C_SHORT_INSTRUMENTED_SOLVER_DIAGNOSTIC_V1

Revision C remains `FAIL_PREVIEW_CALIBRATION`. Start 4 was selected deterministically as the median terminal-optimality checkpoint. The trust state origin was a fresh initialization from its terminal x; historical trust state was not reconstructed.

Checkpoint/evaluator replay: `PASS`. Stock versus instrumented first-three-trial equivalence: `PASS` bitwise. The unique control trajectory recorded 8 accepted states and 8 trial proposals. No shadow candidate was fed back into that trajectory.

Directional derivative qualification: `JACOBIAN_OR_SCALING_QUALIFICATION_FAIL`. Diagnostic result: `JACOBIAN_OR_SCALING_QUALIFICATION_FAIL`. Recommended Revision-D action: `B_REPAIR_JACOBIAN_OR_SCALING_BEFORE_SOLVER_CHANGE`.

Initial-still/T-Pose factor gradients, window motion quality, actual default LSMR telemetry, tight-LSMR, augmented-dense and full-exact trust-region shadows are recorded in the JSONL artifacts. No weights, factors, gates, objective, parameterization or bounds were changed.

The diagnostic terminal state is non-adoptable and is not a calibration result. No freeze, replay, render, commit or push was performed. Golf, boxing, walk and final_still remained sealed; UWB/T4/Anchor and operator measurements were not read.
