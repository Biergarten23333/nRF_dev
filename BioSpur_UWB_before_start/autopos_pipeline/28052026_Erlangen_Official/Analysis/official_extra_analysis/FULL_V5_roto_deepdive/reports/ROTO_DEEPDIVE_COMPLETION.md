# ROTO Deep-Dive Completion

Generated: 2026-06-18T01:47:40

| task | status | elapsed_s | key_finding |
| --- | --- | --- | --- |
| R1 | ok | 16.0 | median improvement 0.7 mm |
| R2 | ok | 0.3 | best D_Sim3_existing_beta 74.3 mm |
| R3 | ok | 51.5 | independent 101.1 mm |
| R4 | ok | 0.2 | gap 45.5 mm |
| R5 | ok | 92.5 | soft_nlos 104.2 mm |
| R6 | ok | 15.5 | worst sector 300 deg |

## Recommended Dynamic Tracking Pipeline

Use the existing V5 D_LOO per-frame solver as the conservative baseline. Treat time-offset tuning and rigid two-tag projection as diagnostics until hardware time sync and a true range-level rigid-body solver are validated.
