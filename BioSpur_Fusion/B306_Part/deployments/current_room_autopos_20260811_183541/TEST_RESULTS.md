# Test results

- Python compilation: PASS for the AutoPos wrapper, V4-io qualifier, and pure
  UWB replay.
- Current-room AutoPos/identity/slot/delay/V4-io/T4/U5 tests: 7 passed.
- Related B306 host/tool tests: 198 passed, 17 subtests passed, 1 deliberately
  deselected stale assertion.
- Fusion Master standalone contracts: host-output non-blocking PASS, spacing
  derivation PASS, stall-read lifecycle PASS (8 terminal paths).
- Two complete positive-layout position replays produced byte-identical
  deterministic CSV/JSON hashes, including both 149,999-row outputs.
- Raw capture SHA-256 before and after analysis remained
  `c5c7c923e2e29ad43d2d5e51217dda0ea1df8f95bdc04d30656f8055b038a9b8`.

The one stale test is
`test_dk_v31_specific_reader_is_bounded_and_independent`: it requires the
literal deployed marker `dk-fusion-imu-relay-v31`, while the current frozen
source intentionally declares `dk-fusion-imu-relay-v36` (and a conditional
CCC reproduction marker). This pre-existing marker expectation is unrelated
to AutoPos/geometry/positioning and was not changed in this task.

