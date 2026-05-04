# Broadcast Tag Inventory

This file records DWM1001C / nRF52832 Tags used with the broadcast Alt SS-TWR
pipeline.

## Current Broadcast Tag Firmware

Recommended direct-flash image for extra pressure-test Tags:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Firmware marker:

```text
alt-bcast-b62-otaprep-silent-g1200-r1000
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
  SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex \
  100
```

After direct flash, push APOS layout again because NVS may be erased.

## Known Tags

| BLE name | Mount / role note | FICR DEVICEID[0] | FICR DEVICEID[1] | Identity code | Default tag byte | BLE addr | Notes |
|---|---|---:|---:|---:|---:|---|---|
| BSF66F | Static Tag | TBD | TBD | `0xF66F` | `0x6F` | TBD | Existing validated Tag |
| BS2DCE | RotoTag | TBD | TBD | `0x2DCE` | `0xCE` | TBD | Existing Roto Tag |
| BSDC91 | RotoTag | TBD | TBD | `0xDC91` | `0x91` | TBD | Existing Roto Tag |
| BSE88E | Pelvis | `0xE3E6D238` | `0x8D08649A` | `0xE88E` | `0x8E` | `DB:AF:30:AA:82:AD` | New manually flashed pressure-test Tag, first seen on 2026-05-03 |
| BS6F3A | Ankle_R | `0xCE9788BB` | `0x06335B4C` | `0x6F3A` | `0x3A` | `FB:BE:DD:5A:88:33` | New manually flashed pressure-test Tag; b62 OTA verified on 2026-05-03 |
| BS10CE | Pressure-test EVK | TBD | TBD | `0x10CE` | `0xCE` | `F2:8A:A6:F5:E2:4A` | DWM1001C EVK flashed to b62 on 2026-05-04; excellent 8-tag TR/TS performance |
| BS7724 | Pressure-test EVK | `0xC0624B92` | `0xF85CEC31` | `0x7724` | `0x24` | TBD | DWM1001C EVK flashed to b62 on 2026-05-04; TDMA/TR verified, UWB range quality weak in first bench probe |
| BS1396 | Pressure-test EVK | `0xD807C222` | `0x49ECF97F` | `0x1396` | `0x96` | `D3:67:A5:A7:35:2F` | DWM1001C EVK flashed to b62 on 2026-05-04; pending TDMA pressure-test validation |

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

## BS6F3A Bring-Up Record

Date: 2026-05-03

Manual probe:

```text
Probe SNR used as external SWD probe: 1050070698
Target device: nRF52832 / DWM1001C
FICR DEVICEID[0] = 0xCE9788BB
FICR DEVICEID[1] = 0x06335B4C
Computed BLE name = BS6F3A
Default tag byte = 0x3A / 58
Default local short address = 0xB13A
Mount / role note = Ankle_R
```

Identity calculation:

```text
seed0=0xCE9788BB
seed1=0x06335B4C
folded=0xC4C2ABF8
code=0x6F3A
name=BS6F3A
```

Direct-flash target image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Expected firmware marker after successful b62 flash:

```text
alt-bcast-b62-otaprep-silent-g1200-r1000
```

Flash, BLE, and OTA verification status:

```text
2026-05-03: Master_Tag VERSION query did not see BS6F3A.
Query log: logs/version_query_BS6F3A_20260503/tag_version_query.log
Observed only existing BSF66F / BS2DCE / BSDC91 advertisements during that query.
Follow-up J-Link reset attempt saw VTref=3.300V but could not connect to target SWD.
After manual power cycle, Master_Tag VERSION query still did not see BS6F3A.
Second query log: logs/version_query_BS6F3A_after_powercycle_20260503/tag_version_query.log
After a later TC2030 reconnect, FICR was readable again as:
  10000060 = CE9788BB 06335B4C
The b62 merged.hex direct flash completed successfully through J-Link:
  Erase done
  Programming flash [100%] Done
  O.K.
The final reset reported SYSRESETREQ confusion and used VECTRESET fallback,
then issued go/exit.
Post-flash broad BS scan still did not see BS6F3A. It saw only:
  BSF66F, BS2DCE, BSDC91
Broad scan log:
  logs/scan_all_bs_after_BS6F3A_flash_20260503.log

After Master_Tag scan/name-fallback fix, BS6F3A was discovered and connected:
  BLE addr = FB:BE:DD:5A:88:33 (random)
  scan log = logs/scan_BS6F3A_after_master_namefallback_20260503.log

BS6F3A was then OTA-updated to b62 successfully:
  log dir = SS-TWR/alt-SS-TWR/broadcast/logs/ota_tag_BS6F3A_b62_20260503_232316
  target = alt-bcast-b62-otaprep-silent-g1200-r1000
  actual = alt-bcast-b62-otaprep-silent-g1200-r1000
  match = True

5-Tag TDMA pressure test confirmation:
  capture = SS-TWR/alt-SS-TWR/broadcast/logs/tdma_5tag_motion180_b62_20260503_232731
  BS6F3A TS = 1801 / 180s = 10.01 Hz
  BS6F3A TR = 14408 rows
  BS6F3A TR valid = 14151 rows = 98.2%
```

Current practical OTA target key after it appears on BLE:

```bash
ota_target name BS6F3A
```

Do not rely on the runtime TDMA `tag_id` as hardware identity. The Master_Tag
can assign logical `TAG=<id>` and `SLOT=<slot>` at runtime, so the logical UWB
short address may change between pressure-test plans. The stable hardware label
is the BLE `BSxxxx` name.

## BS10CE Bring-Up Record

Date: 2026-05-04

Manual direct flash:

```text
Probe SNR / onboard J-Link: 760184784
Target device: nRF52832 / DWM1001C EVK
Image: SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
Computed BLE name observed by Master_Tag: BS10CE
BLE addr = F2:8A:A6:F5:E2:4A (random)
```

8-Tag TDMA pressure test confirmation:

```text
capture = SS-TWR/alt-SS-TWR/broadcast/logs/tdma_8tag_motion180_b66_cmdall_20260504_110804
BS10CE TS = 1733 / 180s = 9.63 Hz
BS10CE TR = 13920 rows
BS10CE TR valid = 13103 rows = 94.1%
```

## BS7724 Bring-Up Record

Date: 2026-05-04

Manual probe and direct flash:

```text
Probe SNR / onboard J-Link: 760184964
Target device: nRF52832 / DWM1001C EVK
FICR DEVICEID[0] = 0xC0624B92
FICR DEVICEID[1] = 0xF85CEC31
Computed BLE name = BS7724
Default tag byte = 0x24 / 36
Default local short address = 0xB124
```

Identity calculation:

```text
seed0=0xC0624B92
seed1=0xF85CEC31
folded=0xC887BFA3
code=0x7724
name=BS7724
```

Direct-flash target image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Flash and TDMA verification status:

```text
2026-05-04: J-Link OB firmware upgraded automatically, then b62 merged.hex flash completed:
  Erase done
  Programming flash [100%] Done
  Verifying flash [100%] Done
  O.K.

20s single-Tag TDMA probe:
  capture = SS-TWR/alt-SS-TWR/broadcast/logs/tdma_BS7724_probe20_20260504_111732
  BS7724 TR = 1600 rows = 10.00 Hz * 8 anchors * 20s
  BS7724 TR valid = 69 rows = 4.3%
  anchors_seen = 0,1
  TS = 0
```

Interpretation:

```text
BS7724 BLE connection, TDMA assignment, and TR export are working.
The first bench probe had poor UWB ranging validity, so check placement,
antenna orientation, power, and local obstruction before using it as a
full-quality pressure-test Tag.
```

## BS1396 Bring-Up Record

Date: 2026-05-04

Manual probe and direct flash:

```text
Probe SNR / onboard J-Link: 760184545
Target device: nRF52832 / DWM1001C EVK
FICR DEVICEID[0] = 0xD807C222
FICR DEVICEID[1] = 0x49ECF97F
Computed BLE name = BS1396
Default tag byte = 0x96 / 150
Default local short address = 0xB196
BLE addr = D3:67:A5:A7:35:2F (random)
```

Identity calculation:

```text
seed0=0xD807C222
seed1=0x49ECF97F
folded=0x023211A4
code=0x1396
name=BS1396
```

Direct-flash target image:

```text
SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex
```

Flash and BLE verification status:

```text
2026-05-04: b62 merged.hex flash completed cleanly:
  Erase done
  Programming flash [100%] Done
  Verifying flash [100%] Done
  O.K.

Master_Tag scan saw:
  RECV candidate rejected: D3:67:A5:A7:35:2F (random) bs=BS1396 not in TDMA profile allow-list
```

Interpretation:

```text
BS1396 is advertising correctly after b62 direct flash.
The scan rejection was expected because the Master_Tag TDMA allow-list was
still restricted to the previous single-Tag probe roster.
```
