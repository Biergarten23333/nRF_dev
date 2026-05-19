# Erlangen 2026-05-28 MoCap Field Folder

This folder is the single entry point for the Erlangen OptiTrack validation run.

Start here:

```text
first_step.md
```

Short experiment plan:

```text
docs/experiment_plan_short.md
```

On-site solver sanity check:

```text
solver/README.md
```

Field helper commands:

```bash
source /home/zekaixiao/Documents/nRF_dev/BioSpur_UWB_before_start/autopos_pipeline/erlangen_20260528_mocap/tools/erlangen_aliases.sh

# Paste the CDC paths found on the Erlangen laptop:
export BIOSPUR_ANCHOR_PORT="/dev/serial/by-id/PASTE_MASTER_ANCHOR_CDC_PATH_HERE"
export BIOSPUR_TAG_PORT="/dev/serial/by-id/PASTE_MASTER_TAG_CDC_PATH_HERE"

# Only change these if jlink_show_emulators.sh shows different SNRs:
export BIOSPUR_ANCHOR_SNR="960148546"
export BIOSPUR_TAG_SNR="1050070698"

bio_setup erlangen_20260528_optitrack
bio_ports
```

Output data go under:

```text
captures/
```

Main short commands after setup:

```bash
static -id PORT_TEST -s 20
bio_check_latest

sweep  -id SW01
us30   -id US01
static -id ID01
roto   -id R01
wand   -id W01
```

Baseline timing:

```text
tail900 start5
A 1200 us, B 2200 us, C 3200 us, D 4200 us, E 5200 us, F 6100 us, G 7000 us, H 7900 us
```

Current capture logic:

```text
TR-only, 10 Hz, explicit target BS IDs.
No old static/roto/motion profile split.
```
