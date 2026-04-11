# BioSpur MultiNode Gesture System

This repository root is the OTA-first consolidation point for the BioSpur BLE gesture system.
It separates the common protocol surface from the two firmware roles that need to evolve in lockstep:

- `shared/`: locked protocol header and shared definitions
- `tx_node/`: BLE peripheral transmitter role with NUS plus MCUmgr SMP over BLE
- `central/`: BLE central receiver role with dual CDC ACM and MCUmgr over logical CDC ACM 0

The legacy folders in the surrounding workspace remain reference material only:

- `central_uart/`
- `Gesture_Recognition/`

They are useful for Zephyr/NCS patterns such as CMake wiring, BLE role scaffolding, and CDC ACM setup, but they are not the target architecture and must not be treated as the final app layout.

## Project Namespace

The canonical short project namespace is `BSGR` (`BioSpurGestureRecognition`).
Human-visible transport labels should use the `BSGR_*` family, while canonical machine identity remains `device_id`.

## OTA-First Development Order

This baseline intentionally prioritizes:

1. MCUboot and partition layout
2. MCUmgr transport wiring
3. USB CDC role split on the central
4. Shared protocol header with only locked elements
5. Build targets and direct-flash entry points

This baseline intentionally defers:

- final JY61P parsing pipeline
- hardware-gated IMU/UWB details
- full BLE aggregation behavior
- final OTA authorization policy

## Locked Baseline Decisions

- JY61P is treated as a UART-streaming module with onboard STM32.
- The universal protocol header stays minimal and does not carry an absolute timestamp.
- `device_id` is mandatory in the shared protocol.
- `seq` is session-scoped and must not be persisted in NVS.
- NUS and SMP over BLE are separate GATT services.
- `SET_IMU_PHASE` is host-side timestamp phase compensation only.
- USB binding is by logical CDC interface, never by host `/dev/ttyACM*` naming.

## Current Status

This OTA-first pass provides buildable project scaffolding for both firmware roles and keeps hardware-gated modules stubbed on purpose. The `docs/` baseline markdown file is a placeholder until the corrected v4.1 architecture/freeze-gate document is supplied verbatim.

## Build Targets

Board for both apps:

- `nrf52840dk/nrf52840`

TX:

```bash
west build -s BioSpur_MultiNode_Gesture_System/tx_node -b nrf52840dk/nrf52840 --sysbuild -d build/tx_node
west flash -d build/tx_node
```

Central:

```bash
west build -s BioSpur_MultiNode_Gesture_System/central -b nrf52840dk/nrf52840 --sysbuild -d build/central
west flash -d build/central
```

Expected artifact roots:

- `build/tx_node/`
- `build/central/`

Important artifacts to verify after a successful sysbuild:

- `merged.hex`
- `zephyr.signed.bin`
- `zephyr.signed.hex`
- `mcuboot/zephyr/zephyr.hex`
- `partitions.yml`
- `pm.config`
- `domains.yaml`

## Direct TX BLE OTA

For direct PC -> TX BLE OTA (without Central bridge), use:

- Runbook: `docs/TX_BLE_OTA_Runbook.md`
- OTA scripts: `tools/ota/`
- Host env check: `tools/host/check_ble_host_env.sh`

The current host `mcumgr` syntax in this environment is `--hci 0 --name BSGR_TX01`.
