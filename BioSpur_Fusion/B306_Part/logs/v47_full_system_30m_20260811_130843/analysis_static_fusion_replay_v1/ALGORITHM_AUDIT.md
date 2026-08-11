# Algorithm audit

## Finding

The historical directions are not collapsed. `HISTORICAL_ALGORITHM_MAP.csv` binds every T number to its exact source file, origin commit `1b80923d1afe483f3be5d1afc18d6ef8ea6c5802`, measurement domain, state and update direction. Git blame confirms `relative_motion_filter`, `integrate_imu_packet`, `fuse_uwb_imu_packet`, `forward_imu_corrects_uwb`, `forward_bidirectional_fusion`, and `range_ekf_track` originate in that commit; later branch head `94ef793faec8570fbf6779a187ad9765a15e9c22` does not create three independent phase2 EKFs.

T2/T2LITE is IMU-corrects-UWB: an IMU position delta predicts the UWB output track, then UWB pulls that output with a Kalman gain or fixed alpha. T5/T5LITE is UWB-corrects-IMU: IMU propagates `[p,v]`, then solved-UWB XYZ updates the inertial state. T3LITE is a literal same-time `0.68*T5 + 0.32*T2` bidirectional output blend. Critically, the phase2 T2/T3/T5 sweep calls the same 3-state `relative_motion_filter` for all three and changes sigma/labels; it is not evidence that three distinct EKFs ran. The T5 registry name says error-state/bias, while executed real6 T5LITE has `[p,v]`, a scalar external yaw and no bias state.

T6 and T8 are separately audited raw-range position prototypes. Both need bound Anchor XYZ/delay; T8 inflates sigma eightfold for residuals beyond three sigma. T11 is strictly the IMU-only control and historically injects truth initialization.

The current real adapter and identical T0+1..484 s window are entered for T2/T3/T5/T6/T8/T11. T11 executes. All five spatial algorithms stop at the same capture-bound geometry gate, with zero state updates; this is recorded per node in `HISTORICAL_DIRECTION_REPLAY.csv`. Running one of them with Erlangen geometry would violate the capture contract and would not be a reproduction of this room. Therefore no performance ranking is claimed. `ARCHITECTURE_SELECTION.json` makes only a structural decision: inherit the T5 UWB-corrects-IMU direction, retain T2/T3 as controls, eliminate their fixed-alpha/fixed-blend implementations from the final architecture, and leave T6/T8 pending geometry-bound comparison. M4 is not declared the final architecture.

## Real compatibility extension

| Concern | Old executed behavior | Real compatibility action | Status |
|---|---|---|---|
| state | `[p,v]` plus external scalar yaw | scalar-first body-to-local quaternion and gyro-bias error states | minimal required extension |
| initialization | truth position/velocity/yaw | local origin/zero velocity, gravity roll/pitch, arbitrary yaw=0, static gyro bias | truth injection removed |
| time | actual synthetic times with 1/120 fallback | every actual B306 TIMER2 sample; explicit fixed-dt ablation only | validated |
| gravity | Vicon world y-up | per-node local z-up; specific force maps to +z, then gravity is subtracted | internally validated; room/body extrinsic missing |
| ZUPT | registry pending | explicit zero-velocity Kalman measurement at 20 Hz under frozen labels | implemented |
| UWB | solved position or Erlangen-bound range prototype | complete range-space innovation/accounting only | spatial coupling blocked |

This proves the real adapter, inertial propagation, gravity initialization, explicit ZUPT, asynchronous UWB insertion and range innovation plumbing. It does not claim completed historical spatial reproduction.
