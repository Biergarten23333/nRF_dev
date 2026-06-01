# Phase 3 Material-Aware Wall Analysis

## Setup

- Same 3m x 3m x 1.4m paired anchor layout as Phase 1/2.
- Same 0/1/2/3/4 wall count and 0-100cm distance sweep.
- Adds German indoor wall material classes with relative bias/sigma/grazing parameters.

## Safe Distance Table

Safe distance means the smallest wall distance where 3D p95 error is below the threshold.

| material | walls | safe <0.15m | safe <0.25m | p95 @0cm | p95 @40cm | p95 @100cm |
|---|---:|---:|---:|---:|---:|---:|
| light_drywall | 1 | 0 | 0 | 0.096 | 0.069 | 0.063 |
| light_drywall | 2 | 0 | 0 | 0.121 | 0.076 | 0.064 |
| light_drywall | 3 | 5 | 0 | 0.163 | 0.086 | 0.065 |
| light_drywall | 4 | 15 | 0 | 0.195 | 0.095 | 0.066 |
| gypsum_block | 1 | 0 | 0 | 0.142 | 0.077 | 0.064 |
| gypsum_block | 2 | 10 | 0 | 0.185 | 0.091 | 0.065 |
| gypsum_block | 3 | 25 | 5 | 0.269 | 0.113 | 0.068 |
| gypsum_block | 4 | 35 | 15 | 0.327 | 0.132 | 0.070 |
| aerated_concrete | 1 | 10 | 0 | 0.181 | 0.086 | 0.064 |
| aerated_concrete | 2 | 25 | 0 | 0.237 | 0.104 | 0.067 |
| aerated_concrete | 3 | 40 | 15 | 0.351 | 0.137 | 0.070 |
| aerated_concrete | 4 | 50 | 25 | 0.428 | 0.161 | 0.073 |
| sand_lime_brick | 1 | 25 | 5 | 0.258 | 0.106 | 0.066 |
| sand_lime_brick | 2 | 35 | 15 | 0.338 | 0.133 | 0.069 |
| sand_lime_brick | 3 | 50 | 30 | 0.508 | 0.184 | 0.075 |
| sand_lime_brick | 4 | 60 | 35 | 0.622 | 0.221 | 0.080 |
| reinforced_concrete_C25_30 | 1 | 40 | 15 | 0.369 | 0.137 | 0.068 |
| reinforced_concrete_C25_30 | 2 | 50 | 25 | 0.484 | 0.175 | 0.073 |
| reinforced_concrete_C25_30 | 3 | 70 | 50 | 0.669 | 0.252 | 0.082 |
| reinforced_concrete_C25_30 | 4 | 80 | 50 | 0.793 | 0.305 | 0.091 |
| reinforced_concrete_C30_37 | 1 | 50 | 25 | 0.435 | 0.160 | 0.070 |
| reinforced_concrete_C30_37 | 2 | 60 | 35 | 0.569 | 0.205 | 0.077 |
| reinforced_concrete_C30_37 | 3 | 70 | 50 | 0.758 | 0.299 | 0.089 |
| reinforced_concrete_C30_37 | 4 | 80 | 60 | 0.889 | 0.363 | 0.099 |
| reinforced_concrete_C40_50 | 1 | 50 | 30 | 0.495 | 0.186 | 0.072 |
| reinforced_concrete_C40_50 | 2 | 70 | 40 | 0.648 | 0.238 | 0.081 |
| reinforced_concrete_C40_50 | 3 | 80 | 60 | 0.854 | 0.350 | 0.096 |
| reinforced_concrete_C40_50 | 4 | 90 | 70 | 0.998 | 0.426 | 0.109 |

## Figures

- `figures/phase3/phase3_material_4wall_pos_p95.png`
- `figures/phase3/phase3_material_heatmap_000cm.png`
- `figures/phase3/phase3_material_heatmap_040cm.png`
- `figures/phase3/phase3_material_heatmap_100cm.png`
