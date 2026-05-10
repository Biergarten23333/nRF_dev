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
7. Request `30Hz` per Wand Tag. Three Wand Tags at `30Hz` each are `90Hz`
   aggregate, still below the validated `10Tag x 10Hz = 100Hz` broadcast
   baseline.

The Wand Tags themselves are normal Tags. They only differ in BLE identity:

| Wand | Identity code | Expected BLE name |
|---|---:|---|
| A | `0xCCF4` | `Wand-A-BSCCF4` |
| B | `0x9336` | `Wand-B-BS9336` |
| C | `0x955A` | `Wand-C-BS955A` |

## Current Firmware Image

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

Runtime capture target:

```text
Wand-A: 30Hz
Wand-B: 30Hz
Wand-C: 30Hz
Expected TR rows: 3 x 30 x 8 = 720 rows/s
```

## Naming Rule

The image is common. It does not require separate builds for A/B/C.

At runtime, firmware maps known identity codes:

```text
BSCCF4 -> Wand-A-BSCCF4
BS9336 -> Wand-B-BS9336
BS955A -> Wand-C-BS955A
```

If a Wand board is still running an older image, it may advertise only
`BSCCF4`, `BS9336`, or `BS955A`. In that case, OTA must target the old BS name
first. After the new image boots, use the `Wand-X-BSxxxx` names.

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
