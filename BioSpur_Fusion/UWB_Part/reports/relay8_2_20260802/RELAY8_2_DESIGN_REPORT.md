# relay8.2 diagnosis and offline build report

The verified log migration recovered **63,047,356,416 bytes** of SSD free space.
D1 cannot provide a direct phase-slope fit from the stored telemetry; the honest boundary-conditioned estimate for the nine normal-cadence nodes is **16.36–19.91 ppm magnitude**, while BSFC2CC is a distinct slot-10 regime.
D3 landed **outside relay8.2 scope**: on BSF1120 and BSF3C79, UWB and IMU stopped together while BLE remained connected, identifying a B306/application data-plane stall rather than a tag beacon-state dead end.

Date: 2026-08-02  
Execution: offline only; no serial, J-Link, radio, OTA, reset, or hardware access  
Result: **P0 PASS / P1 PASS WITH 3 PRESERVED EXCEPTIONS / D COMPLETE TO OBSERVABILITY LIMIT / relay8.2 BUILD PASS**

## P0 — repository safety, commits, and push

### Initial untracked inventory

The 1,537 untracked-path snapshot was classified before staging. No bulk `git add` was used.

| Group | Paths | Disposition |
|---|---:|---|
| `UWB_Part/` source lineages, beacon workspaces, handovers, reports and tools | 1,131 | Selected only relay8.1 canonical source, relevant Fusion-link source/tooling, and small reports; build outputs and raw logs excluded |
| `54L15/` builds, logs, firmware, tools, docs and tests | 232 | Selected genuine source/tooling needed by the recorded work; generated builds/logs excluded |
| `B306_Part/` tools, host, firmware, docs, artifacts and handovers | 169 | Selected genuine source, tests and small documentation; generated artifacts/raw evidence excluded |
| Root/editor/miscellaneous small paths | 5 | Reviewed individually; no editor state or unrelated output was staged |

The amended large-file gate was applied only to files added or staged by these commits within `BioSpur_Fusion`: **zero newly staged files exceeded 10 MiB, PASS**. The pre-existing inventory of **189 files / approximately 5.51 GB** is historical debt only. Removing it requires a separately authorized filter-repo/BFG history rewrite, force-push and LFS cleanup; none was attempted here.

### Commits

| Commit | Subject | Scope |
|---|---|---|
| `addbcf86b0a25209ca443eded989b6b342b44f90` | `fusion: record multi-node relay pipeline` | 202 files; B306/DK source, host tools/tests, aligner v1/v2, reports |
| `a03b752610a08501e965b4c07ef43e7b3bc07110` | `uwb: archive relay8.1 tag and carrier lineage` | 257 files; canonical relay8.1 source/carrier lineage and small reports |

The remote is `origin = https://github.com/Biergarten23333/nRF_dev.git`. Before push, the branch was 151 commits ahead of `main`; the incremental object estimate was approximately 4.8 MB with **zero LFS objects to upload**. The push completed in about 7 seconds and `origin/feature/b306-bringup` reached `a03b75261`. The GitHub LFS endpoint is `https://github.com/Biergarten23333/nRF_dev.git/info/lfs`; the account's server-side quota is not exposed by local Git configuration and is therefore **UNKNOWN**.

### `.git` placement conflict — documented debt

The repository actually in use has a normal directory at `/mnt/nrf_ssd/nRF_dev/.git`, contrary to the HDD-pointer state described in the agent guide for the other working-tree path. Current exact sizes are:

| Item | Bytes | Approximate binary size |
|---|---:|---:|
| `.git` total | 62,716,582,616 | 58.41 GiB |
| `.git/lfs` | 33,455,014,425 | 31.16 GiB |
| `.git/objects` | 29,215,882,532 | 27.21 GiB |

`git count-objects` also reports 204.38 MiB of temporary pack garbage. It was not pruned. No relocation or cleanup was performed.

A safe separately authorized relocation procedure is: stop every Git process; verify the HDD mount and space; run `git fsck --full` and `git lfs fsck`; copy (never initially move) `.git` to a new HDD staging directory with metadata preserved; compare refs/config plus hashes of every pack and LFS object; run `git status`, `git log`, `git fsck` and `git lfs fsck` against the staged gitdir and existing worktree; retain the SSD directory as a rollback copy; only then atomically replace the worktree's `.git` directory with a pointer file and set `core.worktree`; re-run all checks; delete the rollback copy only in a later, explicit cleanup. This is a dangerous standalone operation and is not part of this batch.

## P1 — verified HDD migration

Evidence: `UWB_Part/logs/MIGRATION_20260802.md` and `/mnt/DatenBankHDD/BioSpur_Archive/ARCHIVE_INDEX.md`.

| Item | Result |
|---|---|
| Destination | `/mnt/DatenBankHDD/BioSpur_Archive/` only |
| Filesystem | ext4; no FAT32 4 GiB limit |
| Whole batch directories migrated | 100 |
| Source bytes archived | 63,022,752,634 |
| Verification | Per-file source/destination SHA-256; zero mismatches |
| SSD available before → after | 120,961,757,184 → 184,009,113,600 bytes |
| SSD bytes recovered | 63,047,356,416 |
| Final links | 111 archive symlinks resolve; 100 created by this migration |

The largest migrated batch was `UWB_Part/logs/relay8_1_20260801` at 37,676,595,608 bytes; its largest file was 10,227,564,023 bytes. Three pre-existing hybrid/partial archives were not merged or overwritten: `batchE3_20260729`, `coldstart_20260730`, and `dkv26_leds_20260730` (65,021,164 local bytes total). The active `relay8_2_20260802` evidence was excluded. The HDD must be mounted for archived paths to resolve.

`B306_Part/tools/archive_batch.sh` now accepts either `UWB_Part` or `B306_Part`, keeps the destination fail-closed below `BioSpur_Archive/`, verifies before source cleanup, and handles historical read-only source trees only after the destination is proven.

## D — overnight diagnosis

### Evidence identity

| Evidence | SHA-256 |
|---|---|
| `.../capture/fusion_cdc.log` | `460a3ea3d3f7646c8360b55a6d06ae48f41fec553d79d0bfc55bd7f5e21121ee` |
| `.../listeners/760184753.jsonl` | `1f4071445e9843dcc8fdb18c83173a4edf91384b44a01d5ef8028356b4cbffe4` |
| `UWB_Part/logs/relay8_2_20260802/DIAGNOSIS.json` | `b6991ca76731d6ac0ebfb830b6b4076c7e89b0fc8a43c4b94955bf15565d4231` |
| `UWB_Part/logs/relay8_2_20260802/PROMPT.md` | `61971c0ac1c56168e3f6683f156b2f5ce839a61414273d5da63ce2061239b42b` |

The analysis is reproducible with `B306_Part/tools/analyze_relay8_2_diagnosis.py`. D1's cumulative-delta method and explicit observability boundary are at lines 57–123; D2's listener-domain timing extraction and limitations are at lines 126–214; D3's independent UWB/IMU stream and link-event audit is at lines 217–323.

### D1 — beacon tracking

`BEACON_STATUS` was stored as sparse cumulative snapshots. It contains `rx` and `miss` totals, but no per-reception timestamp, broad-versus-narrow classification, predicted origin, or phase error. Consequently, the exact inter-reception distribution, broad-reacquisition count, phase-error trajectory, drift sign, and a direct ppm slope fit are **NOT RESOLVABLE OFFLINE**. Listener beacon origins are in the listener's independent DW clock and cannot supply the missing tag-local predicted origin.

The defensible quantity is the interval-average valid-reception gap between status snapshots:

| Node | Observed valid RX delta | Miss delta | Median interval-average valid gap | Median miss fraction | Conditional drift magnitude from 500–600 µs boundary |
|---|---:|---:|---:|---:|---:|
| BSF1120 | 243 | 66,301 | 30.560 s | 99.639% | 16.36–19.63 ppm |
| BSF31CC | 512 | 139,020 | 30.560 s | 99.638% | 16.36–19.63 ppm |
| BSF3C79 | 65 | 17,499 | 30.426 s | 99.623% | 16.43–19.72 ppm |
| BSF44AD | 830 | 226,579 | 30.143 s | 99.635% | 16.59–19.91 ppm |
| BSF6C53 | 119 | 32,292 | 30.129 s | 99.632% | 16.60–19.91 ppm |
| BSF8BC4 | 867 | 236,493 | 30.436 s | 99.635% | 16.43–19.71 ppm |
| BSFAA61 | 870 | 236,631 | 30.143 s | 99.635% | 16.59–19.91 ppm |
| BSFB165 | 884 | 240,393 | 30.141 s | 99.635% | 16.59–19.91 ppm |
| BSFEC35 | 314 | 85,571 | 30.436 s | 99.638% | 16.43–19.71 ppm |
| BSFC2CC (slot 10) | 96,536 | 159,849 | 0.293 s | 62.351% | not boundary-limited; no ppm inference |

The 16.36–19.91 ppm values are `500 or 600 µs / median gap`; they are conditional consistency bounds, **not a measured linear fit**. They match the 30 s timeout scale but do not prove that the unseen phase error is linear. With no evidence supporting a new timeout, the 30 s broad-reacquisition timeout remains unchanged.

### D2 — slot-10 tail

The BSFC2CC-to-on-air mapping is `0xB102` (the mapping is also recorded in `B306_Part/docs/TIME_ALIGNMENT_DRY_RUN_V2.md:161`). One observer supplied 4,652,528 decoded records, 249,409 relevant tag transactions, and 27,061 beacon epochs with a preceding observed transaction.

| Quantity | n | min | p50 | p90 | p99 | max |
|---|---:|---:|---:|---:|---:|---:|
| Expected slot-tail cluster, last observed transaction → next origin | 26,537 | 3,886.3 µs | 4,921.5 µs | 4,945.3 µs | 4,965.9 µs | 6,216.7 µs |
| Software budget to the −500 µs window start | 26,537 | 3,386.3 µs | 4,421.5 µs | 4,445.3 µs | 4,465.9 µs | 5,716.7 µs |
| Cross-phase cluster, excluded from CPU budget | 524 | 107,394.5 µs | 107,742.1 µs | 107,765.7 µs | 107,789.8 µs | 107,800.8 µs |
| Poll → next origin (rare complete observations) | 25 | 5,679.9 µs | 6,027.1 µs | 6,173.6 µs | 6,208.6 µs | 6,216.7 µs |

This bounds, but does not measure, tag software time. A passive observer may miss the real final response, so “last observed transaction” can precede the tag's actual final transaction. The logs contain neither a tag CPU timestamp at RX-arm nor a per-epoch accepted/missed marker. Therefore actual software time required and caught-versus-missed epoch partition are **NOT RESOLVABLE OFFLINE**. F2 removes that unobservable software-arrival dependency instead of tuning against a guessed number.

### D3 — connected but silent

BSF1120's UWB stream stopped at host monotonic 204601.204190 and its IMU stream at 204601.153662. The next UWB record arrived 21,579.928 s later; BLE did not disconnect until 225026.942061, 20,425.738 s (5.67 h) after data stopped. Immediately before the stall, telemetry reported `frames=91982`, `imu_records=148575`, `imu_active=1`, `drop_err=0`, `notify_errno=0`, `uart_err=0`; the queue snapshot had all `q_drop_* = 0`, HWM UWB/IMU/control = 1/1/3, and publisher max 1,773 µs. No queue or transport overflow preceded the silence. After reconnect, UWB briefly returned for about 9.5 s; IMU did not resume because the rebooted B306 reported `imu_active=0`.

BSF3C79 matches the same signature: UWB stopped at 199211.739532, IMU at 199211.788528, and BLE remained connected until 222198.299753, about 22,986.56 s later. Its pre-stall telemetry and queue snapshots also had zero `drop_err`, `notify_errno`, `uart_err`, and `q_drop_*`. Later reconnects produced short UWB bursts but did not restart IMU.

Verdict: **both streams silent with BLE alive**, so D3 is a B306/application data-plane stall debt. It is not fixed in relay8.2. The last pre-stall telemetry contains historical JY61P recoveries and UART restarts, but no event occurs at the stall boundary that establishes either as the trigger.

## F — relay8.2 implementation

Workspace: `UWB_Part/relay8_2-workspace/src/`  
Tag marker: `tag-fusion-link-relay8.2`  
Carrier marker: `master-tag-carrier-v2-fix16-relay8.2`  
Baseline: byte-for-byte copy of canonical relay8.1 before the scoped edits.

### F1 — local-DW phase/rate tracker

`include/tag_relay8_2.h:16-28` defines the two states: predicted next origin (`next_origin40`) and signed ticks-per-cycle rate correction (`rate_adjust_ticks`). Every accepted beacon computes the wrapped phase innovation against the prediction, divides it by elapsed beacon-counter epochs, rejects innovations over 5 ms, applies a 1/4 rate gain, clamps the estimate to ±100 ppm, anchors phase to the observed origin, and predicts the next origin (`include/tag_relay8_2.h:59-93`). This is the embedded counterpart of the host aligner's offset-plus-rate model, but uses integer DW ticks and a bounded IIR rather than the host's robust batch regression.

For a constant frequency error, residual rate error decays by 3/4 per valid reception: it reaches <10% after 9 updates (about 0.99 s at 110 ms) and <5% after 11 (about 1.21 s). During a long beacon absence the tracker holds the estimated rate and advances phase by nominal period plus rate correction each epoch (`include/tag_relay8_2.h:95-106`). A schedule-generation change deliberately zeros the rate estimate and re-anchors phase. Unit tests cover +20 ppm convergence, coast, 40-bit DW wrap, uint32 counter wrap, generation reset and outlier behavior (`tests/test_tag_relay8_2.c`).

The accepted-beacon path feeds the tracker and publishes its next-origin prediction at `src/ss_twr_init.c:566-617`; missed windows coast the same state at `src/ss_twr_init.c:755-765`.

### F2 — delayed hardware RX

The new arm routine computes the adaptive absolute window from the F1 prediction, rejects an already-late target, force-stops/clears the DW1000, programs `DX_TIME`, programs the RX timeout, and calls `dwt_rxenable(DWT_START_RX_DELAYED)` (`src/ss_twr_init.c:652-703`). Both pre-arm lateness and driver start failure increment `beacon_rx_arm_failures`; `BEACON_STATUS` exposes it as `rxarm` (`include/ss_twr_init.h:14-26`, `apps/tag/src/uwb_tag_ble.c:2227-2250`).

The critical ordering is at `src/ss_twr_init.c:5064-5080`: after the response collector releases the radio, delayed RX is armed **before** RX diagnostics, range calculation, formatting, and publication. CPU work then occurs while DW hardware counts down. The completion path handles a valid beacon, timeout/error, and a non-beacon first frame while retaining the original deadline (`src/ss_twr_init.c:706-752`). A static contract test asserts the delayed-RX API and arm-before-format order.

### F3 — sweep-counter contract audit and correction

The premise needed correction. In relay8.1, the explicit `ss_twr_init_sweep_count = 0U` was in `ss_twr_init_load_runtime_config` (`relay8_1-workspace/src/src/ss_twr_init.c:2814-2828`), and that loader is called by `ss_twr_init_start_with_config` during tag application startup (`relay8_1-workspace/src/apps/tag/src/tag_app.c:397-399`). Live `CFG`, `MODE`, and slot changes call `ss_twr_init_runtime_configure`, whose pending/apply path did **not** assign the sweep counter. The observed large reorder totals therefore cannot honestly be attributed to ordinary runtime CFG from source alone.

relay8.2 nevertheless removes the explicit assignment and documents that reboot-time BSS initialization is the sole reset (`src/ss_twr_init.c:385-387`). The runtime apply path changes schedule/tracker state without touching the public counter (`src/ss_twr_init.c:2520-2607`, `:5776-5823`), and the static test rejects any source assignment of zero. Natural uint32 wrap remains valid.

Consumer audit:

- Tag public output is the tag-owned uint32 counter (`src/ss_twr_init.c:388-399`).
- B306 performs wrap-safe unsigned delta classification and treats only the half-range backward case as reorder (`B306_Part/firmware/src/main.c:767-792`); it does not require CFG rebase.
- DK wraps the complete UWB packet byte-for-byte into its host record (`B306_Part/host/fusion_master/src/main.c:1376-1382`) and gates its own fault logic on B306 `node_sequence`, rebasing only when B306 uptime restarts (`:355-379`).
- The PC decoder extracts the uint32 sweep without transforming it (`B306_Part/tools/fusion_host_binary.py:382-425`). The reboot-aware classifier permits a rebase only with an independent tag boot/join or B306 reboot (`B306_Part/tools/sweep_counter_rebase.py:44-130`).
- Aligner v2 reconstructs elapsed epochs from B306 TIMER2 time independently of sweep (`B306_Part/tools/alignment/v2/time_aligner_v2.py:145-162`); sweep modulo 256 is only an association fingerprint against listener poll sequence (`:330-417`).
- The OTA readiness gate explicitly waits for the reboot-time backward discontinuity (`B306_Part/tools/relay8_1_batch_ota.py:196-250`). BSS reset preserves that contract.

Thus no audited consumer depends on a runtime-CFG counter reset, and reboot detection remains intact. Whether historical reorder totals came from reboot catch-up, an older deployed image, or another path remains an **UNKNOWN** rather than a manufactured F3 success claim.

### F4 — bounded adaptive window

The early/late base remains −500/+600 µs. Each missed epoch adds 100 µs to both sides; early caps at 3,000 µs after 25 misses and late caps at 3,000 µs after 24 misses (`include/tag_relay8_2.h:9-14`, `:108-124`). At 110 ms the window costs 1.00% radio airtime when locked and at most 5.45% at the 6 ms cap. F1 should keep it at base width in steady lock; F4 handles temperature steps and estimator transients. Because D1 lacks an actual phase-error series, the existing 30 s broad-reacquisition timeout was not tuned.

## Offline gates and artifacts

### Regression and protocol gates

All existing `tests/run_*.sh` suites passed: broadcast TDMA math, beacon sync, LED policy, relay6, relay7, relay8, run-state and beacon tests. New relay8.2 tests passed. The 96-byte UART frame is byte-identical to relay8.1. Round-trip control-plane dry run results:

| Item | Bytes / bound | Result |
|---|---:|---|
| `BEACON_STATUS` UART command | 13 / 191 | PASS |
| Wrapped Fusion control line | 29 / 200 | PASS |
| Worst-case reverse reply including `rxarm=4294967295` | 173 / 191 | PASS |

### Tag memory

| Build | FLASH | RAM | malloc arena | Gate |
|---|---:|---:|---:|---|
| relay8.1 baseline | 212,580 / 228,864 B = 92.88% | 55,312 / 65,536 B = 84.40% | 0 B | PASS |
| relay8.2 A/B | 213,632 / 228,864 B = 93.34% | 55,352 / 65,536 B = 84.46% | 0 B | PASS |
| Delta | +1,052 B | +40 B | 0 B | within FLASH <95%, RAM <85% |

The RTT-buffer reclaim ladder was not needed. OTA/SMP RAM and configuration are untouched.

### Reproducibility and canonical hashes

The unsigned application bytes, MCUboot binary, and MCUboot authenticated image hash are identical across pristine A/B builds. RSA-PSS signed envelopes are intentionally nondeterministic; A is selected as canonical and is the exact payload embedded in the carrier.

| Canonical artifact | SHA-256 |
|---|---|
| Tag unsigned `tag/zephyr/zephyr.bin` | `f792c70f7c4c1e657e7a90ebdf28058a075518c7ea4e3b053509dd4d9dc3b6b8` |
| Tag signed `tag/zephyr/zephyr.signed.bin` | `b7836eb9ce1ae55ad35be118f73046cc675ff1d753016f1a6e0a8c68bf333480` |
| Tag `dfu_application.zip` | `6d804094400983a82cc946c604233bbd3b9c833c37dc386ae8055cad29ec1cf1` |
| Tag `merged.hex` | `fc2795dce34b7436d07cb539b9b8fb7efc68b00c1dad6f5e732bfa746fae8a12` |
| MCUboot `zephyr.bin` | `7e25041f95c91f1e20fc4144ab2602b33e4ec8736c7e8fc84e3bb221d115dcf4` |
| Expected `IMGSTAT` / MCUboot image hash | `dacecc59e5b6fd8d1197e2f6ae57cb2673f1113f4f7902f81d64819190080d3f` |
| Generated carrier `ota_image.inc` | `007a9b838994a75db076429447ceeda8cc4151e41b3c4c786ef35a236350c572` |
| Carrier CPUAPP `zephyr.bin` | `4ba66c501001d29671463209ed5c64b0e8f659d374914c0042f591fcea78e0f7` |
| Carrier CPUNET `zephyr.bin` | `a8a9ddb6fd78e19c27fd8c7fa16849a66eb363d49c06e55ef0d05bfef5a9d3aa` |
| Carrier `merged_domains.hex` | `d10299bc3f249d659b8ec316e3c950e0d7081585c47186415b8750a02ba9aa03` |

Canonical paths:

- Tag: `UWB_Part/builds/tag-fusion-link-relay8.2-a/`
- Carrier: `UWB_Part/builds/master-control-b120-m1-master-tag-lfrc-fix16-relay8.2-a/`

Carrier A/B CPUAPP, CPUNET and `merged_domains.hex` compare byte-identical. Carrier memory is 404,352 B / 1 MiB FLASH (38.56%) and 160,037 B / 448 KiB RAM (34.89%) on CPUAPP; 158,100 B / 256 KiB FLASH (60.31%) and 45,784 B / 64 KiB RAM (69.86%) on CPUNET. Both marker registries bind marker to bytes and pass their reuse guards.

## Preregistered next-round hardware acceptance

All ten boards are gated; no waiver:

1. beacon narrow-window miss fraction <1% on every node;
2. T4 `epoch exact = 1.000` on every node;
3. slot-10 tag-domain rate ≥9.00 Hz;
4. Δmod16 = +1 on ≥99.9% of consecutive records;
5. `telemetry.reorder` delta = 0 fleet-wide;
6. new `rxarm` counter delta = 0 after initial configuration/lock.

## UNKNOWNs and deferred findings

- D1's tag-local phase-error slope, sign, exact valid-reception intervals and broad-reacquisition count are absent from the stored schema.
- D2 cannot identify caught versus missed epochs or actual CPU service time from passive listener data.
- F1/F2/F4 have passed source, unit, protocol, memory and reproducibility gates but have not run on hardware.
- D3's B306/application stall root cause is unresolved and deliberately outside relay8.2.
- Historical reorder attribution remains unresolved after the F3 source audit corrected the runtime-reset premise.
- GitHub LFS remote quota is unknown locally; `.git` relocation and historical large-file surgery require separate authorization.
- Three historical hybrid archives remain local as documented P1 exceptions.

**STOP: relay8.2 and carrier are built and offline-qualified. No hardware token is requested and no deployment was performed.**
