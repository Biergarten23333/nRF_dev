# BioSpur BLE v4.1 Architecture Baseline and Freeze-Gate Specification

This file is a placeholder for the corrected v4.1 architecture/freeze-gate markdown baseline.

The authoritative document was referenced in the implementation request but was not present in the local workspace during this consolidation pass. To avoid fabricating design-locked or hardware-gated details, this repository intentionally stores only the OTA-first code baseline plus this placeholder.

Until the corrected v4.1 markdown is provided verbatim, treat the following as the only locked implementation guidance captured in-repo:

- JY61P is a UART-streaming module with onboard STM32.
- The universal frame header remains minimal and does not include an absolute timestamp.
- `device_id` exists in the shared protocol.
- `seq` is session-scoped and is not persisted to NVS.
- NUS and SMP over BLE are separate GATT services.
- `SET_IMU_PHASE` means host-side timestamp phase compensation only.
- USB role binding is by logical CDC interface rather than host device path naming.
- Hardware-gated items remain stubbed, deferred, or unresolved in this OTA-first baseline.

Replace this file with the corrected v4.1 baseline markdown as soon as it is available.
