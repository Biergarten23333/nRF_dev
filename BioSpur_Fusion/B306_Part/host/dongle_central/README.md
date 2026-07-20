# Fusion Master dongle

This directory is reserved for the nRF52840 dongle firmware that will act as a
BLE central for Fusion nodes and expose captured batches to the PC over native
USB CDC.

The central will eventually negotiate 2M PHY and data length extension, retain
per-node connection metadata, preserve packet boundaries, and forward provenance
with the data stream. It is not scaffolded as an application yet because the
Fusion BLE packet format is not frozen and the nominal 360-byte logical batch
does not fit one 251-byte BLE data-length payload.

This target must never be confused with either B120 UWB master.
