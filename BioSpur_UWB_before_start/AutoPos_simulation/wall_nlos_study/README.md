# AutoPos Wall/NLOS Study

This folder contains the wall and photo-inspired metal-object NLOS simulations for the paired AutoPos anchor layout.

## Folder Map

- `inputs/photos/opti_measurement/raw/`  
  Raw Opti measurement environment photos extracted from `/home/zekaixiao/Downloads/Gmail.zip`.

- `inputs/photos/opti_measurement/analysis/`  
  Contact sheet and qualitative environment analysis used before Phase 2.

- `runs/phase1_wall_distance/`  
  Phase 1 simulation outputs. Uses a 3m x 3m x 1.4m paired anchor layout and sweeps 0/1/2/3/4 walls with 0-100 cm wall distance.

- `runs/phase2_wall_metal_boxes/`  
  Phase 2 simulation outputs. Starts from Phase 1 and adds photo-inspired random metal/equipment boxes near the layout boundary.

- `analysis/`  
  Cross-scenario CSV summaries and Phase 1 vs Phase 2 comparison tables.

- `figures/phase1/`  
  Phase 1 wall-distance plots.

- `figures/phase2/`  
  Phase 2 wall+metal plots and deltas against Phase 1.

- `scripts/run_wall_nlos_sim.py`  
  GPU-capable simulator used for Phase 1 and Phase 2.

## Current Runs

### Phase 1

- Output: `runs/phase1_wall_distance/`
- Report: `runs/phase1_wall_distance/report.md`
- Summary: `runs/phase1_wall_distance/summary.csv`
- Key metrics: `analysis/phase1_wall_count_key_metrics.csv`

### Phase 2

- Output: `runs/phase2_wall_metal_boxes/`
- Report: `runs/phase2_wall_metal_boxes/report.md`
- Raw summary: `runs/phase2_wall_metal_boxes/summary.csv`
- Aggregated summary: `analysis/phase2_wall_metal_aggregated.csv`
- Key distances: `analysis/phase2_key_distances.csv`

### Phase 3

- Output: `runs/phase3_material_walls/`
- Report: `runs/phase3_material_walls/report.md`
- Raw summary: `runs/phase3_material_walls/summary.csv`
- Material safe distances: `analysis/phase3_material_safe_distance.csv`

### Phase 1/2/3 Comparison

- Report: `analysis/phase123_comparison_report.md`
- Key 4-wall comparison: `analysis/phase123_4wall_key_comparison.csv`
- Main figure: `figures/comparison/phase123_4wall_pos_p95_comparison.png`

## Archive Links

Previous large AutoPos simulations are archived under:

- `../archive/2026-06-01_irregular_layout_solver_sweep/`
- `../archive/2026-06-01_phase123_xdop_large_layouts/`

Compatibility symlinks are kept at:

- `../out_100x1000`
- `../out_phase2_1000_large`
