# Anchor Topology A-H

Date: 2026-03-17

Anchor IDs:
- `A = 0` -> `760186071`
- `B = 1` -> `760185876`
- `C = 2` -> `760185878`
- `D = 3` -> `760186081`
- `E = 4` -> `760185904`
- `F = 5` -> `760186124`
- `G = 6` -> `760185889`
- `H = 7` -> `760186121`

Geometry:
- `ABCD` are the lower plane.
- `EFGH` are the upper plane.
- Both planes are arranged clockwise.
- Vertical pairing:
  - `A` under `E`
  - `B` under `F`
  - `C` under `G`
  - `D` under `H`
- `XY` is only approximately aligned between the two planes.
- Approximate plane separation: `140 cm`

Current ranging strategy:
- `A` is the first master / initiator anchor.
- Worker anchors respond in `SS-TWR responder` mode.
- Default inter-anchor schedule uses the upper triangle of the matrix:
  - `A -> B,C,D,E,F,G,H`
  - `B -> C,D,E,F,G,H`
  - `C -> D,E,F,G,H`
  - `D -> E,F,G,H`
  - `E -> F,G,H`
  - `F -> G,H`
  - `G -> H`

Notes:
- This schedule avoids duplicate edges.
- It is suitable for building the full `8x8` symmetric distance matrix needed for autopositioning.
