# AutoPos Complete Evaluation: V1-V5 x Layer Ablation x Algorithm Comparison

Output directory: `/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/outdoor_v4_20260504/full_evaluation_20260506_162829`

## Table 1: Calibration Quality (Inter-Anchor RMS)

| Solver | Dual-layer 8anc | Upper only EFGH | Lower only ABCD | Best6 no DH | Upper+AB | Lower+EF |
| --- | --- | --- | --- | --- | --- | --- |
| MDS+NLS | 42.1 | 0.0 | 0.0 | 49.9 | 15.4 | 28.4 |
| Ridolfi GD | 42.1 | 0.0 | 0.0 | 49.9 | 15.4 | 28.4 |
| SDP+NLS | nan | nan | nan | nan | nan | nan |
| AutoPos V1 | 42.1 | 0.0 | 0.0 | 49.9 | 15.4 | 28.4 |
| AutoPos V2 | 42.4 | 11.1 | 7.0 | 50.2 | 15.0 | 28.5 |
| V3-lite | 42.4 | 0.0 | 0.0 | 50.1 | 15.3 | 28.7 |
| V3-full | 74.9 | 0.0 | 0.0 | 121.2 | 60.8 | 50.1 |
| V4-interonly | 48.0 | 0.0 | 0.0 | 69.6 | 13.4 | 31.3 |


## Table 2: ID02 Positioning - 3D std (mm)

| Solver | Dual-layer 8anc | Upper only EFGH | Lower only ABCD | Best6 no DH | Upper+AB | Lower+EF |
| --- | --- | --- | --- | --- | --- | --- |
| MDS+NLS | 41.3 | 109.5 | 67.6 | 44.0 | 48.6 | 43.9 |
| Ridolfi GD | 41.3 | 109.5 | 67.6 | 44.0 | 48.6 | 43.9 |
| SDP+NLS | nan | nan | nan | nan | nan | nan |
| AutoPos V1 | 41.3 | 109.5 | 67.6 | 44.0 | 48.6 | 43.9 |
| AutoPos V2 | 41.4 | 102.8 | 68.1 | 44.0 | 48.6 | 43.9 |
| V3-lite | 41.3 | 109.7 | 67.0 | 43.9 | 48.6 | 43.9 |
| V3-full | 40.7 | 109.7 | 67.0 | 44.4 | 47.9 | 43.5 |
| V4-interonly | 40.8 | 109.7 | 67.0 | 41.5 | 48.6 | 43.3 |


## Table 3: ID02 Per-Axis Breakdown (Best Solver per Config)

| Config | Best solver | X std | Y std | Z std | 3D std |
| --- | --- | --- | --- | --- | --- |
| Dual-layer 8anc | V3-full | 13.0 | 18.7 | 33.8 | 40.7 |
| Upper only EFGH | AutoPos V2 | 21.0 | 39.9 | 92.4 | 102.8 |
| Lower only ABCD | V3-full | 16.2 | 27.9 | 58.7 | 67.0 |
| Best6 no DH | V4-interonly | 13.2 | 18.2 | 34.9 | 41.5 |
| Upper+AB | V3-full | 13.9 | 40.9 | 20.5 | 47.9 |
| Lower+EF | V4-interonly | 13.4 | 18.1 | 36.9 | 43.3 |


## Table 4: Z-Axis Degradation

| Config | Z std (best solver) | Z degradation vs dual-layer |
| --- | --- | --- |
| Dual-layer 8anc | 33.8 | 1.0x (reference) |
| Upper only EFGH | 92.4 | 2.7x |
| Lower only ABCD | 58.7 | 1.7x |
| Best6 no DH | 34.9 | 1.0x |
| Upper+AB | 20.5 | 0.6x |
| Lower+EF | 36.9 | 1.1x |


## Table 5: V3/V4 Delay Estimation (Dual-Layer Only)

| Anchor | V3-full delay | V4-io delay |
| --- | --- | --- |
| A | 0.0 | 0.0 |
| B | -8.2 | 22.3 |
| C | 28.4 | 20.4 |
| D | -25.3 | 11.6 |
| E | -47.8 | -5.9 |
| F | 42.7 | 3.8 |
| G | -29.2 | 60.0 |
| H | -10.8 | 9.6 |


## Table 6: V5 FIM Uncertainty (Dual-Layer Only)

| Anchor | sigma_x | sigma_y | sigma_z | sigma_3D | sigma_d |
| --- | --- | --- | --- | --- | --- |
| A | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| B | 22.952 | 0.000 | 0.000 | 22.952 | 19.426 |
| C | 30.128 | 36.527 | 0.000 | 47.349 | 19.562 |
| D | 23.245 | 20.974 | 62.088 | 69.535 | 18.552 |
| E | 23.073 | 32.640 | 22.965 | 46.099 | 18.924 |
| F | 29.709 | 44.043 | 30.990 | 61.504 | 19.190 |
| G | 58.640 | 50.038 | 65.945 | 101.445 | 62.784 |
| H | 24.670 | 32.487 | 58.120 | 71.007 | 18.530 |


V4 dual-layer FIM condition number: `1.223e+03`.


## Table 7: Bootstrap Results (50 trials, best solver per config)

| Config | Solver | 3D std mean+/-std | Z std mean+/-std |
| --- | --- | --- | --- |
| Dual-layer 8anc | V3-full | 41.1 +/- 0.5 | 34.3 +/- 0.6 |
| Upper only EFGH | AutoPos V2 | 68.2 +/- 35.3 | 46.2 +/- 46.7 |
| Lower only ABCD | V3-full | 67.1 +/- 0.6 | 58.8 +/- 0.7 |
| Best6 no DH | V4-interonly | 41.6 +/- 0.1 | 34.9 +/- 0.1 |
| Upper+AB | V3-full | 48.2 +/- 0.5 | 20.3 +/- 0.3 |
| Lower+EF | V4-interonly | 43.2 +/- 0.1 | 36.9 +/- 0.1 |


## Table 8: Statistical Tests

| Comparison | Test | p-value | Significant? |
| --- | --- | --- | --- |
| Dual vs Upper-only (Z std) | Wilcoxon | 0.0021 | Yes |
| Dual vs Lower-only (Z std) | Wilcoxon | 0.0000 | Yes |
| Dual vs Best6 (3D std) | Wilcoxon | 0.0000 | Yes |
| V1 vs V4 on Dual (3D std) | Wilcoxon | 0.0000 | Yes |


## Table 9: AutoPos V1-V5 Progression (Dual-Layer, sigma-weighted)

| Solver | Fusion | Delay | 3D std | Delta from V1 |
| --- | --- | --- | --- | --- |
| V1 | simple avg | No | 41.3 | ref |
| V2 | IVW | No | 41.4 | 0.0 |
| V3-lite | MAD+MVUE | No | 41.3 | -0.0 |
| V3-full | MAD+MVUE | Tukey | 40.7 | -0.6 |
| V4-interonly | MAD+MVUE | Huber | 40.8 | -0.5 |


## Table 10: Algorithm Comparison (Dual-Layer, No Delay)

| Algorithm | Init method | Optimizer | 3D std | Inter RMS |
| --- | --- | --- | --- | --- |
| MDS+NLS | Classical MDS | LM/NLS | 41.3 | 42.1 |
| Ridolfi GD | MDS/trilateration | GD+NLS | 41.3 | 42.1 |
| SDP+NLS | SDP relaxation | LM/NLS | nan | nan |
| AutoPos V1 | MDS | LM/NLS | 41.3 | 42.1 |


## Figures

- `figures/layer_ablation_bar.png`
- `figures/z_degradation_bar.png`
- `figures/algorithm_comparison_scatter.png`
- `figures/bootstrap_boxplot.png`
- `figures/v1_to_v5_progression.png`
- `figures/solved_coordinates_3d.png`


## Key Findings

1. Dual-layer best Z std is 33.8 mm. Upper-only and lower-only best Z std are 92.4 mm and 58.7 mm, respectively.
2. Wilcoxon tests are reported in Table 8; dual vs upper-only Z p=0.00212, dual vs lower-only Z p=1.78e-15.
3. On the dual-layer configuration, MDS/Ridolfi/SDP/AutoPos no-delay baselines should be interpreted through Table 10; their differences are mostly initialization/optimizer effects after the same sigma-weighted positioning evaluation.
4. Delay estimation does not automatically help on this clean outdoor dataset; V3-full/V4 should be judged by both positioning std and delay magnitude, not by inlier RMS alone.
5. The solver initialization method matters less than anchor layer geometry and per-anchor range quality once NLS refinement is applied.
6. Best6 no DH is the practical stress test for minimum reliable 3D calibration: it keeps two lower and three upper anchors while removing the weakest D/H anchors.
7. The V1-V5 progression in Table 9 is the deployment-facing summary: it shows whether added fusion, delay estimation, and Huber joint solve actually improve the same ID02 metric.
8. AutoPos should be compared against Ridolfi and SDP in Table 10 under the same weighted Huber positioning evaluation, not under raw inter-anchor RMS alone.
9. Practical recommendation: use dual-layer anchors, sigma-weighted offline positioning, and explicit per-anchor quality handling; do not blindly trust delay variables when they hit large ranges.
10. To improve beyond the present floor, add stronger vertical geometry, CIR/quality features for NLOS rejection, better antenna orientation control, or newer DW3000-class ranging hardware.