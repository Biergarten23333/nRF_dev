# AutoPos Irregular Layout Simulation

This folder contains an anchor-only stress test for the current AutoPos solver
family.

The simulation generates paired lower/upper anchors:

- A-E, B-F, C-G, D-H are vertical pairs.
- Pair `delta Z` is approximately 1.4 m.
- Total layout extent is constrained to a 5 m x 5 m x 5 m box.
- Lower and upper faces are intentionally non-rectangular and skewed.
- Each synthetic sweep uses the same `pairs_all.csv` shape as the field data:
  `a,b,master,dist_mm,quality_percent,raw_mm,ok,fail`.

The runner reuses the existing mainline solver implementations from:

`autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py`

## Quick Smoke Test

```bash
python3 AutoPos_simulation/run_irregular_layout_sim.py \
  --layouts 3 \
  --sets 50 \
  --workers 1 \
  --out AutoPos_simulation/out_smoke
```

## Full Requested Run

```bash
python3 AutoPos_simulation/run_irregular_layout_sim.py \
  --layouts 100 \
  --sets 1000 \
  --workers 8 \
  --out AutoPos_simulation/out_100x1000
```

The solvers are CPU/SciPy based, so `--workers` is the useful acceleration knob.
The script records that GPU acceleration is not used for this path.

## Outputs

- `summary.csv`: one row per layout/solver.
- `summary_by_solver.csv`: aggregate RMS statistics by solver.
- `layout_XXXX/true_layout.json`: generated ground truth.
- `layout_XXXX/pairs_all.csv`: synthetic sweep.
- `layout_XXXX/<solver>/layout.json`: solved layout.
- `layout_XXXX/<solver>/layout_residuals.csv`: per-pair residuals.
- `figures/solver_coord_rms_boxplot.png`
- `figures/solver_fit_rms_boxplot.png`

Important metrics:

- `fit_rms_mm`: anchor-only distance residual against the synthetic fused sweep.
- `coord_rms_mm`: coordinate RMS against the known true layout after rigid
  alignment with reflection allowed.
