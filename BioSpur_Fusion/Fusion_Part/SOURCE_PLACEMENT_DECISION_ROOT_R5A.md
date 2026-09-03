# Root-R5A source placement decision

Root-R5A is an offline, Python-only scientific diagnostic. Its source is
isolated under `Fusion_Part/src/biospur_fusion/root_r5a/`, its tests are under
`Fusion_Part/tests/root_r5a/`, and its entry points are Root-R5A-specific tools
under `Fusion_Part/tools/`.

Formal outputs are written only to new timestamped directories under
`Fusion_Part/logs/root_r5a_diagnostic_*`. Root-R4 source and evidence, frozen
M1, UWB firmware/frontends, existing algorithms, runners, configurations, and
product defaults are immutable inputs. This stage never executes real C1
fusion and never changes firmware or hardware.

The implementation enforces four frozen invariants:

1. IMU and UWB are bidirectional correction inputs; neither is unconditional
   truth.
2. A fixed initial gauge and time-varying IMU yaw error are separate states.
3. UWB authority is conditional on motion, quality, geometry, timing,
   innovation consistency, and state observability.
4. Raw ranges and T4 are dependent representations of shared events and are
   never activated together for the same physical event.
