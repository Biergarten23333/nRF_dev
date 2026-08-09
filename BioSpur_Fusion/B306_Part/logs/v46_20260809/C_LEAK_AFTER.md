# Phase C2 — the "after" half. BSF6C53 on b306-v46-val

Same board, same `V45 LEAK` injection, same master, ~2 hours after Phase A.

## Before / after, invariant by invariant

| # | invariant | A: r7-val (unfixed K_FOREVER) | C2: v46-val |
|---|---|---|---|
| — | export after the leak | **STOPPED within < 1 s and never resumed** | **CONTINUED — 118 complete samples over 117 s** |
| 1 | UWB + IMU export | both ceased together | both nominal: frames 8.33/s, imu 20.00/s |
| — | notify_ok | frozen at 31.3/s then dead | **31.32/s sustained** (baseline 31.33) |
| 2 | link layer | alive, delivery dead | alive, delivery alive |
| 3 | commands answered | none, ever | all answered |
| 5 | watchdog | fed, no reset | fed 1.00/s |
| 8 | latch | yes — abrupt, permanent | **no latch** |

**The deadlock is gone.** On the unfixed build this injection killed delivery in
under a second and it never came back without a power cycle. On v46 the same
command left every rate at baseline for the full 117 s observation.

## BUT C2 IS NOT A CLEAN BEFORE/AFTER, AND MUST NOT BE READ AS ONE

**The board rebooted at the injection.** Uptime went 94.8 s -> 5.6 s across the
`V45 LEAK` command. The 117 s of perfect delivery is from the boot AFTER that
reset, not from the board that received the injection.

The reset is **unattributed**:

| candidate | reading | verdict |
|---|---|---|
| recovery guard | `rcv=0`, `rcv_cause=0` | did NOT fire |
| watchdog | `dog=0` | not the watchdog |
| v45 detector | `present=0`, no corpse | did not capture |
| reset reason | `rr=4` on both sides | a SOFTWARE reset (SREQ) |

So something executed a software reset and no mechanism we instrument claims
it. `CONFIG_RESET_ON_FATAL_ERROR` is not set, so a Zephyr fatal error is not
the obvious explanation either.

**Two readings, and the evidence does not separate them:**

1. The reboot happened just before the injection landed, and the leak was then
   absorbed cleanly by a fresh board. C2 would be a genuine pass.
2. The injection caused a fault that reset the board, and the 117 s of health
   is simply a board that no longer has a held buffer. C2 would be a pass for
   "no permanent wedge" and a **new defect** for "v46 faults on this input".

Reading 2 would be a regression introduced by v46 and cannot be ruled out from
this data. **C2 is recorded as INCONCLUSIVE.**

## What settles it

Re-run C2 with RTT attached (`tools/swd/seg2_rtt_t2.sh`, probe-gated, ~70 s).
The host log names its own reset paths; that is exactly the instrument that
turned the 2026-08-09 "unexplained software reset" into a watchdog in one read.
Without it this is guesswork.

A cheaper first step, no probe: re-run the injection and watch whether the
reboot reproduces. One reset could be coincidence; two is a mechanism.

## C2 RE-RUN — the question is answered, and the answer changes the reading

Re-ran the identical injection with no probe. Result:

- **The reboot reproduced**: uptime 271.7 s -> 4.6 s across the command.
- **`rcv=1 rcv_cause=1`** — cause 1 is `BSF_RECOVERY_CAUSE_NOTIFY_FROZEN`.
  **The recovery guard fired, reset the board, and the node came back and
  resumed** (94 telemetry samples after).

So reading 1 above is wrong and reading 2 is closer, but neither was right:

**`V45 LEAK` still freezes delivery on v46.** The B1 backport did not prevent
it. That is not a surprise once stated precisely: the injection holds the
SINGLETON `sync_evt_pool` buffer, which is a different starvation from the
`hci_rx_pool` exhaustion that `K_FOREVER` turned into a deadlock. B1 removes
the RX-pool deadlock; it was never going to remove a held sync_evt_pool.

**What v46 changed is the outcome, not the freeze.** Before: freeze -> permanent
wedge, only a power cycle recovers, 5 h 27 min observed in the field. After:
freeze -> guard detects frozen delivery within 12 s -> cold reset -> node
rejoins and delivers at nominal rate.

## Honest scoring

| claim | status |
|---|---|
| the recovery guard works on hardware | **PROVEN** — `rcv=1 rcv_cause=1`, reset taken, node resumed |
| v46 removes the `V45 LEAK` wedge | **NO** — the freeze still happens; recovery is what changed |
| B1 removes the `K_FOREVER` RX deadlock | **not demonstrated by this test** — the injection does not exercise that path |
| the first C2 reset (rcv=0) | **still unattributed** — the guard had not fired then |

The first C2 reset remains unexplained and should not be quietly folded into
the second. One reset with `rcv=0` and one with `rcv=1` are different events.

**B1 is therefore verified only by source contract and by the upstream commit,
not by a hardware before/after.** An honest test of B1 needs an injection that
exhausts `hci_rx_pool` specifically. `V45 LEAK` is not that injection, and
Phase A's "before" half measured the same wrong thing — its phenotype match to
the fleet wedges stands, but it was never a K_FOREVER test.
