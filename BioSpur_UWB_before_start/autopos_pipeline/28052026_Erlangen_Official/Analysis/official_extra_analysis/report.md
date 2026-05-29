# 2026-05-28 Erlangen Official Analysis Draft

## 1. Dataset / Hardware / Solver Versions

Dataset root:

`autopos_pipeline/28052026_Erlangen_Official`

Analysis root:

`autopos_pipeline/28052026_Erlangen_Official/Analysis/official_extra_analysis`

Anchor layout solvers included:

`v1-old`, `v2`, `v3-lite`, `v3-full`, `v4-io`

Tag solver family:

`T1`, `T2`, `T3`, `T4`

Roto OptiTrack absolute validation is pending; current roto results are UWB-only consistency diagnostics.

## 2. Anchor Layout Absolute Accuracy

Source: `tables/layout_alignment_summary.md`

Headline convention: reflection-allowed rigid alignment, no scale fitted to OptiTrack truth.

Current v4-io headline:

- all8 rigid RMS: 104.9 mm
- all8 horizontal RMS: 86.1 mm
- all8 vertical RMS: 59.9 mm
- similarity scale: 0.960, diagnostic only
- noG rigid RMS: 104.4 mm

G marker warning: `Gshort/Glong` marker fingerprint is suspect. The report must keep both all8 and noG values.

## 3. Static Tag Absolute Accuracy

Source:

- `tables/tag_accuracy_summary.md`
- `tables/tag_abs_errors_per_session.csv`

This first pass uses the currently available production tag-solver output. The transform is fitted from anchors only; no tag truth is used for fitting.

Current v4-io production-output headline:

- all8 median 3D error: 77.4 mm
- all8 p95 3D error: 270.3 mm
- noG median 3D error: 81.3 mm
- noG p95 3D error: 278.6 mm

Planned matrix:

`5 anchor-layout solvers x 4 tag solvers`

The full `5 Vx x 4 Tx` raw replay matrix is still pending.

## 4. VDOP Geometry Explanation

Source:

- `tables/dop_summary_grid100.md`
- `tables/dop_summary_grid50.md`
- `tables/dop_summary_grid25.md`

Default geometry model: range-only Jacobian `[ux, uy, uz]`.

Current grid50 summary:

- all8 VDOP median 0.806, p95 0.950
- noG VDOP median 0.859, p95 1.185
- dropH VDOP median 0.864, p95 1.206

The 25 mm grid is intended for final report figures.

## 5. MC Keep-k Robustness

Pending active MC completion. Expected complete matrix:

`5 Vx x 4 Tx x static/roto x keep 8/7/6/5/4`

After MC reaches 40/40, run:

```bash
python3 Analysis/official_extra_analysis/scripts/mc_integrity_aggregate.py
python3 Analysis/official_extra_analysis/scripts/bootstrap_ci.py --n-boot 10000 --include-mc
```

## 6. Roto UWB-only Consistency

Source:

- `tables/metric_confidence_intervals.md`
- existing solver tables under `solver/outputs/v1_to_v4_io_field_check/tables/`

Current v4-io bootstrap headline:

- roto abs deltaR error median: 33.33 mm, 95% CI 22.52-40.15 mm
- roto turn-center RMS median: 14.31 mm, 95% CI 13.16-17.36 mm

Roto OptiTrack absolute validation remains pending.

## 7. Pair Residual Diagnostics

Source:

- `tables/pair_residual_diagnostics.md`
- `figs/pair_raw_asymmetry_heatmap.png`
- per-version residual heatmaps in `figs/`

Current v4-io worst all1000 pairs include B-C, B-G, D-E, D-F, and F-H. G-involving pairs are explicitly flagged.

## 8. Bootstrap CI

Source:

- `tables/metric_confidence_intervals.csv`
- `tables/metric_confidence_intervals.md`
- `figs/bootstrap_confidence_intervals.png`

Current CIs include layout, static repeatability, and roto UWB-only metrics. MC CIs will be appended after the MC integrity check passes.

## 9. Limitations / Pending

- G OptiTrack marker labeling likely has a short/long fingerprint issue.
- Roto OptiTrack absolute data is not yet available.
- Static tag absolute accuracy still needs the full `5 Vx x 4 Tx` matrix.
- Stratified keep-k is separate from the current random keep-k MC.
