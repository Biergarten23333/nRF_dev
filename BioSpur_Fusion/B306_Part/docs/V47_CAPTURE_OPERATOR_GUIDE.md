# Starting canonical v47 Fusion/human capture

Use the finalized v47 identity and the existing read-only inventory/precheck.
Start the Listener collector first, then the Fusion raw collector. Establish
one T0 only after both evidence paths are live. The formal collector begins a
serialized, staggered `V45 GUARD` T0 snapshot only after raw logging exists.

Periodic diagnostics are disabled by default. The only automatic diagnostic
phases are the T0 baseline, a host-visible anomaly, and a bounded best-effort
snapshot before a normal operator stop. One node is queried at a time. A
timeout or malformed/wrong-node reply is retained and cannot stop collection.

Do not enable periodic polling until separately authorized hardware validation
measures full ten-node UWB/IMU rate, maximum gaps, malformed/drop counts,
control RTT, connection epochs, Listener continuity, CPU and storage overhead
against a passive control. Do not ACK a corpse during capture. Preserve it and
open a separate evidence workflow. Never infer missing fields as zero.

For a human session, record consent/session metadata outside raw streams,
preserve exact v47 identity and T0, and stop collectors cleanly before changing
power. Observation is not permission to reboot, reconnect, OTA, configure, or
otherwise mutate a node.
