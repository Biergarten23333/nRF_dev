# Implementation and test plan

The implementation order is frozen as follows.

1. Replace the V1 binary stationarity adapter with continuous per-node
   quasi-static confidence, robust bias/gravity estimates, explicit
   uncertainty, and no fallback.
2. Qualify per-node asynchronous common-time interpolation, validity masks,
   boot-epoch and gap rejection independently of quasi-static confidence.
3. Keep neutral standing and T-pose as distinct soft pose factors sharing one
   static calibration.
4. Segment all eleven labelled calibration actions once from signal content;
   precompute informative rows, rotations, whitening, and factor metadata.
5. Build one joint objective whose residual blocks and production Jacobian
   match `ACTION_RESIDUAL_PARAMETER_MATRIX.json`.
6. Verify every estimation action has residual rows, nonzero Jacobian
   information on a frozen/replay-consumed parameter, and a meaningful
   leave-one-action-out effect.
7. Qualify endpoint derivatives and structural sparsity, then use exact dense
   solve for a small state or tight LSMR with the frozen tolerances.
8. Fit one deterministic start, run derivative/physical/restart gates, then
   the remaining starts with per-start checkpoints.
9. Canonically serialize and hash the calibration; destroy the fit object and
   reload in an isolated process before a label-blind continuous replay.
10. Render all eleven calibration actions only after all gates.  Golf and
    boxing may be opened once only after those previews pass.  Walk,
    final-still, UWB/T4/Anchor, and operator measurements remain sealed.

Required fixtures cover natural breathing/sway/micro-motion, independent node
motion, true sustained motion, bias and gravity recovery with uncertainty
coverage, distinct neutral/T-pose replay, removal of the global veto,
asynchronous timestamps/missing samples, eleven-action Jacobian information,
functional-parameter replay consumption, action ablation, endpoint sparsity,
solver tolerances, freeze/reload, and label-blind state evolution.

This is not a gate rename or threshold relaxation: the estimator changes from
binary global-veto sample selection plus fallback to continuous local evidence,
robust M-estimation, correlation-adjusted uncertainty gates, and a Q2 update
whose gravity gain is weighted continuously.
