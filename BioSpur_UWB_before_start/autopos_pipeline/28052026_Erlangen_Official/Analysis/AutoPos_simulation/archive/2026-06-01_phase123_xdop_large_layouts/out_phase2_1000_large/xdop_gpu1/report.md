# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all 200 layouts, true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0508, layout_0523, layout_0524, layout_0532, layout_0549, layout_0553, layout_0560, layout_0561, layout_0563, layout_0565, layout_0577, layout_0579, layout_0618, layout_0622, layout_0629, layout_0642, layout_0692, layout_0706, layout_0711, layout_0726, layout_0737, layout_0741, layout_0763, layout_0769, layout_0786, layout_0790, layout_0821, layout_0824, layout_0838, layout_0839, layout_0842, layout_0860, layout_0886, layout_0890, layout_0891, layout_0900, layout_0901, layout_0902, layout_0908, layout_0909, layout_0910, layout_0913, layout_0916, layout_0918, layout_0921, layout_0926, layout_0944, layout_0947, layout_0963, layout_0964, layout_0967, layout_0970, layout_0984, layout_0986, layout_0993, layout_0994, layout_0996, layout_0997

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 500 | 2.552 | 2.677 | 0.020 |
| v1-old | 500 | 2.552 | 2.675 | 0.020 |
| v2 | 500 | 2.560 | 2.683 | 0.020 |
| v3-lite | 500 | 2.552 | 2.675 | 0.020 |
| v3-full | 500 | 2.551 | 2.674 | 0.020 |
| v4-io | 500 | 2.567 | 2.688 | 0.021 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
