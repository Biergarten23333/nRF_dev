# AutoPos Irregular Layout Simulation Report

- layouts: 100
- sweep sets per layout: 1000
- solver path: existing mainline CPU/SciPy solvers
- GPU acceleration: not_used_scipy_solver_path_cpu_parallel
- reference noise sweep: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv`

## Solver Summary

| solver | success | fit RMS median | coord RMS median | coord RMS p90 | pair RMS truth median |
|---|---:|---:|---:|---:|---:|
| v1-old | 1000/1000 | 0.700 | 1.277 | 1.896 | 0.871 |
| v2 | 1000/1000 | 1.039 | 2.129 | 5.280 | 1.032 |
| v3-lite | 1000/1000 | 0.437 | 0.978 | 1.620 | 0.628 |
| v3-full | 527/1000 | 0.384 | 2.675 | 4.447 | 2.458 |
| v4-io | 1000/1000 | 1.109 | 7.576 | 19.475 | 3.206 |

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
