# REVISION_C_CHECKPOINT_ONLY_SOLVER_AUDIT_V1

Revision C remains `FAIL_PREVIEW_CALIBRATION`; the primary blocker remains `SOLVER_NOT_CONVERGED`. This audit performed zero solver iterations and did not alter the historical result.

## What is verified

All five soft and terminal checkpoint manifests reload and verify. `RUN_FREEZE.json`, its binding, current runtime source/config/template hashes, the calibration-ledger byte SHA, row-manifest SHA, validity-audit SHA, parameter order, units, unit scales, unbounded bounds, residual arrays, soft-L1 weights, robust-effective Jacobians, robust gradients and SciPy-style optimality are internally consistent. The calibration ledger was hashed as bytes only and was not parsed as NPZ payload.

The saved robust-effective Jacobians are 6536 x 180. Direct SVD (never J^T J) gives condition numbers about 79.6–79.7 and machine rank 180/180. No frozen Revision-C relative singular-value threshold was persisted, so a frozen-threshold rank claim is unavailable. There is no machine-precision null, and the bottom-20 subspaces are consistent across starts. A product-changing physical-null test cannot be completed because the checkpoint lacks the timeline/output evaluator and graphical states.

## Why the convergence history cannot be forecast

`cost_history` records residual calls, including finite-difference trials; it is not an accepted-iteration trace. Accepted/rejected steps, trust radius, actual/predicted reduction, step vectors, LSMR inner telemetry and active-set switching history were not persisted. Therefore last-100/200/400/800 accepted-iteration analysis, hindcast and a credible nfev-to-1e-4 forecast are `NOT_EVALUABLE_CHECKPOINT_TELEMETRY_MISSING`. The earlier label `HARD_CAP_REACHED_WHILE_PROGRESSING` cannot establish trust-region health by itself.

## Local numerical evidence

The exact robust gradient reconstructed from saved residuals and SciPy robust-effective Jacobians matches the stored gradient and optimality. Raw J^T r is separately reported and is not substituted for the robust gradient. Endpoint central derivatives cannot be newly evaluated without reopening the payload evaluator, so endpoint derivative qualification is incomplete rather than silently passed.

All bounds are unbounded and all active masks are zero: `NO_BOUND_LIMITATION`. Robust weights are stable from soft to terminal (no row changed by more than 0.01). The factor-gradient audit finds no single dominating factor, but very large initial-still and T-pose static gradients oppose and nearly cancel. This is evidence of local factor tension, not by itself proof that the model must be reweighted.

A checkpoint-only local linear diagnostic shows tight LSMR and dense direct least squares agree, while standalone default LSMR stops much earlier with a much smaller step. The actual SciPy trust radius, damping and LSMR telemetry were not saved, so this is a strong diagnostic signal for an inner-solve limitation but not a reconstruction of the historical solver step.

Endpoint residual correlations are near one, but mandatory 21-point chord costs and graphical-output comparisons cannot be computed from checkpoints. Basin classification is therefore `NOT_DETERMINABLE_CHECKPOINT_INCOMPLETE`.

## Decision

`CAP_EXTENSION_REASONABLE = false`. The required telemetry and forecast are unavailable, and endpoint derivative/output checks are incomplete. The single permitted recommendation is `G_RUN_SHORT_INSTRUMENTED_DIAGNOSTIC`: a future separately authorized short run should persist accepted/rejected iterations, trust radius, predicted/actual reductions, actual LSMR telemetry and trial-step derivatives. This audit does not run it.

Golf, boxing, walk and final_still remained sealed. UWB/T4/Anchor and operator measurements were not read. No freeze, replay, render, commit or push was performed.
