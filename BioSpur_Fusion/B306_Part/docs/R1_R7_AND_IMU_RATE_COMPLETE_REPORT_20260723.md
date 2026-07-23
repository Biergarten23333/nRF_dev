# Fusion Remote R1–R7 + JY61P IMU Rate Complete Report

Date: 2026-07-23  
Workspace: `/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion`  
Fusion node: `BSF3C79`  
Fusion Master: nRF52840 DK, J-Link `683234364`  
Control transport during this session: J-Link RTT  

## 1. Scope and result at a glance

This document combines two consecutive investigations:

1. the remote-executable R1–R7 validation batch; and
2. the follow-up JY61P I2C output-rate investigation through B306 firmware
   versions v12–v15 and the official WIT protocol.

The batch did not finish with an all-PASS result. It found two independent
blockers:

- JY61P provisioning failed at register `0x63`, leaving gyro auto-zero
  suppression inactive and every observed gyro sample clamped to exact zero;
- the DK RTT interim output was not end-to-end lossless under concurrent
  logging, even though B306/BLE internal counters remained clean.

The later rate investigation proved that B306 can schedule and transport
6,010 IMU samples in 30 seconds at exact 5,000 us timestamp spacing with zero
sequence gaps and zero device-side I2C/BLE errors. It did not prove that every
AX–GZ value is a newly calculated physical sample. Stationary AX–GZ values
retain a strong four-frame equality lattice while the sensor is configured
for 20 Hz bandwidth. Equality of stationary quantized values is therefore not
a valid freshness test.

## 2. Session constraints and hardware state

- No physical action was available during the remote batch.
- The DK native USB connector was physically broken. CDC firmware remained
  compiled and untouched, but RTT was the active PC transport.
- Probe `683234364` was the only probe used for DK flashing and RTT.
- Probe `1050070698` was not opened, reset, or flashed.
- The Master_Tag carrier was not flashed.
- Tag `v2-relay1` OTA and V-B validation were deferred.
- B306 updates used the existing BLE SMP/MCUboot OTA path.
- No IMU rate experiment issued JY61P SAVE or a JY61P register restart.

## 3. DK v7 and BLE bridge acceptance

DK marker:

```text
dk-fusion-imu-relay-v7
```

DK v7 added an RTT down-channel line reader to the same command parser used by
CDC. CDC remained the intended production interface when working hardware is
available.

The bridge to `BSF3C79` was established and subscribed. Negotiated parameters
were:

| Parameter | Result |
|---|---:|
| ATT MTU | 247 bytes |
| Data length | 251 bytes |
| PHY | 2M |
| Initial connection interval | 30 ms |
| Later connection interval | 50 ms |

Continuous protocol-v2 `FUSION_UWB` records were observed with
`valid=0xff`, `verdict=healthy`, and approximately 10 Hz output.

## 4. Pre-registered validation matrix

| Item | Intended acceptance | Final status |
|---|---|---|
| R1 / provisioning | Program and read back `0x61`, `0x63`, `0x03`, `0x1F`; prove persistence | **FAILED** at `0x63`; persistence half **NOT RUN** |
| R2 / self-test | Plausible acceleration, gyro, and temperature | Diagnostic completed; gyro result was a blocker |
| R3 / chip-time latch | Characterize `0x33` refresh behavior | **PASS** |
| R4 / static 5 min | 200 Hz, no gaps/errors, valid duplicate analysis, gyro not clamped | Original gate failed; freshness instrumentation later classified **INVALID** |
| R5 / 65.5 s boundary | No delayed gyro clamp/bias step | Formal gate **NOT RUN** because gyro was already clamped |
| R6 / 30 min BLE stress | IMU + UWB with zero end-to-end loss | **FAILED** at RTT text egress |
| R7 / session dry-run | Complete S1–S7 and reduced V-C1 | Stopped correctly at S2; remainder **NOT RUN** |

## 5. R1 — JY61P provisioning

Before provisioning:

```text
verify=WARN 61=0000 63=03E8 03=0006 1F=0004
```

`IMU PROVISION` attempted the configured register sequence. The write call for
register `0x63` returned success, but immediate readback did not equal
`0xFFFF`:

```text
IMU PROVISION FAIL step=63 err=-5
```

Firmware stopped before writing RRATE or bandwidth and before SAVE or sensor
restart.

Outcome:

- R1 programming: **FAILED**.
- R1 persistence test: **NOT RUN**, not failed.
- A subsequent B306 remote reboot reproduced:

```text
63=03E8 03=0006 1F=0004
```

This showed that the aborted volatile sequence did not modify the persisted
configuration.

## 6. R2 — read-only self-test

Thirty of thirty SELFTEST commands completed.

First converted sample:

```text
acceleration = [0.00928, -0.00781, -1.00830] g
norm         = 1.00837 g
temperature  = 28.48 °C
gyro         = [0, 0, 0] dps
```

Acceleration magnitude and temperature were plausible. Every gyro axis was
exactly zero. This was not a low-noise pass: it was evidence that automatic
gyro zeroing suppression had not been configured successfully.

## 7. R3 — chip-time latch characterization

R3 is **PASS** for the specific chip-time question.

- Register `0x33` advanced in approximately 5 ms steps.
- It wrapped at 1,000 ms.
- It did not advance on every faster host read.
- Of the first nominal 2 ms gaps, 22/30 crossed a refresh boundary.
- All 30 following nominal 4 ms gaps crossed a refresh boundary.

This proves the behavior of the chip-millisecond register. It does not, by
itself, prove that AX–GZ receives a different physical value on every chip-time
step.

## 8. R4 — five-minute static run

The run completed after a formal B306 remote reboot.

| Counter/metric | Result |
|---|---:|
| Pull attempts | 59,808 |
| Firmware-classified duplicates | 46,201 |
| Classified duplicate fraction | 77.25% |
| `imu_i2c_err` delta | 0 |
| BLE `drop_err` delta | 0 |
| UART/frame errors | 0 |
| Orphan counter deltas | 0 |
| Logger-drop counter delta | 0 |

The deployed firmware classified a frame as duplicate when its 12 AX–GZ bytes
were byte-identical to the previous frame. Every gyro value was already exact
zero.

The original closure treated the 77.25% rejection as an R4/V-A4 failure.
Later v12 analysis showed that the equality predicate was invalid
instrumentation for a stationary, quantized sensor. The numerical observation
remains real, but it cannot be called a measured stale-frame fraction.

Persisted bandwidth was `0x0004`, which the official protocol defines as
20 Hz. Surviving AX–GZ changes had a median interval of 19.991 ms and observed
change rates of 45.50–46.09 events/s. These are value-change rates, not proven
sensor production rates.

## 9. R5 — 65.5-second boundary

The diagnostic ran for 70 seconds after a separate B306 reboot.

- No additional discrete step was observed across 65.5 seconds.
- Gyro mean, standard deviation, and nonzero fraction remained zero on all
  three axes both before and after the boundary.
- Sequence gaps were zero.
- Firmware anomaly deltas were zero.
- UWB rate was 10.0121 Hz.

Formal V-A2 is **NOT RUN** because its prerequisite was absent: the gyro had
already been clamped to exact zero for the entire capture.

## 10. R6 — 30-minute concurrent BLE/UWB stress

The full interval completed and `IMU STOP` was acknowledged.

Device-side results:

| Metric | Result |
|---|---:|
| B306 I2C errors | 0 |
| BLE `drop_err` | 0 |
| Registered firmware anomaly deltas | 0 |
| UWB rate | 10.0006 Hz |

End-to-end RTT output was not lossless:

- four otherwise complete IMU lines were prefixed/interleaved into the
  thread-analyzer output and could be recovered offline;
- one IMU record was split by a concurrent `FUSION_HEALTH` print;
- that split left one sequence gap and two unavailable IMU samples;
- five lines contained embedded `FUSION_HEALTH`;
- two of those damaged UWB text records.

Therefore R6 is **FAILED** at the RTT interim egress. `logger_drop=0` only
proved that the DK internal queue did not drop; it did not prove that the
SEGGER RTT `NO_BLOCK_SKIP` stream was lossless.

## 11. R7 — session dry-run and V-C1

The script wrote predictions before opening RTT, remotely rebooted B306,
re-established the bridge, and passed S1 PING.

B306 returned three valid S2 STATUS replies, all containing:

```text
verify=WARN
```

The session predicate correctly rejected those replies. The reported
`no matching reply` meant that no reply met the PASS predicate, not that B306
was silent.

Because S2 failed:

- S3 never began;
- Master_Tag CDC was not opened;
- TDMA was not modified;
- S4–S7 were **NOT RUN**;
- reduced 30-minute V-C1 was **NOT RUN**.

## 12. R1–R7 original rig end state

At the end of the original remote batch:

```text
B306 remotely rebooted
IMU active=0
verify=WARN
61=0000
63=03E8
03=0006
1F=0004
UWB left as found
```

This is the original R1–R7 closure state. It differs from the current state
after the later v13–v15 volatile RRATE experiment.

## 13. Deferred physical validation

The following remained **NOT RUN / DEFERRED-PHYSICAL**:

- V-A1 slow rotation, the gold-standard gyro auto-zero test;
- V-A3 repeated ±2 g boundary crossings;
- V-C2 dynamic UWB/IMU timing handshake;
- lever-arm extraction from the KiCad machine;
- Master_Tag carrier flash and cold cycle;
- tag `v2-relay1` OTA;
- V-B1 through V-B5.

No claim is made that gyro auto-zero suppression works.

## 14. R1–R7 delivery provenance

Seven local test methods were present in the closure:

- five in `test_fusion_session.py`;
- two in `test_imu_remote_validation.py`.

Relevant commits:

```text
8794c848f fusion-master: add RTT control transport
d517a857c fusion-master: report current connection interval
661822f41 fusion: record remote IMU validation
```

Probe `1050070698` was not touched during this work.

---

# Part II — JY61P IMU Output-Rate Investigation

## 15. Why the rate investigation was reopened

The initial R4 firmware discarded byte-identical AX–GZ samples and labelled
them duplicates. The user correctly challenged the conclusion that this proved
an approximately 50 Hz sensor rate:

- a stationary, filtered, quantized sensor may legitimately emit identical
  values;
- old projects had configured and transported at least 100 samples/s;
- B306/Fusion Master batching had to be checked for correct N=2 decoding;
- the first-register and transaction shape had to match the official protocol.

The investigation therefore separated four different quantities:

1. B306 polling rate;
2. B306 publication/timestamp rate;
3. chip-time register progression;
4. physical AX–GZ value refresh.

These quantities must not be treated as interchangeable.

## 16. Official WIT protocol facts

Source:

```text
/home/zekaixiao/Documents/Datasheets/JY901S/WIT Standard Communication Protocol.pdf
```

### RRATE register `0x03`

| Value | Documented output rate |
|---:|---:|
| `0x08` | 50 Hz |
| `0x09` | 100 Hz |
| `0x0B` | 200 Hz |

The document names RRATE “Output rate”. It does not explicitly state that
RRATE controls I2C data-register refresh.

### BANDWIDTH register `0x1F`

| Value | Documented bandwidth |
|---:|---:|
| `0x00` | 256 Hz |
| `0x01` | 188 Hz |
| `0x02` | 98 Hz |
| `0x03` | 42 Hz |
| `0x04` | 20 Hz |
| `0x05` | 10 Hz |
| `0x06` | 5 Hz |

The tested device read back `0x0004`, meaning 20 Hz bandwidth.

### Data window

- Chip time occupies registers `0x30`–`0x33`.
- Acceleration begins at `0x34`.
- The official six-axis data read therefore begins at `0x34`.

## 17. Old-code audit

### Zentral_Sensorhub

`/mnt/nrf_ssd/nRF_dev/Zentral_Sensorhub/src/imu_task.c` writes:

```text
0x69 <- 0xB588
0x03 <- 0x0008
```

`0x08` is 50 Hz according to the official protocol. The code may poll or emit
host records more frequently, but its RRATE command is not 100 or 200 Hz.

### ADS1298_Test / Gesture-derived implementation

The relevant older implementation writes:

```text
0x69 <- 0xB588
wait 2 ms
0x03 <- 0x0009
wait 5 ms
```

The actual value is `0x09`, meaning 100 Hz. The source comments contain stale
200 Hz wording, but the command bytes and 10 ms sampling period are 100 Hz.

This supports the user's recollection that the old system was configured for
at least 100 Hz. It does not prove that the old project audited unique physical
samples at 100 Hz.

## 18. v12 — chip-time-based transport

Firmware marker:

```text
b306-imu-relay-v12
```

Read sequence:

1. burst read `0x33` through `0x40`;
2. immediately reread `0x33`;
3. discard a burst if the guard chip time changed;
4. retain a new chip-time frame even if AX–GZ equalled the previous frame.

Thirty-second result:

| Metric | Result |
|---|---:|
| Host samples | 6,034 |
| B306 TIMER2 span | 30.046780 s |
| Different-chip-time record rate | 200.7869 Hz |
| Mean detection interval | 4,980.4 us |
| Median detection interval | 5,003 us |
| p01 / p99 interval | 4,133 / 5,893 us |
| Sequence gaps | 0 |
| Missing samples | 0 |
| I2C errors | 0 |
| BLE notify errors | 0 |
| Concurrent UWB rate | 9.9980 Hz |

On-device accounting:

```text
26690 pulls
= 16028 repeated-chip-time polls
+  6044 fresh chip-time frames
+  4618 cross-refresh/coherency retries
```

AX–GZ equality:

| Metric | Result |
|---|---:|
| Equal adjacent AX–GZ | 4,670 / 6,033 = 77.41% |
| Four-frame runs | 1,177 |
| Eight-frame runs | 101 |
| Twelve-frame runs | 10 |
| Sixteen-frame runs | 2 |

The chip-time stream progressed at approximately 200 Hz. The four-frame
AX–GZ equality lattice remained.

Correct interpretation: v12 proved transport accounting and chip-time
progression. It did not independently prove 200 newly calculated physical
AX–GZ values per second.

## 19. v13 — volatile RRATE `0x000B`

Firmware marker:

```text
b306-imu-relay-v13
```

Runtime command:

```text
IMU RRATE=11
```

Register sequence:

```text
0x69 <- 0xB588
0x03 <- 0x000B
read 0x03
```

No SAVE and no JY61P restart were issued.

Reply:

```text
IMU RRATE OK request=000B readback=000B volatile=1 saved=0 step=readback err=0
```

Thirty-second result:

| Metric | RRATE `0x000B` result |
|---|---:|
| Valid samples | 6,032 |
| Different-chip-time rate | 200.8133 Hz |
| Centered `A=B=C` triplets | 3,305 / 6,028 = 54.83% |
| Four-frame runs | 1,169 |
| Eight-frame runs | 102 |
| Twelve-frame runs | 8 |
| Sixteen-frame runs | 6 |
| Device I2C/BLE errors | 0 |

RRATE changed from `0x0006` to `0x000B` and read back correctly, but the
stationary equality lattice was materially unchanged.

This rejects “the RRATE write was not accepted”. It does not prove whether
RRATE is unrelated to I2C refresh or whether 20 Hz bandwidth and stationary
quantization mask a 200 Hz calculation stream.

## 20. v14 — official `0x34` data window

Firmware marker:

```text
b306-imu-relay-v14
```

Changes:

- burst start moved from `0x33` to the official `0x34`;
- second `0x33` guard read was removed;
- data read covered `0x34` through `0x40`;
- B306 published on 5 ms B306-clock deadlines.

Thirty-second result:

| Metric | Result |
|---|---:|
| Samples | 6,010 |
| Unique acceleration triplets | 42 |
| Equal adjacent acceleration triplets | 4,635 / 6,009 |
| Four-frame runs | 1,144 |
| Eight-frame runs | 84 |
| Twelve-frame runs | 14 |
| Sequence/I2C/BLE errors | 0 |

The equality lattice survived. Therefore it was not created by placing
`0x33` at the front of the burst or by the second guard read.

## 21. v15 — old-code write-settle timing

Firmware marker:

```text
b306-imu-relay-v15
```

Version:

```text
0.1.14-imu-relay
```

Runtime sequence:

```text
0x69 <- 0xB588
wait 2 ms
0x03 <- 0x000B
wait 5 ms
read 0x03
```

No SAVE and no JY61P restart were issued.

Preflight:

```text
IMU RRATE OK request=000B readback=000B volatile=1 saved=0 step=readback err=0
STATUS fw=b306-imu-relay-v15
```

### v15 thirty-second result

| Metric | Result |
|---|---:|
| Samples | 6,010 |
| Timestamp spacing | 6,009 / 6,009 exactly 5,000 us |
| IMU sequence gaps | 0 |
| Missing IMU samples | 0 |
| I2C errors | 0 |
| BLE drops/errors | 0 |
| UWB records | 301 |
| UWB rate | 9.99767 Hz |
| Acceleration norm mean | 1.009906 g |
| Acceleration norm standard deviation | 0.000325 g |
| Mean temperature | 30.1088 °C |
| Exact-zero gyro triplets | 6,010 / 6,010 |

Acceleration equality:

| Metric | Result |
|---|---:|
| Unique acceleration triplets | 50 |
| Equal adjacent acceleration | 4,661 / 6,009 |
| Centered `A=B=C` | 3,320 / 6,008 = 55.26% |
| Four-frame runs | 1,110 |
| Eight-frame runs | 90 |
| Twelve-frame runs | 15 |
| Maximum equal run | 33 frames |

Including temperature produced 273 unique complete output tuples, but the
motion-vector four-frame lattice remained.

The missing 2 ms/5 ms delays were therefore not the cause of the equality
lattice.

## 22. What is proved and what remains unresolved

### Proved

- `RRATE=0x000B` is accepted and reads back as 200 Hz.
- B306 can publish 200 timestamped samples/s.
- B306 timestamps can remain exactly 5,000 us apart.
- N=2 decompression on the Fusion Master produces the expected two samples per
  record.
- Thirty-second v15 transport had zero sequence gaps, I2C errors, or BLE
  drops.
- UWB remained healthy at approximately 10 Hz concurrently.
- Register `0x33` progresses in approximately 5 ms steps.
- The stationary AX–GZ stream has a strong four-frame equality lattice.
- Changing the transaction to start at official register `0x34` did not remove
  the lattice.
- Adding the old 2 ms/5 ms configuration delays did not remove the lattice.
- Changing RRATE to `0x000B` did not materially change the lattice.

### Not proved

- It is not proved that AX–GZ is physically recalculated only at approximately
  50 Hz.
- It is not proved that every 5 ms B306 output contains a newly calculated
  physical AX–GZ sample.
- Static equality cannot distinguish a held register from a 200 Hz filtered
  and quantized stream.
- The official document does not explicitly bind RRATE to I2C register
  refresh.
- Gyro freshness cannot be evaluated while every gyro output is clamped to
  zero.

### Corrected conclusion

The earlier statement “the JY61P I2C motion window is proven to be about
50 Hz” is withdrawn.

The valid observation is:

> With RRATE `0x000B`, bandwidth `0x0004` (20 Hz), a stationary board, and
> gyro clamped to zero, AX–GZ values show a strong approximately four-sample
> equality lattice while B306 publishes at 200 samples/s.

The lattice is suspicious and consistent with an approximately 50 Hz latch,
but static equality is not sufficient to prove that interpretation.

## 23. Next discriminating experiments

### Remote, no persistent write

Add a volatile bandwidth command and test:

1. `BANDWIDTH=0x0002` (98 Hz), no SAVE;
2. if needed, `BANDWIDTH=0x0000` (256 Hz), no SAVE;
3. leave RRATE at `0x000B`;
4. repeat run-length and A/B/C analysis.

If the four-frame lattice changes with bandwidth, the current 20 Hz bandwidth
is part of the observed behavior.

### Physical gold standard

When an operator can touch the board:

- rotate or oscillate the sensor slowly and continuously;
- compare successive raw AX–GZ values and phase delay;
- repeat at RRATE 100/200 and bandwidth 20/98/256;
- perform the V-A1 slow-rotation gyro test after provisioning is fixed.

Dynamic excitation is the decisive freshness test.

## 24. v15 artifacts and resource gates

| Artifact | SHA-256 |
|---|---|
| `B306_Part/builds/b306-imu-relay-v15/firmware/zephyr/zephyr.signed.bin` | `3b3d3cc0fd02882edc922b15c12854e528c813e859399d440248695eca475fc8` |
| `B306_Part/builds/b306-imu-relay-v15/merged.hex` | `f52e50a422bee233d4ba6931213af25b362efcc3d4f17360958f9b47a2548d2d` |
| `B306_Part/builds/dk-ota-b306-imu-relay-v15/merged.hex` | `70c979b6bbb3d0db340058727170752bcf1ff8be682df2ccabed18c6c8dd15f5` |

Production memory gate:

| Region | Used | Capacity | Percent | Gate |
|---|---:|---:|---:|---:|
| FLASH | 196,524 B | 499,200 B | 39.37% | ≤95% PASS |
| RAM | 76,876 B | 262,144 B | 29.33% | ≤85% PASS |

Commit containing the v14/v15 sampling correction and report update:

```text
694d19b37 fusion: correct IMU register sampling experiment
```

## 25. Current rig end state after v15

The current state is later than the original R1–R7 closure:

```text
Fusion Master DK = dk-fusion-imu-relay-v7
B306             = b306-imu-relay-v15
IMU active       = 0
RRATE 0x03       = 0x000B, volatile
BANDWIDTH 0x1F   = 0x0004, unchanged
GYROCALITHR 0x61 = 0x0000
GYROCALTIME 0x63 = 0x03E8
UWB              = running and healthy
```

A B306 remote reboot returned the same v15 marker, proving the OTA image
survived a second boot. A B306-only reset does not remove power from the JY61P,
so the volatile RRATE value remained `0x000B`. No JY61P SAVE was issued.

## 26. Evidence index

### R1–R7 raw evidence

```text
B306_Part/logs/imu_remote_20260723_1600/
```

Important subdirectories:

```text
r1_r3/
r2_r3_after_r1_failure/
r4_unprovisioned_static_5min/
r5_unprovisioned_static_70s/
r6_unprovisioned_ble_stress_30min/
r7_sessions/
final_rig_state/
```

### Rate-investigation evidence

```text
B306_Part/logs/imu_v12_ratecheck_20260723_173932/
B306_Part/logs/imu_v13_rrate_runtime_20260723_181500/
B306_Part/logs/imu_v14_official_window_20260723/
B306_Part/logs/imu_v15_rrate_delay_20260723/
```

v15 raw capture:

```text
B306_Part/logs/imu_v15_rrate_delay_20260723/capture_30s/raw.log
```

v15 machine-readable summary:

```text
B306_Part/logs/imu_v15_rrate_delay_20260723/capture_30s/summary.json
```

Related living report:

```text
B306_Part/docs/imu_relay_batch_report.md
```
