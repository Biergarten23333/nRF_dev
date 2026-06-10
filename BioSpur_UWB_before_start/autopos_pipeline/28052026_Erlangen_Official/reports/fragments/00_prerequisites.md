## Phase 1 Prerequisites

Ground-truth system terminology: **Vicon**. The local `opti_captures` name is a storage convention, not the report terminology.

Anchor ID mapping was verified from static tag ranges against Vicon link distances before any tag-link bias calculation.

| best_mapping | best_rms_mm | second_best_mapping | second_best_rms_mm | second_over_best_cost_ratio | data_config |
| --- | --- | --- | --- | --- | --- |
| 0->A, 1->B, 2->C, 3->D, 4->E, 5->F, 6->G, 7->H | 196.101 | 0->A, 1->B, 2->C, 3->D, 4->H, 5->F, 6->G, 7->E | 238.529 | 1.480 | data_config.py |

Direction columns were asserted before use:

| pair_columns_consistent | master_equals_initiator | self_links | direction_definition |
| --- | --- | --- | --- |
| True | True | 0 | initiator->responder |

Quality fields were audited for saturation; fields marked `no` are excluded from weighting decisions.

| dataset | field | rows | non_null | top_value | top_percent | informative |
| --- | --- | --- | --- | --- | --- | --- |
| sweep | quality_percent | 56000 | 56000 | 100 | 99.400 | no |
| sweep | quality_flag_percent | 56000 | 0 |  |  | missing |
| static | quality_percent | 230544 | 230544 | 100 | 93.948 | yes |
| static | quality_flag_percent | 230544 | 230544 | 0 | 100.000 | no |
| roto | quality_percent | 345696 | 345696 | 100 | 93.055 | yes |
| roto | quality_flag_percent | 345696 | 345696 | 0 | 100.000 | no |

Full quality distributions:

| dataset | field | value | count | percent |
| --- | --- | --- | --- | --- |
| sweep | quality_percent | 91 | 2 | 0.004 |
| sweep | quality_percent | 92 | 6 | 0.011 |
| sweep | quality_percent | 93 | 6 | 0.011 |
| sweep | quality_percent | 94 | 14 | 0.025 |
| sweep | quality_percent | 95 | 76 | 0.136 |
| sweep | quality_percent | 96 | 232 | 0.414 |
| sweep | quality_percent | 100 | 55664 | 99.400 |
| static | quality_percent | 62 | 2 | 0.001 |
| static | quality_percent | 63 | 2 | 0.001 |
| static | quality_percent | 64 | 10 | 0.004 |
| static | quality_percent | 65 | 28 | 0.012 |
| static | quality_percent | 66 | 79 | 0.034 |
| static | quality_percent | 67 | 42 | 0.018 |
| static | quality_percent | 68 | 157 | 0.068 |
| static | quality_percent | 69 | 93 | 0.040 |
| static | quality_percent | 70 | 369 | 0.160 |
| static | quality_percent | 71 | 208 | 0.090 |
| static | quality_percent | 72 | 345 | 0.150 |
| static | quality_percent | 73 | 359 | 0.156 |
| static | quality_percent | 74 | 142 | 0.062 |
| static | quality_percent | 75 | 455 | 0.197 |
| static | quality_percent | 76 | 495 | 0.215 |
| static | quality_percent | 77 | 375 | 0.163 |
| static | quality_percent | 78 | 327 | 0.142 |
| static | quality_percent | 79 | 140 | 0.061 |
| static | quality_percent | 80 | 443 | 0.192 |
| static | quality_percent | 81 | 213 | 0.092 |
| static | quality_percent | 82 | 328 | 0.142 |
| static | quality_percent | 83 | 295 | 0.128 |
| static | quality_percent | 84 | 300 | 0.130 |
| static | quality_percent | 85 | 319 | 0.138 |
| static | quality_percent | 86 | 318 | 0.138 |
| static | quality_percent | 87 | 222 | 0.096 |
| static | quality_percent | 88 | 480 | 0.208 |
| static | quality_percent | 89 | 273 | 0.118 |
| static | quality_percent | 90 | 562 | 0.244 |
| static | quality_percent | 91 | 232 | 0.101 |
| static | quality_percent | 92 | 469 | 0.203 |
| static | quality_percent | 93 | 501 | 0.217 |
| static | quality_percent | 94 | 697 | 0.302 |
| static | quality_percent | 95 | 1501 | 0.651 |
| static | quality_percent | 96 | 3171 | 1.375 |
| static | quality_percent | 100 | 216592 | 93.948 |
| static | quality_flag_percent | 0 | 230544 | 100.000 |
| roto | quality_percent | 75 | 3 | 0.001 |
| roto | quality_percent | 77 | 4 | 0.001 |
| roto | quality_percent | 78 | 1 | 0.000 |
| roto | quality_percent | 80 | 13 | 0.004 |
| roto | quality_percent | 81 | 18 | 0.005 |
| roto | quality_percent | 82 | 74 | 0.021 |
| roto | quality_percent | 83 | 125 | 0.036 |
| roto | quality_percent | 84 | 226 | 0.065 |
| roto | quality_percent | 85 | 377 | 0.109 |
| roto | quality_percent | 86 | 476 | 0.138 |
| roto | quality_percent | 87 | 396 | 0.115 |
| roto | quality_percent | 88 | 905 | 0.262 |
| roto | quality_percent | 89 | 568 | 0.164 |
| roto | quality_percent | 90 | 1322 | 0.382 |
| roto | quality_percent | 91 | 543 | 0.157 |
| roto | quality_percent | 92 | 1114 | 0.322 |
| roto | quality_percent | 93 | 1124 | 0.325 |
| roto | quality_percent | 94 | 1805 | 0.522 |
| roto | quality_percent | 95 | 4461 | 1.290 |
| roto | quality_percent | 96 | 10454 | 3.024 |
| roto | quality_percent | 100 | 321687 | 93.055 |
| roto | quality_flag_percent | 0 | 345696 | 100.000 |

Sweep rows do not have per-sample timestamps, so time-drift analysis is out of Phase 1 scope.
