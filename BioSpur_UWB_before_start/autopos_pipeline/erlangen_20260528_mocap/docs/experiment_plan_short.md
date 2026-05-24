# Erlangen MoCap Capture Plan - Field Version

This file is the plan shown inside the BioSpur AutoPos UI. It keeps the
outdoor full experiment plan content, but uses the short section names that the
UI parser reads.

## Baseline

- Capture mode: TR-only, 10 Hz, explicit target BS IDs.
- Do not push APOS layout to Tags during capture.
- Layout / position / trajectory are solved offline from raw TR.
- Static Tag: `BSF66F`.
- Roto Tags: `BS2DCE`, `BSDC91`.
- Wand Tags: `BSCCF4`, `BS9336`, `BS955A`.
- Wand geometry:
  - `BSCCF4 --285 mm-- T center --385 mm-- BS9336`
  - `T center --595 mm-- BS955A`

## Phase 0 - Port and Smoke Test

Run before real capture:

```bash
bio_ports
static -id PORT_TEST -s 20
bio_check_latest
```

Pass condition:

- Master_Anchor and Master_Tag ports both exist.
- Short BSF66F capture succeeds.
- `bio_check_latest` shows `success: true`.

## Phase 1 - AutoPos Sweep and F/G/H Ultrasound

Run:

```bash
sweep -id SW01
us30  -id US01
```

Record:

- Sweep folder.
- `summary.json` success.
- F/G/H ultrasound files: `ultrasound_F.csv`, `ultrasound_G.csv`, and `ultrasound_H.csv`.
- F/G/H antenna-center values from `*_ant_center_mm` columns.

Pass condition:

- Sweep completes with A--H.
- Final responder restore succeeds.
- US30 reaches `DONE` and then `USOFF`.

## Phase 2 - Static BSF66F Dataset

Use:

```bash
static -id ID01
```

Recommended IDs:

| ID | Plan |
|---|---|
| ID01 | Near ABEF face, low height, 60s, edge + low |
| ID02 | Near ABEF face, mid height, 60s, edge + mid |
| ID03 | Near ABEF face, high height, 60s, edge + high |
| ID04 | Near BCGF face, low height, 60s, edge + low |
| ID05 | Near BCGF face, mid height, 60s, edge + mid |
| ID06 | Near BCGF face, high height, 60s, edge + high |
| ID07 | Near CDHG face, low height, 60s, edge + low |
| ID08 | Near CDHG face, mid height, 60s, edge + mid |
| ID09 | Near CDHG face, high height, 60s, edge + high |
| ID10 | Near ADHE face, low height, 60s, edge + low |
| ID11 | Near ADHE face, mid height, 60s, edge + mid |
| ID12 | Near ADHE face, high height, 60s, edge + high |
| ID13 | Center mid, Tag faces ABEF, 60s, known orientation |
| ID14 | Center mid, Tag faces BCGF, 60s, known orientation |
| ID15 | Center mid, Tag faces CDHG, 60s, known orientation |
| ID16 | Center mid, Tag faces ADHE, 60s, known orientation |
| ID17 | Center low, Tag faces ABEF, 60s, known orientation |
| ID18 | Center low, Tag faces BCGF, 60s, known orientation |
| ID19 | Center low, Tag faces CDHG, 60s, known orientation |
| ID20 | Center low, Tag faces ADHE, 60s, known orientation |
| ID21 | Center high, Tag faces ABEF, 60s, known orientation |
| ID22 | Center high, Tag faces BCGF, 60s, known orientation |
| ID23 | Center high, Tag faces CDHG, 60s, known orientation |
| ID24 | Center high, Tag faces ADHE, 60s, known orientation |

If time is short, minimum static set:

```text
ID01, ID03, ID07, ID09, ID13, ID15, ID17, ID21, ID23
```

Notes:

- `BSF66F` should run around `10Hz/tag`.
- Each capture should see 8 anchors frequently.
- Static center-mid 3D std should be checked against the previous `40-50mm`
  baseline, but OptiTrack alignment is the real reference for Erlangen.
- `BS2DCE/BSDC91` are not the primary static dataset; they belong to Roto.

## Phase 3 - RotoArm Dataset

Use:

```bash
roto -id R01
```

Recommended IDs:

| ID | Plan |
|---|---|
| R01 | Almost planar, 180s, planar does not distinguish antenna face |
| R02 | Small tilt, antenna faces ABEF, 180s |
| R03 | Small tilt, antenna faces BCGF, 180s |
| R04 | Small tilt, antenna faces CDHG, 180s |
| R05 | Small tilt, antenna faces ADHE, 180s |
| R06 | Mid tilt, antenna faces ABEF, 180s |
| R07 | Mid tilt, antenna faces BCGF, 180s |
| R08 | Mid tilt, antenna faces CDHG, 180s |
| R09 | Mid tilt, antenna faces ADHE, 180s |
| R10 | High tilt, antenna faces ABEF, 180s |
| R11 | High tilt, antenna faces BCGF, 180s |
| R12 | High tilt, antenna faces CDHG, 180s |
| R13 | High tilt, antenna faces ADHE, 180s |
| R14 | Almost vertical, antenna faces ABEF, 180s |
| R15 | Almost vertical, antenna faces BCGF, 180s |
| R16 | Almost vertical, antenna faces CDHG, 180s |
| R17 | Almost vertical, antenna faces ADHE, 180s |

If time is short, minimum Roto set:

```text
R01, R02, R03, R04, R05
```

Notes:

- Roto uses `BS2DCE` and `BSDC91`.
- True trajectory is approximately a 3D circle.
- Post-process with circle-fit residual, radius consistency, and two-tag
  center/normal consistency.
- The face label is only a repeatable physical orientation label.
- Do not over-interpret it as the exact UWB antenna main-lobe direction.
- Dynamic pure-UWB residual around `100-300mm` RMS can still be normal.

## Phase 4 - Wand Dataset

Use:

```bash
wand -id W01
```

Recommended IDs:

| ID | Plan |
|---|---|
| W01 | Fixed on small fixture, AB is local triangle base, AB near vertical, BSCCF4 upper, BS9336 lower, BS955A tip points to ABEF, 120s |
| W02 | Same fixture, rotate around center of mass, BS955A tip points to BCGF, 120s |
| W03 | Same fixture, rotate around center of mass, BS955A tip points to CDHG, 120s |
| W04 | Same fixture, rotate around center of mass, BS955A tip points to ADHE, 120s |
| W05 | Free move like OptiTrack calibration wand, slow spatial move and rotation, 180s |

Known Wand geometry:

```text
BSCCF4 --285 mm-- T center --385 mm-- BS9336
T center --595 mm-- BS955A
```

Notes:

- Wand is not only for validation; it can provide rigid-body constraints for
  anchor layout refinement.
- Do not require a perfectly horizontal table.
- For fixed captures, keep the Wand as still as possible.
- In windy environment the Wand may slightly turn; relative geometry matters,
  absolute fixture pose should not be treated as ground truth.
- `BS955A tip points to face X` is a repeatable pose label, not a strict antenna
  main-lobe definition.
- Free Move should be analyzed separately from fixed-pose repeatability.

## Stop Conditions

Pause and debug if any of these happen:

- `bio_ports` shows missing Master_Anchor or Master_Tag path.
- Anchor preflight cannot reach 8/8 ready.
- Sweep misses one anchor for a long time.
- Capture `summary.json` has `success: false`.
- Static/Roto/Wand capture has sustained fewer than 7 anchors.
- TDMA verify fails for one or more target Tags.
- Wand three Tags cannot all maintain around `10Hz/tag`.
- US30 stays running after `USOFF` or US text appears in normal Tag capture raw
  logs.

## Notes to Record

Use:

```bash
bio_note ID01 "OptiTrack file=..., position=..., height=..., facing=..."
```

For every capture, record at least:

- ID.
- OptiTrack filename or trial number.
- Physical position and orientation.
- Any occlusion / LOS / BLE instability.
- Whether anchors were moved after the sweep.

## Minimal Day Plan

If time is limited:

```bash
bio_ports
static -id PORT_TEST -s 20
sweep -id SW01
us30 -id US01

static -id ID13
static -id ID17
static -id ID21
static -id ID07
static -id ID09

roto -id R01
roto -id R02
roto -id R04

wand -id W01
wand -id W05
```

This gives center/height structure, one hard CDHG edge case, basic Roto, and
basic Wand consistency.
