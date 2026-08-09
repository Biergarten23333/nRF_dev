# v46r2 status — session aborted 2026-08-10

## Achieved

- **BSF6C53 on `b306-imu-relay-v46` (v46r2), confirmed, healthy, guard armed,
  budget full, `unk_sreq=0`.** Cold boot verified on four witnesses.
- **Q0 retry PASSED — the guard fired in isolation**:
  `rcv=1 cause=1 frozen_ms=12019 intent=1`. The arm-1 disjunction is verified
  on hardware; the change that could have blinded the guard does not.
- **C4/DFU answered**: BSF6C53's DFU path is intact after five SWD flashes.
- **Reset attribution works**: every reset this session was named
  (`named_sreq`), `unk_sreq=0` after the cold boot. The one pre-registered
  prediction (DFU raises `unk_sreq` by exactly one) held.
- Four of ten boards on v46r2; six not.

## Two reset authorities (read from source, §A)

| | |
|---|---|
| `intent=5` v41 stall recovery | `main.c:1595`, budget `STALL_MAX_RECOVERIES_PER_POWER = 1u` (`main.c:94`), counter `retained_stall.recovery_count` in `.noinit` (`main.c:429`), cleared only by power cycle |
| `intent=1` v46 guard | three strikes, cleared by 30 min healthy |

**The first wedge after any power cycle is taken by stall recovery, not the
guard.** A board reporting `rcv=0` has not necessarily been trouble-free.
`intent=5` needs its own ledger column.

## NOT done

- **B1 (the Nordic `K_FOREVER` backport) has NEVER been tested on hardware.**
  The `hci_rx_pool` injection (`V45 RXPOOL`) exists but was not run.
- Fleet OTA incomplete: 6 boards still on v44.
- Overnight run not started. No short test.
- Q1, Q2, Q4 not run.

## Blocking next session

1. Fix the one-shot marker check in `confirm_b306_v32.py` (poll, don't sample).
2. Re-run the fleet OTA; verify by content check only, never by `rc`.
3. Then B1's first hardware test.
