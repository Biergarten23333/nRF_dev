# PC capture and parsing

This directory will contain the PC-side receiver, binary parser, capture writer,
and timing-validation tools for BioSpur Fusion.

Every capture must record both MCU firmware SHAs, node identity, BLE PHY,
connection interval, ATT MTU/data length, IMU rate, UWB interface version, and
GPIO-ready convention. Raw input and derived output belong in timestamped
directories under `../../logs/`, not beside the scripts.

The first host milestone is lossless capture and alignment diagnostics. ES-EKF
fusion remains host-side and starts only after the measurement model and R
matrix are frozen.
