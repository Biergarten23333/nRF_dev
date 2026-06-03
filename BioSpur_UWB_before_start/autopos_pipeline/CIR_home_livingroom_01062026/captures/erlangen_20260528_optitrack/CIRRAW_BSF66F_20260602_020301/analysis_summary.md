# BSF66F F/H Full-CIR 8h Capture Analysis

## Capture
- Capture dir: `autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/CIRRAW_BSF66F_20260602_020301`
- Time span: 2026-06-02T02:03:02+02:00 to 2026-06-02T10:03:01+02:00 (8.00 h)
- Frames: 31,514
- Full accumulator files: 31,514
- Accumulator length: 4064 bytes = 1016 complex samples per frame
- Plots: `cir_metrics_timeseries.png`, `cir_mean_waveforms.png`, `cir_tail_ratio_timeseries.png`

## Coverage
| Anchor | Frames | Sweep min | Sweep max | Median sweep gap | Max gap | Gaps >2 |
|---|---:|---:|---:|---:|---:|---:|
| F | 15,758 | 228 | 31744 | 2.0 | 4 | 1 |
| H | 15,756 | 229 | 31743 | 2.0 | 4 | 2 |

Interpretation: priority-only F/H sweep is stable. F and H are nearly 1:1 across the full run, so the earlier missing-F/H behavior was not an offline-anchor problem; it was caused by the old all-anchor sweep/roster timing path.

## Per-Anchor Scalar Metrics
| Anchor | raw mm mean | raw sd | raw p05-p95 | FP amp median | peak median | noise median | carrier median |
|---|---:|---:|---:|---:|---:|---:|---:|
| F | 2870.2 | 31.0 | 2819-2921 | 20378 | 2039 | 68 | -158 |
| H | 2369.2 | 24.6 | 2330-2410 | 9859 | 489 | 32 | 6697 |

## Full-CIR Waveform Metrics
| Anchor | peak index median | peak index IQR | peak mag median | pre-noise mag median | peak/pre median | tail/main median | tail/main p95 |
|---|---:|---:|---:|---:|---:|---:|---:|
| F | 750.0 | 4.0 | 7547.7 | 85.0 | 87.8 | 0.137 | 0.215 |
| H | 750.0 | 4.0 | 3528.4 | 42.9 | 82.3 | 0.132 | 0.186 |

## Notes
- This run is not sampling all eight anchors. It intentionally captures only F/H full CIR, one full accumulator per sweep, to avoid the DW1000 single-accumulator overwrite/timing limit.
- Current stored full CIR is the complete DW1000 accumulator readout for the selected response, not just diagnostics. Scalar fields remain useful: raw distance, first_path, first-path amplitude sum, maxGrowthCIR, and stdNoise.
- The mean waveform plot should be interpreted as shape/relative multipath evidence, not as calibrated channel impulse response power in dBm.
- Next useful experiment: keep the same F/H-only image and deliberately block the Tag-body line for annotated intervals, then compare tail/main and FP amplitude changes against these baseline distributions.
