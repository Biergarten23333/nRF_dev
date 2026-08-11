# Old Fusion compatibility matrix

| Old algorithm requirement | Current real field | Available | Unit/coordinate certain | Missing |
|---|---|---|---|---|
| 120 Hz synthetic/Vicon-derived IMU | 200 Hz timestamped AX..GZ | yes | units yes; body extrinsic no | real loader and extrinsic calibration |
| UWB solved position at capture cadence | eight raw ranges at ~8.33 Hz | raw only | mm/anchor ID yes | production geometry/delay binding and solver output |
| world frame x,z horizontal; y up | sensor-local axes | no direct map | no | board/body/world rotation |
| gravity `[0,-9.80665,0]` world | measured specific force | yes | scale yes | initial attitude; yaw unobservable statically |
| simulated bias/noise/RW | measured static bias/noise/Allan | partly | one pose only | temperature/multi-pose calibration |
| initial pose from Vicon/trajectory | none | no | no | pose/heading initialization |
| exact synthetic time alignment | B306 TIMER2 per node; UWB same node clock | within node yes | yes | cross-node common-clock fit is diagnostic, not a global truth clock |
| state `[p,v]` lite EKF or ESKF bias states | adapter provides IMU/UWB observations | input only | fields yes | validated attitude propagation/state initialization |
| propagation using fixed 1/120 s fallback | real B306 timestamp | yes | yes | loader must remove fixed-dt assumption |
| solved-position UWB update / limited raw prototype | eight-slot observations | yes | fields yes | production raw-range update and explicit invalid policy |
| innovation/NIS rejection | quality/validity retained | no implicit gate | yes | tuned R matrix and explicit rejection policy |
| A0 geometry and R2/R4 delay/bias | not embedded in capture | not bound | no | select authoritative production geometry; do not refit here |
| ideal/perfect IMU L0 or simulated L models | JY61P real data | no | n/a | abandon ideal-noise assumption |
| Vicon truth and full wand motion | absent | no | n/a | known trajectory or external ground truth for accuracy |

The reviewed branch `feature/wand-internal-sweep` implements position-domain T2/T3/T5, limited raw-range T6/T8 prototypes, and an IMU-only T11 diagnostic. The "real 6-axis" path still synthesizes IMU from Vicon at nominal sensor ODR, assumes a gravity/world convention and trajectory-derived initial orientation, and is not a loader for these physical JY61P samples. Final Fusion is therefore intentionally not run here.
