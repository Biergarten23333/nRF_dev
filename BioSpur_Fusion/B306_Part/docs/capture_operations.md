# Fusion capture operations

## Mandatory pre-session reset

For unattended operation, issue `BSF#### REBOOT` through Fusion Master USB CDC
before every capture session. Wait for BLE reconnection and confirm fresh
`node_ms` plus `reset_reason`; a disconnect/reconnect alone is not proof.
Physical power cycling remains a bench fallback, not a remote prerequisite.

The v10-and-later timer extends the free-running 1 MHz TIMER2 across its natural
`2^32 us` wrap and reports `timer_wraps`; it no longer stops at 71.58 minutes.
The remote reboot rule remains because a fresh, attributable baseline is
required for every formal session, not as a workaround for the retired wrap
defect.

## Physical capture preflight

Before starting a formal capture:

1. Secure the RDY logic-analyser probe and both recorder connections with tape
   or an equivalent strain relief. Record a note or photo of the secured state.
2. Reboot B306 remotely, then record the fresh-uptime evidence.
3. Verify Tag firmware marker, TDMA generation, connection parameters, CAP
   mode, Anchor Master responder state, and all baseline counters.
4. Start the continuous B306 recorder before the formal window. When DSView's
   software limit requires segments, record each stop/start seam and use B306
   as the continuity authority across it.
5. Do not move, touch, or reconnect the rig during the run. Any physical
   intervention voids the run; stop and restart from step 1 rather than
   stitching runs.

Logs belong under a timestamped `logs/<purpose>_YYYYMMDD_HHMMSS/` directory and
must retain raw recorder files, wall-clock start/stop metadata, image markers,
connection settings, counter baselines, and SHA-256 for each DSView segment.
