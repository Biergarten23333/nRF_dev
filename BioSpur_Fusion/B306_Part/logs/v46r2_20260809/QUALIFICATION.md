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

The observed run establishes a narrower result: for this injection and state,
v41 preempted the guard while its detector was eligible and its budget was
available. It does not prove that every possible first wedge after every power
cycle must be handled by v41. An overnight ledger must still keep `intent=5`
as a separate column because `rcv=0` does not prove an uneventful run.

### What would settle it, cheaply, next session

Re-run `V45 LEAK` now, on this same boot. The stall-recovery budget has been
consumed by the reset just observed, so if the guard is healthy it should fire
on the second injection. That is one command and it distinguishes preemption
from blindness directly. It was not run here because the stated rule is to stop
on a Q0 no-trigger and report.

## Q1, Q2, Q4, Q5 — NOT RUN

Stopped per the rule. `UNKNOWN_SREQ` is nevertheless 0 across everything since
the cold boot, with one reset correctly named.

## Q0 RETRY — THE GUARD FIRED. Arm-1 fix verified on hardware.

Run on BSF6C53's current boot, where the stall-recovery credit was already
spent by the first Q0.

```
V45 GUARD rcv=1 cause=1 frozen_ms=12019 streak=1 max=3 latched=0
          intent=1 unk_sreq=0 named_sreq=2 rr=00000004
```

| field | reading |
|---|---|
| `rcv=1` | the guard triggered |
| `cause=1` | `NOTIFY_FROZEN` — the classic fleet terminal state |
| `frozen_ms=12019` | exactly the 12 s dwell |
| `intent=1` | `BSF_RESET_INTENT_RECOVERY_GUARD` — the reset attributed to the guard itself |
| `named_sreq=2 unk_sreq=0` | both resets on this boot named; none unexplained |
| `streak=1 max=3 latched=0` | one strike used, budget remaining, not locked |
| after | `fw=b306-imu-relay-v46`, delivery nominal, `verify=PASS` |

**The arm-1 disjunction works.** The change that could have blinded the guard to
the parked-worker state does not: it fired on exactly that state, at exactly the
dwell.

**And the preemption hypothesis is confirmed, not assumed.** First Q0: fresh
cold boot, stall-recovery credit available, `intent=5` took it. Second Q0: same
injection, same boot, credit spent, `intent=1` — the guard. Two runs differing
only in which authority had budget.

### §A — the two reset authorities, read from source

| fact | location |
|---|---|
| `intent=5` belongs to the v41 stall recovery | `main.c:1595` |
| its budget | `STALL_MAX_RECOVERIES_PER_POWER = 1u`, `main.c:94` |
| where the counter lives | `retained_stall.recovery_count`, `.noinit`, `main.c:429` (field `main.c:424`) |
| what clears it | a power cycle only. `.noinit` is wiped by POR; there is no software refund |

**Fleet consequence, and it changes how the morning ledger reads:**
`STALL_MAX_RECOVERIES_PER_POWER=1` permits at most one v41 recovery per power
cycle. This experiment proves v41 preempted the guard for this injection and
state when its detector was eligible and budget was available; it does not
prove every possible first wedge after every power cycle is handled by v41.
A board reporting `rcv=0` has not necessarily been trouble-free, so `intent=5`
must remain its own ledger column.

For deployment accounting, keep three distinct facts: payload transferred,
target image observed running, and exact target image durably confirmed.

Corpse from the retry collected and ACKed: seq=1 cause=BOTH_FROZEN, 29 752 B,
crc32 `49e743d8`. Detector re-armed.
