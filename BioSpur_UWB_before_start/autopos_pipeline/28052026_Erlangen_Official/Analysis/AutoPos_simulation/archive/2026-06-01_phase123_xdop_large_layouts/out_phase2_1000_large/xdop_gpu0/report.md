# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all 200 layouts, true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0007, layout_0012, layout_0024, layout_0025, layout_0039, layout_0042, layout_0051, layout_0055, layout_0064, layout_0066, layout_0102, layout_0106, layout_0110, layout_0119, layout_0126, layout_0133, layout_0134, layout_0137, layout_0146, layout_0149, layout_0158, layout_0179, layout_0187, layout_0191, layout_0195, layout_0205, layout_0209, layout_0226, layout_0227, layout_0241, layout_0272, layout_0283, layout_0285, layout_0287, layout_0295, layout_0309, layout_0314, layout_0315, layout_0327, layout_0335, layout_0337, layout_0347, layout_0354, layout_0361, layout_0372, layout_0376, layout_0377, layout_0379, layout_0381, layout_0394, layout_0411, layout_0414, layout_0418, layout_0426, layout_0440, layout_0447, layout_0482, layout_0495

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 500 | 2.676 | 2.779 | 0.017 |
| v1-old | 500 | 2.674 | 2.778 | 0.017 |
| v2 | 500 | 2.681 | 2.782 | 0.017 |
| v3-lite | 500 | 2.675 | 2.780 | 0.017 |
| v3-full | 500 | 2.675 | 2.779 | 0.017 |
| v4-io | 500 | 2.681 | 2.783 | 0.018 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
