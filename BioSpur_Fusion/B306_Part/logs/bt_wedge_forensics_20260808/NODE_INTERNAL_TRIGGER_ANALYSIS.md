# NODE_INTERNAL_TRIGGER_ANALYSIS — §7.2

The master command log is not the only source of asynchronous work on the
node. This section tests every node-internal event class that the 1 Hz
telemetry can date, against its own base rate.

## 1. Base rates (per node-hour, whole fleet, recomputed)

| class | N5 | N7 | N8 |
|---|---|---|---|
| `imu_hreset` (IMU chip reset) | 6.96 | 6.69 | 5.11 |
| `imu_hrecover_ok` | 7.00 | 6.69 | 5.13 |
| `imu_hrecover_fail` | 0 | 0 | 0 |
| `imu_i2c_err` | 1.63 | 2.15 | 1.28 |
| `imu_hrate` | 0.17 | 0 | 0.16 |
| `uart_restarts` / `uart_err` | 0 | 0 | 0 |
| `timer_wraps` | 0.83 | 0 | 0.60 |

The fleet-wide IMU-reset figure from prior work — 22–36 per board per 4.4 h —
is confirmed: 5.1–7.0 per board-hour is one every 8.6–11.8 minutes, and
`imu_hrecover_ok == imu_hreset` with `imu_hrecover_fail == 0` in **every run**.
The recovery layer masks the episode completely; it never surfaces as a fault.

`uart_restarts` and `uart_err` are **zero on every board in every run** —
the DWM→B306 link never restarted once in 107 board-hours. That axis is
empty, not merely negative.

## 2. Proximity to the four wedge onsets

Expected coincidence in a 10 s pre-onset window at the measured rate is
1.4–1.9 %.

| event | nearest `imu_hreset` before onset | ≤10 s | ≤60 s | nearest other |
|---|---|---|---|---|
| N7 BSF6C53 | 514.3 s | 0 | 0 | — |
| N8 BSFEC35 | 381.9 s | 0 | 0 | — |
| N8 BSF1120 | 286.3 s | 0 | 0 | **`imu_i2c_err` at 19.05 s** |
| N8 BSF44AD | none within 600 s | 0 | 0 | — |

**0 of 4 within 10 s. 0 of 4 within 60 s.** Exactly what chance predicts.
The axis is demoted, and it cost nothing to test — this was the cheap,
high-value check the brief asked for, and the answer is clean.

The one non-null: BSF1120 logged an `imu_i2c_err` increment 19.05 s before
onset, against a base rate of 1.28/node-hour (P(≤60 s) ≈ 0.021 per event).
One hit in four events has P ≈ 0.08. **n=1, not a finding**, listed so it is
not silently dropped. Note also that BSF1120's last inbound master command
was at 20.43 s — so its I²C error and its last command are 1.4 s apart, and
neither is closer to the onset than 19 s.

## 3. Resolution limit, stated explicitly

The telemetry is 1 Hz, so these timings are ±1 s. That is enough to detect
**enrichment** and is what was tested. It is **not** enough to claim causal
ordering, and none is claimed. Since no enrichment appears, the question of
ordering does not arise; if a future run shows enrichment, §14 records what
v45 would have to timestamp.

## 4. Clustering — per-board, not environmental

N8: 312 IMU-recovery episodes across 10 boards fall into 286 clusters when
grouped at 5 s (262 singletons, 22 pairs, 2 triples). Expected multi-board
coincidence by chance at 5.11/node-hour × 9 other boards × 10 s window is
≈0.13 companions per episode, i.e. ≈40 episodes in multi-clusters against 50
observed.

**The episodes are per-board independent, not fleet-synchronised.** There is
no shared environmental driver (temperature, supply, RF) behind them, which
also means they cannot explain a failure that hits one board at a time —
and they don't.
