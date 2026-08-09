# Qualification — STOPPED AT Q0

## 3.0 Cold boot — VERIFIED, all four witnesses

| witness | reading |
|---|---|
| reset reason | `rr=00000000` — power-on, not SREQ (`0x04`) |
| uptime | 64.6 s, fresh |
| `.noinit` | `RING boot=1 init=cold` — cold init, boot counter reset to 1 |
| budget | `present=0`, guard `rcv=0 streak=0 max=3 latched=0`, `unk_sreq=0` |

`unk_sreq` went 1 -> 0 across the dock. That counter lives in `.noinit`, so only
a true power removal clears it: the cold boot is confirmed by the mechanism
itself, not just asserted.

## Q0 — `V45 LEAK` replay: THE GUARD DID NOT FIRE

```
V45 GUARD rcv=0 cause=0 frozen_ms=0 streak=0 max=3 latched=0
          intent=5 unk_sreq=0 named_sreq=1 rr=00000004
```

- **`rcv=0`** — the recovery guard did not trigger.
- **`intent=5` = `BSF_RESET_INTENT_STALL_RECOVERY`** — the board WAS reset, by
  the older v41 stall recovery.
- `named_sreq=1`, `unk_sreq=0` — the reset was correctly attributed. §1.3 works.
- The board recovered: healthy, `fw=b306-imu-relay-v46`, delivery nominal.

**Per the stated rule this stops the session, and it does.** But the result must
not be over-read in either direction.

### What is NOT established

"The guard did not fire" and "the guard is blind" are different claims, and this
run cannot separate them. The board never stayed wedged long enough for the
guard's 12 s dwell, because a different, legitimate mechanism reset it first.
A mechanism that is preempted has not been shown to be broken.

### The likely explanation, and why it matters more than Q0

On the **v46** build, before the arm-1 change, this same injection fired the
guard: `rcv=1 rcv_cause=1`. On v46r2 it did not. The arm-1 disjunction is
strictly weaker as a precondition -- it can only make the guard fire in MORE
states, never fewer -- so it is an implausible cause of the guard firing less.

What else changed is the **cold boot**. `retained_stall` was wiped, so the v41
stall recovery's per-power-cycle budget was FRESH and available. In the earlier
v46 run the board had been up for a long time with that budget likely already
spent, leaving the guard as the only responder.

If that is right, the fleet consequence is significant and is not about the
guard at all: **after a power cycle, the first wedge on any board is handled by
v41 stall recovery, not by the v46 guard.** The guard is the second line. That
changes what an overnight run's counters will mean -- a board that recovers may
show `rcv=0` and still have recovered correctly.

### What would settle it, cheaply, next session

Re-run `V45 LEAK` now, on this same boot. The stall-recovery budget has been
consumed by the reset just observed, so if the guard is healthy it should fire
on the second injection. That is one command and it distinguishes preemption
from blindness directly. It was not run here because the stated rule is to stop
on a Q0 no-trigger and report.

## Q1, Q2, Q4, Q5 — NOT RUN

Stopped per the rule. `UNKNOWN_SREQ` is nevertheless 0 across everything since
the cold boot, with one reset correctly named.
