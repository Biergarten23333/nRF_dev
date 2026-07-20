# Timing contract

## Clock ownership

The B306 hardware timer is the only node time base. A timestamp is a wrapping
unsigned timer tick plus a boot/session identity; host software must use modular
subtraction and must not compare ticks across boots.

Stage 2 will use a 1 MHz, 32-bit TIMER2-or-higher counter extended to 64 bits
in software. TIMER0 belongs to MPSL, and PPI channels are allocated
dynamically. The concrete instance and the multiple-wrap implementation remain
`UNKNOWN` until Stage 2 is implemented and measured.

NINA-B306-01B has no 32.768 kHz crystal. MCUboot and the application therefore
use the 500 ppm LFRC with periodic calibration against the high-frequency
clock:

```ini
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_500PPM=y
CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC_CALIBRATION=y
```

In NCS v2.8.0, `..._RC_CALIBRATION` is the applicable symbol; the shorter
`CONFIG_CLOCK_CONTROL_NRF_CALIBRATION` spelling is not defined. The LF clock is
for BLE scheduling and sleep only. Never move the node master clock, IMU
trigger, or UWB capture to an RTC: at 500 ppm an LF-derived clock accumulates
500 us of error per second and violates the single-clock alignment contract.

## IMU samples

The target IMU cadence is 200 Hz, one trigger every 5 ms. The timestamp attached
to a sample is the hardware-timer trigger instant, not I2C completion and not a
JY61P internal timestamp. The I2C transaction may complete later, but its result
is paired with the already-captured trigger tick.

The fitted JY61P uses the WT61P-compatible 6-axis subset: I2C address `0x50`,
accelerometer and gyroscope registers `0x34`–`0x39`, and `RRATE` register
`0x03 = 0x000B` for nominal 200 Hz. Do not read magnetometer/Euler registers
`0x3A`–`0x3F`.

P1 must still verify the part functionally: ACK at `0x50`, gravity and gyro
while flat/still, gravity moving to the expected signed axis after a 90-degree
rotation, and duplicate consecutive sample counts at a 200 Hz poll rate.
Measure missed triggers, read latency, and JY61P-versus-B306 drift in ppm for
30 minutes.

## UWB epochs

The intended UWB timestamp is a GPIOTE-to-PPI-to-TIMER capture of the DWM1001C
ready edge in the broadcast-poll TX-done path. The paired UART frame carries
the exact raw DW1000 poll-TX timestamp, so the constant edge-to-TX offset is a
clock-filter offset term. Software ISR time and UART arrival time are not valid
primary timestamps.

Current hardware/firmware state:

1. The frozen DWM1001C firmware has no sweep-ready output.
2. The schematic label `GPIO19 Ready` means DWM1001 module pin 19 (`READY`),
   which maps to nRF52832 P0.26. A whole-tree audit of the frozen firmware found
   P0.26 unused.
3. nRF52832 P0.19 remains the internal DW1000 IRQ input and must not be
   repurposed.
4. The derived `UWB_Part/fusion-link/` Task A image configures P0.26 low at
   initialization and emits one nominal 10 us pulse in the broadcast-poll
   TX-done path. It has compiled but has not been deployed or measured.
5. The confirmed B306 capture input is nRF52840 P1.03 (`UWB_RDY`, NINA GPIO_37).

The accompanying UART v2 frame carries the same sweep counter, raw 40-bit
DW1000 poll-TX timestamp, and measured response-RX-minus-poll-TX
`t_round_us[]`. UART arrival remains diagnostic; the captured P0.26 edge is
the B306 time-domain observation. B306 receives the frame on nRF52840 P1.01
(`UWB_RX1`, NINA GPIO_35); P1.02 is the wired but currently unused transmit
path.

The 0 Ω series resistors on `UWB_RDY`, `UWB_TX1`, and `UWB_RX1` are available
as logic-analyser test points. They are bring-up ground truth. Field telemetry
retains CRC, dropped/duplicate sweep, unpaired edge/frame, and clock-filter
residual counters; a separate strobe-to-poll standard-deviation gate is not
required because its jitter appears directly in the clock-filter residual.
Per-rank `t_round_us` distributions are normal-capture analysis output, not a
bring-up acceptance gate.

## Frozen sweep facts

The frozen tag uses one broadcast poll for eight anchors. Responders are ranked
at 1,000 us spacing after a 1,200 us response delay; source comments place the
last frame completion near 8.45 ms. The nominal build/runtime schedule is one
10 ms active slot in a 10-slot cycle, hence 100 ms or 10 Hz per tag, but BLE
runtime configuration can change the slot count and period.

The earlier 7.18 ms sweep-spread value in `AGENTS.md` does not match the frozen
source and must not be used as a host model. P2 must measure the sweep-start to
each anchor response offsets and then freeze the model.

## Pairing and loss rules

- A UWB payload is paired to a ready-edge capture by monotonic sweep sequence.
- Edges are ignored until their cadence is plausible and stable.
- Duplicate, missing, or out-of-order sequences are reported; they are never
  silently interpolated.
- UART arrival time may be retained as diagnostics only.
- A logical BLE batch carries one B306 time domain and explicit first/last
  sequence values so the host can detect loss.
