# AutoPos xDOP Phase 3 Report

## Scope

- Phase 1: all available layouts (500), true + five solver geometries, 100 mm grid, all8.
- Phase 3: selected top/bottom/representative layouts, true + five solver geometries, 50 mm grid, all8 + drop-one.
- xDOP model: range-only unit-vector Jacobian, Q = inv(G^T G).

## Selected Phase 3 Layouts

layout_0507, layout_0510, layout_0517, layout_0525, layout_0528, layout_0566, layout_0609, layout_0614, layout_0619, layout_0641, layout_0645, layout_0661, layout_0666, layout_0680, layout_0700, layout_0718, layout_0728, layout_0733, layout_0741, layout_0743, layout_0776, layout_0795, layout_0802, layout_0821, layout_0824, layout_0825, layout_0833, layout_0838, layout_0851, layout_0852, layout_0854, layout_0860, layout_0866, layout_0868, layout_0881, layout_0883, layout_0884, layout_0886, layout_0887, layout_0894, layout_0896, layout_0902, layout_0908, layout_0909, layout_0911, layout_0938, layout_0944, layout_0949, layout_0951, layout_0964, layout_0967, layout_0978, layout_0983, layout_0984, layout_0986, layout_0993, layout_0996, layout_0997

## Phase 1 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | bad VDOP>3 median |
|---|---:|---:|---:|---:|
| true | 500 | 1.707 | 1.886 | 0.000 |
| v1-old | 500 | 1.707 | 1.886 | 0.000 |
| v2 | 500 | 1.709 | 1.887 | 0.000 |
| v3-lite | 500 | 1.707 | 1.886 | 0.000 |
| v3-full | 500 | 1.708 | 1.886 | 0.000 |
| v4-io | 500 | 1.711 | 1.887 | 0.000 |

## Phase 3 Geometry Summary

| geometry | n | VDOP p90 median | GDOP p90 median | drop/full details |
|---|---:|---:|---:|---|

## Outputs

- `phase1_xdop_grid100.csv`
- `phase1_layout_ranking.csv`
- `phase3_xdop_grid50.csv`
- `phase3_drop_robustness.csv`
- `figures/*.png`
