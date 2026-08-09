# T3-A pre-registration — written BEFORE `V45 LEAK` is issued

Board BSF6C53, `b306-v45r7-val`, unfixed `K_FOREVER`, uptime 3406 s, healthy.

Measured baseline (node clock, not host arrival):
notify_ok 31.33/s | frames 8.33/s | imu_records 20.00/s | watchdog_feeds 1.00/s
drop_err 0 | notify_errno 0 | drop_unsub 70 flat | subscribed=1 have=1
QoS per 1002 ms window: crc_ok 31, crc_error 1, nak 0, rx_timeout 0, reports 20

NOTE: baseline `crc_error` is 1, not 0. Invariant 2 below is therefore stated
as "does not degrade", not "is zero". Recording this now so it cannot be
reinterpreted after the fact.

## Predictions

| # | invariant | prediction | confidence |
|---|---|---|---|
| 1 | UWB + IMU export both stop, near-simultaneously | both cease; separation < 1 s at record resolution; in-flight residual ≤ 8 records | 0.85 |
| 2 | BLE Link Layer stays alive | crc_ok stays ≈ 20-31/window, crc_error does not climb, nak stays 0 | 0.90 |
| 3 | ATT read accepted on air, never answered | read times out; a subsequent op returns -ENOMEM | 0.70 |
| 4 | controller disconnect does not re-advertise | no advertising for ≥ 120 s after a forced disconnect | 0.75 |
| 5 | syswq + watchdog stay alive | no reset over extended observation | **0.95** |
| 6 | DWM tag keeps ranging | listener post/pre poll ratio in 0.94-1.10 | 0.60 |
| 7 | power cycle fully restores | all rates return to baseline | 0.97 |
| 8 | the stop is a latch, not graded decay | rates go to zero within one sample, no ramp | 0.80 |

Invariant 5 is the highest-confidence prediction because it is not a guess: the
N8 fleet data shows a 30 s RESET_SOC watchdog fed 1:1 with uptime that never
fired across BSFEC35's 5 h 27 min wedge. If invariant 5 fails here, the fleet
data and this bench event are different phenomena and Phase B's premise needs
re-examination.

Drain time prediction: 8 att_pool buffers at 31.33 notifications/s = **0.255 s**
from the leak command to export cessation. Accept 0.1-1.0 s as confirming;
anything > 5 s falsifies the pool-exhaustion mechanism.

## Falsifier for the whole experiment

If export does NOT stop within 30 s of `V45 LEAK`, the injection does not
reproduce the wedge phenotype on this build, and the claim that
`K_FOREVER` + a held `sync_evt_pool` buffer produces the fleet wedge is
unsupported. Phase B1's premise would then rest on the upstream commit message
alone, and that must be stated rather than smoothed over.

## What this experiment CANNOT show

It cannot prove the fleet wedges share this mechanism. It reproduces a
phenotype; matching phenotypes are consistent with, not proof of, a shared
cause. Wedge #2 (host conn released, ref=0) is a separate mechanism that this
injection is not expected to reproduce at all.
