# LATENCY_PRECURSOR — §6

## Method

Per node, per boot segment, a robust Theil–Sen fit of
`master_reception_time ≈ a · node_generation_time + b` on healthy pre-event
data (`onset − 3600 s` … `onset − 120 s`), then the residual in ms. IMU and
UWB are fitted and reported **separately**, because the publisher's strict
`ctl > uwb > imu` drain order makes stream divergence informative.

Drift removal is not optional. Fitted `a` gives −9.2 to +14.1 ppm residual
slope *after* the fit absorbs the fleet's common-mode offset; over a 600 s
window an undetrended residual would move tens of ms, which is the same order
as the signal being looked for.

## Result — stationary, then a cliff, on all four

Median / p95 residual (ms) by window before onset:

| event | stream | −1800…−600 s | −600…−60 s | −60…−10 s | −10…−2 s | last 2 s |
|---|---|---|---|---|---|---|
| N7 BSF6C53 | IMU | 1.9 / 70.2 | −8.0 / 64.3 | −4.3 / 94.2 | −3.5 / 49.1 | −3.1 / 95.4 |
| | UWB | −0.5 / 46.2 | 1.0 / 51.1 | 0.0 / 79.6 | 6.1 / 76.9 | 2.9 / 98.6 |
| N8 BSFEC35 | IMU | 1.0 / 51.2 | −8.2 / 56.0 | −8.7 / 43.2 | −8.3 / 41.7 | −7.0 / 41.9 |
| | UWB | −0.2 / 38.0 | 0.2 / 63.3 | −2.5 / 56.9 | −2.7 / 36.1 | 7.4 / 27.6 |
| N8 BSF1120 | IMU | −5.0 / 31.8 | −4.4 / 38.9 | **−19.5** / 30.2 | −18.0 / 30.7 | −17.8 / 31.0 |
| | UWB | −0.4 / 35.2 | 1.6 / 36.1 | 2.4 / 23.6 | 3.1 / 24.5 | 3.2 / 23.0 |
| N8 BSF44AD | IMU | −4.3 / 71.6 | 1.1 / 55.0 | 6.5 / 57.9 | 6.9 / 57.1 | 7.6 / 57.6 |
| | UWB | −3.6 / 59.7 | 4.6 / 64.7 | 0.8 / 69.2 | −0.5 / 48.5 | 5.6 / 49.2 |

Matched same-window controls (UWB, median of per-node medians): 1.13, −0.74,
0.38, 0.78 ms at −60 s and 1.61, −1.42, 1.07, −4.22 ms at −10 s. Every event's
UWB residual is inside the control spread.

### The one candidate precursor, and why it is not one

BSF1120's IMU residual steps by about −15 ms between the −600 s and −60 s
windows. Tested against the fleet, same windows, same statistic
(median over the last 60 s minus median over the preceding 540 s):

| event | node step | control median | control |max| | n controls |
|---|---|---|---|---|
| N7 BSF6C53 | +3.83 ms | −3.88 ms | 299.5 ms | 8 |
| N8 BSFEC35 | −0.39 ms | +5.83 ms | 39.1 ms | 9 |
| **N8 BSF1120** | **−15.01 ms** | +6.58 ms | **38.2 ms** | 8 |
| N8 BSF44AD | +5.66 ms | +6.02 ms | 43.5 ms | 7 |

BSF1120's −15 ms step is **less than half the largest step among the healthy
controls in the same window**. It is not an outlier. Per §15 rule 3, outlier
judgements are intra-fleet-relative, and by that rule this is noise.

## Verdict

**Stationary-then-cliff on all four events. No precursor ramp in either
stream, in any window, that is distinguishable from the fleet.** No
stream-specific queueing, no fleet-common effect.

This is what the pre-registration expected under H1/H2/H3 and is what it
expected *not* to see under H4.

## Backlog arithmetic

Legal per `COUNTER_SEMANTICS.md` (node-side terms only). At the last delivered
`FUSION_QUEUE` record of each event:

| event | Σ enq | publisher_count | drop_unsub | Σ q_drop | Σ abort | residual |
|---|---|---|---|---|---|---|
| N7 BSF6C53 | 79 589 | 79 269 | 319 | 0 | 0 | **1** |
| N8 BSFEC35 | 62 248 | 61 951 | 296 | 0 | 0 | **1** |
| N8 BSF1120 | 187 952 | 187 787 | 161 | 3 | 0 | **1** |
| N8 BSF44AD | 524 929 | 524 719 | 209 | 0 | 0 | **1** |

**Exactly one record outstanding on all four** — the one inside the call at
sampling time. There was no backlog anywhere in the node at the last
observable instant. `q_hwm_*` at onset: 1/1/4 (BSF6C53), 15/3/4 (BSFEC35),
7/2/4 (BSF1120), 1/1/4 (BSF44AD) — those are lifetime maxima, and the live
depths from the stall reads are `q=0/0/0` on all four.

`publisher_max_us` caveat, binding and applied: 1467 µs, 317 683 µs,
250 953 µs, 1611 µs at onset. A permanently blocked final call never updates
this field, so **none of these values excludes anything**. What they do show
is the fleet context in `WEDGE_HYPOTHESIS_SCORECARD.md` §2: calls of
100–400 ms are routine on healthy boards and always return.
