"""Human-readable Root-R4 decision report with the required direct answers."""
from __future__ import annotations

from pathlib import Path


def build_report(output: Path, context: dict) -> Path:
    t4 = context["t4"]; raw = context["raw"]; hybrid = context["hybrid"]
    blocks = context["time_blocks"]; tag_loo = context["tag_loo"]; counter = context["counter"]
    inertial = context["inertial"]; candidate = context["candidate"]; health = context["health"]
    verdicts = context["verdicts"]
    text = f"""# BioSpur C1 Root-R4 raw-range/frame/fusion final report

## Outcome

Root-R4 closes the raw-range schema and exact raw→T4 lineage, implements
lineage-safe T4-only, raw-likelihood, and disjoint replacement strategies, and
synthetically qualifies the root-inertial equations. It does **not** authorize a
capture-bound common frame. The real-C1 yaw evidence is locally identifiable but
not stable: the five independent T4 time blocks span {blocks['yaw_range_deg']:.3f}°
against a predeclared 10° limit, and tag leave-one-out changes reach
{tag_loo['maximum_abs_yaw_change_deg']:.3f}° against a 15° limit. Consequently no
real-C1 IMU–UWB fused-root candidate was run past the frame gate.

Final taxonomy:

```text
{chr(10).join(verdicts)}
```

## Direct answers

1. **Raw ranges found?** Yes. C1 contains {context['raw_events']:,} decoded valid pre-solver tag–anchor ranges from {context['t4_events']:,} accepted UWB records.
2. **Exact raw schema/timing?** Each 184-byte v7 UWB payload supplies node identity, packet sequence, sweep, eight fixed A–H slots, `range_mm` (`uint16`, millimetres), `t_round_us`, quality, CFO Q8, validity/flags, B306 `strobe_us`, and completed-frame `frame_us`, plus raw record/byte offsets. Per-link measurement time is `common(strobe_us) + affine_node_slope*t_round_us/2`; availability is the mapped completed-UART-frame lower bound. Both originate in the per-node 1 MHz B306 TIMER2 and are mapped to the common capture-relative/beacon schedule.
3. **Exact raw→T4 lineage?** Yes: all {context['t4_events']:,} T4 events map exactly by `(node_index, source_index)` to one raw record, and `solved_used_mask` identifies the constituent links.
4. **Exact/envelope/unresolved counts?** Exact {context['t4_events']:,}; conservative envelopes 0; unresolved 0.
5. **How are both layers retained safely?** T4-only replaces its constituent ranges; raw-only/T4-initialized candidates use raw factors with T4 only as initializer/diagnostic; the hybrid uses raw factors on even epochs and replacing T4 factors on odd epochs. The factor ledger permits one active factor per raw event.
6. **T4 roles?** Baseline and active factor in T4-only; numerical initializer and health diagnostic in raw-only/C4; replacement factor on disjoint odd epochs in C5; never an independent factor beside its own ranges.
7. **One common `R_N_from_V4` estimated?** One global candidate per evidence layer was estimated, never one per node, but none was authorized.
8. **Exact mapping?** `v^N = R_N_from_V4 v^V4`; `R_V4_from_N = R_N_from_V4^T`. The state is parameterized directly in V4, eliminating any redundant transform translation.
9. **Full SO(3) necessary?** No. Frozen M1 says `+Z up` and the metric V4 two-layer layout defines the upper anchor layer as `+Z`; roll/pitch are therefore contract-bound. The estimated family is yaw-only embedded as a proper `SO(3)` rotation. A diagnostic full-SO(3) Jacobian is reported, but roll/pitch were not fitted for authorization.
10. **Global or single node?** Global: all ten labeled tags, many epochs, and all eight anchors for the raw likelihood. Single-node alignment is a rejected diagnostic.
11. **What made yaw locally observable?** Time-varying left/right arm and leg tag baselines across all ten tags; the local Fisher result is full rank for the one-parameter yaw family. This local rank did not survive the required stability interpretation.
12. **Rank after nuisance elimination?** Yes for yaw: per-epoch common translation is analytically eliminated and the yaw Schur/Fisher rank is 1. That is necessary, not sufficient, for authorization.
13. **Stability?** T4 block range {blocks['yaw_range_deg']:.3f}°; maximum tag-LOO change {tag_loo['maximum_abs_yaw_change_deg']:.3f}°. Full bootstrap and anchor-LOO rows are in the named JSON artifacts. Both predeclared stability gates fail.
14. **Counterfactuals rejected?** Synthetic truth rejects inverse/90°/180°/reflection. Real C1 does not materially reject every required yaw counterfactual: the real +90° objective ratio is {counter['yaw_counterfactuals']['plus_90_deg']['objective_ratio_to_optimum']:.3f} (required ≥1.25). Reflection and free scale remain unauthorized regardless of fit.
15. **Layer agreement?** T4 gives {t4.yaw_v4_from_n_deg:.3f}°, raw likelihood {raw.yaw_v4_from_n_deg:.3f}°, and lineage-safe hybrid {hybrid.yaw_v4_from_n_deg:.3f}°. Their pairwise differences are reported; agreement cannot rescue failed block/tag stability.
16. **Single-tag/anchor dominance?** Tag LOO shows material dominance/inconsistency. Raw anchor LOO is less extreme but is not enough to authorize the frame.
17. **Fixed 0.915 m disagreement explained?** No. Unit, scale, direction, and lineage audits do not justify absorbing it; free scale is diagnostic and prohibited.
18. **Bad links identifiable?** Yes at the canonical raw/T4 residual level. The audit classifies {health['classification_counts'].get('RANGE_LINK_CREDIBLE',0)} credible, {health['classification_counts'].get('RANGE_LINK_DEGRADED',0)} degraded, and {health['classification_counts'].get('RANGE_LINK_REJECTED',0)} rejected tag–anchor pairs. These are internal residual classifications, not external truth.
19. **Body-shadow-consistent behavior?** A model-based candidate-frame ray-risk association is reported as `BODY_SHADOW_CONSISTENT_DIAGNOSTIC_ONLY`; no true NLOS label is claimed.
20. **CIR?** Neither required nor used. RSS, first-path power, and CIR are absent from the method.
21. **Did UWB modify M1?** No. M1 orientation, FK, validity, reset arrays, joint semantics, and bone lengths are input-only and byte-identical in the final integrity check.
22. **Inertial synthetic truth?** {inertial['classification']}; real C1 remains unqualified with {context['real_inertial_terminal_m']:.3f} m terminal inertial-only displacement norm.
23. **Future/pre-availability influence?** Zero in eligible synthetic causal executions; the mutation controls detect future UWB, future IMU, and missing availability.
24. **Immutable outputs?** Yes; zero violations, and a write/mutation negative control is detected.
25. **50 mm physical-update cap?** Yes in eligible synthetic executions. Splitting eight ranges from one sweep still yields at most 0.050 m cumulative correction.
26. **Dropout/reacquisition?** Covariance grows during the five-second synthetic dropout; five ramped updates use 0.2/0.4/0.6/0.8/1.0 authority. Real-C1 reacquisition is not evaluated because the frame gate fails.
27. **Candidate outcomes?** {sum(row['status'].startswith('COMPLETED') for row in candidate['rows'])} completed, diagnostics are explicitly labelled, C3–C5 stop at the frame gate, and NC-1–NC-4 are rejected. See `CANDIDATE_ARCHITECTURE_MATRIX.json`.
28. **Genuine lineage-safe real fusion?** None. R4-C5 is structurally/synthetically supported but did not become a real-C1 fused candidate because `R_N_from_V4` was not authorized.
29. **What C1 establishes?** Exact schema/lineage, timing, local mathematical rank, frame instability, per-link internal consistency, strict causal architecture, bounded authority, and synthetic correctness.
30. **What requires external truth?** Absolute root/world accuracy, true drift reduction, true NLOS/body-shadow labels, true common-mode bias, biomechanical accuracy, end-to-end host/display latency, and product readiness.
31. **Product-ready?** No. Every candidate remains experimental.
32. **Repository/product actions?** No commit, push, merge, promotion, enablement, firmware/hardware change, capture, or product-default change occurred.

## Scientific boundary

The real-C1 result is internal evidence only. Smoothness, raw/T4 mutual agreement,
or a locally full-rank Hessian is not external accuracy. The decisive Root-R4
result is therefore explicit frame non-authorization, not a tuned fusion trace.
"""
    path = output / "ROOT_R4_RAW_RANGE_FRAME_FUSION_FINAL.md"; path.write_text(text, encoding="utf-8")
    return path
