# L3 — report corrections and the latency measurement

**Offline only.** Nothing was flashed, commanded, connected or disconnected. The
N8 run continued untouched throughout; BSF1120 and BSFEC35 were not addressed.
All inputs were files already on disk.

---

# PART A — corrections applied to `WEDGE_LOCALISATION.md`

## A1 — §2.1 was factually wrong, and it was load-bearing

**Was:** the Bluetooth host's own RX pool is not among the eight sampled pools and
the app cannot see it; "that distinction turns out to be the whole answer (§6)".

**Is:** `20588eb5` **is** `hci_rx_pool`. Verified independently rather than
accepted — `pool_name_hash()` (`firmware/src/main.c:498`) is FNV-1a/32, so the
hashes can be inverted by trial:

| hash | pool | reported | cross-check |
|---|---|---|---|
| `20588eb5` | **`hci_rx_pool`** | **10/10** | `BT_BUF_RX_COUNT = MAX(EVT 10, ACL 6) = 10` ✓ |
| `11597b73` | `acl_tx_pool` | 8/8 | `BT_BUF_ACL_TX_COUNT=8` ✓ |
| `858969d7` | `att_pool` | 8/8 | |
| `a14875f8` | `discardable_pool` | 3/3 | |
| `39b3fc03` | `hci_cmd_pool` | 2/2 | |
| `2de570ea` | `fragments` | 1/1 | `BT_CONN_FRAG_COUNT=1` ✓ |
| `27b70977` | **INSUFFICIENT** | 1/1 | not identified |
| `ef427c73` | **INSUFFICIENT** | 4/4 | not identified |

Three counts independently match their Kconfig values, which is what makes the
identification safe rather than suggestive. Two remain unidentified and are
recorded as `INSUFFICIENT` rather than guessed.

The pool `bt_buf_get_rx()` allocates from **was being sampled at 1 Hz the entire
time and read 10/10 to the last record.**

## A2 — the correction hands §6 a numeric constraint, replacing an open question

`avail == low_water == max` means `hci_rx_pool` **never once dipped to 9** in
either board's life. So the §6 mechanism requires the pool to go **10 → 0 and
never recover, entirely inside the final sub-second.**

At ~20 connection events/s, a one-buffer-per-event drain takes ≈ **0.5 s**.
Against 1 Hz sampling that has ≈ **50 %** chance of being caught by some sample;
**both boards missing it is ≈ 25 %.**

**Mild evidence against a progressive drain**, mildly favouring block seizure or
a different mechanism. One in four is not small, so this does **not** refute §6 —
it is a constraint every candidate must now clear. The old text ("which
allocation is unproven") has been replaced by this.

## A3 — §8 item 2 replaced: sample the *holder*, not the free count

Sampling the host RX pool was already being done (A1), so the old item 2 was a
no-op. The real gap is a circularity §6 never named:

> If `rx_work_handler()` is genuinely idle, it is not holding RX buffers — **so
> who is?** `sdc_rx` blocking in `bt_buf_get_rx(..., K_FOREVER)` requires an empty
> pool; an empty pool requires a holder. A free count cannot name one.

First candidate `conn->rx` (incomplete ACL reassembly, holds a buffer
indefinitely) — but at `BT_MAX_CONN=1` there is at most one, so it cannot empty a
pool of 10 alone and is not the whole story.

## A4 — §8 item 3's predicate covered only the mechanism that happened to win

`bsf_bt_stage_seq` frozen catches the idle thread. **It does not catch a
livelock.** Hypothesis (2) is refuted for *these two events*; pinning the
predicate to that outcome is the v43/v44 error committed a third time.

Replaced with the mechanism-independent form, same cost:

> **producer counters advancing AND exported records stopped for 20 s ⇒ wedge**

The standing objection (readmits producer, RF, scheduling, central, application
as false-positive sources) is answered by the protection the dwell predicate
already leans on: a vanished central disconnects at the **4000 ms** supervision
timeout, clearing subscriptions, dropping `armed` and resetting the counter, far
inside 20 s. Noted that `test_bt_stage_contract.py` §3 must be relaxed to permit
this exact form and no other.

## A5 — two small ones

- §2.7's phase table: **BSF1120's row is now explicitly excluded from the
  alternation argument.** Its phase was inferred from its tier, i.e. from the
  conclusion it would support. The eight measured phases alternate perfectly
  without it; the row stays only to mark the gap.
- The duplicated `## 9. State ledger` heading is removed.

---

# PART B — end-to-end latency

## B0. What was measured

`latency = (master_ms × 1000 − node_TIMER2_µs)`, detrended, minus its window
minimum. Node capture instant is `frame_us` for UWB and `base_us` for IMU;
`master_ms` is stamped by the DK at BLE reception, so USB/CDC jitter is excluded.

**3 228 805 log lines → 2 709 451 records** across the two windows.

| window | definition | span |
|---|---|---|
| `at_capacity` | 15:14:57 → 17:28:20, ten connections, master **not** scanning | 132.7 min |
| `degraded` | after 17:28:20, master scanning, tiers A/B | 50.5 min |

**Measurement floor is 1 ms** — `master_ms` has millisecond resolution.

## B1. Drift removal — and the assumption in the brief that did not hold

The brief expected per-node slopes "small, stable and consistent with ±20 ppm",
i.e. scattered about zero. **They are not.** Every node sits near **−33 ppm**:

```
fleet median  -33.22 ppm     MAD 0.88 ppm
kept spread   -35.15 .. -30.23 ppm   (4.9 ppm total, 30 groups)
```

Ten independent crystals do not cluster inside 5 ppm. This is a **common-mode
reference offset** — the DK's `k_uptime` clock against the nodes' TIMER2 — plus a
per-node spread of only about **±2.5 ppm**, which is *better* than the ±20 ppm
assumed. The nodes hold HFXO and they agree with each other well.

**This changed the method.** A zero-relative outlier test rejected all ten good
nodes and kept only the bad fits — exactly backwards. The criterion used is
fleet-relative: **median ± 3·1.4826·MAD (= −33.22 ± 3.91 ppm), plus a 20-minute
span floor.** Four groups excluded:

| excluded | slope | reason |
|---|---|---|
| `BSFEC35 at_capacity IMU` | −17.78 | wedged at 15:46, 30.5 min span, fit unreliable |
| `BSFEC35 at_capacity UWB` | −25.92 | same |
| `BSF31CC degraded IMU` | −25.14 | drift outlier |
| `BSFAA61 degraded IMU` | −27.66 | drift outlier |

Drift was fitted on the **lower envelope** (60 s bins, minimum per bin, line
through the minima), not plain least squares — a plain fit is dragged by the
queueing distribution, which is the signal.

**Residual uncertainty, stated because it bounds what can be claimed.** A node's
IMU and UWB fits should give the same slope; their disagreement measures the
method's error. Worst kept cases: BSF8BC4 degraded 3.45 ppm, BSF44AD degraded
2.16 ppm; most are under 1 ppm. Over the 50.5 min degraded window 3.45 ppm ≈
**10 ms** of residual tilt; over the 132.7 min baseline 1 ppm ≈ **8 ms**.

> **Anything below ~10 ms in this measurement is not resolvable.**

## B2. Absolute latency — and why the minimum method under-reports

`lat = detrended_offset − min(detrended_offset)`. The minimum is a record that
happened to be queued just before a connection event, so ≈ zero queueing. **The
residual is the true minimum transport time, which is small but not zero, so
every absolute figure below is a lower bound on true end-to-end latency.**

For IMU there is a second reason it is a lower bound: `base_us` is the *first* of
ten samples spanning 45 ms, so a constant ≈45 ms of batching sits in the minimum
and is subtracted away. **IMU and UWB absolute numbers are therefore not
comparable to each other in the "time since the physical event" sense.** They are
comparable as *queueing above best case*, which is what all comparisons below use.

## B4. The dose–response check — run first, as instructed

Tier B loses more connection events (16.18/s vs 18.42/s), so tier B must wait
longer. **Both streams: HOLDS.**

| stream | tier A p95 | tier B p95 | Δ |
|---|---|---|---|
| UWB | 87.53 ms | 112.49 ms | **+24.96 ms** |
| IMU | 92.08 ms | 118.58 ms | **+26.50 ms** |

Both far exceed the ~10 ms resolution floor from B1.

**And the negative control is cleaner than the positive one.** The same tier
labels applied to the at-capacity window — where scanning is off and the tiers
should not exist — produce nothing:

| stream | tier A p95 | tier B p95 | Δ |
|---|---|---|---|
| UWB | 83.41 ms | 81.53 ms | −1.88 ms |
| IMU | 108.27 ms | 110.14 ms | +1.87 ms |

Under 2 ms, and with **opposite signs** between streams — i.e. noise. The same
partition of the same boards separates by ~25 ms when scanning and by nothing
when not. **The anchor-phase model of §2.7 is confirmed with a matched control.**

## B3. UWB and IMU reported separately — the sacrificial class

At capacity, drain priority is `ctl > UWB > IMU`:

| | UWB | IMU | IMU penalty |
|---|---|---|---|
| p50 | 29.82 ms | 54.10 ms | **+24.28 ms** |
| p95 | 82.57 ms | 109.10 ms | **+26.53 ms** |
| p99 | 170.70 ms | 199.79 ms | +29.09 ms |

**IMU is worse, as designed, and the price of being designated sacrificial is
about 26 ms at p95.** That is now a measured number rather than an intention.

## B5. Full tables

```
stream window        tier  nodes   records     p50     p95     p99
UWB    at_capacity   ALL       9    579615   29.82   82.57  170.70
UWB    at_capacity   A         5    314188   29.81   83.41  190.32
UWB    at_capacity   B         4    265427   29.83   81.53  146.18
UWB    degraded      ALL       8    202026   35.80  100.01  140.33
UWB    degraded      A         4    101013   31.16   87.53  120.84
UWB    degraded      B         4    101013   40.45  112.49  159.82
IMU    at_capacity   ALL       9   1391063   54.10  109.10  199.79
IMU    at_capacity   A         5    754046   52.70  108.27  219.75
IMU    at_capacity   B         4    637017   55.85  110.14  174.85
IMU    degraded      ALL       6    363642   40.57  105.33  149.51
IMU    degraded      A         3    181819   37.73   92.08  123.62
IMU    degraded      B         3    181823   43.41  118.58  175.40
```

### The window-to-window comparison is confounded — do not use it

| stream | at capacity | degraded | Δ |
|---|---|---|---|
| UWB | 82.57 ms | 100.01 ms | +17.44 ms (+21.1 %) |
| IMU | 109.10 ms | 105.33 ms | **−3.77 ms (−3.5 %)** |

The two streams **disagree in sign**, and that is the tell. The windows differ in
*two* ways, not one: scanning turned on **and** the delivering fleet fell from ten
boards to eight (BSFEC35 gone, BSF1120 wedged). Fewer nodes means less contention
for the master's radio, which pushes latency *down*, opposing the scanning effect.

So:

- **UWB's +17.44 ms is a lower bound** on the scanning penalty — it survived an
  opposing confound.
- **IMU's −3.77 ms is `INSUFFICIENT`.** It is smaller than the ~10 ms resolution
  floor *and* confounded. It is not evidence that scanning helps IMU.
- **The tier contrast (B4) is immune**, because both tiers sit in the same window
  with the same node count and the same scanning. That is why B4 is the result
  and this table is not.

### Against the historical 52.0 ms

The at-capacity p95 is **82.57 ms (UWB)** and **109.10 ms (IMU)** — both worse
than the **52.0 ms** quoted on 2026-07-25. That comparison is **not valid**, and
saying so is more useful than the number:

- 52.0 ms was measured below capacity, so it carried scanning degradation — which
  inflates it.
- It was also measured with far fewer connections, which deflates it far more.
- Net direction is unknown, so **52.0 ms is not a baseline for a ten-node fleet
  and should not be quoted as one.** The at-capacity figures here are the first
  latency numbers this project has that were taken at full capacity.

> **The degradation is common-mode, so comparing two degraded runs against each
> other cannot detect a fleet-wide regression.** This is why the at-capacity
> window matters more than any single number in it, and it applies to every
> future comparison, not only this one.

## B6. What this does not settle

**It does not make the 20-node case.** Twenty nodes at a 50 ms interval needs
**2.5 ms spacing — a different regime** — and everything here was measured at
5 ms. What has changed is only that "exactly full, therefore no margin" is
replaced by:

- a measured throughput headroom (≥1.94 notifications/event, ceiling not found), and
- a measured latency cost for spending it (~25 ms p95 per ~2.24 lost events/s), and
- a named open question: **the 2.5 ms regime is unmeasured.**

Any 20-node argument must measure that regime, not extrapolate this one.

---

# Evidence index

Full list in `L3_SHA256SUMS.txt` (regenerate: `sha256sum *.md *.json *.csv *.log *.py`).
`L3_SHA256SUMS.txt` itself is necessarily not in its own list.

| file | sha256 (first 16) | bytes |
|---|---|---|
| `L3_REPORT.md` | `4b1558c8d6aaebed` | 12 843 |
| `WEDGE_LOCALISATION.md` | `9fc97ddf6440b290` | 34 687 |
| `ACTION_LOG.md` | `ead1902f0a181b19` | 4 381 |
| `EXPORT_README.md` | `527295d6da36c122` | 3 257 |
| `latency_summary.json` | `34748c8ead2ec22e` | 9 829 |
| `latency_by_node.json` | `511a516a0de735dd` | 7 203 |
| `baseline_at_capacity_summary.json` | `ed5b647d8e6fbfae` | — |
| `baseline_at_capacity_qos.csv` | `ce7bd2913cf21d82`¹ | 7 240 823 |
| `control_comparison.json` | `eefc41b1baf99bc2` | — |
| `pre_reconnect_snapshot.json` | `14ccbbfcc1c3e41d` | 241 573 |
| `qos_BSF1120_with_controls.csv` | `07dd935a261a8e92` | 5 246 915 |
| `qos_BSFEC35_with_controls.csv` | `8a9a4e75008df393` | 10 197 141 |
| `onset_pm30s_BSF1120.log` | `2b423d5c73f84f64` | 7 816 377 |
| `onset_pm30s_BSFEC35.log` | `b980fae6a6e7eccc` | 8 736 137 |
| `latency.py` | `8e1e487d1f240bd1` | 9 400 |
| `export_wedge.py` | `2447607326da4144` | 5 356 |

¹ truncated display; the authoritative value is in `L3_SHA256SUMS.txt`.

Both scripts are copied into this directory alongside their outputs and are
reproducible against `I_RUN/fusion_h0*.log`. Note that the run is still writing,
so re-running `latency.py` later will include more `degraded`-window data and
will not reproduce these hashes; the JSON outputs are the frozen record.

---

**BANNER: L3 COMPLETE — PART A APPLIED, PART B MEASURED. RUN UNTOUCHED. STOP.**
