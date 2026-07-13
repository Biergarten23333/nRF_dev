# RF-Quality Metric Design — replacing the success-rate `quality_percent` solver weight

**Date:** 2026-07-12
**Scope:** design + implementation status. What metric, how it maps to a weight, the
sigma sign convention, the validation plan, and the firmware reality that constrains
where it can actually run.

---

## 1. Why (one paragraph)

`quality_percent` is a ranging **success-rate** — a link-completion counter — not an RF
metric. It reads zero DW1000 RF registers, so an obstructed anchor that still exchanges
frames and returns a plausible (biased) range scores ~100. Empirically (14,000 AutoPos
samples) B/E/H are indistinguishable from clean anchors (mean 99.98, B = 100 on all 3,500
of its samples). The solver weight `0.25 + qf/100 ≈ 1.25` is uniform → effectively a no-op.
The data that *would* discriminate NLOS (FP_AMPL1/2/3, CIR power, RXPACC, STD_NOISE) is
already computed by the DW1000 and, in the broadcast firmware, already travels to the tag
in the RESP_DIAG trailer — then is discarded for weighting. This doc specifies how to turn
it into a real confidence weight. (Full trace: `analysis/qf_trace/REPORT.md`.)

---

## 2. Which metric — A, B, or both

Two candidates, both computable from the already-parsed RESP_DIAG fields
(`struct ss_twr_init_rf_diag_sample`: `fp_ampl1, fp_ampl2, fp_ampl3, cir_pwr, rxpacc,
std_noise, fp_index`).

**Metric A — First-path SNR** `fp_snr = fp_ampl1 / std_noise`
- Measures how far the *leading edge* stands above the noise floor. This is the quantity
  that most directly separates a clean LOS first path (fp/noise ≈ 10–30) from an attenuated
  NLOS first path (fp/noise ≈ 2–5). NLOS/obstruction attacks the **first path** specifically
  (it is delayed and buried), which is exactly what biases the range — so A is the more
  *physically-targeted* NLOS discriminator.

**Metric B — Receive power** `rx_power = (cir_pwr << 17) / rxpacc²`
- The Decawave standard linear RX-power estimate `C·2¹⁷ / N²` (C = maxGrowthCIR = `cir_pwr`,
  N = RXPACC = `rxpacc`). This is exactly what the listener already computes
  (`UWB_listener/src/main.c:469`). It measures **total** received power, which falls with
  distance and gross blockage but is *less* specific to first-path degradation (a strong
  multipath cluster keeps total power high even when the first path is buried).

**Decision: implement the FP-SNR σ multiplier as the mechanism, but the metric fed into it
must change — validation shows FP-SNR alone does NOT catch this deployment's events.** See §5.
- FP-SNR is the natural first choice (targets first-path degradation) and is what the C
  `rf_sigma_multiplier` consumes today. But on the overnight capture, **B/E/H show high
  first-path SNR (54–77), the same as stable anchors** — their errors are range *bias* (step,
  delayed multipath) with an *intact* first path, so FP-SNR never drops and never inflates σ.
- Therefore the recommended σ driver is a **timing/shape** CIR feature that senses a
  delayed/blurred leading edge — `FP_PK_ratio`, `RMS_delay_spread`, `rise_time`, or
  `friis_residual` (range↔power consistency) from `pg_lib.cir_features` — not raw FP-SNR.
  Keep FP-SNR and RX-power (Metric B) as inputs, but select the feature that actually
  correlates with the deployment's failure mode (currently none did strongly; best CIR
  |ρ|≈0.10 per the proxy-gate study — treat RF-informed σ as a weak prior, not a gate).
- The σ *mechanism* (multiplier ≥ 1.0, §3) is metric-agnostic — only the scalar fed in changes.

---

## 3. Sigma mapping — RF INFLATES σ (multiplier ≥ 1.0), never shrinks it

The RF metric drives a per-anchor, per-frame **σ multiplier that is ≥ 1.0 always**. RF can
only *relax* trust (widen σ for a poor link); it can never tighten σ below the DWM1001C
hardware LOS noise floor (the uniform 25 mm baseline, §4). This is the design invariant.

```
rf_quality_i = FP_SNR_i = fp_ampl1_i / std_noise_i        (first-path SNR)
multiplier_i = clamp( rf_snr_ref / max(FP_SNR_i, 1) , 1.0 , rf_sigma_mult_cap )
σ_i          = base_sigma × quality_penalty × residual_penalty × multiplier_i
```
with `rf_snr_ref = 10`, `rf_sigma_mult_cap = 10` (config-tunable). So:
`FP_SNR ≥ 10 → 1.0` (clean LOS, no change); `= 5 → 2.0`; `= 2 → 5.0`; `< 1 → 10.0` (cap).

Implemented in `tagpos_solver.c::rf_sigma_multiplier` / `effective_sigma`:
```c
sigma *= rf_sigma_multiplier(cfg, rf_quality, idx);   /* >= 1.0 ALWAYS; NULL => 1.0 */
```

**What inflating σ does — the actual IRLS math (stated precisely, because it is subtle).**
In σ-weighted IRLS an anchor's influence on the normal equations is `∝ weight_i / σ_i²`.
Inflating σ therefore **reduces** that anchor's total influence (the `1/σ²` term), while
simultaneously **relaxing the robust loss** (the normalized residual `r/σ` shrinks, so Huber
stops flagging it as an outlier). Net effect of a low-FP-SNR link: **softly discounted, never
dropped** — its bad range still contributes, at reduced weight, and the geometry (z-DOP) is
preserved. (Verified: a synthetic FP-SNR=2 on a +400 mm-biased anchor inflated its σ 5× and
cut its position pull 17.1 mm → 0.7 mm, with all 8 anchors kept — see the validation harness.)

> **Honest caveat on the brief's prose.** The brief describes a four-case intent where a
> *bad-RF* anchor should end up with *more* influence than a *good-RF anomaly*. In standard
> σ-weighted least squares that is not what σ-inflation produces — inflating σ *lowers*
> influence (via `1/σ²`) regardless of the robust-loss details. This doc implements the
> brief's **explicit formula** (`σ = base × clamp(rf_snr_ref/max(snr,1), 1, 10)`, multiplier
> ≥ 1.0), which softly *discounts* NLOS. If the intent is genuinely to *keep* bad-RF anchors
> influential (only relax the robust flagging, not the baseline weight), that is a different
> mechanism (e.g. RF-scaled Huber δ, leaving σ fixed) — flag which behavior is wanted.

### U5-device (on-nRF52, `uwb_tag_loc.c`) — direct GN weight
The device GN weights each anchor by `w_i` directly (no σ). Replace the success-rate weight
with an RF confidence in `[low, 1.0]` derived from the same FP-SNR, e.g.
`w_i = clamp(FP_SNR_i / rf_snr_ref, w_floor, 1.0)` (clean LOS → 1.0; NLOS → small but > 0).
The anchor is **never dropped** — `w_floor > 0` keeps it in the solve.

### No LOO / no anchor rejection anywhere
The host LOO/rejection path stays dead by design (MC5000: 8→7 anchors loses precision under
tight z-DOP). All robustness is soft: per-anchor σ (RF-informed) + Huber. `reject_*` config
fields remain defined for ABI stability but are inert.

---

## 4. Normalization constants

`metric_nominal` (the "1.0 = nominal LOS" reference) must be fit from the LOS population,
not guessed. Procedure:
1. From a clean (empty-room) capture, take the anchors known to be LOS to the tag.
2. Compute `fp_snr` / `rx_power` per exchange; take the **median** over LOS anchors as
   `metric_nominal`. (Median, not mean — robust to the odd NLOS sample.)
3. Clamp bounds `[0.1, 10.0]` cap the correction at ±10× so a single bad diag frame cannot
   zero or explode an anchor's weight.
4. Recompute `metric_nominal` per deployment (antenna, channel, PRF change it). Store it
   alongside the layout (a new `rf_metric_nominal` field), the same way `anchor_sigma.json`
   travels with `anchor_layout.json`.

---

## 5. Validation plan

The overnight/baseline logs carry **no per-anchor RF scalar** (`agc=0` constant, `ttcki`
constant; no `cir_pwr`/`fp_ampl`/`rxpacc` fields). The only per-anchor RF signal is the
**1016-tap CIR blob** logged for one round-robined anchor per line (`cir_aid`). So RF
discrimination is validated from the **CIR**, using the existing `pg_lib.cir_features`
(SNR_fp, fp_mag, FP_PK_ratio, RMS_delay_spread, friis_residual, …), not from a logged scalar.

1. **Discrimination (static, known geometry) — DONE, and it FAILED for FP-SNR.** Over the
   overnight capture (~480 CIR frames per anchor after `cir_aid` round-robin) we computed
   `cir_features.SNR_fp` per anchor (the `fp_ampl1/std_noise` proxy). **Result:** median FP-SNR
   = A 60, **B 76**, C 54, D 73, **E 54**, F 70, G 66, **H 77** — the event anchors B/E/H are
   NOT low-SNR (B and H are the *highest*); all anchors are 54–77 ("clean LOS", multiplier 1.0).
   So FP-SNR does not discriminate these events and drives no σ inflation (RF-σ ≡ flat-σ). The
   B step / H multipath are range-bias with an intact first path. **Next:** re-run this same
   discrimination test with `FP_PK_ratio`, `RMS_delay_spread`, `rise_time`, `friis_residual`
   (all already in `pg_lib.cir_features`) to find a feature that does separate the events; if
   none does, these events are not RF-observable and residual-Huber + uniform σ handles them.
2. **Weight effect (host):** feed the CIR-derived `conf` into `tagpos_solver` via the new
   `rf_weight` array (already plumbed, NULL-safe) and measure LOO-residual / position change
   vs the flat-weight baseline. Expect the biased anchors' pull to shrink.
3. **Person effect (dynamic NLOS):** on the person-vs-clean pair, confirm `conf` drops on
   the near-wall anchors (BCFG) when the person is present, i.e. the metric tracks an
   introduced NLOS, not just static per-anchor character.

---

## 6. Firmware reality — where this can actually run (important)

The spec assumes the RESP_DIAG fields are "already parsed" in `src/ss_twr_init.c`. **They
are not.** Recon of the deployed tree:

| Tree | RESP_DIAG parse? | Per-anchor RF stored? | Notes |
|---|---|---|---|
| **Deployed `src/ss_twr_init.c`** (what `apps/tag` compiles) | **No** (`rf_diag`/`resp_diag`/`parse_resp` = 0 hits) | No | Response frame lands in `ss_twr_init_rx_buffer` (`:3360-3390`) and is dropped after timestamp extraction. |
| **`SS-TWR/alt-SS-TWR/broadcast/src/ss_twr_init.c`** (broadcast variant) | **Yes** — `ss_twr_init_parse_resp_diag_v2` (`:3108-3144`) | **Yes** — `ss_twr_init_sweep_tag_resp_diag[anchor_id]` (`:601`) | This is where the RF data already exists per-anchor. |
| `UWB_listener/src/main.c` | reads `dwt_readdiagnostics` directly | rx_power_q only | Reference for Metric B. |
| **U5-host** (`tagpos_solver.c`) | n/a | **No RF in the input frame** | The host receives ranges + quality, not RESP_DIAG. |

Consequences for each deliverable:

- **U5-host (`effective_sigma`)** — the C hook is **done and verified** (`rf_weight` threaded
  through `solve_frame → solve_once → effective_sigma`, NULL-safe). But the host *input*
  format carries no per-anchor RF today, so it is a no-op until the capture/telemetry path
  logs a per-anchor RF scalar (or the CIR-derived `conf` is joined in offline). The machinery
  is ready; the data path is the remaining work.
- **U5-device (`uwb_tag_loc.c` weight)** — realizable **only in the broadcast tree** with
  minimal change, because that is the only tag build where the per-anchor
  `ss_twr_init_sweep_tag_resp_diag[anchor_id]` exists at the measurement-assembly loop.
  Steps: add `rf_weight` to `struct uwb_tag_measurement` + `struct uwb_tag_loc_candidate`,
  compute `conf` from `ss_twr_init_sweep_tag_resp_diag[anchor_id]` at the measurement loop,
  copy it in `uwb_tag_loc_build_candidates`, consume it at the weight line.
- **Deployed `src/` tree** — requires a ~5-step port first (struct + LE-read helper +
  `parse_resp_diag_v2` + a persistent `ss_twr_init_resp_diag[UWB_MAX_ANCHORS]` array + a call
  in the RX handler). Larger change, unverifiable without hardware. **Recommend NOT touching
  the live `src/` tree blind**; prototype in the broadcast tree, validate on-bench, then port.

**`quality_percent` stays as-is** for its real job — link-health / dead-link detection
(`qf < 50` ⇒ link failing). It is simply no longer the solver weight.
