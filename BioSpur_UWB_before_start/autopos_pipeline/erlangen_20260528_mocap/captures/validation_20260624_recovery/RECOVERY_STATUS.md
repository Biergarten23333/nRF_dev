# AutoPos Recovery Status - 2026-06-24

## 2026-06-25 RF Diagnostics Overnight Execution

The 2026-06-25 rollback freeze is preserved at:

```text
firmware_freeze/autopos_full_system_20260625_0245
```

Current hard priority for live positioning:

1. Preserve 7/8 and 8/8 anchor availability.
2. Use only diagnostic paths that do not reduce ranging success.

This priority is fixed by the Monte Carlo/layout sensitivity result: losing two
anchors causes a severe positioning-accuracy drop. Diagnostics that reduce
anchor coverage are therefore invalid for positioning-quality captures, even if
their RF data are useful offline.

New RF diagnostics builds were prepared and partially deployed:

- Tag marker: `tag-rfdiag-v2-g1200-r1000`
- Anchor marker: `alt-bcast-a19-rfdiag-v2-g1200-r1000`
- Generic co-located listener build:
  `SS-TWR/alt-SS-TWR/broadcast/build-uwb-listener-poll-diag-generic-20260625/merged.hex`
- `Master_Tag` and `Master_Anchor` B120 builds were verified for internal LFRC
  and flashed.
- Listener E SNR `760184767` was flashed with the generic poll diagnostics
  listener image.
- Tag `BSF66F` was OTA-updated and runtime `VERSION` verified the marker
  `tag-rfdiag-v2-g1200-r1000`.
- Tag `BS2DCE` and `BSDC91` OTA attempts failed before upload. Log inspection
  indicates the targets were not matched/accepted during OTA scan before the
  `phase_c_timeout_without_transport_result`; this is a target
  visibility/advertising/matching blocker, not a proven SMP upload failure.
  `BS9336`, `BS955A`, and `BSCCF4` were not attempted after this repeated
  failure pattern.
- No Anchor body was updated. Anchor A reached `DFU SMP service ready`, but
  failed the upload gate with `ota_gate_failed_after_dfu_ready`: image-state
  read did not get a usable SMP response after DFU-ready.
- Later Anchor OTA recovery succeeded; see
  `docs/rf_diag_overnight_20260625.md` for the full rollout log.
- Later RFD hot-path A/B proved that per-anchor `RFD` output damages normal
  ranging. The current safe Tag diagnostics path is v4 compact TR:
  `tag-rfdiag-v4tr-g1200-r1000`.
- Legacy listener SNR `760185886` remains out of scope and must not be touched.

RFD hot-path A/B result on 2026-06-25:

- RFD-on with Listener E:
  `tr_valid_all=4453/7064`, `ge7=0.124575`, `ge8=0.001133`.
- RFD-on without Listener:
  `tr_valid_all=4355/6968`, `ge7=0.095293`, `ge8=0.002296`.
- RFD-off without Listener:
  `tr_valid_all=7572/8552`, `ge7=0.927035`, `ge8=0.291862`.
- Conclusion: Listener E is not the source of the timeout collapse. The current
  Tag `RFD` output path at `APP_TAG_RF_DIAG_OUTPUT_PERIOD=1` is too heavy for
  normal 10 Hz full-sweep ranging.
- The temporary `tag-rfdiag-v2-rfd0-g1200-r1000` state was superseded by v4:
  `BSF66F` is now on `tag-rfdiag-v4tr-g1200-r1000`, and `Master_Tag` SNR
  `1050070698` is flashed with the matching v4 carrier.
- v4 disables legacy `RFD` rows and disables Tag-side `dwt_readdiagnostics()` in
  the response hot path. Anchor-side poll diagnostics are carried as compact
  `TR;3;...;D1,<base64>` data.
- Operational lock: do not re-enable legacy Tag `RFD` rows or Tag-side
  hot-path RX diagnostics for normal 10 Hz full-sweep ranging.

Rank-rotation diagnostic on 2026-06-25:

- A temporary `BSF66F` build forced `APP_TAG_ALT_BCAST_RANK_OFFSET_OVERRIDE=1`,
  so Anchor B became the first responder while guard stayed `1200 us` and
  response spacing stayed `1000 us`.
- Capture path:
  `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_rank1_Brank0_20260625_180213_20260625_180213/`
- Per-anchor valid ratios:
  A `682/1092 = 0.625`, B `322/1092 = 0.295`, C-G `1068/1092 = 0.978`,
  H `1067/1092 = 0.977`.
- Conclusion: the low-valid anchor follows the first responder slot. This points
  to a rank-0 / early Tag RX turn-around timing issue rather than a fixed Anchor
  A/H physical link problem.
- After the test, `Master_Tag` SNR `1050070698`, active Tag OTA payload, and
  `BSF66F` were restored to `tag-rfdiag-v4tr-g1200-r1000`.

Minimal validation capture succeeded with updated `BSF66F`, old A18 Anchors, and
Listener E:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_listenerE/
```

Key capture result:

- `tr_all=3632`
- `tr_valid_all=2815`
- `rfd_all=2821`
- `rfd_joined_all=2821`
- Listener E `lpd_rows=241`
- Time-filtered Listener E join rows: `206`
- Host parsing was patched after this capture to backfill missing TR/RFD
  `tag_id` values from the validated TDMA config. A repaired artifact exists at
  `range_diag_joined_tagid_backfilled.csv`, and the Listener E join now also
  reproduces without forcing a default tag id:
  `range_diag_listener_E_joined_tagid_backfilled.csv`.

Full plan, artifact hashes, deploy logs, capture commands, and go/no-go gates:

```text
docs/rf_diag_overnight_20260625.md
```

## 2026-06-24 Night Resume After Reboot - 6-Tag 10 Hz Validation

The workstation reboot did not leave any known capture, OTA, or CIR USB helper
process running. After reboot, the current `r800` no-diagnostic Tag firmware was
verified with all six Tags:

- `BSF66F`
- `BS2DCE`
- `BSDC91`
- `BS9336`
- `BS955A`
- `BSCCF4`

The active Tag marker for this validation is:

```text
compact-sampled-tdmafix-nodiag-r800-20260624
```

### Backend Fixes Under Test

- The old unconditional BLE diagnostic leak is removed:
  - `RXG`
  - `CD;1`
  - `CD;2`
- `tdma clear` now leaves Master_Tag in an explicit empty roster instead of
  auto-rescheduling every ready Tag after cleanup.
- Capture cleanup now restores Tags to `MODE IDLE`, clears TDMA, and verifies
  explicit-roster behavior before releasing hold.

### 6-Tag 10 Hz, CIR Off

Path:
`free_TAG6_10HZ_OFF_NODIAG_R800_BSF66F_BS2DCE_BSDC91_BS9336_BS955A_BSCCF4_120s_20260624_211420/tag_capture_20260624_211512/summary.json`

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `cleanup.success=true`
- `tr_all=52376`
- `tr_valid_all=36191`
- raw diagnostic leakage:
  - `RXG=0`
  - `CD;1=0`
  - `CD;2=0`
  - `CRX=0`
  - `CIRP=0`

Per Tag:

| Tag | Rows | Valid | ge7 | ge8 | Status summary |
| --- | ---: | ---: | ---: | ---: | --- |
| BSF66F | 8792 | 8101 | 0.970883 | 0.531392 | O=8101, T=691 |
| BS2DCE | 8808 | 2374 | 0.276113 | 0.151680 | O=2374, R=90, T=6344 |
| BSDC91 | 8808 | 7970 | 0.889192 | 0.480472 | O=7970, R=18, T=820 |
| BS9336 | 8576 | 5815 | 0.624067 | 0.217351 | O=5815, R=182, T=2579 |
| BS955A | 8816 | 4064 | 0.304900 | 0.076225 | O=4064, R=86, T=4666 |
| BSCCF4 | 8576 | 7867 | 0.968284 | 0.504664 | O=7867, T=709 |

Interpretation: the 6-Tag 10 Hz scheduler path runs to completion with no
controller loss and no diagnostic leakage in CIR-off mode. Range quality is not
uniform; `BS2DCE` and `BS955A` are the weak Tags in this run.

### 6-Tag 10 Hz, Compact CIR

Path:
`free_TAG6_10HZ_COMPACT_NODIAG_R800_BSF66F_BS2DCE_BSDC91_BS9336_BS955A_BSCCF4_120s_20260624_211845/tag_capture_20260624_211936/summary.json`

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `cleanup.success=true`
- `tag_cir=compact`
- `tr_all=52104`
- `tr_valid_all=40656`
- raw diagnostic output:
  - `RXG=0`
  - `CD;1=0`
  - `CD;2=0`
  - `CRX=700`
  - `CIRP=0`

Correct compact `CRX` format used for parsing:

```text
CRX;1;<sweep>;<anchor>;<raw_mm>;...
```

Compact `CRX` by anchor:

| Anchor | CRX rows |
| --- | ---: |
| A / 0 | 105 |
| B / 1 | 92 |
| C / 2 | 85 |
| D / 3 | 85 |
| E / 4 | 83 |
| F / 5 | 84 |
| G / 6 | 86 |
| H / 7 | 80 |

Compact `CRX` by Tag:

| Tag | CRX rows |
| --- | ---: |
| BSF66F | 38 |
| BS2DCE | 133 |
| BSDC91 | 131 |
| BS9336 | 132 |
| BS955A | 132 |
| BSCCF4 | 134 |

Per Tag:

| Tag | Rows | Valid | ge7 | ge8 | Status summary |
| --- | ---: | ---: | ---: | ---: | --- |
| BSF66F | 8688 | 956 | 0.106814 | 0.062615 | O=956, R=21, T=7711 |
| BS2DCE | 8664 | 7643 | 0.825485 | 0.373961 | O=7643, R=77, T=944 |
| BSDC91 | 8728 | 8086 | 0.967003 | 0.576535 | O=8086, T=642 |
| BS9336 | 8632 | 7972 | 0.968489 | 0.553290 | O=7972, T=660 |
| BS955A | 8712 | 8027 | 0.975207 | 0.528007 | O=8027, T=685 |
| BSCCF4 | 8680 | 7972 | 0.966820 | 0.513364 | O=7972, T=708 |

Interpretation: compact CIR no longer packet-bombs BLE and is distributed over
A-H, not stuck on G/H. In this specific run `BSF66F` had a severe range-quality
collapse, while the other five Tags stayed healthy. Because the weak Tag changes
between runs, this looks more like RF/link/placement/state instability than a
simple compact-CIR throughput failure.

### 6-Tag 10 Hz, Full CIR

Path:
`free_TAG6_10HZ_FULL_NODIAG_R800_DISCOVERYUSB_BSF66F_BS2DCE_BSDC91_BS9336_BS955A_BSCCF4_120s_20260624_212428/tag_capture_20260624_212519/summary.json`

- `success=true`
- `controller_lost=false`
- `no_tr_timeout=false`
- `tdma_config_failed=false`
- `cleanup.success=true`
- `tag_cir=full`
- `tag_cir_range_phase=off`
- `tr_all=52256`
- `tr_valid_all=34416`
- raw diagnostic output during range phase:
  - `RXG=0`
  - `CD;1=0`
  - `CD;2=0`
  - `CRX=0`
  - `CIRP=164`

Full CIR USB phase:

- `cir_full_phase.success=true`
- `returncode=0`
- `tag_full_sent=true`
- `tag_full_ack_seen=true`
- `tag_off_ack_seen=true`
- `frames=164`
- capture path:
  `free_TAG6_10HZ_FULL_NODIAG_R800_DISCOVERYUSB_BSF66F_BS2DCE_BSDC91_BS9336_BS955A_BSCCF4_120s_20260624_212428/tag_capture_20260624_212519/cir_full_usb/CIRRAW_BSF66F_BS2DCE_BSDC91_BS9336_BS955A_BSCCF4_20260624_212807/cir_full_meta.csv`

Frames by discovered USB stream:

| Port label | Stream | Frames |
| --- | --- | ---: |
| S760185889 | anchor | 29 |
| S760184781 | anchor | 29 |
| S760185876 | anchor | 29 |
| S760185878 | anchor | 29 |
| S760185904 | anchor | 28 |
| S760186115 | tag | 20 |

Per Tag during the Full-CIR run's range phase:

| Tag | Rows | Valid | ge7 | ge8 | Status summary |
| --- | ---: | ---: | ---: | ---: | --- |
| BSF66F | 8904 | 7869 | 0.822102 | 0.407907 | O=7869, R=58, T=977 |
| BS2DCE | 8656 | 7490 | 0.763401 | 0.369686 | O=7490, R=94, T=1072 |
| BSDC91 | 8720 | 1046 | 0.111927 | 0.072477 | O=1046, R=50, T=7624 |
| BS9336 | 8568 | 7812 | 0.969188 | 0.459384 | O=7812, T=756 |
| BS955A | 8800 | 2289 | 0.075455 | 0.053636 | O=2289, R=136, T=6375 |
| BSCCF4 | 8608 | 7910 | 0.971190 | 0.513941 | O=7910, T=698 |

Interpretation: deferred Full CIR command/control works and the USB capture
pipeline can collect real frames. However, this discovery-mode run only found
one Tag USB stream, `S760186115`, plus several anchor streams. Therefore this is
not yet proof that Full CIR USB capture is complete for all six Tags. The next
Full CIR-specific task is to build a reliable BS ID to J-Link CDC mapping, or
verify why the other Tag USB streams are not emitting `tag` frames.

### Current Conclusion

- The previous "cannot reach 10 Hz / BLE is overloaded by diagnostics" failure
  is fixed for this build.
- `cir=off` produces no raw diagnostic spam.
- `cir=compact` produces lightweight sampled `CRX` output across A-H and does
  not break the 6-Tag 10 Hz controller path.
- `cir=full` runs range first with CIR off, then performs deferred USB capture.
- The remaining open problem is range-quality instability under multi-Tag
  pressure. The failing Tag is not constant:
  - CIR off: `BS2DCE` and `BS955A` are poor.
  - Compact: `BSF66F` is poor.
  - Full run range phase: `BSDC91` and `BS955A` are poor.

This remaining issue is not currently explained by Flutter UI, TDMA cleanup,
stale roster, BLE diagnostic spam, or a missing 10 Hz scheduler path. It needs a
separate RF/link-quality pass: physical placement, orientation, near-field
effects, antenna state, per-Tag hardware state, and anchor geometry/volume.

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

## RF Diagnostics Overnight - 2026-06-25

The rfdiag-v2 visibility path was brought up without changing the solver.

- Freeze baseline:
  `firmware_freeze/autopos_full_system_20260625_0245`
- Listener split:
  old listener path moved to `UWB_listener_old`; new generic listener path is
  `SS-TWR/alt-SS-TWR/broadcast/UWB_listener`.
- Old listener SNR `760185886` was not touched.
- Listener E SNR `760184767` was flashed as the generic poll diagnostics
  listener beside Anchor E.
- `Master_Tag` SNR `1050070698` and `Master_Anchor` SNR `960148546` were
  flashed with LFRC-verified rfdiag-v2 master images.
- Tag `BSF66F` was OTA-updated to `tag-rfdiag-v2-g1200-r1000`.
- Tags `BS2DCE` and `BSDC91` failed before upload because the target was not
  accepted into the OTA transport path; remaining Tags were not attempted.
- A follow-up broad `BS*` visibility check still saw only `BSF66F`:
  `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/manual_master_tag_broad_bs_visibility_20260625_0510.log`.
  Do not force OTA for the remaining Tags while their pre-version/scan
  visibility is missing.
- Current overnight acceptance is `BSF66F` only; the other Tags are deferred.
- Anchors A-H were OTA-updated to the rfdiag-v2 Anchor image after adding a
  pre-reset + Master_Anchor reset flow.
- `anchor role all responder` was manually confirmed afterward:
  `sent=8 ready=8/8 total_sent=24`, `rc=0`.

Main proof capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_listenerE_anchor_v2_full_listener/
```

Result:

- `success=true`
- `tr_all=1848`
- `tr_valid_all=1134`
- `rfd_all=1139`
- `rfd_joined_all=1139`
- `anchor_diag_valid=1139`
- `tag_diag_valid=1139`
- Listener E `lpd.csv` rows: `228`
- Listener E to Anchor E joined rows: `158`
- Listener join `time_rejected=0` with `max_time_delta_s=0.5`

Interpretation: the new Anchor payload v2, Tag parser/RFD output, Master/host
capture parser, and Listener E poll diagnostics are all observable and aligned
for the single updated Tag. Proxy-quality/body-shadow usefulness is still not
proven; that requires controlled body-worn data.

## RF Diagnostics v4 Hot-Path Fix - 2026-06-25 13:25

The `RFD` path was fixed so diagnostics do not own the normal ranging output
path.

Root cause split:

- v2 emitted one legacy `RFD` line per anchor per sweep. This overloaded the
  normal output path and collapsed `ge7` to roughly `10-12%`.
- v3 removed the legacy `RFD` rows, but still called `dwt_readdiagnostics()` for
  every response in the Tag hot path. At 1 ms response spacing this still caused
  alternating missed slots.
- v4 disables both hazards for normal ranging.

Current safe live state:

- `BSF66F`: `tag-rfdiag-v4tr-g1200-r1000`
- `Master_Tag` SNR `1050070698`:
  `build-master-control-b120-m1-master-tag-lfrc-rfdiag-v4tr-g1200-r1000-20260625`
- Legacy `RFD`: disabled by `APP_TAG_RF_DIAG_LEGACY_RFD_ENABLE=0`
- Tag-side RX diagnostics read: disabled by `APP_TAG_RF_DIAG_TAG_RX_ENABLE=0`
- Compact RF diagnostics: enabled as `TR;3;...;D1,<base64>`
- Anchor-side poll diagnostics from response payload v2 remain available.
- Tag-side diagnostic q8 fields are expected to be zero in v4 unless the hot-path
  read is explicitly re-enabled for a non-performance experiment.

Final v4 no-listener acceptance capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_no_listener_anchor_v2_tag_v4tr_20260625_131518_20260625_131518/
```

Result:

- `success=true`
- `tr_all=8696`
- `tr_valid_all=7566`
- `tr_diag_all=8696`
- `rfd_all=0`
- `rfd_joined_all=8696`
- `sweeps_total=1087`
- `ratio_ge7=0.847286`
- `ratio_ge8=0.254830`
- `O=7566`, `T=1130`
- all valid `O` rows have `anchor_diag_valid=1`, `tag_diag_valid=0`.

Operational rule:

- Use v4 compact TR for RF-diagnostic visibility captures where normal ranging
  must keep working.
- Use the pure no-diagnostic freeze for strict positioning baselines.
- Do not re-enable per-anchor legacy `RFD` rows or Tag-side hot-path RX
  diagnostics in normal 10 Hz full-sweep ranging.

## 2026-06-25 Rank0 A/B Test and Recovery

Rank rotation confirmed that the low-valid problem follows the first response
slot:

- A19 Anchor v2 + v4 Tag baseline, A rank0:
  A/0 `424/1087 = 0.390`, B-G about `0.969-0.977`, H/7 `0.721`.
- B-rank0 Tag test:
  B/1 `322/1092 = 0.295`, A/0 improved to `682/1092 = 0.625`,
  C-H about `0.977-0.978`.

Two temporary Anchor A tests were attempted and rejected:

- A20 `skipr0` sent v1 length for rank0: A/0 `0/1103 = 0.000`.
- A21 `zeror0` kept v2 length but skipped rank0 diag read: A/0
  `0/1091 = 0.000`.

Recovery actions completed:

- Master_Anchor B120 restored to:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- `apps/master_ota/generated/active_ota_payload.json` restored to
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A OTA restored to A19.
- Anchor A was found running `role=matrix`; forced back to responder via:
  `anchor role F3BB7A04104F9CB8561DDDACB9E53714 responder`.
- Final 30 s verification:
  `capture_BSF66F_30s_after_A_role_responder_restore_20260625_190421_20260625_190421`
  with A/0 `102/262 = 0.389` and all anchors seen.

Current operational state: A is back in responder role, BSF66F remains
`tag-rfdiag-v4tr-g1200-r1000`, and the A20/A21 skip-rank0 builds must not be
used as production firmware.

## 2026-06-25 Rank0 Responder-Side Profile

Temporary measurement build:

```text
alt-bcast-a22-rfdiag-v2-prof-g1200-r1000
```

Only Anchor A was OTA-updated to A22. The build kept the A19/v2 response
payload behavior and enabled low-rate responder-side profile/diag printk.

Evidence:

```text
Anchor A CDC profile:
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_prof_cdc_20260625_192627/anchor_A_cdc.log

Range capture:
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_60s_anchor_A_prof_20260625_192627_20260625_192627/
```

Profile capture result:

- A/0 range validity collapsed to `0/541 = 0.000`.
- Other non-tail anchors stayed around `0.972-0.978`; H/7 was `0.617`.
- A responder diag showed BSF66F polls were received:
  `tag_poll=0,292,0,0,0,0,0,0`.
- A responder diag showed no successful rank0 responses:
  `tag_ok=0,0,0,0,0,0,0,0`.
- A responder diag showed every BSF66F attempt missed delayed TX:
  `tag_tx_miss=0,292,0,0,0,0,0,0`.
- Parsed profile windows: `294/294` delayed-TX attempts missed.
- Timing profile:
  `avg start_us=1220-1434`, `avg txprog_us=976-1190`,
  `min_slack_uus=-55585..96`, `resp_delay_uus=1200`.

Conclusion:

Anchor A rank0 is not mainly failing because the Tag opens RX too late, and it
is not failing because A misses the poll. A receives the poll, then misses the
rank0 delayed-TX deadline before/at `dwt_starttx()`. The 1200 uus responder delay
has no practical slack for the current responder hot path with v2 diagnostics.

Recovery after this measurement:

- Master_Anchor restored to:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- Active Anchor OTA payload restored to:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A OTA-restored to A19 and forced back to responder role.
- Final 30 s restore capture:
  `capture_BSF66F_30s_after_A19_restore_20260625_193438_20260625_193438`
  with A/0 `100/272 = 0.368`, B-F `266/272 = 0.978`,
  G/6 `264/272 = 0.971`, H/7 `169/272 = 0.621`.

Current operational state: A is A19 responder, Master_Anchor is the A19 carrier,
the active Anchor OTA payload is A19, and BSF66F remains
`tag-rfdiag-v4tr-g1200-r1000`.

## 2026-06-25 Rank0 Hot-Path Fix Attempts

Two temporary Anchor A builds tested the direct fix suggested by the A22
profile: shorten the responder hot path before delayed TX. Both kept
`APP_ALT_SS_TWR_GUARD_US=1200`, `APP_ALT_SS_TWR_RESP_SPACING_US=1000`, and
`APP_ANCHOR_RESP_DELAY_UUS=1200`.

### A23 no-diagnostic hot path

Build:

```text
alt-bcast-a23-nodiag-prof-g1200-r1000
```

Artifacts:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_A23_nodiag_prof_20260625_195807_20260625_195807/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_A23_nodiag_prof_cdc_20260625_195807/anchor_A_cdc.log
```

Result:

- A/0 recovered to `370/402 = 0.920`.
- B-G stayed around `0.965-0.975`.
- H/7 was `312/402 = 0.776`.
- A responder profile: `ok=187`, `tx_miss=8`,
  `tag_poll=0,195,0,0,0,0,0,0`.
- Delayed-TX miss rate: `4.1%`.
- `start_us=707-769`, `txprog_us=491-552`.

### A24 rank0 fast path

Build:

```text
alt-bcast-a24-r0fast-prof-g1200
```

This variant keeps the v2 response frame shape but skips rank0 diagnostics
before delayed TX. Non-rank0 responders still return v2 diagnostics.

Artifacts:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_A24_r0fast_prof_20260625_200856/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_30s_A24_r0fast_prof_retry_20260625_201105/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_A24_r0fast_prof_cdc_retry_20260625_201105/anchor_A_cdc.log
```

45 s result:

- A/0 recovered to `378/405 = 0.933`.
- B/1 `396/405 = 0.978`.
- C/2 `387/405 = 0.956`.
- D/3 `394/405 = 0.973`.
- E/4 `393/405 = 0.970`.
- F/5 `396/405 = 0.978`.
- G/6 `396/405 = 0.978`.
- H/7 `307/405 = 0.758`.
- `ratio_ge7=0.960494`, `ratio_ge8=0.696296`.

Diagnostic behavior:

- A/0 rank0 had `anchor_diag_valid=0` for `405/405` rows.
- Non-rank0 successful rows kept `anchor_diag_valid=1`.

Responder profile retry:

- Final diag: `ok=604`, `tx_miss=32`,
  `tag_poll=0,636,0,0,0,0,0,0`.
- Delayed-TX miss rate: `5.0%`.
- Profile windows: `238` attempts, `14` misses.
- `start_us=749-792`, `txprog_us=552-590`.

Conclusion:

- Rank0 failure follows the responder deadline, not Tag RX enable timing.
- Keeping rank0 pre-TX work below roughly `800 us` recovers range validity.
- A24 is the preferred next implementation direction because it protects rank0
  timing while preserving diagnostics for the other response slots.

Recovery completed after A24:

- Master_Anchor restored to
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- Active Anchor OTA payload restored to
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A OTA-restored to A19 and forced back to responder role.
- Final restore smoke capture:
  `capture_BSF66F_30s_restore_A19_after_A24_20260625_201807`
  with A/0 `97/261 = 0.372`, B-G around `0.973-0.977`, H/7
  `178/261 = 0.682`.

Current state: A is A19 responder, Master_Anchor is the A19 carrier, active
Anchor OTA payload is A19, and BSF66F remains `tag-rfdiag-v4tr-g1200-r1000`.

## 2026-06-25 22:14-22:51 - A25/A26/A27 Rank0 Diagnostic Preservation Tests

Goal: test three alternatives after A24 showed that rank0 succeeds only when
diagnostic work is removed from the pre-`dwt_starttx()` hot path.

Method:

- Only Anchor A was OTA-updated for each temporary build.
- Temporary Master_Anchor carriers were used only to deliver each OTA payload.
- Before each capture, Master_Anchor was restored to the stable A19 carrier and
  a normal 8/8 responder preflight was run.

### A25 post-TX read

Build marker:

```text
alt-bcast-a25-postread-prof-g1200
```

Artifacts:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a25_A19carrier_20260625_221406/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a25_A19carrier_cdc_20260625_221406/anchor_A_cdc.log
```

Result:

- A/0: `399/412 = 0.968`
- B-G: `0.973-0.978`
- H/7: `251/412 = 0.609`
- `ratio_ge7=0.978155`, `ratio_ge8=0.589806`
- A/0 diagnostics: `anchor_diag_valid=0` for all rows.
- Profile: `46` attempts, `0` misses, `start_us=781`, `txprog_us=578`.

### A26 delayed payload diagnostics

Build marker:

```text
alt-bcast-a26-delayed-prof-g1200
```

Artifacts:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a26_A19carrier_20260625_221925/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a26_A19carrier_cdc_20260625_221925/anchor_A_cdc.log
```

Result:

- A/0: `382/392 = 0.974`
- B-G: `0.967-0.977`
- H/7: `241/392 = 0.615`
- `ratio_ge7=0.974490`, `ratio_ge8=0.602041`
- A/0 diagnostics present on `380/392` rows.
- A/0 diagnostic flags: `3 = VALID | DELAYED`.
- Profile: `44` attempts, `0` misses, `start_us=786-854`,
  `txprog_us=579-671`.

Conclusion: A26 is the best tested option. It preserves rank0 validity and
returns A/0 diagnostics through the normal range stream, marked as delayed.

### A27 side-channel diagnostics

Build marker:

```text
alt-bcast-a27-side-prof-g1200
```

Artifacts:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_45s_a27_A19carrier_20260625_222439/
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_A_a27_A19carrier_cdc_20260625_222439/anchor_A_cdc.log
```

Result:

- A/0: `401/412 = 0.973`
- B-G: `0.968-0.978`
- H/7: `261/412 = 0.633`
- `ratio_ge7=0.968447`, `ratio_ge8=0.616505`
- A/0 payload diagnostics: `anchor_diag_valid=0` for all rows.
- Expected `APD;` side-channel rows were not captured (`APD count = 0`).
- Profile: `46` attempts, `0` misses, `start_us=793`, `txprog_us=592`.

Conclusion: A27 keeps rank0 timing healthy but does not currently deliver useful
diagnostics without more side-channel plumbing.

### Restore State After A25/A26/A27

Restored:

- Master_Anchor carrier:
  `build-master-control-b120-m1-master-anchor-lfrc-rfdiag-v2-g1200-r1000-20260625`.
- Active Anchor OTA payload:
  `alt-bcast-a19-rfdiag-v2-g1200-r1000`.
- Anchor A A19 OTA upload:
  `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_A_restore_A19_after_A27_20260625_222755/`.

Open caveat:

- Final skip-preflight smoke capture
  `capture_BSF66F_30s_restore_A19_after_A27_skippre_20260625_223903`
  ran, but A/0 was absent. This means Anchor A was not successfully forced back
  to responder role after the A19 restore.
- Attempted Anchor BLE responder preflight after restore did not recover 8/8
  links before interruption (`conn_count=0`).
- Before the next normal experiment, run a control-plane recovery/8-anchor
  responder preflight and confirm A/0 is again present in `tr_all.csv`.

## 2026-06-25 23:04 - A26 Promoted As Working Baseline

A26 was selected as the new Anchor A rank0 diagnostic baseline.

Actions:

- Active Anchor OTA payload metadata set to
  `alt-bcast-a26-delayed-prof-g1200`.
- Anchor A OTA-updated to A26:
  `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/anchor_ota_A_A26_baseline_20260625_230233/`.
- Master_Anchor was restored to the stable A19 control carrier before capture.
- Anchor responder preflight succeeded with `sent=8`, `ready=8/8`.

120 s BSF66F capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_120s_A26_baseline_20260625_230426/
```

Per-anchor validity:

- A/0: `1069/1100 = 0.971818`
- B/1: `1075/1100 = 0.977273`
- C/2: `1059/1100 = 0.962727`
- D/3: `1076/1100 = 0.978182`
- E/4: `1074/1100 = 0.976364`
- F/5: `1076/1100 = 0.978182`
- G/6: `1075/1100 = 0.977273`
- H/7: `715/1100 = 0.650000`

Sweep validity:

- `ratio_ge7=0.974545`
- `ratio_ge8=0.628182`
- valid-count distribution: `{6: 4, 7: 381, 8: 691}`

Diagnostic confirmation:

- A/0 `anchor_diag_valid=1` on `1063/1100` rows.
- A/0 `anchor_diag_flags=3` on those rows, confirming delayed diagnostics.

Current state:

- Anchor A: A26 delayed-diagnostic baseline.
- Active Anchor OTA payload metadata: A26.
- Master_Anchor runtime/control carrier: stable A19 carrier.
- Tag `BSF66F`: v4 compact-TR diagnostics path,
  `tag-rfdiag-v4tr-g1200-r1000`.
- Legacy Tag `RFD` rows: disabled for live positioning captures.
- Tag-side hot-path RX diagnostics: disabled for live positioning captures.

Frozen working result:

- This A26 + v4tr state is the current working RF-diagnostic baseline.
- It preserves A rank0 validity while keeping Anchor-side diagnostic visibility.
- A/0: `1069/1100 = 0.971818`.
- H/7: `715/1100 = 0.650000`.
- `ratio_ge7=0.974545`.
- `ratio_ge8=0.628182`.
- Remaining 8/8 loss is H-dominated; do not trade away additional anchors for
  Tag-side diagnostics.

## 2026-06-26 00:12 - Wand v4tr OTA and 4-Tag Static Pressure Test

The three Wand Tags were OTA-updated to the current safe Tag firmware:

```text
tag-rfdiag-v4tr-g1200-r1000
```

OTA evidence:

- `BSCCF4` / Wand-A:
  - upload:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_20260626_000025/`
  - version verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_verify_BSCCF4_20260626_000724/`
  - post marker: `tag-rfdiag-v4tr-g1200-r1000`
- `BS9336` / Wand-B:
  - upload + post verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_continue_20260626_000401/`
  - post marker: `tag-rfdiag-v4tr-g1200-r1000`
- `BS955A` / Wand-C:
  - upload + post verification:
    `SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_wand_v4tr_continue_20260626_000401/`
  - post marker: `tag-rfdiag-v4tr-g1200-r1000`

The first OTA run completed `BSCCF4` upload successfully but the Master_Tag CDC
renumbered from `/dev/ttyACM18` to `/dev/ttyACM19`. Follow-up work used the
stable by-id path:

```text
/dev/serial/by-id/usb-Master_Tag_BioSpur_BLE_Control_6918E0384172A49F-if00
```

120 s static pressure capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_wand3_static120_v4tr_A26_20260626_000844_20260626_000844/
```

Capture status:

- Targets: `BSF66F,BSCCF4,BS9336,BS955A`.
- `tag-cir=off`; no listener path used.
- Anchor responder preflight: `sent=8`, `ready=8/8`.
- TDMA config matched all four Tags at 10 Hz.
- `success=true`, `controller_lost=false`, `no_tr_timeout=false`.
- Cleanup succeeded and returned Tags to quiet/IDLE.
- `rfd_all=0`, so legacy Tag `RFD` stayed off.

Overall result:

- `tr_valid_all=25462/35272 = 0.722`
- `ratio_ge7=0.724427`
- `ratio_ge8=0.504650`

Per-Tag result:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `8464/9024 = 0.938` | `0.968085` | `0.663121` | `O=8464, T=560` |
| `BSCCF4` | `2107/8488 = 0.248` | `0.245052` | `0.131008` | `O=2107, R=95, T=6286` |
| `BS9336` | `6264/8832 = 0.709` | `0.680254` | `0.320652` | `O=6264, R=135, T=2433` |
| `BS955A` | `8627/8928 = 0.966` | `0.977599` | `0.881720` | `O=8627, T=301` |

Interpretation:

- `BSF66F` and `BS955A` are healthy in this 4-Tag static run.
- `BS9336` is usable but weaker, especially A/0 and H/7.
- `BSCCF4` is the dominant problem; all anchors are low
  (`0.149-0.277` per-anchor valid ratio). This points to a Tag/placement/power
  or physical RF issue rather than the scheduler, since other Tags were running
  in the same capture.

State restored after the Tag OTA work:

- Active generated OTA payload restored to Anchor A26:
  `alt-bcast-a26-delayed-prof-g1200`.
- `verify_ota_payload_kind.py --expected anchor` passed.

## 2026-06-26 00:24 - ROTO v4tr OTA and 3-Tag Static Pressure Test

The two ROTO Tags were OTA-updated to the current safe Tag firmware:

```text
tag-rfdiag-v4tr-g1200-r1000
```

OTA evidence:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/tag_ota_roto_v4tr_20260626_001737/
```

OTA results:

- `BS2DCE`: post marker `tag-rfdiag-v4tr-g1200-r1000`
- `BSDC91`: post marker `tag-rfdiag-v4tr-g1200-r1000`

120 s static pressure capture:

```text
SS-TWR/alt-SS-TWR/broadcast/logs/rfdiag_v2_overnight_20260625/capture_BSF66F_roto2_static120_v4tr_A26_20260626_002045_20260626_002045/
```

Capture status:

- Targets: `BSF66F,BS2DCE,BSDC91`.
- `tag-cir=off`; no listener path used.
- Anchor responder preflight: `sent=8`, `ready=8/8`.
- TDMA config matched all three Tags at 10 Hz.
- `success=true`, `controller_lost=false`, `no_tr_timeout=false`.
- Cleanup succeeded and returned Tags to quiet/IDLE.
- `rfd_all=0`, so legacy Tag `RFD` stayed off.

Overall result:

- `tr_valid_all=17395/26352 = 0.660`
- `ratio_ge7=0.624165`
- `ratio_ge8=0.428355`

Per-Tag result:

| Tag | Valid rows | ge7 | ge8 | Status summary |
|---|---:|---:|---:|---|
| `BSF66F` | `7795/8864 = 0.879` | `0.800542` | `0.393502` | `O=7795, R=60, T=1009` |
| `BS2DCE` | `1117/8624 = 0.130` | `0.083488` | `0.069573` | `O=1117, R=40, T=7467` |
| `BSDC91` | `8483/8864 = 0.957` | `0.973827` | `0.812274` | `O=8483, T=381` |

Interpretation:

- `BSDC91` is healthy in this 3-Tag static run.
- `BSF66F` is usable but weaker than its solo/A26 baseline in this scene,
  especially B/C/H.
- `BS2DCE` is the dominant problem; all links are poor, especially C-H
  (`0.071-0.083` per-anchor valid ratio). This points to a `BS2DCE`
  Tag/placement/power/RF-state issue rather than the scheduler, since `BSDC91`
  was healthy in the same capture.

State restored after the Tag OTA work:

- Active generated OTA payload restored to Anchor A26:
  `alt-bcast-a26-delayed-prof-g1200`.
- `verify_ota_payload_kind.py --expected anchor` passed.
