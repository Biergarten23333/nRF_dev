# v46r2 status — 2026-08-09

**Part 1 COMPLETE. Part 2 BLOCKED at preflight; no board state changed.**

BSF6C53: on `b306-v46-val` (NOT v46r2), healthy, guard armed, probe released.
Nine other boards: v44, zero recovery, powered down.

## Part 1 — done

| item | state |
|---|---|
| 1.1 arm A + `notify_attempt` | done |
| 1.2 arm B clearing (send / disconnect / epoch) | done |
| 1.3 guard general requirements | done |
| 1.4 reset attribution + `UNKNOWN_SREQ` | done; contract proven FAIL-then-PASS |
| 1.5 `hci_rx_pool` injection (`V45 RXPOOL`) | done, untested on hardware |
| 1.6 builds + gates | done: 20/20, byte-identical, delta 1 line |

FLASH 47.16% (+0.08% vs v46), RAM 53.47% (unchanged).

## Open

1. **Part 2 blocked** — OTA tool's `--master-marker`/`--restore-build` defaults
   are v32-era; rig runs v36. Needs the correct restore image, not a guess.
2. **C4 DFU self-check still unanswered** for BSF6C53.
3. **B1 still has no hardware test.** `V45 RXPOOL` exists to provide one but has
   not been run.
4. **The shared-SDK `#error` constraint** now blocks other projects building
   against `~/ncs/v2.8.0` unless the patch is reverted first.
5. BSF6C53 currently holds an uncollected v45 corpse (`present=1 cause=3`), so
   its v45 detector is blind (`armed=0`, 51 min). The recovery guard is
   unaffected and armed.
