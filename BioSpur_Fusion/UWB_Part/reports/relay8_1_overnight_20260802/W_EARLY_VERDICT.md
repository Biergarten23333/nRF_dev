# relay8.1 W early verdict

Date: 2026-08-02

State: **W FAIL; endurance capture continues unchanged by standing order.**

The prospectively designated six capture chunks closed after 1,854.007 s of
continuous raw logging (the excess over 1,800 s is the time spent taking the
required five-minute snapshots; the Fusion and listener streams did not
close between chunks). All ten peers remained live.

## Decisive failures

- `BSFC2CC` (slot 10) produced 8,428 UWB records at 4.545455 Hz. Every
  consecutive carried epoch label advanced by 2 (`Δmod16=+2`: 8,427/8,427),
  not by 1. It therefore failed both the no-waiver UWB-rate gate and the
  relay8.1 `Δmod16=+1` fix reading.
- Beacon-window miss fraction was 0.621574 for `BSFC2CC` and approximately
  0.9963 for each of the other nine nodes. The required approximately-zero
  miss reading failed fleet-wide. The nine normal-rate nodes were coasting
  across almost all beacon epochs and only periodically reacquiring.
- The gated B306 ledgers were not zero. Runtime CFG reset the tag-owned sweep
  count, while B306 retained its pre-CFG baseline and counted the resulting
  backward range as `reorder`. Additional gated events included two DK CRC
  latch increments, 16 DK I/O latch increments, two B306 header increments,
  and one duplicate increment.
- A provisional independent-observer match found absolute epoch-label
  agreement of 100% on six nodes, but only 36.6%, 33.3%, 7.4%, and 23.6% on
  `BSF3C79`, `BSF31CC`, `BSFAA61`, and `BSFB165`, respectively. The complete
  all-observer result is deferred until the listener collector closes.

## Passing portions

- All ten IMU streams delivered approximately 200 Hz with zero 16-bit
  sequence discontinuities and zero missing samples.
- All per-node `q_drop_imu`, `q_drop_uwb`, `abort_imu`, and `abort_uwb`
  deltas were zero.
- Nine nodes met the UWB rate threshold. `BSFB165` was the slowest of those
  at 9.062861 Hz, still above 99% of 9.0909 Hz.
- Fusion host decoder errors, malformed records, and disconnects were zero.
- The main beacon had zero inferred delayed-TX start failures; the sub
  remained SLAVED and emitted zero independent TX records.
- `imu_i2c_err` and `imu_hreset` were observed as context, as required, and
  were not used as gates.

## Source attribution fixed before endurance completion

- The tracker advances by a fixed period in the tag-local DW clock and uses
  a -500/+600 us window (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:11-12,80-87,104-127`).
  A miss advances the same local prediction, while broad reacquisition waits
  30 s (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:614-625,773-779,815-833`).
- relay8.1's urgent slot-tail service runs only after the complete sweep
  (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:3709-3718,837-857`),
  against a declared slot-10 tail budget of 1,400 us
  (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:13`). Hardware
  data show that this point remains too late.
- Runtime configuration resets the local public sweep counter to zero
  (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:2814-2828`; public mapping
  at `UWB_Part/relay8_1-workspace/src/include/tag_relay6.h:22-29`). B306 rejects
  a backward jump without rebasing its stored last value
  (`B306_Part/firmware/src/main.c:767-792`), explaining the sustained reorder
  increments without host packet loss.

