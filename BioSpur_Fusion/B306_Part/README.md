# B306 fusion node

`B306_Part` contains the new nRF52840 side of BioSpur Fusion. The B306 owns the
node clock, timestamps the JY61P IMU and DWM1001C UWB epochs, batches the aligned
records, and sends them to a Fusion Master over BLE.

The current phase is scaffolding and P1 bring-up on an nRF52840 DK. There is no
IMU driver, UWB UART parser, GPIO capture path, fusion algorithm, or production
BLE service here yet.

## Layout

- `firmware/`: minimal Zephyr application and future B306 firmware.
- `host/dongle_central/`: future nRF52840 dongle BLE-central/USB-CDC firmware.
- `host/pc/`: future capture, validation, parsing, and provenance tooling.
- `docs/`: timing, BLE, and DFU contracts.
- `tools/`: workspace-local build and bench helpers.
- `logs/`: ignored runtime output. Create timestamped run directories here.

## Hard boundaries

- The B306 hardware timer is the node time base.
- IMU timestamps belong to hardware-timer trigger instants.
- UWB epoch timestamps belong to hardware-captured ready edges.
- Host-side fusion remains on the PC until the P6 measurement model is frozen.
- `../UWB_Part/2026-07-15-FREEZE/` is read-only.

Read the workspace `AGENTS.md`, `../UWB_Part/FREEZE_INTERFACE.md`, and the
documents under `docs/` before adding firmware behavior.
