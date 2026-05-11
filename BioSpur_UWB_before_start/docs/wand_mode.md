# Calibration Wand Mode

This document records the canonical meaning of "Wand Calibration" in the
broadcast SS-TWR system.

## Current Definition

Wand Calibration is a `Master_Tag` / host-script workflow, not a special Tag
firmware ranging protocol.

When the user says "do Wand Calibration":

1. Use `Master_Tag`, never `Master_Anchor`.
2. Connect only the three Wand Tags.
3. Filter by Wand identity/name and reject ordinary `BSxxxx` Tags from the TDMA
   roster.
4. Run the normal broadcast SS-TWR Tag ranging path.
5. Record normal `TR` output for the offline solver.
6. Do not enable TS/CX/CAL_STATIC/CAL_ROTO outputs.
7. For the V015 stable baseline, request `10Hz` per Wand Tag. Three Wand Tags
   at `10Hz` each are the currently verified reliable Wand capture capability.

The Wand Tags themselves are normal Tags. The canonical identity is the BS code;
the Wand role name is only a human-facing alias used by capture scripts:

| Wand | Canonical BS code | Optional role alias |
|---|---|---|
| A | `BSCCF4` | `Wand-A-BSCCF4` |
| B | `BS9336` | `Wand-B-BS9336` |
| C | `BS955A` | `Wand-C-BS955A` |

Memory rule: when the user says "the Wand Tags", it means exactly
`BSCCF4`, `BS9336`, and `BS955A`, just like "the old three Tags" means
`BSF66F`, `BS2DCE`, and `BSDC91`.

## Physical T-Structure Geometry

The current Calibration Wand is a physical T-shaped bar with three Tags fixed
on the bar. These dimensions are manually measured mechanical distances, not
UWB-estimated distances.

Measurement note: these dimensions were measured by tape measure, so expect
about `2-5 mm` manual measurement uncertainty.

Top bar:

```text
BSCCF4 --- 285 mm --- T center --- 385 mm --- BS9336
```

Vertical side:

```text
T center
   |
   | 595 mm
   |
BS955A
```

Coordinate convention for analysis scripts:

```text
T center = (0, 0, 0) mm
BSCCF4   = (-285, 0, 0) mm   # Wand A, left side of top bar
BS9336   = ( 385, 0, 0) mm   # Wand B, right side of top bar
BS955A   = (   0,-595, 0) mm # Wand C, vertical wand side
```

Derived pair distances:

| Pair | Mechanical distance |
|---|---:|
| `BSCCF4` - `BS9336` | `670 mm` |
| `BSCCF4` - `BS955A` | `sqrt(285^2 + 595^2) = 659.8 mm` |
| `BS9336` - `BS955A` | `sqrt(385^2 + 595^2) = 708.7 mm` |

Use these values as the ground-truth Wand geometry for later calibration,
range sanity checks, and rigid-body fit tests.

## Current Stable Firmware Image

As of 2026-05-10, the stable rollback line is:

```text
wand-b65timing-g1200-r1000-tr1tr2-bd-bs-v015-20260510
```

Stable `Master_Tag` carrier:

```text
SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b65-master10ms-wandpayload-tr1tr2-bd-bs-v015-mastertdma10ms-20260510/
```

Verified result:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/wand3_v015_bscode_verify_tr10_60s_20260510_20260510_193758/
```

All three Wand Tags reported `TDMA match=true`, `period_ms=10`,
`active_ms=9`, `actual_hz=10.0`, and saw anchors `0-7`.

## Previous Wand Role-Prefix Image

Use one common image for all three Wand boards:

```text
alt-bcast-b65-tr3-ledpos-tronly-g1200-r1000-wand-roleprefix-20260509
```

Build evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b65-tr3-ledpos-tronly-tag-wand-roleprefix-g1200-r1000-tdma10-20260509/
```

OTA evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/wand_roleprefix_b65_ota_by_bs_20260509_012422/
```

Key build parameters:

| Parameter | Value |
|---|---:|
| `APP_TAG_BLE_NAME_PREFIX` | `Wand` |
| `APP_TAG_WAND_MODE_ENABLE` | `0` |
| `APP_ALT_SS_TWR_ENABLE` | `1` |
| `APP_ALT_SS_TWR_BCAST_ENABLE` | `1` |
| `APP_ALT_SS_TWR_GUARD_US` | `1200` |
| `APP_ALT_SS_TWR_RESP_SPACING_US` | `1000` |
| `APP_TAG_TDMA_SLOT_PERIOD_MS` | `10` |
| `APP_TAG_TDMA_SLOT_ACTIVE_MS` | `9` |
| `APP_TAG_POSITION_OUTPUT_ENABLE` | `0` |
| `APP_TAG_EKF_ENABLE` | `0` |

V015 verified runtime capture capability:

```text
Wand-A / BSCCF4: 10Hz
Wand-B / BS9336: 10Hz
Wand-C / BS955A: 10Hz
Expected TR rows: 3 x 10 x 8 = 240 rows/s
```

Higher Wand rates such as `20Hz/tag` or `30Hz/tag` are future stress-test
targets, not the V015 stable baseline.

## Naming Rule

The image is common. It does not require separate builds for A/B/C.

At runtime, firmware maps known identity codes:

```text
BSCCF4 -> Wand-A-BSCCF4
BS9336 -> Wand-B-BS9336
BS955A -> Wand-C-BS955A
```

OTA must target the canonical BS code, not the Wand alias:

```text
BSCCF4, BS9336, BS955A
```

The OTA deploy script canonicalizes `Wand-A-BSCCF4` to `BSCCF4`, but commands
and docs should prefer the BS codes directly. Capture scripts may accept
`Wand-X-BSxxxx` aliases, but the TDMA roster is still keyed by the same BS
identities.

## What Not To Do

- Do not treat Wand Calibration as Tag-side PMODE logic.
- Do not require the Wand Tags to parse a new PMODE.
- Do not enable direct Tag-to-Tag internal sweep unless explicitly testing a
  separate prototype.
- Do not output CX/CAL_STATIC/CAL_ROTO for Wand Calibration.
- Do not include old ordinary Tags in the Wand TDMA roster.

## Future Prototype

Direct Tag-to-Tag Wand sweep is a separate future firmware feature. It would
pause normal broadcast SS-TWR and add a radio-safe internal ranging loop. It is
not the current production Wand Calibration path.
