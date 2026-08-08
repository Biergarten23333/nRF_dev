# CROSS_RUN_NECESSITY — §11

## 1. Exposure

Delivered board-hours = Σ over nodes of (last delivered − first delivered)
minus non-terminal stall time. Terminal stall time is post-mortem and is not
exposure. Rates use `(n−1)/(t_last−t_first)` with stale-prefix exclusion
wherever a rate is quoted per node.

| run | fw | master fw | conns | delivered bh | wedges |
|---|---|---|---|---|---|
| N5 | v43 | dk-v35 | 9 | 54.00 | 0 |
| N6 | v43 | dk-v35 | 10 | 0.11 | 0 |
| N7 | v43 | dk-v35 | 9 | 5.80 | 1 |
| N8 | v44 | dk-v36 | 10 | 47.21 | 3 |
| **total** | | | | **107.12** | **4** |

Pooled rate **1 per 26.8 delivered board-hours** (95 % Poisson CI on 4 events:
1.09–10.2 events ⇒ **1 per 9.8 to 1 per 98 bh**). N8 alone: **1 per 15.7**
(95 % CI on 3 events: 0.62–8.8 ⇒ 1 per 5.4 to 1 per 76 bh). The two intervals
overlap almost completely. **At n=4 the data cannot distinguish the N8 rate
from the pooled rate**, and no statement in this report may assume it can.

## 2. Necessity — is any condition present at every event?

| condition | present at all 4? | evidence |
|---|---|---|
| v44 instrumentation | **no** | N7 BSF6C53 is v43 |
| 10 connected nodes | **no** | N7 ran 9 |
| master scanning | **no** | BSFEC35 and BSF1120 wedged inside the at-capacity non-scanning window (20.10 reports/s); BSF44AD in the low scan tier (16.18); BSF6C53 in the high tier (18.42) |
| full capacity | **no** | as above |
| battery depletion | **no** | air ratio 0.96–1.10, link survived 615 s–4 h 38 min |
| weak RF | **no** | `crc_error` 0.4–1.8 per window pre-onset, ≈0 post; `nak` ≈0.005; RSSI −66 to −71 dBm at every reconnect attempt |
| board identity | **no** | four different boards, no repeat offender |
| diagnostic polling within 10 s | **no** | 3 of 4 had none (§7.1) |
| IMU-recovery episode within 60 s | **no** | 0 of 4 (§7.2) |
| LL procedure at onset | **no** | 0 of 4 (§5.2) |
| first boot segment of the run | **yes (4/4)** | but ~97 % of all board-hours are first-segment, so this is uninformative |
| `reset_reason` unchanged | yes (4/4) | same objection |
| producers healthy to the last millisecond | **yes (4/4)** | IMU `seq` +10 and UWB `sweep` +1 on every one of the last twelve records |
| link alive ≥ 10 min after onset | **yes (4/4)** | 615 s / 3 439 s / 16 071 s / 19 669 s |

**No environmental or configuration condition is necessary.** The only
universals are properties of the failure itself.

## 3. Rate modulation — what the runs can and cannot separate

The required sentence, verbatim:

> **N8 is simultaneously the only v44 run and the only 10-connection run; the
> v43-to-v44 rate difference cannot be separated from a capacity effect with
> this capture.**

It is worse than that, and the report should say so: N8 is *also* the only
dk-v36 run. Three candidate causes — node firmware v43→v44, 9→10
connections, master firmware dk-v35→dk-v36 — are perfectly confounded in a
single run. No re-analysis of this data can separate them.

What the data *does* constrain:

- **Diagnostic polling cadence does not modulate the rate in the expected
  direction.** N5 160.0 ops/node-hour → 0 events in 54.0 bh; N8 142.5 → 3 in
  47.2 bh. The lowest-cadence run has the highest rate.
- **Master scan activity is identical between N5 and N7** (360 vs 362
  `FUSION_SCAN_STARTED`/h) and cannot explain 0 vs 1.
- **Connection phase / anchor slot does not matter**: the four events sit at
  mod-50 ms phases 0–2, 32–34, 37–39 and 2–4, in both the high and low
  reports/s tiers.
- **`spacing_generation` was 1 throughout N8**; no spacing transition
  precedes any event.
- **Battery state modulates the *depletion* class strongly and the wedge
  class not at all**: all 11 depletion events fall in the last 27 minutes of
  N8, while the wedges are at run+31 min, run+98 min and run+277 min.

## 4. The one firmware-controlled comparison

N5 vs N7 are both v43, both dk-v35, both 9 nodes, same polling cadence, same
scan cadence: 0 events in 54.0 bh vs 1 event in 5.8 bh. Under the pooled
rate, P(0 | N5) = 0.133 — **unremarkable**, so this pair does not establish a
difference either. Everything that differed is listed in `LABEL_AUDIT.md`;
the only item that is not obviously irrelevant is that **BSF44AD, one of the
four victims, was absent from N5 and present in N7 and N8**.

## 5. Validation exposure required for v45

Given the pooled point estimate of 1 per 26.8 bh, the exposure needed to see
≥2 events with probability 0.9 is **λ ≥ 3.89**, i.e. **≈104 delivered
board-hours** ≈ 10.4 hours at 10 connections. Using the N8-only estimate
(1 per 15.7) it is **≈61 bh** ≈ 6.1 hours at 10 connections. Using the
pessimistic end of the pooled 95 % CI (1 per 98 bh) it is **≈381 bh** ≈ 38
hours at 10 connections.

**Plan for the pessimistic end.** A single overnight 10-node run is 60–80 bh
and would, at the point estimate, be expected to yield 2–5 events — but has
roughly a one-in-five chance of yielding fewer than two. Two such runs make
that unlikely. Battery life caps a single run at ~6 h at full rate, so the
realistic v45 validation is **three to six full-fleet runs**, and the
capture must survive a run in which nothing happens.
