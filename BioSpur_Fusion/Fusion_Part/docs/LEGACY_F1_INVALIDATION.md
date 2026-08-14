# Legacy F1 invalidation

Commit `f43ae6b94ccb290f549ee054e617d1183a7a8b7e` is superseded as follows:

```text
BODY_MAPPING_CONSTRAINED_PASS
TIME_ALIGNMENT_NOT_PROVEN
F1_BODY_FUSION_FAIL
CURRENT_F1_ANIMATIONS_INVALID_AS_FUSION_EVIDENCE
```

Mapping evidence, raw capture and action annotations remain historical
evidence. The prior F1 trajectories and animations are not valid Fusion output.

Exact causes are:

- `B306_Part/tools/body_calibration_v1/analyze_constrained_capture.py`,
  `replay_f1`: constructs one `Q1T4ESKF` per node, initializes from the first
  five raw seconds, schedules T4 using `master_arrival_ms`-derived `host_s`, and
  calls `t4_position_update` unconditionally while discarding its returned NIS.
- `B306_Part/tools/v47_q1_eskf.py` at that commit,
  `Q1T4ESKF.t4_position_update`: performs `_correct` before the caller can gate
  the innovation, allowing one bad point to mutate p, v, q, both biases and P.
  The historical implementation is now reached only through a compatibility
  import; ownership moved to `Fusion_Part`.
- `analyze_constrained_capture.py`, `replay_f1`: does not call
  `gravity_update_causal`, `zupt_update` or `MotionVetoGate`.
- `analyze_constrained_capture.py`, `body_frame` and `replay_f1`: promotes the
  T-Pose/display `R_body_from_V4` to a valid physical V4/navigation binding,
  bypassing gravity/frame observability.
- `analyze_constrained_capture.py`, `replay_f1`: contains no shared joints,
  immutable segment geometry, lever arms or articulated state.
- `B306_Part/tools/body_calibration_v1/render_constrained_body.py`,
  `Scene.positions`/`Scene.frame`: connects independent filtered positions in
  the renderer; dashed lines do not create estimator constraints.
- `render_constrained_body.py`, `interp`: uses `numpy.interp` without a maximum
  source-gap rule and can visually bridge unsupported intervals.
- `render_constrained_body.py`, comparison modes: places raw T4, renderer lines,
  Q1 attitude and the invalid independent F1 together in a way that could be
  mistaken for a qualified joint-fusion comparison.

Historical reports are not rewritten. This document is their formal
superseding architectural verdict.
