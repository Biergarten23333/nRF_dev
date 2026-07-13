# CIR Morphology vs Overnight B/E Events — discrimination test

**Date:** 2026-07-12  **Data:** `logs/geiger_overnight_static_20260711/scan.log` (1.30 GB, 160,678 LSCAN, ~10.6 h).
**CPU:** 12 logical cores, 12 workers. Wall-clock **5.4 s** (parse+decode 5.2 s). Peak RSS **200 MB** (CIRs streamed+discarded).
**Frames decoded** (CIR present, cir_aid round-robin): A=19750, B=19774, E=19712.

> **Task 1.1:** no overlapping listener rxdiag data — the radar listener logs (LB/LE.log) ended Jul 11 12:59, ~12 h before this capture started (Jul 12 00:34). So the RX−FP power NLOS ratio is computed from the Geiger's own CIR (Task 1.2), same device/path (`nlos_ratio_db` below).

## Event definitions (data-driven)
- **B step**: rolling-median range drop >150 mm. Detected window **6.5–8.8 h** (stable 2512 mm vs step 2240 mm; n_stable=11648, n_step=4279).
- **E image vs quiet**: image = range > median+150 mm, quiet = |range−median|<60 mm (median 3480 mm; n_quiet=6998, n_image=6720).
- **A control**: always-stable anchor; features should be flat.

## Discrimination table

AUC = event-state vs baseline separability (0.5 = none). **null** = the same feature's AUC on a range-median split *within the baseline state* (same range direction as the event): if AUC≈null, the feature is just tracking distance/SNR, not the NLOS event. `fp_snr` is the amplitude reference (already known not to discriminate). A feature is a genuine event flag only if **AUC>0.75 AND AUC−0.5 clearly exceeds null−0.5**.

| feature | B stable | B step | **AUC(B)** | nullB | E quiet | E image | **AUC(E)** | nullE | A med (IQR) |
|---|---|---|---|---|---|---|---|---|---|
| fp_to_peak_ratio | 0.484 | 0.536 | **0.523** | 0.563 | 0.316 | 0.407 | **0.764** | 0.625 | 0.485 (0.234) |
| rms_delay_spread | 17.078 | 17.195 | **0.612** | 0.480 | 6.531 | 6.283 | **0.267** | 0.370 | 7.847 (0.453) |
| early_to_late | 0.350 | 0.329 | **0.076** | 0.499 | 13.360 | 14.242 | **0.636** | 0.531 | 3.940 (0.786) |
| nlos_ratio_db | 9.025 | 9.004 | **0.531** | 0.422 | 7.795 | 6.473 | **0.207** | 0.304 | 7.092 (1.837) |
| peak_minus_fp | 8.000 | 8.000 | **0.495** | 0.534 | 2.000 | 2.000 | **0.418** | 0.375 | 2.000 (1.000) |
| fp_snr | 73.583 | 80.965 | **0.537** | 0.564 | 49.036 | 67.394 | **0.796** | 0.675 | 60.876 (29.658) |

## Verdict
- **B step** (~6.5–8.8 h, −272 mm): best raw AUC 0.924; features clearing 0.75 AND beating their range-null: **early_to_late**.
- **E bursts** (+~290 mm image): best raw AUC 0.796; features clearing 0.75 AND beating their range-null: **NONE**.

**Viable on-device σ-scaler:** at least one CIR-shape feature genuinely discriminates an event beyond the range/SNR confound (B: ['early_to_late']; E: —). This is a POSITIVE result vs FP-SNR — a windowed-CIR feature could flag the biased periods on-device.

## Scrutiny — is the B `early_to_late` signal real or a range/SNR artifact?
- Within stable B, Spearman(early_to_late, range) = **-0.019** (≈0 → not distance-driven); the range-null AUC above is ≈0.50 for the same reason.
- Within stable B, Spearman(early_to_late, fp_snr) = 0.245. During the step fp_snr *rises* (74→81, closer/stronger), which by that correlation would push early_to_late UP — but it goes DOWN (0.350→0.329), so the drop is a genuine channel-shape change, opposite to the SNR trend.
- `peak_minus_fp` = 8 taps in both states → the gross multipath structure is intact; only the fine early/late power balance shifts (~6 %). Tight distributions make the small shift separable (AUC≈0.92), but the *magnitude* is small → it needs per-anchor/per-environment calibration and the range-null cannot test the full 272 mm step magnitude, so treat as a modest, not decisive, signal.

## Caveats on downstream use
1. **Detection ≠ bias.** B's step is a sustained ~272 mm shift for ~2.3 h then full recovery — consistent with a real geometry/antenna change, in which case B's range during the step is *correct for the new pose*, not NLOS-biased. A σ-scaler that down-weights it would be wrong; the right response might be to re-solve the layout. `early_to_late` flags the *change*, not necessarily a *bias*.
2. **E (true multipath, where down-weighting IS correct) is the marginal case** — its image is largely a range/SNR phenomenon (fp_snr AUC≈0.80, null≈0.68); no shape feature cleanly beats the confound. So the event we'd most want to catch is the one CIR morphology catches least well.
3. FP-SNR remains a non-discriminator here (AUC_B 0.54, AUC_E confounded), consistent with the earlier finding.

Outputs: `features.npz` (per-frame arrays), `summary.json` (full stats incl. scrutiny).