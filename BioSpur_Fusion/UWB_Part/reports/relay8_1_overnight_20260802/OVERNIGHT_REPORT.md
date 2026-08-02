# relay8.1 overnight report

OTA outcome: 10/10 canonical relay8.1 confirmed, 0 quarantined.
W verdict: **FAIL** across the preregistered ten-node gate.
Endurance outcome: terminal=fleet_death, per-node data-service span 2.05–7.94 h; data cessation is not assumed to mean battery death, and this is not a full-charge endurance record.

Run status: capture started at 2026-08-02 00:52:11 CEST and reached fleet death at 08:51:49; the complete run closed at 08:55:29 after the quiet witness and beacon restore. The host and all seven listener collectors are stopped, the main beacon was restored to 100,000 us, and no hardware action is pending. Terminal cleanup is `PARTIAL` only because zero peers remained reachable for IMU STOP/composed idle; there was no surviving tag left running.

## Phase 1 — OTA and command-path warm-up

| BSF | result | confirmed | first successful VERSION after true app start (s) |
|---|---|---:|---:|
| BSF3C79 | COMPLETE | 1 | 287.58 (earlier patient discriminator) |
| BSFC2CC | COMPLETE | 1 | not measured against the corrected discriminator |
| BSF44AD | COMPLETE | 1 | 959.716 |
| BSF6C53 | COMPLETE | 1 | 15.802 |
| BSF8BC4 | COMPLETE | 1 | 15.603 |
| BSF1120 | COMPLETE | 1 | 15.800 |
| BSF31CC | COMPLETE | 1 | 15.751 |
| BSFAA61 | COMPLETE | 1 | 15.802 |
| BSFEC35 | COMPLETE | 1 | 15.750 |
| BSFB165 | COMPLETE | 1 | 15.604 |

Corrected seven-board observed distribution: min 15.603 s, median 15.751 s, max 15.802 s. The first query was deliberately scheduled after the 15 s readiness hold, so this is an observation bound, not the intrinsic earliest command-ready time.

## W qualification

Window wall span: 1854.007 s, comprising 1800.001 s of live capture plus bounded snapshot intervals. All rates are tag-domain rates.

| BSF | slot | UWB Hz | IMU delivered | IMU gaps | Δmod16 +1 | epoch exact | miss fraction | verdict |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BSF3C79 | 1 | 9.09091 | 1.000000 | 0 | 0.999941 | 0.365988 | 0.996303 | FAIL |
| BSFEC35 | 2 | 9.09091 | 1.000000 | 0 | 1.000000 | 1.000000 | 0.996346 | FAIL |
| BSF44AD | 3 | 9.09091 | 1.000000 | 0 | 1.000000 | 1.000000 | 0.996303 | PASS |
| BSF6C53 | 4 | 9.09091 | 1.000000 | 0 | 1.000000 | 1.000000 | 0.996319 | FAIL |
| BSF8BC4 | 5 | 9.09091 | 1.000000 | 0 | 1.000000 | 1.000000 | 0.996319 | FAIL |
| BSF1120 | 6 | 9.09091 | 1.000000 | 0 | 1.000000 | 1.000000 | 0.996319 | FAIL |
| BSF31CC | 7 | 9.09091 | 1.000000 | 0 | 0.999941 | 0.332799 | 0.996319 | FAIL |
| BSFAA61 | 8 | 9.09091 | 1.000000 | 0 | 0.999703 | 0.073769 | 0.996362 | FAIL |
| BSFB165 | 9 | 9.06286 | 1.000000 | 0 | 0.996845 | 0.236162 | 0.996362 | FAIL |
| BSFC2CC | 10 | 4.54545 | 1.000000 | 0 | 0.000000 | 1.000000 | 0.621574 | FAIL |

Gated counter deltas are preserved in `analysis/analysis.json`. Non-zero gated deltas by node:

- BSF3C79: `{"telemetry.duplicate": 1, "telemetry.header": 1, "telemetry.reorder": 3225}`
- BSFEC35: `{"telemetry.reorder": 16842}`
- BSF6C53: `{"telemetry.reorder": 16842}`
- BSF8BC4: `{"telemetry.reorder": 16852}`
- BSF1120: `{"telemetry.reorder": 16843}`
- BSF31CC: `{"telemetry.reorder": 16843}`
- BSFAA61: `{"telemetry.header": 1, "telemetry.reorder": 16852}`
- BSFB165: `{"telemetry.reorder": 16800}`
- BSFC2CC: `{"telemetry.reorder": 8422}`

### relay8.1 fix readings

- Δmod16 +1 ≥99.9% on all ten: **False**.
- Listener absolute epoch exact on all ten: **False**.
- Beacon-window miss approximately zero: **False**.
- Slot-10 BSFC2CC rate: 4.54545 Hz; ≥9 Hz: **False**.

### Source-audited attribution

- The beacon tracker extrapolates the next window by one fixed period in the tag's local DW clock (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:80-87`) and uses only a −500/+600 µs window (`:11-12`, `:104-127`). A miss advances the same local prediction by one period (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:614-625`); broad reacquisition is not entered until 30 s without a valid beacon (`:773-779`, `:815-833`). The observed roughly 30 s reacquisition cadence is therefore consistent with unmodelled relative DW-clock drift escaping the narrow window.
- relay8.1 services the slot-tail window only after the complete sweep (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:3709-3718`, service at `:837-857`), despite a declared slot-10 tail budget of only 1,400 µs (`UWB_Part/relay8_1-workspace/src/include/tag_beacon_sync.h:13`). The measured slot-10 every-other-epoch output shows that this service point remains too late in the real path; this is an inference from the source ordering plus hardware data, not a direct internal timing trace.
- Runtime configuration resets the tag-owned sweep counter to zero (`UWB_Part/relay8_1-workspace/src/src/ss_twr_init.c:2814-2828`), while the public value is exactly that local counter (`UWB_Part/relay8_1-workspace/src/include/tag_relay6.h:22-29`). B306 classifies a backward jump as reorder and deliberately keeps the old baseline (`B306_Part/firmware/src/main.c:767-792`). Thus the post-CFG reorder increments are a deterministic generation/rebase incompatibility, not host packet loss.

## First-ten-minute data products

Absolute position accuracy is out of scope because there is no ground truth. RMS below is scatter about each node's own mean using the standing V4-io geometry.

![Ten-node position scatter](analysis/positions_3d_first10min.png)

| BSF | solved/attempted | scatter RMS (mm) |
|---|---:|---:|
| BSF3C79 | 5454/5454 | 128.29 |
| BSFC2CC | 2727/2727 | 80.78 |
| BSF44AD | 5454/5454 | 164.08 |
| BSF6C53 | 5453/5454 | 72.82 |
| BSF8BC4 | 5412/5454 | 107.96 |
| BSF1120 | 5403/5455 | 67.84 |
| BSF31CC | 5455/5455 | 92.11 |
| BSFAA61 | 5380/5455 | 100.53 |
| BSFB165 | 5433/5442 | 47.86 |
| BSFEC35 | 5243/5454 | 86.07 |

Per-node six-axis figures are `analysis/imu_BSFxxxx_first10min.png`; the complete mean/noise table is `analysis/imu_bias_noise_first10min.csv`. Acceleration means include the gravity projection and therefore are not pure sensor bias.

## Remaining-capacity endurance

| BSF | last observed data from W start (h) | data cessation | first BLE epoch (h) | BLE drop observed |
|---|---:|---|---:|---|
| BSF3C79 | 7.632 | True | 7.032 | True |
| BSFC2CC | 7.940 | False | 7.988 | True |
| BSF44AD | 6.973 | True | 7.039 | True |
| BSF6C53 | 7.061 | True | 7.046 | True |
| BSF8BC4 | 7.295 | True | 7.334 | True |
| BSF1120 | 2.052 | True | 7.813 | True |
| BSF31CC | 4.339 | True | 6.923 | True |
| BSFAA61 | 7.237 | True | 7.341 | True |
| BSFB165 | 7.359 | True | 7.443 | True |
| BSFEC35 | 7.272 | True | 7.348 | True |

## Integrity and limitations

- Decoder errors: 0; malformed: 0; disconnects during W: 0.
- DK LED counter deltas: `{"crc": 2, "disc": 0, "io": 16, "queue": 0, "seq": 0}`.
- Sub remained SLAVED: True; main start-failure fraction: 0.000000.
- Batteries had already spent hours off-dock before this run. The endurance rows measure remaining capacity only.
- “Last observed data” is a span to the final UWB/IMU record, not proof of continuous service. Zero-progress intervals and later reconnections are retained in `OVERNIGHT_RUN_STATE.json`/`run_state.json`; notably BSF3C79 stopped its data plane early and later reappeared briefly.
- Data-plane cessation while BLE remains connected is classified as an application/data-path stall, not as battery death. The BLE column is the first closed connection epoch; it is not substituted for data lifetime.
- BSF1120 is the clearest connected-but-silent case: its last data was at 2.052 h while its BLE epoch remained open to 7.813 h. Root cause is UNKNOWN; no unattended reset or reconfiguration was attempted.
- The listener collector returned code 2 when deliberately terminated after fleet death; its final `summary.json` exists and the raw listener streams are complete through shutdown.
- Terminal cleanup was partial because no peers remained reachable. The 210 s quiet witness completed, and the main-beacon 100 ms restore passed independently.
- `analysis/SHA256SUMS` contains the exact hashes of raw evidence and derived products.
