# Filtered Reanalysis Matrix

The filtered deployment analysis should mirror the existing 25-part extra
analysis without overwriting it.

## What Changes

Only tag-output quantities change:

- per-frame or per-session tag position;
- static absolute tag error;
- repeatability/jitter;
- roto trajectory/turn-center consistency;
- keep-k robustness if the filter is rerun under dropout;
- error-vector and outlier statistics derived from tag output.

## What Does Not Change

These are invariant and should be referenced, not recomputed as filtered
results:

- OptiTrack anchor truth audit;
- AutoPos anchor layout absolute comparison;
- anchor all8/noG layout sensitivity;
- corrected OptiTrack tag truth construction;
- VDOP/DOP geometry maps;
- inter-anchor pair residuals;
- raw directional asymmetry;
- temporal drift of raw range links;
- delay common/differential decomposition;
- method/source evidence archive.

## Status After 2026-06-01 Run

Completed:

- Static absolute filtered matrix for all five layouts, all8/noG, `T1--T4 + F0--F5`, and `T5a--T5e`.
- Full filtered static metric set: percentile table, per-axis bias, outlier rates, height/edge/facing splits, radial decomposition, figures, and report.
- Bootstrap CI for filtered static headline metrics.

Not rerun as filtered outputs:

- Anchor/layout/VDOP/pair/drift/delay analyses, because tag-output filtering cannot change those upstream quantities.
- Roto absolute validation, because OptiTrack roto truth is still pending.
- MC5000 and stratified keep-k with filters, because that is a separate robustness campaign and should be run only for selected deployment candidates (`T4+F4`, `T4+F5`, `T5b/T5c`) after this static matrix is reviewed.

## Mapping to Existing 1--25 Analysis List

| # | Existing analysis | Filtered action |
|---:|---|---|
| 1 | OptiTrack anchor truth audit | reference unchanged |
| 2 | AutoPos vs OptiTrack layout comparison | reference unchanged |
| 3 | Anchor source all8/noG sensitivity | reference unchanged |
| 4 | Static tag truth correction | reference unchanged |
| 5 | Static tag production absolute accuracy | **done** for full filtered static matrix |
| 6 | Frame-locking sanity | reuse anchor-locked transform; optional sanity only |
| 7 | Localization metric set | **done** for filtered output |
| 8 | Raw static replay matrix | **done** as `T1--T4 + F0--F5` and `T5a--T5e` |
| 9 | Surveyed-anchor baseline | not yet rerun; optional selected-candidate control |
| 10 | Worst-point resolution | **done** through filtered per-session/per-position tables and figures |
| 11 | VDOP/DOP maps | reference unchanged; optional innovation-vs-VDOP plot |
| 12 | Pair residual diagnostics | reference unchanged |
| 13 | Raw directional asymmetry | reference unchanged |
| 14 | Bootstrap confidence intervals | **done** for filtered static headline metrics |
| 15 | MC5000 random keep-k | not yet rerun; filtered robustness campaign |
| 16 | Stratified fixed-drop keep-k | not yet rerun; filtered robustness campaign |
| 17 | Temporal/thermal drift | reference unchanged; add filter innovation drift |
| 18 | Delay decomposition | reference unchanged |
| 19 | Tag radial/scale error structure | **done** with filtered output |
| 20 | Worst-point raw-range fingerprint | reference unchanged plus filtered outcome |
| 21 | Anchor health scorecard | reference unchanged; optional innovation health |
| 22 | Single-anchor criticality | rerun only if keep-k filtered replay is done |
| 23 | Height/edge/facing stratification | **done** with filtered output |
| 24 | Method/source evidence archive | filter scripts and run metadata added here |
| 25 | Reports and figures packaging | **done** for static filtered deployment supplement |

## Matrix Size

Full exhaustive matrix:

```text
5 layouts x 4 existing tag solvers x N external filters x all8/noG
+ 5 layouts x M T5 variants x all8/noG
```

This can become large quickly. The recommended staged matrix is:

### Stage A: Minimal defensible supplement

```text
v4-io x {T4, T4+F1, T5a} x {all8, noG}
```

### Stage B: External-filter comparison

```text
v4-io x {T1, T2, T3, T4} x {F0, F1, F2, F3} x {all8, noG}
```

### Stage C: Native T5 comparison

```text
v4-io x {T4, T4+F1, T5a, T5b, T5c} x {all8, noG}
```

### Stage D: Full solver-layout matrix

```text
{v1-old, v2, v3-lite, v3-full, v4-io}
x {T1, T2, T3, T4, T4+F1, T5a, T5b}
x {all8, noG}
```

### Stage E: Robustness

Run MC keep-k and stratified fixed-drop only for the deployment candidates:

```text
v4-io x {T4, T4+F1, T5a, T5b, T5c}
x keep {8,7,6,5,4}
```

## Reporting Rule

Filtered metrics must be named as deployment-output metrics. They must not
replace the official unfiltered absolute validation.
