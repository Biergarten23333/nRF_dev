# r7 acceptance — BSF6C53, 2026-08-09

## Flashing (SWD, hand-held)

| press | image | contact attempt | InitTarget | flash | readback |
|---|---|---|---|---|---|
| 1 | r7-prod | 1 | 1.79 ms | 9941 ms | PASS 286720 B, 0 mismatches |
| 2 | r7-val | 4 (2 fallbacks) | 1.99 ms | — | **FAILED** `Failed to preserve target RAM`, nothing written |
| 3 | r7-val | 5 attempts, all blocked | 1.69 ms on #5 | — | blocked by reseat-confirm |
| 4 | r7-val | 1 | 1.74 ms | 9941 ms | PASS 286720 B, 0 mismatches |

Press 4 succeeded on the first attempt after a physical re-seat. Presses 2-3
were the same script, hex and probe. **Contact is the whole variable.**

The gate introduced after press 2 blocked press 3 at a measurement of 1.69 ms,
which is inside the good band — correctly, because the hold had already slipped
four times, and press 2 proved that "the attempt that finally attaches" is not
evidence of a sound hold.

## A acceptance — r7-prod live, witness sane

`V45 present=0 ... armed=1 dog=0 dog_dwell=0 dog_age_ms=0 dog_tick_ms=0`
after an SWD flash (SREQ). **PASS as a negative control**: the witness fields
exist, read zero after a non-watchdog reset, and are not stuck on.

## B acceptance — NOT PASSED, and not the expected failure either

Sequence: r7-val flashed and verified -> `V45 STATUS` shows `armed=1` ->
`V45 HANG rc=0 arm_delay_ms=1000` accepted (fault injection present, so the
val image is confirmed live).

Reading afterwards:

    STATUS ... up_ms=51496 verify=PASS
    V45 present=0 ... armed=1 dog=0 dog_dwell=0 dog_age_ms=0 dog_tick_ms=0
    STALL e=3896 x=3895 rc=0 rcc=0/0/0/0

**The board rebooted** — inferred, not directly observed: `up_ms=51496` against
roughly 105 s elapsed since the flash reset places the boot ~13 s after the
injection, which is the new 12 s dwell plus the 1 s arm delay. The inference is
consistent but it is an inference, and a direct `boot_id` reading would settle
it.

Against the three predicted outcomes:

| predicted | seen |
|---|---|
| `present=1`, banks decode | no |
| `present=0`, `dog=1 dog_dwell=1` | no |
| `present=0`, `dog=0` — detector never triggered | **this one** |

So the board took a **software reset** at almost exactly the dwell, with:
- no corpse (`present=0`),
- not the watchdog (`dog=0`),
- not the v41 stall recovery (`rc=0 rcc=0/0/0/0`).

Two readings fit and they are not equally good news:

1. **The detector fired, captured, rebooted, and the corpse did not survive.**
   This would be a retention regression — the F1 class of defect, the one
   schema 4 and the split `PRE_KERNEL_1` init were supposed to close.
2. **Something else reset the board at coincidentally the same moment.** Less
   likely given the timing lands on the dwell, but not excluded.

`dog=0` is informative either way: it is a *measurement*, not an absence.
Whatever reset this board, RESETREAS did not name the watchdog, so the
watchdog-side changes are not implicated in the failure.

## What settles it

`tools/swd/seg2_rtt_t2.sh` — RTT held across a T2 re-run, ~70 s of contact.
It is the only instrument that names a reboot path, and it is the one thing
that distinguishes reading 1 from reading 2. Probe-gated; needs `PROBE GO`.

Cheaper first step, no probe: read `boot_id` and `reset_reason` from the boot
banner to confirm the reboot happened and name its class. That should be done
before spending a press.

## Status of the three watchdog changes

All three are ON THE BOARD and none is refuted by the above:

- dwell 12 s — the reboot landed at ~13 s after injection, consistent with the
  new dwell being reached;
- capture-time `wdt_feed()` — untested, because no capture is known to have run;
- `.noinit` witness — working, verified by the `dog=0` negative control.
