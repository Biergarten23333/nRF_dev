# IMU + relay batch report

Status: implementation/build phase. Hardware outcomes are intentionally blank
until measured.

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
| V-A2 65 s static | Noise and bias remain continuous across 65.5 s; no discrete clamp/step to exact zero. | At least 70 s raw gyro plot and change-point check. | NOT RUN |
| V-A3 ±2 g boundary | Raw acceleration is continuous across the boundary; no approximately 8× scale jump or persistent transient. | Raw axes and converted-g plot during repeated crossings. | NOT RUN |
| V-A4 static 5 min | About 60,000 pull attempts at 200 Hz; IMU sequence gaps 0, `imu_i2c_err=0`, `drop_err=0`; duplicate rate is measured rather than assumed; acceleration norm is near 1 g and gyro is noisy near zero, not clamped. | CDC raw log, derived JSON/PNG, start/end counters and negotiated CI. | NOT RUN |
| V-A5 chip-time latch | Register 0x33 advances on the sensor's approximately 5 ms refresh boundary, not on every faster host read. | Repeated `IMU SELFTEST` 0x30–0x40 triplets and step histogram. | NOT RUN |
| V-A6 BLE stress | With N=2 plus UWB for 30 min, `drop_err` delta is 0, CI is 15–30 ms, UWB rate remains steady, and any `drop_unsub` is confined to pre-subscription or intentional disconnect. | CDC log and counter deltas. | NOT RUN |

N=1 is not a production setting until a separate 30-minute run satisfies the
same loss criteria.

## Phase-A build evidence

Both builds were pristine NCS v2.8.0 builds under the mandated centralized
directories. Hardware deployment and validation have not yet occurred.

| Target | Marker | FLASH | RAM | malloc arena | Gate |
|---|---|---:|---:|---:|---|
| Fusion-PCB B306 | `b306-imu-relay-v11` | 195,732 / 499,200 B (39.21%) | 76,876 / 262,144 B (29.33%) | 0 B explicit | PASS |
| Fusion Master DK | `dk-fusion-imu-relay-v5` | 163,748 / 1,048,576 B (15.62%) | 87,836 / 262,144 B (33.51%) | 0 B explicit | PASS |

| Artifact | SHA-256 |
|---|---|
| `B306_Part/builds/b306-imu-relay-v11/merged.hex` | `5340d5284c79d0babed129f8ba06bf14767e4857063f0275eabb44ebe679965a` |
| `B306_Part/builds/b306-imu-relay-v11/firmware/zephyr/zephyr.signed.bin` | `e34e6f44bf061b50a0335dc7363d238e95b2726b8d14d092b014dcfc57ea9053` |
| `B306_Part/builds/b306-imu-relay-v11/dfu_application.zip` | `5a882bac2540c79726c59d82bdf90e75cce15d3bf82684488751dc649e0352a3` |
| `B306_Part/builds/dk-fusion-imu-relay-v5/merged.hex` | `ab2f83ca3401f84161d9262550fe3e97f6cbd4a652636ba507712e37e14c450a` |
| `B306_Part/builds/dk-fusion-imu-relay-v5/fusion_master/zephyr/zephyr.hex` | `e3c0699f0c84f33624cc8bc0c06c442d5409504dd9d5e6e5eedc19e511b31469` |

The two `biospur_link.h` copies are byte-identical at SHA-256
`792db4819ec320b586ac47b0a0a22e799c119b81bfb74ede3d8e0b40f06230f5`.
Both compiled consumers retain the original 90-byte `bsl_uwb_t` and 96-byte
`bsl_frame_t` static assertions.
