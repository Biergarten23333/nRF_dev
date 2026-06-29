# Tier 2 — Tag Phase Telemetry (in-slot BLE preemption detection)

Date 2026-06-27. Goal: a **deterministic, fast** signal that identifies the
"random victim" tag in multi-tag @10Hz, replacing the dead-end MSTAT BLE
pkt-rate detector (0/5). See memory `tdma-capacity-ble-phase-beat` and
`a7-tail-responder-window-bug`.

## Root-cause restatement (what we are measuring)

The BLE↔UWB collision is **CPU/interrupt-time contention, not RF**. nRF52832 runs
the BLE peripheral link (SoftDevice Controller, conn interval 7.5 ms) and the UWB
TDMA sweep on ONE MCU. The DW1000 is a separate SPI radio. During the tag's UWB
slot the firmware sits in a tight busy-wait (`ss_twr_init_alt_burst_sweep_once`,
the BROADCAST collector loop at `src/ss_twr_init.c:4878`) polling
`SYS_STATUS_ID` over SPI and reading out each anchor response as it lands. When a
BLE connection event fires, the controller preempts the application for the
duration of the event (~hundreds of µs to >1 ms). The collector stops servicing
the DW1000 → single-buffer overrun / missed re-arm → anchor responses are dropped
even though they were on-air (Listener E confirmed). Unlucky BLE↔slot phase (fixed
per connection) = persistent in-slot collision = that tag is the victim.

## Key insight: measure the symptom where it happens

We do NOT need to hook the BLE controller. The preemption is directly observable
as a **time gap in the collector busy-wait**: between two consecutive spin
iterations the loop normally advances < 1 RTC cycle; a BLE event shows up as a
multi-cycle stall. That single measurement yields BOTH metrics the user asked for:

- **in-slot RX-preempt count** = number of stalls per sweep.
- **BLE-event ↔ UWB-slot offset** = the offset (from RX-window start) of the spin
  iteration where the stall began. This is exactly where in the slot the BLE event
  landed, which is what directed phase correction (Tier 2b) needs.

Clock: Zephyr nRF52 system clock = RTC @ 32768 Hz, so `k_cycle_get_32()` = ~30.5 µs
/cycle. A BLE event (≥ ~150 µs, typically 0.5–2 ms) = 5–65 cycles — far above the
spin-jitter floor. Threshold = 4 cycles (~122 µs) → near-zero false positives.
Window length ≈ 8665 µs (GUARD 1200 + 7×1000 + TAIL 800 − POLL_AIR 335) = 283 cyc.

## Implementation (UWB-side only, zero BLE-stack risk)

`src/ss_twr_init.c`:

1. Feature gate `SS_TWR_INIT_PHASE_TELEMETRY_ENABLE` (default 1) + module statics +
   3 inline helpers placed just before `ss_twr_init_alt_burst_sweep_once`.
   - `ss_twr_init_phase_loop_begin(window_start_cyc)` — reset per-sweep counters.
   - `ss_twr_init_phase_loop_spin(now_cyc)` — charge the gap since the last spin
     sample; if ≥ threshold, count it, track max/total gap and first/worst offset.
   - `phase_skip_next` flag — after a frame-processing iteration, the next gap
     (which includes SPI readout time) is NOT charged as a preemption.
2. In the BROADCAST collector loop (`:4878`): call `loop_begin` once before the
   loop (at `response_window_start_cycles` assignment), `loop_spin` at the top of
   each iteration, and set `phase_skip_next` on the frame/timeout event path.
3. Emit a new per-sweep BLE status line from `ss_twr_init_print_location_if_ready`,
   gated on `preempt > 0 || sweep % 50 == 0` (victim announces itself; clean tags
   stay near silent; periodic heartbeat proves telemetry alive):

   **PLACEMENT GOTCHA (cost ~1h):** the deployed tag builds set
   `APP_TAG_POSITION_OUTPUT_ENABLE=0`. In that config `print_location_if_ready`
   takes the `#if APP_TAG_POSITION_OUTPUT_ENABLE == 0U` fast path (`src/ss_twr_init.c`
   ~3588): it publishes the per-sweep `TR;` record via
   `ss_twr_init_publish_tag_range_summary` and **`return`s** before ever reaching the
   `TS;`/`TagSummary` summary block. That summary block is DEAD CODE in the shipped
   firmware. The phase publish MUST go in the POSITION_OUTPUT==0 early-return block,
   right after the `TR;` publish and before `return;`. (Symptom of wrong placement:
   the `TP;1;` format literal and the publish-only statics `window_cyc`/`first_off`/
   `worst_off` get dead-code-eliminated; verify with
   `objdump -t <elf> | grep ss_twr_init_phase_` — all 9 statics present == live.)

   ```
   TP;1;<sweep>;<slot>;<valid>;<preempt>;<maxgap_us>;<firstoff_us>;<worstoff_us>;<totgap_us>;<win_us>
   ```

   `valid` = valid anchor count this sweep, so "preempt high AND valid low" is the
   smoking-gun signature on one line.

Host side (`scripts/run_recv_tdma_capture.py`): parse `TP;` per tag, and in the
end-of-run summary print per-tag `preempt_rate`, `mean preempt/sweep`,
`mean/worst in-slot offset` → the victim is the tag with a high, sustained
preempt rate (deterministic), unlike MSTAT.

## Tier 2b (next, after 2a proven): closed-loop self-correction

Once the tag can see its own in-slot collision it can hill-climb out of it
deterministically: on sustained `preempt > 0`, request a small BLE conn-param /
slot-phase nudge and re-measure until `preempt` clears. No dice-rolling. Not in
this change — detection must be validated against a live victim first.
