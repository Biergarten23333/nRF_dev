# Fusion capture operations

## Mandatory pre-session reset

**Power-cycle the B306 before every capture session.** Unplug and reconnect its
power immediately before preflight. Do not substitute a button reset. Confirm
the first B306 `strobe_us` / `node_ms` values are near zero before accepting
the start of a run.

The deployed capture firmware stops producing UWB records and telemetry when
its 1 MHz 32-bit TIMER2 reaches `2^32 us` (71.58 minutes of B306 uptime). The
power-cycle rule keeps that boundary outside a capture session of at most 60
minutes; it is an operational mitigation, not the firmware fix. Continuous
operation and sessions longer than 60 minutes are unsupported until the wrap
debt in `dfu.md` is closed.

## Physical capture preflight

Before starting a formal capture:

1. Secure the RDY logic-analyser probe and both recorder connections with tape
   or an equivalent strain relief. Record a note or photo of the secured state.
2. Power-cycle B306, then record the fresh-uptime evidence.
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
