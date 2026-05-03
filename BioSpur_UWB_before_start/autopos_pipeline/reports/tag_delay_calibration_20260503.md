# Tag Delay Calibration Checkpoint - 2026-05-03

## Capture

Stationary three-tag capture was run with the Roto Arm motor off.

Capture root:

`SS-TWR/alt-SS-TWR/broadcast/logs/tag_delay_cal_stationary_30s_20260503_103617`

Responder preflight:

- `ready=8/8`
- Anchor preflight succeeded before capture.

Capture result:

- `positions_all=901`
- `tr_all=7200`
- `tr_valid_all=6958`
- `cm_all=0`
- `cr_all=0`
- `tf_all=0`

Important note:

The current b61 architecture exports per-anchor ranges as `TR`, not `CR`.
The calibration used:

`SS-TWR/alt-SS-TWR/broadcast/logs/tag_delay_cal_stationary_30s_20260503_103617/recv_20260503_103618/tr_all.csv`

## Solver

New script:

`autopos_pipeline/scripts/calibrate_tag_delay.py`

Command:

```bash
python3 autopos_pipeline/scripts/calibrate_tag_delay.py \
  --anchor-layout SS-TWR/alt-SS-TWR/broadcast/logs/apos_verified_b61_all3_apos_to_20260503_004436/summary.json \
  --range-csv SS-TWR/alt-SS-TWR/broadcast/logs/tag_delay_cal_stationary_30s_20260503_103617/recv_20260503_103618/tr_all.csv \
  --output autopos_pipeline/logs/tag_delay_cal_stationary_30s_20260503_103617_zero_anchor_delay.json \
  --sigma-mm 50 \
  --min-quality 50
```

Output:

`autopos_pipeline/logs/tag_delay_cal_stationary_30s_20260503_103617_zero_anchor_delay.json`

## Estimated Tag Delays

Anchor delays were set to zero for this first calibration.

| Tag | d_tag_mm | RMS mm | Observations | Position mm |
|---|---:|---:|---:|---|
| BSF66F | +43.67 | 140.80 | 2350 | `[1424.3, 1775.9, 976.5]` |
| BS2DCE | +40.54 | 153.34 | 2231 | `[2276.2, 1846.8, 731.2]` |
| BSDC91 | -14.57 | 229.16 | 2355 | `[3201.7, 2212.8, 778.7]` |

Per-anchor residual red flags:

- BS2DCE: A RMS `354.9mm`, H RMS `179.7mm`
- BSDC91: C RMS `581.0mm`, B RMS `167.5mm`
- BSF66F: A RMS `227.7mm`, B RMS `255.3mm`

These residuals are too large for a clean tag-delay-only calibration.

## V4 Feedback Test

The estimated d_tag values were applied as fixed pre-corrections to the V4 tag ranges:

- BSF66F: subtract `+43.67mm`
- BS2DCE: subtract `+40.54mm`
- BSDC91: subtract `-14.57mm`

Generated corrected V4 data:

- `autopos_pipeline/logs/v4_data_b61_tr_sigma_i30_t150_dtagcorr_stationary_20260503.json`
- `autopos_pipeline/logs/v4_data_b61_tr_sigma_i30_t150_noH_dtagcorr_stationary_20260503.json`

V4 Phase B comparison, `tag_subsample=20`, `delay_sigma=10mm`:

| Case | inter RMS mm | tag RMS mm |
|---|---:|---:|
| No d_tag correction | 133.97 | 102.68 |
| d_tag corrected | 130.10 | 98.61 |
| No H tag ranges, no d_tag correction | 137.03 | 100.44 |
| No H tag ranges, d_tag corrected | 132.67 | 99.00 |

The correction helps slightly, but it does not resolve the global inconsistency.

Top remaining inter-anchor errors after d_tag correction:

- A-F: `+446.8mm`
- A-G: `+325.1mm`
- B-H: `-277.8mm`
- B-D: `+174.7mm`

## Verdict

Do not integrate these d_tag values into firmware yet.

The stationary capture worked and the solver path is usable, but the estimated d_tag values are contaminated by layout/inter-anchor inconsistencies. The residuals are far too high for a tag-delay-only calibration, and V4 only improves modestly after applying them.

Recommended next step:

1. Add robust/downweighted inter-anchor pair handling in V4, especially for A-F, A-G, B-H, and B-D.
2. Re-run V4 with robust inter factors.
3. Re-run stationary d_tag calibration using the improved layout.
4. Only then implement `DTAG` firmware/NVS if the stationary fit RMS falls to a reasonable range.

