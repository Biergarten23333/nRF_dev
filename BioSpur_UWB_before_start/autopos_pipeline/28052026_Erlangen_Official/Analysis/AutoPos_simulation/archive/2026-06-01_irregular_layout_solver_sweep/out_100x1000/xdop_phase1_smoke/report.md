# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all 200 layouts, true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0055, layout_0176

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 200 | 1.467 | 1.828 | 0.000 |
| v1-old | 200 | 1.466 | 1.828 | 0.000 |
| v2 | 200 | 1.468 | 1.828 | 0.000 |
| v3-lite | 200 | 1.467 | 1.828 | 0.000 |
| v3-full | 200 | 1.467 | 1.828 | 0.000 |
| v4-io | 200 | 1.467 | 1.827 | 0.000 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|
| true | 2 | 1.431 | 1.778 | see `phase3_drop_robustness.csv` |
| v1-old | 2 | 1.430 | 1.777 | see `phase3_drop_robustness.csv` |
| v2 | 2 | 1.423 | 1.757 | see `phase3_drop_robustness.csv` |
| v3-lite | 2 | 1.431 | 1.777 | see `phase3_drop_robustness.csv` |
| v3-full | 2 | 1.431 | 1.776 | see `phase3_drop_robustness.csv` |
| v4-io | 2 | 1.423 | 1.757 | see `phase3_drop_robustness.csv` |

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
