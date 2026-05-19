# Erlangen MoCap Experiment Plan - Short Version

Goal: collect one clean OptiTrack-aligned dataset for AutoPos validation. Keep the
field procedure simple: capture raw TR data and write clear IDs. Solver and paper
analysis can happen later.

## Baseline

- Broadcast timing: `tail900 start5`.
- Capture mode: TR-only, 10 Hz, explicit target BS IDs.
- Output root after `bio_setup`:

```text
autopos_pipeline/erlangen_20260528_mocap/captures/erlangen_20260528_optitrack
```

## Device Groups

```text
Static Tag: BSF66F
Roto Tags:  BS2DCE, BSDC91
Wand Tags:  BS9336, BS955A, BSCCF4
Anchor H:   BS506D, UUID B1E487C2B1FD740D1442206A1857DFA1
```

## Phase 0 - Port and Smoke Test

Run this before real capture:

```bash
bio_ports
static -id PORT_TEST -s 20
bio_check_latest
```

Pass condition:

- Master_Anchor and Master_Tag ports both exist.
- Short BSF66F capture succeeds.
- `bio_check_latest` shows `success: true`.

## Phase 1 - AutoPos Sweep and H Ultrasound

Run:

```bash
sweep -id SW01
us30  -id US01
```

Record:

- Sweep folder.
- `summary.json` success.
- H ultrasound `ultrasound_H.csv`.
- H antenna-center value from `*_ant_center_mm` columns.

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

| ID | Position |
|---|---|
| ID01 | near ABEF, low |
| ID02 | near ABEF, mid |
| ID03 | near ABEF, high |
| ID04 | near BCGF, low |
| ID05 | near BCGF, mid |
| ID06 | near BCGF, high |
| ID07 | near CDHG, low |
| ID08 | near CDHG, mid |
| ID09 | near CDHG, high |
| ID10 | near ADHE, low |
| ID11 | near ADHE, mid |
| ID12 | near ADHE, high |
| ID13 | center mid, faces ABEF |
| ID14 | center mid, faces BCGF |
| ID15 | center mid, faces CDHG |
| ID16 | center mid, faces ADHE |
| ID17 | center low, faces ABEF |
| ID18 | center low, faces BCGF |
| ID19 | center low, faces CDHG |
| ID20 | center low, faces ADHE |
| ID21 | center high, faces ABEF |
| ID22 | center high, faces BCGF |
| ID23 | center high, faces CDHG |
| ID24 | center high, faces ADHE |

If time is short, minimum static set:

```text
ID01, ID03, ID07, ID09, ID13, ID15, ID17, ID21, ID23
```

## Phase 3 - RotoArm Dataset

Use:

```bash
roto -id R01
```

Recommended IDs:

| ID | Roto pose |
|---|---|
| R01 | planar / low tilt |
| R02 | small tilt, faces ABEF |
| R03 | small tilt, faces BCGF |
| R04 | small tilt, faces CDHG |
| R05 | small tilt, faces ADHE |
| R06 | mid tilt, faces ABEF |
| R07 | mid tilt, faces BCGF |
| R08 | mid tilt, faces CDHG |
| R09 | mid tilt, faces ADHE |

If time is short, minimum Roto set:

```text
R01, R02, R03, R04, R05
```

Notes:

- The face label is only a repeatable physical orientation label.
- Do not over-interpret it as the exact UWB antenna main-lobe direction.

## Phase 4 - Wand Dataset

Use:

```bash
wand -id W01
```

Recommended IDs:

| ID | Wand pose |
|---|---|
| W01 | fixed pose, one orientation |
| W02 | fixed pose, rotated |
| W03 | fixed pose, rotated |
| W04 | fixed pose, rotated |
| W05 | slow free move / rotation |

Known Wand geometry:

```text
BSCCF4 --285 mm-- T center --385 mm-- BS9336
T center --595 mm-- BS955A
```

Notes:

- Wand is for physical consistency and possible layout refinement.
- Do not require a perfectly horizontal table.
- For fixed captures, keep the Wand as still as possible.

## Stop Conditions

Pause and debug if any of these happen:

- `bio_ports` shows missing Master_Anchor or Master_Tag path.
- Anchor preflight cannot reach 8/8 ready.
- Sweep misses one anchor for a long time.
- Capture `summary.json` has `success: false`.
- Static/Roto/Wand capture has sustained fewer than 7 anchors.
- US30 stays running after `USOFF` or US text appears in normal Tag capture raw logs.

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
