# BSFC2CC interactive rotating-arm validation

Primary verdict: `C2CC_ROTATION_STATE_MACHINE_FAIL`

The collector used one Fusion serial open, one raw file and one decoder lifecycle. Warm-up ended at `2026-08-11T23:38:55.015+02:00` with `LIVE_CATCHUP_OBSERVED`. Formal evidence contains `365383` IMU samples and `15224` UWB sweeps over `1826.8920828700066` seconds; capture integrity is `PASS`.

The operator completed low, medium and high sustained ON/OFF phases and short cycles 1–2. At `2026-08-12T00:07:55.011+02:00` the operator shortened the protocol before viewing analysis, kept the motor OFF for `86.89628328004619` seconds, and cleanly stopped the existing collector. Cycles 3–5 are `NOT_EXECUTED_BY_OPERATOR_SHORTENING`; they are not data loss and cannot be represented as completed.

Frozen S2 parameters were not changed. B0, historical S1, S2P and S2R used identical decoded inputs and hardware time. State-machine gate details are in `ROTATION_PHASE_RESULTS.csv` and `SHORT_CYCLE_RESULTS.csv`. Any path-shape diagnostic is `NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY`; absolute trajectory accuracy is unavailable.

## Operator brackets

- `RPM3_READY`: instruction `2026-08-11T23:39:55.039+02:00`, token `RPM3_READY` at `2026-08-11T23:49:14.189+02:00`, bracket `559.149716` s.
- `LOW_ON`: instruction `2026-08-11T23:49:14.203+02:00`, token `LOW_ON` at `2026-08-11T23:49:56.276+02:00`, bracket `42.072546` s.
- `LOW_OFF`: instruction `2026-08-11T23:50:56.306+02:00`, token `LOW_OFF` at `2026-08-11T23:51:37.567+02:00`, bracket `41.261180` s.
- `RPM8_READY`: instruction `2026-08-11T23:52:37.659+02:00`, token `RPM8_READY` at `2026-08-11T23:53:00.681+02:00`, bracket `23.022546` s.
- `MEDIUM_ON`: instruction `2026-08-11T23:53:00.709+02:00`, token `MEDIUM_ON` at `2026-08-11T23:53:20.249+02:00`, bracket `19.539866` s.
- `MEDIUM_OFF`: instruction `2026-08-11T23:54:20.260+02:00`, token `MEDIUM_OFF` at `2026-08-11T23:54:56.136+02:00`, bracket `35.875461` s.
- `RPM15_SAFETY`: instruction `2026-08-11T23:55:56.163+02:00`, token `RPM15_SAFE` at `2026-08-11T23:56:31.711+02:00`, bracket `35.547619` s.
- `RPM15_READY`: instruction `2026-08-11T23:56:31.715+02:00`, token `RPM15_READY` at `2026-08-11T23:56:41.916+02:00`, bracket `10.201248` s.
- `HIGH_ON`: instruction `2026-08-11T23:56:41.964+02:00`, token `HIGH_ON` at `2026-08-11T23:57:10.771+02:00`, bracket `28.806262` s.
- `HIGH_OFF`: instruction `2026-08-11T23:58:10.817+02:00`, token `HIGH_OFF` at `2026-08-11T23:58:31.885+02:00`, bracket `21.068192` s.
- `SHORT_RPM8_READY`: instruction `2026-08-12T00:00:01.920+02:00`, token `SHORT_RPM8_READY` at `2026-08-12T00:00:26.902+02:00`, bracket `24.982073` s.
- `CYCLE_1_ON`: instruction `2026-08-12T00:00:26.921+02:00`, token `CYCLE_1_ON` at `2026-08-12T00:00:55.657+02:00`, bracket `28.735379` s.
- `CYCLE_1_OFF`: instruction `2026-08-12T00:01:05.671+02:00`, token `CYCLE_1_OFF` at `2026-08-12T00:04:02.567+02:00`, bracket `176.895416` s.
- `CYCLE_2_ON`: instruction `2026-08-12T00:04:33.176+02:00`, token `CYCLE_2_ON` at `2026-08-12T00:05:12.108+02:00`, bracket `38.931785` s.
- `CYCLE_2_OFF`: instruction `2026-08-12T00:05:22.129+02:00`, token `CYCLE_2_OFF` at `2026-08-12T00:05:38.340+02:00`, bracket `16.211355` s.
- `MEDIUM_OFF#`: rejected exactly as entered; capture continued until accepted `MEDIUM_OFF`.
- `END_SEQUENCE_AFTER_CYCLE_2`: `2026-08-12T00:07:55.011+02:00`; followed by `86.896283` s final static and legacy clean unwind.

## Phase results

- LOW: definitely-ON `60.029903` s; independent motion `115.577851624` s; S2P/S2R release `1.73994488049` s; false relock `0`; settling interruptions `4`; post-settle relock `0.0` s (`ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE`).
- MEDIUM: definitely-ON `60.011745` s; independent motion `104.181699641` s; S2P/S2R release `1.44495422547` s; false relock `1`; settling interruptions `0`; post-settle relock `None` s (`SETTLE_TIME_AMBIGUOUS`).
- HIGH: definitely-ON `60.046264` s; independent motion `104.751681585` s; S2P/S2R release `4.88984509521` s; false relock `0`; settling interruptions `1`; post-settle relock `None` s (`SETTLE_TIME_AMBIGUOUS`).
- CYCLE_1: definitely-ON `10.014469` s; independent motion `203.803543733` s; S2P/S2R release `4.9248439865` s; false relock `0`; settling interruptions `0`; post-settle relock `None` s (`SETTLE_TIME_AMBIGUOUS`).
- CYCLE_2: definitely-ON `10.020751` s; independent motion `29.7640571082` s; S2P/S2R release `1.19496214496` s; false relock `1`; settling interruptions `2`; post-settle relock `0.0` s (`ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE`).

Low-speed release passed its 2.0 s target. Medium and high release failed the 1.0 s target. Medium produced a complete `MOVING → SETTLING → STATIONARY → MOTION_SUSPECTED → MOVING` false-relock sequence while independent raw motion continued. Low and high also entered `SETTLING` and returned to `MOVING` during sustained raw motion, violating the no-chattering gate even though they did not fully relock. Both completed short ON episodes were detected; Cycle 2 relocked during residual independently detected motion and then unlocked again before its final relock.

Initial and shortened-final stationary gates pass for both S2P and S2R. Locked published movement is zero and reinitializations are zero. Capture accounting, finite/symmetric/PSD covariance and update accounting close. S2R nevertheless has a dynamic self-consistency failure: its global position RMS is 63.835 m and phase position norm reaches 556.405 m, versus room-scale S2P behavior. This is not an absolute-accuracy claim.

No perfect-circle, radius, angular-speed, loop-closure, home-return, lever-arm or absolute trajectory metric is reported. No external ground truth exists.
