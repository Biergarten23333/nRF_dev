# Canonical Plan v2.1 addendum: operator-recorded mapping

This append-only addendum is bound to Canonical Staged Plan v2.0 SHA-256
`305770321af8f2764e70a932c38c0675679bb94928e0d1dedfdb508eedafe0af`.
It does not edit or replace the original document outside the limited mapping
authority decisions below.

For the current capture/session/donning, A-03 is
`SOLVED_WITH_SCOPE_OPERATOR_RECORDED_MAPPING` and automatic node association is
`AUTOMATIC_NODE_ASSOCIATION_DEFERRED`. The operator-recorded mapping is an
explicit acquisition/calibration input. It is neither an algorithmic inference
nor external pose truth.

The Phase 2-R result remains unchanged: automatic Top-1 was 8/10, truth was rank
3 in the frozen Top-K, the statistical gates failed, and the execution remains
marked `TRUTH_CONTAMINATED_DEVELOPMENT_REVISION`. Full
`PASS_NODE_ASSOCIATION_AND_PROBABILISTIC_BODY_CALIBRATION` was not obtained.
Phase 3 may consume only the operator-bound conditional handoff as a
research/engineering input.

The v2.0 assumptions that users need not provide a mapping and that requesting
one is a no-go apply only to the former AutoMapping-first product assumption.
For every future donning/session, an operator must explicitly record or confirm
the hardware-to-role bijection. Missing or stale bindings fail closed as
`MAPPING_BINDING_REQUIRED`; no historical binding may be silently reused.

The v2.0 `top-K -> freeze` path is superseded only for the current product main
line by `validated automatic freeze OR operator-recorded session binding`.
Future AutoMapping must implement the same `FrozenMappingBinding` provider
interface. It may not alter estimator states or factors and may never reorder
nodes at runtime. Its status is
`DEFERRED_OPTIONAL_CAPABILITY_AFTER_RUNTIME_FRAMEWORK_MATURITY`.

This change removes only the automatic-association prerequisite. It does not
resolve sensor-to-segment extrinsics, joint centres, bone lengths, metric world,
accelerometer bias, antenna lever arms, contact, or external accuracy. The
revealed session is development/negative-regression evidence for AutoMapping;
future blind validation requires a new independent session, donning, and split.
