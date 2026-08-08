# STATUS — v45 offline implementation

**Complete.** All §15 gates PASS. Nothing is left in flight and nothing needs
resuming.

| | |
|---|---|
| started from HEAD | `d19538c94ab4bf193177e3f2ce23ce6104187258` (`feature/b306-bringup`) |
| ended at | see the commit created by this task; the branch is pushed, **not deployed** |
| wall time | one working session, 2026-08-08 |
| hardware touched | **none** |

## Progress

| § | item | state |
|---|---|---|
| 2 | hard source audit, 11 items | DONE — 8 PASS, 3 CORRECTED, all resolved |
| 3 | four trace channels + 5-file SDK patch set | DONE |
| 4 | thread + wait-object snapshot | DONE |
| 5 | dual-watermark detector on the system workqueue | DONE |
| 6 | pool/buffer ownership + shadow atomics | DONE |
| 7 | retired/kept counter semantics | DONE (documented, nothing deleted) |
| 8 | ring 200→510, corpse schema 3, five banks | DONE |
| 9 | flash persistence | CODE COMPLETE, **shipped disabled** — zero free flash, see the blocker below |
| 10 | capture sequence | DONE |
| 11 | collection: node opcodes + host script + runbook | DONE, master untouched |
| 12 | tests | DONE — 16 suites, all PASS |
| 13 | frozen configuration | DONE, contract-tested |
| 14 | patch manager | DONE — `selftest` runs the full round trip |
| 15 | build gates + deliverables | DONE |

## Deliverables in this directory

`CONTEXT_AUDIT.md` · `CONTEXT_AUDIT.json` · `V45_DESIGN.md` ·
`V45_OFFLINE_REPORT.md` · `HARDWARE_STAGE_PLAN.md` · `DECISIONS.md` ·
`STATUS.md` · `EVIDENCE_SHA256.txt`

## Source, tests and tools produced

```
firmware/src/bsf_v45_trace.h        four channels, the marker, pool/conn/waitobj types
firmware/src/bsf_v45_detector.h     pure policy: arm, dual watermark, recovery, jitter
firmware/src/bsf_v45_corpse.h       wire contract: CORE, banks, flash container, schema 3
firmware/src/bsf_v45.h              runtime interface to the application
firmware/src/bsf_v45.c              storage, capture, persistence, export, the monitor
firmware/src/bsf_v45_pools.c        pool snapshot, ownership, law-4 hook, fault injection
firmware/Kconfig                    BSF_V45_TRACE / BSF_V45_FAULT_INJECT, default n
firmware/pm_static_v45_corpse.yml   the optional 16 KiB carve (NOT the default)
firmware/patches/sdk_patch.sh       5 files, 2 roots, apply/verify/revert/selftest
firmware/patches/ncs-v2.8.0-bsf-v45-instrumentation.patch
firmware/tests/test_bsf_v45_detector.c + runner
firmware/tests/test_v45_source_contract.py
firmware/tests/test_bsf_v45_decoder.py
firmware/tests/test_v45_partition_overlap.py
tools/bsf_v45_corpse_decode.py      decoder, incl. v43/v44 and the decision table
tools/v45_corpse_collect.py         host-side retrieval, verify-then-ACK
```

Modified: `firmware/CMakeLists.txt`, `VERSION`, `prj.conf`, `src/main.c`,
`src/stall_ring_policy.h`, `tests/test_stall_ring_policy.c`,
`tests/test_bt_stage_contract.py`.

## Builds

| build | purpose | FLASH | RAM |
|---|---|---|---|
| `b306-imu-relay-v45-a` | deployment candidate | 46.47 % | 52.88 % |
| `b306-imu-relay-v45-b` | reproducibility check — unsigned app + MCUboot byte-identical to `-a` | 46.47 % | 52.88 % |
| `b306-imu-relay-v45-flash` | proves the §9 code and the partition overlay compile and link. **Not a deployment candidate.** | 47.38 % | 56.01 % |

## The one thing that is not deployable

`BSF_CORPSE_FLASH_ENABLED=0`. `pm_static.yml` has **zero free bytes**, and the
only clean carve needs MCUboot rebuilt and SWD-reflashed on all ten boards, which
the OTA-only Stage C cannot do. Full reasoning in `CONTEXT_AUDIT.md` item 11 and
`V45_OFFLINE_REPORT.md` §6.

## How to resume, if anything needs redoing

```bash
# SDK patch state (five files, two roots)
B306_Part/firmware/patches/sdk_patch.sh status      # -> patched
B306_Part/firmware/patches/sdk_patch.sh selftest    # full round trip

# builds
B306_Part/tools/build_firmware.sh b306-imu-relay-v45-a

# every test
cd B306_Part/firmware/tests
for s in run_*.sh; do bash "$s"; done
for p in test_*.py; do python3 "$p"; done
```

The SDK is left in the **patched** state, which is what the build gate requires.

## Next action

Stage B, on one canary, per `HARDWARE_STAGE_PLAN.md`. Nothing in this task
touched hardware and nothing here authorises deployment.
