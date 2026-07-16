# Offline Gateway Feasibility — Passive Sweep Reconstruction from the 7-Listener Capture

**Dataset:** `logs/overnight_power_position_high_20260715/` — 3 wand tags
(BS9336/BS955A/BSCCF4 = on-air 0xB102/03/04) × 8 anchors (0xA100–0xA107),
all 7 listeners (LB, LE, LF, LA, LCCF4, L9336, L955A) recording at 460800 baud,
09:30:07–10:36. Read-only; all outputs in this directory.
**Analysis:** [pipeline.py](pipeline.py), [figures.py](figures.py) →
[results.json](results.json), [figures/](figures/). Every threshold carries an
inline provenance note in the scripts.

---

## BOTTOM LINE

The gateway's *frame-capture* half — the part this study validates — **does not
work with the current listener firmware, and the failure is not RF, not
geometry, and not fixable by adding gateways.** Every one of the 7 spatially
diverse listeners captures the **same ~2 of 8** anchor responses per sweep
(ranks 1 and 6 only), **9/9 = 0.00%**, and the **union of all 7 still reaches
only 1.93/8 anchors and 0.00% full sweeps.** The cause is a firmware throughput
limit (per-frame diagnostic reads + a 4 kB CIR dump, all in one polled loop that
also owns RX re-arm, on a single-buffered DW1000). This is *good* news for the
reverse-broadcast design, because it kills the wrong architecture early:

> **Do not have the gateway reconstruct ranges from the 8 responses it hears.**
> It cannot hear them. **Put every timestamp the gateway needs into the FINAL
> frame**, so the gateway must catch exactly **one** frame per sweep, at the
> quiet slot tail, where single-frame capture is 73–82 % today and →95 %+ with
> a stripped gateway firmware. That reduces the whole problem to *P(catch
> FINAL)*, which two gateways drive toward ~99 %.

Full end-to-end range reconstruction from these recordings is impossible by
design (t1/t4 are tag-local and never go on air — the reason the FINAL must
exist); this was expected and is not counted as a failure.

---

## DECISIONS

### D1 — Sweep-capture completeness (the go/no-go risk numbers)

Per-listener, **conditioned on the listener catching the poll** (poll-anchored
sweeps; no denominator ambiguity):

| listener | mean anchors / sweep (of 8) | 9/9 full | rank-1 | rank-6 | every other rank |
|---|---|---|---|---|---|
| LB | 1.77 | 0.00 % | 82.8 % | 80.9 % | ≤ 5.9 % |
| LE | 1.78 | 0.00 % | 84 % | 82 % | ≤ 6 % |
| LF | 1.75 | 0.00 % | 81.2 % | 78.9 % | ≤ 6.0 % |
| LA | 1.77 | 0.00 % | 83.0 % | 81.4 % | ≤ 5.5 % |
| LCCF4 | 1.78 | 0.00 % | 83.3 % | 81.6 % | ≤ 5.7 % |
| L9336 | 1.78 | 0.00 % | 84.1 % | 82.4 % | ≤ 5.5 % |
| L955A | 1.78 | 0.00 % | 83.5 % | 81.8 % | ≤ 5.6 % |

**Joint (union) coverage, greedy best-first (LE → L9336 → LF → L955A → LB → LA → LCCF4):**

| k gateways | mean anchors/sweep | all-8 anchors | poll captured |
|---|---|---|---|
| 1 | 1.30* | 0.00 % | 73.1 % |
| **2** | **1.73*** | **0.00 %** | **94.0 %** |
| 3 | 1.84* | 0.00 % | 96.6 % |
| 7 | 1.93 | 0.00 % | 100 % |

\* union means are over *all* master sweeps (including ones the listener missed →
masked to 0); the 1.77 per-listener figure is conditioned on catching the poll.

**The 2-gateway anchor-diversity value is essentially nil** (1.30 → 1.73 anchors,
still 0 full sweeps) because the misses are **temporally common-mode**: all
listeners run the same firmware and the same sweep triggers the same busy
window, so they all miss the same ranks. Anchor 0 (rank 0, at 1200 µs right
after the poll) is caught **0.0 %** by *any* listener across the whole hour.
Spatial diversity recovers **polls** (73 % → 94 % at k=2) but not **responses**.
See [fig_per_anchor_capture.png](figures/fig_per_anchor_capture.png) and
[fig_union_vs_k.png](figures/fig_union_vs_k.png).

**Implied FINAL-frame data-loss (the number that actually matters).** Per-frame
capture *when the listener is positioned to hear a frame* is 73 % (poll) to 82 %
(rank-1/rank-6) under the current firmware — this is the best available estimator
for **P(catch FINAL)**. So, *with today's firmware*: 1 gateway loses **18–27 %**
of sweeps' FINALs; 2 gateways help only weakly because the losses are correlated.
*With the stripped gateway firmware D5 requires* (misses become RF-driven and
independent across sites): single-frame capture rises toward the tag's own
~95–97 %, so 1 gateway loses ~3–5 %, and 2 gateways lose **< 0.3 %** per sweep.

**Per tag (D1.3):** mean anchors/sweep = 1.78 / 1.75 / 1.80 for tags 2/3/4 — no
meaningful difference; CCF4's flipped antenna does not move *capture* (capture is
firmware-bound, not RF-bound).

**Time stability (D1.4):** capture is flat at ~1.3 (1-gw) / ~1.7 (2-gw) across
the whole hour; see [fig_completeness_timeline.png](figures/fig_completeness_timeline.png).
Overlaying the 30 movement events (operator walking, `movement_events.json`,
32 s total): mean anchors in movement windows 1.18 vs 1.31 quiet — a marginal,
noise-level dip. **A person walking through does not degrade capture**, precisely
because the bottleneck is firmware, not RF occlusion. (This is the miniature
court-deployment answer: capture completeness is robust to people moving; range
*accuracy* under occlusion is a separate question, out of scope here.)

### D2 — Payload decode from current logs? **NO.**

The listener records **no raw payload bytes** — confirmed in firmware
([UWB_listener/src/main.c](../../UWB_listener/src/main.c), `struct lrec` +
`print_lpd`/`print_lrd`) and in every CSV. Each per-frame record carries only
`{rx_ts_lo32, carrier_integrator, src, dst, anchor/tag id, seq byte, frame_len,
rxdiag scalars}`. The anchor reply-timing fields a third party needs
(`poll_rx_ts` @ bytes 10–13, `resp_tx_ts` @ bytes 14–17 of the 36-byte V3
response) are **never parsed or logged**. Gap list in D5.

### D3 — Consistency verdict: partial checks **PASS**; the range check is **blocked**.

What is derivable from the timestamps *does* check out:
- **Response order == responder rank == anchor_id** (verified): measured
  poll→response device-time offset = `GUARD 1200 + rank·SPACING 1000 µs` with
  **IQR = 0 µs** (n = 232 k for rank 1). Residual grows a steady ~26 µs/rank,
  i.e. the true on-air spacing is **1025.6 µs, identical across all 7 listeners
  to < 1 µs** (1000 µs programmed + frame airtime). The listener timestamps are
  fully self-consistent with the known TDMA structure.
- **Blocked:** recomputing the tag's TR range from a hypothetical FINAL is
  impossible — t1 (poll TX) and t4 (response RX at the tag) are tag-local and
  absent from every recording, *and* the anchor reply fields (t2/t3) sit in
  response payloads the listener never logs. Both halves are missing. This is
  the by-design gap the FINAL frame closes.

### D4 — FINAL frame spec v0 — see full section below. Fits the 800 µs slot tail with > 500 µs margin.

### D5 — Firmware work list for the gateway role (ordered) — see below.

---

## TASK 0 — Listener log format inventory

**Per-frame records exist** (not just aggregates). Polls → `lpd.csv`,
responses → `lrd.csv`, one row per accepted frame. Grammar (firmware
`printk` in [main.c](../../UWB_listener/src/main.c) lines 372–437, CSV-ified by
[scripts/capture_uwb_poll_listener.py](../../scripts/capture_uwb_poll_listener.py)):

```
LPD header: host_elapsed_s,host_epoch_s,ver,listener_id,near_anchor_id,
            listener_t_ms,accepted_polls,poll_seq,tag_id,src,dst,rx_ts_lo32,
            carrier_integrator,fp_index,fp1,fp2,fp3,cir_pwr,rxpacc,std_noise,
            frame_len,poll_mask
LPD ex:     …,1784100607.348695,1,255,255,70977352,492934,8,2,0xb102,0xffff,
            1320948435,-2346,47831,2544,4154,3151,1558,121,32,17,0xff
                       │           │      │ │ │      │      │              │   │
   host wall clock (shared)  poll# seq  tag src   dst   rx_ts_lo32(device) len  mask
```
```
LRD header: …,accepted_resps,resp_seq,anchor_id,src,dst,rx_ts_lo32,
            carrier_integrator,fp_index,fp1,fp2,fp3,cir_pwr,rxpacc,std_noise,frame_len
LRD ex:     …,835582,97,1,0xa101,0xb102,1465144419,-2666,47735,…,36
                        │  │      │      └ device RX ts (lo32)   └ 36 = V3 response
                     anchor  src(anchor) dst(tag = which sweep)
```

Field semantics (with what each is good for):
- **`rx_ts_lo32`** — low 32 bits of the 40-bit DW1000 RX timestamp; 1 tick =
  15.65 ps, **wraps every 67.2 ms**. Exact, and the basis for intra-sweep timing.
- **`host_epoch_s`** — host `time.time()` at UART-parse time, **shared across all
  7 listener processes** (one capture box). Cross-listener jitter for the same
  poll is **median 0.1 ms, p95 3.6 ms** (measured) → safe for cross-listener
  alignment.
- **source ID** — derived from the on-air 16-bit `src` address: `anchor_id =
  src − 0xA100` (0–7, **== responder rank**), `tag_id = src − 0xB100` (2/3/4).
- **`poll_seq` / `resp_seq`** — the 802.15.4 sequence *byte*. The poll's seq is
  set by the tag (identical at every listener → a cross-listener sweep key). The
  **response's seq is the anchor's own TX counter, NOT the poll's** — so a sweep
  cannot be reassembled by seq-matching; it is reassembled by dst-tag + device-time
  window (method below).
- **rxdiag scalars** — `fp_index, fp1/2/3, cir_pwr, rxpacc, std_noise,
  carrier_integrator` present; raw CIR lives separately in `lcird/lcire/lcirm.csv`.

**Poll logged?** Yes (`lpd.csv`). **All 8 responses logged?** Only the ~2 the
listener actually receives (`lrd.csv`) — see D1. **Anything dropped by design?**
**No.** `ring_drops = 0` and the `accepted_polls`/`accepted_resps` counters are
**contiguous** across the whole capture, so every *accepted* frame is logged. The
missing responses are **RX-buffer losses** (never received while RX was disabled
during the busy window) — upstream of the ring, invisible to `ring_drops`.

> **RAW PAYLOAD BYTES ARE NOT LOGGED.** This is **firmware gap #1**. It degrades
> Task 2 to not-possible and blocks half of Task 3.

---

## TASK 1 — Sweep capture completeness (method)

**Sweep model.** Tags poll at 100 ms/tag (10 slots × 10 ms, 10 Hz —
[broadcast_tdma.c](../../src/broadcast_tdma.c)); measured median inter-poll =
100.5 ms. A sweep = 1 poll (dst 0xFFFF) + up to 8 responses (dst = tag) at
`GUARD 1200 + rank·1000 µs` after the poll ([ss_twr_resp.c](../../src/ss_twr_resp.c)).

**Reconstruction.** Per listener, per tag: for each captured poll, gather
responses whose **exact device-time delta** `(resp.rx_ts − poll.rx_ts) mod 2³²`
∈ (0, 12 ms] — the 12 ms window covers rank-7 (8.2 ms) + margin and is far below
the 100 ms cadence, so assignment is unambiguous. Device time is exact and
immune to UART-drain jitter. **Cross-listener master list**: cluster all
captured polls per tag by host wall-clock (30 ms gap; justified by the 3.6 ms
p95 jitter vs 100 ms cadence). Validated: the 7-listener host-cluster count
(54,569) equals the pipeline master exactly.

**Denominator honesty.** The listener union caught the poll for **54,569**
sweeps; the tag-side TR ground truth logged **64k–71k** real tag·sweeps over the
run. So the 7-listener union misses the poll for **~15–23 %** of all real sweeps
(they were missed by *every* listener) — a second, independent symptom of the
throughput limit. Per-listener poll capture is ~73 % against the union
denominator (≈ 60 % against TR).

Numbers in D1; figures in [figures/](figures/).

---

## TASK 2 — Response payload decode: **NOT POSSIBLE WITH CURRENT LOGS**

No payload bytes are logged (D2). The information a third party needs —
`poll_rx_ts` (V3 response bytes 10–13) and `resp_tx_ts` (bytes 14–17), plus the
embedded diag block — is present *on air* in the 36-byte V3 response
([uwb_ss_twr_shared.h](../../include/uwb_ss_twr_shared.h) `UWB_MSG_RESP_*`) but
never captured to the log. To enable Task 2 the gateway firmware must log, per
frame, **the raw payload bytes + the frame RX timestamp**.

**Added log bandwidth** (does it fit at 460800 baud?). A raw 36-byte response as
hex + rx_ts ≈ 90 extra chars/frame. At the *real* on-air rate (poll + 8 resp per
sweep, 3 tags, 10 Hz = 270 frames/s) that is ~24 kchar/s ≈ **530 kbit/s with
framing — over the 460 kbaud link.** Text hex does *not* fit. A **binary,
payload-only record** (drop the 15 rxdiag columns; keep 8 B rx_ts + ~20 B payload
≈ 28 B/frame) is ~60 kbit/s — fits with headroom. Conclusion: logging payloads is
feasible **only** alongside the D5 firmware diet (binary records, no per-frame
diag, no CIR). This is the same rewrite that fixes the capture bottleneck.

---

## TASK 3 — Consistency cross-check

Possible and **passing**: response order ↔ rank ↔ anchor_id, and the 1025.6 µs
inter-response spacing, both verified to sub-µs across all listeners (D3).
Possible and passing: sweep cadence 100.5 ms ↔ configured 10 Hz. **Blocked**: any
range recompute — needs t1/t4 (tag-local, never on air) and t2/t3 (in unlogged
payloads). No partial range check survives the double gap.

---

## TASK 4 — FINAL frame specification v0

**Design pivot (the key result of this study).** Because no gateway can hear all
8 responses (D1), the FINAL must **not** rely on the gateway reassembling
response payloads. Instead the tag — which *did* hear the responses and *did*
compute per-link timing — **relays every quantity per link inside the FINAL**, so
a gateway that catches the single FINAL frame can compute all 8 ranges. This
turns "capture 9/9" into "capture 1/1".

**Fields** (payload after the standard 9-byte 802.15.4 MAC header; new code
`0xE2`):

| field | width | justification |
|---|---|---|
| `code = 0xE2` | 1 B | new FINAL message type |
| `sweep_seq` | 2 B | 16-bit sweep id (8-bit poll seq wraps every 25.6 s → ambiguous); dedup key |
| `anchor_rx_mask` | 1 B | which of 8 links are valid (tag heard ~97 %/anchor, usually 0xFF) |
| `t1` poll TX ts | 5 B | 40-bit tag poll-TX (shared reference for all links) |
| per link ×8: `Δt4_i` (tag RX of resp_i, delta from t1) | 4 B ×8 = 32 B | sweep < 10 ms < 2³⁰ ticks → 32-bit delta suffices |
| per link ×8: `reply_i = t3_i − t2_i` (anchor turnaround) | 4 B ×8 = 32 B | the only anchor-clock quantity range needs; from response payload the tag parsed |
| `t5` FINAL TX ts | 5 B | tag's own programmed FINAL TX — enables the CFO-robust variant |

Total payload ≈ **78 B** (frame ≈ 89 B with MAC+CRC). A leaner variant drops the
per-link `reply_i` when anchors keep the fixed programmed delay (`reply_i =
GUARD + rank·SPACING`, known) → payload ≈ **45 B**. Cheapest variant relays the 8
tag-computed ranges (16 B) — smallest, but forfeits raw-timestamp defensibility.

**Airtime (CH5/PRF64/6.8 Mbps/PLEN128)** — SHR 138 µs + PHR 21 µs + data:
- relay-range 30 B → **198 µs** · v0 56 B → **232 µs** · full relay-ts 86 B → **271 µs**

**Slot budget fit.** Slot = 10 000 µs. Last anchor (rank 7) TX at 8200 µs, ends
~8600 µs. Schedule FINAL in the "rank-8" position at **9200 µs** → **800 µs tail**
to the slot boundary. FINAL airtime ≤ 271 µs ⇒ **> 500 µs margin.** No change to
the anchor sweep; the FINAL is one extra tag TX at sweep end.

**What the gateway computes per link** (single-sided TWR, relayed):
```
ToF_i = ½ · [ (t4_i − t1) − (t3_i − t2_i) ] · 15.65 ps  − (antenna delays)
range_i = c · ToF_i          # t1,t4_i from FINAL; (t3_i−t2_i)=reply_i from FINAL
```
`(t4_i − t1)` is a tag-clock interval, `reply_i` an anchor-clock interval; the
clock *offset* cancels, the *frequency* offset does not fully cancel in SS-TWR.
**CFO-cancellation win:** carrying `t5` (FINAL TX) lets the gateway measure the
tag's clock rate over the sweep (`(t5 − t1)` tag-clock vs the gateway's own
`g_final − g_poll`) and rescale `reply_i`, recovering the second-order CFO
cancellation of DS-TWR without the dumb anchors needing to receive anything —
the reason `t5` is in the frame.

**Failure handling.**
- *FINAL lost → whole sweep lost.* Quantified in D1: ~3–5 % per gateway (fixed
  fw), **< 0.3 % for 2 gateways** (independent RF losses). This is the dominant
  single-point-of-failure and the reason to run **two** gateways.
- *Partial anchor mask.* `anchor_rx_mask` marks valid links; gateway ranges only
  those. Tag hears ~97 %/anchor (TR `per_anchor_valid_pct`), so mask is normally
  full.
- *Duplicate capture by 2 gateways.* Dedup key = **(tag_id, sweep_seq)**. Both
  gateways relay identical tag timestamps → identical ranges; server keeps one,
  or keeps both FINAL RX times for gateway-side TDOA as a bonus.

---

## D5 — Gateway firmware work list (ordered)

1. **Break the RX-throughput bottleneck (blocking, root cause).** The current
   listener reads full `dwt_readdiagnostics` + TTCKO + AGC per frame and dumps a
   4 kB CIR (~170 ms of UART) on sampled polls, all in the single polled loop
   that also re-arms the single-buffered DW1000 — so it catches only ~2 frames
   per 9 ms sweep. Gateway fw must: **strip per-frame rxdiag**, **remove CIR
   capture**, grab only `{rx_ts, payload}`, and **re-arm immediately** (enable
   double-buffered RX). Target: all 8 responses + poll + FINAL in one sweep.
2. **Log raw payload bytes + frame rx_ts, in a binary record** (fixes D2/Task 3;
   fits 460 kbaud only after step 1's diet — see Task 2 bandwidth).
3. **Parse & timestamp the FINAL (`code 0xE2`)** and emit its relayed fields.
4. **Add the tag-side FINAL transmitter** (separate firmware, on the wand tag):
   assemble t1/Δt4_i/reply_i/t5 and TX at the 9200 µs slot position — a new
   capability, not present in any current image.
5. **Dedup/merge service** keyed on (tag_id, sweep_seq); optionally retain both
   gateway FINAL-RX times for TDOA cross-check.
6. **Two-gateway deployment** to get FINAL loss < 0.3 %/sweep (D1); a single
   gateway is a hard single point of failure.

*Note:* the "repurpose the existing listeners as gateways as-is" assumption is
**false** — steps 1–4 are a real firmware effort. But the reverse-broadcast
redesign already assumes new gateway + tag firmware, so this is a scoping input,
not a showstopper. The capture ceiling is a *firmware* ceiling, and the FINAL-relay
design (Task 4) routes around it entirely.

---

## Caveats / provenance

- Anchor-coverage numbers (mean 1.77 per-listener, 1.93 union, all-8 = 0.00 %)
  are denominator-independent and exact. Poll-capture % depends on the chosen
  denominator (union master vs TR); both are reported.
- 17.6 % of master sweeps are single-listener; these are consistent with genuine
  capture diversity plus rare host-drain outliers (±300 ms seen at the tail).
  They do not affect the anchor-coverage conclusion (union only *raises* coverage).
- "P(catch FINAL) ≈ 73–82 %" uses the per-frame capture rate for frames the
  current firmware *is* positioned to hear (poll, rank-1, rank-6); it is a
  conservative floor for a stripped gateway fw, whose FINAL — alone at the quiet
  slot tail — should approach the tag's own ~95–97 % link rate.
- Airtime uses the DW1000 CH5/PRF64/6.8 Mbps/PLEN128 timing (SHR 138 µs, PHR
  21 µs, +10 % RS overhead); see [figures.py](figures.py)/the airtime cell.
