# Bring-up measurement and field telemetry

The Fusion PCB exposes `UWB_RDY`, `UWB_TX1`, and `UWB_RX1` at 0 Ω series
resistors. A logic analyser is the ground truth during UART/strobe bring-up;
the firmware does not need an inferred failure taxonomy to replace signals
that can be observed directly.

## Bring-up

Capture the DWM1001C nRF52832 P0.26 ready pulse at `UWB_RDY`, the UART frame at
`UWB_RX1`, and, if later used, the B306 transmit line at `UWB_TX1`. Decode
UART at 460800 8N1. Correlate the ready pulse, 96-byte v2 frame, sweep counter,
and `poll_tx_ts` while exercising normal and deliberately interrupted ranging.

The Stage 4c clock-filter residual is the timing-quality result. Ready-edge
jitter appears in that residual, so strobe-to-`poll_tx_ts` standard deviation
is not a separate deliverable. Analyse `t_round_us` by rank from ordinary
captures to characterize responder timing, but do not use its distribution as
a bring-up acceptance gate.

## Field telemetry

Keep these counters and estimates available when the board is worn and the
analyser is absent:

- UART CRC errors and parser resynchronizations.
- Dropped and duplicated sweep counters.
- Unpaired ready strobes and unpaired UART frames, counted separately.
- Clock-filter residual and trend.
- BLE logical-batch loss and reconnect count once those paths exist.

Counters diagnose field degradation; they do not replace the analyser during
bring-up. Raw captures, derived distributions, and decision reports remain
separate under timestamped `logs/` run directories.
