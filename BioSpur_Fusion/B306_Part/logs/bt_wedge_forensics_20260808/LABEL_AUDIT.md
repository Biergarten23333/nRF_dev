# LABEL_AUDIT — recomputed labels vs the labels in circulation

Prior labels: **N5 = 0, N7 = 1, N8 = 3.**
Recomputed labels: **N5 = 0, N6 = 0, N7 = 1, N8 = 3.**

No discrepancy in the counts. Three discrepancies in the *details*, all of
which matter downstream.

## Discrepancy 1 — the third N8 event is identified

| | prior | recomputed |
|---|---|---|
| N8 event 3 | "identity/time never pinned" | **BSF44AD, onset_lower 2026-08-07 19:51:58, onset_upper +120 ms** |

Evidence: joint stall 5341.1 s to run end; master QoS for that connection
continued **3438.8 s (3421 records)** past onset; no reboot at onset; listener
air ratio **1.096** (its tag polled slightly *harder* after onset than before).
The depletion-contamination risk the brief flagged is real for the 20:49–21:16
cluster but not here — BSF44AD wedged 58 minutes before the first depletion
casualty and then held a supervised BLE link for another 57 minutes.

## Discrepancy 2 — the N5 zero is real but is **not** statistically loud

The brief anticipated that a true N5 zero "would make the process strongly
non-homogeneous". It does not, at this sample size.

| run | delivered bh | expected at pooled rate | observed | P(0) |
|---|---|---|---|---|
| N5 | 54.00 | 2.02 | 0 | **0.133** |
| N7 | 5.80 | 0.22 | 1 | — (P(≥1)=0.195) |
| N8 | 47.21 | 1.76 | 3 | — (P(≥3)=0.260) |

A 13 % outcome is unremarkable. **N5's zero is fully consistent with the same
homogeneous Poisson process that produced N7 and N8.** It neither rewrites
the conditions story nor supports one. Any claim that "v43 was fine and v44
broke it" cannot rest on N5.

That said, N5-vs-N7 remains the only *firmware-controlled* comparison (both
v43), and it is worth stating exactly what differed, because none of it is
resolved by the data:

| condition | N5 | N7 |
|---|---|---|
| node firmware | v43 | v43 |
| master firmware | dk-v35 | dk-v35 |
| nodes | 9 | 9 |
| duration | 6.00 h | 0.66 h |
| wall clock | 02:13–08:13 (night, unattended) | 11:46–12:26 (daylight, attended) |
| exposure | 54.0 bh | 5.8 bh |
| board set | no BSF44AD | no BSF31CC (see note) |
| polling cadence | 216 STALL_READ_START/h, 1224 COMMAND_TX/h | 218/h, 1241/h — **the same** |
| scan activity | 2160 `FUSION_SCAN_STARTED` (360/h) | 239 (362/h) — **the same** |

Note on the board set: N5 ran BSF1120 BSF31CC BSF3C79 BSF6C53 BSF8BC4
BSFAA61 BSFB165 BSFC2CC BSFEC35; N7 ran BSF1120 BSF3C79 **BSF44AD** BSF6C53
BSF8BC4 BSFAA61 BSFB165 BSFC2CC BSFEC35 — i.e. N7 swapped BSF31CC out for
BSF44AD. So **BSF44AD was absent from the only zero-event run** and is one of
the four wedge victims. With n=4 that is not evidence, but it is the single
cleanest follow-up: N5 is the only run BSF44AD sat out.

The two conditions most often blamed — diagnostic polling cadence and master
scan activity — are **measured here to be identical between N5 and N7**, to
within 1 %. Neither explains the N5-vs-N7 difference. What is left is
duration/exposure (54.0 bh vs 5.8 bh, which points the *wrong* way), the
board swap, and the time of day.

Per-board wedge counts across all runs: BSF6C53 ×1, BSFEC35 ×1, BSF1120 ×1,
BSF44AD ×1 — **four different boards, no repeat offender.** Board identity is
not the variable.

## Discrepancy 3 — the rate figures were both right, for different denominators

Prior reporting carried "one per 15.7 delivered board-hours (N8-era)" after
an earlier "26.5". Recomputed:

- N8 only: 47.21 bh / 3 = **1 per 15.74**
- pooled over N5+N6+N7+N8: 107.12 bh / 4 = **1 per 26.78**

Neither supersedes the other. Every future statement of the rate must name
its denominator; the two numbers are the same data sliced two ways, and the
gap between them is exactly the N5 zero.

## Cross-check against the non-authoritative sources

- `events.jsonl` `DATA_PLANE_SILENT` markers: agree on BSFEC35 and BSF1120.
  For **BSF44AD** and for N7's **BSF6C53** the marker exists but was never
  promoted to an event label in the reports — the detector finds both without
  reference to the markers.
- The v43/v44 stage traps contributed nothing: `rings.jsonl` is 0 bytes in
  every run and `CORPSE present=0` on every board. Consistent with §0.3;
  **no conclusion in this document rests on their silence.**

## What is *not* audited

Events outside N5/N6/N7/N8 (the earlier J-series BSF44AD capture, the
`aa61_stall_read_20260805` and `bearer_recovery_20260806` captures) are
context only, per the brief. They are not in the registry and do not enter
any rate.
