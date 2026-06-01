# AutoPos Irregular Layout Simulation Report

- layouts: 100
- sweep sets per layout: 1000
- solver path: existing mainline CPU/SciPy solvers
- GPU acceleration: not_used_scipy_solver_path_cpu_parallel
- reference noise sweep: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv`

## Solver Summary

| solver | success | fit RMS median | coord RMS median | coord RMS p90 | pair RMS truth median |
|---|---:|---:|---:|---:|---:|
| v1-old | 200/200 | 0.717 | 1.113 | 1.426 | 0.847 |
| v2 | 200/200 | 0.949 | 1.310 | 1.962 | 0.960 |
| v3-lite | 200/200 | 0.460 | 0.821 | 1.104 | 0.615 |
| v3-full | 150/200 | 0.366 | 2.088 | 3.573 | 2.199 |
| v4-io | 200/200 | 0.943 | 3.277 | 7.925 | 2.562 |

## Notes

- `fit_rms_mm` is residual against the synthetic fused inter-anchor sweep.
- `coord_rms_mm` is solved coordinates against known truth after rigid alignment with reflection allowed.
- `v4-io` includes the current soft two-layer physical prior, so this report is useful for detecting whether that prior helps or distorts unusual layouts.

## Files

- `summary.csv`
- `summary_by_solver.csv`
- `figures/solver_coord_rms_boxplot.png`
- `figures/solver_fit_rms_boxplot.png`
- `figures/layout_xy_gallery_page_*.png`
