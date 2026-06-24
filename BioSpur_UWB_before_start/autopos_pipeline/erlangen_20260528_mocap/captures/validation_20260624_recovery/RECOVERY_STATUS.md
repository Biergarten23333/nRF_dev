# AutoPos Recovery Status - 2026-06-24

## 2026-06-24 Evening r800 OTA + 4-Tag Pressure Validation

Supersedes the earlier "wand validation still requires..." note below.

### Firmware Deployed

- Tags `BSF66F`, `BS9336`, `BS955A`, and `BSCCF4` were OTA-updated through
  Master_Tag.
  - Marker: `compact-sampled-tdmafix-boundary2spin-r800-20260624`
  - Post-version check matched on all 4 Tags.
  - Tag app RAM remains tight: about 97.36%.
- Anchors A-H were OTA-updated through Master_Anchor.
  - Marker: `compact-r800-20260624`
  - A succeeded in the first batch run.
  - B-H succeeded after the known DFU gate recovery pattern:
    pre-reset target, reset Master_Anchor carrier, then normal OTA.
  - No Anchor body was direct-flashed.

### Capture Path Fix

- `run_dual_master_tdma_capture.py` no longer has a hard-coded 24 s Anchor
  responder restore timeout.
- Added `--anchor-restore-timeout-s`, defaulting from
  `BIOSPUR_ANCHOR_RESTORE_TIMEOUT_S` or 75 s.
- The 4-Tag validation used `BIOSPUR_ANCHOR_RESTORE_TIMEOUT_S=90`.
- Reason: r800 A-H reconnect/ready can legitimately exceed 24 s after Master
  reset. The old timeout caused a false `anchor_responder_restore_failed`
  before tag capture started.

### 4-Tag 40 Hz, CIR Off

Path:
`free_TAG4_40HZ_OFF_R800_RETRY_BSF66F_BS9336_BS955A_BSCCF4_120s_20260624_181107/summary.json`

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `tr_all=37944`
- total row rate: about 316 rows/s, matching 4 Tags * 10 Hz * 8 anchors.
- overall sweep validity:
  - `ratio_ge4=0.792326`
  - `ratio_ge7=0.698503`
  - `ratio_ge8=0.374657`

Per Tag:

| Tag | Rows | Rows/s | ge7 | ge8 |
| --- | ---: | ---: | ---: | ---: |
| BSF66F | 9480 | 79.00 | 0.815190 | 0.356118 |
| BS9336 | 9488 | 79.07 | 0.979764 | 0.616358 |
| BS955A | 9488 | 79.07 | 0.023609 | 0.011804 |
| BSCCF4 | 9488 | 79.07 | 0.975548 | 0.514334 |

Interpretation: throughput and scheduling are stable, but this run had a severe
range-quality collapse on `BS955A`. That is not a 40 Hz scheduling failure.

### 4-Tag 40 Hz, Compact CIR

Path:
`free_TAG4_40HZ_COMPACT_R800_BSF66F_BS9336_BS955A_BSCCF4_120s_20260624_181431/summary.json`

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `tr_all=37928`
- total row rate: about 316 rows/s, again matching 4 Tags * 10 Hz * 8 anchors.
- overall sweep validity:
  - `ratio_ge4=0.979751`
  - `ratio_ge7=0.973423`
  - `ratio_ge8=0.546509`
- compact CIR raw log contains 579 `CRX` rows.
  - By Tag: BSF66F=142, BS9336=145, BS955A=145, BSCCF4=147.
  - By anchor ID: 0=76, 1=72, 2=69, 3=77, 4=70, 5=73, 6=71, 7=71.
  - This confirms sampled compact CIR is distributed across A-H; it is not
    stuck on G/H.

Per Tag:

| Tag | Rows | Rows/s | ge7 | ge8 |
| --- | ---: | ---: | ---: | ---: |
| BSF66F | 9480 | 79.00 | 0.978059 | 0.562869 |
| BS9336 | 9488 | 79.07 | 0.974705 | 0.580944 |
| BS955A | 9480 | 79.00 | 0.967089 | 0.556962 |
| BSCCF4 | 9480 | 79.00 | 0.973840 | 0.485232 |

Interpretation: compact CIR did not overload BLE or break 4-Tag 40 Hz TDMA.
The poor `BS955A` result in the CIR-off run did not reproduce in the compact
run, so it should be treated as a transient PHY/link/placement or per-run
quality issue, not proof that compact CIR itself breaks ranging.

## Result

Updated after BSF66F was power-cycled on 2026-06-24:

- BSF66F USB CDC returned as
  `/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00`.
- Master_Tag rediscovered BSF66F and restored the BLE/NUS link.
- `static 120s cir=off` passed.
- `static 120s cir=compact` passed without BLE/controller overload.
- `static 120s cir=full` passed, including deferred Full CIR USB capture.
- BSF66F was stopped back to `MODE IDLE` after the tests.

The earlier software/UI path issue was repaired where it was hiding the real
startup failure. Before the power-cycle, the flow was blocked by the live Tag
side:

- Master_Tag control CDC is present and responds.
- Master_Tag is in `RECV` mode with target kind `tag` and BLE ready.
- Master_Tag can start a `BS*` scan.
- After dual-core Master_Tag reset, targeted `BSF66F` discovery still failed.
- After `tdma clear`, broad `BS*` discovery still produced zero scan hits.
- Full CIR Tag USB `000760186115` is still absent from `/dev/serial/by-id`.

This means the current failure is not Flutter, not helper command routing, not
anchor preflight, not target-name selection, and not a stale TDMA roster. The
current blocker is that the Tag devices are not visible to Master_Tag as
connectable BioSpur `BS*` BLE advertisers.

## Post-Recovery Validation

- Direct BSF66F backend smoke:
  `direct_recv_BSF66F_after_repower/summary.json`
  - `success=true`
  - `tr_all=640`
  - `ratio_ge7=0.975`
  - `ratio_ge8=0.925`

- Static 120 s, CIR off:
  `static_TAG120_OFF_AFTER_REPOWER_BSF66F_120s_20260624_120640/summary.json`
  - `success=true`
  - `tr_all=9608`
  - `tr_valid_all=9352`
  - `ratio_ge7=0.980017`
  - `ratio_ge8=0.926728`
  - anchors seen: 0-7

- Static 120 s, Compact CIR:
  `static_TAG120_COMPACT_AFTER_REPOWER_BSF66F_120s_20260624_124926/summary.json`
  - `success=true`
  - `tag_cir=compact`
  - `tr_all=9608`
  - `tr_valid_all=9066`
  - `ratio_ge7=0.975853`
  - `ratio_ge8=0.692756`
  - no `HOST_WARN`, no `HOST_ERROR`, no serial exception
  - no controller recovery attempts
  - raw log contains 1200 `CRX` compact rows across 120 s
  - compact CRX distribution: anchor 5 = 1, anchor 6 = 343, anchor 7 = 856
  - interpretation: compact BLE output does not break the 10 Hz range stream,
    but all-8 validity is lower than CIR off/full.

- Static 120 s, Compact CIR after rank-offset repair:
  `static_TAG120_COMPACT_RANKOFF_RERUN_BSF66F_120s_20260624_143357/tag_capture_20260624_143416/summary.json`
  - firmware marker: `compact-rankoff-20260624`
  - `success=true`
  - `tr_all=9608`
  - `tr_valid_all=8730`
  - `ratio_ge4=0.980017`
  - `ratio_ge7=0.978351`
  - `ratio_ge8=0.410491`
  - `tr_status_counts={"O":8730,"T":878}`
  - raw log contains 1202 `CRX` compact rows, exactly one CRX per sweep.
  - compact CRX distribution is now all anchors, not only G/H:
    A=148, B=147, C=161, D=149, E=158, F=140, G=152, H=147.
  - interpretation: rank-offset compact achieves the intended A-H CIR sampling
    without breaking 10 Hz ranging, but all-8 sweep validity is still worse
    than CIR off/full. Treat compact as a light diagnostic mode, not a
    quality-equivalent ranging mode.

- Static 120 s, Compact CIR after sampled-compact repair:
  `static_TAG120_COMPACT_SAMPLED_BSF66F_BSF66F_120s_20260624_145609/tag_capture_20260624_145629/summary.json`
  - firmware marker: `compact-sampled-20260624`
  - `success=true`
  - `tr_all=9600`
  - `tr_valid_all=9026`
  - `ratio_ge4=0.980000`
  - `ratio_ge7=0.979167`
  - `ratio_ge8=0.662500`
  - `tr_status_counts={"O":9026,"T":574}`
  - raw log contains 147 `CRX` compact rows, sampled about once per 8 sweeps.
  - compact CRX distribution is all anchors:
    A=16, B=19, C=21, D=17, E=17, F=22, G=15, H=20.
  - single-missing sweeps are now concentrated on H again:
    A=10, B=5, C=5, D=8, E=6, F=5, G=9, H=332.
  - interpretation: sampled compact removes the rank-offset damage that spread
    timeouts across A-H. The remaining all-8 loss is mainly the existing H/tail
    slot weakness, not compact BLE bandwidth.

- Static 120 s, Full CIR:
  `static_TAG120_FULL_AFTER_REPOWER_BSF66F_120s_20260624_120950/summary.json`
  - `success=true`
  - range phase uses CIR off, deferred Full CIR phase after 120 s
  - `tr_all=9608`
  - `tr_valid_all=9288`
  - `ratio_ge7=0.979184`
  - `ratio_ge8=0.874271`
  - `cir_full_phase.success=true`
  - Full CIR frames: total 177, TAG 23, A-H all present

## Fixed

- `run_recv_tdma_capture.py` now writes `summary.json` even when initial
  session configuration fails with `target_link_not_ready:*`.
- `run_recv_tdma_capture.py` Master controller recovery now resets both
  nRF5340 NET and APP cores instead of APP only.
- `erlangen_aliases.sh` helper reset now also resets both NET and APP cores.
- Compact CIR no longer reads DW3000 diagnostics inside the RX window. The
  broadcast poll now carries a responder-rank offset, so compact mode rotates
  which anchor responds last and then performs the existing one-shot diagnostic
  read after the receive window.
- Compact CIR now samples one diagnostic sweep per 8 range sweeps by default
  (`APP_TAG_CIR_COMPACT_SAMPLE_PERIOD=8`). Non-sampled sweeps use the normal
  responder order and do not emit `CRX`, so compact CIR remains a lightweight
  diagnostic stream instead of perturbing every 10 Hz range sweep.
- The rejected intermediate compact implementations are documented:
  - per-anchor immediate diagnostic reads produced A-H CRX but damaged ranging
    timing (`ratio_ge7=0.461797`, `ratio_ge8=0.089001`).
  - one-immediate-read round-robin improved ranging but still damaged all-8
    quality (`ratio_ge7=0.921732`, `ratio_ge8=0.626145`).

## Evidence

- Direct short backend capture:
  `direct_recv_BSF66F_short_after_patch/summary.json`
  - `success=false`
  - `startup_failed=true`
  - `startup_fail_targets=["BSF66F"]`
  - `tr_all=0`

- Broad BS scan after clearing TDMA roster:
  `manual_master_tag_broad_bs_scan_tdma_clear.log`
  - `SCAN start req: bt_ready=1 ... target=tag prefix=BS`
  - `broad_bs_scan_tdma_clear_hits_lines=0`

- Full CIR Tag USB:
  - Expected: `/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00`
  - Current: not present.

## Required Next Physical Step

Static BSF66F is recovered. The later r800 validation above confirms the wand
Tags `BS9336`, `BS955A`, and `BSCCF4` are visible to Master_Tag and can run
with BSF66F as a 4-Tag 40 Hz group.
