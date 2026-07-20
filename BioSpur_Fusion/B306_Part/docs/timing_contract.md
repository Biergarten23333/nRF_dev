# Timing contract

## Clock ownership

The B306 hardware timer is the only node time base. A timestamp is a wrapping
unsigned timer tick plus a boot/session identity; host software must use modular
subtraction and must not compare ticks across boots.

The timer frequency, width, prescaler, and wrap period are `UNKNOWN` until P1
selects and measures the concrete TIMER instance.

## IMU samples

The target IMU cadence is 200 Hz, one trigger every 5 ms. The timestamp attached
to a sample is the hardware-timer trigger instant, not I2C completion and not a
JY61P internal timestamp. The I2C transaction may complete later, but its result
is paired with the already-captured trigger tick.

P1 must measure missed triggers, read latency, and JY61P-versus-B306 drift in ppm
for 30 minutes. The JY61P I2C address, register map, and 200 Hz configuration are
currently `UNKNOWN`.

## UWB epochs

The intended UWB timestamp is a GPIOTE-to-PPI-to-TIMER capture of a DWM1001C
ready edge at sweep start. Software ISR time and UART arrival time are not valid
primary timestamps.

This intended contract is blocked by two frozen-baseline findings:

1. The frozen DWM1001C firmware has no sweep-ready output.
2. nRF52832 P0.19 is the DW1000 IRQ input and is configured as an input. The
   proposed "GPIO19 ready" assignment therefore conflicts with the running UWB
   design and must be corrected in the schematic/pin map before P2.

No B306 capture pin is assigned until that conflict is resolved.

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
