# A-H Inter-Anchor Matrix Progress

Date: 2026-03-18

Purpose:
- Collect the full `8x8` inter-anchor distance matrix for `A..H`.
- Use rotating visible masters because `A` has unreliable USB console output.
- Keep all workers in `allow_tag_polls=0` mode during matrix collection.

Board mapping:
- `A = 0` -> `760186071`
- `B = 1` -> `760185876`
- `C = 2` -> `760185878`
- `D = 3` -> `760186081`
- `E = 4` -> `760185904`
- `F = 5` -> `760186124`
- `G = 6` -> `760185889`
- `H = 7` -> `760186121`

Geometry note:
- `ABCD` are the lower plane.
- `EFGH` are the upper plane.
- `A` under `E`, `B` under `F`, `C` under `G`, `D` under `H`.
- Approximate plane separation: `140 cm`.

Measurement method:
- `SS-TWR anchor-anchor` mode.
- The listed values are filtered distances reported by the visible master.
- Different rows may not be perfectly symmetric yet because they were collected in different sweeps and with different active masters.

Collected rows:

## Row B

Source:
- `B = 760185876`
- `B` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for B`

| Edge | Filtered distance |
| --- | ---: |
| B-A | 3720 mm |
| B-C | 3792 mm |
| B-D | 5533 mm |
| B-E | 4829 mm |
| B-F | 1419 mm |
| B-G | 4071 mm |
| B-H | 5583 mm |

## Row C

Source:
- `C = 760185878`
- `C` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for C`

| Edge | Filtered distance |
| --- | ---: |
| C-A | 5109 mm |
| C-B | 3793 mm |
| C-D | 3948 mm |
| C-E | 5317 mm |
| C-F | 4078 mm |
| C-G | 1524 mm |
| C-H | 4123 mm |

## Row D

Source:
- `D = 760186081`
- `D` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for D`

| Edge | Filtered distance |
| --- | ---: |
| D-A | 4083 mm |
| D-B | 5525 mm |
| D-C | 3955 mm |
| D-E | 4008 mm |
| D-F | 5782 mm |
| D-G | 4260 mm |
| D-H | 1562 mm |

## Row E

Source:
- `E = 760185904`
- `E` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for E`

| Edge | Filtered distance |
| --- | ---: |
| E-A | 1823 mm |
| E-B | 4871 mm |
| E-C | 5336 mm |
| E-D | 3966 mm |
| E-F | 3477 mm |
| E-G | 5222 mm |
| E-H | 3634 mm |

## Row F

Source:
- `F = 760186124`
- `F` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for F`

| Edge | Filtered distance |
| --- | ---: |
| F-A | 4225 mm |
| F-B | 1477 mm |
| F-C | 4055 mm |
| F-D | 5788 mm |
| F-E | 3435 mm |
| F-G | 3888 mm |
| F-H | 5501 mm |

## Row G

Source:
- `G = 760185889`
- `G` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for G`

| Edge | Filtered distance |
| --- | ---: |
| G-A | 5773 mm |
| G-B | 4077 mm |
| G-C | 1515 mm |
| G-D | 4216 mm |
| G-E | 5235 mm |
| G-F | 3855 mm |
| G-H | 3938 mm |

## Row H

Source:
- `H = 760186121`
- `H` flashed as `master full-sweep`
- Values taken from `Anchor sweep 3 complete for H`

| Edge | Filtered distance |
| --- | ---: |
| H-A | 4272 mm |
| H-B | 5548 mm |
| H-C | 4106 mm |
| H-D | 1485 mm |
| H-E | 3621 mm |
| H-F | 5862 mm |
| H-G | 3947 mm |

Current partial matrix:

|   | A | B | C | D | E | F | G | H |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 0 | 3720 | 5109 | 4083 | 1823 | 4225 | 5773 | 4272 |
| B | 3720 | 0 | 3792 | 5533 | 4829 | 1419 | 4071 | 5583 |
| C | 5109 | 3793 | 0 | 3948 | 5317 | 4078 | 1524 | 4123 |
| D | 4083 | 5525 | 3955 | 0 | 4008 | 5782 | 4260 | 1562 |
| E | 1823 | 4871 | 5336 | 3966 | 0 | 3477 | 5222 | 3634 |
| F | 4225 | 1477 | 4055 | 5788 | 3435 | 0 | 3888 | 5501 |
| G | 5773 | 4077 | 1515 | 4216 | 5235 | 3855 | 0 | 3938 |
| H | 4272 | 5548 | 4106 | 1485 | 3621 | 5862 | 3947 | 0 |

Notes:
- `B-G` and `G-B` differ by `6 mm`, which is acceptable at this stage and shows the two independent sweeps are consistent.
- `C-G` and `G-C` differ by `9 mm`, which is also consistent across two independent sweeps.
- `C-D` and `D-C` differ by `7 mm`, which is acceptable at this stage.
- `F-H` is the noisiest pair: `F-H=5501 mm` from row `F` versus `H-F=5862 mm` from row `H`. The `F` row was more stable across sweeps and is the better value to trust for a first solver pass.
- `A` still has silent USB console output, but it responds correctly over UWB and can be included in the matrix through other visible masters.
- The matrix is now complete enough for a first autopositioning solve.
- The current offline solver input is stored in `data/inter_anchor_matrix_ah.json`.
