# Broadcast Tag Inventory

This file records DWM1001C / nRF52832 Tags used with the broadcast Alt SS-TWR
pipeline.

## Current Broadcast Tag Firmware

Recommended direct-flash image for extra pressure-test Tags:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b61-tr2-b55base-ekf0-tag-g1200-r1000-rms0/merged.hex
```

Firmware marker:

```text
alt-bcast-b61-tr2-b55base-ekf0-g1200-r1000-rms0
```

Build properties:

- Broadcast 8-anchor ranging
- TR / TS / TF output architecture
- EKF disabled
- `APP_ALT_SS_TWR_GUARD_US=1200`
- `APP_ALT_SS_TWR_RESP_SPACING_US=1000`
- Lightweight TDMA: `10 ms` slot period, `10` slots, `9 ms` active
- Runtime APOS layout supported through NVS

Direct flash command template:

```bash
SS-TWR/alt-SS-TWR/broadcast/scripts/jlink_flash_hex_by_snr.sh \
  <JLINK_PROBE_SNR> \
  nRF52832_XXAA \
  SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b61-tr2-b55base-ekf0-tag-g1200-r1000-rms0/merged.hex \
  100
```

After direct flash, push APOS layout again because NVS may be erased.

## Known Tags

| BLE name | Mount / role note | FICR DEVICEID[0] | FICR DEVICEID[1] | Identity code | Default tag byte | BLE addr | Notes |
|---|---|---:|---:|---:|---:|---|---|
| BSF66F | Static Tag | TBD | TBD | `0xF66F` | `0x6F` | TBD | Existing validated Tag |
| BS2DCE | RotoTag | TBD | TBD | `0x2DCE` | `0xCE` | TBD | Existing Roto Tag |
| BSDC91 | RotoTag | TBD | TBD | `0xDC91` | `0x91` | TBD | Existing Roto Tag |
| BSE88E | Pelvis | `0xE3E6D238` | `0x8D08649A` | `0xE88E` | `0x8E` | TBD | New manually flashed pressure-test Tag, first seen on 2026-05-03 |

## BSE88E Bring-Up Record

Date: 2026-05-03

Manual probe:

```text
Probe SNR used as external SWD probe: 1050070698
Target device: nRF52832 / DWM1001C
FICR DEVICEID[0] = 0xE3E6D238
FICR DEVICEID[1] = 0x8D08649A
Computed BLE name = BSE88E
Default tag byte = 0x8E / 142
```

Identity calculation used by firmware:

```c
folded = seed0 ^ seed1 ^ (seed0 >> 16) ^ (seed1 << 1);
code = ((folded >> 16) ^ folded) & 0xffff;
name = "BS%04X";
```

For BSE88E:

```text
seed0=0xE3E6D238
seed1=0x8D08649A
folded=0x74FE9C70
code=0xE88E
name=BSE88E
```

Independent confirmation:

```text
BSE88E was seen from BLE scan on a second Linux machine.
```

## BLE UUID / OTA Targeting Notes

For b61 Tags, the per-device stable identity is the `BSxxxx` name derived from
FICR. The advertised OTA/SMP service UUID is common to all Tags, not unique per
board:

```text
8d53dc1d-1db7-4cd3-868b-8a527460aa84
```

The short manufacturer token carries:

```text
Company ID: 0xffff
Magic:      'B'
Tag byte:   code & 0xff
BS code:    code
```

Current practical OTA target key:

```bash
ota_target name BSE88E
```

Do not rely on the runtime TDMA `tag_id` as hardware identity. The Master_Tag
can assign logical `TAG=<id>` and `SLOT=<slot>` at runtime, so the logical UWB
short address may change between pressure-test plans. The stable hardware label
is the BLE `BSxxxx` name.
