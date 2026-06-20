# Mechanistic Deep-Dive Completion

Generated: 2026-06-18T01:59:35

| task | status | elapsed_s | key_finding | notes |
| --- | --- | --- | --- | --- |
| M1 | ok | 0.462 | V4 signed radial -7.8 mm vs V5 -4.8 mm |  |
| M2 | ok | 0.179 | total 67.8 mm; anchor 53.2, delay 50.6, NLOS 0.0 | counterfactual/proxy non-orthogonal budget |
| M3 | ok | 0.178 | V5 offset mean 56.8 mm; direction resultant 0.09 |  |
| M4 | ok | 0.017 | corr(e_i,rho_rms) 0.08; all-e_i-zero median 64.5 mm |  |
| M5 | ok | 10.533 | 8-anchor redundancy +2; 9-anchor redundancy +6; simulated 9-anchor median 60.7 mm | subset replay uses median-range LS |
| M6 | ok | 0.004 | worst sector 300 deg, 117.7 mm, anchor D |  |
| M7 | ok | 0.164 | constraint diagnostic best median 101.1 mm | diagnostic projection reused |
| M8 | ok | 0.181 | strongest simple predictor n_bad_anchors R2=0.18 |  |
| M9 | ok | 0.003 | weakest eig 5.98e-03; proj scale -0.01, c 0.01, Dtag 0.01 |  |
| M10 | ok | 0.029 | V5 baseline consistency max delta 0.00 mm |  |

## Paper Cross-References

| paper_section | tasks | tables |
| --- | --- | --- |
| Scale-delay mechanism | M1, M3, M9 | m1_error_direction_summary.csv; m3_phase_center_offset.csv; m9_fisher_eigenvectors.csv |
| Error budget | M2, M8 | m2_error_budget.csv; m8_position_anatomy.csv |
| NLOS and delay absorption | M4, M6 | m4_ei_vs_nlos.csv; m6_roto_phase_aggregate.csv |
| Anchor-count identifiability | M5 | m5_identifiability_table.csv |
| Dynamic tracking limitations | M6, M7 | m6_roto_phase_aggregate.csv; m7_rigid_summary.csv |
| Consistency audit | M10 | m10_evidence_matrix.csv |

## Runtime Self-Report

Workers: 6, CPU-only, total task wall sum: 11.8 s

| task | elapsed_s | mean_cpu_percent | max_cpu_percent | workers |
| --- | --- | --- | --- | --- |
| M1 | 0.462 | 32.200 | 32.200 | 6 |
| M2 | 0.179 | 28.800 | 28.800 | 6 |
| M3 | 0.178 | 31.400 | 31.400 | 6 |
| M4 | 0.017 | 33.300 | 33.300 | 6 |
| M5 | 10.533 | 27.800 | 27.800 | 6 |
| M6 | 0.004 | 42.900 | 42.900 | 6 |
| M7 | 0.164 | 28.700 | 28.700 | 6 |
| M8 | 0.181 | 27.100 | 27.100 | 6 |
| M9 | 0.003 | 50.000 | 50.000 | 6 |
| M10 | 0.029 | 30.300 | 30.300 | 6 |

