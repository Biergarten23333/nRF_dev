# First Step on the Erlangen Laptop

Before running any AutoPos, Tag, RotoArm, or Wand capture, first identify the two
master devices on the laptop:

- `Master_Anchor`: controls anchors, switches A--H between Matrix and Responder,
  runs AutoPos sweep, anchor preflight, and F/G/H ultrasound commands.
- `Master_Tag`: controls the Tag-side capture stream and records TR data.

Do this once after plugging in both master boards.

## 1. Go to the repo root

```bash
cd /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start
```

## 2. List connected J-Link devices

Use this to confirm the two SNRs:

```bash
SS-TWR/alt-SS-TWR/broadcast/scripts/jlink_show_emulators.sh
```

Expected master SNR mapping:

```text
Master_Anchor SNR = 960148546
Master_Tag    SNR = 1050070698
```

If the SNRs are different on the laptop, write the observed values here:

```text
Master_Anchor SNR =
Master_Tag    SNR =
```

## 3. List CDC serial names

```bash
ls -l /dev/serial/by-id
```

On the 2026-05-19 desktop, the working ports were:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
```

On the Erlangen laptop, the exact `/dev/serial/by-id/...` names may differ.
Choose the two entries that correspond to the two master boards and write them
here:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/..."
export BIOSPUR_TAG_PORT="/dev/serial/by-id/..."
```

## 3.5. Apply the observed SNR and CDC values

Important: do not edit `.protec` on the field laptop unless there is a special
reason. For the Erlangen run, just export the values in the current terminal.
The helper script always respects these exported values.

If the SNR values match the expected values, only export the two CDC paths:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/PASTE_MASTER_ANCHOR_CDC_PATH_HERE"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/PASTE_MASTER_TAG_CDC_PATH_HERE"
```

If the SNR values are different on the laptop, also export them:

```bash
export BIOSPUR_ANCHOR_SNR="PASTE_MASTER_ANCHOR_SNR_HERE"
export BIOSPUR_TAG_SNR="PASTE_MASTER_TAG_SNR_HERE"
```

Example shape only:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00"
export BIOSPUR_ANCHOR_SNR="960148546"
export BIOSPUR_TAG_SNR="1050070698"
```

## 4. Source the field helper file

```bash
source /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh
```

If you source the helper before exporting the port values, that is still OK.
Just export the observed values afterwards:

```bash
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/PASTE_MASTER_ANCHOR_CDC_PATH_HERE"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/PASTE_MASTER_TAG_CDC_PATH_HERE"
```

## 5. Start a session folder

```bash
bio_setup erlangen_20260528_optitrack
```

The output root will be:

```text
/home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/captures/erlangen_20260528_optitrack
```

## 6. Verify the two ports before capture

```bash
bio_ports
```

Both configured paths should exist. If either one says `[WARN]`, fix the export
before running any capture.

## 7. Do a small first test

Before the real OptiTrack captures, run one short BSF66F test:

```bash
static -id PORT_TEST -s 20
bio_check_latest
```

If this passes, the two masters, ports, SNRs, Anchor preflight, and Tag capture
path are all basically correct.

## 8. Real command examples

```bash
sweep  -id SW01
us30   -id US01
static -id ID01
roto   -id R01
wand   -id W01
```

Current defaults:

```text
static: BSF66F, 120 s, TR-only, 10 Hz
roto:   BS2DCE + BSDC91, 120 s, TR-only, 10 Hz
wand:   BS9336 + BS955A + BSCCF4, 120 s, TR-only, 10 Hz
sweep:  1000 formal SW sets + 10 prewarm SW sets
us30:   F/G/H ultrasound 30 s, output ultrasound_F.csv, ultrasound_G.csv, and ultrasound_H.csv
```

Important: do not use the old `static/roto/motion profile` commands. The current
baseline is TR-only with explicit target IDs.
