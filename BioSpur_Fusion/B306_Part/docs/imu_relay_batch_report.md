# IMU + relay batch report

Status: B306 v15 and DK `dk-fusion-imu-relay-v7` are deployed. DK v7 provides
bidirectional J-Link RTT control on explicitly selected probe `683234364` while
retaining the native-USB CDC implementation. Remote validation found two
independent blockers: JY61P provisioning fails at register `0x63`, and the RTT
interim output is not lossless under the current logging load. Phase-B tag OTA
and every physical validation remain deferred while the operator is away.

## A0 preflight evidence

| Check | Evidence | Result |
|---|---|---|
| B306 strobe input | `firmware/src/strobe_capture.c:28`: `STROBE_PIN NRF_GPIO_PIN_MAP(1, 3)` | P1.03 |
| IMU SDA/SCL | `firmware/boards/biospur_fusion_nrf52840/biospur_fusion_nrf52840_nrf52840-pinctrl.dtsi:36-37`: `TWIM_SDA,0,26` and `TWIM_SCL,0,27` | P0.26/P0.27 |
| Pin conflict | strobe is port 1 pin 3; IMU is port 0 pins 26/27 | none |
| I2C controller before this batch | `firmware/boards/biospur_fusion_nrf52840/biospur_fusion_nrf52840_nrf52840.dts:87-94`: `imu_i2c: &i2c0`, 400 kHz, `status = "disabled"` | confirmed |
| I2C controller in this image | `firmware/boards/biospur_fusion_nrf52840_nrf52840.overlay:6-9`: `&imu_i2c { status = "okay"; }` | enabled at 400 kHz |
| UART downlink | board pinctrl has `UART_TX,1,2`; application overlay uses the full board UART pinctrl | P1.02 enabled |

TIMER2 on nRF52840 has four CC channels and the deployed strobe implementation
already owns all four: CC0 natural-wrap notification, CC1 rising capture, CC2
falling capture, CC3 software capture/read. The IMU scheduler therefore uses
absolute deadlines read from the same 1 MHz TIMER2 and kernel timed sleep
between deadlines; each actual TWIM pull is timestamped from TIMER2 at
initiation. It does not steal or weaken either hardware strobe-capture channel.
The final generated DTS identifies the controller as `nordic,nrf-twim`, records
`easydma-maxcnt-bits = <16>`, and enables interrupt 3. The final `.config`
contains `CONFIG_I2C_NRFX_TWIM=y`; its ELF contains `i2c_nrfx_twim_transfer`,
`nrfx_twim_xfer`, and `nrfx_twim_0_irq_handler`. NCS
`zephyr/drivers/i2c/i2c_nrfx_twim.c:60,150` serializes the transfer and waits
on the completion semaphore. No polling I2C path is linked.

## Pre-registered Phase-A predictions

These predictions were written before any IMU hardware validation run.

| Test | Prediction fixed before run | Pass evidence to collect | Outcome |
|---|---|---|---|
| V-A1 slow rotation | A sustained 0.5–1 °/s input remains visibly above stationary noise through the final 20 s; it does not decay to zero. | Raw gyro time series, first/last-window comparison, provisioning transcript. | NOT RUN |
| V-A2 65 s static | Noise and bias remain continuous across 65.5 s; no discrete clamp/step to exact zero. | At least 70 s raw gyro plot and change-point check. | NOT RUN as a valid gate; R5 diagnostic crossed the boundary with gyro already clamped to exact zero throughout |
| V-A3 ±2 g boundary | Raw acceleration is continuous across the boundary; no approximately 8× scale jump or persistent transient. | Raw axes and converted-g plot during repeated crossings. | NOT RUN |
| V-A4 static 5 min | About 60,000 pull attempts at 200 Hz; IMU sequence gaps 0, `imu_i2c_err=0`, `drop_err=0`; duplicate rate is measured rather than assumed; acceleration norm is near 1 g and gyro is noisy near zero, not clamped. | RTT raw log, derived JSON, start/end counters and negotiated CI. | INVALID INSTRUMENTATION: firmware used equality of AX..GZ as its freshness test, which discards legitimate equal-valued static samples; chip-time evidence instead shows an approximately 5 ms refresh |
| V-A5 chip-time latch | Register 0x33 advances on the sensor's approximately 5 ms refresh boundary, not on every faster host read. | Repeated `IMU SELFTEST` 0x30–0x40 triplets and step histogram. | PASS: 30 triplets show approximately 5 ms steps and 1000 ms wrap; it does not step on every read |
| V-A6 BLE stress | With N=2 plus UWB for 30 min, `drop_err` delta is 0, CI is 15–30 ms, UWB rate remains steady, and any `drop_unsub` is confined to pre-subscription or intentional disconnect. | RTT log and counter deltas. | FAILED end-to-end: BLE/device counters were clean and UWB was 10.0006 Hz, but actual CI was 50 ms and RTT output corrupted/lost records |

N=1 is not a production setting until a separate 30-minute run satisfies the
same loss criteria.

## Pre-registered Phase-C predictions

These predictions were written before deployment or any integration run.

| Test | Prediction fixed before run | Outcome |
|---|---|---|
| S4 UWB proof | Both UART-frame and RDY-rise counters increase at 8–12 Hz, with at most one boundary-count difference; `LIVE=1` alone cannot pass. | NOT RUN |
| S7 10 s sentinel | UWB remains at 8–12 Hz, every observed UWB record is healthy, all CRC/header/ring/sweep/drop/orphan/logger/relay-timeout deltas are zero, IMU output reaches at least 80% of its configured record rate, and IMU sequence gaps are zero. | NOT RUN; R7 stopped at S2 because `verify=WARN` |
| S7 rollback | Any S7 exception or failed predicate issues and acknowledges `IMU STOP`; UWB is not stopped. | NOT RUN |
| V-C1 30 min | UWB/tag production anomalies and B306 transport/orphan/drop-error deltas remain zero, frame rate stays steady, IMU sequence gaps remain zero, and every 60 s relay command is correlated and acknowledged. | NOT RUN; S2 prerequisite failed and RTT interim had already shown nonzero loss |
| V-C2 dynamic handshake | UWB displacement and IMU acceleration share one TIMER2 axis; their apparent offset is constant and approximately the I2C pull latency rather than motion-dependent drift. | NOT RUN |

`B306_Part/tools/fusion_session.py` implements the mandatory S1–S7/T1–T3
ordering with bounded waits, a single-owner lock, stable USB identity
resolution, DTR/RTS disabled, per-run raw/JSON logs, and a prediction file
written before either serial port is opened. Offline parser/gate tests cover
correlated replies, 16-bit IMU sequence wrap, a clean sentinel, and rejection
of orphan plus sequence-gap evidence.

The original Phase-C search looked for the wrong CAD family. The board design
authority is **EasyEDA**, and the recovered project identifies BioSpur V0.10
and the fitted JY61P. Existing P1/P2 data resolve the maneuver-frame mapping,
but the capture metadata does not bind that hinge to the PCB long/short axis;
the persisted operator estimate contains only the approximate 400 mil
magnitude, not signed board-frame XYZ. The host-side `fusion_config.json`
therefore keeps the three components null and retains
`allow_fusion_with_missing_lever_arm=false` instead of inventing signs.

## Phase-A build evidence

All builds were pristine NCS v2.8.0 builds under the mandated centralized
directories. Deployment and remote-validation outcomes are recorded below.

| Target | Marker | FLASH | RAM | malloc arena | Gate |
|---|---|---:|---:|---:|---|
| Fusion-PCB B306 | `b306-imu-relay-v11` | 195,732 / 499,200 B (39.21%) | 76,876 / 262,144 B (29.33%) | 0 B explicit | PASS |
| Fusion Master DK | `dk-fusion-imu-relay-v6` | 163,824 / 1,048,576 B (15.62%) | 87,836 / 262,144 B (33.51%) | 0 B explicit | PASS |
| Fusion Master DK | `dk-fusion-imu-relay-v7` | 164,572 / 1,048,576 B (15.69%) | 90,396 / 262,144 B (34.48%) | 0 B explicit | PASS |

| Artifact | SHA-256 |
|---|---|
| `B306_Part/builds/b306-imu-relay-v11/merged.hex` | `5340d5284c79d0babed129f8ba06bf14767e4857063f0275eabb44ebe679965a` |
| `B306_Part/builds/b306-imu-relay-v11/firmware/zephyr/zephyr.signed.bin` | `e34e6f44bf061b50a0335dc7363d238e95b2726b8d14d092b014dcfc57ea9053` |
| `B306_Part/builds/b306-imu-relay-v11/dfu_application.zip` | `5a882bac2540c79726c59d82bdf90e75cce15d3bf82684488751dc649e0352a3` |
| `B306_Part/builds/dk-fusion-imu-relay-v6/merged.hex` | `c3b49e433e74a0dfd3f60b0ef9cda36108d347479b0801e888a65861d0690783` |
| `B306_Part/builds/dk-fusion-imu-relay-v6/fusion_master/zephyr/zephyr.hex` | `74a7a2f7e05d4c3bad8bafb1302413d04b8babd7de348c3c54e651c45b6a46ea` |
| `B306_Part/builds/dk-fusion-imu-relay-v7/merged.hex` | `3d9fb24cc8f2f8d3c6eb75e2fcca4446a3e0d4619b450044bc5d854c476b52f0` |
| `B306_Part/builds/dk-fusion-imu-relay-v7/fusion_master/zephyr/zephyr.hex` | `085888e3f8ce360cdd97e5c723783930d0397e0eaf8c7cf9347bd68e9f0730c4` |

The two `biospur_link.h` copies are byte-identical at SHA-256
`792db4819ec320b586ac47b0a0a22e799c119b81bfb74ede3d8e0b40f06230f5`.
Both compiled consumers retain the original 90-byte `bsl_uwb_t` and 96-byte
`bsl_frame_t` static assertions.

## Deployment evidence

The B306 updater targeted exact identity `BSF3C79` and payload
`b306-imu-relay-v11`, signed-file SHA-256
`e34e6f44bf061b50a0335dc7363d238e95b2726b8d14d092b014dcfc57ea9053`.
The independent verify-only run reported:

```text
OTA image-state verdict: marker=b306-imu-relay-v11 hash=match active=1 confirmed=1
OTA_STATE:post_verify_passed detail=hash_active_confirmed
```

The initial DK v5 deployment exposed a client-side discovery defect:
data notifications were subscribed at value/CCC 18/19, but UUID-filtered
discovery returned `discover_4 not_found` after telemetry value 21. DK v6
enumerates the descriptor and verifies its UUID before subscribing. Its
hardware RTT acceptance reported:

```text
FUSION_TELEMETRY_SUBSCRIBED value=21 ccc=22
FUSION_CONTROL_CHARACTERISTIC value=24 props=0x0c
FUSION_BRIDGE_READY name=BSF3C79 rssi=-45 mtu=247 data=18 telemetry=21 control=24
```

The same run negotiated ATT MTU 247, DLE 251 bytes, and 2M PHY, then received
continuous protocol-v2 `FUSION_UWB` records with `valid=0xff` and
`verdict=healthy`. This proves the DWM1001C→B306 UART/strobe path and
B306→DK BLE data path are live before IMU is enabled. The DK native connector
is physically broken for this trip, so DK v7 added RTT down-channel input to
the same parser used by CDC. `LIST` over RTT verified BSF3C79 connected and
subscribed, with MTU 247, DLE 251, 2M PHY, an initial 30 ms CI, and a subsequent
50 ms CI update. The CDC code remains compiled and unchanged in role.

## Remote-continuation evidence — 2026-07-23

All evidence is under
`B306_Part/logs/imu_remote_20260723_1600/`. No command opened or reset probe
`1050070698`; no Master_Tag flash, tag OTA, or physical action was attempted.

### R1–R7 closure summary

- DK v7 RTT bidirectional control was deployed and verified. Native CDC was
  not merely unopened: the connector was physically broken. BLE negotiated
  MTU 247, DLE 251, and 2M PHY; CI initially reached 30 ms and later settled
  at 50 ms.
- R1 **FAILED** at register `0x63`: the write returned success but immediate
  readback was not `FFFF`, so provisioning stopped before SAVE. After reboot,
  persisted state remained `63=03E8 03=0006 1F=0004`.
- R2 measured acceleration norm `1.00837 g` and temperature `28.48 °C`.
  Every gyro sample was exactly zero, proving auto-zero suppression had not
  succeeded.
- R3 **PASS**: chip time advanced in approximately 5 ms steps and wrapped at
  1000 ms.
- The original R4/V-A4 closure labelled the run failed because 77.25% of
  pulls were discarded as duplicates and every retained gyro value was zero.
  The gyro failure remains real. Later v12 analysis reclassified the
  duplicate-rate portion as **INVALID INSTRUMENTATION**, because equality of
  stationary quantized AX..GZ values is not a valid freshness predicate.
- R5 crossed 65.5 s without an additional discrete step, but the gyro was
  already clamped throughout; formal V-A2 therefore remained **NOT RUN**.
- R6 had zero B306/BLE internal errors and UWB `10.0006 Hz`, but concurrent
  RTT output interleaved/split records: one IMU record/two samples and two UWB
  text records became unavailable. The end-to-end result was **FAILED**.
- R7 stopped correctly at S2 on `verify=WARN`. Master_Tag CDC was not opened,
  TDMA was not changed, and S3–S7 plus V-C1 remained **NOT RUN**.
- Probe `1050070698` was not touched. The original closure left B306 remotely
  rebooted, `IMU active=0`, and UWB as found.

The closure delivery comprised seven local tests: five in
`test_fusion_session.py` and two in `test_imu_remote_validation.py`. Relevant
commits were:

- `8794c848f` — `fusion-master: add RTT control transport`
- `d517a857c` — `fusion-master: report current connection interval`
- `661822f41` — `fusion: record remote IMU validation`

### R1 — provisioning

R1 is **FAILED**. Before provisioning, `IMU STATUS` reported:

```text
verify=WARN 61=0000 63=03E8 03=0006 1F=0004
```

`IMU PROVISION` returned `FAIL step=63 err=-5`. The register-0x63 write call
returned success but its immediate readback did not equal `FFFF`; firmware
therefore aborted before writing RRATE/BW and before SAVE or sensor restart.
The persistence half of R1 is **NOT RUN**, not failed. A B306 remote reboot
discarded the partial volatile sequence and reproduced the same four persisted
values. Evidence:
`r1_r3/raw.log`, `r1_r3/summary.json`, and
`r2_r3_after_r1_failure/summary.json`.

### R2/R3 — read-only characterization after R1 failure

R2 ran only after that reboot. Thirty of thirty SELFTEST commands completed.
The first sample was acceleration
`[0.00928, -0.00781, -1.00830] g` (norm `1.00837 g`), temperature
`28.48 °C`, and gyro `[0, 0, 0] dps`. Exact zero on all gyro axes is evidence
that auto-zero suppression is not active; it is not a low-noise pass.

R3 is **PASS** for the chip-time question. The 30 triplets show approximately
5 ms refresh steps and a 1000 ms wrap. Of the first nominal 2 ms gaps, 22/30
crossed a refresh boundary; all 30 following nominal 4 ms gaps did. The value
did not advance on every host read.

### R4/R5 — static diagnostics under persisted defaults

R4 completed 300 seconds after a formal remote reboot. Device deltas for
`imu_i2c_err`, BLE `drop_err`, UART/frame errors, orphan counters, and logger
drops were all zero. It made 59,808 pulls. The firmware classified 46,201
(77.25%) as duplicates because `imu.c` compared only the 12 AX..GZ bytes and
discarded a pull when they were byte-identical. That is not a valid freshness
test for a stationary quantized sensor, especially with persisted bandwidth
`0x0004` (the WIT SDK names this approximately 21 Hz). The surviving
motion-change events had a 19.991 ms median interval and rates of 45.50,
46.09, and 45.67 events/s across R4, R5, and R6 respectively; these are
AX..GZ-change rates, not sensor output rates.

R3 proves only that register `0x33` advances in approximately 5 ms steps even
when read at shorter intervals. The initial interpretation—that this also
proved a 200 Hz AX..GZ refresh—was later falsified by v12's run-length
measurement. Acceleration norm was `1.00928 g`, while every retained gyro
triplet was exactly zero; the gyro finding remains evidence that auto-zero
suppression was not configured.

### v12 freshness correction and direct rate check

`b306-imu-relay-v12` replaces AX..GZ equality as the freshness test with
register `0x33` chip milliseconds. It continuously reads `0x33` through
`0x40`, guards each burst with an immediate second `0x33` read, and retains
new chip-time frames even when all six motion channels are byte-identical.

A 30-second run produced 6,034 different-chip-time records over 30.046780
seconds of TIMER2 time, or 200.7869 records/s. On-device counters were
`p=26690 rpt=16028 new=6044 eq=4678 bad=4618 miss=0 ie=0 rec=3022`.
`new / N=2 = records` exactly, but this proves only transport accounting.

The A/B/C audit found 3,308 / 6,032 centered triplets with `A=B=C`. Of 1,364
identical-vector runs, 1,177 were exactly four frames long, 101 were eight, 10
were twelve, and two were sixteen. This is a real four-frame equality lattice,
but it is not by itself a freshness clock: the device was stationary and
quantized, the gyro was clamped to zero, and register `BANDWIDTH=0x0004`.
The official WIT protocol defines `0x0004` as 20 Hz bandwidth. The experiment
therefore cannot distinguish an approximately 50 Hz register latch from
200 Hz computation whose filtered, quantized motion values remain equal.
Evidence is under
`B306_Part/logs/imu_v12_ratecheck_20260723_173932/`.

### v13 volatile RRATE=0x000B experiment

`b306-imu-relay-v13` adds the two-write runtime path recovered from
`Zentral_Sensorhub`: `0x69←0xB588`, then `0x03←requested`, with immediate
readback. It deliberately issues neither SAVE nor sensor restart.

The command changed RRATE from `0x0006` to `0x000B` and read back `0x000B`.
After a 30-second run it still read `0x000B`, proving that the volatile write
was accepted. The resulting distribution was materially unchanged:
1,169 four-frame runs, 102 eight-frame runs, eight twelve-frame runs, and six
sixteen-frame runs; 3,305 / 6,028 centered triplets had `A=B=C`.
Therefore RRATE `0x000B` did not change the static equality lattice. The WIT
protocol calls RRATE the output rate but does not state that it controls I2C
register refresh, so this run cannot decide between that interpretation and a
20 Hz bandwidth/quantization effect. One DK RTT line was corrupted by an
embedded `FUSION_HEALTH`, making two host text samples unavailable;
device-side I2C and BLE error counters remained zero. Evidence is under
`B306_Part/logs/imu_v13_rrate_runtime_20260723_181500/`.

### v14/v15 official-window and write-settle correction

`b306-imu-relay-v14` removed the `0x33`-leading burst and guard read and used
the official six-axis register window starting at `0x34`. Version v15 then
matched the older ADS/Gesture runtime write timing: 2 ms after
`0x69←0xB588`, 5 ms after `0x03←0x000B`, readback, and no SAVE or restart.
Both changes left the static equality lattice materially unchanged.

The v15 run delivered 6,010 samples at exactly 5,000 us B306 timestamp
spacing, with zero sequence gaps, I2C errors, BLE drops, or UWB anomalies.
There were 1,110 four-frame and 90 eight-frame equal-motion runs; 3,320 /
6,008 centered triplets had `A=B=C`. This rejects the hypotheses that either
the `0x33` transaction shape or missing 2 ms/5 ms write delays created the
lattice. It still does not turn equality into a sensor freshness test.
After the run, a B306 remote reboot returned the same
`b306-imu-relay-v15` marker with IMU stopped, proving the OTA image survived a
second boot; RRATE remained volatile `0x000B` because a B306-only reset does
not remove power from the JY61P. Bandwidth remained untouched at `0x0004`.

Protocol facts from `WIT Standard Communication Protocol.pdf` are:
`RRATE 0x09=100 Hz`, `0x0B=200 Hz`; `BANDWIDTH 0x04=20 Hz`,
`0x02=98 Hz`, `0x00=256 Hz`; and six-axis data starts at `0x34`.
The next discriminating register experiment is a volatile bandwidth change
with no SAVE, followed by a dynamic test when physical access is available.
Evidence is under
`B306_Part/logs/imu_v14_official_window_20260723/` and
`B306_Part/logs/imu_v15_rrate_delay_20260723/`.

R5 completed 70 seconds after a separate reboot. The 65.5 s boundary has
zero mean, zero standard deviation, and zero fraction 1.0 on every gyro axis
both before and after. There was no additional step, but because the signal
was already clamped for the whole run, V-A2's prerequisite was absent and its
formal outcome remains **NOT RUN**. Sequence gaps and firmware anomaly deltas
were zero; UWB rate was `10.0121 Hz`.

### R6 — 30-minute concurrent stress

The full device interval completed and `IMU STOP` was acknowledged. B306
reported 359,870 pulls and 277,689 AX..GZ-equality rejections (77.16%); the
latter must not be interpreted as a sensor duplicate rate. I2C errors, BLE
`drop_err`, and every registered firmware anomaly-counter delta were zero.
UWB frame rate was `10.0006 Hz`.

The RTT interim exit is **FAILED** for end-to-end losslessness. Four otherwise
complete IMU lines were prefixed/interleaved into the periodic thread-analyzer
dump and were recoverable offline. One IMU record was split by a concurrent
`FUSION_HEALTH` print, leaving one sequence gap and two unavailable samples.
Five output lines contained embedded `FUSION_HEALTH`; two of those corrupted
UWB text records. Thus `logger_drop=0` proves only that the DK's internal
message queue did not drop—it does not prove the `NO_BLOCK_SKIP` RTT stream is
lossless. The current periodic analyzer dump also creates a burst large enough
to collide with the data stream. Evidence:
`r6_unprovisioned_ble_stress_30min/raw.log`, `summary.json`, and
`analysis.json`.

### R7 and V-C1

The R7 dry-run wrote its predictions before opening RTT, remotely rebooted
B306, re-established the bridge, and passed S1 PING. B306 returned three valid
S2 STATUS replies, all containing `verify=WARN`; the session predicate
correctly rejected them. The displayed `no matching reply` error means no
reply satisfied the PASS predicate, not that B306 was silent. S3 never began,
so the Master_Tag CDC was not opened and TDMA was not changed. S4–S7 and the
reduced 30-minute V-C1 are **NOT RUN** because their S2 prerequisite failed and
the RTT exit had already failed zero-loss validation.

### Deferred and rig end state

V-A1 slow rotation, V-A3 ±2 g, V-C2 dynamic handshake, lever-arm extraction,
the Master_Tag carrier flash/cold cycle, tag v2-relay1 OTA, and V-B1–V-B5 are
all **NOT RUN / DEFERRED-PHYSICAL**. No claim is made for auto-zero suppression.

The final remote action observed IMU inactive, rebooted BSF3C79, re-established
the bridge, and re-read
`IMU active=0 ... verify=WARN 61=0000 63=03E8 03=0006 1F=0004`.
It sent no UWB command, so UWB was left as found. Evidence:
`final_rig_state/summary.json`.

## Phase-B offline and handover evidence

The Fusion-tag image is `tag-fusion-link-v2-relay1`. Its signed binary is
SHA-256 `3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b`;
the DFU ZIP is
`63b8127638c972a5551d8c007e0386de270cba72ea02446fcd07ca357361a8ce`.
Its production gates pass at 90.45% FLASH, 84.13% RAM, and explicit zero-byte
malloc arena. The 96-byte range-frame static assertions remain present; the
APOS symbols are absent and the UART RX/ring/relay symbols are present.

The Master_Tag carrier embeds that exact signed payload. Its CPUAPP image
SHA-256 is
`f5f504360bfea2e5b5fb13c76b40a5830f1bf3e83f01d4feec0865c47b1ce37a`;
its CPUNET image is
`9c17013e933dcccfdc611085b1154a6b3cc775e59b00da542f5fcf8a0ba94199`.
CPUAPP passes at 37.88% FLASH / 34.45% RAM and CPUNET at 59.50% FLASH /
66.55% RAM; both malloc arenas are explicit zero and both cores use calibrated
LFRC.

The carrier was deliberately not flashed by Codex: probe `1050070698` is
outside the standing autonomous-flash authority and was previously used while
the Fusion PCB DWM1001C was connected. The operator must confirm it is attached
to the Master_Tag B120, execute the dual-core handover, and cold-power-cycle
the B120. The standalone procedure and frozen rollback are in
`UWB_Part/handover/master-tag-relay1-carrier/README.md`. Tag OTA duration and
all V-B results remain **NOT RUN** until that handover is reported successful.

## Homecoming continuation — H1.2 and V-B

The deferred carrier ceremony was completed and cold-cycled under the H1
recovery record. Probe `1050070698` remained on Master_Tag and was not touched
during the following OTA or validation.

The tag payload `tag-fusion-link-v2-relay1`, SHA-256
`3175f6b5b72258fe6da73ac89b72cfd839bba7443f2028f2b1418cf77429e97b`,
was delivered over Path M. The established OTA driver completed upload,
pending/test, and reset in 41.59 seconds and restored Master_Tag RECV at
43.50 seconds. The independent nRF54 observer saw the post-reset advertising
burst; Master_Tag reconnected and confirmed the new marker. The later marker
check was not part of one continuous host timeline, so no exact
command-to-marker duration is claimed.

V-B1 through V-B5 passed:

- Path M produced a one-tag `MASK=0x0001` schedule and 10.000 Hz `TR;2`.
- Path R PING/STATUS/VERSION preserved B306 queue acknowledgement, tag source,
  and correlation.
- Direct Path-R CFG returned `LIVE=1 RUN=1` and produced 10.021391 Hz in the
  steady window with zero monitored anomaly deltas. Its acknowledgement
  reported `MASK=0x0000`, retained as an observed direct-CFG representation.
- M→R→M completed without a stuck/zero-TX state; the final Path-M stage
  measured 9.9751 Hz.
- V-B5 is the H1.2 timing result: 41.59 seconds through upload/test/reset and
  43.50 seconds to restored Master_Tag RECV. Exact command-to-marker time is
  not claimed because the marker check used a later host session.

The pre-registered immediate V-B3 12-second rate prediction missed because the
window included the explicit 5,000 ms epoch delay and averaged 6.582 Hz. This
was an activation-timing prediction error, not missing transmissions after the
epoch.

The two prior empty 60-second Master_Tag scans remain unexplained. The live
post-OTA advertising/reconnect path passed, so a tag-side no-resume defect is
not confirmed. Full candidate-stream logging remains mandatory for future
scans.

The Fusion Master decision is now corrected: DK `683234364` with
`dk-fusion-imu-relay-v7` remains installed, native USB CDC is primary, and RTT
is diagnostic backup. The previously reported "physically broken connector"
was wrong; cable/data-path replacement restored CDC. The custom-B306 receiver
swap is cancelled as a necessity.

Evidence:
`B306_Part/logs/homecoming_20260724/h1_2_ota_20260724_183622/REPORT.md`.
