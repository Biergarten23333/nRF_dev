# Ten-node body Fusion V4.1 measurement-conditioned centerline

Top-level verdict: `BLOCKED_V4_1_INPUTS_INCOMPLETE`.

- `FULL_SEGMENT_POSE_CALIBRATION`: `FAIL_AXIAL_TWIST_GAUGES_UNRESOLVED`
- `STICK_FIGURE_CENTERLINE_CALIBRATION`: `NOT_RUN_INPUTS_INCOMPLETE`
- `FOOT_RENDERING`: `BLOCKED_SHOE_GEOMETRY_INCOMPLETE`

V3 and V4 were treated as immutable historical runs; both SHA manifests were fully verified before V4.1 began. V4.1 separates direct palpable-landmark measurements, derived internal joint-centre geometry, two-stage sensor placement, and rendering-only shoe geometry. Foot and ankle rendering inputs do not participate in the centerline solver gate.

The historical input remains fail-closed: no direct subject measurements, named/versioned shoulder or hip derivation, marked antenna phase-centre/enclosure transform, or evidence-bounded capture placement prior was found. These fields remain `MISSING`; no population values or fabricated historical board offsets were substituted. Input validation therefore completed before the calibration ledger was opened. Held-out walk/final_still were not opened and no GIF was generated.

When valid inputs exist, all calibration-estimated capture placement vectors are bounded nuisance parameters in the measurement and posterior Jacobians, per-coordinate profiles, multistart, interleaved, and action-removal refits. Joint-centre and antenna predictions are computed independently. Anthropometric scalars are treated as fixed; reported output uncertainty explicitly excludes anthropometric measurement and derivation uncertainty.
