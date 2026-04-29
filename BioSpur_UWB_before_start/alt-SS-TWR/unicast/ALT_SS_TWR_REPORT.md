# Alt SS-TWR Burst-Poll Implementation Report

## Scope
All source edits and builds were performed inside `./alt-SS-TWR/` only. The production workspace outside this directory was not modified.

## Baseline Builds
Baseline copy/build validation was completed before protocol changes:

- Tag baseline: `build-baseline-tag-unified/merged.hex`
- Anchor baseline: `build-baseline-anchor-unified/zephyr/merged.hex`

The initial isolated copy was missing `include/`; after copying it into `alt-SS-TWR/`, both baseline builds succeeded.

## Implemented Protocol Changes

### Shared Frame Format
Files:

- `include/uwb_ss_twr_shared.h`
- `src/uwb_ss_twr_shared.c`

Added two poll-frame fields:

- `poll_index` at byte 10
- `poll_count` at byte 11

Legacy polls keep these fields at zero. `poll_count == 0` means standard SS-TWR compatibility mode.

### Tag Side
File: `src/ss_twr_init.c`

Added compile-time controlled experimental mode:

- `APP_ALT_SS_TWR_ENABLE`
- `APP_ALT_SS_TWR_POLL_SPACING_US`
- `APP_ALT_SS_TWR_GUARD_US`
- `APP_ALT_SS_TWR_RESP_SPACING_US`

Current parameter set:

- `POLL_SPACING_US = 200`
- `GUARD_US = 500`
- `RESP_SPACING_US = 800`

Implemented a burst sweep path that:

- schedules one delayed TX poll per selected anchor
- includes `poll_index` and `poll_count` in each poll
- records one scheduled poll TX timestamp per anchor
- waits for all anchor responses in one RX window
- matches responses by anchor source address
- computes SS-TWR ToF using tag RX carrier-integrator CFO compensation
- preserves CM/CR/CF output path and timing instrumentation

Important implementation note:

- DW1000 cannot safely queue multiple TX buffers. The burst TX loop waits for `SYS_STATUS_TXFRS` after each delayed poll before loading the next poll frame. If runtime reports delayed-TX-late at 200 us spacing, increase `APP_ALT_SS_TWR_POLL_SPACING_US` to 250-400 us.

### Anchor Side
File: `src/ss_twr_resp.c`

Added alt responder scheduling when `poll_count > 0`:

```text
response_delay_from_poll =
  (poll_count - poll_index) * POLL_SPACING_US
  + GUARD_US
  + poll_index * RESP_SPACING_US
```

This makes each anchor wait until the burst is complete, then respond in its assigned response slot. Legacy polls still use `APP_ANCHOR_RESP_DELAY_UUS`.

### Build System
Files:

- `apps/tag/CMakeLists.txt`
- `apps/anchor/CMakeLists.txt`
- `scripts/build_tag_ble_motion.sh`
- `scripts/build_anchor_unified.sh`

Added CMake cache variables and compile definitions for the alt parameters. Verified via `compile_commands.json` that `APP_ALT_SS_TWR_ENABLE=1` and timing values are present in the actual compiler command lines.

## Built Artifacts

Current experimental artifacts:

- Tag OTA/merged image: `build-alt-tag-burst-v2/merged.hex`
- Tag DFU zip: `build-alt-tag-burst-v2/dfu_application.zip`
- Anchor merged image: `build-alt-anchor-burst-v1/zephyr/merged.hex`

Earlier superseded builds:

- `build-alt-tag-burst-v0`: compiled before CMake macro injection was fixed, do not use.
- `build-alt-anchor-burst-v0`: compiled before CMake macro injection was fixed, do not use.
- `build-alt-tag-burst-v1`: compiled with alt macros, but before the TX-buffer overwrite risk fix, do not use.

## Verification Status

Completed:

- Isolated workspace created.
- Baseline tag and anchor builds passed.
- Alt tag and anchor builds passed.
- Compile command verification confirms alt macros are active.

Not yet completed:

- OTA flashing to physical anchors/tags.
- Runtime validation of burst timing.
- Runtime validation of range plausibility.
- CFO sign/scale validation.
- Comparison of `first_to_last_us` / `frame_us` against production baseline.

## Expected Runtime Checks

Primary pass criteria:

- CF `first_to_last_us` should drop from production ~8100 us to near `(poll_count - 1) * POLL_SPACING_US`.
- For 4 anchors at 200 us spacing, expected first-to-last is ~600 us if no late scheduling occurs.
- `frame_us` should be much lower than legacy if responses are received in the burst response window.
- CM should contain plausible range values, not 100 m scale values.
- CR/CF should not show persistent delayed TX or rx timeout failures.

## Known Risks Before Flashing

1. `POLL_SPACING_US=200` may be too aggressive when waiting for TXFRS and reloading the next frame over SPI. If so, rebuild with 250-400 us.
2. CFO compensation currently uses the tag-side carrier integrator from response RX, matching the existing production ToF style. If ranges drift by anchor response slot index, validate sign and multiplier.
3. Anchor response scheduling assumes the anchor receives exactly one matching burst poll and uses `poll_index` as response-slot index. This is correct for selected-anchor burst plans but must be validated with listener traces.
4. This is an experimental protocol fork; do not mix these images with production tag/anchor images during the same test.

## Recommended Next Step

Build or configure an OTA/master path that uses only `alt-SS-TWR` artifacts, then flash in this order:

1. Anchor alt image to all 8 anchors.
2. Tag alt image to test tags.
3. Run short 30-60s capture and inspect CF first.
4. If first-to-last is still >2000 us, debug burst TX scheduling before range accuracy work.

## OTA-Capable Artifacts Added

Anchor OTA sysbuild completed after exporting the full Zephyr module path for the isolated copy.

Current usable experimental artifacts are now:

- Tag merged image: `build-alt-tag-burst-v2/merged.hex`
- Tag signed image: `build-alt-tag-burst-v2/tag/zephyr/zephyr.signed.bin`
- Tag DFU zip: `build-alt-tag-burst-v2/dfu_application.zip`
- Anchor direct merged image: `build-alt-anchor-burst-v1/zephyr/merged.hex`
- Anchor OTA merged image: `build-alt-anchor-ota-burst-v1/merged.hex`
- Anchor signed image: `build-alt-anchor-ota-burst-v1/anchor/zephyr/zephyr.signed.bin`
- Anchor DFU zip: `build-alt-anchor-ota-burst-v1/dfu_application.zip`

Build markers used for the OTA-capable experimental images:

- Tag: build directory `build-alt-tag-burst-v2`; marker inheritance depends on the copied tag build script configuration.
- Anchor: `APP_ANCHOR_FW_MARKER=alt-ss-twr-anchor-v1`

## Runtime / Flash Status

No physical device has been flashed from this `alt-SS-TWR` fork yet.

The code is build-complete for tag and anchor, but it is not runtime-validated. The next step must be a controlled small test, not a full system rollout:

1. Use an OTA/master carrier that embeds `alt-SS-TWR` tag or anchor DFU payloads.
2. OTA one tag and one or two anchors first if possible.
3. Run a short capture and inspect `CF first_to_last_us` before OTAing all devices.
4. Only expand to all 8 anchors / 3 tags after CF confirms burst-poll timing.

## Build Commands Used

Tag alt build:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/alt-SS-TWR
APP_ALT_SS_TWR_ENABLE=1 \
APP_ALT_SS_TWR_POLL_SPACING_US=200 \
APP_ALT_SS_TWR_GUARD_US=500 \
APP_ALT_SS_TWR_RESP_SPACING_US=800 \
./scripts/build_tag_ble_unified.sh 0 10 build-alt-tag-burst-v2
```

Anchor direct build:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/alt-SS-TWR
APP_ANCHOR_FW_MARKER=alt-ss-twr-anchor-v1 \
APP_ALT_SS_TWR_ENABLE=1 \
APP_ALT_SS_TWR_POLL_SPACING_US=200 \
APP_ALT_SS_TWR_GUARD_US=500 \
APP_ALT_SS_TWR_RESP_SPACING_US=800 \
APP_ANCHOR_ALLOW_TAG_POLLS=1 \
APP_ANCHOR_USE_AUTO_SCHEDULE=1 \
APP_ANCHOR_SCHEDULE_MODE=2 \
./scripts/build_anchor_unified.sh build-alt-anchor-burst-v1 2
```

Anchor OTA sysbuild:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/alt-SS-TWR
rm -rf build-alt-anchor-ota-burst-v1
export KCONFIG_ALLOW_WARNINGS=1
export WEST_TOPDIR=/home/zekaixiao/ncs/v2.8.0
export WEST_BIN=west
export ZEPHYR_BASE=/home/zekaixiao/ncs/v2.8.0/zephyr
export ZEPHYR_NRF_MODULE_DIR=/home/zekaixiao/ncs/v2.8.0/nrf
export ZEPHYR_MODULES="$(cd "$WEST_TOPDIR" && "$WEST_BIN" list --format={abspath} | tr '\n' ';' | sed 's/;$//')"
export PYTHONPATH="/usr/lib/python3/dist-packages${PYTHONPATH:+:${PYTHONPATH}}"
export APP_ANCHOR_FW_MARKER=alt-ss-twr-anchor-v1
export APP_ALT_SS_TWR_ENABLE=1
export APP_ALT_SS_TWR_POLL_SPACING_US=200
export APP_ALT_SS_TWR_GUARD_US=500
export APP_ALT_SS_TWR_RESP_SPACING_US=800
export APP_ANCHOR_ALLOW_TAG_POLLS=1
export APP_ANCHOR_USE_AUTO_SCHEDULE=1
export APP_ANCHOR_SCHEDULE_MODE=2
west build -b decawave_dwm1001_dev/nrf52832 -s apps/anchor -d build-alt-anchor-ota-burst-v1 --sysbuild --pristine=always -- \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DCONFIG_BUILD_OUTPUT_META=n \
  -Dmcuboot_CONFIG_BUILD_OUTPUT_META=n \
  -DSB_CONFIG_BOOTLOADER_MCUBOOT=y \
  '-DCONF_FILE=prj.conf;prj_ota.conf' \
  -DAPP_ALT_SS_TWR_ENABLE=1 \
  -DAPP_ALT_SS_TWR_POLL_SPACING_US=200 \
  -DAPP_ALT_SS_TWR_GUARD_US=500 \
  -DAPP_ALT_SS_TWR_RESP_SPACING_US=800 \
  -DAPP_ANCHOR_FW_MARKER=alt-ss-twr-anchor-v1 \
  -DAPP_ANCHOR_SCHEDULE_MODE=2
```

## Immediate Open Item

The original OTA carrier blocker has been resolved by generating isolated `alt-SS-TWR` payload metadata and building separate Master_Anchor and Master_Tag carriers below. The remaining blocker is physical runtime validation.

## Master OTA Carrier Builds

The isolated tree now also contains B120 master-control carrier builds using internal LFRC on both CPUAPP and CPUNET.

Anchor OTA carrier:

- Payload prepared from: `build-alt-anchor-ota-burst-v1/anchor/zephyr/zephyr.signed.bin`
- Payload marker: `alt-ss-twr-anchor-v1`
- Payload kind verification: passed as `anchor`
- Master build: `build-master-control-b120-m1-master-anchor-lfrc-alt-ss-twr-anchor-v1`
- Flash image: `build-master-control-b120-m1-master-anchor-lfrc-alt-ss-twr-anchor-v1/zephyr/merged_domains.hex`
- LFRC verification: passed for CPUAPP and CPUNET

Tag OTA carrier:

- Payload prepared from: `build-alt-tag-burst-v2/tag/zephyr/zephyr.signed.bin`
- Payload marker: `alt-ss-twr-tag-v2`
- Payload kind verification: passed as `tag`
- Master build: `build-master-control-b120-m1-master-tag-lfrc-alt-ss-twr-tag-v2`
- Flash image: `build-master-control-b120-m1-master-tag-lfrc-alt-ss-twr-tag-v2/zephyr/merged_domains.hex`
- LFRC verification: passed for CPUAPP and CPUNET

Note: `apps/master_ota/generated/ota_image.inc` is a single-payload generated file. At the end of this run it contains the tag payload (`alt-ss-twr-tag-v2`). The already-built `Master_Anchor` carrier remains valid because the anchor payload was compiled into its build before the generated file was switched to tag.

## Safe Flash Commands If You Choose To Test

These commands are examples only. They have not been run during this implementation pass.

Flash Master_Anchor carrier to the anchor-control B120:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/alt-SS-TWR
B120_SNR=960148546 \
./scripts/flash_master_control_b120_m1_noninteractive.sh \
  build-master-control-b120-m1-master-anchor-lfrc-alt-ss-twr-anchor-v1/zephyr/merged_domains.hex
```

Flash Master_Tag carrier to the tag-control B120:

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/alt-SS-TWR
B120_SNR=1050070698 \
./scripts/flash_master_control_b120_m1_noninteractive.sh \
  build-master-control-b120-m1-master-tag-lfrc-alt-ss-twr-tag-v2/zephyr/merged_domains.hex
```

After flashing carriers, OTA should be staged carefully: anchor carrier contains anchor payload, tag carrier contains tag payload. Do not use a carrier against the wrong device class.
