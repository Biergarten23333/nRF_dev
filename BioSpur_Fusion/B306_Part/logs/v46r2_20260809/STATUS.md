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

### Hardened qualification rerun

The exact read-only fleet gate initially passed 10/10 consecutive samples in
`../ota_timing_inventory_20260810_104526/`. The guarded reboot run
`../ota_timing_qualification_20260810_104547/` produced two valid samples
(BSF6C53 11.450379 s, BSF8BC4 12.453154 s), four invalid reboot samples, and
stopped before rebooting BSF31CC or the final three boards when the exact fleet
gate did not recover. Invalid and valid evidence was preserved.

After correcting the disconnect/reconnect evidence join, the required
read-only preflight was repeated. Across 21 inventory samples, BSF1120 remained
present, connected and subscribed but never answered PING; the other nine
passed and no unexpected peer was present. Evidence:
`../ota_timing_inventory_20260810_105516/`. The reboot phase was therefore not
entered again.

Exact gate result remains **BLOCKED: 2/10 valid common-clock samples**. Maximum,
P95, complete component maxima and the conservative upper bound are unavailable,
so `upper_bound + max(30 s, 25%) < 180 s` is not proven. No OTA or slot write
occurred.

### BSF1120 localization

Offline re-evaluation refused to salvage BSF3C79, BSFC2CC or BSF44AD because
their raw records lack post-reboot uptime and confirmation witnesses. A
64.269009 s preserved-state capture then showed three BSF1120 Master writes
with `err=0`, zero rejects and zero replies. Both control peers passed all
rounds. BSF1120 also emitted zero UWB, IMU or telemetry records; only
Master-side QoS continued with frozen delivered counters, so `ctrl_rx` was not
observable. Classification remains `INCOMPLETE_EVIDENCE`, narrowed to a
BSF1120-local path after successful Master write submission.

Targeted peer redraw disconnected BSF1120 but it did not advertise/reconnect.
Restarting the unchanged v36 production Master rebuilt nine healthy peers and
scanned for 180 s at `count=9 ready=9`; BSF1120 remained absent. Recovery C now
requires a physical BSF1120 reset/power cycle and immediate retained-state
inspection. No B306 reset, OTA or slot write has yet occurred. Full report:
`../../docs/BSF1120_CONTROL_FAILURE_AUDIT.md`.

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
