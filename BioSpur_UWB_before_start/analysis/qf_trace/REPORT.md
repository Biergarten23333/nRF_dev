# `quality_percent` (qf) Trace — Registers + All Code Paths

**Scope:** read-only audit. No code modified.
**Date:** 2026-07-12
**Repo:** `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`
**Question that prompted this:** qf is used as the solver weight
(`uwb_tag_loc.c`: `weight = 0.25 + quality_percent/100`), yet it reads 95+ for
almost every anchor including visibly obstructed links. What does it actually measure?

---

## 0. Answer up front

**`quality_percent` is a ranging *success-rate* — a link-completion counter — not an
RF/signal-quality metric.** In the deployed firmware (`src/`) it is computed by exactly
one function, [`uwb_range_tracker_quality_percent()`](../../src/uwb_range_tracker.c#L58), as

```
qf = 100 * recent_success_count / (recent_success_count + recent_failure_count)
```

over a decaying window of the last ~32 ranging attempts. **Success** = a range was
computed and passed a plausibility gate. **Failure** = the range was implausible, or the
RX timed out / errored. It reads **zero** DW1000 RF-quality registers — no CIR power, no
first-path amplitude, no receive level, no noise estimate.

That is *structurally* why qf pins at 95+ on obstructed links: an anchor that is behind an
obstruction but still exchanges frames and returns a plausible (even if biased) range
**succeeds** on nearly every attempt, so its success-rate stays ~100. NLOS bias moves the
range value, not the success/failure flag, so qf is blind to it by construction. qf only
falls when the *link drops* (timeouts) or produces a wildly out-of-range value.

The firmware *does* read the real RF diagnostics that would discriminate NLOS
(FP_AMPL1/2/3, CIR power, RXPACC, STD_NOISE) — but only in the listener/telemetry and
RESP_DIAG paths, and it **never converts any of them into the `quality_percent` the solver
weights on**. See §2.

**Empirical confirmation** (§5): on the AutoPos calibration path tied to the current
deployment, 14,000 qf samples average **99.98%**, are **exactly 100 on 99.5%** of samples,
**never drop below 94**, and the known-bad/obstructed anchors **B/E/H report the same ~100
as clean anchors** (B = 100 on all 3,500 of its samples).

**Fix-or-drop implication:** qf as it stands cannot do NLOS discrimination — not because
of a tuning bug but because it measures the wrong physical quantity. Either (a) replace the
solver-weight input with a real RF metric (the diagnostics are already on the wire in
RESP_DIAG, §2.3), or (b) drop the weight to a constant, since `0.25 + ~1.0 = ~1.25` for
every anchor today means it is already almost a no-op.

---

## 1. Every place qf is defined / computed / consumed

### 1.1 The single computation point (SET)

There is exactly **one** function that computes qf from state, in the deployed tree:

**[`src/uwb_range_tracker.c:58-69`](../../src/uwb_range_tracker.c#L58)** — verbatim:

```c
uint8_t uwb_range_tracker_quality_percent(
    const struct uwb_range_tracker *tracker)
{
    uint32_t total =
        (uint32_t)tracker->recent_success_count + (uint32_t)tracker->recent_failure_count;

    if (total == 0U) {
        return 0U;
    }

    return (uint8_t)(((uint32_t)tracker->recent_success_count * 100U) / total);
}
```

Everything else in the firmware that has a `quality_percent` either **calls this function**
or **averages/passes-through** its result. The grep for qf tokens
(`quality_percent|quality_pct|link_quality|rx_quality|range_quality`) across
`src/ SS-TWR/ UWB_listener/ drivers/` returns consumers, not alternative computations —
every consumer traces back to this one success-rate.

### 1.2 The success/failure bookkeeping (what the rate counts)

`recent_success_count` / `recent_failure_count` are updated by:

- **[`uwb_range_tracker_record_success()`](../../src/uwb_range_tracker.c#L25)** (`:25-44`) — increments success, stores the raw range.
- **[`uwb_range_tracker_record_failure()`](../../src/uwb_range_tracker.c#L46)** (`:46-51`) — increments failure.
- **[`uwb_range_tracker_decay_recent_counts()`](../../src/uwb_range_tracker.c#L5)** (`:5-16`) — once the recent window reaches `UWB_RANGE_TRACKER_QUALITY_WINDOW` (32), it halves both counters (`success=(s+1)/2`, `failure=f/2`), making this a leaky/EWMA window rather than a strict last-N.

Call sites in the deployed tag path ([`src/ss_twr_init.c`](../../src/ss_twr_init.c)):

| Line | Event | Booked as |
|---|---|---|
| [3460](../../src/ss_twr_init.c#L3460) | `!ss_twr_init_raw_range_plausible(...)` → raw outlier | **failure** |
| [3509](../../src/ss_twr_init.c#L3509) | plausible range | **success** |
| [3556](../../src/ss_twr_init.c#L3556) | RX timeout/error, retries exhausted | **failure** |

So **success ⇔ (a frame came back) AND (the raw range passed the plausibility gate)**. The
plausibility gate [`ss_twr_init_raw_range_plausible()`](../../src/ss_twr_init.c#L1164)
(`:1164-1189+`) only checks `raw_mm != 0` and a raw-vs-recent-window delta — no RF content.

### 1.3 Tracker state (no RF fields)

**[`include/uwb_range_tracker.h:7-22`](../../include/uwb_range_tracker.h#L7)**:

```c
#define UWB_RANGE_TRACKER_WINDOW_SIZE 3U
#define UWB_RANGE_TRACKER_QUALITY_WINDOW 32U

struct uwb_range_tracker {
    uint16_t peer_short_addr;
    uint32_t raw_window[UWB_RANGE_TRACKER_WINDOW_SIZE];
    uint32_t last_raw_mm;
    uint32_t filtered_mm;
    uint32_t success_count;
    uint32_t failure_count;
    uint16_t recent_success_count;
    uint16_t recent_failure_count;
    uint8_t raw_count;
    uint8_t raw_head;
    bool filtered_valid;
};
```

The struct holds only ranges and success/failure tallies. There is no field for CIR power,
first-path amplitude, RSSI, or noise — qf could not be RF-derived even if a caller wanted
it to be.

---

## 2. Which DW1000 registers feed qf?

### 2.1 In the deployed `src/` tree: NONE

The deployed broadcast tag/anchor firmware (`src/`) contains **no `dwt_readdiagnostics()`
call at all**, and no read of RX_FQUAL / FP_AMPL / CIR power for quality purposes. Its only
`dwt_read*` calls are for protocol mechanics: `SYS_STATUS` (status), `RX_FINFO` (frame
length only, not RXPACC), RX/TX timestamps, and `dwt_readcarrierintegrator` (clock offset).
**qf touches no register.**

### 2.2 The RF-diagnostic path that DOES read registers (but does NOT feed qf)

Real RF diagnostics are read — but only in the **listener** and **alt-SS-TWR broadcast**
trees, and the values are used for **telemetry/logging only**, never as a solver weight.
The driver call is `dwt_readdiagnostics(&diag)`; call sites include
`UWB_listener/src/main.c:798`, `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_anchor_init.c:317`,
and `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:1214/1344/1431`.

**Driver struct — `drivers/dw1000/include/deca_device_api.h:259-269`:**

```c
typedef struct {
    uint16 maxNoise;        // LDE max value of noise
    uint16 firstPathAmp1;   // Amplitude at floor(index FP) + 1
    uint16 stdNoise;        // Standard deviation of noise
    uint16 firstPathAmp2;   // Amplitude at floor(index FP) + 2
    uint16 firstPathAmp3;   // Amplitude at floor(index FP) + 3
    uint16 maxGrowthCIR;    // Channel Impulse Response max growth CIR
    uint16 rxPreamCount;    // Count of preamble symbols accumulated
    uint16 firstPath;       // First path index (10.6 fixed point)
} dwt_rxdiag_t;
```

**Driver implementation — `drivers/dw1000/src/deca_device.c:1003-1017`:**

```c
void dwt_readdiagnostics(dwt_rxdiag_t *diagnostics)
{
    diagnostics->firstPath     = dwt_read16bitoffsetreg(RX_TIME_ID, RX_TIME_FP_INDEX_OFFSET);
    diagnostics->maxNoise      = dwt_read16bitoffsetreg(LDE_IF_ID, LDE_THRESH_OFFSET);
    dwt_readfromdevice(RX_FQUAL_ID, 0x0, 8, (uint8*)&diagnostics->stdNoise);  // 8 bytes → stdNoise,fpAmpl2,fpAmpl3,maxGrowthCIR
    diagnostics->firstPathAmp1 = dwt_read16bitoffsetreg(RX_TIME_ID, RX_TIME_FP_AMPL1_OFFSET);
    diagnostics->rxPreamCount  = (dwt_read32bitreg(RX_FINFO_ID) & RX_FINFO_RXPACC_MASK) >> RX_FINFO_RXPACC_SHIFT;
}
```

**Register map** (addresses from `drivers/dw1000/include/deca_regs.h`):

| `dwt_rxdiag_t` field | Register | Addr | Sub-offset / mask | Physically measures |
|---|---|---|---|---|
| `firstPath` | RX_TIME | **0x15** | `FP_INDEX_OFFSET=5`, 16-bit | First-path index (10.6 fixed-point sample of the detected leading edge) |
| `maxNoise` | LDE_IF | **0x2E** | `LDE_THRESH_OFFSET=0x00`, 16-bit | LDE noise threshold used to find the first path |
| `stdNoise` | RX_FQUAL | **0x12** | bit 0, `STD_NOISE_MASK=0x0000FFFF` | Std-dev of the CIR noise estimate |
| `firstPathAmp2` | RX_FQUAL | **0x12** | bit 16, `FP_AMPL2_MASK=0xFFFF0000` | Magnitude of CIR sample at Ceil(FP)+1 |
| `firstPathAmp3` | RX_FQUAL | **0x12** | bit 32, `FP_AMPL3_MASK=0x…FFFF00000000` | Magnitude of CIR sample near FP |
| `maxGrowthCIR` (=CIR power) | RX_FQUAL | **0x12** | bit 48, `CIR_MXG_MASK=0xFFFF…` | CIR peak/max-growth ∝ received signal power (the "C" in RX-power estimate) |
| `firstPathAmp1` | RX_TIME | **0x15** | `FP_AMPL1_OFFSET=7`, 16-bit | First-path amplitude at Ceil(FP_Index) |
| `rxPreamCount` (RXPACC) | RX_FINFO | **0x10** | `RXPACC_MASK=0xFFF00000`, `SHIFT=20` | Preamble symbols accumulated (the "N" normalizer in RX-power) |

These are exactly the fields (RX_FQUAL 0x12, RX_TIME 0x15, RX_FINFO 0x10, LDE_IF 0x2E)
the task asked about. **All are read; none feed `quality_percent`.**

### 2.3 They even travel over the air — and are still discarded for qf

The broadcast responder packs the diagnostics into a **RESP_DIAG** trailer
(`SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h:36-56`, message bytes 18-35):

```c
#define UWB_MSG_RESP_DIAG_FP_INDEX_IDX 20U
#define UWB_MSG_RESP_DIAG_FP_AMPL1_IDX 22U
#define UWB_MSG_RESP_DIAG_FP_AMPL2_IDX 24U
#define UWB_MSG_RESP_DIAG_FP_AMPL3_IDX 26U
#define UWB_MSG_RESP_DIAG_CIR_PWR_IDX  28U   // packs diag->maxGrowthCIR
#define UWB_MSG_RESP_DIAG_RXPACC_IDX   30U
#define UWB_MSG_RESP_DIAG_STD_NOISE_IDX 32U
```

Packed by `ss_twr_resp_write_diag_v2()` (`ss_twr_resp.c:837-870`), and **unpacked on the tag
side** by `ss_twr_init_parse_resp_diag_v2()` (`ss_twr_init.c:3108-3144`) into a
`struct ss_twr_init_rf_diag_sample` (`ss_twr_init.c:504-515`) whose fields are
`fp_index, fp_ampl1, fp_ampl2, fp_ampl3, cir_pwr, rxpacc, std_noise, flags, temp_raw,
vbat_raw` — **and notably no `quality_percent`**. That struct is consumed only by
`ss_twr_init_publish_rf_diag()` → a text `RFD` telemetry line. Grepping the broadcast tag
for `cir_pwr|fp_ampl|std_noise|rxpacc` intersected with `qual` returns nothing.

So the NLOS-relevant data reaches the tag CPU and is thrown away for weighting purposes.

---

## 3. Is qf computed the same in every code path?

| Path | Where qf is set | Formula | Reaches a solver weight? |
|---|---|---|---|
| **Tag ranging** (on-device solver) | [`src/ss_twr_init.c:2558`](../../src/ss_twr_init.c#L2558) | (i) `uwb_range_tracker_quality_percent` + optional continuity penalty | **Yes** — becomes `weight=0.25+qf/100` |
| **Anchor sweep / matrix** | [`src/uwb_anchor_matrix.c:34`](../../src/uwb_anchor_matrix.c#L34) | (i) same success-rate tracker | Yes (anchor-matrix cell weight) |
| **Aggregate / target** | [`src/ss_twr_init.c:1569`](../../src/ss_twr_init.c#L1569) | (i) unweighted mean of the per-anchor tracker qf | Reported, not a per-anchor weight |
| **Wand peer-range** | `src/ss_twr_init.c:2166` (`WR;` line) | (iii) **none** — raw mm + 1-bit success flag | No |
| **Geiger MODE_SCAN** | `UWB_listener/src/main.c:1057` (`LSCAN;`) | (iii) **none** — ranges + raw CIR hex | No |
| **Listener `q=`** | `UWB_listener/src/main.c:469-479` | (ii) `(maxGrowthCIR<<17)/rxPreamCount²` — **real RF power proxy** | No (display/telemetry only) |

**Every path that feeds a solver uses the identical success-rate (i).** The two paths that
have real RF content are the ones that *don't* weight a solver: the wand/scan paths carry no
qf at all, and the listener's `q=` is a genuine RF metric used only for the UI bar.

The anchor-sweep plausibility gate is even weaker than the tag's — `src/ss_twr_anchor_init.c:122-127`
is just `return raw_mm != 0U;` — so anchor-matrix qf is a nearly pure "did a frame come
back" rate.

### 3.1 The one genuinely-RF formula in the tree (listener `q=`)

`UWB_listener/src/main.c:469-479` — for contrast, this is what an RF-based quality looks
like (and it is *not* what the solver uses):

```c
static uint32_t rx_power_q_from_diag(const dwt_rxdiag_t *diag)
{
    uint64_t denom;
    if (diag->rxPreamCount == 0U) {
        return 0U;
    }
    denom = (uint64_t)diag->rxPreamCount * (uint64_t)diag->rxPreamCount;
    return (uint32_t)(((uint64_t)diag->maxGrowthCIR << 17) / denom);
}
```

This is the Decawave linear RX-power estimate `C·2^17 / N²` (C = CIR max-growth, N = RXPACC).
It is bucketed into an 8-level bar (`rx_level_from_q`, thresholds
`{300,700,1500,3000,5500,9000,14000,22000}`) for display and stored in
`anchor_state[].rx_power_q` — never used to weight a position solve.

---

## 4. The actual formula (verbatim, deployed tag path)

The full chain from register-free state to the solver weight, in execution order:

**(1) Base qf — success-rate** — `src/uwb_range_tracker.c:58-69` (quoted in §1.1):
`qf = 100 * recent_success / (recent_success + recent_failure)`.

**(2) Per-measurement assignment + optional geometric penalty** —
`src/ss_twr_init.c:2557-2573` (the loop immediately before `uwb_tag_loc_solve`):

```c
measurements[i].anchor_id = anchor_id;
measurements[i].quality_percent =
    uwb_range_tracker_quality_percent(tracker);          // base success-rate
measurements[i].valid = measured_this_sweep && range_ok_this_sweep &&
                        tracker->filtered_valid;
measurements[i].range_mm = tracker->filtered_mm;

if (measurements[i].valid &&
    !ss_twr_init_apply_range_continuity_gate(
        anchor_id, measurements[i].range_mm,
        &measurements[i].quality_percent)) {             // may lower qf or drop the anchor
    measurements[i].valid = false;
}
```

**(3) The continuity gate** — `src/ss_twr_init.c:1075-1162`
(`APP_TAG_RANGE_CONTINUITY_ENABLE = 1`, warmup 3 sweeps). It compares the measured range to
a range *predicted from the last solved position* and penalizes disagreement:

```c
if (residual_mm >= hard_gate_mm) {
    ... return false;                       // hard gate: drop the anchor entirely
}
if (residual_mm > soft_gate_mm) {
    uint32_t overshoot = residual_mm - soft_gate_mm;
    uint32_t penalty = 20U + ((overshoot * 40U) / (hard_gate_mm - soft_gate_mm));
    if (penalty >= *quality_percent) {
        *quality_percent = 0U;
    } else {
        *quality_percent = (uint8_t)(*quality_percent - penalty);
    }
}
```

Two crucial caveats that make this penalty *usually inactive*:
- It is **bypassed entirely in calibration modes** (`ss_twr_init_runtime_any_calibration_mode()`
  returns early, `:1099-1104`) — and AutoPos / geiger / overnight captures all run in
  calibration modes. That is why logged qf is unpenalized ~100 (§5).
- Even when active, it is a **geometric self-consistency** check ("does this range match my
  current position estimate?"), **not** an RF/NLOS measurement.

**(4) Candidate floor** — `src/uwb_tag_loc.c:59`: candidates with
`quality_percent < APP_TAG_LOC_MIN_QUALITY_PERCENT` (=50) are dropped from the solve. At
95+ this never triggers for obstructed-but-communicating links.

**(5) The solver weight** — `src/uwb_tag_loc.c:412`:

```c
weight = 0.25 + ((double)candidates[i].quality_percent / 100.0);
```

With qf ≈ 100 everywhere, weight ≈ 1.25 for every anchor → the weighting is effectively
uniform in practice.

---

## 5. Sanity check against logged data

### 5.1 The overnight/geiger captures have NO qf field

- `logs/geiger_overnight_static_20260711/scan.log` — **160,678 `LSCAN` lines** over ~10.6 h
  (00:34→11:10, ~4.53 Hz). Keys are `LSCAN src a0..a7 cir_aid rcph rxtofs ttcki agc cir` —
  per-anchor ranges + receiver/CIR fields, **no `q=`/`quality`**. A token scan for
  `q=/qf=/quality` over 300 MB returned zero. (Consistent with §3: MODE_SCAN emits no qf.)
- `logs/geiger_scan_20260711_161258_8anchor/scan.log` — same LSCAN key set, **no qf**.

So the two captures one might reach for cannot show a qf distribution — the metric isn't in
them. This is itself a finding: the primary data-collection path (MODE_SCAN) never records
the quality the solver uses.

### 5.2 The richest qf source — AutoPos `pairs_all.csv` (14,000 samples, tied to the current deployment)

`logs/system_calibration_20260710_233443/autopos/pairs_all.csv`
(columns `a,b,master,dist_mm,quality_percent,raw_mm,ok,fail`), the calibration referenced
by the overnight run's metadata.

**Overall (n = 14,000):**

| metric | value |
|---|---|
| min | **94** |
| max | 100 |
| mean | **99.98** |
| median | 100 |
| std | 0.29 |
| fraction q ≥ 95 | **0.99986** |
| fraction q == 100 | **0.99543** |

The entire below-100 tail is `{94: 2, 95: 13, 96: 49}` — 64 of 14,000 samples. Nothing ever
below 94. (An independent parse of the `summary.json` SW-lines reproduces these stats
exactly.)

**Per-anchor (n = 3,500 each; obstructed anchors bolded):**

| anchor | min | mean | max | frac == 100 |
|---|---|---|---|---|
| A | 94 | 99.95 | 100 | 0.988 |
| **B** | **100** | **100.00** | 100 | **1.000** |
| C | 100 | 100.00 | 100 | 1.000 |
| D | 95 | 99.99 | 100 | 0.996 |
| **E** | 94 | 99.95 | 100 | 0.989 |
| F | 100 | 100.00 | 100 | 1.000 |
| G | 95 | 99.97 | 100 | 0.992 |
| **H** | 96 | 99.99 | 100 | 0.999 |

**Observation confirmed.** The anchors with known directional/multipath problems
(B, E, H per prior analysis) are **indistinguishable** from clean anchors: B reports 100 on
every one of its 3,500 samples; E and H average 99.95–99.99. Obstruction/NLOS is invisible
in qf on this path.

### 5.3 Why the AutoPos path saturates while the TR path can resolve

For contrast, the broadcast **TR/wand** path carries a per-anchor qf list with full 0–100
resolution and *does* log low values (e.g. an old3 motion run: non-zero qualities span 3–85,
mean 11, 0% at 100; a wand run: 3–100, only 9% at 100). This is fully consistent with qf
being a **success-rate**: AutoPos dwells on each pair with retries → ranging almost always
completes → ~100%; a fast TR sweep with many missed slots → lower completion → lower qf.
Either way it is tracking *link completion*, not signal integrity — a NLOS anchor that keeps
answering scores high on both paths.

---

## 6. Bottom line for the fix-or-drop decision

1. **qf measures the wrong quantity for NLOS.** It is a ranging success/completion rate; it
   contains no first-path, CIR-power, or noise information. No amount of retuning changes
   that — an obstructed anchor that still ranges will always score high.
2. **The data you'd want already exists on the wire.** RESP_DIAG (§2.3) delivers
   FP_AMPL1/2/3, CIR power (=maxGrowthCIR), RXPACC and STD_NOISE to the tag every exchange;
   the listener already computes a real RX-power proxy from them (§3.1). A genuine link
   quality (e.g. RX-power `C·2^17/N²`, or a first-path-to-total-power ratio, or FP_AMPL vs
   STD_NOISE) is a small amount of code away — but it is **not** wired into
   `measurements[i].quality_percent` today.
3. **As currently weighted, qf is nearly a no-op.** `weight = 0.25 + qf/100 ≈ 1.25` for all
   anchors → effectively uniform weighting. Dropping it to a constant would change almost
   nothing; the value is only in *replacing* it with an RF-based metric, not in fixing the
   success-rate.
4. **Note the deployed-tree gap:** `src/` reads no diagnostics at all, and MODE_SCAN (the
   overnight capture path) logs no qf. Any RF-quality plan has to add both the register read
   *and* the logging on the path that is actually used for data collection.

---

## Appendix — file/line index

**qf computation & consumption:**
- `src/uwb_range_tracker.c:58-69` — the one qf formula (success-rate); `:5-51` bookkeeping/decay.
- `include/uwb_range_tracker.h:7-22` — window constants (32) + struct (no RF fields).
- `src/ss_twr_init.c:2557-2573` — per-measurement qf set (feeds the solver); `:3460/3509/3556` success/failure booking; `:1075-1162` continuity gate; `:1164` plausibility gate; `:1569-1588` aggregate mean; `:184` `APP_TAG_RANGE_CONTINUITY_ENABLE=1`.
- `src/uwb_tag_loc.c:412` — `weight = 0.25 + quality_percent/100`; `:11` `APP_TAG_LOC_MIN_QUALITY_PERCENT=50`; `:59` candidate floor.
- `src/uwb_anchor_matrix.c:34` — anchor-matrix cell qf; `src/ss_twr_anchor_init.c:122-127` weak plausibility, `:88/308/328/338` prints.

**RF diagnostics (read but not used for qf):**
- `drivers/dw1000/include/deca_device_api.h:259-269` — `dwt_rxdiag_t`.
- `drivers/dw1000/src/deca_device.c:1003-1017` — `dwt_readdiagnostics` register reads.
- `drivers/dw1000/include/deca_regs.h` — RX_FQUAL 0x12, RX_TIME 0x15, RX_FINFO 0x10, LDE_IF 0x2E.
- `SS-TWR/alt-SS-TWR/broadcast/include/uwb_ss_twr_shared.h:36-56` — RESP_DIAG indices.
- `SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_resp.c:837-870` — pack; `.../ss_twr_init.c:3108-3144` + `:504-515` — unpack (no qf) → RFD telemetry.
- `UWB_listener/src/main.c:469-479` — listener `q=(maxGrowthCIR<<17)/rxPreamCount²`; `:798` diag read; `:481` level buckets.
- `SS-TWR/alt-SS-TWR/broadcast/src/anchors/unified/anchor_cir_output.c:198-214` — `fp_amp_sum` CIR log field.

**Data:**
- `logs/system_calibration_20260710_233443/autopos/pairs_all.csv` — 14,000 qf samples (mean 99.98, 99.5% ==100, min 94).
- `logs/geiger_overnight_static_20260711/scan.log`, `logs/geiger_scan_20260711_161258_8anchor/scan.log` — LSCAN, no qf field.
</content>
