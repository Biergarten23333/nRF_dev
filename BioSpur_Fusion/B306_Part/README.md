# B306 fusion node

`B306_Part` contains the new nRF52840 side of BioSpur Fusion. The B306 owns the
node clock, timestamps the JY61P IMU and DWM1001C UWB epochs, batches the aligned
records, and sends them to a Fusion Master over BLE.

The current checkpoint adds a receive-only DWM1001C UART parser and a diagnostic
BLE data/telemetry service on top of the accepted Stage 1 OTA base. The
installed B306 image is `b306-uart-rx-p1.01-v7`, version `0.1.6+0`. It has no
IMU driver, ready-edge capture path, production batching, or fusion algorithm.
See `UART_BRINGUP_REPORT.md` for the real-board A/B result and current DWM-side
blocker.

## Layout

- `builds/`: the only location for generated B306 and Fusion Master DK builds.
- `firmware/`: minimal Zephyr application and future B306 firmware.
- `host/dk_ota/`: Fusion Master build wrapper around the single frozen fast OTA
  core; exact B306 target/image/SHA are mandatory build inputs.
- `host/fusion_master/`: DK diagnostic central for the UART-to-BLE bridge.
- `host/dwm_scanner/`: read-only DK scanner used to identify DWM firmware state.
- `host/dongle_central/`: retired placeholder; the DK is the Fusion Master.
- `host/pc/`: future capture, validation, parsing, and provenance tooling.
- `docs/`: timing, BLE, and DFU contracts.
- `handover/`: self-contained human-only Fusion-PCB flash packages.
- `tools/`: workspace-local build and bench helpers.
- `logs/`: ignored runtime output. Create timestamped run directories here.

## Hard boundaries

- The B306 hardware timer is the node time base.
- IMU timestamps belong to hardware-timer trigger instants.
- UWB epoch timestamps belong to hardware-captured ready edges.
- Host-side fusion remains on the PC until the P6 measurement model is frozen.
- The first B306 image must demonstrate a BLE-only DFU cycle before feature
  images are accepted.
- `../UWB_Part/2026-07-15-FREEZE/` is read-only.

Read the workspace `AGENTS.md`, `../UWB_Part/FREEZE_INTERFACE.md`, and the
documents under `docs/` before adding firmware behavior.
