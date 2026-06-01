# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all available layouts (1000), true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0001, layout_0018, layout_0024, layout_0030, layout_0072, layout_0104, layout_0139, layout_0186, layout_0195, layout_0222, layout_0230, layout_0233, layout_0236, layout_0241, layout_0295, layout_0297, layout_0309, layout_0315, layout_0327, layout_0344, layout_0370, layout_0376, layout_0403, layout_0411, layout_0412, layout_0431, layout_0444, layout_0459, layout_0465, layout_0510, layout_0517, layout_0525, layout_0566, layout_0583, layout_0607, layout_0609, layout_0619, layout_0710, layout_0730, layout_0743, layout_0750, layout_0771, layout_0813, layout_0822, layout_0825, layout_0893, layout_0908, layout_0909, layout_0923, layout_0926, layout_0944, layout_0947, layout_0974, layout_0984, layout_0986, layout_0993, layout_0996, layout_0997

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 1000 | 1.854 | 2.015 | 0.000 |
| v1-old | 1000 | 1.854 | 2.015 | 0.000 |
| v2 | 1000 | 1.857 | 2.016 | 0.000 |
| v3-lite | 1000 | 1.854 | 2.015 | 0.000 |
| v3-full | 1000 | 1.854 | 2.015 | 0.000 |
| v4-io | 1000 | 1.857 | 2.017 | 0.000 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|
| true | 58 | 1.822 | 1.983 | see `phase3_drop_robustness.csv` |
| v1-old | 58 | 1.822 | 1.983 | see `phase3_drop_robustness.csv` |
| v2 | 58 | 1.824 | 1.985 | see `phase3_drop_robustness.csv` |
| v3-lite | 58 | 1.822 | 1.983 | see `phase3_drop_robustness.csv` |
| v3-full | 58 | 1.822 | 1.983 | see `phase3_drop_robustness.csv` |
| v4-io | 58 | 1.823 | 1.983 | see `phase3_drop_robustness.csv` |

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
