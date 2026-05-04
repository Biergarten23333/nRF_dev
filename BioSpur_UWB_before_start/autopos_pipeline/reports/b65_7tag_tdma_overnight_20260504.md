# B65 / 7-Tag TDMA Overnight Notes - 2026-05-04

## What Ran

- Capture: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_7tag_motion1800_b64_hold_allowzero_20260504_005439/recv_20260504_005440`
- Duration requested: 1800 s
- Targets: `BSF66F, BS2DCE, BSDC91, BSE88E, BS6F3A, BSF8E0, BS8251`
- Anchor preflight: pass, `ready=8/8`
- Capture result: `success=true`, `controller_lost=false`, `tf_all=0`

## Important Correction

This run was **not** a valid test of the new TDMA hold/roster firmware.

Evidence from raw log:

- `tdma hold 1` returned `Unknown tdma command`
- `tdma roster ...` returned `Unknown tdma command`
- The running help only listed the old TDMA commands

Root cause: the hold/roster code had been added to the nested
`SS-TWR/alt-SS-TWR/broadcast/apps/...` copy, but the B120 Master_Tag build
uses the repo-root `apps/master_control` and `apps/master` sources.

Fix applied after capture:

- Ported hold/roster changes to:
  - `apps/master_control/src/main.c`
  - `apps/master/src/master_multi_app.c`
  - `apps/master/src/master_multi_app.h`
- Rebuilt: `build-master-control-b120-m1-master-tag-lfrc-b65-tdma-hold-roster`
- Verified LFRC policy with `scripts/assert_b120_internal_osc_build.sh`
- Flashed Master_Tag B120 SNR `1050070698`
- Verified live commands:
  - `tdma hold 1` -> `tdma hold rc=0`
  - `tdma roster BSF8E0 motion` -> `tdma roster rc=0`
  - `tdma hold 0` -> `tdma hold rc=0`

Master_Tag was left in `mode ota` and `tdma clear` was issued so the tags are
not kept running overnight.

## Capture Metrics

Overall:

- `positions_all=77446`
- `tr_all=920536`
- `tr_valid_all=584608`
- `cm/cs/cr/cf=0`
- `tf_all=0`

Per-tag summary:

| Tag | TS rows | TS Hz | TR rows | TR sweeps Hz | TR valid % | Latest TS |
|---|---:|---:|---:|---:|---:|---:|
| BSF66F | 10303 | 5.72 | 132904 | 9.23 | 61.4% | 1799.9s |
| BS2DCE | 14362 | 7.98 | 132680 | 9.21 | 80.6% | 1800.0s |
| BSDC91 | 14826 | 8.24 | 132448 | 9.07 | 84.2% | 1799.8s |
| BSE88E | 10491 | 5.83 | 132440 | 5.91 | 59.6% | 1799.9s |
| BS6F3A | 5803 | 3.22 | 124368 | 3.54 | 35.6% | 1229.0s |
| BSF8E0 | 7237 | 4.02 | 132808 | 9.22 | 42.0% | 1420.3s |
| BS8251 | 14424 | 8.01 | 132888 | 9.23 | 79.5% | 1800.0s |

Interpretation:

- The BLE/TDMA transport did carry seven tags for the full run.
- Most tags continued producing TR to the end.
- `BS6F3A` and `BSF8E0` kept emitting TR but stopped producing useful TS late
  in the run, consistent with poor UWB quality / placement / power behavior
  rather than an immediate identity failure.
- `BS6F3A` had repeated BLE disconnect/reconnect cycles, mostly reason `0x08`
  with some `0x3e`.

## Timing

TDIAG rows parsed: `11633`

| Phase | Median | P95 | P99 | Max |
|---|---:|---:|---:|---:|
| wait_ms | 79 | 89 | 89 | 3043 |
| tx_us | 610 | 732 | 823 | 1281 |
| rx_us | 183 | 244 | 305 | 793 |
| coll_us | 8453 | 8697 | 8819 | 9185 |
| range_us | 549 | 29571 | 43914 | 47729 |
| solve_us | 7049 | 12084 | 12664 | 15441 |
| out_us | 2136 | 3112 | 4455 | 5706 |
| clean_us | 30 | 61 | 91 | 17608 |
| total_ms | 100 | 129 | 143 | 3119 |

Normal sweep timing is around 100 ms per tag in the old 40 ms weighted TDMA
mode. There are rare multi-second long tails, usually wait/cleanup dominated.
Those need a repeat test with actual B65 hold enabled before deciding whether
they are scheduler or recovery artifacts.

## TDMA Stability Notes

Because hold/roster was not actually present in the running firmware, the old
behavior was still visible:

- Multiple CFG generations at startup (`gen=14..17`)
- A disconnect of `BSDC91` caused temporary rebalance to 6 tags, then back to 7
- `BS6F3A` repeated disconnect/reconnects caused many later CFG generations

This confirms the design reason for B65: freeze roster, hold rebalances while
enrolling, then release once, rather than letting every connection/profile
change reshape the schedule.

## Next Test

Use the newly flashed B65 Master_Tag:

1. Confirm `tdma show` help includes `tdma hold` and `tdma roster`.
2. Run a short 7-tag capture with the updated script.
3. Check raw log for:
   - `tdma hold rc=0`
   - `tdma roster rc=0`
   - one stable 7-tag generation after release
   - no repeated `gen` churn except real disconnect recovery
4. Then run the longer 10-tag scaling test.

