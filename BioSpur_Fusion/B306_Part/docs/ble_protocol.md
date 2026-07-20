# BLE protocol

This document records constraints for the production B306-to-Fusion-Master
service. The current UART bring-up firmware implements a diagnostic
UWB/telemetry service; its packed definitions and UUIDs are in
`../include/biospur_fusion_ble.h`. That service is not yet the production
batched transport.

## Roles

- B306: BLE peripheral and producer.
- nRF52840 DK, probe `683234364`: Fusion Master BLE central; native USB CDC is
  still a later acceptance item.
- PC: loss detection, persistence, alignment, and fusion.

The DWM1001C BLE link is OTA-only and silent during capture.

## Production cadence

The logical target batch covers 100 ms:

- 20 IMU samples at 200 Hz.
- The concurrent UWB epoch or an explicit no-epoch marker.
- One common B306 timer domain and sequence metadata.

Twenty 12-byte IMU samples plus one 90-byte `bsl_uwb_t` and batch metadata are
about 337 bytes. Individual IMU samples do not carry timestamps; the batch
start and fixed 5 ms cadence reconstruct their trigger times. A BLE Link Layer
data payload with
data length extension is at most 251 bytes, and ATT notification payload is
smaller after protocol headers. Therefore the logical batch cannot be "one DLE
packet."
Before implementation, choose and test one of:

- fragment one logical batch into multiple sequenced notifications;
- reduce samples per batch while retaining a 10 Hz logical envelope; or
- use a more compact packed representation.

The exact byte layout, service UUIDs, ATT MTU, fragment size, integrity check,
and retransmission policy are `UNKNOWN`.

## Required fields

Each logical batch must identify:

- protocol version, node identity, boot/session identity, and batch sequence;
- batch-start B306 timer tick, fixed sample cadence, and batch-end consistency
  information;
- IMU sample count plus six signed axes per sample;
- UWB sweep sequence, sweep-start timestamp, eight range/status entries, and
  the UWB interface version;
- overflow, dropped-sample, missing-edge, and parser-error counters.

Integers must have a documented byte order and scale. Invalid data needs an
explicit status; zero is not a missing-value sentinel.

## Link behavior

Request 2M PHY and data length extension. Start with a 15–30 ms connection
interval and measure, rather than assuming, notification scheduling and latency.
Entering DFU disables capture and the production stream. Returning from DFU
starts a new boot/session identity in RUN.
