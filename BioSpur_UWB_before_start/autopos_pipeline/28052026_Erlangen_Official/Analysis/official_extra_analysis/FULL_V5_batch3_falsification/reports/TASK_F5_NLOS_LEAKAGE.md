# Task F5 - NLOS Leakage Audit

Generated: 2026-06-18T01:39:31

Fold-average PR-AUC exposes whether the NLOS detector transfers beyond anchor/position identity.

| split_type | model | pr_auc | roc_auc | n_train | n_test |
| --- | --- | --- | --- | --- | --- |
| anchor_id_only_random | logistic | 0.517 | 0.633 | 75.000 | 20.000 |
| anchor_id_only_random | mlp | 0.508 | 0.627 | 75.000 | 20.000 |
| leave_one_anchor_out | logistic | 0.548 | 0.628 | 83.125 | 11.875 |
| leave_one_anchor_out | mlp | 0.419 | 0.525 | 83.125 | 11.875 |
| leave_one_height_out | logistic | 0.372 | 0.527 | 63.333 | 31.667 |
| leave_one_height_out | mlp | 0.370 | 0.543 | 63.333 | 31.667 |
| leave_one_position_out | logistic | 0.655 | 0.593 | 90.895 | 4.105 |
| leave_one_position_out | mlp | 0.746 | 0.672 | 90.895 | 4.105 |
| random_position_pair | logistic | 0.579 | 0.747 | 75.000 | 20.000 |
| random_position_pair | mlp | 0.525 | 0.687 | 75.000 | 20.000 |
| shuffled_labels_random | logistic | 0.329 | 0.507 | 75.000 | 20.000 |
| shuffled_labels_random | mlp | 0.247 | 0.373 | 75.000 | 20.000 |
