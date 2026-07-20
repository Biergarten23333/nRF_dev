# Fusion Master UART-bridge bring-up

This nRF52840 DK application proves the basic
`DWM1001C -> B306 UART -> BLE -> Fusion Master` path. It scans for the `BSF`
name prefix plus the Task B Fusion service UUID, subscribes to the UWB and
telemetry characteristics, and prints both over RTT.

This is a bring-up instrument, not the final capture transport. It does not
claim the Stage 2 hardware timebase, IMU batching, strobe pairing, or native
USB CDC acceptance. Those remain separate gates.

Flash only DK probe `683234364`, always with an explicit `--dev-id`.
