# Phase 1 Wall Distance Analysis

## Setup

- Layout: paired 3m x 3m x 1.4m anchors.
- Ceiling assumption: 2.5m; ceiling not modelled as NLOS source in Phase 1.
- Wall cases: 0, 1, 2, 3, 4 reflective side walls.
- Distance sweep: 0, 5, 10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 80, 90, 100 cm.
- Error model: LOS Gaussian range noise plus near-wall/grazing positive bias and extra variance.

## Key Metrics

| walls | p95 @ 0cm | p95 @ 100cm | ratio | best distance | best p95 | worst distance | worst p95 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0.062 | 0.063 | 0.99x | 40cm | 0.062 | 15cm | 0.063 |
| 1 | 0.344 | 0.067 | 5.11x | 100cm | 0.067 | 0cm | 0.344 |
| 2 | 0.449 | 0.072 | 6.22x | 100cm | 0.072 | 0cm | 0.449 |
| 3 | 0.637 | 0.081 | 7.89x | 100cm | 0.081 | 0cm | 0.637 |
| 4 | 0.764 | 0.088 | 8.72x | 100cm | 0.088 | 0cm | 0.764 |

## Reading

- 0-wall is the baseline and should stay almost flat across distance because no wall is active.
- More walls and smaller wall distance increase positive range bias and error tails.
- Phase 2 should focus on 2-wall/3-wall/4-wall cases and distances below 40cm, because that is where the photo environment looks most realistic.

## Figures

- `figures/phase1/pos_err_p95_m_vs_distance.png`
- `figures/phase1/z_err_p95_m_vs_distance.png`
- `figures/phase1/hor_err_p95_m_vs_distance.png`
- `figures/phase1/failure_gt_0p5m_ratio_vs_distance.png`
- `figures/phase1/phase1_pos_p95_heatmap.png`
