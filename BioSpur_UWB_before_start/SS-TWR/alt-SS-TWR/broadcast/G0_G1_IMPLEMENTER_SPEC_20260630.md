# G0 + G1 Implementer Spec — BLE↔UWB Collision Diagnosis

Date: 2026-06-30
Scope: Instrument-build (G0) + mechanism-discrimination (G1) for the
time-varying ranging collapse on the 3-Wand-tag broadcast SS-TWR system.
Audience: implementer (Codex). This is a self-contained spec; downstream
goals G2–G-frozen are summarized only as routing targets.

---

## 0. Established facts (do not re-derive)

- **Master = nRF5340** (NORA-B120, build board `nrf5340dk`), **SNR 1050070698**
  (Master-Tag, the authorized flash target). Master-Anchor SNR 960148546 is
  PROTECTED — never J-Link reflash.
- **Master LFCLK = internal RC *with HFXO calibration*** :
  `CONFIG_CLOCK_CONTROL_NRF_K32SRC_RC=y` + `..._RC_CALIBRATION=y`. NOT raw
  250 ppm. Recalibrates every CTIV (~4 s default) and on ≥0.5 °C step.
- **Tag LFCLK = board default** (DWM1001, on-board 32.768 kHz crystal → LFXO).
- **Effective relative drift ε ≈ single-digit ppm at stable temp** (back-derived
  from the ~26 min collapse episode: ε = W/episode_duration). The old "250 ppm
  LFRC" premise is FALSIFIED.
- **duty = W / CI**, beat_period = CI/ε, episode_duration = W/ε. Duty is
  drift-independent; CI raises the denominator only; clock is NOT the fix lever.
- **Tag die-temp is FLAT** across a 4 h hold (~122–125 **raw SAR byte, 0–255,
  NOT °C**). Flatness is scale-invariant → thermal drift of the *tag* is ruled
  out. The *master* temp was never measured (see G0).
- ε(t) under a calibrated RC is **piecewise-constant with steps at each
  recalibration**, not smooth.

---

## 1. Node / instrument map

| Role | Part | SNR / port | State |
|---|---|---|---|
| Master-Tag (controller) | nRF5340 B120 | 1050070698 | tag-temp carrier flashed |
| Master-Anchor | nRF5340 B120 | 960148546 | PROTECTED, do not flash |
| Wand tags ×3 | nRF52832 DWM1001 | BS9336,BS955A,BSCCF4 | fw `tag-tempTR-nodiag-a7win-20260630` (OTA only) |
| Listener-E (rich observer) | — | probe 760184767 | to build/flash in G0 |
| BLE Sniffer | nRF52840 DK | 683234364 | flash nRF Sniffer in G0 (corroboration only) |

---

## 2. G0 — Instruments (zero-risk / reversible). DONE-WHEN at end.

### G0.1 Parser fix (pure software, offline-safe)
- Bound `tag_temp_raw`/`tag_vbat_raw`/`anchor_temp_raw` to 0–255; drop/flag
  out-of-range as concat artifact.
- Add a **TR concatenation-rate stat** to the capture summary (fraction of TR
  lines that fail the field-count / trailer regex) → settles whether the
  observed **550→312 report-rate decay is real or a concat artifact**.
- **Gate downstream:** the report-rate-decay curve may be used as G1/M2
  mechanism evidence **only if this stat says "real decay"**. If artifact,
  that evidence line is removed (no circular use).

### G0.2 Master telemetry (firmware edit to master_control, flash 1050070698)
- Add **nRF5340 die-temp** read (app-core TEMP peripheral), logged on the
  controller's own line periodically.
- Add **LFCLK recalibration-event instrument**: timestamp + running count of
  calibration completions.
  - Preferred API: nrfx CLOCK `CAL_DONE` event, or Zephyr calibration debug
    counter (`CONFIG_CLOCK_CONTROL_NRF_CALIBRATION*`). **If the driver does not
    expose it, this is acceptable to drop** (see priority below); do NOT block
    G0 on a driver patch.
  - **Priority: A2 (recal-event log) = nice-to-have. A1 (ε texture, §3 M2) =
    must-have fallback** — the step-vs-smooth texture of ε(t) discriminates
    calibrated-RC-driven vs independent-oscillator beat WITHOUT the recal log.
- **Caveat to write in code/comment:** the single on-die TEMP is in the
  app-core power domain; the BLE radio (heat source) is on the network core
  with no exposed sensor → die-temp **underestimates radio-local ΔT**. Use the
  recal-event rate (A2) or ε texture (A1) as the oscillator-co-located thermal
  proxy, not die-temp alone.

### G0.3 Tag MPSL radio-notification hook (firmware edit to tag, OTA)
- NCS v2.8.0 `mpsl_radio_notification_cfg_set` (NOT nRF5-SDK
  RADIO_NOTIFICATION_DISTANCES).
- Two capabilities, both required:
  1. **Record:** per-sweep log of BLE-event-time (tag clock) vs poll-TX-time →
     yields the natural phase and, fitted over many CIs, ε(t).
  2. **Open-loop phase-set primitive (test fixture):** force the poll to a
     commanded offset `φ` relative to the **last observed BLE event**
     (re-reference every CI — see §3 M1 constraint). Label explicitly
     **"TEST FIXTURE, not the production phasing controller"** (G3 upgrades it
     to closed-loop).
- **ε read constraint (A1):** ε must be fit from the **cumulative slope over
  hundreds of CIs**, NOT a single CI (5 ppm × 30 ms = 150 ns/CI, below
  radio-notification ISR jitter of ~µs). **Segment the fit at recalibration
  boundaries** — a window spanning a recal step yields a spurious slope whose
  sign depends on how many step-edges it straddles (this is how "true beat" and
  "temperature walk" can masquerade as each other in the fit if not segmented).

### G0.4 Observers
- Build + flash Listener-E (probe 760184767) — mandatory (on-air poll uplink +
  anchor-response decomposition = TX-death vs RX-loss evidence).
- Flash nRF52840 DK (683234364) as nRF Sniffer for BLE — corroboration only
  (cross-host ms alignment; the MPSL hook is the primary tag-local µs source).

**G0 DONE-WHEN:** a 3-tag instrumented capture emits, per sweep:
{on-air poll (E), BLE-event↔poll offset (hook), 0-anchor / partial-loss class,
tag temp, master temp, master recal-event marks}; parser concat-rate stat
present; hook phase-set primitive verified to place poll at commanded φ.

---

## 3. G1 — Mechanism discrimination (run on the G0 instruments)

**Objective:** pin the mechanism to exactly one outcome AND map the collision-
zone shape — WITHOUT relying on "will the episode recur this time."

Two **independent** measurements, deliberately separated in time so they do not
contaminate each other.

### M1 — Phase-sweep → collision-zone width (deterministic, open-loop, minutes)
- Hook open-loop-forces poll offset φ across the full BLE connection interval
  (N points, dwell of a few hundred sweeps each).
- Per point record:
  - **W_poll(φ):** 0-anchor depth (poll suppression).
  - **W_resp(φ):** partial-loss depth (response window vs event).
- **φ zero-point definition (mandatory):**
  - W_poll(φ): φ = poll-TX time relative to last observed BLE event.
  - **W_resp(φ): φ = response-collection-window START relative to BLE event**,
    NOT poll-TX time. The collection window trails poll-TX by ~8 ms, so W_resp
    is (a) **offset** by ~one collection window from W_poll AND (b) **structurally
    broadened** by the ~8 ms window width (an event anywhere in the window harms
    some responses) → W_resp is intrinsically wider than W_poll. Defining φ wrong
    here mis-aligns the "② fixes poll / ①+③ fix response" decomposition.
- **Smear constraint (A1 round-4 P1):** the collision zone drifts on the
  absolute-phase axis at rate ε during the scan; a slow scan convolves W with
  its own drift trajectory (broadening + warm-up left/right asymmetry artifact).
  Defenses, in priority:
  1. **PRIMARY (mechanism-independent): re-reference placement to each observed
     BLE event every CI** → the relevant smear timescale collapses from
     whole-scan to single-point-dwell.
  2. **SECONDARY: scan in the quietest available window** (lowest |ε|). NOTE this
     does NOT presuppose a steady state — under the temperature-walk mechanism
     there is none; that is why (1) is primary.
  3. Verify W self-consistency across repeated scans.
  - Per-point placement error from ε is **negligible** (≤~1.5 µs worst case vs
    ms-scale W); do NOT spend effort optimizing placement precision — the smear
    (1) is what binds, not placement (round-4 P2 corrected).
- **TX-mode scope (round-3 P3):** run M1 at **@immediate-TX baseline** and label
  all W/TX-death/RX-loss numbers `@immediate`. Optional G1b: re-sweep at
  delayed/prewrite TX after G3 lights that path, for apples-to-apples W.

### M2 — Continuous ε(t) + mechanism evidence (passive; duration calibrated by M1)
- Cold-start long hold (record master temp + recal marks from t=0).
- Per sweep: natural phase, master die-temp, recal-event marks, report rate
  (latter only if G0.1 says "real decay").
- ε(t): cumulative-slope fit, **segmented at recal boundaries** (A1).
- **Duration calibration:** use M1's measured W to compute "time to naturally
  drift across W"; set hold length / repeat count from that — NOT a blind 4 h
  (round-4 P3 anti-undersampling).

### Discriminator — TWO LEVELS (round-4 P3)

**Level 1 (gate, deterministic from M1): does a collision zone exist?**
- **W_poll ≈ 0** (no dip anywhere in φ) → **BLE-UWB phase-collision hypothesis
  FALSIFIED.** Stop; re-examine root cause from scratch. (Most-watched exit.)
- **W_poll > 0** → proceed to Level 2.

**Level 2 (W_poll>0): ε-texture × ε–temp correlation (lagged)**

| ε texture | ε vs master-temp (lagged) | Mechanism | Route |
|---|---|---|---|
| stepped | strong corr, temp monotone↑ | master warm-up transient | **G-warmup** |
| stepped | strong corr, temp random-walk | temperature-driven walk | **G3 closed-loop phasing / environmental temp control** (CI ineffective: walk wanders back) |
| smooth, ~linear | uncorrelated | true 2-oscillator beat | **G2** (CI/W levers effective) |
| flat, ε≈0, M2 ≥ beat-period lower bound | uncorrelated | **static reconnect-lottery phase, no drift to self-heal → session stuck** | **G-now (detect + reroll) as primary** (consistent with SEG2 data) |
| flat, ε≈0, **M2 < beat lower bound** | — | **UNDERSAMPLED, not a result** | extend M2 or declare inconclusive |

- ε–temp correlation must allow **lag** (calibrated RC recalibrates with CTIV
  cadence + thermal mass) and expect **stepped** ε, not smooth.
- **TX-death vs RX-loss:** from Listener-E on-air poll (poll present + no anchor
  = RX-loss; no poll = TX-death), labeled `@immediate-TX`. **PRIOR EVIDENCE
  (2026-06-29, Listener-E live):** verdict was **UWB-TX suppression, not BLE
  loss** — victim BS9336 reported ~217 sweeps over BLE but E heard only 82/217
  polls on-air (~38%) vs 216/216 for a clean tag in the same window. So G1's
  TX/RX split is expected TX-death-dominant; the 3-tag instrumented run
  re-confirms at scale rather than discovers.

**G1 DONE-WHEN:** W_poll(φ) & W_resp(φ) measured @immediate; mechanism resolved
to exactly one Level-1/Level-2 cell; TX-death/RX-loss split quantified
@immediate. **Gate:** route to the cell's downstream goal.

---

## 4. Downstream routing (structure frozen; G1 exits here)

- **G-warmup** — master thermal-stabilize / warm-up gate / post-warm-up reroll.
- **G2** — minimize integrated downtime via duty=W/CI: shrink W (batch/compress
  TR → shorter BLE events) + raise CI (7.5→30 ms, supervision ~200 ms scaled
  with CI, latency 0). Validate by BOTH deterministic injection (depth unchanged)
  AND long/repeated 3-tag (integrated coverage). **Gate = integrated session
  coverage, NOT peak depth.**
- **G3** — active phasing (real fix). HARD PREREQ GATE: resolve UWB-TDMA slot
  ownership (master-scheduled vs tag-self-organized) before committing. Upgrades
  the G0 hook to closed-loop.
- **G4** — RX double-buffer backstop (`dwt_setdblrxbuffmode` + fixed HSRBP
  sequence + assert); short interrupts only.
- **G-now** — runtime reroll on slow-onset 0-anchor rise (independent, deployable
  today; reroll breaks any G3 phasing lock → needs lock-loss re-acquire).
- **G-frozen** — clock/LFXO swap frozen. Master = calibrated RC (~few ppm at
  stable temp); LFXO (±20 ppm) has no headroom at stable temp, MAY help only
  under thermal swings. Re-open only after G1 confirms drift headroom.

---

## 5. Deferred to G0 landing
- ~~A2 recal-event API existence~~ → RESOLVED, see Appendix A.
- Tag raw→°C scale via per-chip OTP[0x009] (flatness conclusion already safe
  without it; absolute °C only needed if a thermal-limit question arises).

---

## Appendix A — G0.1 done + G0.2/G0.3 survey & hook design (2026-06-30, pre-firmware review)

### A.0 G0.1 — DONE & verified (parser, `run_recv_tdma_capture.py`)
- Temp/vbat bytes bounded 0–255; OOB dropped (not stored as garbage).
- **PRIMARY** concat signal `tr_splice_loss_rate` = on-wire `TR;` tokens dropped
  by the `|`-split-then-`search-first` logic when two TR lines are concatenated
  into one notify fragment (no `|`). This is the actual 550→312 mechanism.
- **`tr_concat_rate_timeline`** = 60 s-bucketed splice-loss rate → matched
  against the TR-rate decay curve: **flat = real attrition, rising = artifact.**
- `tr_temp_trailer_oob_rate_supplementary` = value-corruption subset ONLY
  (does not catch in-range splices) — explicitly demoted from "primary."
- Verified: `py_compile` clean + unit test (splice count, bucketing, rates).

### A.1 G0.2 — DONE & HARDWARE-VERIFIED (recal-count works; die-temp dropped)
- **die-temp DROPPED — hardware constraint, not a choice.** The nRF5340 TEMP
  peripheral is **network-core-only** (`temp@41010000` in `nrf5340_cpunet.dtsi`;
  nothing in cpuapp, no `NRF_TEMP` in the app-core MDK header). The master app
  core (this image) has no die-temp. Per the agreed A1>A2>die-temp priority,
  recal-rate is the (superior, oscillator-co-located) thermal proxy anyway.
- **recal-count instrument: built + flashed (1050070698) + VERIFIED.** On the
  live master: `[RECV] MCLK cal=17 skips=0 up_ms=65083` … `cal=23` — i.e.
  `z_nrf_clock_calibration_count()` **increments ~every 4 s** (matches MPSL/CTIV
  period 4000 ms). The Zephyr driver calibration runs (NOT MPSL-monopolized),
  so the counter is live, not pinned.
- **Correct gating symbol = `CONFIG_CLOCK_CONTROL_NRF_DRIVER_CALIBRATION`**
  (NOT `..._NRF_CALIBRATION`, which does not exist here). Counters return `-1`
  unless **`CONFIG_CLOCK_CONTROL_NRF_CALIBRATION_DEBUG=y`** (added to
  `b120_master_tag_lfrc.conf`). Externs guarded by `#if defined(..._DRIVER_CALIBRATION)`.
- **Thermal signal = skips/cal ratio over time:** `skips=0` while temperature
  changes (warm-up) → calibrates every period; `skips` accrues once thermally
  stable (TEMP_DIFF=2 ≈0.5 °C, MAX_SKIP=1). The just-rebooted master showing
  `skips=0` is a live preview of the G1 warm-up signature.
- **Parser wired:** `MCLK_RE` (handles the `[RECV] ` prefix) → `master_clock.csv`
  (host_elapsed_s, host_epoch_s, cal, skips, up_ms) + `mclk_rows` in summary.
- A1 ε-texture remains the must-have fallback (no Kconfig dependency).

### A.2 G0.3 — hook design (answers the three review points BEFORE firmware)

Poll-TX path facts (`src/ss_twr_init.c`): broadcast poll (the wand path) builds
+ writes the frame at 5214–5233, then `dwt_starttx(DWT_START_TX_IMMEDIATE)` at
**5237**. The firmware **already** CPU-busy-waits to a target cycle before
immediate-TX for poll spacing (`ss_twr_init_alt_wait_until_cycle(target_poll_cycle)`
at 5165) and already has LTDMA slot timing (`ss_twr_init_alt_ltdma_slot_start_cycles`,
`use_prearmed_tx`). A delayed-TX path exists at 5292 — **the phase-set primitive
must NOT use it.**

**Point 1 — φ injection = CPU-timed wait + immediate-TX (NOT DW1000 delayed).**
The primitive is a natural extension of the existing pattern: in phase-set mode,
override the poll's target cycle to `event_cycle + φ_cycles` and reuse
`ss_twr_init_alt_wait_until_cycle()` → then the unchanged `dwt_starttx(IMMEDIATE)`
at 5237. The DW1000 delayed path (5292) is never touched. **@immediate-TX label
holds** and no premature prewrite race is introduced. Busy-wait-then-immediate
deliberately keeps the BLE-event/CPU contention in the measured window (that IS
the collision under test).

**Point 2 — event time = notification-ISR cycle − notification distance.**
MPSL `mpsl_radio_notification_cfg_set` fires the ISR a configured *distance*
before the radio event. The hook records `k_cycle_get_32()` in that ISR, then
`event_cycle = isr_cycle + notification_distance_cycles`. **φ is defined relative
to `event_cycle`, not the ISR.** Document the configured distance; an un-subtracted
distance only shifts φ's zero-point but, critically, would add a second offset on
top of the W_poll↔W_resp ~8 ms separation (§3) → must be subtracted.

**Point 3 — record vs phase-set are one build, switched at runtime, never mixed.**
- **Record mode (M2):** poll keeps its natural `target_poll_cycle`; hook only
  *logs* {event_cycle, poll_cycle, recal_count, master not involved}. Natural
  phase preserved → ε(t) valid.
- **Phase-set mode (M1):** hook *overrides* target cycle to `event_cycle + φ`.
- Switched by a BLE command (or Kconfig for a dedicated test build). **Mutually
  exclusive** — phase-set must not write the target in record mode, or M2's
  natural phase is corrupted. Default = record.

**Slot-ownership note for G3 (found in passing):** the tag references
`ss_twr_init_alt_ltdma_slot_start_cycles` — slot timing exists tag-side. Whether
the master *assigns* it or the tag self-derives it is the G3 hard-gate question;
flagged here, not resolved.

### A.3 Execution-order amendment (user)
Listener-E flash (probe 760184767, `build_uwb_listener_poll_diag.sh` +
`flash_uwb_listener_jlink.sh`) is pulled EARLY, parallel to this survey — it is
pure-flash/zero-design-risk and is the only HW means for the TX/RX split; do not
strand it at the end of the critical path. 52840 sniffer stays last (redundant).
