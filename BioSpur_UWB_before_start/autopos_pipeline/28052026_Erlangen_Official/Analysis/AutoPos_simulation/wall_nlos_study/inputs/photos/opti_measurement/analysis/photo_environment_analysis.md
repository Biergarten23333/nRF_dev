# Opti Measurement Environment Photo Analysis

## Source

- Archive: `/home/zekaixiao/Downloads/Gmail.zip`
- Extracted photos: `AutoPos_simulation/wall_nlos_study/inputs/photos/opti_measurement/raw/`
- Contact sheet: `contact_sheet.jpg`

## Observed Environment

- The measurement area is a narrow rectangular indoor test space with green floor mat.
- Side walls are close to the working area. The right wall has light wall panels and a lower rough/wood-textured band.
- The rear side has a large white curtain/screen-like wall surface.
- There is a large black ceiling/overhead soft panel above the rear/center area.
- Multiple tripod stands and OptiTrack/camera-like devices sit close to the working volume.
- There are tables, monitors, cables, electronics, and black equipment boxes near the boundary.
- Several metallic/black vertical poles and horizontal support bars are close to likely UWB paths.

## Phase 2 Modelling Implications

- Treat side and rear walls as the dominant fixed reflective boundaries.
- Use 2-wall, 3-wall, and 4-wall cases as the most realistic cases.
- Distances below 40 cm are especially important because anchors/equipment appear close to walls and stands.
- Metal objects should not be sampled uniformly everywhere. They should be sampled outside or near the layout boundary, especially:
  - side-wall zones,
  - table/equipment zones,
  - tripod/stand zones,
  - rear wall/screen zone.
- The first Phase 2 model should use box-shaped reflectors with random sizes from small electronics to medium equipment boxes.

## Recommended Initial Phase 2 Parameters

- Layout: 3m x 3m x 1.4m paired anchors.
- Wall counts: 0, 1, 2, 3, 4.
- Distance sweep: 0-100 cm, same as Phase 1.
- Metal box seeds per scenario: 12 initially.
- Metal boxes per seed: 6 initially.
- Metal box sizes:
  - small electronics: 0.25-0.50 m,
  - monitor/equipment box: 0.4-0.9 m,
  - vertical stand-like box approximation: narrow depth, height up to 1.2 m.
- Primary outputs:
  - position p95,
  - Z p95,
  - horizontal p95,
  - failure ratio above 0.5 m and 1.0 m,
  - comparison against Phase 1 wall-only baseline.

## Assumptions

- This is not a full ray-tracing model.
- Phase 2 uses a first-order wall/metal multipath risk model with positive range bias and increased variance.
- Photos provide qualitative placement priors for reflectors, not exact dimensions.
