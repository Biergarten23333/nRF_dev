# Phase A — T3-A, the "before" half. BSF6C53 on b306-v45r7-val (unfixed K_FOREVER)

Pre-registration: `A_PREREGISTRATION.md`,
sha256 `3443b1f9ea319cfa7d77d6f70e546215f9475fa11425c8c1806ede88c2b0968f`,
written and hashed **before** `V45 LEAK` was issued. Not edited since.

## Result: the phenotype reproduced

`V45 LEAK rc=0` accepted at host 1786300827.834594. The last telemetry ever
delivered arrived at 1786300827.277171 — **0.557 s before the command**. The
next 1 Hz telemetry, due ~1786300828.27, never came, and none has come since.

The board has been unreachable to every command issued after that point.

## Baseline (node clock, never host arrival)

notify_ok **31.33/s** | frames **8.33/s** | imu_records **20.00/s** |
watchdog_feeds **1.00/s** | drop_err 0 | notify_errno 0 | subscribed=1 have=1

## Invariants

| # | invariant | predicted | result | evidence |
|---|---|---|---|---|
| 1 | UWB + IMU stop near-simultaneously | 0.85 | **CONFIRMED** | both advance at nominal rate in the same final sample (frames +8, imu +20) and neither appears again; separation < 1 telemetry period |
| 2 | Link Layer stays alive | 0.90 | **CONFIRMED** | 213 QoS reports over 150 s of wedge, `crc_ok` steady 20/window, `crc_error` 0→0, handle 390 unchanged. `nak` climbed 0→20 |
| 3 | ATT read accepted, never answered | 0.70 | **CONFIRMED** | every command after the leak timed out with no reply, while QoS showed the link carrying traffic |
| 4 | disconnect does not re-advertise | 0.75 | **CONFIRMED** | forced disconnect via master `MODE IDLE`; **0 BSF6C53 advertising reports in 130 s** of scanning; 45 s after `MODE RECV` still no reconnection, QoS 0 |
| 5 | syswq + watchdog stay alive | 0.95 | **CONFIRMED** | no reset across the wedge: connection handle 390 unchanged for 150 s+, no reboot, no re-enumeration |
| 6 | DWM tag keeps ranging | 0.60 | **NOT TESTED** | no UWB listener process was running. Recorded as untested rather than assumed |
| 7 | power cycle fully restores | 0.97 | **PENDING** | requires the operator power-pull |
| 8 | latch, not graded decay | 0.80 | **CONFIRMED** | final samples are +31/+32 notify_ok, +8/+9 frames, +20 imu — fully nominal — then nothing. No ramp, no partial rate |

**Six confirmed, one not tested, one pending. Nothing refuted.**

## Drain time

Predicted 0.255 s, accept band 0.1–1.0 s, falsifier > 5 s.

**Measured: < 1.0 s.** The command landed 0.557 s after the last delivered
telemetry and the next 1 Hz sample never arrived. The 1 Hz telemetry cadence is
the observation resolution, so the true drain cannot be resolved more finely
than "less than one telemetry period". **Consistent with the prediction; not
independently precise.** Stated as a bound, not a measurement.

## Two things worth taking to Phase B

**The phenotype matches the fleet wedges exactly.** In all four N8 events
`notify_ok` ran at 31.4/s to the last complete sample and then stopped dead,
with `drop_err=0` and `notify_errno=0`. This injection produces the same:
nominal to the last sample, then a latch. That is a match of phenotype, and
phenotype matching is consistent with — not proof of — a shared cause.

**Invariant 4 is the one that settles B2's design.** A wedged node that gets
disconnected does not come back on air at all: no advertising for 130 s, no
reconnection after the master returned to RECV. `bt_conn_disconnect()` is
therefore useless as a recovery action — it converts a wedged-but-connected
node into a wedged-and-invisible one. Recovery must be
`sys_reboot(SYS_REBOOT_COLD)`. This confirms independently what wedge #2's RAM
dump implied.

## Fork recorded

Invariant 6 could not be run: no listener was up, and starting one after the
wedge would not give the required pre/post poll-rate ratio anyway. Marked NOT
TESTED. The invariant matters for whether the UWB path survives independently
of BLE; it remains open.
