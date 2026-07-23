# Fusion Master Stage 2/4b instrument

This nRF52840 DK application records the
`DWM1001C -> B306 UART/READY -> BLE -> Fusion Master` attribution path. It
scans for the `BSF` name prefix plus the Task B Fusion service UUID, subscribes
to the UWB and telemetry characteristics, and prints the complete protocol-v2
record over RTT: sweep, raw 40-bit `poll_tx`, `valid_mask`, `STROBE_SENT`,
64-bit B306 frame/rising/falling timestamps, pairing verdict, and cumulative
orphan/drop counters.

BLE callbacks only copy packets into a fixed message queue. A dedicated logger
thread formats RTT output, so console latency cannot block the Bluetooth RX
context. Queue overflow, malformed packets, and protocol drift are explicit
counters.

The current build understands the extended remote-readiness telemetry
(`timer_wraps`, `watchdog_feeds`, and `reset_reason`). Its merged image is:

```text
B306_Part/builds/dk-fusion-remote-ready-v10/merged.hex
SHA-256 c1eba5b73f59baa3970e1e98955f13b6d1b58f06526420edbe5332c55d1fc65e
```

The 2026-07-21 300 s acceptance recorded 2,907 complete records with
`malformed=0` and `logger_drop=0`. This remains an RTT measurement instrument,
not the final native-USB-CDC capture transport; IMU batching and the Stage 4c
clock filter remain separate gates.

Flash only DK probe `683234364`, always with explicit probe selection. Never
allow an interactive J-Link probe-selection dialog.
