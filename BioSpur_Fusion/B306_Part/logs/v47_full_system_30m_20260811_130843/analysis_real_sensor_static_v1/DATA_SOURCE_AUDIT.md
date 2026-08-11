# Data source audit

The authoritative raw is `formal_capture/fusion_host_raw.cobs.bin` (SHA-256 `c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8`). It was streamed through the production COBS/CRC contract. Before T0: 1790 complete records and 1 accepted preflight-boundary decode error; formal records have one 1-byte shutdown tail fragment and no complete corrupt record. Formal IMU/UWB counts exactly match `PER_BOARD_COUNTS.csv`; every node has zero IMU sequence and UWB sweep gaps.

Source trail: `firmware/src/imu.c` reads 26 bytes from JY61P register 0x34 as signed little-endian AX,AY,AZ,GX,GY,GZ and temperature; `include/biospur_fusion_ble.h` defines batch `seq`, low-word `base_timer2_ts_us`, and per-sample `delta_us`; `host/fusion_master/src/main.c` extends the timer epoch and stamps `master_arrival_ms=k_uptime_get()`; `include/biospur_link.h` defines the 90-byte eight-slot UWB body; `tools/fusion_host_binary.py` is the reference host decoder. The conversion constants are explicitly documented in `docs/ble_protocol.md`.

The source does not define a calibrated board/body extrinsic, a fully validated axis handedness mapping, or an absolute yaw reference: these are `UNKNOWN_FROM_SOURCE`. Host and Master receipt timestamps are not substituted for B306 sample time. Pre-T0 and shutdown-tail bytes are isolated from formal sensor statistics.
