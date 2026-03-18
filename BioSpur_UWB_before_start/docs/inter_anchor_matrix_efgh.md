# EFGH Inter-Anchor Matrix

Date: 2026-03-17

Geometry note:
- `E/F/G/H` are nearly coplanar.
- Physical order is clockwise: `E -> F -> G -> H`.

Board mapping:
- `E` -> `760185904`
- `F` -> `760186124`
- `G` -> `760185889`
- `H` -> `760186121`

Measurement method:
- `SS-TWR anchor-anchor` mode.
- One anchor was flashed as temporary master, the others as workers.
- Matrix values below are from the filtered distance reported by the master.
- `Tag 760186127` was still powered and occasionally polling anchors during the runs, so this matrix is good for bring-up and geometry sanity checks, not final calibration.

Current inter-anchor matrix:

| Edge | Filtered distance |
| --- | ---: |
| E-F | 3406 mm |
| E-G | 5221 mm |
| E-H | 3641 mm |
| F-G | 3841 mm |
| F-H | 5798 mm |
| G-H | 3952 mm |

Symmetric matrix:

|   | E | F | G | H |
| --- | ---: | ---: | ---: | ---: |
| E | 0 | 3406 | 5221 | 3641 |
| F | 3406 | 0 | 3841 | 5798 |
| G | 5221 | 3841 | 0 | 3952 |
| H | 3641 | 5798 | 3952 | 0 |

Deployment state after measurement:
- `E` restored to master image.
- `F/G/H` restored to worker images.
