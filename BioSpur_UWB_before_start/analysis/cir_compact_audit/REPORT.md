# CIR Compact Audit — What CIR data can the tag access and send over BLE?

**Scope:** read-only audit. No code modified.
**Date:** 2026-07-12
**Repo:** `/mnt/nrf_ssd/nRF_dev/BioSpur_UWB_before_start`
**Question:** FP-SNR (from RESP_DIAG) doesn't catch B's step or E's multipath. The features that
would (fp_to_peak_ratio, rms_delay_spread, CIR shape) need CIR taps. Does "CIR compact" give the
tag CIR morphology, and can the tag read enough CIR within its TDMA budget to compute those?

---

## 0. Answer up front

Two separate things, do not conflate them:

1. **"CIR compact" carries NO CIR taps.** It is the `CRX;` text line
   ([`ss_twr_init.c:721-736`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L721)) containing the
   eight DW1000 **rxdiag scalar registers** — `firstPath` (FP index), `firstPathAmp1/2/3`,
   `maxGrowthCIR`, `rxPreamCount`, `stdNoise`, `maxNoise`. Same data class as RESP_DIAG/FP-SNR,
   plus the FP index and the total-power terms. **It cannot yield `rms_delay_spread` or a true
   `fp_to_peak_ratio`** (those need the tap array). It *can* yield FP-SNR (already tested, doesn't
   discriminate) **and the classic Decawave RX-power−FP-power NLOS metric** (untested — see §7).
   The full accumulator path (`CIRM/CIRD`) exists but is **USB/CDC-only, never BLE**.

2. **But the tag CAN read real CIR taps, and a windowed read fits the budget.** The broadcast tag
   already calls `dwt_readaccdata()` in CIR-full mode
   ([`ss_twr_init.c:864`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L864)); the driver
   supports an **arbitrary offset+len windowed read** and forces the accumulator clock internally.
   A **20-tap window around firstPath ≈ 84 µs** at 8 MHz SPI, vs a **~1000 µs per-anchor slot** and
   **~1000 ms inter-sweep idle** — under 12 % of one slot. On-device `fp_to_peak_ratio` and
   `rms_delay_spread` from that window are **feasible** (§4).

**Net:** the morphology features that catch B/E do NOT come from the compact format — but they are
reachable by adding a small windowed accumulator read + on-device feature math to the ranging loop.
The deployed `src/` tag does neither today (reads no CIR at all).

---

## 1. CIR compact — where it lives, how it's gated

Everything CIR is in the **broadcast dev tree** (`SS-TWR/alt-SS-TWR/broadcast/`). The **deployed
`src/` tree reads no CIR and no rxdiag at all** (`grep readaccdata|readdiagnostics|ACC_MEM|firstPath src/` = 0 hits).

| element | location | note |
|---|---|---|
| CIR mode enum | [`include/uwb_tdma.h:28-32`](../../SS-TWR/alt-SS-TWR/broadcast/include/uwb_tdma.h#L28) | `OFF=0, COMPACT=1, FULL=2` |
| mode storage | `ss_twr_init.c:563` `static atomic_t ss_twr_init_cir_mode;` | **zero-init → default OFF**, not persisted |
| get / set / parse | `ss_twr_init.c:630 / 641 / 651` | parse accepts `off/compact/feature/full/raw` |
| set at runtime | [`uwb_tag_ble.c:1609-1633`](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/src/uwb_tag_ble.c#L1609) | BLE NUS text command `CIR <arg>` |
| compact publish | `ss_twr_init.c:706` `ss_twr_init_publish_cir_features()` | builds `CRX;`, prints + BLE |
| full publish | `ss_twr_init.c:825` `ss_twr_init_publish_full_cir()` | `CIRM/CIRD/CIRE`, **CDC only** |

**Compile gates (`#ifndef` defaults = CMake cache defaults):**

| symbol | default | effect |
|---|---|---|
| `APP_TAG_CIR_FEATURE_OUTPUT_ENABLE` | **0** | compact `CRX` **compiled out**; runtime `CIR COMPACT` command rejected `CIR_UNSUPPORTED` |
| `APP_TAG_CIR_FEATURE_OUTPUT_BLE_ENABLE` | 1 | if feature on, forward `CRX` to BLE |
| `APP_TAG_CIR_FULL_OUTPUT_ENABLE` | 0 | full CIR compiled out |
| `APP_TAG_CIR_FULL_OUTPUT_CDC_ENABLE` | 1 | full CIR is USB/CDC-only |
| `APP_TAG_CIR_COMPACT_SAMPLE_PERIOD` | 8 | compact sampled 1 anchor / 8 sweeps |

So on a stock build, CIR is off both at compile time and at runtime; compact-over-BLE exists only in
builds with `-DAPP_TAG_CIR_FEATURE_OUTPUT_ENABLE=1`.

---

## 2. What CIR compact contains (the `CRX;` line)

`ss_twr_init.c:721` — `snprintk(line,...,"CRX;1;%lu;%u;%ld;%lu;%ld;%u;%u;%u;%u;%u;%u;%u;%u", ...)`:

| field | source (`dwt_rxdiag_t`) | meaning |
|---|---|---|
| sweep_count, anchor_id | — | which sweep / anchor |
| raw_distance_mm, resp_rx_ts, carrier_integrator | ranging | range + clock |
| **firstPath** | `diag->firstPath` | FP index (10.6 fixed-point tap) |
| **firstPathAmp1/2/3** | `diag->firstPathAmp1/2/3` | first-path amplitudes (3 taps around FP) |
| **maxGrowthCIR** | `diag->maxGrowthCIR` | CIR peak growth ∝ total RX power (the "C") |
| **rxPreamCount** | `diag->rxPreamCount` | RXPACC (the "N" normalizer) |
| **stdNoise / maxNoise** | `diag->stdNoise / maxNoise` | noise std / LDE noise threshold |

- **CIR taps included: ZERO.** These are scalar registers read by `dwt_readdiagnostics()`
  ([`ss_twr_init.c:6069`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L6069)), not accumulator samples.
- Complex/mag, bit depth: N/A (no taps). The registers are native 16-bit, rendered as ASCII decimal.
- **Total size:** one ASCII line, **~60–90 bytes**.
- **Fits a BLE packet?** Yes, trivially — one NUS notification (§5), no chunking.
- **Metadata present:** FP index (`firstPath`) ✔, noise floor (`stdNoise`/`maxNoise`) ✔, peak *power*
  (`maxGrowthCIR`) ✔. **Absent:** peak *index*, and the tap array.

**What you can/can't compute from compact:**
- ✔ **FP-SNR** = `firstPathAmp1 / stdNoise` (already tested — does not discriminate B/E/H).
- ✔ **RX power** = `(maxGrowthCIR<<17)/rxPreamCount²`; **FP power** = `(F1²+F2²+F3²)/rxPreamCount²`.
  → the **Decawave RX−FP power NLOS metric** (multipath energy fraction), **not yet tested** (§7).
- ✘ **rms_delay_spread** — needs the power-vs-delay profile (tap array).
- ✘ **true fp_to_peak_ratio** — `maxGrowthCIR` is a scalar peak-growth, not `max|tap|`; `F1` is FP
  amplitude, so `F1/maxGrowthCIR` is only a crude proxy, and it misses late-arriving multipath peaks.
- ✘ kurtosis / early-late ratio / multipath_count — all need the tap window.

---

## 3. What CIR access the tag firmware currently has

- **`dwt_readaccdata(buffer, len, accOffset)`** — [`deca_device.c:943-951`](../../SS-TWR/alt-SS-TWR/broadcast/drivers/dw1000/src/deca_device.c#L943).
  Arbitrary offset+len ⇒ **windowed reads are natively supported.** The driver **forces the ACC clock
  on/off itself** (`_dwt_enableclocks(READ_ACC_ON/OFF)` → `PMSC_CTRL0`), so **no app-level FACE/AMCE
  handling is needed** (app grep for `FACE|AMCE|PMSC_CTRL0` = 0 hits — correctly delegated to driver).
- **`ACC_MEM` (0x25), `ACC_MEM_LEN = 4064`** — [`deca_regs.h:660-661`](../../SS-TWR/alt-SS-TWR/broadcast/drivers/dw1000/include/deca_regs.h#L660).
  1016 complex taps × (int16 I + int16 Q) = **4 B/tap**. Every read requests `len+1` and discards
  byte[0] (the DW1000 dummy octet).
- **The tag already reads the full accumulator** in CIR-full mode
  ([`ss_twr_init.c:857-879`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L857)): 85 chunks of
  ≤48 B, **one anchor per sweep** (`should_publish_unicast`), output over **USB/CDC** (`printk`), not BLE.
- **Timing constraint:** ACC_MEM holds only the *most recent* RX frame's CIR and is overwritten on RX
  re-arm, so a per-anchor read must happen **inline after that anchor's RXFCG, before re-arming**.
- **SPI = 8 MHz** ([`uwb_port.c:160`](../../SS-TWR/alt-SS-TWR/broadcast/src/uwb_port.c#L160), fast cfg
  engaged at bringup) → **1 byte/µs**.

Read-time at 8 MHz:

| read | wire bytes | time |
|---|---|---|
| 20-tap window (80 B + dummy + header) | ~84 | **~84 µs** (single transaction) |
| 30-tap window (~120 B) | ~124 | ~124 µs |
| full 4064 B (85×48 chunks) | ~4400 | **~4.4 ms** |

---

## 4. On-device feature extraction — feasible?

**nRF52832 = Cortex-M4F @ 64 MHz, single-precision FPU** (`VSQRT.F32` ≈ 14 cyc, `VMUL/VADD.F32` ≈ 1 cyc).
Operate on a 20–30 tap window; use tap **power** `p = I²+Q²` (2 mul + 1 add ≈ 3 cyc, no sqrt) for most
features, one final `sqrt` for the spread.

| feature | per-window cost | ~time @64 MHz |
|---|---|---|
| `fp_to_peak_ratio` (|CIR[fp]| vs max over window) | 30 × (3 cyc pow) + max scan | **~2 µs** |
| `rms_delay_spread` = √(Στ²p / Σp) | 30 × (pow + τ²·p + accum ≈ 8 cyc) + 1 sqrt | **~4 µs** |
| kurtosis / early-late ratio / multipath_count | 30 × O(few cyc) each | **~3–6 µs** each |
| **all of the above together** | | **~20–40 µs** |

**Budget check (per anchor):** windowed read **~84 µs** + feature math **~20–40 µs** ≈ **~120 µs**,
against a **~1000 µs per-anchor slot** ([`ss_twr_init.c:423`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L423),
`ALT_BCAST_POLL_SCHED_UUS=1000`) → **~12 % of one slot**. Across 8 anchors that adds ~0.9 ms/sweep, and
there is a **~1000 ms inter-sweep idle** (`RNG_DELAY_MS=1000`,
[`:40`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L40)) of headroom. The on-device subset
search already burns ~ms per fix, so ~120 µs is comfortably affordable.

> **Verdict (§4/the task's explicit question):** **YES** — the tag can read a ~20-tap CIR window
> around `firstPath` (which it already has from `dwt_readdiagnostics`) and compute both
> `fp_to_peak_ratio` and `rms_delay_spread` on-device, well within its TDMA budget. A **full**
> accumulator read (4.4 ms) does NOT fit a 1 ms anchor slot — so the design must be **windowed**, or
> deferred to the inter-sweep idle for one anchor at a time.

Caveat: `fp_to_peak_ratio` computed over a *window* sees only the local peak; a strong late-multipath
peak outside the window is missed (use `maxGrowthCIR` from rxdiag as the global-peak reference, or
widen the window past the expected multipath spread, ~40 taps ≈ 160 B ≈ 164 µs, still < slot).

---

## 5. BLE transport

- **Service/characteristic:** Nordic UART Service (NUS), TX characteristic
  `6e400003-b5a3-f393-e0a9-e50e24dcca9e`, **notify** (not indicate). `bt_nus_send()` at
  [`uwb_tag_ble.c:1451`](../../SS-TWR/alt-SS-TWR/broadcast/apps/tag/src/uwb_tag_ble.c#L1451). No custom
  CIR characteristic.
- **MTU:** `CONFIG_BT_L2CAP_TX_MTU=498` (`apps/tag/prj.conf:59`) → up to ~495 B/notification once the
  central negotiates MTU. The ~60–90 B `CRX` line fits one notification; no chunking (chunking exists
  only on the CDC full-CIR path).
- **Update rate:** compact is due `(sweep_count % 8)==0` for **one round-robin anchor**
  ([`ss_twr_init.c:4957/4941`](../../SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c#L4957)) →
  **≈ sweep_rate/8 lines/s** (single-digit Hz).
- **Receiver:** no live in-repo BLE client for `CRX`. It is `printk`'d to console and offline-parsed by
  [`scripts/cir_features_to_pair_weights.py`](../../SS-TWR/alt-SS-TWR/broadcast/scripts/cir_features_to_pair_weights.py)
  (`CRX_RE`/`ACRX_RE`) into solver pair-weights; a generic NUS client (nRF Connect/bleak) can read it.
  `ACRX;` is the anchor/Master-relayed variant.
- **Interleave with ranging:** **non-blocking.** `uwb_tag_ble_publish_status` copies to a slab item and
  `k_fifo_put` to a bounded (10-item) FIFO drained by a separate TX thread (prio 7); slab-exhausted →
  dropped (`ble_tx_drop_count`), never stalls the ranging loop.

> **BLE verdict:** no CIR tap data reaches BLE today, by architecture — compact carries only rxdiag
> scalars, and full-CIR is CDC-only (`uwb_tag_ble_cir_mode_supported` blocks FULL over BLE). Any
> morphology feature must be **computed on the tag** and sent as a small scalar line (like `CRX`), which
> the NUS path handles trivially.

---

## 6. Listener path (for comparison)

- Full CIR read: `while (offset < 4064)` in 48-B chunks (`+1` dummy), `dwt_readaccdata` at
  [`UWB_listener/src/main.c:484`](../../SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c#L484) → 85
  chunks, hex-dumped as `LCIRD` lines.
- **Reads with RX stopped and BLOCKS the next RX.** Comment
  [`main.c:617-623`](../../SS-TWR/alt-SS-TWR/broadcast/UWB_listener/src/main.c#L617): dumps the
  accumulator "while RX is still stopped after RXFCG, because re-arming overwrites ACC_MEM. This blocks
  the loop for one dump and misses frames arriving during it." RX re-armed only after the full dump.
- Wall-time is **UART/printk-bound** (85 lines), not SPI-bound — the listener sacrifices frames per dump.
- **Applicability to the tag:** the tag does NOT need the listener's full-dump approach. The tag is the
  TDMA master with a 1000 ms inter-sweep idle and a 1 ms/anchor slot; a **windowed 84 µs** read fits
  inline without dropping the sweep, whereas the listener's 4.4 ms full dump (plus UART) is why the
  listener drops frames. The tag's timing is *looser*, not tighter, than the listener's — the tag can
  afford CIR access the listener cannot amortize.

---

## 7. Bottom line + recommendations

1. **The compact format is a dead end for morphology** — it is rxdiag scalars, no taps. It cannot give
   `rms_delay_spread` or a true `fp_to_peak_ratio`.
2. **One untested lever still lives in the compact fields:** the Decawave **RX-power − FP-power** NLOS
   metric = `10·log10[(maxGrowthCIR·2¹⁷)/N²] − 10·log10[(F1²+F2²+F3²)/N²]`, i.e. how much received power
   is *not* in the first path. >~6 dB ⇒ likely NLOS/multipath. This is morphology-adjacent, computable
   from the already-published compact line, and **specifically targets E's multipath** (though not B's
   clean step). Test it against the overnight/person data before adding any CIR read — it may be enough
   for the multipath case and costs nothing new on the wire.
3. **For the features that need shape (both B and E):** add a **windowed accumulator read** (~20–40 taps
   around `firstPath`, ~84–164 µs) + on-device `fp_to_peak_ratio`/`rms_delay_spread` (~20–40 µs) to the
   ranging loop, and emit the computed scalars in the existing `CRX`-style NUS line. This is **new
   capability**, feasible within the per-anchor 1 ms slot (~12 %), and must be **windowed** (a full 4.4 ms
   read does not fit a slot). The driver already supports the windowed read and the ACC clock; the
   deployed `src/` tag would need this added from scratch (it reads no CIR today).

**Explicit answer to the task's closing question:** *Can the tag compute `fp_to_peak_ratio` and
`rms_delay_spread` from data it can read within its TDMA budget?* — **Yes, but not from the compact
format.** It requires a new ~84 µs windowed `dwt_readaccdata` around the first path (well inside the
~1000 µs anchor slot); from that window both features are ~2–4 µs of M4F math each. The compact `CRX`
line alone is insufficient (no taps); it only supports FP-SNR (tested, ineffective) and the untested
RX−FP power NLOS ratio.

---

## Appendix — file/line index

- Tag CIR path: `ss_twr_init.c` — mode enum/get/set/parse `:563/630/641/651`; compact publish `:706-740`;
  full publish `:825-891` (`dwt_readaccdata` `:864`); compact sampling `:4941-4983`; rxdiag read `:6069`;
  compact emit `:6093`, full emit `:6100`; budget `RNG_DELAY_MS :40`, per-anchor `:423`, RESP timeout `:52`.
- Mode command: `apps/tag/src/uwb_tag_ble.c:1609-1633`; capability gate `:727`; NUS send `:1451`; async TX `:1424`.
- Driver: `deca_device.c:943` `dwt_readaccdata`, `:2674` `_dwt_enableclocks(READ_ACC_ON/OFF)`;
  `deca_regs.h:660-661` `ACC_MEM_ID/LEN`; SPI `uwb_port.c:159-160`.
- Listener: `UWB_listener/src/main.c:477-498` read loop, `:617-631` RX-blocking dump comment.
- Compact gates: `ss_twr_init.c:183-212`; `apps/tag/CMakeLists.txt:52-58`.
- Deployed `src/`: no CIR (grep `readaccdata|readdiagnostics|ACC_MEM|firstPath` = 0).
- Offline consumer: `SS-TWR/alt-SS-TWR/broadcast/scripts/cir_features_to_pair_weights.py`.
