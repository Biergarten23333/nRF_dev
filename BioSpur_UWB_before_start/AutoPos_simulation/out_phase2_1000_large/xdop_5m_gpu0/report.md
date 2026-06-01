# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all available layouts (500), true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0000, layout_0001, layout_0006, layout_0008, layout_0018, layout_0024, layout_0039, layout_0040, layout_0049, layout_0059, layout_0066, layout_0074, layout_0075, layout_0099, layout_0102, layout_0117, layout_0128, layout_0134, layout_0136, layout_0137, layout_0186, layout_0193, layout_0195, layout_0212, layout_0230, layout_0233, layout_0241, layout_0290, layout_0295, layout_0307, layout_0309, layout_0327, layout_0344, layout_0370, layout_0371, layout_0372, layout_0376, layout_0381, layout_0396, layout_0403, layout_0406, layout_0411, layout_0412, layout_0418, layout_0419, layout_0431, layout_0443, layout_0444, layout_0447, layout_0458, layout_0459, layout_0463, layout_0464, layout_0465, layout_0469, layout_0481, layout_0496, layout_0499

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 500 | 2.487 | 2.595 | 0.000 |
| v1-old | 500 | 2.486 | 2.595 | 0.000 |
| v2 | 500 | 2.490 | 2.598 | 0.000 |
| v3-lite | 500 | 2.487 | 2.595 | 0.000 |
| v3-full | 500 | 2.488 | 2.595 | 0.000 |
| v4-io | 500 | 2.491 | 2.600 | 0.000 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
