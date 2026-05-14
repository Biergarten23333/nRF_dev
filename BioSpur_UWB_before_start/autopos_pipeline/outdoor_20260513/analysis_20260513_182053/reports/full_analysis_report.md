# AutoPos Outdoor 20260513 Full Analysis
Data root: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513`

Output: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_20260513/analysis_20260513_182053`
## Key Findings
1. Sweep quality: 28 inter-anchor pairs loaded. Median pair MAD is 27.43 mm; D/H-related median MAD is 29.65 mm.
2. V1-V4 progression: V4-io inter-anchor RMS is 29.29 mm; all solver JSONs are under `solves/`.
3. Static positioning: collected 23/24 static captures; center median 3D std is 75.10 mm.
4. Antenna orientation: max height-level orientation spread is 54.96 mm. See `tables/orientation_effect.csv`.
5. Roto tilt ablation: 17 captures analyzed; median circle-fit 3D std is 71.61 mm.
6. Wand rigid body: 4 pair summaries generated. W05 free move is analyzed, but should not be used as strong same-frame rigid constraint because TDMA tag positions are not simultaneous.
7. Wand-only calibration: marked experimental/not solved in this single-pass script; table reserves the comparison slot as NaN.
8. Sweep+Wand fusion: marked experimental/not solved in this single-pass script; current recommended baseline remains sweep-only V4-io plus validation captures.
9. 20260504 comparison: loaded if `outdoor_v4_20260504/sweeps/inter_anchor_500set_20260504_185011/pairs_all.csv` exists; see `tables/sweep_vs_20260504.csv`.
10. New finding: in Roto vertical/high captures, BS2DCE usually has much higher ge8/circle robustness than BSDC91; this is visible in raw capture summaries and circle fit tables.

## Tables
- `tables/anchor_sigma.csv`
- `tables/orientation_effect.csv`
- `tables/roto_dynamic_error.csv`
- `tables/roto_tilt_ablation.csv`
- `tables/solver_progression.csv`
- `tables/static_positioning.csv`
- `tables/static_spatial_summary.csv`
- `tables/sweep_quality.csv`
- `tables/sweep_vs_20260504.csv`
- `tables/v5_fim_uncertainty.csv`
- `tables/wand_orientation_invariance.csv`
- `tables/wand_rigid_body.csv`

## Figures
- `figures/fig01_sweep_quality_comparison.png`
- `figures/fig02_static_spatial_heatmap.png`
- `figures/fig03_antenna_orientation_effect.png`
- `figures/fig04_roto_tilt_ablation.png`
- `figures/fig05_roto_radius_vs_tilt.png`
- `figures/fig06_wand_distance_timeseries.png`
- `figures/fig08_calibration_comparison_bar.png`
- `figures/fig09_v1_v4_progression.png`
