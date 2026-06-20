# Paper Strengthening Completion

Generated: 2026-06-18T02:08:04

## Task Status

| task | status | elapsed_s | key_finding |
| --- | --- | --- | --- |
| P1 | ok | 0.144 | V4 mean signed radial -7.8 mm; V5 -4.8 mm |
| P2 | ok | 0.121 | strongest \|corr(e_i, predictor)\| is layer_binary r=-0.46 |
| P3 | ok | 3.934 | 2 Pareto rows; best proxy median 61.3 mm |
| P4 | ok | 0.207 | D_tag residual slope -19.14 mm/order, R2=0.14 |
| P5 | ok | 0.270 | quality score vs error Pearson r=-0.82; weighted D_tag 42.6 mm |
| P6 | ok | 0.002 | approx explained listed proxies 30.0 mm of 45.5 mm |
| P7 | ok | 0.350 | largest mean contribution: anchor D 37.8 mm |
| P8 | ok | 0.635 | generated 5 upgraded figures |
| P9 | ok | 0.000 | drafted LaTeX intro/method |
| P10 | ok | 0.014 | wrote key numbers card |

## Outputs

- P1 radial mechanism: `tables/p1_signed_radial.csv`, `figures/p1_radial_error_comparison.png`.
- P2 delay/NLOS: `tables/p2_ei_correlations.csv`, `tables/p2_counterfactual.csv`.
- P3 deployment: `tables/p3_deployment_sweep.csv`, `tables/p3_pareto_frontier.csv`, `figures/p3_pareto_frontier.png`.
- P4 temporal: `tables/p4_temporal_order.csv`, `figures/p4_temporal_stability.png`.
- P5 quality score: `tables/p5_quality_score.csv`, `figures/p5_quality_vs_error.png`.
- P6 dynamic gap: `tables/p6_gap_decomposition.csv`.
- P7 anchor contributions: `tables/p7_per_anchor_contribution.csv`, `figures/p7_contribution_heatmap.png`.
- P8 upgraded figures: `figures/fig11_cancellation_mechanism.png` through `figures/fig15_consistency_matrix.png` where source data were available.
- P9 LaTeX draft: `tables/paper_draft_intro_method.tex`.
- P10 key numbers: `reports/KEY_NUMBERS_CARD.md`.

## Runtime

- Workers: 6
- Total task CPU wall sum: 5.7 s
- Mean live CPU%: 29.9
- Max live CPU%: 50.0

Notes: P3 is a deployment-screening sweep using the static range least-squares proxy, not a replacement for the full C trajectory solver. P6 is a component estimate assembled from the ROTO deep-dive and should be read as non-orthogonal gap accounting.
