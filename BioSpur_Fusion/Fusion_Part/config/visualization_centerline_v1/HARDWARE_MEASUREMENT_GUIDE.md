# VISUALIZATION_CENTERLINE_V1 hardware measurement guide

This guide collects only directly observable distances and directions. Do **not** calculate XYZ coordinates, rotation matrices or Euler angles. The deterministic compiler performs that conversion and records every axis/sign choice.

Measure one representative assembly only after confirming that all ten PCBs use the same enclosure registration. Record three raw repeats in millimetres, the instrument resolution, the date on which these new measurements were actually taken, the operator, and labelled photo references. Do not enter the historical capture date unless the measurement truly occurred that day.

## Named physical faces and frames

The verified board frame `B` has its origin at the U4/DWM1001C CAD reference on the PCB top plane. `B+z` leaves the PCB top/component face. `B+x` and `B+y` follow the verified PCB CAD axes.

Top/component-side view of the actual board convention:

```text
                         B+y / edge B_POS_Y
                                  ↑
              edge B_NEG_X  ┌─────┼─────┐  edge B_POS_X
                            │     │     │
                            │  U4 ●─────┼────→ B+x
                            │ DWM1001C  │
                            └─────┼─────┘
                                  ↓
                         edge B_NEG_Y

                    B+z points out of this page
                    ● = U4 CAD reference / B origin
```

The enclosure frame `E` is defined from physical faces, not from an operator-computed coordinate:

```text
Non-body-facing view (look through E+z toward the assembly)

                         E_POS_Y_LONG_SIDE
                    ┌────────────────────────┐
                    │                        │
 E_NONANTENNA_END   │          +E+y          │   E_ANTENNA_END
 SHORT_FACE         │           ↑            │   SHORT_FACE
        -E+x  ←─────┼───────────●────────────┼─────→ +E+x
                    │                        │
                    └────────────────────────┘
                         E_NEG_Y_LONG_SIDE

 ● = enclosure geometric centre
 +E+x points toward the named antenna-end short face
 +E+z points away from the body-facing enclosure face
```

Side view:

```text
                    non-body-facing enclosure face
                  ┌────────────────────────────┐
             +E+z ↑                            │
                  │  PCB top plane  ─────────  │  B+z is normal to this plane
                  │                            │
                  └────────────────────────────┘
                    body-facing enclosure face   z = -thickness/2

Measure the perpendicular distance from the PCB top plane to the
body-facing enclosure face; do not assign it a signed z value.
```

## Direct distance measurements

Use `v47_visualization_hardware_measurements.csv`:

1. Measure enclosure outer long, short and thickness.
2. From the U4 reference, measure perpendicular distance to both short faces: `E_ANTENNA_END_SHORT_FACE` and `E_NONANTENNA_END_SHORT_FACE`.
3. From the U4 reference, measure perpendicular distance to both long-side faces: `E_POS_Y_LONG_SIDE` and `E_NEG_Y_LONG_SIDE`.
4. Measure from the PCB top plane to the body-facing enclosure face.
5. Measure total free mechanical play along the enclosure long direction, short direction and thickness direction. Enter a non-negative magnitude, not a signed displacement.
6. Measure strap width.

## Direct orientation observations

Select the physically visible answer; do not calculate an angle:

- `pcb_edge_toward_antenna_end`: one of `B_POS_X`, `B_NEG_X`, `B_POS_Y`, `B_NEG_Y`.
- `pcb_x_edge_relation_to_enclosure_long_axis`: `PARALLEL` when the B+x/B−x edge direction runs along the enclosure long axis, otherwise `PERPENDICULAR`.
- `pcb_top_component_face_points`: `AWAY_FROM_BODY` when the U4/component side faces away from the body-facing enclosure face; otherwise `TOWARD_BODY`.
- `identical_PCB_enclosure_registration`: `YES` only if the same transform legitimately applies to all ten assemblies.

Photographs should identify the antenna-end face, body-facing face, U4 reference, and ruler/caliper placement. A photo reference is evidence metadata; it is not a substitute for a missing distance.

## Compiler conversion

The compiler derives:

- U4/B-origin position in `E` from paired face distances;
- `R_E_from_B` from the named B edge, the top-face direction and right-handed cross products;
- a closure audit comparing paired face distances with enclosure dimensions;
- the bounded printed-antenna/U4 region in both B and E frames;
- non-zero uncertainty from repeatability, instrument resolution, RF-region extent and mechanical play.

The compiled provenance records the selected faces, edge mapping, z sign, handedness construction and all source row identifiers.
