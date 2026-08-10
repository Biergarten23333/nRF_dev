# v46r2 status — fleet OTA blocked 2026-08-10

## P0 confirmation-pipeline closure

The host-side P0 changes are implemented and tested: PREPARE/BUILD/FINALIZE
identity, embedded FWID plus active MCUboot image-hash readback, the complete
seven-state classifier, one inherited absolute deadline, post-confirm spacing,
independent rescue confirmation, and a genuinely fresh final fleet verifier.
No OTA or B306 MCUboot-slot write was performed.

The required ten-board reboot-only timing gate did not run because the
production Master repeatedly reported `count=0 ready=0`. Both attempts stopped
before T0; no board received `REBOOT`. Exact gate result is **BLOCKED: 0/10
common-clock samples, so max/P95/component maxima and a conservative bound are
unavailable and the strict `<180 s` predicate is unproven.** Evidence is in
`../ota_timing_qualification_20260810_103454/` and
`../ota_timing_qualification_20260810_103518/`. Fleet OTA remains prohibited.

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

`STALL_MAX_RECOVERIES_PER_POWER=1` means at most one v41 recovery per power
cycle. This experiment shows v41 preempted the guard for this injection and
state while its detector was eligible and budget was available. It does not
show that every possible first wedge after every power cycle must be handled by
v41. A board reporting `rcv=0` has not necessarily been trouble-free, and
`intent=5` remains a separate fleet-ledger column.

Deployment evidence must distinguish payload transferred, target image
observed running, and exact target image durably confirmed.

## NOT done

- **B1 (the Nordic `K_FOREVER` backport) has NEVER been tested on hardware.**
  The `hci_rx_pool` injection (`V45 RXPOOL`) exists but was not run.
- Fleet OTA incomplete: 6 boards still on v44.
- Overnight run not started. No short test.
- Q1, Q2, Q4 not run.

## Blocking next session

1. Power/connect all ten B306 boards and rerun the reboot-only timing
   qualification. Require ten raw T0--T4 samples and
   `upper_bound + max(30 s, 25%) < 180 s`.
2. Only after that strict PASS may a separately authorized fleet OTA begin;
   durable success is fresh node + FWID + active image SHA + `confirmed=1`,
   never transaction `rc`.
3. Then B1's first hardware test.
