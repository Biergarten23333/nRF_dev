## b65 7-Tag TDMA Hold/Roster Stress - 2026-05-04

### Run

- Capture: `SS-TWR/alt-SS-TWR/broadcast/logs/tdma_7tag_motion180_b65_hold_roster_clean_20260504_104939/recv_20260504_104940`
- Duration: 180s
- Targets: BSF66F, BS2DCE, BSDC91, BSE88E, BS6F3A, BSF8E0, BS8251
- Profiles: all `motion`
- Anchor preflight: ready=8/8
- Capture result: success=true, controller_lost=false, startup_failed=false

### Startup Control Evidence

The b65 Master_Tag build did run the new hold/roster path:

- `tdma hold 1` accepted: `tdma hold rc=0 hold=1`
- Roster accepted for all 7 tags:
  - `BSF66F -> motion`
  - `BS2DCE -> motion`
  - `BSDC91 -> motion`
  - `BSE88E -> motion`
  - `BS6F3A -> motion`
  - `BSF8E0 -> motion`
  - `BS8251 -> motion`
- Link setup reached 7/7 before release.
- `tdma hold 0` accepted and produced one 7-tag rebalance generation (`gen=7`), with CFG confirmations for all 7 tags.

This validates the fixed b65 control path. The previous b64 overnight run did not validate this because its firmware did not understand `tdma hold` or `tdma roster`.

### Aggregate Results

- `positions_all=7981` over 180s = 44.34 TS/s total
- `tr_all=97648` over 180s = 542.49 TR rows/s total
- `tr_valid_all=58669` = 60.1% valid TR rows
- `tf_all=0`
- `CM/CS/CR/CF=0` as expected for TR/TS/TF broadcast path

### Per-Tag Results

| Tag | TS | TS Hz | TR rows | TR rows/s | TR valid | Latest TS time | Latest RMS | Latest anchors |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BSF66F | 1289 | 7.16 | 14008 | 77.8 | 72.9% | 180.0s | 47 | ABCDEFGH |
| BS2DCE | 1750 | 9.72 | 14008 | 77.8 | 88.7% | 180.0s | 31 | ABCDEFGH |
| BSDC91 | 1090 | 6.06 | 14000 | 77.8 | 42.7% | 180.0s | 117 | ABCH |
| BSE88E | 594 | 3.30 | 13992 | 77.7 | 36.9% | 178.4s | 6765871 | ABCD |
| BS6F3A | 539 | 2.99 | 13624 | 75.7 | 37.5% | 60.6s | 271 | ACDEFGH |
| BSF8E0 | 1561 | 8.67 | 14000 | 77.8 | 83.7% | 180.0s | 401 | ABCDEFGH |
| BS8251 | 1158 | 6.43 | 14016 | 77.9 | 57.4% | 179.9s | 493 | ABCDEFGH |

### Interpretation

The BLE/TDMA data plane is working for 7 tags: every tag produced roughly 14k TR rows in 180s, which is close to the expected 10 sweeps/s x 8 anchors. This answers the core capacity question: one Master_Tag B120 can receive 7-tag TR traffic at this scale, at least for a 180s run.

The remaining weakness is not basic TDMA slot allocation. It is per-tag range/solve quality and connection stability:

- BS2DCE is essentially at target: 9.72Hz TS and 88.7% valid TR.
- BSF8E0 is now usable: 8.67Hz TS and 83.7% valid TR.
- BSF66F is moderate: 7.16Hz TS, good final RMS.
- BSDC91, BS8251, BSE88E, and BS6F3A all receive slots and produce TR, but have many `T` rows and lower TS yield.
- BS6F3A disconnected around 60s and later reconnected/configured; its latest TS remained at 60.6s even though TR rows continued later.
- BSE88E's latest TS is numerically invalid despite ongoing TR; this points to layout/range-quality/solver input, not missing BLE data.

### Control-Path Issues Found

The capture cleanup path is still flawed. `cmd MODE AOTA` was reported as cleanup success by the host script, but raw log shows it was skipped by target filtering for all peers. After capture, tags were still producing TR. Manual targeted stop attempts also caused Master_Tag CDC/control disruption.

This should be fixed before overnight multi-hour stress:

1. Add a true broadcast stop command that ignores `ota_target name` filtering, or make `cmd MODE AOTA` with `ota_target prefix BS` actually match all `BS*` peers.
2. Make the host cleanup validate that Tag output has stopped, not just that the serial command was written.
3. Avoid switching Master control modes repeatedly during cleanup; it disturbs connected peers.

### Verdict

b65 hold/roster startup is validated. 7-tag TR throughput is validated for 180s. 7-tag 10Hz TS is not yet validated because several tags have low valid-range fraction or solver-quality issues, and BS6F3A still has a connection/quality problem.

Next best test is not another blind 7-tag run. First fix the cleanup/broadcast-stop path, then run a 4-5 tag known-good subset (`BS2DCE`, `BSF8E0`, `BSF66F`, maybe `BS8251`) to establish the clean ceiling, then add the weak tags one by one.
