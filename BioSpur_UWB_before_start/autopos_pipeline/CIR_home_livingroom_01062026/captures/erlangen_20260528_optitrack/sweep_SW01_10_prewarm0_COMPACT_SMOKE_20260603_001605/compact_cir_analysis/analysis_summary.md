# COMPACT CIR Sweep Analysis

- Sweep dir: `autopos_pipeline/CIR_home_livingroom_01062026/captures/erlangen_20260528_optitrack/sweep_SW01_10_prewarm0_COMPACT_SMOKE_20260603_001605`
- ACRX compact samples: 952
- Directed links observed: 56

Important limitation: COMPACT does not contain the full accumulator samples, so it cannot reproduce the full-CIR waveform/envelope plot. It can only draw feature maps from first-path amplitude, peak/growth, noise, and raw-distance stability.

## Top Compact-Suspicious Directed Links

| link | n | raw median mm | raw std mm | fp amp median | peak/fp median | snr proxy median | score |
|---|---:|---:|---:|---:|---:|---:|---:|
| A<-C | 17 | -1.0 | 33.8 | 7578.0 | 0.193 | 270.6 | 7.72 |
| C<-A | 18 | -1.0 | 30.7 | 7377.5 | 0.182 | 286.2 | 6.54 |
| C<-G | 16 | -1.0 | 24.9 | 11642.5 | 0.140 | 210.2 | 6.14 |
| G<-C | 18 | 790.5 | 27.5 | 13691.5 | 0.150 | 240.4 | 5.57 |
| A<-B | 19 | -1.0 | 750.5 | 19106.0 | 0.124 | 421.8 | 4.88 |
| E<-H | 16 | -1.0 | 29.3 | 14386.0 | 0.131 | 233.2 | 4.56 |
| H<-E | 18 | 1390.0 | 37.7 | 14567.5 | 0.120 | 255.1 | 4.00 |
| B<-C | 20 | 1654.5 | 50.1 | 17211.0 | 0.123 | 348.0 | 3.14 |
| C<-H | 15 | -1.0 | 45.5 | 19024.0 | 0.105 | 312.9 | 2.89 |
| E<-F | 17 | -1.0 | 36.4 | 14392.0 | 0.125 | 307.0 | 2.67 |
| F<-E | 18 | 2152.5 | 29.9 | 14438.5 | 0.135 | 307.4 | 2.57 |
| G<-F | 17 | -1.0 | 18.9 | 14981.0 | 0.129 | 294.0 | 2.42 |

## Generated Plots

- `compact_count_heatmap.png`
- `compact_fp_amp_sum_heatmap.png`
- `compact_snr_proxy_heatmap.png`
- `compact_peak_over_fp_heatmap.png`
- `compact_raw_std_heatmap.png`
- `compact_suspicion_score_heatmap.png`
- `compact_suspicious_links.png`
- `cir_pair_weights.csv`
- `cir_pair_weights.json`
