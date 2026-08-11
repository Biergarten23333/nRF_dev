# v47 host evidence status — 2026-08-11

Canonical v47 is frozen and unchanged. The collector correction is host-only:
dedicated GUARD retention, append-only schema, raw replies, identity checks,
T0 deltas and isolated failures. Periodic polling remains disabled.

No firmware/release/identity file was changed; no firmware was built. No
hardware, serial, BLE, J-Link, SWD or RTT was accessed, and no command was sent.
The implementation contains no OTA, reboot, configuration or corpse-ACK path.
