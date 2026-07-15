# BioSpur Usable Firmware Versions

Last updated: 2026-07-15

This file is the quick root-level index for firmware builds that are currently
usable or useful as restore points. It does not replace the detailed freeze
records in `.protec/` or `SS-TWR/alt-SS-TWR/broadcast/BROADCAST_BASELINE_FREEZE.md`.

## Hardware Role Guard

Never swap the two B120 masters:

| Role | SNR | CDC name |
|---|---:|---|
| Master_Anchor | 960148546 | `Master_Anchor_BioSpur_BLE_Control_87EA2F4A526C5A02` |
| Master_Tag | 1050070698 | `Master_Tag_BioSpur_BLE_Control_6918E0384172A49F` |

B120 master-control images must use LFRC/internal RC. Before flashing a B120
image, verify the build according to `AGENTS.md`.

## Production Freeze — `freeze-4piece-20260715` (V1, current)

Verified-pass 4-piece production freeze (git tag `freeze-4piece-20260715`).
Full record: `SS-TWR/alt-SS-TWR/broadcast/FREEZE_4PIECE_20260715.md`.
Verified 2026-07-15: ge7 0.978 / ge8 0.934 / valid% 97.3, 3 tags, both masters
boot-verified. TR format = `TR;3`/blank-D1 when DIAG OFF (accepted V1; clean
`TR;2` deferred to freeze-clean batch).

| Piece | marker / carrier | signed.bin or merged sha256 (prefix) |
|---|---|---|
| TAG fw (BS9336/BS955A/BSCCF4) | `tag-freeze-20260715` (`build-tag-freeze-20260715`) | `12681984d516c4b5…` |
| ANCHOR fw (A–H) | `anchor-freeze-20260715` (`build-anchor-freeze-20260715`) | `32769f9a6a8e700b…` |
| MASTER_TAG carrier (SNR 1050070698, `boot=tag`) | `build-master-control-b120-m1-master-tag-freeze-20260715-boottag` | `1863676228466ef7…` |
| MASTER_ANCHOR carrier (SNR 960148546, `boot=anchor`, PROTECTED) | `build-master-control-b120-m1-master-anchor-freeze-20260715-bootanchor` | `9054bf34434d0f3a…` |

Flash masters with `scripts/flash_b120_master_freeze.sh` (recover + loadfile).
Master carriers MUST carry an explicit `-DAPP_MASTER_BOOT_PROFILE`; `neutral` is a
build error. See the freeze doc's "Frozen-firmware laws".

## Current Tag Raw-IMU Poll-Timestamp Line

Use this for CaliWand / full IMU output tests where the host needs the three
accelerometer axes, not only `|a|` summary statistics. This is the preferred
Tag IMU test line as of 2026-05-26.

| Item | Value |
|---|---|
| Tag marker | `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` |
| Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b69-imu-rawxyz-tspoll-tag-g1200-r1000` |
| Tag OTA payload | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b69-imu-rawxyz-tspoll-tag-g1200-r1000/dfu_application.zip` |
| Tag signed bin | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b69-imu-rawxyz-tspoll-tag-g1200-r1000/tag/zephyr/zephyr.signed.bin` |
| Tag merged hex | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b69-imu-rawxyz-tspoll-tag-g1200-r1000/merged.hex` |
| Build source record | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b69-imu-rawxyz-tspoll-tag-g1200-r1000.source` |
| Generated | `2026-05-26T09:23:30Z` |

Build configuration:

- `APP_TAG_IMU_SAMPLE_PERIOD=1`
- `APP_TAG_TR_IMU_RAW_ENABLE=1`
- `APP_TAG_TR_IMU_SUMMARY_ENABLE=0`
- `APP_ALT_SS_TWR_GUARD_US=1200`
- `APP_ALT_SS_TWR_RESP_SPACING_US=1000`

TR output:

```text
TR;4;...;R,<acc_x_mg>,<acc_y_mg>,<acc_z_mg>,<acc_norm_mg>,<imu_timestamp_ms>,<imu_poll_to_read_start_us>,<imu_poll_to_read_mid_us>,<imu_poll_to_read_end_us>,<imu_read_duration_us>
```

Host parser columns:

```text
imu_raw_valid,
acc_x_mg, acc_y_mg, acc_z_mg, acc_norm_mg,
imu_timestamp_ms,
imu_poll_to_read_start_us, imu_poll_to_read_mid_us, imu_poll_to_read_end_us,
imu_read_duration_us
```

Field meaning:

| Field | Unit | Meaning |
|---|---:|---|
| `imu_raw_valid` | flag | Raw IMU trailer was present in this TR frame. |
| `acc_x_mg` | mg | LIS2DH12 X acceleration sample in Tag body frame. |
| `acc_y_mg` | mg | LIS2DH12 Y acceleration sample in Tag body frame. |
| `acc_z_mg` | mg | LIS2DH12 Z acceleration sample in Tag body frame. |
| `acc_norm_mg` | mg | `sqrt(x^2+y^2+z^2)`, computed on the Tag. |
| `imu_timestamp_ms` | ms | Tag-side uptime when the IMU read was issued. |
| `imu_poll_to_read_start_us` | us | Time from broadcast poll TX-done to I2C read start. |
| `imu_poll_to_read_mid_us` | us | Midpoint of the I2C read, relative to poll TX-done. |
| `imu_poll_to_read_end_us` | us | Time from poll TX-done to I2C read end. |
| `imu_read_duration_us` | us | I2C read duration. |

Timing note:

- The IMU I2C read is still done after the UWB response window is complete and
  after `dwt_forcetrxoff()`. It is not inserted into the critical poll/RX
  window.
- The `imu_poll_to_read_*_us` fields use the Tag CPU cycle counter and are
  relative to the broadcast poll TX-done cycle, not host receive time.
- The LIS2DH12 ODR is 100 Hz, so the physical accelerometer sample may be up to
  about 10 ms older than the read time. The fields above record read timing,
  not the exact MEMS sampling instant.

## Current Master_Tag Raw-IMU Poll-Timestamp Carrier

This is the B120 carrier with the b69 raw-IMU Tag OTA payload embedded.

| Item | Value |
|---|---|
| Master_Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b69-imu-rawxyz-tspoll-10x9-roto10-namefix-20260526` |
| Flash hex | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b69-imu-rawxyz-tspoll-10x9-roto10-namefix-20260526/zephyr/merged.hex` |
| Build source record | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b69-imu-rawxyz-tspoll-10x9-roto10-namefix-20260526.source` |
| Active payload record | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b69-imu-rawxyz-tspoll-10x9-roto10-namefix-20260526/active_ota_payload.json` |
| Generated | `2026-05-26T09:32:44Z` |
| Flashed to | `Master_Tag`, SNR `1050070698` |
| CDC after flash | `/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00` |
| Default TDMA | `slot_period_ms=10`, `slot_active_ms=9` |
| Default TDMA profiles | `motion=10 Hz`, `static=5 Hz`, `roto=10 Hz` |

This is a Master_Tag image only. Do not flash it to Master_Anchor.

LFRC check:

```text
[ok] B120 internal LFRC verified: build-master-control-b120-m1-master-tag-lfrc-b69-imu-rawxyz-tspoll-10x9-roto10-namefix-20260526
```

Namefix note:

- The earlier `build-master-control-b120-m1-master-tag-lfrc-b68-imu-rawxyz-tspoll-20260526`
  carrier used the correct payload but enumerated as `usb-BioSpur_BioSpur...`.
- The active carrier above restores the intended `Master_Tag` CDC name and
  restores the mature 10/9 TDMA defaults.
- Current UI / field capture scripts pass `--tr-hz 10` and use the firmware
  `motion` TDMA bucket for all TR captures. The `static` and `roto` profile
  defaults are retained only as firmware-side fallback/status values.

Raw-IMU build proof:

```text
alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000
;R,%ld,%ld,%ld,%ld,%lu,%lu,%lu,%lu,%lu
```

Active payload hashes:

| Item | SHA256 |
|---|---|
| Tag signed bin | `b80a66b1d3345f8996396cc35940d18ced512f19d6b21ca35024e529fa96aabb` |
| Tag DFU zip | `5add58c6c2576f6ad519076ce694c425b783a3f292e6b5d0c2eac213ff2f50e9` |

Confirmed OTA targets:

| Tag | Result |
|---|---|
| BS2DCE | confirmed `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` on 2026-05-26 |
| BSDC91 | confirmed `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` on 2026-05-26 |
| BS9336 | confirmed `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` on 2026-05-26 |
| BS955A | confirmed `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` on 2026-05-26 |
| BSCCF4 | confirmed `alt-bcast-b69-imu-rawxyz-tspoll-g1200-r1000` on 2026-05-26 |
| BSF66F | pending b69 OTA after this carrier flash |

Important: flashing this Master_Tag makes the b69 payload available for OTA, but
it does not automatically upgrade any physical Tag. Use the UI/OTA flow to push
b69 to the required Tags before expecting raw-IMU `TR;4 ... ;R,...` output.

OTA proof for Roto 2Tag + Wand 3Tag:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/ota_roto2_wand3_b69_imu_rawxyz_tspoll_20260526_114759/deploy_summary.json
```

## Superseded Tag Raw-IMU Poll-Timestamp Line

The b68 line should not be used as the current raw-IMU line. It was useful for
iteration, but b69 is the confirmed build where the raw-IMU compile flag reaches
the Tag target.

| Item | Value |
|---|---|
| Superseded marker | `alt-bcast-b68-imu-rawxyz-tspoll-g1200-r1000` |
| Superseded Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b68-imu-rawxyz-tspoll-tag-g1200-r1000` |
| Superseded Master_Tag carrier | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b68-imu-rawxyz-tspoll-namefix-20260526` |

## Previous Tag Raw-IMU XYZ Line

This is the earlier b67 raw XYZ line. It has the raw accelerometer values, but
does not include poll-relative I2C read timing fields.

| Item | Value |
|---|---|
| Tag marker | `alt-bcast-b67-imu-rawxyz-g1200-r1000` |
| Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b67-imu-rawxyz-tag-g1200-r1000` |
| Tag OTA payload | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b67-imu-rawxyz-tag-g1200-r1000/dfu_application.zip` |
| Tag signed bin | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b67-imu-rawxyz-tag-g1200-r1000/tag/zephyr/zephyr.signed.bin` |
| Build source record | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b67-imu-rawxyz-tag-g1200-r1000.source` |

TR output:

```text
TR;4;...;R,<acc_x_mg>,<acc_y_mg>,<acc_z_mg>,<acc_norm_mg>,<imu_timestamp_ms>
```

## Previous Tag IMU Summary Experimental Line

Use this when testing Tag-side IMU summary output.

| Item | Value |
|---|---|
| Tag marker | `alt-bcast-b63-imu-summary-g1200-r1000` |
| Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b63-imu-summary-tag-g1200-r1000` |
| Tag OTA payload | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b63-imu-summary-tag-g1200-r1000/dfu_application.zip` |
| Tag merged hex | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b63-imu-summary-tag-g1200-r1000/merged.hex` |
| Build source record | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b63-imu-summary-tag-g1200-r1000.source` |
| Build command | `scripts/build_tag_ble_motion.sh 0 10 build-alt-bcast-b63-imu-summary-tag-g1200-r1000` |
| Generated | `2026-05-25T13:16:48Z` |

Known behavior:

- Emits TRv4 with IMU summary trailer.
- Keeps the normal UWB broadcast ranging path.
- IMU sampling code uses LIS2DH12 and reports short-window acceleration norm
  summary, not full raw IMU streams.
- Host parser columns include:
  `imu_valid`, `imu_n`, `acc_norm_mean_mg`, `acc_norm_std_mg`,
  `acc_norm_min_mg`, `acc_norm_max_mg`, `imu_skip_count`.

Confirmed OTA targets:

| Tag | Result |
|---|---|
| BS2DCE | confirmed `alt-bcast-b63-imu-summary-g1200-r1000` |
| BSDC91 | confirmed `alt-bcast-b63-imu-summary-g1200-r1000` |

OTA proof:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/roto_b63_imu_summary_tag_ota_20260525_1541/deploy_summary.json
```

Important: only `BS2DCE` and `BSDC91` are confirmed on this IMU image. Other
Tags may still be on the b62 no-IMU restore line unless explicitly upgraded.

## Previous Master_Tag IMU Summary Carrier

This is the B120 carrier used to OTA and operate the b63 IMU Tag line.

| Item | Value |
|---|---|
| Master_Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b63-imu-summary-20260525` |
| Flash hex | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b63-imu-summary-20260525/zephyr/merged.hex` |
| Build source record | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-b63-imu-summary-20260525.source` |
| Build command | `scripts/build_master_control_b120_m1.sh build-master-control-b120-m1-master-tag-lfrc-b63-imu-summary-20260525` |
| Generated | `2026-05-25T13:40:24Z` |

This is a Master_Tag image only. Do not flash it to Master_Anchor.

## Stable Tag Fallback Line

Use this if the IMU experiment line needs to be rolled back.

| Item | Value |
|---|---|
| Tag marker | `alt-bcast-b62-otaprep-silent-g1200-r1000` |
| Tag build dir | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0` |
| Tag OTA payload | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/dfu_application.zip` |
| Tag merged hex | `SS-TWR/alt-SS-TWR/broadcast/build-alt-bcast-b62-otaprep-silent-tag-g1200-r1000-rms0/merged.hex` |
| Master_Tag carrier | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-m1-master-tag-lfrc-alt-bcast-b62-otaprep-silent-carrier` |
| Freeze record | `.protec/fullspeed_10hz_b62_restore_20260513.env` |

Known validation:

- Restored 10 Hz/tag output on the old three Tags.
- Targets recorded in the freeze manifest: `BSF66F,BS2DCE,BSDC91`.
- No IMU summary output.

## Erlangen Anchor Baseline

Use this for the current Erlangen/outdoor anchor-side baseline unless a new
anchor experiment is explicitly started.

| Item | Value |
|---|---|
| Timing name | `tail900 start5` |
| Anchor marker | `us-hc-exp4-tail900-start5` |
| Anchor build dir | `SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-us-hc-exp4-tail900-start5` |
| Anchor OTA payload | `SS-TWR/alt-SS-TWR/broadcast/build-anchor-unified-ota-us-hc-exp4-tail900-start5/dfu_application.zip` |
| Master_Anchor carrier | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-us-hc-exp4-tail900-start5` |
| Master_Anchor flash hex | `SS-TWR/alt-SS-TWR/broadcast/build-master-control-b120-us-hc-exp4-tail900-start5/zephyr/merged.hex` |
| Detailed freeze | `SS-TWR/alt-SS-TWR/broadcast/BROADCAST_BASELINE_FREEZE.md` |

Timing:

| Anchor | Slot after poll |
|---|---:|
| A | 1200 us |
| B | 2200 us |
| C | 3200 us |
| D | 4200 us |
| E | 5200 us |
| F | 6100 us |
| G | 7000 us |
| H | 7900 us |

Notes:

- Ultrasound support exists in this anchor image.
- Ultrasound must be opened only during the short US capture window and closed
  before normal Tag/Roto/Wand capture.
- Normal responder operation after US close was verified.

## Older Full-System Freeze

The older 2026-05-12 full-system freeze is still useful for historical restore
context, but it is not the latest IMU line.

```text
.protec/full_system_freeze_20260512.env
```

This records the earlier `stable10x9-tr12-bdbs-cleantr-20260512` Tag line and
the older `g1200/r1000` anchor timing.

## Next IMU Development Notes

For the next IMU experiment:

1. Do not overwrite the b63 IMU build directory.
2. Use a new firmware marker. Since b63--b68 names already exist in the repo
   history, prefer a fresh explicit marker such as:

   ```text
   alt-bcast-b69-imu-<short-feature>-g1200-r1000
   ```

3. Create both records:

   ```text
   build-alt-bcast-b69-imu-<short-feature>-tag-g1200-r1000.source
   build-master-control-b120-m1-master-tag-lfrc-b69-imu-<short-feature>-YYYYMMDD.source
   ```

4. After OTA, always record a deploy summary with:

   ```text
   pre_version_query
   post_version_query
   expected_fw_marker
   target list
   OTA success/failure
   ```

5. If the new IMU line becomes a keeper, add a `.protec/tag_imu_*_freeze.env`
   manifest so it is not only documented by build directories and logs.
