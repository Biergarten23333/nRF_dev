# Timing contract

## Clock ownership

The B306 hardware timer is the only node time base. The Stage 2 implementation
uses TIMER2 at a nominal 1 MHz in 32-bit mode and extends it to a 64-bit
microsecond count in software. A compare event at zero accounts for each
natural `2^32`-tick wrap; it deliberately does not clear the timer at
`UINT32_MAX`, which would shorten every epoch by one microsecond. TIMER0 belongs
to MPSL. Both GPIOTE channels and both PPI channels are allocated dynamically.

The 64-bit count is local to one boot/session. Host software must not compare
timestamps across boots. More importantly, the intended software extension is
not currently functional at the first 32-bit wrap: the 2026-07-21 extended run
stopped producing both UWB records and one-Hz telemetry at `2^32 us` of B306
uptime (71.58 minutes). The exact evidence is in
`UWB_Part/logs/absdeadline_1h_20260721_205638/analysis/1h-summary.md`.

Until the firmware debt is fixed and validated across multiple wraps, the
operating rule is: **power-cycle the B306 before every capture session.** A
current capture session must be no longer than 60 minutes. A button reset is
not the prescribed mitigation; remove and restore B306 power so TIMER2 uptime
is known to begin near zero. Confirm the first `strobe_us` / `node_ms` values
are near zero in preflight. See `capture_operations.md`.

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

The target IMU cadence is 200 Hz. Because this board has no JY61P data-ready
wire, the current continuous-pull implementation stamps the TIMER2 value
immediately before arming the blocking TWIM read, not at I2C completion and not
from the JY61P clock. The accepted `0x34`/26-byte transaction at 400 kHz takes
788 us on this board.

The 2026-07-25 C-R two-speed regression measured
`latency = 203 + 22.5*N` us at 400 kHz and
`latency = 464 + 90*N` us at 100 kHz, both with R² = 1 and zero residual at all
six tested lengths. A joint wire-time model gives 585 us returned-data time,
approximately 87 us addressing/control time, and approximately 116 us fixed
software time for the production transaction. The old approximately 650 us
figure was an unmeasured incomplete estimate and is superseded. A byte-identical
future image must reproduce 788 us within both 100 us and 15%; the effective
gate is therefore ±100 us.

Software cannot split the 116 us fixed term into pre-SCL TWIM/DMA setup and
post-SCL completion/return. The true correction relative to the first SCL edge
therefore remains bounded within `[0, 116] us`; an electrical trace would be
required to place it more precisely. This is 2.32% of the sensor's dominant
0–5 ms asynchronous refresh phase.

Within the burst, same-axis gyro data begins six returned bytes after the
corresponding accelerometer data, a measured 135 us wire offset at 400 kHz.
That is 0.0486 degrees even at 360 degrees/s. Whether this JY61P shadows the
whole register block at burst start is unknown; the available WT61P-family
documentation is not authority for this fitted part.

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
4. The deployed `tag-fusion-link-v2-polltxmargin3ms1` Task A image configures
   P0.26 low at initialization and emits one nominal 10 us pulse in the
   broadcast-poll TX-done path.
5. The confirmed B306 capture input is nRF52840 P1.03 (`UWB_RDY`, NINA GPIO_37).
6. B306 configures P1.03 as a no-pull input. Separate dynamically allocated
   GPIOTE channels capture rising and falling edges through PPI into TIMER2 CC1
   and CC2. Software sees the captured CC values; it does not read the timer in
   place of the hardware timestamp.

The accompanying UART v2 frame carries the same sweep counter, raw 40-bit
DW1000 poll-TX timestamp, and measured response-RX-minus-poll-TX
`t_round_us[]`. UART arrival remains diagnostic; the captured P0.26 edge is
the B306 time-domain observation. B306 receives the frame on nRF52840 P1.01
(`UWB_RX1`, NINA GPIO_35); P1.02 is the wired but currently unused transmit
path.

The 0 Ω series resistors on `UWB_RDY`, `UWB_TX1`, and `UWB_RX1` are available
as logic-analyser test points. They are bring-up ground truth. Field telemetry
retains CRC, dropped/duplicate sweep, and unpaired edge/frame counters.
Per-rank `t_round_us` distributions are normal-capture analysis output, not a
bring-up acceptance gate.

## READY polarity and Stage 2 measurement

The 2026-07-21 parallel B306/DSView run settles the polarity conflict:

- inactive level: **low**;
- pulse: **active high**;
- B306 boot sample: low, with no input pull (`capture_flags=0x0e`);
- DSView width over the formal 300 s window: 10.6 us minimum, 10.8 us median,
  10.8 us p99 and maximum;
- B306 captured 2,907 rising and 2,907 falling edges in the same window.

The low idle level therefore comes from the DWM1001C firmware's output-low
initialization, not a B306 pull resistor. B306 retains both-edge capture so a
future polarity regression is visible rather than silently absorbed.

Pairing uses the closest preceding pulse to the expected 10,583 us frame delay,
within a hard 20,000 us window. The formal run observed exactly one candidate
for every frame. Frame-minus-strobe delay was 14,412 us minimum, 14,669 us
median, 17,078 us p99 and 17,284 us maximum; zero pairs were within 1 ms of
either window edge. This attribution path does not use a clock filter.

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

- A UWB payload is paired to the closest preceding ready pulse within 50 ms.
  The frame-side timestamp is captured in the UART `RX_RDY` callback and
  carried with the DMA bytes, so parser-thread scheduling cannot change it.
  The sweep counter is retained for continuity checks and reporting.
- Edges during the first 50 ms after capture initialization are discarded;
  later edges must form a valid dual-edge pulse and satisfy the frame-pairing
  window. Cadence plausibility is reported by the host analysis, not silently
  filtered in B306 firmware.
- Duplicate, missing, or out-of-order sequences are reported; they are never
  silently interpolated.
- UART arrival time participates only in strobe/frame association and
  transport-delay diagnostics. It is not the UWB measurement epoch; the
  hardware-captured ready edge remains that authority.
- A logical BLE batch carries one B306 time domain and explicit first/last
  sequence values so the host can detect loss.

The 300 s Stage 2/4b run paired all 2,907 records as healthy, with zero
one-sided orphans, zero edge-queue drops, zero sweep-counter gaps, and zero
DSView/B306 cadence disagreements. DSView nevertheless found 93 absent nominal
10 Hz slots (3.10%) while tag `pollfail=0` and `polllast=0`. That is an upstream
scheduler-generation finding, not a B306 capture or UART loss. Do not relabel
those absent slots as `pollfail` without a new tag-side missed-deadline counter.
