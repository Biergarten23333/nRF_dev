# Phase 2 Wall + Metal Box Analysis

## Setup

- Starts from Phase 1 wall/distance grid.
- Adds photo-inspired random metal/equipment boxes near the layout boundary.
- 12 random seeds per wall/distance scenario, 6 boxes per seed.
- Outputs below are medians across seeds unless noted.

## Key Distances

| walls | dist | Phase1 p95 | Phase2 median p95 | metal delta | ratio | failure>0.5m |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0cm | 0.062 | 0.322 | 0.260 | 5.18x | 0.005 |
| 0 | 40cm | 0.062 | 0.170 | 0.108 | 2.75x | 0.000 |
| 0 | 100cm | 0.063 | 0.087 | 0.024 | 1.39x | 0.000 |
| 1 | 0cm | 0.344 | 0.533 | 0.189 | 1.55x | 0.067 |
| 1 | 40cm | 0.129 | 0.238 | 0.109 | 1.85x | 0.000 |
| 1 | 100cm | 0.067 | 0.097 | 0.029 | 1.44x | 0.000 |
| 2 | 0cm | 0.449 | 0.656 | 0.206 | 1.46x | 0.197 |
| 2 | 40cm | 0.165 | 0.292 | 0.127 | 1.77x | 0.001 |
| 2 | 100cm | 0.072 | 0.104 | 0.032 | 1.44x | 0.000 |
| 3 | 0cm | 0.637 | 0.783 | 0.145 | 1.23x | 0.395 |
| 3 | 40cm | 0.237 | 0.355 | 0.118 | 1.50x | 0.005 |
| 3 | 100cm | 0.081 | 0.114 | 0.033 | 1.41x | 0.000 |
| 4 | 0cm | 0.764 | 0.892 | 0.128 | 1.17x | 0.546 |
| 4 | 40cm | 0.287 | 0.405 | 0.118 | 1.41x | 0.011 |
| 4 | 100cm | 0.088 | 0.123 | 0.035 | 1.40x | 0.000 |

## Main Reading

- Worst absolute Phase 2 case: 4 walls at 0 cm, median p95 = 0.892 m.
- Largest metal-box added error: 0 walls at 0 cm, delta = 0.260 m over Phase 1.
- Wall proximity remains the dominant effect; metal boxes add an extra tail, especially when walls are already close.
- The photo environment most closely maps to 3-wall/4-wall, 0-40 cm cases with side equipment and stands.

## Figures

- `figures/phase2/phase2_pos_err_p95_m_median_vs_distance.png`
- `figures/phase2/metal_delta_pos_p95_m_vs_distance.png`
- `figures/phase2/phase2_failure_gt_0p5m_ratio_median_vs_distance.png`
- `figures/phase2/phase2_pos_p95_heatmap.png`
- `figures/phase2/phase2_metal_delta_heatmap.png`
